"""Metric definitions endpoint (spec section 15)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_auth, get_db
from app.api.dto import MetricListResponse, MetricSummary
from app.auth.sessions import AuthContext
from app.models.orm import MetricDefinition

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_model=MetricListResponse)
def list_metrics(
    request: Request,
    _auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> MetricListResponse:
    query = request.query_params.get("q")
    statement = select(MetricDefinition).where(MetricDefinition.active.is_(True))
    rows = list(session.execute(statement.order_by(MetricDefinition.metric_key)).scalars())
    items: list[MetricSummary] = []
    for row in rows:
        definition = row.definition or {}
        name = str(definition.get("name", row.metric_key))
        if query and query.lower() not in f"{row.metric_key} {name}".lower():
            continue
        datasets = definition.get("datasets") or (
            [definition["dataset"]] if definition.get("dataset") else []
        )
        items.append(
            MetricSummary(
                metric_key=row.metric_key,
                version=int(row.version),
                name=name,
                unit=str(definition.get("unit", "")),
                currency=definition.get("currency"),
                timezone=str(definition.get("timezone", "UTC")),
                grain=list(definition.get("grain", [])),
                allowed_dimensions=list(definition.get("allowed_dimensions", [])),
                aggregation_kind=str(definition.get("aggregation_kind", "")),
                datasets=[str(item) for item in datasets],
                formula=str(definition.get("formula", "")),
            )
        )
    return MetricListResponse(items=items)
