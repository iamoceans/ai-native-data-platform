"""ChartSpec contract: controlled kinds, bounded data, traceable fields."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.analysis.contribution import decompose_contribution
from app.charts.spec import (
    MAX_CHART_POINTS,
    ChartSpec,
    ChartValidationError,
    build_bounded_view,
    contribution_chart,
    validate_chart_data,
)


def _contribution(pairs: list[tuple[str, str, str]]) -> dict:
    baseline = [{"country": country, "metric_value": value} for country, value, _ in pairs]
    current = [{"country": country, "metric_value": value} for country, _, value in pairs]
    delta = sum(Decimal(item["metric_value"]) for item in current) - sum(
        Decimal(item["metric_value"]) for item in baseline
    )
    return decompose_contribution(
        baseline,
        current,
        dimension=["country"],
        value_column="metric_value",
        parent_delta=delta,
        merge_below_support=False,
    ).as_dict()


def test_contribution_chart_is_a_bounded_traceable_bar_chart():
    contribution = _contribution([("US", "100", "80"), ("DE", "50", "60")])
    chart = contribution_chart(
        contribution["groups"],
        contribution["dimension"],
        calculation_id="calc-1",
        title="各国家收入变化",
        unit="USD",
        currency="USD",
        metric_key="ads_revenue",
        query_ids=["q1", "q2"],
    )
    spec = chart["spec"]
    assert spec["kind"] == "bar"
    assert spec["data_ref"] == {"type": "calculation", "id": "calc-1"}
    assert spec["encoding"]["y"]["unit"] == "USD"
    assert spec["encoding"]["x"]["field"] == "key"
    assert spec["evidence_ids"] == ["q1", "q2"]
    assert spec["interaction"]["drilldown_dimensions"] == ["country"]
    assert chart["truncated"] is False
    # Ranked by absolute delta, so the bar order matches the report's citation.
    assert [row["key"] for row in chart["data"]] == ["US", "DE"]
    assert chart["data"][0]["delta"] == "-20"
    assert chart["data"][0]["dimension_values"] == ["US"]

    view = build_bounded_view(chart, limit=1)
    assert view["total_points"] == 2
    assert len(view["data"]) == 1
    assert view["spec"]["kind"] == "bar"


def test_chart_spec_rejects_unknown_options_and_absent_fields():
    with pytest.raises(ValueError):
        ChartSpec.model_validate(
            {
                "kind": "bar",
                "title": "x",
                "data_ref": {"type": "calculation", "id": "c1"},
                "encoding": {
                    "x": {"field": "key", "type": "category"},
                    "y": {"field": "delta", "type": "quantitative"},
                },
                "echarts_option": {"series": [{"type": "custom"}]},
            }
        )
    spec = ChartSpec.model_validate(
        {
            "kind": "bar",
            "title": "x",
            "data_ref": {"type": "calculation", "id": "c1"},
            "encoding": {
                "x": {"field": "key", "type": "category"},
                "y": {"field": "delta", "type": "quantitative", "unit": "USD"},
            },
        }
    )
    with pytest.raises(ChartValidationError):
        validate_chart_data(spec, [{"label": "US"}])
    with pytest.raises(ChartValidationError):
        validate_chart_data(
            spec, [{"key": f"g{i}", "delta": "1"} for i in range(MAX_CHART_POINTS + 1)]
        )


def test_line_chart_requires_ordered_x_without_silent_zero_fill():
    spec = ChartSpec.model_validate(
        {
            "kind": "line",
            "title": "daily revenue",
            "data_ref": {"type": "calculation", "id": "c1"},
            "encoding": {
                "x": {"field": "dt", "type": "temporal"},
                "y": {"field": "delta", "type": "quantitative", "unit": "USD"},
            },
        }
    )
    validate_chart_data(
        spec,
        [{"dt": "2026-09-01", "delta": "1"}, {"dt": "2026-09-03", "delta": "2"}],
    )
    with pytest.raises(ChartValidationError):
        validate_chart_data(
            spec,
            [{"dt": "2026-09-03", "delta": "2"}, {"dt": "2026-09-01", "delta": "1"}],
        )
    with pytest.raises(ChartValidationError):
        validate_chart_data(spec, [{"dt": None, "delta": "1"}])
