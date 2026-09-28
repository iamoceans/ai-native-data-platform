"""Business memory endpoints: what the platform learned from its own analyses.

Reading is open to any authenticated user - the statements are business
knowledge, never credentials or row data. Curation (confirm/reject) is an
administrator action: a model-written statement becomes settled business
knowledge only through a human decision, and a rejection is permanent.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import current_auth, get_db, get_trace_id, require_csrf
from app.api.dto import MemoryItem, MemoryListResponse
from app.auth.sessions import AuthContext
from app.constants import ErrorCode
from app.errors import ApiError
from app.repositories import audit as audit_repo
from app.repositories import memory as memory_repo

router = APIRouter(tags=["memory"])

MAX_PAGE = 200
CURATION_CAPABILITY = "admin.manage"


def _item(row) -> MemoryItem:
    return MemoryItem(**{**memory_repo.as_dict(row), "id": row.id})


def _require_curator(auth: AuthContext) -> None:
    if CURATION_CAPABILITY not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")


@router.get("/memory", response_model=MemoryListResponse)
def list_memory(
    request: Request,
    metric_key: str | None = None,
    status: str | None = None,
    limit: int = 50,
    _auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> MemoryListResponse:
    if status is not None and status not in {"proposed", "confirmed", "rejected"}:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "status must be proposed, confirmed or rejected")
    if not 1 <= limit <= MAX_PAGE:
        raise ApiError(ErrorCode.VALIDATION_ERROR, f"limit must be between 1 and {MAX_PAGE}")
    rows = memory_repo.list_recent(
        session, metric_key=metric_key, status=status, limit=limit
    )
    return MemoryListResponse(
        items=[_item(row) for row in rows],
        counts=memory_repo.counts(session, metric_key=metric_key),
    )


def _curate(
    session: Session,
    auth: AuthContext,
    memory_id: uuid.UUID,
    *,
    status: str,
    trace_id: str,
) -> MemoryItem:
    row = memory_repo.get(session, memory_id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "memory not found")
    # Read before the write: set_status mutates the row in place, so asking it
    # afterwards would record the new status as the previous one.
    previous = row.status
    memory_repo.set_status(session, row, status)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action=f"memory.{status}",
        resource_type="business_memory",
        resource_id=str(row.id),
        trace_id=trace_id,
        outcome="SUCCESS",
        details={"metric_key": row.metric_key, "previous": previous},
    )
    return _item(row)


@router.post("/memory/{memory_id}/confirm", response_model=MemoryItem)
def confirm_memory(
    memory_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> MemoryItem:
    _require_curator(auth)
    return _curate(session, auth, memory_id, status="confirmed", trace_id=get_trace_id(request))


@router.post("/memory/{memory_id}/reject", response_model=MemoryItem)
def reject_memory(
    memory_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> MemoryItem:
    _require_curator(auth)
    return _curate(session, auth, memory_id, status="rejected", trace_id=get_trace_id(request))
