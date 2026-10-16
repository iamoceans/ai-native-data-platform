"""Persistence for the learned business-knowledge store.

Every write here is idempotent by ``dedupe_key``: the same statement learned
twice bumps ``seen_count`` instead of creating a second row, so repeated
analyses strengthen a memory rather than duplicating it.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import and_, case, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.ids import utcnow
from app.models.orm import BusinessMemory, Permission, QueryDependency, QueryJob, User

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
    team_id: uuid.UUID | None = None,
    owner_user_id: uuid.UUID | None = None,
) -> tuple[BusinessMemory, bool]:
    """Insert the statement, or bump the row that already says it.

    Returns ``(row, created)``. A re-learned statement keeps its original
    curator decision: a ``rejected`` row stays rejected rather than being
    resurrected by the next analysis.
    """
    scope = str(team_id or owner_user_id or "legacy")
    dedupe_key = hashlib.sha256(f"{scope}:{dedupe_key}".encode()).hexdigest()
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
        team_id=team_id,
        owner_user_id=owner_user_id,
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
    user_id: uuid.UUID | None = None,
    role_ids: list[uuid.UUID] | None = None,
) -> list[BusinessMemory]:
    """Memories for one metric, most reused first, then freshest.

    Ranked rather than filtered: a memory that planning keeps choosing rises,
    which is what makes the store converge on the business facts that matter
    instead of on whatever was written last.

    Prompt readers must pass current role_ids for source filtering before LIMIT.
    None is reserved for the internal scope-only extraction dedupe window.
    """
    if user_id is None or role_ids == []:
        return []
    rows = session.execute(
        select(BusinessMemory)
        .where(
            visible_scope(session, user_id),
            BusinessMemory.metric_key == metric_key,
            BusinessMemory.status.in_(tuple(statuses)),
            authorized_provenance(role_ids) if role_ids is not None else True,
        )
        .order_by(
            BusinessMemory.reuse_count.desc(),
            BusinessMemory.updated_at.desc(),
            BusinessMemory.id,
        )
        .limit(limit)
    ).scalars()
    return list(rows)


def authorized_provenance(role_ids: list[uuid.UUID]):
    """Current grants on every dependency, before LIMIT and semantic ranking.

    EXISTS avoids multiplying rows for multi-query analyses and grants held by
    different roles. Missing provenance and expired/partial grants fail closed.
    """
    if not role_ids:
        return False
    now = utcnow()
    def granted(action: str):
        return select(Permission.id).where(
            Permission.dataset_id == QueryDependency.dataset_id,
            Permission.role_id.in_(role_ids), Permission.action == action,
            or_(Permission.expires_at.is_(None), Permission.expires_at > now),
        ).correlate(QueryDependency).exists()

    dependencies = select(QueryDependency.query_id).join(
        QueryJob, QueryJob.id == QueryDependency.query_id
    ).where(QueryJob.analysis_id == BusinessMemory.analysis_id).correlate(BusinessMemory)
    denied = dependencies.where(or_(~granted("discover"), ~granted("query")))
    return and_(dependencies.exists(), ~denied.exists())


def memory_dataset_actions_ok(
    session: Session, *, role_ids: list[uuid.UUID], row: BusinessMemory, user_id: uuid.UUID | None = None
) -> bool:
    """Only inject a memory whose source datasets are still granted.

    Use the source analysis's registered dependency IDs rather than resolving
    display qualifiers, which can be ambiguous across datasources. Shared memory
    need not have the same author, but it must have traceable source evidence.
    """
    from app.repositories import analyses as analyses_repo

    if not scope_allowed(session, row=row, user_id=user_id):
        return False
    if row.analysis_id is None or analyses_repo.get_analysis(session, row.analysis_id) is None:
        return False
    query_ids = [item.id for item in analyses_repo.list_queries(session, row.analysis_id)]
    if not analyses_repo.list_dependencies(session, query_ids):
        return False
    return analyses_repo.analysis_dataset_actions_ok(
        session, role_ids=role_ids, analysis_id=row.analysis_id
    )


def list_recent(
    session: Session,
    *,
    metric_key: str | None = None,
    status: str | None = None,
    limit: int = 50,
    role_ids: list[uuid.UUID] | None = None,
    user_id: uuid.UUID | None = None,
) -> list[BusinessMemory]:
    if user_id is None or role_ids == []:
        return []
    statement = select(BusinessMemory).where(visible_scope(session, user_id))
    if metric_key:
        statement = statement.where(BusinessMemory.metric_key == metric_key)
    if status:
        statement = statement.where(BusinessMemory.status == status)
    if role_ids is not None:
        statement = statement.where(authorized_provenance(role_ids))
    return list(session.scalars(
        statement.order_by(BusinessMemory.updated_at.desc(), BusinessMemory.id).limit(limit)
    ))


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
    wanted = sorted(set(ids))
    if not wanted:
        return
    now = utcnow()
    if len(wanted) > 1:
        # Overlapping multi-memory updates take locks in the same order.
        list(session.scalars(select(BusinessMemory.id).where(BusinessMemory.id.in_(wanted))
                             .order_by(BusinessMemory.id).with_for_update()))
    session.execute(update(BusinessMemory).where(BusinessMemory.id.in_(wanted)).values(
        reuse_count=BusinessMemory.reuse_count + 1,
        last_used_at=case(
            (BusinessMemory.last_used_at.is_(None), now),
            (BusinessMemory.last_used_at < now, now),
            else_=BusinessMemory.last_used_at,
        ),
    ))
    session.flush()


def set_status(session: Session, row: BusinessMemory, status: str) -> BusinessMemory:
    row.status = status
    row.source = "curator"
    row.updated_at = utcnow()
    session.flush()
    return row


def counts(session: Session, *, metric_key: str | None = None, role_ids: list[uuid.UUID] | None = None, user_id: uuid.UUID | None = None) -> dict[str, int]:
    """Rows per status, optionally scoped to one metric.

    Scoped counts matter because the list endpoint pages at ``limit``; a UI that
    counted only the rows it fetched would under-report a busy store.
    """
    if user_id is None or role_ids == []:
        return {}
    statement = select(BusinessMemory.status, func.count()).where(visible_scope(session, user_id)).group_by(BusinessMemory.status)
    if metric_key:
        statement = statement.where(BusinessMemory.metric_key == metric_key)
    if role_ids is not None:
        statement = statement.where(authorized_provenance(role_ids))
    rows = session.execute(statement).all()
    return {str(status): int(count) for status, count in rows}


def as_dict(row: BusinessMemory) -> dict[str, Any]:
    return {
        "id": row.id,
        "metric_key": row.metric_key,
        "team_id": row.team_id,
        "owner_user_id": row.owner_user_id,
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


def visible_scope(session: Session, user_id: uuid.UUID | None):
    if user_id is None:
        return False  # Missing identity is never platform-wide access.
    team = select(User.team_id).where(User.id == user_id, User.active.is_(True)).scalar_subquery()
    active_user = select(User.id).where(User.id == user_id, User.active.is_(True)).exists()
    return and_(active_user, or_(
        and_(BusinessMemory.team_id.is_(None), BusinessMemory.owner_user_id == user_id),
        and_(BusinessMemory.team_id.is_not(None), BusinessMemory.team_id == team),
    ))


def scope_allowed(session: Session, *, row: BusinessMemory, user_id: uuid.UUID | None) -> bool:
    if user_id is None:
        return False
    return bool(session.scalar(select(BusinessMemory.id).where(
        BusinessMemory.id == row.id, visible_scope(session, user_id))))


def lock_scope(session: Session, *, metric_key: str, team_id, owner_user_id) -> None:
    """Serialize near-duplicate checks across analyses in the same memory scope."""
    scope = f"memory:{metric_key}:{team_id or owner_user_id}"
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:scope, 0))"), {"scope": scope})
