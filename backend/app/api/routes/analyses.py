"""Agent session, analysis, report, evidence and SSE endpoints."""

from __future__ import annotations

import json
import time
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent.budget import AnalysisBudget
from app.agent.state import AnalysisStatus, TERMINAL_ANALYSIS_STATUSES
from app.api.deps import current_auth, get_db, get_settings_dep, get_trace_id, require_csrf
from app.api.dto import (
    AgentMessageListResponse,
    AgentMessageResponse,
    AgentSessionCreate,
    AgentSessionResponse,
    AnalysisCreate,
    AnalysisDetail,
    AnalysisListResponse,
    AnalysisStepResponse,
    AnalysisSubmitResponse,
)
from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import DatasetAction, ErrorCode
from app.db import get_session_factory
from app.errors import ApiError
from app.ids import sha256_hex
from app.models.orm import QueryDependency
from app.repositories import analyses as analyses_repo
from app.repositories import audit as audit_repo
from app.repositories import datasets as datasets_repo
from app.repositories import events as events_repo
from app.repositories import idempotency as idempotency_repo
from app.repositories import policy as policy_repo
from app.repositories import queries as queries_repo
from app.repositories import queue as queue_repo
from app.results.cursor import decode_list_cursor, encode_list_cursor

router = APIRouter(tags=["analyses"])

ANALYSES_ROUTE = "GET /analyses"
MESSAGES_ROUTE = "GET /sessions/messages"
SSE_POLL_SECONDS = 1.0
SSE_HEARTBEAT_SECONDS = 15.0
SSE_MAX_SECONDS = 30 * 60


def _own_session(session: Session, auth: AuthContext, session_id: uuid.UUID):
    row = analyses_repo.get_session(session, session_id)
    if row is None or row.user_id != auth.user.id:
        raise ApiError(ErrorCode.NOT_FOUND, "session not found or not visible")
    return row


def _own_analysis(session: Session, auth: AuthContext, analysis_id: uuid.UUID):
    row = analyses_repo.get_analysis(session, analysis_id)
    if row is None or row.user_id != auth.user.id:
        raise ApiError(ErrorCode.NOT_FOUND, "analysis not found or not visible")
    return row


def _detail(session: Session, task) -> AnalysisDetail:
    steps = analyses_repo.list_steps(session, task.id)
    return AnalysisDetail(
        id=task.id,
        session_id=task.session_id,
        parent_id=task.parent_id,
        status=task.status,
        question=task.question,
        context=task.context or {},
        plan=task.plan or {},
        budget=task.budget or {},
        state=task.state or {},
        checkpoint_version=int(task.checkpoint_version),
        policy_revision=int(task.policy_revision),
        final_report=task.final_report,
        steps=[
            AnalysisStepResponse(
                key=step.step_key,
                status=step.status,
                tool_name=step.tool_name,
                observation=step.observation,
                error=step.error,
            )
            for step in steps
        ],
        created_at=task.created_at,
        finished_at=task.finished_at,
    )


@router.post("/sessions", response_model=AgentSessionResponse, status_code=201)
def create_session(
    payload: AgentSessionCreate,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> AgentSessionResponse:
    if "analysis.create" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "analysis capability required")
    row = analyses_repo.create_session(session, user_id=auth.user.id, title=payload.title)
    return AgentSessionResponse(id=row.id, title=row.title, created_at=row.created_at)


