"""ChartSpec: controlled, traceable charts (spec section 25).

A chart never carries a renderer configuration. It names a bounded data
reference, the two or three fields it uses, and the interaction the UI may
offer; the frontend owns colours and layout. Unknown fields are rejected, the
row budget is enforced, and a chart built from a calculation keeps that
calculation's evidence ids, so every bar can be traced back to a query.

Only ``line``, ``bar`` and ``table`` exist in V1. A contribution waterfall is a
bar chart with a signed delta field and an explicit sort, not a custom option
blob.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_CHART_POINTS = 1000
CHART_KINDS = ("line", "bar", "table")


class ChartValidationError(ValueError):
    pass


class ChartField(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=128)
    type: Literal["category", "quantitative", "temporal"]
    unit: str | None = Field(default=None, max_length=32)


class ChartEncoding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    x: ChartField
    y: ChartField
    series: ChartField | None = None


class ChartSort(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=128)
    direction: Literal["asc", "desc"]


class ChartInteraction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tooltip: bool = True
    zoom: bool = False
    drilldown_dimensions: list[str] = Field(default_factory=list, max_length=4)


class ChartDataRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["calculation", "result"]
    id: str


class ChartSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    kind: Literal["line", "bar", "table"]
    title: str = Field(min_length=1, max_length=200)
    data_ref: ChartDataRef
    encoding: ChartEncoding
    sort: ChartSort | None = None
    interaction: ChartInteraction = Field(default_factory=ChartInteraction)
    evidence_ids: list[str] = Field(default_factory=list, max_length=64)
    empty_state: str = Field(default="当前条件下没有数据", max_length=200)


def validate_chart_data(spec: ChartSpec, rows: list[dict[str, Any]]) -> None:
    """Reject a chart whose fields are absent, unbounded or mis-ordered."""
    if len(rows) > MAX_CHART_POINTS:
        raise ChartValidationError(
            f"chart data has {len(rows)} points; the limit is {MAX_CHART_POINTS}"
        )
    if not rows:
        return
    available = set(rows[0])
    for field in (spec.encoding.x, spec.encoding.y, spec.encoding.series):
        if field is not None and field.field not in available:
            raise ChartValidationError(f"chart field '{field.field}' is not in the data")
    if spec.sort is not None and spec.sort.field not in available:
        raise ChartValidationError(f"sort field '{spec.sort.field}' is not in the data")
    for dimension in spec.interaction.drilldown_dimensions:
        if len(dimension.strip()) == 0:
            raise ChartValidationError("drilldown dimensions must be named")
    if spec.kind == "line":
        values = [row.get(spec.encoding.x.field) for row in rows]
        if any(value is None for value in values):
            raise ChartValidationError("a line chart needs a value on every x point")
        if values != sorted(values):
            raise ChartValidationError("a line chart's x values must be ordered")


def contribution_chart(
    groups: list[dict[str, Any]],
    dimension: list[str],
    *,
    calculation_id: str,
    title: str,
    unit: str,
    currency: str | None,
    metric_key: str,
    query_ids: list[str],
    empty_state: str = "当前条件下没有可比的分组",
) -> dict[str, Any]:
    """Turn a contribution decomposition into a bounded bar chart.

    The rows are the groups ranked by absolute delta (the same order the report
    cites), and each row keeps the group key so a drilldown can bind the value
    as a query parameter. Points beyond the budget are dropped and reported,
    never silently summarised as "other".
    """
    ordered = sorted(groups, key=lambda group: abs(Decimal(str(group["delta"]))), reverse=True)
    kept = ordered[:MAX_CHART_POINTS]
    rows = [
        {
            "group": group.get("label"),
            "key": " / ".join(
                "NULL" if item is None else str(item) for item in group.get("key") or []
            ),
            "dimension_values": list(group.get("key") or []),
            "delta": str(group["delta"]),
            "net_change_share": group.get("net_change_share"),
        }
        for group in kept
    ]
    y_unit = currency or unit
    spec = ChartSpec(
        kind="bar",
        title=title,
        data_ref=ChartDataRef(type="calculation", id=calculation_id),
        encoding=ChartEncoding(
            x=ChartField(field="key", type="category"),
            y=ChartField(field="delta", type="quantitative", unit=y_unit),
        ),
        sort=ChartSort(field="delta", direction="asc"),
        interaction=ChartInteraction(
            tooltip=True,
            zoom=False,
            drilldown_dimensions=list(dimension),
        ),
        evidence_ids=query_ids,
    )
    validate_chart_data(spec, rows)
    return {
        "kind": "chart",
        "metric_key": metric_key,
        "spec": spec.model_dump(mode="json"),
        "data": rows,
        "truncated": len(ordered) > len(kept),
        "dropped_points": max(0, len(ordered) - len(kept)),
    }


def build_bounded_view(chart: dict[str, Any], *, offset: int = 0, limit: int = 200) -> dict[str, Any]:
    """Bounded read view for ``GET /charts/{id}`` (spec 25: <=1000 points)."""
    data = list(chart.get("data") or [])
    window = data[offset : offset + limit]
    return {
        "schema_version": 1,
        "id": chart.get("id"),
        "spec": chart.get("spec"),
        "data": window,
        "total_points": len(data),
        "offset": offset,
        "limit": limit,
        "truncated": chart.get("truncated", False),
        "dropped_points": chart.get("dropped_points", 0),
        "empty_state": (chart.get("spec") or {}).get("empty_state"),
    }
