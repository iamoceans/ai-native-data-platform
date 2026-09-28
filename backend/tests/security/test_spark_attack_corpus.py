"""Spark uses the same denylist and registered table resolution as other engines."""

from __future__ import annotations

import pytest

from app.errors import ApiError
from app.query.validator import validate_query
from tests.unit.test_validator import SCHEMAS, make_dataset


def _validate(sql: str):
    datasets = [make_dataset(name, schema="demo", catalog="spark_catalog") for name in SCHEMAS]
    return validate_query(
        sql=sql, dialect="spark", datasets=datasets,
        schema_loader=lambda dataset: SCHEMAS[dataset.object_name],
        database="demo", max_rows=100, max_relations=8,
        max_subquery_depth=4, engine="spark",
    )


@pytest.mark.parametrize("sql", [
    "DROP TABLE demo.fixture_metrics",
    "SELECT * FROM demo.fixture_metrics; DELETE FROM demo.fixture_metrics",
    "SELECT secret_udf(dt) FROM demo.fixture_metrics",
    "SELECT * FROM other.demo.fixture_metrics",
    "SELECT * FROM information_schema.columns",
    "SELECT * FROM demo.fixture_metrics FOR UPDATE",
])
def test_spark_attack_rejected(sql: str) -> None:
    with pytest.raises(ApiError):
        _validate(sql)


def test_spark_registered_table_is_fully_qualified() -> None:
    result = _validate("SELECT dt FROM fixture_metrics")
    assert "demo.fixture_metrics" in result.validated_sql
    assert "LIMIT 101" in result.validated_sql
