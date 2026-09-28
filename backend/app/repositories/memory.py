"""Persistence for the learned business-knowledge store.

Every write here is idempotent by ``dedupe_key``: the same statement learned
twice bumps ``seen_count`` instead of creating a second row, so repeated
analyses strengthen a memory rather than duplicating it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.ids import utcnow
from app.models.orm import BusinessMemory

ACTIVE_STATUSES = ("proposed", "confirmed")


def upsert(
    session: Session,
    *,
    metric_key: str,
    kind: str,
    statement: str,
    dedupe_key: str,
    scope_datasets: Iterable[str] = (),
    scope_dimensions: Iterable[str] = (),
    analysis_id: uuid.UUID | None = None,
    calculation_id: uuid.UUID | None = None,
    model_id: str | None = None,
    source: str = "model",
) -> tuple[BusinessMemory, bool]:
    """Insert the statement, or bump the row that already says it.

    Returns ``(row, created)``. A re-learned statement keeps its original
    curator decision: a ``rejected`` row stays rejected rather than being
    resurrected by the next analysis.
    """
    existing = session.execute(
        select(BusinessMemory).where(BusinessMemory.dedupe_key == dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        existing.seen_count = int(existing.seen_count) + 1
        existing.updated_at = utcnow()
        session.flush()
        return existing, False
    row = BusinessMemory(
        id=uuid.uuid4(),
        metric_key=metric_key,
        kind=kind,
        statement=statement,
        scope_datasets=list(scope_datasets),
        scope_dimensions=list(scope_dimensions),
        status="proposed",
        source=source,
        model_id=model_id,
        analysis_id=analysis_id,
        calculation_id=calculation_id,
        dedupe_key=dedupe_key,
    )
    session.add(row)
    session.flush()
    return row, True


def list_for_metric(
    session: Session,
    *,
    metric_key: str,
    statuses: Iterable[str] = ACTIVE_STATUSES,
    limit: int = 50,
) -> list[BusinessMemory]:
    """Memories for one metric, most reused first, then freshest.

    Ranked rather than filtered: a memory that planning keeps choosing rises,
    which is what makes the store converge on the business facts that matter
    instead of on whatever was written last.
    """
    rows = session.execute(
        select(BusinessMemory)
        .where(
            BusinessMemory.metric_key == metric_key,
            BusinessMemory.status.in_(tuple(statuses)),
        )
        .order_by(
            BusinessMemory.reuse_count.desc(),
            BusinessMemory.updated_at.desc(),
            BusinessMemory.id,
        )
        .limit(limit)
    ).scalars()
    return list(rows)


def list_recent(
    session: Session,
    *,
    metric_key: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> list[BusinessMemory]:
    statement = select(BusinessMemory)
    if metric_key:
        statement = statement.where(BusinessMemory.metric_key == metric_key)
    if status:
        statement = statement.where(BusinessMemory.status == status)
    rows = session.execute(
        statement.order_by(BusinessMemory.updated_at.desc(), BusinessMemory.id).limit(limit)
    ).scalars()
    return list(rows)


def list_by_ids(session: Session, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, BusinessMemory]:
    wanted = list(ids)
    if not wanted:
        return {}
    rows = session.execute(
        select(BusinessMemory).where(BusinessMemory.id.in_(wanted))
    ).scalars()
    return {row.id: row for row in rows}


def get(session: Session, memory_id: uuid.UUID) -> BusinessMemory | None:
    return session.get(BusinessMemory, memory_id)


def mark_used(session: Session, ids: Iterable[uuid.UUID]) -> None:
    """Record that these memories were put in front of the planner."""
    wanted = list(ids)
    if not wanted:
        return
    now = utcnow()
    for row in session.execute(
        select(BusinessMemory).where(BusinessMemory.id.in_(wanted))
    ).scalars():
        row.reuse_count = int(row.reuse_count) + 1
        row.last_used_at = now
    session.flush()


def set_status(session: Session, row: BusinessMemory, status: str) -> BusinessMemory:
    row.status = status
    row.source = "curator"
    row.updated_at = utcnow()
    session.flush()
    return row


def counts(session: Session, *, metric_key: str | None = None) -> dict[str, int]:
    """Rows per status, optionally scoped to one metric.

    Scoped counts matter because the list endpoint pages at ``limit``; a UI that
    counted only the rows it fetched would under-report a busy store.
    """
    statement = select(BusinessMemory.status, func.count()).group_by(BusinessMemory.status)
    if metric_key:
        statement = statement.where(BusinessMemory.metric_key == metric_key)
    rows = session.execute(statement).all()
    return {str(status): int(count) for status, count in rows}


def as_dict(row: BusinessMemory) -> dict[str, Any]:
    return {
        "id": row.id,
        "metric_key": row.metric_key,
        "kind": row.kind,
        "statement": row.statement,
        "scope_datasets": list(row.scope_datasets or []),
        "scope_dimensions": list(row.scope_dimensions or []),
        "status": row.status,
        "source": row.source,
        "model_id": row.model_id,
        "analysis_id": row.analysis_id,
        "calculation_id": row.calculation_id,
        "seen_count": int(row.seen_count),
        "reuse_count": int(row.reuse_count),
        "last_used_at": _iso(row.last_used_at),
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
