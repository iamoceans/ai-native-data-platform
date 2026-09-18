"""Contract tests for locked libraries and the provider protocol (spec 34).

These pin the behaviours the gateway/executor depend on, so an accidental
dependency bump fails loudly instead of silently changing semantics.
"""

from __future__ import annotations

import inspect

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.qualify import qualify


def test_locked_sqlglot_version():
    assert sqlglot.__version__ == "30.18.0"


def test_postgres_placeholder_rendering_is_psycopg_style():
    tree = sqlglot.parse_one("SELECT dt FROM t WHERE dt >= :start", read="postgres")
    assert tree.sql(dialect="postgres").endswith("%(start)s")


def test_qualified_and_doris_dialects_are_available():
    # M2 providers must be able to use the dedicated dialects; parse-only check.
    sqlglot.parse_one("SELECT 1", read="mysql")
    sqlglot.parse_one("SELECT 1", read="doris")


def test_scope_analysis_separates_cte_from_physical_tables():
    from sqlglot.optimizer.scope import traverse_scope

    tree = sqlglot.parse_one(
        "WITH c AS (SELECT * FROM real_t) SELECT * FROM c", read="postgres"
    )
    physical = []
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                physical.append(source.name)
    assert physical == ["real_t"]


def test_expand_stars_with_nested_schema():
    tree = sqlglot.parse_one("SELECT * FROM demo.ads", read="postgres")
    out = qualify(
        tree,
        schema={"demo": {"ads": {"dt": "date", "v": "numeric"}}},
        dialect="postgres",
        expand_stars=True,
        validate_qualify_columns=True,
        quote_identifiers=False,
        identify=False,
    )
    rendered = out.sql(dialect="postgres")
    assert "SELECT *" not in rendered
    assert "dt" in rendered and "v" in rendered


def test_join_attributes_used_by_validator():
    natural = sqlglot.parse_one("SELECT * FROM a NATURAL JOIN b", read="postgres").find(exp.Join)
    cross = sqlglot.parse_one("SELECT * FROM a CROSS JOIN b", read="postgres").find(exp.Join)
    assert natural.args.get("method") == "NATURAL"
    assert cross.args.get("kind") == "CROSS"


def test_operator_conditionals_are_func_subclasses():
    """The function allowlist must keep allowing these structural classes."""
    tree = sqlglot.parse_one(
        "SELECT CASE WHEN a AND b THEN 1 ELSE 0 END FROM t", read="postgres"
    )
    classes = {type(node).__name__ for node in tree.walk() if isinstance(node, exp.Func)}
    assert {"And", "Case", "If"} <= classes


# ---------------------------------------------------------------------------
# psycopg contract (driver API used by the provider)
# ---------------------------------------------------------------------------
def test_locked_psycopg_version():
    import psycopg

    assert psycopg.__version__ == "3.3.5"


def test_psycopg_exposes_cancel_api():
    import psycopg

    assert hasattr(psycopg.Connection, "cancel")
    # cancel_safe exists in psycopg >= 3.2 and is used when available
    assert hasattr(psycopg.Connection, "cancel_safe")


def test_psycopg_type_registry_maps_known_oids():
    from psycopg.postgres import types as pg_types

    assert pg_types[23].name == "int4"
    assert pg_types[1700].name == "numeric"


def test_psycopg_errors_used_by_executor_exist():
    import psycopg

    for name in ("QueryCanceled", "InsufficientPrivilege", "ReadOnlySqlTransaction"):
        assert hasattr(psycopg.errors, name), name


# ---------------------------------------------------------------------------
# provider protocol conformance
# ---------------------------------------------------------------------------
def test_postgres_provider_implements_protocol_surface():
    from app.providers.base import QueryProvider
    from app.providers.postgres import PostgresProvider

    protocol_methods = {
        name
        for name, _ in inspect.getmembers(QueryProvider, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    missing = [name for name in protocol_methods if not hasattr(PostgresProvider, name)]
    assert missing == []


def test_default_registry_supports_all_three_engines():
    from app.providers.registry import get_registry

    assert get_registry().kinds() == ["doris", "mysql", "postgres"]


def test_mysql_doris_providers_implement_protocol_surface():
    import inspect

    from app.providers.base import QueryProvider
    from app.providers.doris import DorisProvider
    from app.providers.mysql import MySQLProvider

    protocol_methods = {
        name
        for name, _ in inspect.getmembers(QueryProvider, predicate=inspect.isfunction)
        if not name.startswith("_")
    }
    for provider_class in (MySQLProvider, DorisProvider):
        missing = [name for name in protocol_methods if not hasattr(provider_class, name)]
        assert missing == [], f"{provider_class.__name__} missing {missing}"


def test_mysql_and_doris_are_distinct_implementations():
    """Doris must never inherit MySQL semantics (spec section 3)."""
    from app.providers.doris import DorisProvider
    from app.providers.mysql import MySQLProvider

    assert not issubclass(DorisProvider, MySQLProvider)
    assert DorisProvider.capabilities.__qualname__.startswith("DorisProvider.")
    assert MySQLProvider.capabilities.__qualname__.startswith("MySQLProvider.")


# ---------------------------------------------------------------------------
# OpenAPI contract used to generate frontend types
# ---------------------------------------------------------------------------
def test_openapi_contains_m1_paths_and_no_password_fields():
    from app.main import create_app

    schema = create_app().openapi()
    paths = schema["paths"]
    for required in (
        "/api/v1/auth/login",
        "/api/v1/datasources",
        "/api/v1/datasets",
        "/api/v1/queries",
        "/api/v1/queries/{query_id}/results",
        "/api/v1/queries/{query_id}/events",
        "/api/v1/admin/grants",
        "/health/ready",
    ):
        assert required in paths, required
    datasource_schema = schema["components"]["schemas"]["DatasourceResponse"]
    assert "password" not in str(datasource_schema).lower()
    login_response = schema["components"]["schemas"]["LoginResponse"]
    assert "csrf_token" in login_response["properties"]
