"""Idempotency keys (spec section 22): same user/route/key + same body hash
returns the original resource; a different body is a conflict."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.errors import ApiError
from app.ids import utcnow
from app.models.orm import IdempotencyKey


def lookup(
    session: Session, *, user_id: uuid.UUID, route: str, key: str, request_hash: str
) -> uuid.UUID | None:
    row = session.execute(
        select(IdempotencyKey).where(
            IdempotencyKey.user_id == user_id,
            IdempotencyKey.route == route,
            IdempotencyKey.key == key,
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.expires_at <= utcnow():
        session.delete(row)
        session.flush()
        return None
    if row.request_hash != request_hash:
        raise ApiError(
            "IDEMPOTENCY_CONFLICT",
            "the same Idempotency-Key was used with a different request body",
        )
    return row.resource_id


def store(
    session: Session,
    *,
    user_id: uuid.UUID,
    route: str,
    key: str,
    request_hash: str,
    resource_id: uuid.UUID,
    ttl_hours: int,
) -> None:
    session.add(
        IdempotencyKey(
            user_id=user_id,
            route=route,
            key=key,
            request_hash=request_hash,
            resource_id=resource_id,
            expires_at=utcnow() + timedelta(hours=ttl_hours),
        )
    )
    session.flush()


def purge_expired(session: Session) -> int:
    result = session.execute(delete(IdempotencyKey).where(IdempotencyKey.expires_at < utcnow()))
    return int(result.rowcount or 0)
