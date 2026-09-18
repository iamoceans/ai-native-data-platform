"""Query job persistence and state transitions (spec sections 8, 13)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import TERMINAL_QUERY_STATUSES, QUERY_TRANSITIONS, QueryStatus
from app.errors import ApiError
from app.ids import utcnow
from app.models.orm import QueryDependency, QueryJob, QueryResult


class InvalidTransition(RuntimeError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"invalid query transition {current} -> {target}")
        self.current = current
        self.target = target


def create_query_job(
    session: Session,
    *,
    user_id: uuid.UUID,
    datasource_id: uuid.UUID,
    purpose: str,
    original_sql: str,
    validated_sql: str | None,
    parameters: dict[str, Any],
    sql_hash: str | None,
    limits: dict[str, Any],
    policy_revision: int,
    analysis_id: uuid.UUID | None = None,
) -> QueryJob:
    job = QueryJob(
        id=uuid.uuid4(),
        user_id=user_id,
        datasource_id=datasource_id,
        analysis_id=analysis_id,
        purpose=purpose,
        status=QueryStatus.QUEUED,
        original_sql=original_sql,
        validated_sql=validated_sql,
        bound_parameters=parameters,
        sql_hash=sql_hash,
        limits=limits,
        policy_revision=policy_revision,
        attempt=0,
    )
    session.add(job)
    session.flush()
    return job


def get_query(session: Session, query_id: uuid.UUID) -> QueryJob | None:
    return session.get(QueryJob, query_id)


def get_query_for_update(session: Session, query_id: uuid.UUID) -> QueryJob | None:
    return session.execute(
        select(QueryJob).where(QueryJob.id == query_id).with_for_update()
    ).scalar_one_or_none()


def list_queries_for_user(
    session: Session,
    *,
    user_id: uuid.UUID,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[QueryJob], int]:
    filters = [QueryJob.user_id == user_id]
    if status:
        filters.append(QueryJob.status == status)
    total = session.execute(
        select(func.count()).select_from(QueryJob).where(*filters)
    ).scalar_one()
    rows = (
        session.execute(
            select(QueryJob)
            .where(*filters)
            .order_by(QueryJob.created_at.desc(), QueryJob.id)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def transition(job: QueryJob, target: str) -> None:
    """Apply the explicit state machine; any other write is a bug."""
    current = QueryStatus(job.status)
    target_enum = QueryStatus(target)
    if target_enum == current:
        return
    allowed = QUERY_TRANSITIONS.get(current, frozenset())
    if target_enum not in allowed:
        raise InvalidTransition(job.status, target)
    job.status = target_enum
    if target_enum in TERMINAL_QUERY_STATUSES:
        job.finished_at = utcnow()


def request_cancel(session: Session, job: QueryJob) -> QueryJob:
    """User cancel: QUEUED -> CANCELLED, RUNNING -> CANCEL_REQUESTED.

    The conditional update makes the cancel/complete race safe: if the result
    was already committed (SUCCEEDED) the cancel becomes a no-op (spec 13).
    """
    locked = session.execute(
        select(QueryJob).where(QueryJob.id == job.id).with_for_update()
    ).scalar_one()
    if locked.status in TERMINAL_QUERY_STATUSES:
        return locked
    if locked.cancel_requested_at is None:
        locked.cancel_requested_at = utcnow()
    if locked.status == QueryStatus.QUEUED:
        transition(locked, QueryStatus.CANCELLED)
    elif locked.status == QueryStatus.RUNNING:
        transition(locked, QueryStatus.CANCEL_REQUESTED)
    session.flush()
    return locked


def add_dependencies(
    session: Session,
    *,
    query_id: uuid.UUID,
    dependencies: list[tuple[uuid.UUID, uuid.UUID | None]],
) -> None:
    # A table may legitimately appear several times (self-joins); the primary
    # key is (query_id, dataset_id), so deduplicate here.
    unique: dict[uuid.UUID, uuid.UUID | None] = {}
    for dataset_id, snapshot_id in dependencies:
        unique.setdefault(dataset_id, snapshot_id)
    for dataset_id, snapshot_id in unique.items():
        session.add(
            QueryDependency(
                query_id=query_id, dataset_id=dataset_id, metadata_snapshot_id=snapshot_id
            )
        )
    session.flush()


def list_active_queries_for_dataset(session: Session, dataset_id: uuid.UUID) -> list[QueryJob]:
    active = [
        QueryStatus.QUEUED,
        QueryStatus.RUNNING,
        QueryStatus.CANCEL_REQUESTED,
    ]
    return list(
        session.execute(
            select(QueryJob)
            .join(QueryDependency, QueryDependency.query_id == QueryJob.id)
            .where(QueryDependency.dataset_id == dataset_id, QueryJob.status.in_(active))
            .with_for_update(of=QueryJob)
        ).scalars()
    )


def list_active_queries_for_user(session: Session, user_id: uuid.UUID) -> list[QueryJob]:
    active = [
        QueryStatus.QUEUED,
        QueryStatus.RUNNING,
        QueryStatus.CANCEL_REQUESTED,
    ]
    return list(
        session.execute(
            select(QueryJob)
            .where(QueryJob.user_id == user_id, QueryJob.status.in_(active))
            .with_for_update()
        ).scalars()
    )


def list_results_for_queries(
    session: Session, query_ids: list[uuid.UUID]
) -> dict[uuid.UUID, QueryResult]:
    if not query_ids:
        return {}
    rows = session.execute(
        select(QueryResult).where(QueryResult.query_id.in_(query_ids))
    ).scalars()
    return {row.query_id: row for row in rows}


def get_result(session: Session, query_id: uuid.UUID) -> QueryResult | None:
    return session.execute(
        select(QueryResult).where(QueryResult.query_id == query_id)
    ).scalar_one_or_none()


def sql_error(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"code": code, "message": message, "details": details or {}}


def raise_if_transition_invalid(job: QueryJob, target: str) -> None:
    try:
        transition(job, target)
    except InvalidTransition as exc:  # pragma: no cover - defensive
        raise ApiError("INTERNAL_ERROR", str(exc)) from exc
