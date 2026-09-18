"""Unit tests for the SQL validation pipeline (spec section 12)."""

from __future__ import annotations

import uuid

import pytest

from app.errors import ApiError
from app.models.orm import Dataset
from app.query.validator import validate_query

DIALECT = "postgres"


def make_dataset(
    name: str, schema: str = "public", catalog: str = "ainative_source", object_type: str = "table"
) -> Dataset:
    return Dataset(
        id=uuid.uuid4(),
        datasource_id=uuid.uuid4(),
        catalog_name=catalog,
        schema_name=schema,
        object_name=name,
        object_type=object_type,
        sync_status="SYNCED",
        active=True,
    )


SCHEMAS = {
    "fixture_metrics": [
        {"name": "dt", "type": "date"},
        {"name": "country", "type": "text"},
        {"name": "platform", "type": "text"},
        {"name": "revenue_usd", "type": "numeric(20,6)"},
        {"name": "impressions", "type": "bigint"},
    ],
    "fixture_extra": [
        {"name": "dt", "type": "date"},
        {"name": "country", "type": "text"},
        {"name": "device", "type": "text"},
    ],
}


def dataset(name: str, **kwargs) -> Dataset:
    return make_dataset(name, **kwargs)


def loader(dataset: Dataset) -> list[dict]:
    return SCHEMAS[dataset.object_name]


def validate(sql: str, datasets: list[Dataset] | None = None, dialect: str = DIALECT, **kwargs):
    default_datasets = datasets if datasets is not None else [dataset("fixture_metrics"), dataset("fixture_extra")]
    return validate_query(
        sql=sql,
        dialect=dialect,
        datasets=default_datasets,
        schema_loader=loader,
        database="ainative_source",
        max_rows=kwargs.pop("max_rows", 1000),
        max_relations=kwargs.pop("max_relations", 8),
        max_subquery_depth=kwargs.pop("max_subquery_depth", 4),
        **kwargs,
    )


def expect_error(sql: str, code: str, **kwargs) -> ApiError:
    with pytest.raises(ApiError) as excinfo:
        validate(sql, **kwargs)
    assert excinfo.value.code == code, f"expected {code}, got {excinfo.value.code}: {excinfo.value.message}"
    return excinfo.value


# ---------------------------------------------------------------------------
# happy paths
# ---------------------------------------------------------------------------
def test_select_with_aggregation_passes():
    result = validate(
        "SELECT dt, SUM(revenue_usd) AS revenue FROM fixture_metrics "
        "WHERE dt >= :start AND country = :country GROUP BY dt ORDER BY dt"
    )
    # internal probe limit is max_rows + 1 (truncation detection), see validator
    assert "LIMIT 1001" in result.validated_sql
    assert "public.fixture_metrics" in result.validated_sql
    assert result.placeholder_names == ["country", "start"]
    assert len(result.relations) == 1


def test_rewritten_physical_identifiers_preserve_mixed_case():
    mixed_case = dataset("Orders")
    result = validate_query(
        sql='SELECT id FROM "Orders"',
        dialect="postgres",
        datasets=[mixed_case],
        schema_loader=lambda _dataset: [{"name": "id", "type": "integer"}],
        database="ainative_source",
        max_rows=100,
        max_relations=8,
        max_subquery_depth=4,
    )

    assert 'public."Orders"' in result.validated_sql
    assert "public.orders" not in result.validated_sql


def test_star_expands_to_columns():
    result = validate("SELECT * FROM fixture_metrics")
    assert "dt" in result.validated_sql
    assert "revenue_usd" in result.validated_sql
    assert "SELECT *" not in result.validated_sql


def test_qualified_star_expands():
    result = validate("SELECT m.* FROM fixture_metrics m")
    assert "revenue_usd" in result.validated_sql
    assert "m.*" not in result.validated_sql


def test_join_with_on_is_allowed():
    result = validate(
        "SELECT m.dt, SUM(m.revenue_usd), COUNT(e.device) "
        "FROM fixture_metrics m JOIN fixture_extra e ON m.dt = e.dt AND m.country = e.country "
        "GROUP BY m.dt"
    )
    assert len(result.relations) == 2


