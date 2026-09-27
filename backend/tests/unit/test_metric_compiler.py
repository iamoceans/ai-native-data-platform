"""Metric compiler tests (spec section 15): SQL shape, composition, refusals."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.analysis.types import AnalysisError
from app.metrics.compiler import compile_metric
from app.metrics.registry import (
    MetricDefinitionModel,
    load_metric_definitions,
)

ROOT = Path(__file__).resolve().parents[2]
METADATA_DIR = ROOT.parent / "metadata"

PERIOD = {"period_start": "2026-09-11", "period_end": "2026-09-13"}


@pytest.fixture(scope="module")
def definitions() -> dict[str, MetricDefinitionModel]:
    return {item.metric_key: item for item in load_metric_definitions(METADATA_DIR)}


def test_all_shipped_metrics_compile(definitions):
    # Pinned by key rather than by count: a new definition must be a deliberate
    # addition here, with the dataset it reads and the dimensions it opens.
    assert set(definitions) == {
        "ads_revenue",
        "campaign_attributed_revenue",
        "campaign_spend",
        "cohort_size",
        "dau",
        "ecpm",
        "iap_revenue",
        "impressions",
        "new_users",
        "total_revenue",
    }
    for definition in definitions.values():
        dimensions = list(definition.allowed_dimensions)[:1]
        compiled = compile_metric(definition, dimensions=dimensions, **PERIOD)
        assert compiled.queries, definition.metric_key
        for query in compiled.queries:
            assert query.sql.startswith("SELECT ")
            assert ":start" in query.sql and ":end" in query.sql
            assert query.parameters["start"] == PERIOD["period_start"]
            assert query.parameters["end"] == PERIOD["period_end"]
            # The compiler never emits literals for the period or a LIMIT; the
            # gateway adds the row limit later.
            assert "LIMIT" not in query.sql.upper()


def test_ecpm_compiles_ratio_of_sums(definitions):
    compiled = compile_metric(
        definitions["ecpm"], dimensions=["country", "ad_network"], **PERIOD
    )
    query = compiled.queries[0]
    assert "NULLIF" in query.sql.upper()
    assert "SUM(revenue_usd)" in query.sql
    assert "SUM(impressions)" in query.sql
    assert "AS ecpm" in query.sql
    assert query.group_by == ("country", "ad_network")
    assert compiled.unit == "USD_per_1000_impressions"


def test_total_revenue_compiles_per_dataset_and_never_joins(definitions):
    compiled = compile_metric(definitions["total_revenue"], dimensions=["country"], **PERIOD)
    assert len(compiled.queries) == 2
    datasets = {query.dataset for query in compiled.queries}
    assert datasets == {"demo.ads_revenue_daily", "demo.iap_revenue_daily"}
    assert compiled.composition is not None and "ads" in compiled.composition
    aliases = {query.metric_alias for query in compiled.queries}
    assert aliases == {"ads", "iap"}
    for query in compiled.queries:
        assert "JOIN" not in query.sql.upper()


def test_dau_compiles_daily_rows_for_averaging(definitions):
    compiled = compile_metric(definitions["dau"], dimensions=["country"], **PERIOD)
    query = compiled.queries[0]
    assert query.group_by[0] == "dt"
    assert query.post_aggregation == "daily_average"
    assert any("average across days" in warning for warning in compiled.warnings)


def test_campaign_metrics_read_their_own_cohort_date_column(definitions):
    """The cohort tables are keyed on cohort_date, so freshness decides the period filter."""
    compiled = compile_metric(definitions["campaign_spend"], dimensions=["campaign_id"], **PERIOD)
    query = compiled.queries[0]
    assert "FROM demo.campaign_cohort_daily" in query.sql
    assert "cohort_date >= :start AND cohort_date < :end" in query.sql
    assert query.group_by == ("campaign_id",)
    assert "(SUM(cost_usd)) AS campaign_spend" in query.sql
    assert query.parameters["start"] == "2026-09-11"


def test_cohort_size_sums_the_cohort_without_inventing_a_retention_rate(definitions):
    """Only the cohort denominator is declared; the NULL-bearing rate columns stay out."""
    compiled = compile_metric(definitions["cohort_size"], dimensions=["country"], **PERIOD)
    query = compiled.queries[0]
    assert "FROM demo.retention_daily" in query.sql
    assert "(SUM(cohort_size)) AS cohort_size" in query.sql
    assert "d1_users" not in query.sql and "d7_users" not in query.sql
    assert query.group_by == ("country",)


def test_dimension_must_be_allowed(definitions):
    with pytest.raises(AnalysisError) as excinfo:
        compile_metric(definitions["ads_revenue"], dimensions=["dt"], **PERIOD)
    assert excinfo.value.code == "METRIC_DIMENSION_NOT_ALLOWED"


def test_period_is_mandatory(definitions):
    with pytest.raises(AnalysisError) as excinfo:
        compile_metric(definitions["ads_revenue"], dimensions=["country"])
    assert excinfo.value.code == "METRIC_PERIOD_REQUIRED"


def test_filters_are_bound_parameters(definitions):
    compiled = compile_metric(
        definitions["ads_revenue"], dimensions=["country"], filters={"country": "US"}, **PERIOD
    )
    query = compiled.queries[0]
    assert "AND country = :filter_country" in query.sql
    assert query.parameters["filter_country"] == "US"


def test_filter_on_unknown_column_is_refused(definitions):
    with pytest.raises(AnalysisError):
        compile_metric(
            definitions["ads_revenue"], dimensions=["country"], filters={"secret_col": "x"}, **PERIOD
        )


def _definition_with_formula(formula: str) -> MetricDefinitionModel:
    return MetricDefinitionModel.model_validate(
        {
            "metric_key": "probe",
            "version": 1,
            "name": "probe",
            "dataset": "demo.ads_revenue_daily",
            "components": {"revenue": {"aggregation": "sum", "column": "revenue_usd"}},
            "formula": formula,
            "unit": "USD",
            "aggregation_kind": "sum",
        }
    )


@pytest.mark.parametrize(
    "formula",
    [
        "revenue; DROP TABLE demo.ads_revenue_daily",
        "revenue UNION SELECT 1",
        "pg_sleep(1)",
        "revenue -- comment",
        "unknown_component + 1",
        "revenue / company_secret",
        "__import__('os')",
    ],
)
def test_formula_injection_attempts_are_refused(formula):
    with pytest.raises(AnalysisError) as excinfo:
        compile_metric(_definition_with_formula(formula), **PERIOD)
    assert excinfo.value.code == "METRIC_FORMULA_INVALID"


def test_component_aggregation_is_validated():
    definition = MetricDefinitionModel.model_validate(
        {
            "metric_key": "probe",
            "version": 1,
            "name": "probe",
            "dataset": "demo.ads_revenue_daily",
            "components": {"revenue": {"aggregation": "sum;drop", "column": "revenue_usd"}},
            "formula": "revenue",
            "unit": "USD",
            "aggregation_kind": "sum",
        }
    )
    with pytest.raises(AnalysisError) as excinfo:
        compile_metric(definition, **PERIOD)
    assert excinfo.value.code == "METRIC_AGGREGATION_UNSUPPORTED"
