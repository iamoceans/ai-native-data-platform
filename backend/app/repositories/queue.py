"""Task queue, execution leases and the claim protocol (spec section 13).

Claiming follows the specification exactly:
  - SELECT ... FOR UPDATE SKIP LOCKED picks a resource, then its READY queue row
  - queue owner / lease / fencing_token are updated in a short transaction
  - capacity rows are locked in the fixed order global -> datasource -> user
  - no row locks are held while the SQL itself runs
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import TypeVar

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.constants import ErrorCode, LeaseState, QueryStatus, QueueState, TERMINAL_QUERY_STATUSES
from app.errors import ApiError
from app.ids import utcnow
from app.models.orm import (
    AnalysisTask, ConversationTurn, ExecutionCapacity, ExecutionLease,
    IngestionTask, QueryJob, TaskQueue,
)
from app.repositories import events as events_repo
from app.repositories import queries as queries_repo
from app.repositories.datasources import GLOBAL_SCOPE_ID


class ClaimRefused(RuntimeError):
    """Capacity row missing or inconsistent: do not claim (spec 13)."""


@dataclass(frozen=True)
class ClaimedTask:
    queue_id: uuid.UUID
    query_id: uuid.UUID
    datasource_id: uuid.UUID
    user_id: uuid.UUID
    worker_id: str
    fencing_token: int
    attempt: int


@dataclass(frozen=True)
class AnalysisClaim:
    queue_id: uuid.UUID
    analysis_id: uuid.UUID
    worker_id: str
    fencing_token: int
    attempt: int


_Resource = TypeVar("_Resource", QueryJob, AnalysisTask, ConversationTurn, IngestionTask)

_QUEUE_TRANSITIONS = {
    QueueState.READY: frozenset({QueueState.LEASED, QueueState.DONE, QueueState.FAILED}),
    QueueState.LEASED: frozenset({QueueState.READY, QueueState.DONE, QueueState.FAILED}),
    QueueState.DONE: frozenset(),
    QueueState.FAILED: frozenset(),
}


def transition(row: TaskQueue, target: QueueState) -> None:
    current = QueueState(row.state)
    target = QueueState(target)
    if target == current:
        return
    if target not in _QUEUE_TRANSITIONS[current]:
        raise RuntimeError(f"invalid queue transition {current} -> {target}")
    row.state = target


def _claim_resource_queue(
    session: Session, *, kind: str, model: type[_Resource], resumable: bool = False
) -> tuple[_Resource | None, TaskQueue | None]:
    """Lock the resource before its queue, matching completion/cancellation.

    SKIP LOCKED applies to the resource itself: a worker doing a checkpoint
    must never make a competing claimer hold its queue while waiting for it.
    Recheck queue eligibility under its lock after choosing the resource.
    """
    eligible = TaskQueue.state == QueueState.READY
    if resumable:
        eligible = or_(
            eligible,
            (TaskQueue.state == QueueState.LEASED) & (TaskQueue.lease_until < func.now()),
        )
    resource = session.execute(
        select(model).join(TaskQueue, TaskQueue.resource_id == model.id)
        .where(TaskQueue.kind == kind, eligible, TaskQueue.available_at <= func.now())
        .order_by(TaskQueue.available_at, TaskQueue.id)
        .with_for_update(of=model, skip_locked=True).limit(1)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if resource is None:
        # Orphan cleanup needs only a queue lock; there is no resource to lock.
        orphan = session.execute(
            select(TaskQueue).where(TaskQueue.kind == kind, eligible,
                ~select(model.id).where(model.id == TaskQueue.resource_id).exists())
            .with_for_update(skip_locked=True).limit(1)
        ).scalar_one_or_none()
        if orphan is not None:
            transition(orphan, QueueState.FAILED)
            session.flush()
        return None, None
    row = session.execute(
        select(TaskQueue).where(TaskQueue.kind == kind,
            TaskQueue.resource_id == resource.id, eligible,
            TaskQueue.available_at <= func.now())
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    return resource, row


def enqueue(session: Session, *, kind: str, resource_id: uuid.UUID) -> TaskQueue:
    row = TaskQueue(
        id=uuid.uuid4(),
        kind=kind,
        resource_id=resource_id,
        state=QueueState.READY,
        attempt=0,
        fencing_token=0,
    )
    session.add(row)
    session.flush()
    return row


def _seconds(interval: int) -> text:
    return text(f"interval '{int(interval)} seconds'")


def _lock_capacity(session: Session, scope_type: str, scope_id: str) -> int:
    row = session.execute(
        select(ExecutionCapacity)
        .where(ExecutionCapacity.scope_type == scope_type, ExecutionCapacity.scope_id == scope_id)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise ClaimRefused(f"missing execution_capacity row for {scope_type}:{scope_id}")
    return int(row.max_running)


def _count_leases(session: Session, **scope: object) -> int:
    stmt = select(func.count()).select_from(ExecutionLease).where(
        ExecutionLease.state.in_([LeaseState.ACTIVE, LeaseState.SUSPECT])
    )
    for column, value in scope.items():
        stmt = stmt.where(getattr(ExecutionLease, column) == value)
    return int(session.execute(stmt).scalar_one())


def _capacity_available(session: Session, job: QueryJob, settings: Settings) -> bool:
    global_max = _lock_capacity(session, "global", GLOBAL_SCOPE_ID)
    datasource_max = _lock_capacity(session, "datasource", str(job.datasource_id))
    user_max = _lock_capacity(session, "user", str(job.user_id))
    if _count_leases(session) >= global_max:
        return False
    if _count_leases(session, datasource_id=job.datasource_id) >= datasource_max:
        return False
    if _count_leases(session, user_id=job.user_id) >= user_max:
        return False
    return True


def claim_next_query(session: Session, *, worker_id: str, settings: Settings) -> ClaimedTask | None:
    """Claim one READY query task or return None.

    The caller commits this transaction before executing anything.
    """
    now = utcnow()
    job, queue_row = _claim_resource_queue(session, kind="query", model=QueryJob)
    if queue_row is None:
        return None
    if job.status in TERMINAL_QUERY_STATUSES:
        transition(queue_row, (
            QueueState.DONE if job.status == QueryStatus.SUCCEEDED else QueueState.FAILED
        ))
        session.flush()
        return None
    if job.status != QueryStatus.QUEUED:
        transition(queue_row, QueueState.FAILED)
        session.flush()
        return None

    created_at = job.created_at if job.created_at is not None else now
    age = now - created_at
    if age > timedelta(seconds=settings.queue_timeout_seconds):
        queries_repo.transition(job, QueryStatus.FAILED)
        job.error = queries_repo.sql_error(
            "QUEUE_TIMEOUT", "query did not get an execution slot before the queue timeout"
        )
        transition(queue_row, QueueState.FAILED)
        events_repo.add_event(
            session,
            resource_kind="query",
            resource_id=job.id,
            event_type="query.failed",
            payload={"query_id": str(job.id), "status": str(job.status), "code": "QUEUE_TIMEOUT"},
        )
        session.flush()
        return None

    if not _capacity_available(session, job, settings):
        queue_row.available_at = func.now() + _seconds(1)
        session.flush()
        return None

    lease_until = now + timedelta(seconds=settings.lease_seconds)
    transition(queue_row, QueueState.LEASED)
    queue_row.worker_id = worker_id
    queue_row.lease_until = lease_until
    queue_row.attempt = int(queue_row.attempt) + 1
    queue_row.fencing_token = int(queue_row.fencing_token) + 1
    fencing_token = int(queue_row.fencing_token)

    lease = session.get(ExecutionLease, job.id)
    if lease is None:
        lease = ExecutionLease(
            query_id=job.id,
            datasource_id=job.datasource_id,
            user_id=job.user_id,
            worker_id=worker_id,
            fencing_token=fencing_token,
            state=LeaseState.ACTIVE,
            heartbeat_at=now,
            lease_until=lease_until,
        )
        session.add(lease)
    else:
        lease.worker_id = worker_id
        lease.fencing_token = fencing_token
        lease.state = LeaseState.ACTIVE
        lease.heartbeat_at = now
        lease.lease_until = lease_until

    queries_repo.transition(job, QueryStatus.RUNNING)
    job.started_at = now
    job.attempt = int(queue_row.attempt)
    job.provider_handle = {"worker_id": worker_id, "fencing_token": fencing_token, "attempt": int(queue_row.attempt)}
    events_repo.add_event(
        session,
        resource_kind="query",
        resource_id=job.id,
        event_type="query.started",
        payload={"query_id": str(job.id), "status": str(job.status), "worker_id": worker_id},
    )
    session.flush()
    return ClaimedTask(
        queue_id=queue_row.id,
        query_id=job.id,
        datasource_id=job.datasource_id,
        user_id=job.user_id,
        worker_id=worker_id,
        fencing_token=fencing_token,
        attempt=int(queue_row.attempt),
    )


def claim_next_analysis(
    session: Session, *, worker_id: str, settings: Settings
) -> AnalysisClaim | None:
    """Claim a resumable analysis checkpoint using the queue fencing token."""
    from app.agent.state import AnalysisStatus, TERMINAL_ANALYSIS_STATUSES
    from app.models.orm import AnalysisTask

    now = utcnow()
    task, queue_row = _claim_resource_queue(
        session, kind="analysis", model=AnalysisTask, resumable=True
    )
    if queue_row is None:
        return None
    if task.status in TERMINAL_ANALYSIS_STATUSES:
        transition(queue_row, (
            QueueState.DONE if task.status in {AnalysisStatus.COMPLETED, AnalysisStatus.PARTIAL}
            else QueueState.FAILED
        ))
        session.flush()
        return None
    transition(queue_row, QueueState.LEASED)
    queue_row.worker_id = worker_id
    queue_row.lease_until = now + timedelta(seconds=settings.lease_seconds)
    queue_row.attempt = int(queue_row.attempt) + 1
    queue_row.fencing_token = int(queue_row.fencing_token) + 1
    session.flush()
    return AnalysisClaim(
        queue_id=queue_row.id,
        analysis_id=task.id,
        worker_id=worker_id,
        fencing_token=int(queue_row.fencing_token),
        attempt=int(queue_row.attempt),
    )


def release_analysis_claim(
    session: Session,
    claim: AnalysisClaim,
    *,
    terminal: bool = False,
    failed: bool = False,
    delay_seconds: int = 1,
) -> bool:
    row = session.execute(
        select(TaskQueue).where(TaskQueue.id == claim.queue_id).with_for_update()
    ).scalar_one_or_none()
    if (
        row is None
        or row.worker_id != claim.worker_id
        or int(row.fencing_token) != claim.fencing_token
    ):
        return False
    if terminal:
        transition(row, QueueState.FAILED if failed else QueueState.DONE)
    else:
        transition(row, QueueState.READY)
        row.available_at = func.now() + _seconds(delay_seconds)
    row.worker_id = None
    row.lease_until = None
    session.flush()
    return True


def lock_analysis_claim(session: Session, claim: AnalysisClaim) -> TaskQueue | None:
    """Called after the analysis lock: only a current, unexpired owner may write."""
    row = session.execute(
        select(TaskQueue).where(TaskQueue.id == claim.queue_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if (row is None or row.state != QueueState.LEASED
            or row.worker_id != claim.worker_id or int(row.fencing_token) != claim.fencing_token
            or row.lease_until is None or row.lease_until <= utcnow()):
        return None
    return row


def heartbeat(session: Session, claim: ClaimedTask, settings: Settings) -> bool:
    """Refresh the lease; returns False when the worker lost the lease."""
    result = session.execute(
        update(ExecutionLease)
        .where(
            ExecutionLease.query_id == claim.query_id,
            ExecutionLease.worker_id == claim.worker_id,
            ExecutionLease.fencing_token == claim.fencing_token,
            ExecutionLease.state == LeaseState.ACTIVE,
        )
        .values(
            heartbeat_at=func.now(),
            lease_until=func.now() + _seconds(settings.lease_seconds),
        )
    )
    return int(result.rowcount or 0) == 1


def check_cancel_requested(session: Session, query_id: uuid.UUID) -> bool:
    row = session.execute(
        select(QueryJob.cancel_requested_at).where(QueryJob.id == query_id)
    ).scalar_one_or_none()
    return row is not None


def _lock_query_rows(
    session: Session, query_id: uuid.UUID
) -> tuple[ExecutionLease | None, QueryJob | None, TaskQueue | None]:
    job = session.execute(
        select(QueryJob).where(QueryJob.id == query_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    queue_row = session.execute(
        select(TaskQueue).where(TaskQueue.kind == "query", TaskQueue.resource_id == query_id)
        .with_for_update()
    ).scalar_one_or_none()
    lease = session.execute(
        select(ExecutionLease).where(ExecutionLease.query_id == query_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    return lease, job, queue_row


def _lock_lease_and_job(
    session: Session, claim: ClaimedTask
) -> tuple[ExecutionLease | None, QueryJob | None]:
    lease, job, _ = _lock_query_rows(session, claim.query_id)
    return lease, job


def _lease_is_mine(lease: ExecutionLease | None, claim: ClaimedTask) -> bool:
    return (
        lease is not None
        and lease.worker_id == claim.worker_id
        and int(lease.fencing_token) == claim.fencing_token
        and lease.state != LeaseState.RELEASED
    )


def publish_success(
    session: Session,
    claim: ClaimedTask,
    *,
    result_id: uuid.UUID,
    storage_key: str,
    fmt: str,
    schema_json: dict,
    row_count: int,
    byte_count: int,
    truncated: bool,
    content_hash: str,
    expires_at,
) -> bool:
    """Publish a result atomically with the terminal status (spec 13/14).

    Returns False when the lease moved on or a cancel was requested first;
    in that case the caller must discard the result.
    """
    from app.models.orm import QueryResult

    lease, job = _lock_lease_and_job(session, claim)
    if not _lease_is_mine(lease, claim) or job is None:
        return False
    if job.status not in (QueryStatus.RUNNING,):
        return False
    if job.cancel_requested_at is not None:
        return False
    session.add(
        QueryResult(
            id=result_id,
            query_id=claim.query_id,
            storage_key=storage_key,
            format=fmt,
            schema_json=schema_json,
            row_count=row_count,
            byte_count=byte_count,
            truncated=truncated,
            content_hash=content_hash,
            expires_at=expires_at,
        )
    )
    queries_repo.transition(job, QueryStatus.SUCCEEDED)
    job.finished_at = utcnow()
    job.error = None
    lease.state = LeaseState.RELEASED
    queue_row = session.execute(
        select(TaskQueue).where(TaskQueue.kind == "query", TaskQueue.resource_id == claim.query_id).with_for_update()
    ).scalar_one_or_none()
    if queue_row is not None:
        transition(queue_row, QueueState.DONE)
    events_repo.add_event(
        session,
        resource_kind="query",
        resource_id=claim.query_id,
        event_type="query.finished",
        payload={
            "query_id": str(claim.query_id),
            "status": str(job.status),
            "result_id": str(result_id),
            "row_count": row_count,
            "truncated": truncated,
        },
    )
    session.flush()
    return True


def publish_terminal(
    session: Session,
    claim: ClaimedTask,
    *,
    status: str,
    code: str,
    message: str,
    details: dict | None = None,
) -> bool:
    lease, job = _lock_lease_and_job(session, claim)
    if not _lease_is_mine(lease, claim) or job is None:
        return False
    if job.status in TERMINAL_QUERY_STATUSES:
        return False
    engine_status = QueryStatus(job.status)
    target = QueryStatus(status)
    if engine_status == QueryStatus.CANCEL_REQUESTED and target == QueryStatus.FAILED:
        # The locked, refreshed row decides the cancel/error race. An earlier
        # executor read may still have observed RUNNING before cancellation.
        details = {
            **(details or {}),
            "cause": {"code": code, "message": message, "details": details or {}},
        }
        target = QueryStatus.CANCELLED
        code = ErrorCode.QUERY_CANCELLED
        message = "query was cancelled"
    # CANCEL_REQUESTED resolves to CANCELLED/TIMED_OUT/LOST; RUNNING may fail.
    if target == QueryStatus.CANCELLED and engine_status not in (
        QueryStatus.RUNNING,
        QueryStatus.CANCEL_REQUESTED,
    ):
        return False
    if target in (QueryStatus.FAILED, QueryStatus.TIMED_OUT, QueryStatus.LOST):
        if engine_status not in (QueryStatus.RUNNING, QueryStatus.CANCEL_REQUESTED):
            return False
    if target == QueryStatus.CANCELLED and engine_status == QueryStatus.RUNNING and job.cancel_requested_at is None:
        # A cancel without the flag would be a bug; treat as FAILED instead.
        target = QueryStatus.FAILED
    queries_repo.transition(job, target)
    job.error = {"code": code, "message": message, "details": details or {}}
    if lease is not None:
        lease.state = LeaseState.RELEASED
    queue_row = session.execute(
        select(TaskQueue).where(TaskQueue.kind == "query", TaskQueue.resource_id == claim.query_id).with_for_update()
    ).scalar_one_or_none()
    if queue_row is not None:
        transition(queue_row, QueueState.FAILED)
    events_repo.add_event(
        session,
        resource_kind="query",
        resource_id=claim.query_id,
        event_type="query.finished",
        payload={"query_id": str(claim.query_id), "status": str(job.status), "code": code},
    )
    session.flush()
    return True


def list_expired_queued(session: Session, queue_timeout_seconds: int) -> list[QueryJob]:
    cutoff = utcnow() - timedelta(seconds=queue_timeout_seconds)
    return list(
        session.execute(
            select(QueryJob)
            .join(TaskQueue, TaskQueue.resource_id == QueryJob.id)
            .where(
                QueryJob.status == QueryStatus.QUEUED,
                TaskQueue.state == QueueState.READY,
                TaskQueue.kind == "query",
                QueryJob.created_at < cutoff,
            )
            .order_by(QueryJob.id)
            .with_for_update(of=QueryJob, skip_locked=True)
        ).scalars()
    )


def mark_suspect_leases(session: Session, now=None) -> int:
    moment = now or utcnow()
    candidates = list(session.execute(
        select(ExecutionLease.query_id).where(
            ExecutionLease.state == LeaseState.ACTIVE, ExecutionLease.lease_until < moment
        ).order_by(ExecutionLease.query_id)
    ).scalars())
    marked = 0
    for query_id in candidates:
        lease, _, _ = _lock_query_rows(session, query_id)
        if lease is not None and lease.state == LeaseState.ACTIVE and lease.lease_until < moment:
            lease.state = LeaseState.SUSPECT
            marked += 1
    session.flush()
    return marked


def reap_lost_leases(session: Session, settings: Settings) -> list[uuid.UUID]:
    """SUSPECT leases past the safe grace window become LOST.

    The source-side hard deadline (statement_timeout) is the guarantee that
    the query has stopped even if the worker died; the grace window is
    lease + statement deadline + margin.
    """
    moment = utcnow()
    grace = timedelta(
        seconds=settings.lease_seconds + settings.hard_max_timeout_seconds + 15
    )
    cutoff = moment - grace
    rows = list(
        session.execute(
            select(ExecutionLease)
            .where(ExecutionLease.state == LeaseState.SUSPECT, ExecutionLease.lease_until < cutoff)
            .order_by(ExecutionLease.query_id)
        ).scalars()
    )
    lost: list[uuid.UUID] = []
    for candidate in rows:
        lease, job, queue_row = _lock_query_rows(session, candidate.query_id)
        if lease is None or lease.state != LeaseState.SUSPECT or lease.lease_until >= cutoff:
            continue
        if job is not None and job.status in (QueryStatus.RUNNING, QueryStatus.CANCEL_REQUESTED):
            queries_repo.transition(job, QueryStatus.LOST)
            job.error = queries_repo.sql_error(
                "QUERY_LOST", "worker lost during execution; source deadline elapsed"
            )
            events_repo.add_event(
                session,
                resource_kind="query",
                resource_id=job.id,
                event_type="query.finished",
                payload={"query_id": str(job.id), "status": "LOST", "code": "QUERY_LOST"},
            )
            lost.append(job.id)
        lease.state = LeaseState.RELEASED
        if queue_row is not None:
            transition(queue_row, QueueState.FAILED)
    session.flush()
    return lost


def delete_expired_leases(session: Session, older_than_days: int = 30) -> int:
    cutoff = utcnow() - timedelta(days=older_than_days)
    result = session.execute(
        delete(ExecutionLease).where(
            ExecutionLease.state == LeaseState.RELEASED, ExecutionLease.lease_until < cutoff
        )
    )
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------
# Ingestion tasks (spec section 10.1)
#
# Ingestion runs in its own worker/image and does not consume query execution
# capacity: the platform allows one active ingestion per datasource (enforced at
# submission) and the worker processes one task at a time. A long lease plus the
# reconciler handle crashed workers.
# ---------------------------------------------------------------------------
INGESTION_LEASE_SECONDS = 1800


# ---------------------------------------------------------------------------
# Conversation turns (design 10.1). The orchestrator only holds a lease while it
# is doing work: waiting for a clarification answer or for the linked analysis
# must not occupy a worker, so the claim is released with a delay instead of
# being renewed.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnClaim:
    queue_id: uuid.UUID
    turn_id: uuid.UUID
    worker_id: str
    fencing_token: int
    attempt: int


def claim_next_turn(
    session: Session, *, worker_id: str, settings: Settings
) -> TurnClaim | None:
    from app.constants import TERMINAL_CONVERSATION_TURN_STATUSES
    from app.models.orm import ConversationTurn

    turn, queue_row = _claim_resource_queue(
        session, kind="conversation", model=ConversationTurn, resumable=True
    )
    if queue_row is None:
        return None
    if turn.status in TERMINAL_CONVERSATION_TURN_STATUSES:
        transition(queue_row, (
            QueueState.DONE
            if turn.status == "COMPLETED"
            else QueueState.FAILED
        ))
        session.flush()
        return None
    now = utcnow()
    transition(queue_row, QueueState.LEASED)
    queue_row.worker_id = worker_id
    queue_row.lease_until = now + timedelta(seconds=settings.lease_seconds)
    queue_row.attempt = int(queue_row.attempt) + 1
    queue_row.fencing_token = int(queue_row.fencing_token) + 1
    session.flush()
    return TurnClaim(
        queue_id=queue_row.id,
        turn_id=turn.id,
        worker_id=worker_id,
        fencing_token=int(queue_row.fencing_token),
        attempt=int(queue_row.attempt),
    )


def release_turn_claim(
    session: Session,
    claim: TurnClaim,
    *,
    terminal: bool = False,
    failed: bool = False,
    delay_seconds: int = 1,
) -> bool:
    row = session.execute(
        select(TaskQueue).where(TaskQueue.id == claim.queue_id).with_for_update()
    ).scalar_one_or_none()
    if (
        row is None
        or row.worker_id != claim.worker_id
        or int(row.fencing_token) != claim.fencing_token
    ):
        return False
    if terminal:
        transition(row, QueueState.FAILED if failed else QueueState.DONE)
    else:
        transition(row, QueueState.READY)
        row.available_at = func.now() + _seconds(delay_seconds)
    row.worker_id = None
    row.lease_until = None
    session.flush()
    return True


def _lock_stale_resource(
    session: Session, candidate: TaskQueue, model: type[_Resource]
) -> tuple[_Resource | None, TaskQueue | None]:
    resource = session.execute(
        select(model).where(model.id == candidate.resource_id).with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    row = session.execute(
        select(TaskQueue).where(TaskQueue.id == candidate.id,
            TaskQueue.state == QueueState.LEASED, TaskQueue.lease_until < func.now())
        .with_for_update(skip_locked=True).execution_options(populate_existing=True)
    ).scalar_one_or_none()
    return resource, row


def requeue_stale_turns(session: Session) -> list[uuid.UUID]:
    """Reconciler: re-queue turns whose orchestrator disappeared mid-lease."""
    from app.constants import TERMINAL_CONVERSATION_TURN_STATUSES
    from app.models.orm import ConversationTurn

    rows = list(
        session.execute(
            select(TaskQueue)
            .where(
                TaskQueue.kind == "conversation",
                TaskQueue.state == QueueState.LEASED,
                TaskQueue.lease_until < func.now(),
            )
            .order_by(TaskQueue.resource_id)
        ).scalars()
    )
    requeued: list[uuid.UUID] = []
    for candidate in rows:
        turn, row = _lock_stale_resource(session, candidate, ConversationTurn)
        if row is None:
            continue
        if turn is None or turn.status in TERMINAL_CONVERSATION_TURN_STATUSES:
            transition(row, QueueState.FAILED)
            continue
        transition(row, QueueState.READY)
        row.worker_id = None
        row.lease_until = None
        row.available_at = func.now()
        requeued.append(row.resource_id)
    session.flush()
    return requeued


def claim_next_ingestion(session: Session, *, worker_id: str):
    from app.models.orm import IngestionTask

    task, queue_row = _claim_resource_queue(session, kind="ingestion", model=IngestionTask)
    if queue_row is None:
        return None
    if task is None or task.status not in ("QUEUED", "RUNNING"):
        transition(queue_row, QueueState.FAILED if task is None else QueueState.DONE)
        session.flush()
        return None
    now = utcnow()
    transition(queue_row, QueueState.LEASED)
    queue_row.worker_id = worker_id
    queue_row.lease_until = now + timedelta(seconds=INGESTION_LEASE_SECONDS)
    queue_row.attempt = int(queue_row.attempt) + 1
    queue_row.fencing_token = int(queue_row.fencing_token) + 1
    task.status = "RUNNING"
    if task.started_at is None:
        task.started_at = now
    session.flush()
    return queue_row


def requeue_stale_ingestions(session: Session, max_attempts: int = 3) -> dict:
    """Reconciler: re-queue ingestion tasks whose worker disappeared."""
    from app.models.orm import IngestionTask

    rows = list(
        session.execute(
            select(TaskQueue)
            .where(
                TaskQueue.kind == "ingestion",
                TaskQueue.state == QueueState.LEASED,
                TaskQueue.lease_until < utcnow(),
            )
            .order_by(TaskQueue.resource_id)
        ).scalars()
    )
    requeued = 0
    failed = 0
    for candidate in rows:
        task, row = _lock_stale_resource(session, candidate, IngestionTask)
        if row is None:
            continue
        if task is None:
            transition(row, QueueState.FAILED)
            continue
        if int(row.attempt) >= max_attempts:
            task.status = "FAILED"
            task.error = {"code": "INGESTION_LOST", "message": "worker lost; attempts exhausted"}
            task.finished_at = utcnow()
            transition(row, QueueState.FAILED)
            failed += 1
        else:
            transition(row, QueueState.READY)
            row.available_at = func.now()
            task.status = "QUEUED"
            requeued += 1
    session.flush()
    return {"requeued": requeued, "failed": failed}