def test_cte_and_union_are_allowed():
    result = validate(
        "WITH daily AS (SELECT dt, SUM(revenue_usd) AS r FROM fixture_metrics GROUP BY dt) "
        "SELECT dt, r FROM daily WHERE r > 0 "
        "UNION ALL SELECT dt, 0 FROM daily WHERE r <= 0"
    )
    assert len(result.relations) == 1


def test_outer_limit_is_rewritten_not_inner():
    result = validate(
        "SELECT dt FROM (SELECT dt FROM fixture_metrics LIMIT 5) t LIMIT 999999"
    )
    assert result.validated_sql.count("LIMIT") == 2
    assert "LIMIT 1001" in result.validated_sql
    assert "LIMIT 5" in result.validated_sql


def test_user_limit_below_max_is_kept():
    result = validate("SELECT dt FROM fixture_metrics LIMIT 10")
    assert "LIMIT 10" in result.validated_sql
    assert "LIMIT 1001" not in result.validated_sql


def test_parameter_placeholders_survive():
    result = validate(
        "SELECT COUNT(*) FROM fixture_metrics WHERE country = :country AND dt < :end"
    )
    assert "%(country)s" in result.validated_sql
    assert "%(end)s" in result.validated_sql


def test_percent_literal_is_kept_in_canonical_sql():
    """Canonical SQL keeps a single %; the provider escapes it at execution
    time only when parameters are bound (app.providers.sql_prep)."""
    result = validate("SELECT country FROM fixture_metrics WHERE platform LIKE 'and%'")
    assert "'and%'" in result.validated_sql
    assert "'and%%'" not in result.validated_sql


def test_cte_shadowing_real_table_is_not_resolved_as_table():
    result = validate(
        "WITH fixture_metrics AS (SELECT 1 AS x) SELECT fixture_metrics.x FROM fixture_metrics"
    )
    assert result.relations == []


# ---------------------------------------------------------------------------
# rejections: statement shape
# ---------------------------------------------------------------------------
def test_multi_statement_rejected():
    error = expect_error("SELECT 1; DELETE FROM fixture_metrics", "SQL_FORBIDDEN")
    assert error.details["rule_id"] == "SINGLE_STATEMENT_ONLY"


def test_non_select_statement_rejected():
    error = expect_error("DELETE FROM fixture_metrics", "SQL_FORBIDDEN")
    assert error.details["rule_id"] == "AST_SELECT_ONLY"


def test_write_cte_rejected():
    error = expect_error(
        "WITH gone AS (DELETE FROM fixture_metrics RETURNING dt) SELECT * FROM gone",
        "SQL_FORBIDDEN",
    )
    assert error.details["rule_id"] == "STATEMENT_NODE_DENIED"


def test_select_into_rejected():
    expect_error("SELECT * INTO new_table FROM fixture_metrics", "SQL_FORBIDDEN")


def test_executable_comment_rejected():
    error = expect_error("SELECT /*! SLEEP(1) */ country FROM fixture_metrics", "SQL_FORBIDDEN")
    assert error.details["rule_id"] == "EXECUTABLE_COMMENT"


def test_recursive_cte_rejected():
    error = expect_error(
        "WITH RECURSIVE r AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM r) SELECT * FROM r",
        "SQL_FORBIDDEN",
    )
    assert error.details["rule_id"] == "RECURSIVE_CTE"


def test_for_update_rejected():
    error = expect_error("SELECT dt FROM fixture_metrics FOR UPDATE", "SQL_FORBIDDEN")
    assert error.details["rule_id"] in {"LOCKING_READ", "STATEMENT_NODE_DENIED"}


# ---------------------------------------------------------------------------
# rejections: functions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "fragment",
    [
        "pg_sleep(1)",
        "SLEEP(1)",
        "BENCHMARK(1000, MD5('x'))",
        "md5(country)",
        "current_timestamp",
        "random()",
    ],
)
def test_dangerous_or_unknown_functions_rejected(fragment):
    error = expect_error(f"SELECT {fragment} FROM fixture_metrics", "SQL_FORBIDDEN")
    assert error.details["rule_id"] == "FUNCTION_NOT_ALLOWED"


