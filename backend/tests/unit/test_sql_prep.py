"""Unit tests: driver SQL preparation per engine (spec section 22).

The validator emits canonical SQL; providers convert it to the driver's
parameter style with an AST round-trip and escape literal ``%`` only when
parameters are bound.
"""

from __future__ import annotations

import pytest

from app.providers.dialects import codegen_dialect_for, parse_dialect_for
from app.providers.sql_prep import prepare_driver_sql


def test_parse_dialects_per_kind():
    assert parse_dialect_for("postgres") == "postgres"
    assert parse_dialect_for("mysql") == "mysql"
    assert parse_dialect_for("doris") == "doris"
    assert parse_dialect_for("spark") == "spark"


def test_unknown_kind_rejected():
    from app.errors import ApiError

    with pytest.raises(ApiError):
        parse_dialect_for("unknown")
    with pytest.raises(ApiError):
        codegen_dialect_for("unknown")


def test_postgres_named_parameters_stay_pyformat():
    sql = prepare_driver_sql(
        "SELECT dt FROM public.t WHERE dt >= :start", kind="postgres"
    )
    assert "%(start)s" in sql


def test_mysql_named_parameters_become_pyformat():
    sql = prepare_driver_sql("SELECT dt FROM demo.t WHERE dt >= :start", kind="mysql")
    assert "%(start)s" in sql


def test_doris_named_parameters_become_pyformat():
    sql = prepare_driver_sql("SELECT dt FROM demo.t WHERE dt >= :start", kind="doris")
    assert "%(start)s" in sql


def test_percent_literal_escaped_only_when_parameters_bound():
    with_params = prepare_driver_sql(
        "SELECT a FROM demo.t WHERE s LIKE 'a%' AND dt >= :start", kind="mysql"
    )
    assert "'a%%'" in with_params

    without_params = prepare_driver_sql(
        "SELECT a FROM demo.t WHERE s LIKE 'a%'", kind="mysql"
    )
    assert "'a%'" in without_params
    assert "'a%%'" not in without_params


def test_postgres_percent_literal_same_rule():
    with_params = prepare_driver_sql(
        "SELECT a FROM public.t WHERE s LIKE 'a%' AND dt >= :start", kind="postgres"
    )
    assert "'a%%'" in with_params

    without_params = prepare_driver_sql(
        "SELECT a FROM public.t WHERE s LIKE 'a%'", kind="postgres"
    )
    assert "'a%'" in without_params


def test_preparation_preserves_query_shape():
    canonical = (
        "SELECT country, SUM(revenue_usd) AS revenue FROM public.fixture_metrics "
        "WHERE dt >= :start GROUP BY country ORDER BY country LIMIT 1001"
    )
    prepared = prepare_driver_sql(canonical, kind="postgres")
    assert "SUM(revenue_usd)" in prepared
    assert "GROUP BY country" in prepared
    assert "ORDER BY country" in prepared
    assert "LIMIT 1001" in prepared
    assert "%(start)s" in prepared