@router.get("/sessions/{session_id}/messages", response_model=AgentMessageListResponse)
def list_messages(
    session_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> AgentMessageListResponse:
    _own_session(db, auth, session_id)
    route = f"{MESSAGES_ROUTE}:{session_id}"
    offset = decode_list_cursor(settings.resolved_cursor_secret(), cursor, route=route) if cursor else 0
    rows, total = analyses_repo.list_messages(db, session_id=session_id, limit=limit, offset=offset)
    next_cursor = (
        encode_list_cursor(settings.resolved_cursor_secret(), route=route, offset=offset + len(rows))
        if offset + len(rows) < total
        else None
    )
    return AgentMessageListResponse(
        items=[
            AgentMessageResponse(id=row.id, role=row.role, content=row.content, created_at=row.created_at)
            for row in rows
        ],
        next_cursor=next_cursor,
    )


@router.post("/analyses", response_model=AnalysisSubmitResponse, status_code=202)
def create_analysis(
    payload: AnalysisCreate,
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> AnalysisSubmitResponse:
    if "analysis.create" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "analysis capability required")
    _own_session(session, auth, payload.session_id)
    if payload.parent_id is not None:
        _own_analysis(session, auth, payload.parent_id)
    request_hash = sha256_hex(
        json.dumps(payload.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    )
    if idempotency_key:
        if len(idempotency_key) > 128 or not idempotency_key.strip():
            raise ApiError(ErrorCode.VALIDATION_ERROR, "Idempotency-Key must be 1..128 characters")
        existing = idempotency_repo.lookup(
            session,
            user_id=auth.user.id,
            route="POST /analyses",
            key=idempotency_key,
            request_hash=request_hash,
        )
        if existing:
            task = analyses_repo.get_analysis(session, existing)
            if task is None:
                raise ApiError(ErrorCode.CONFLICT, "idempotency record points to a missing analysis")
            response.headers["Idempotency-Replayed"] = "true"
            return AnalysisSubmitResponse(analysis_id=task.id, status=task.status)
    budget = AnalysisBudget.start().as_dict()
    task = analyses_repo.create_analysis(
        session,
        session_id=payload.session_id,
        user_id=auth.user.id,
        question=payload.question,
        context=payload.context.model_dump(mode="json"),
        parent_id=payload.parent_id,
        budget=budget,
        policy_revision=policy_repo.get_revision(session),
    )
    analyses_repo.add_message(
        session,
        session_id=payload.session_id,
        role="user",
        content={"question": payload.question, "analysis_id": str(task.id)},
    )
    queue_repo.enqueue(session, kind="analysis", resource_id=task.id)
    events_repo.add_event(
        session,
        resource_kind="analysis",
        resource_id=task.id,
        event_type="analysis.created",
        payload={"analysis_id": str(task.id), "status": str(task.status)},
    )
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="analysis.create",
        resource_type="analysis",
        resource_id=str(task.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"metric_key": payload.context.metric_key},
    )
    if idempotency_key:
        idempotency_repo.store(
            session,
            user_id=auth.user.id,
            route="POST /analyses",
            key=idempotency_key,
            request_hash=request_hash,
            resource_id=task.id,
            ttl_hours=settings.idempotency_ttl_hours,
        )
    response.headers["Location"] = f"/api/v1/analyses/{task.id}"
    return AnalysisSubmitResponse(analysis_id=task.id, status=task.status)


@router.get("/analyses", response_model=AnalysisListResponse)
def list_analyses(
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    cursor: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> AnalysisListResponse:
    offset = decode_list_cursor(settings.resolved_cursor_secret(), cursor, route=ANALYSES_ROUTE) if cursor else 0
    rows, total = analyses_repo.list_for_user(
        session, user_id=auth.user.id, status=status, limit=limit, offset=offset
    )
    next_cursor = (
        encode_list_cursor(
            settings.resolved_cursor_secret(), route=ANALYSES_ROUTE, offset=offset + len(rows)
        )
        if offset + len(rows) < total
        else None
    )
    return AnalysisListResponse(items=[_detail(session, row) for row in rows], next_cursor=next_cursor)


@router.get("/analyses/{analysis_id}", response_model=AnalysisDetail)
def get_analysis(
    analysis_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> AnalysisDetail:
    return _detail(session, _own_analysis(session, auth, analysis_id))


def _ensure_evidence_permission(session: Session, auth: AuthContext, analysis_id: uuid.UUID) -> None:
    query_ids = [row.id for row in analyses_repo.list_queries(session, analysis_id)]
    dependencies = (
        session.query(QueryDependency).filter(QueryDependency.query_id.in_(query_ids)).all()
        if query_ids
        else []
    )
    for dependency in dependencies:
        if not datasets_repo.has_dataset_action(
            session, auth.role_ids, dependency.dataset_id, DatasetAction.QUERY
        ):
            raise ApiError(ErrorCode.PERMISSION_DENIED, "data permission for this evidence was revoked")


@router.get("/analyses/{analysis_id}/report")
def get_report(
    analysis_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> dict:
    task = _own_analysis(session, auth, analysis_id)
    _ensure_evidence_permission(session, auth, analysis_id)
    if task.final_report is None:
        raise ApiError(ErrorCode.RESULT_UNAVAILABLE, "analysis report is not available")
    return task.final_report


@router.get("/analyses/{analysis_id}/evidence")
def get_evidence(
    analysis_id: uuid.UUID,
    claim_id: str | None = Query(default=None),
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> dict:
    task = _own_analysis(session, auth, analysis_id)
    _ensure_evidence_permission(session, auth, analysis_id)
    claims = list((task.final_report or {}).get("claims", []))
    if claim_id:
        claims = [claim for claim in claims if claim.get("id") == claim_id]
        if not claims:
            raise ApiError(ErrorCode.NOT_FOUND, "claim not found")
    queries = analyses_repo.list_queries(session, analysis_id)
    artifacts = analyses_repo.list_artifacts(session, analysis_id)
    return {
        "schema_version": 1,
        "analysis_id": str(analysis_id),
        "claims": claims,
        "queries": [
            {
                "id": str(query.id),
                "status": query.status,
                "validated_sql": query.validated_sql,
                "sql_hash": query.sql_hash,
                "created_at": query.created_at,
            }
            for query in queries
        ],
        "calculations": [artifact.content for artifact in artifacts if artifact.kind == "calculation"],
    }


@router.post("/analyses/{analysis_id}/cancel", response_model=AnalysisDetail)
def cancel_analysis(
    analysis_id: uuid.UUID,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> AnalysisDetail:
    task = _own_analysis(session, auth, analysis_id)
    if task.status not in TERMINAL_ANALYSIS_STATUSES:
        for query in analyses_repo.list_queries(session, analysis_id):
            queries_repo.request_cancel(session, query)
        analyses_repo.set_status(task, AnalysisStatus.CANCELLED)
        events_repo.add_event(
            session,
            resource_kind="analysis",
            resource_id=task.id,
            event_type="analysis.cancelled",
            payload={"analysis_id": str(task.id), "status": str(task.status)},
        )
    return _detail(session, task)


@router.get("/analyses/{analysis_id}/events")
def analysis_events(
    analysis_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> StreamingResponse:
    _own_analysis(session, auth, analysis_id)
    supplied = request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id")
    last_id = int(supplied) if supplied and supplied.isdigit() else 0
    user_id = auth.user.id
    session_factory = get_session_factory()

    def stream():
        yield ": connected\n\n"
        started = time.monotonic()
        last_heartbeat = started
        last_sent = last_id
        while time.monotonic() - started <= SSE_MAX_SECONDS:
            with session_factory() as poll:
                task = analyses_repo.get_analysis(poll, analysis_id)
                if task is None or task.user_id != user_id:
                    yield 'event: stream.closed\ndata: {"reason":"not_authorized"}\n\n'
                    return
                events = events_repo.events_after(
                    poll, resource_kind="analysis", resource_id=analysis_id, after_id=last_sent
                )
                status = task.status
            for event in events:
                yield (
                    f"id: {event.id}\n"
                    f"event: {event.event_type}\n"
                    f"data: {json.dumps(event.payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
                )
                last_sent = int(event.id)
            if status in TERMINAL_ANALYSIS_STATUSES and not events:
                yield f'event: stream.end\ndata: {{"status":"{status}"}}\n\n'
                return
            if time.monotonic() - last_heartbeat > SSE_HEARTBEAT_SECONDS:
                last_heartbeat = time.monotonic()
                yield ": heartbeat\n\n"
            time.sleep(SSE_POLL_SECONDS)
        yield 'event: stream.end\ndata: {"reason":"max_duration"}\n\n'

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