def test_allowed_functions_pass():
    validate(
        "SELECT COALESCE(SUM(revenue_usd), 0), COUNT(*), MIN(dt), MAX(dt), AVG(impressions), "
        "ROUND(AVG(revenue_usd), 2), ABS(-1), LOWER(country), DATE_TRUNC('day', dt), "
        "EXTRACT(day FROM dt) FROM fixture_metrics GROUP BY LOWER(country), DATE_TRUNC('day', dt), dt"
    )


def test_generate_series_table_function_rejected():
    expect_error("SELECT gs FROM generate_series(1, 10) gs", "SQL_FORBIDDEN")


# ---------------------------------------------------------------------------
# rejections: joins
# ---------------------------------------------------------------------------
def test_cross_join_rejected():
    error = expect_error(
        "SELECT m.dt FROM fixture_metrics m CROSS JOIN fixture_extra e", "SQL_FORBIDDEN"
    )
    assert error.details["rule_id"] == "CROSS_JOIN"


def test_comma_join_rejected():
    error = expect_error("SELECT m.dt FROM fixture_metrics m, fixture_extra e", "SQL_FORBIDDEN")
    assert error.details["rule_id"] == "JOIN_WITHOUT_ON"


def test_natural_join_rejected():
    error = expect_error(
        "SELECT m.dt FROM fixture_metrics m NATURAL JOIN fixture_extra e", "SQL_FORBIDDEN"
    )
    assert error.details["rule_id"] == "NATURAL_JOIN"


def test_constant_on_join_rejected():
    error = expect_error(
        "SELECT m.dt FROM fixture_metrics m JOIN fixture_extra e ON TRUE", "SQL_FORBIDDEN"
    )
    assert error.details["rule_id"] == "CONSTANT_JOIN_CONDITION"


# ---------------------------------------------------------------------------
# rejections: resolution / schema
# ---------------------------------------------------------------------------
def test_unregistered_table_rejected():
    error = expect_error("SELECT * FROM secret_table", "DATASET_NOT_REGISTERED")
    assert "secret_table" in error.message


def test_system_schema_rejected():
    expect_error("SELECT * FROM pg_catalog.pg_tables", "DATASET_NOT_REGISTERED")
    expect_error("SELECT * FROM information_schema.tables", "DATASET_NOT_REGISTERED")


def test_cross_database_reference_rejected():
    expect_error("SELECT * FROM other_db.public.fixture_metrics", "DATASET_NOT_REGISTERED")


def test_ambiguous_table_reference_rejected():
    datasets = [dataset("dup", schema="public"), dataset("dup", schema="analytics")]
    error = expect_error("SELECT * FROM dup", "AMBIGUOUS_TABLE_REFERENCE", datasets=datasets)
    assert "public.dup" in error.details["matches"][0] + error.details["matches"][1]


def test_unknown_column_rejected():
    error = expect_error("SELECT not_a_column FROM fixture_metrics", "SQL_UNSUPPORTED")
    assert error.details["rule_id"] == "UNKNOWN_COLUMN_REFERENCE"


def test_relation_budget_enforced():
    expect_error(
        "SELECT a.dt FROM fixture_metrics a "
        "JOIN fixture_extra b ON a.dt = b.dt "
        "JOIN fixture_extra c ON a.dt = c.dt "
        "JOIN fixture_extra d ON a.dt = d.dt",
        "SQL_FORBIDDEN",
        max_relations=3,
    )


def test_subquery_depth_enforced():
    sql = (
        "SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT * FROM (SELECT * FROM "
        "(SELECT * FROM fixture_metrics) a) b) c) d) e"
    )
    error = expect_error(sql, "SQL_FORBIDDEN")
    assert error.details["rule_id"] == "SUBQUERY_DEPTH"


def test_quoted_case_sensitive_table_must_match():
    expect_error('SELECT * FROM "Fixture_Metrics"', "DATASET_NOT_REGISTERED")

# --- engine-specific tests appended by M2 ---

MYSQL_DATASET = make_dataset("fixture_metrics", schema="", catalog="demo")
DORIS_DATASET = make_dataset("fixture_metrics", schema="demo", catalog="internal")


