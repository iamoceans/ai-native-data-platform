"""Query endpoints, results and SSE (spec sections 14, 22, 23)."""

from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import current_auth, get_db, get_settings_dep, get_trace_id, require_csrf
from app.api.dto import (
    QueryDetail,
    QueryListResponse,
    QueryResultResponse,
    QuerySubmitRequest,
    QuerySubmitResponse,
    ResultPayload,
)
from app.api.serializers import query_to_detail
from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import TERMINAL_QUERY_STATUSES, DatasetAction, ErrorCode, QueryStatus
from app.db import get_session_factory
from app.errors import ApiError
from app.ids import utcnow
from app.query import gateway as query_gateway
from app.repositories import datasets as datasets_repo
from app.repositories import events as events_repo
from app.repositories import queries as queries_repo
from app.results.cursor import decode_list_cursor, decode_result_cursor, encode_list_cursor, encode_result_cursor
from app.runtime import get_result_store

router = APIRouter(tags=["queries"])

ROUTE_KEY = "GET /queries"
RESULT_PAGE_DEFAULT = 100
RESULT_PAGE_MAX = 500
SSE_POLL_SECONDS = 1.0
SSE_HEARTBEAT_SECONDS = 15.0
SSE_PERMISSION_CHECK_SECONDS = 5.0
SSE_MAX_SECONDS = 30 * 60


def load_own_query(session: Session, auth: AuthContext, query_id: uuid.UUID):
    job = queries_repo.get_query(session, query_id)
    if job is None or job.user_id != auth.user.id:
        # Cross-user private resources are invisible: 404, not 403 (spec 22).
        raise ApiError(ErrorCode.NOT_FOUND, "query not found or not visible")
    return job


def ensure_query_dataset_permission(session: Session, auth: AuthContext, query_id: uuid.UUID) -> None:
    """Downloading results requires current data permission (spec 12.3)."""
    from app.models.orm import QueryDependency

    dependencies = session.query(QueryDependency).filter_by(query_id=query_id).all()
    for dependency in dependencies:
        if not datasets_repo.has_dataset_action(
            session, auth.role_ids, dependency.dataset_id, DatasetAction.QUERY
        ):
            raise ApiError(
                ErrorCode.PERMISSION_DENIED,
                "data permission for this query's datasets was revoked",
                details={"dataset_id": str(dependency.dataset_id)},
            )


