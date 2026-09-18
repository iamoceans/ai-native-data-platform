"""Task events: source of truth for SSE replay (spec section 23)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.constants import SCHEMA_VERSION
from app.models.orm import TaskEvent
from app.ids import utcnow


def add_event(
    session: Session,
    *,
    resource_kind: str,
    resource_id: uuid.UUID,
    event_type: str,
    payload: dict[str, Any],
) -> TaskEvent:
    body = {"schema_version": SCHEMA_VERSION, **payload}
    row = TaskEvent(
        resource_kind=resource_kind,
        resource_id=resource_id,
        event_type=event_type,
        payload=body,
    )
    session.add(row)
    session.flush()
    return row


def events_after(
    session: Session, *, resource_kind: str, resource_id: uuid.UUID, after_id: int, limit: int = 200
) -> list[TaskEvent]:
    return list(
        session.execute(
            select(TaskEvent)
            .where(
                TaskEvent.resource_kind == resource_kind,
                TaskEvent.resource_id == resource_id,
                TaskEvent.id > after_id,
            )
            .order_by(TaskEvent.id)
            .limit(limit)
        ).scalars()
    )


def oldest_event_id(session: Session, *, resource_kind: str, resource_id: uuid.UUID) -> int | None:
    return session.execute(
        select(func.min(TaskEvent.id)).where(
            TaskEvent.resource_kind == resource_kind, TaskEvent.resource_id == resource_id
        )
    ).scalar_one_or_none()


def latest_event_id(session: Session, *, resource_kind: str, resource_id: uuid.UUID) -> int | None:
    return session.execute(
        select(func.max(TaskEvent.id)).where(
            TaskEvent.resource_kind == resource_kind, TaskEvent.resource_id == resource_id
        )
    ).scalar_one_or_none()


def purge_older_than_days(session: Session, days: int) -> int:
    from datetime import timedelta

    cutoff = utcnow() - timedelta(days=days)
    result = session.execute(delete(TaskEvent).where(TaskEvent.created_at < cutoff))
    return int(result.rowcount or 0)
