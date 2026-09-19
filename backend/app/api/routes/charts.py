"""Chart reads and drilldowns (spec sections 22 and 25).

A chart is an artifact of an analysis, so reading one re-checks the analysis
owner and the data permissions behind it: a revoked grant closes the chart too.
A drilldown never rewrites the parent analysis; it creates a child analysis with
the drilled value bound as a *parameter*, carrying `parent_id` so the lineage of
the question stays visible.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.agent.budget import AnalysisBudget
from app.api.deps import current_auth, get_db, get_settings_dep, get_trace_id, require_csrf
from app.api.dto import ChartDrilldownRequest, ChartDrilldownResponse
from app.auth.sessions import AuthContext
from app.charts.spec import build_bounded_view
from app.config import Settings
from app.constants import DatasetAction, ErrorCode
from app.errors import ApiError
from app.models.orm import QueryDependency
from app.repositories import analyses as analyses_repo
from app.repositories import audit as audit_repo
from app.repositories import datasets as datasets_repo
from app.repositories import events as events_repo
from app.repositories import policy as policy_repo
from app.repositories import queue as queue_repo

router = APIRouter(tags=["charts"])


def _chart_for(session: Session, auth: AuthContext, chart_id: uuid.UUID) -> tuple[dict, object]:
    artifact = analyses_repo.get_artifact(session, chart_id)
    if artifact is None or artifact.kind != "chart":
        raise ApiError(ErrorCode.NOT_FOUND, "chart not found or not visible")
    task = analyses_repo.get_analysis(session, artifact.analysis_id)
    if task is None or task.user_id != auth.user.id:
        raise ApiError(ErrorCode.NOT_FOUND, "chart not found or not visible")
    query_ids = [row.id for row in analyses_repo.list_queries(session, task.id)]
    dependencies = (
        session.query(QueryDependency).filter(QueryDependency.query_id.in_(query_ids)).all()
        if query_ids
        else []
    )
    for dependency in dependencies:
        if not datasets_repo.has_dataset_action(
            session, auth.role_ids, dependency.dataset_id, DatasetAction.QUERY
        ):
            raise ApiError(ErrorCode.PERMISSION_DENIED, "data permission for this chart was revoked")
    return artifact.content, task


@router.get("/charts/{chart_id}")
def get_chart(
    chart_id: uuid.UUID,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=200, ge=1, le=1000),
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> dict:
    content, _task = _chart_for(session, auth, chart_id)
    return build_bounded_view(content, offset=offset, limit=limit)


@router.post("/charts/{chart_id}/drilldown", response_model=ChartDrilldownResponse, status_code=202)
def drilldown(
    chart_id: uuid.UUID,
    payload: ChartDrilldownRequest,
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> ChartDrilldownResponse:
    if "analysis.create" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "analysis capability required")
    content, parent = _chart_for(session, auth, chart_id)
    spec = content.get("spec") or {}
    allowed = list((spec.get("interaction") or {}).get("drilldown_dimensions") or [])
    if allowed and payload.dimension not in allowed:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            f"dimension '{payload.dimension}' cannot be used to drill down this chart",
            details={"allowed": allowed},
        )
    context = dict(parent.context or {})
    dimensions = [
        dimension
        for dimension in list(context.get("dimensions") or [])
        if dimension != payload.dimension
    ]
    filters = {**dict(context.get("filters") or {}), payload.dimension: payload.value}
    question = (
        f"{parent.question}（下钻：{payload.dimension}={payload.value}，"
        f"{payload.period} 期间）"
    )
    child_context = {
        **context,
        "dimensions": dimensions,
        "filters": filters,
    }
    task = analyses_repo.create_analysis(
        session,
        session_id=parent.session_id,
        user_id=auth.user.id,
        question=question,
        context=child_context,
        parent_id=parent.id,
        budget=AnalysisBudget.start().as_dict(),
        policy_revision=policy_repo.get_revision(session),
    )
    analyses_repo.add_message(
        session,
        session_id=parent.session_id,
        role="user",
        content={
            "question": question,
            "analysis_id": str(task.id),
            "drilldown": {"chart_id": str(chart_id), "dimension": payload.dimension, "value": payload.value},
        },
    )
    queue_repo.enqueue(session, kind="analysis", resource_id=task.id)
    events_repo.add_event(
        session,
        resource_kind="analysis",
        resource_id=task.id,
        event_type="analysis.created",
        payload={
            "analysis_id": str(task.id),
            "status": str(task.status),
            "parent_id": str(parent.id),
            "drilldown": {"dimension": payload.dimension, "value": payload.value},
        },
    )
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="chart.drilldown",
        resource_type="analysis",
        resource_id=str(task.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={
            "chart_id": str(chart_id),
            "parent_id": str(parent.id),
            "dimension": payload.dimension,
        },
    )
    response.headers["Location"] = f"/api/v1/analyses/{task.id}"
    return ChartDrilldownResponse(
        analysis_id=task.id,
        status=task.status,
        parent_id=parent.id,
        filters=filters,
    )
