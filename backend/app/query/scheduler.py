"""Scheduler and global monitor duties (spec section 13).

Claiming is delegated to repositories.queue (short transaction with
SKIP LOCKED and capacity locks). The reconciler loop owns queue timeouts,
suspect/lost leases and TTL cleanup so a dead worker cannot block a source
forever once the source-side hard deadline has elapsed.
"""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.config import Settings
from app.constants import QueryStatus, QueueState
from app.repositories import events as events_repo
from app.repositories import queries as queries_repo
from app.repositories import queue as queue_repo
from app.results.store import ResultStore

logger = logging.getLogger(__name__)


def claim_next(session: Session, *, worker_id: str, settings: Settings):
    try:
        return queue_repo.claim_next_query(session, worker_id=worker_id, settings=settings)
    except queue_repo.ClaimRefused as exc:
        logger.warning("claim refused: %s", exc, extra={"outcome": "refused"})
        return None


def expire_queued(session: Session, settings: Settings) -> list[str]:
    failed: list[str] = []
    for job in queue_repo.list_expired_queued(session, settings.queue_timeout_seconds):
        queries_repo.transition(job, QueryStatus.FAILED)
        job.error = queries_repo.sql_error(
            "QUEUE_TIMEOUT", "query did not get an execution slot before the queue timeout"
        )
        queue_row = session.query(queue_repo.TaskQueue).filter_by(resource_id=job.id).one_or_none()
        if queue_row is not None:
            queue_row.state = QueueState.FAILED
        events_repo.add_event(
            session,
            resource_kind="query",
            resource_id=job.id,
            event_type="query.failed",
            payload={"query_id": str(job.id), "status": "FAILED", "code": "QUEUE_TIMEOUT"},
        )
        failed.append(str(job.id))
    return failed


def cleanup_ttl(session: Session, settings: Settings, store: ResultStore) -> dict:
    from datetime import timedelta

    from app.ids import utcnow
    from app.models.orm import QueryResult
    from app.repositories import audit as audit_repo
    from app.repositories import idempotency as idempotency_repo
    from app.repositories import users as users_repo

    summary: dict[str, int] = {}
    expired = (
        session.query(QueryResult)
        .filter(QueryResult.expires_at < utcnow())
        .limit(200)
        .all()
    )
    for result in expired:
        store.delete(result.storage_key)
        session.delete(result)
    summary["results_expired"] = len(expired)
    summary["events_purged"] = events_repo.purge_older_than_days(session, settings.event_retention_days)
    summary["audits_purged"] = audit_repo.purge_older_than_days(session, settings.audit_retention_days)
    summary["idempotency_purged"] = idempotency_repo.purge_expired(session)
    summary["sessions_purged"] = users_repo.purge_expired_sessions(session)
    summary["leases_purged"] = queue_repo.delete_expired_leases(session)
    session.flush()
    return summary


def reconcile_once(session: Session, settings: Settings, store: ResultStore) -> dict:
    summary: dict[str, object] = {}
    summary["queue_timeouts"] = expire_queued(session, settings)
    summary["leases_suspect"] = queue_repo.mark_suspect_leases(session)
    summary["leases_lost"] = [str(qid) for qid in queue_repo.reap_lost_leases(session, settings)]
    summary["ingestions"] = queue_repo.requeue_stale_ingestions(session)
    summary.update(cleanup_ttl(session, settings, store))
    return summary