@router.post("/queries", response_model=QuerySubmitResponse, status_code=202)
def submit_query(
    payload: QuerySubmitRequest,
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> QuerySubmitResponse:
    if idempotency_key is not None and (len(idempotency_key) > 128 or not idempotency_key.strip()):
        raise ApiError(ErrorCode.VALIDATION_ERROR, "Idempotency-Key must be 1..128 characters")
    job, created = query_gateway.submit_query(
        session,
        auth=auth,
        request=payload,
        settings=settings,
        request_id=uuid.UUID(getattr(request.state, "request_id", str(uuid.uuid4()))),
        trace_id=get_trace_id(request),
        idempotency_key=idempotency_key,
    )
    response.headers["Location"] = f"/api/v1/queries/{job.id}"
    if not created:
        response.headers["Idempotency-Replayed"] = "true"
    return QuerySubmitResponse(query_id=job.id, status=job.status)


@router.get("/queries", response_model=QueryListResponse)
def list_queries(
    request: Request,
    cursor: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> QueryListResponse:
    offset = (
        decode_list_cursor(settings.resolved_cursor_secret(), cursor, route=ROUTE_KEY)
        if cursor
        else 0
    )
    rows, total = queries_repo.list_queries_for_user(
        session, user_id=auth.user.id, status=status, limit=limit, offset=offset
    )
    results = queries_repo.list_results_for_queries(session, [row.id for row in rows])
    items = [QueryDetail(**query_to_detail(row, results.get(row.id))) for row in rows]
    next_cursor = None
    if offset + len(rows) < total:
        next_cursor = encode_list_cursor(
            settings.resolved_cursor_secret(), route=ROUTE_KEY, offset=offset + len(rows)
        )
    return QueryListResponse(items=items, next_cursor=next_cursor)


@router.get("/queries/{query_id}", response_model=QueryDetail)
def get_query(
    query_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> QueryDetail:
    job = load_own_query(session, auth, query_id)
    result = queries_repo.get_result(session, job.id)
    return QueryDetail(**query_to_detail(job, result))


@router.post("/queries/{query_id}/cancel", response_model=QueryDetail)
def cancel_query(
    query_id: uuid.UUID,
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> QueryDetail:
    job = load_own_query(session, auth, query_id)
    before = QueryStatus(job.status)
    job = queries_repo.request_cancel(session, job)
    if before == QueryStatus.QUEUED and job.status == QueryStatus.CANCELLED:
        events_repo.add_event(
            session,
            resource_kind="query",
            resource_id=job.id,
            event_type="query.finished",
            payload={"query_id": str(job.id), "status": "CANCELLED", "code": "QUERY_CANCELLED"},
        )
    elif job.status == QueryStatus.CANCEL_REQUESTED:
        events_repo.add_event(
            session,
            resource_kind="query",
            resource_id=job.id,
            event_type="query.cancel_requested",
            payload={"query_id": str(job.id), "status": str(job.status)},
        )
    result = queries_repo.get_result(session, job.id)
    if job.status in TERMINAL_QUERY_STATUSES:
        response.status_code = 200
    else:
        response.status_code = 202
    return QueryDetail(**query_to_detail(job, result))


@router.get("/queries/{query_id}/results", response_model=QueryResultResponse)
def get_results(
    query_id: uuid.UUID,
    request: Request,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=RESULT_PAGE_DEFAULT, ge=1, le=RESULT_PAGE_MAX),
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> QueryResultResponse:
    job = load_own_query(session, auth, query_id)
    ensure_query_dataset_permission(session, auth, job.id)
    trace_id = get_trace_id(request)
    result = queries_repo.get_result(session, job.id)
    if result is None:
        if job.status in TERMINAL_QUERY_STATUSES:
            if job.status != QueryStatus.SUCCEEDED:
                return QueryResultResponse(
                    query_id=job.id, status=job.status, result=None,
                    warnings=[job.error.get("code", "NO_RESULT") if job.error else "NO_RESULT"],
                    trace_id=trace_id,
                )
            raise ApiError(ErrorCode.RESULT_UNAVAILABLE, "result files are missing for this query")
        return QueryResultResponse(
            query_id=job.id, status=job.status, result=None, warnings=[], trace_id=trace_id
        )
    if result.expires_at <= utcnow():
        raise ApiError(ErrorCode.RESULT_EXPIRED, "result has expired")

    store = get_result_store()
    payload = store.read_json(result.storage_key)
    schema_hash = str(result.schema_json.get("schema_hash") or result.content_hash)
    offset = 0
    if cursor:
        offset = decode_result_cursor(
            settings.resolved_cursor_secret(), cursor, result_id=result.id, schema_hash=schema_hash
        )
    rows = payload.get("rows", [])
    page = rows[offset : offset + limit]
    next_cursor = None
    if offset + len(page) < len(rows):
        next_cursor = encode_result_cursor(
            settings.resolved_cursor_secret(),
            result_id=result.id,
            offset=offset + len(page),
            schema_hash=schema_hash,
        )
    warnings: list[str] = []
    if result.truncated:
        warnings.append(
            "RESULT_TRUNCATED: the query hit the row/byte limit; do not use truncated rows for exact totals"
        )
    return QueryResultResponse(
        query_id=job.id,
        status=job.status,
        result=ResultPayload(
            id=result.id,
            row_count=int(result.row_count),
            truncated=bool(result.truncated),
            columns=payload.get("columns", []),
            rows=page,
            next_cursor=next_cursor,
            expires_at=result.expires_at,
        ),
        warnings=warnings,
        trace_id=trace_id,
    )


@router.get("/queries/{query_id}/events")
def query_events(
    query_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> StreamingResponse:
    load_own_query(session, auth, query_id)
    last_event_id = request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id")
    last_id = int(last_event_id) if last_event_id and str(last_event_id).isdigit() else 0
    user_id = auth.user.id
    session_factory = get_session_factory()

    def stream():
        yield ": connected\n\n"
        started = time.monotonic()
        last_heartbeat = time.monotonic()
        last_permission_check = 0.0
        last_sent = last_id
        while True:
            if time.monotonic() - started > SSE_MAX_SECONDS:
                yield "event: stream.end\ndata: {\"reason\":\"max_duration\"}\n\n"
                return
            with session_factory() as poll:
                oldest = events_repo.oldest_event_id(
                    poll, resource_kind="query", resource_id=query_id
                )
                # Expired cursor: the client explicitly resumed from an id that
                # is older than the retained window. Without a Last-Event-ID we
                # replay whatever is retained (the client refetches current
                # state on reconnect).
                if oldest is not None and last_sent > 0 and last_sent < oldest - 1:
                    yield (
                        "event: error\ndata: "
                        '{"code":"EVENT_CURSOR_EXPIRED","message":"event history no longer covers the requested cursor"}'
                        "\n\n"
                    )
                    return
                events = events_repo.events_after(
                    poll, resource_kind="query", resource_id=query_id, after_id=last_sent
                )
                job = queries_repo.get_query(poll, query_id)
                status = job.status if job is not None else None
            for event in events:
                yield _format_event(event)
                last_sent = int(event.id)
            if status in TERMINAL_QUERY_STATUSES and not events:
                yield "event: stream.end\ndata: {\"status\":\"" + str(status) + "\"}\n\n"
                return
            if time.monotonic() - last_permission_check > SSE_PERMISSION_CHECK_SECONDS:
                last_permission_check = time.monotonic()
                if not _still_authorized(session_factory, query_id, user_id):
                    yield "event: stream.closed\ndata: {\"reason\":\"permission_revoked\"}\n\n"
                    return
            if time.monotonic() - last_heartbeat > SSE_HEARTBEAT_SECONDS:
                last_heartbeat = time.monotonic()
                yield ": heartbeat\n\n"
            time.sleep(SSE_POLL_SECONDS)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )


def _still_authorized(session_factory, query_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    from app.models.orm import QueryDependency
    from app.repositories import users as users_repo

    with session_factory() as session:
        job = queries_repo.get_query(session, query_id)
        if job is None or job.user_id != user_id:
            return False
        user = users_repo.get_user(session, user_id)
        if user is None or not user.active:
            return False
        role_ids = users_repo.role_ids_for_user(session, user_id)
        dependencies = session.query(QueryDependency).filter_by(query_id=query_id).all()
        for dependency in dependencies:
            if not datasets_repo.has_dataset_action(
                session, role_ids, dependency.dataset_id, DatasetAction.QUERY
            ):
                return False
        return True


def _format_event(event) -> str:
    import json

    return (
        f"id: {event.id}\n"
        f"event: {event.event_type}\n"
        f"data: {json.dumps(event.payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
    )
