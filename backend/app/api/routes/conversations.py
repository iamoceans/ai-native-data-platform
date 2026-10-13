"""Conversation endpoints: sessions, turns and the message transcript.

Design: `docs/superpowers/specs/2026-10-13-askdata-conversation-skills-design.md`
sections 5-6 and 9. The API validates and enqueues; the orchestration worker
resolves intent and drives the analysis. Nothing here executes user SQL.

Write endpoints are gated by `AIND_CONVERSATIONAL_ASK_ENABLED` (design 14); the
legacy `POST /analyses` path keeps working either way.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.agent.evidence_access import redact_report_message
from app.api.deps import current_auth, get_db, get_settings_dep, get_trace_id, require_csrf
from app.api.dto import (
    AgentMessageListResponse,
    AgentMessageResponse,
    AgentSessionCreate,
    AgentSessionListResponse,
    AgentSessionResponse,
    AgentSessionSummary,
    SessionPatch,
    TurnCreate,
    TurnResponse,
)
from app.api.session_activity import ensure_session_idle
from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import ErrorCode, ConversationTurnStatus
from app.db import get_session_factory
from app.errors import ApiError
from app.ids import sha256_hex, utcnow
from app.repositories import analyses as analyses_repo
from app.repositories import audit as audit_repo
from app.repositories import conversation as conversation_repo
from app.repositories import events as events_repo
from app.repositories import queue as queue_repo
from app.repositories import users as users_repo
from app.results.cursor import decode_list_cursor, encode_list_cursor

router = APIRouter(tags=["conversations"])

SESSIONS_ROUTE = "GET /sessions"
SSE_POLL_SECONDS = 1.0
SSE_HEARTBEAT_SECONDS = 15.0
SSE_MAX_SECONDS = 30 * 60
MESSAGES_ROUTE = "GET /sessions/messages"


def _own_session(
    session: Session, auth: AuthContext, session_id: uuid.UUID, *, for_update: bool = False
):
    row = (analyses_repo.get_session_for_update(session, session_id) if for_update
           else analyses_repo.get_session(session, session_id))
    if row is None or row.user_id != auth.user.id:
        raise ApiError(ErrorCode.NOT_FOUND, "session not found or not visible")
    return row


def _require_conversations(settings: Settings) -> None:
    if not settings.conversational_ask_enabled:
        raise ApiError(
            ErrorCode.CONVERSATIONAL_DISABLED,
            "conversational Ask is disabled (AIND_CONVERSATIONAL_ASK_ENABLED=0)",
        )


def _session_response(session: Session, row) -> AgentSessionResponse:
    active = conversation_repo.active_turn(session, row.id)
    return AgentSessionResponse(
        id=row.id,
        title=row.title,
        created_at=row.created_at,
        updated_at=row.updated_at,
        archived_at=row.archived_at,
        context_version=int(row.context_version),
        resolved_context=row.resolved_context or {},
        skill_config=row.skill_config or {},
        active_turn_id=active.id if active is not None else None,
    )


def _turn_response(session: Session, turn) -> TurnResponse:
    analysis = conversation_repo.analysis_for_turn(session, turn.id)
    return TurnResponse(
        id=turn.id,
        session_id=turn.session_id,
        status=turn.status,
        intent=turn.intent,
        user_message_id=turn.user_message_id,
        assistant_message_id=turn.assistant_message_id,
        analysis_id=analysis.id if analysis is not None else None,
        base_context_version=int(turn.base_context_version),
        context_after=turn.context_after,
        skill_snapshot=turn.skill_snapshot or {},
        error=turn.error,
        created_at=turn.created_at,
        finished_at=turn.finished_at,
    )


@router.get("/sessions", response_model=AgentSessionListResponse)
def list_sessions(
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    include_archived: bool = Query(default=False),
    cursor: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
) -> AgentSessionListResponse:
    offset = (
        decode_list_cursor(settings.resolved_cursor_secret(), cursor, route=SESSIONS_ROUTE)
        if cursor
        else 0
    )
    rows, total = analyses_repo.list_sessions(
        db, user_id=auth.user.id, include_archived=include_archived, limit=limit, offset=offset
    )
    next_cursor = (
        encode_list_cursor(
            settings.resolved_cursor_secret(), route=SESSIONS_ROUTE, offset=offset + len(rows)
        )
        if offset + len(rows) < total
        else None
    )
    items = []
    for row in rows:
        active = conversation_repo.active_turn(db, row.id)
        items.append(
            AgentSessionSummary(
                id=row.id,
                title=row.title,
                created_at=row.created_at,
                updated_at=row.updated_at,
                archived_at=row.archived_at,
                context_version=int(row.context_version),
                active_turn_id=active.id if active is not None else None,
            )
        )
    return AgentSessionListResponse(items=items, next_cursor=next_cursor)


@router.post("/sessions", response_model=AgentSessionResponse, status_code=201)
def create_session(
    payload: AgentSessionCreate,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> AgentSessionResponse:
    if "analysis.create" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "analysis capability required")
    row = analyses_repo.create_session(session, user_id=auth.user.id, title=payload.title)
    if payload.skill_config:
        # Shape only; ids are validated when a turn loads skills (P2/P3).
        analyses_repo.update_session_scope(session, row, skill_config=payload.skill_config)
    return _session_response(session, row)


@router.get("/sessions/{session_id}", response_model=AgentSessionResponse)
def get_session(
    session_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> AgentSessionResponse:
    row = _own_session(session, auth, session_id)
    return _session_response(session, row)


@router.patch("/sessions/{session_id}", response_model=AgentSessionResponse)
def patch_session(
    session_id: uuid.UUID,
    payload: SessionPatch,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> AgentSessionResponse:
    """Rename, archive or re-bind skills. Scope changes only happen through turns."""
    if "analysis.create" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "analysis capability required")
    row = _own_session(session, auth, session_id, for_update=True)
    if payload.archived is not None or payload.skill_config is not None:
        ensure_session_idle(session, row.id)
    if (
        payload.expected_context_version is not None
        and payload.expected_context_version != int(row.context_version)
    ):
        raise ApiError(
            ErrorCode.CONTEXT_VERSION_CONFLICT,
            "the session scope changed since you loaded it",
            details={"current_context_version": int(row.context_version)},
        )
    if payload.archived is not None:
        row.archived_at = utcnow() if payload.archived else None
    if payload.title is not None:
        row.title = payload.title
    if payload.skill_config is not None:
        analyses_repo.update_session_scope(session, row, skill_config=payload.skill_config)
    analyses_repo.touch_session(session, row)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="session.patch",
        resource_type="agent_session",
        resource_id=str(row.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={
            "renamed": payload.title is not None,
            "archived": payload.archived,
            "skills_changed": payload.skill_config is not None,
        },
    )
    return _session_response(session, row)


def _redact_if_revoked(session: Session, auth: AuthContext, content: dict[str, Any]) -> dict[str, Any]:
    return redact_report_message(session, user_id=auth.user.id, content=content)


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
            AgentMessageResponse(
                id=row.id,
                role=row.role,
                content=_redact_if_revoked(db, auth, analyses_repo.message_content(row)),
                created_at=row.created_at,
                sequence=int(row.sequence),
                turn_id=row.turn_id,
                content_schema_version=int(row.content_schema_version),
            )
            for row in rows
        ],
        next_cursor=next_cursor,
    )


def _request_hash(payload: TurnCreate) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, separators=(",", ":")
    )
    return sha256_hex(canonical)


@router.post("/sessions/{session_id}/turns", response_model=TurnResponse, status_code=202)
def create_turn(
    session_id: uuid.UUID,
    payload: TurnCreate,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TurnResponse:
    """Accept one user input atomically: message + turn + queue row."""
    _require_conversations(settings)
    if "analysis.create" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "analysis capability required")
    if not idempotency_key or not idempotency_key.strip() or len(idempotency_key) > 128:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR, "Idempotency-Key is required for a conversation turn (1..128 chars)"
        )
    row = analyses_repo.get_session_for_update(session, session_id)
    if row is None or row.user_id != auth.user.id:
        raise ApiError(ErrorCode.NOT_FOUND, "session not found or not visible")
    if row.archived_at is not None:
        raise ApiError(ErrorCode.CONFLICT, "session is archived; restore it before submitting")

    request_hash = _request_hash(payload)
    existing = conversation_repo.find_by_idempotency(
        session, session_id=session_id, idempotency_key=idempotency_key
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise ApiError(
                ErrorCode.IDEMPOTENCY_CONFLICT,
                "the same Idempotency-Key was used with a different turn request",
            )
        return _turn_response(session, existing)

    ensure_session_idle(session, session_id)
    if (
        payload.expected_context_version is not None
        and payload.expected_context_version != int(row.context_version)
    ):
        raise ApiError(
            ErrorCode.CONTEXT_VERSION_CONFLICT,
            "the session scope changed since you loaded it",
            details={"current_context_version": int(row.context_version)},
        )
    if payload.parent_analysis_id is not None:
        parent = analyses_repo.get_analysis(session, payload.parent_analysis_id)
        if (
            parent is None
            or parent.user_id != auth.user.id
            or parent.session_id != session_id
        ):
            # Design 6.2: the parent must belong to this user *and* this session.
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                "parent_analysis_id must reference an analysis in this session",
            )

    user_message = analyses_repo.add_message(
        session,
        session_id=session_id,
        role="user",
        content={
            "schema_version": 1,
            "kind": "user",
            "text": payload.text,
            "scope_patch": payload.scope_patch.model_dump(mode="json") if payload.scope_patch else None,
            "skill_selection": (
                payload.skill_selection.model_dump(mode="json") if payload.skill_selection else None
            ),
            "parent_analysis_id": str(payload.parent_analysis_id) if payload.parent_analysis_id else None,
        },
        content_schema_version=1,
    )
    turn = conversation_repo.create_turn(
        session,
        session_id=session_id,
        user_id=auth.user.id,
        user_message_id=user_message.id,
        request_hash=request_hash,
        idempotency_key=idempotency_key.strip(),
        base_context_version=int(row.context_version),
        context_before=dict(row.resolved_context or {}),
        deadline_at=utcnow() + timedelta(seconds=settings.conversation_turn_deadline_seconds),
    )
    # Link both ways inside the same transaction (the turn needed the message id
    # first); the transcript query orders by sequence, not by this link.
    user_message.turn_id = turn.id
    session.flush()
    queue_repo.enqueue(session, kind="conversation", resource_id=turn.id)
    analyses_repo.touch_session(session, row)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="turn.create",
        resource_type="conversation_turn",
        resource_id=str(turn.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"session_id": str(session_id), "idempotency_key": idempotency_key.strip()},
    )
    return _turn_response(session, turn)


@router.get("/sessions/{session_id}/turns/{turn_id}", response_model=TurnResponse)
def get_turn(
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> TurnResponse:
    _own_session(session, auth, session_id)
    turn = conversation_repo.get_turn(session, turn_id)
    if turn is None or turn.session_id != session_id:
        raise ApiError(ErrorCode.NOT_FOUND, "turn not found in this session")
    return _turn_response(session, turn)


@router.post("/sessions/{session_id}/turns/{turn_id}/cancel", response_model=TurnResponse)
def cancel_turn(
    session_id: uuid.UUID,
    turn_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> TurnResponse:
    """Cancel the turn and its unfinished analysis; idempotent."""
    _own_session(session, auth, session_id)
    turn = conversation_repo.get_turn_for_update(session, turn_id)
    if turn is None or turn.session_id != session_id:
        raise ApiError(ErrorCode.NOT_FOUND, "turn not found in this session")
    if turn.status in ("CANCELLED", "COMPLETED", "FAILED", "WAITING_INPUT"):
        return _turn_response(session, turn)
    analysis = conversation_repo.analysis_for_turn(session, turn.id)
    if analysis is not None:
        analyses_repo.cancel_task(session, analysis)
    conversation_repo.finish(
        session, turn, status=ConversationTurnStatus.CANCELLED,
        error={"code": ErrorCode.QUERY_CANCELLED, "message": "turn cancelled by the user"},
    )
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="turn.cancel",
        resource_type="conversation_turn",
        resource_id=str(turn.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"session_id": str(session_id), "analysis_id": str(analysis.id) if analysis else None},
    )
    return _turn_response(session, turn)


@router.get("/sessions/{session_id}/events")
def session_events(
    session_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> StreamingResponse:
    """SSE stream for one conversation (design 10.3).

    Events are persisted rows; the client resumes with `Last-Event-ID` and merges
    by id, so a reconnect cannot duplicate or reorder what it already applied.
    The stream ends when the session has no active turn left to report.
    """
    _own_session(session, auth, session_id)
    supplied = request.headers.get("Last-Event-ID") or request.query_params.get("last_event_id")
    last_id = int(supplied) if supplied and supplied.isdigit() else 0
    user_id = auth.user.id
    session_factory = get_session_factory()

    def _pairs(poll: Session) -> list[tuple[str, uuid.UUID]]:
        turns = analyses_repo.list_session_turn_ids(poll, session_id)
        analyses = analyses_repo.list_session_analysis_ids(poll, session_id)
        return [("conversation_turn", item) for item in turns] + [
            ("analysis", item) for item in analyses
        ]

    def stream():
        yield ": connected\n\n"
        started = time.monotonic()
        last_heartbeat = started
        last_sent = last_id
        announced_revoked: set[uuid.UUID] = set()
        while time.monotonic() - started <= SSE_MAX_SECONDS:
            with session_factory() as poll:
                row = analyses_repo.get_session(poll, session_id)
                if row is None or row.user_id != user_id:
                    yield 'event: stream.closed\ndata: {"reason":"not_authorized"}\n\n'
                    return
                events = events_repo.events_for_resources(
                    poll, pairs=_pairs(poll), after_id=last_sent
                )
                active = conversation_repo.active_turn(poll, session_id)
                # An analysis whose dataset grants were revoked while a client
                # watches must stop looking live. Announced once per stream.
                role_ids = users_repo.role_ids_for_user(poll, user_id)
                revoked = [
                    analysis_id
                    for analysis_id in analyses_repo.list_session_analysis_ids(poll, session_id)
                    if analysis_id not in announced_revoked
                    and not analyses_repo.analysis_dataset_actions_ok(
                        poll, role_ids=role_ids, analysis_id=analysis_id
                    )
                ]
            for event in events:
                yield (
                    f"id: {event.id}\n"
                    f"event: {event.event_type}\n"
                    f"data: {json.dumps(event.payload, ensure_ascii=False, separators=(',', ':'))}\n\n"
                )
                last_sent = int(event.id)
            for analysis_id in revoked:
                announced_revoked.add(analysis_id)
                yield (
                    "event: analysis.permission.revoked\n"
                    f'data: {{"analysis_id":"{analysis_id}","session_id":"{session_id}"}}\n\n'
                )
            if active is None and not events and not revoked and last_sent:
                yield 'event: stream.end\ndata: {"status":"idle"}\n\n'
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
