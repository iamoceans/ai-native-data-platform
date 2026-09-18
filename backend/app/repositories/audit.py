"""Audit log persistence (spec section 29)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.ids import utcnow
from app.models.orm import AuditLog

_SENSITIVE_KEYS = {
    "password",
    "password_hash",
    "token",
    "csrf_token",
    "secret",
    "secret_ref_value",
    "credential",
    "authorization",
}


def _redact(value: Any, depth: int = 0) -> Any:
    if depth > 4:
        return "<truncated>"
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if key.lower() in _SENSITIVE_KEYS else _redact(val, depth + 1))
            for key, val in value.items()
        }
    if isinstance(value, list):
        return [_redact(item, depth + 1) for item in value[:50]]
    if isinstance(value, str) and len(value) > 500:
        return value[:500] + "...<truncated>"
    return value


def add_audit(
    session: Session,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    resource_type: str,
    resource_id: str | None,
    trace_id: str,
    outcome: str,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    row = AuditLog(
        actor_id=actor_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        trace_id=trace_id,
        outcome=outcome,
        details=_redact(details or {}),
    )
    session.add(row)
    session.flush()
    return row


def list_audits(
    session: Session,
    *,
    action: str | None,
    limit: int,
    offset: int,
) -> tuple[list[AuditLog], int]:
    filters = []
    if action:
        filters.append(AuditLog.action == action)
    total = session.execute(
        select(func.count()).select_from(AuditLog).where(*filters)
    ).scalar_one()
    rows = (
        session.execute(
            select(AuditLog)
            .where(*filters)
            .order_by(AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def purge_older_than_days(session: Session, days: int) -> int:
    from datetime import timedelta

    cutoff = utcnow() - timedelta(days=days)
    result = session.execute(delete(AuditLog).where(AuditLog.created_at < cutoff))
    return int(result.rowcount or 0)