def validate_engine(sql: str, engine: str, datasets=None):
    engine_datasets = datasets if datasets is not None else (
        [MYSQL_DATASET] if engine == "mysql" else [DORIS_DATASET]
    )
    return validate_query(
        sql=sql,
        dialect=engine,
        datasets=engine_datasets,
        schema_loader=loader,
        database="demo",
        max_rows=1000,
        max_relations=8,
        max_subquery_depth=4,
        engine=engine,
    )


def expect_engine_error(sql: str, engine: str, code: str):
    with pytest.raises(ApiError) as excinfo:
        validate_engine(sql, engine)
    assert excinfo.value.code == code, f"{engine}: {excinfo.value.code} {excinfo.value.message}"
    return excinfo.value


@pytest.mark.parametrize("engine", ["mysql", "doris"])
def test_engine_qualified_query_passes(engine):
    result = validate_engine(
        "SELECT country, SUM(revenue_usd) AS revenue FROM demo.fixture_metrics "
        "WHERE country = :country GROUP BY country ORDER BY country",
        engine,
    )
    assert "%(country)s" not in result.validated_sql  # canonical keeps :name until provider prep
    assert ":country" in result.validated_sql
    assert "LIMIT 1001" in result.validated_sql
    if engine == "doris":
        assert "internal.demo.fixture_metrics" in result.validated_sql
    else:
        assert "demo.fixture_metrics" in result.validated_sql
    assert result.placeholder_names == ["country"]


@pytest.mark.parametrize("engine", ["mysql", "doris"])
def test_engine_unqualified_table_resolves(engine):
    result = validate_engine("SELECT COUNT(*) AS n FROM fixture_metrics", engine)
    assert result.relations and result.relations[0].dataset.object_name == "fixture_metrics"


def test_mysql_rejects_three_part_name():
    expect_engine_error("SELECT * FROM catalog.demo.fixture_metrics", "mysql", "DATASET_NOT_REGISTERED")


def test_doris_rejects_external_catalog():
    error = expect_engine_error("SELECT * FROM hive.demo.fixture_metrics", "doris", "DATASET_NOT_REGISTERED")
    assert "external catalog" in error.message


@pytest.mark.parametrize(
    "sql,code,rule",
    [
        ("SHOW PROCESSLIST", "SQL_FORBIDDEN", "AST_SELECT_ONLY"),
        ("SET @x = 1", "SQL_FORBIDDEN", "AST_SELECT_ONLY"),
        ("KILL QUERY 123", "SQL_FORBIDDEN", "AST_SELECT_ONLY"),
        ("EXPLAIN SELECT 1", "SQL_FORBIDDEN", "AST_SELECT_ONLY"),
        ("ANALYZE TABLE demo.fixture_metrics", "SQL_FORBIDDEN", "AST_SELECT_ONLY"),
        ("SELECT @x := 1 FROM demo.fixture_metrics", "SQL_FORBIDDEN", "STATEMENT_NODE_DENIED"),
        ("SELECT @@version FROM demo.fixture_metrics", "SQL_FORBIDDEN", "STATEMENT_NODE_DENIED"),
        ("SELECT * FROM demo.fixture_metrics TABLESAMPLE (10 PERCENT)", "SQL_FORBIDDEN", "STATEMENT_NODE_DENIED"),
        ("SELECT * FROM demo.fixture_metrics FOR UPDATE", "SQL_FORBIDDEN", "STATEMENT_NODE_DENIED"),
        ("SELECT * FROM demo.fixture_metrics INTO OUTFILE '/tmp/x'", "SQL_SYNTAX_ERROR", "PARSE_ERROR"),
        ("SELECT SLEEP(1) FROM demo.fixture_metrics", "SQL_FORBIDDEN", "FUNCTION_NOT_ALLOWED"),
        ("SELECT LOAD_FILE('/etc/passwd') FROM demo.fixture_metrics", "SQL_FORBIDDEN", "FUNCTION_NOT_ALLOWED"),
        ("SELECT * FROM mysql.user", "DATASET_NOT_REGISTERED", "dataset"),
    ],
)
@pytest.mark.parametrize("engine", ["mysql", "doris"])
def test_engine_attack_corpus(engine, sql, code, rule):
    error = expect_engine_error(sql, engine, code)
    if rule != "dataset":
        assert error.details.get("rule_id") == rule, error.details
