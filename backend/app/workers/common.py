"""Worker entry points (spec section 28).

query_worker: claims and executes query tasks.
agent_worker: runs the global reconciler (leases, queue timeouts, TTL) and claims
              checkpointed governed-analysis tasks.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import uuid
from collections.abc import Mapping

from app.config import Settings, get_settings
from app.db import get_session_factory, session_scope
from app.ids import utcnow
from app.logging_setup import configure_logging
from app.query import executor, scheduler
from app.repositories import queue as queue_repo
from app.runtime import get_result_store

logger = logging.getLogger(__name__)


def _has_activity(value: object) -> bool:
    """Return whether a reconciler value contains a non-zero outcome."""
    if isinstance(value, Mapping):
        return any(_has_activity(item) for item in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_has_activity(item) for item in value)
    return bool(value)


def worker_identity(prefix: str) -> str:
    return f"{prefix}-{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


class StopFlag:
    def __init__(self) -> None:
        self._event = threading.Event()

    def install(self) -> None:
        for name in ("SIGINT", "SIGTERM", "SIGBREAK"):
            sig = getattr(signal, name, None)
            if sig is not None:
                try:
                    signal.signal(sig, self._handle)
                except (ValueError, OSError):  # pragma: no cover - non-main thread
                    pass

    def _handle(self, signum, frame) -> None:  # pragma: no cover - signal path
        logger.info("stop signal received", extra={"event": "shutdown"})
        self._event.set()

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)

    def is_set(self) -> bool:
        return self._event.is_set()


def run_query_worker(settings: Settings | None = None, stop: StopFlag | None = None, once: bool = False) -> None:
    settings = settings or get_settings()
    configure_logging("query-worker", settings.log_level)
    stop = stop or StopFlag()
    stop.install()
    worker_id = worker_identity("qw")
    session_factory = get_session_factory()
    store = get_result_store()
    logger.info("query worker started", extra={"worker_id": worker_id})

    while not stop.is_set():
        claimed = None
        try:
            with session_scope() as session:
                claimed = scheduler.claim_next(session, worker_id=worker_id, settings=settings)
        except Exception:
            logger.exception("claim failed", extra={"worker_id": worker_id})
        if claimed is None:
            if once:
                break
            stop.wait(settings.worker_poll_seconds)
            continue
        logger.info(
            "claimed query",
            extra={"query_id": str(claimed.query_id), "worker_id": worker_id, "step": "claim"},
        )
        try:
            executor.execute_claim(
                session_factory=session_factory, settings=settings, store=store, claim=claimed
            )
        except Exception:
            logger.exception("execution crashed", extra={"query_id": str(claimed.query_id)})
        if once:
            break
    logger.info("query worker stopped", extra={"worker_id": worker_id})


def run_reconciler_loop(settings: Settings | None = None, stop: StopFlag | None = None, once: bool = False) -> None:
    settings = settings or get_settings()
    configure_logging("agent-worker", settings.log_level)
    stop = stop or StopFlag()
    stop.install()
    worker_id = worker_identity("aw")
    store = get_result_store()
    from app.agent import runner as analysis_runner
    logger.info(
        "agent worker started (reconciler and analysis runner)",
        extra={"worker_id": worker_id},
    )
    while not stop.is_set():
        try:
            with session_scope() as session:
                summary = scheduler.reconcile_once(session, settings, store)
            with session_scope() as session:
                claim = queue_repo.claim_next_analysis(
                    session, worker_id=worker_id, settings=settings
                )
            if claim is not None:
                with session_scope() as session:
                    analysis_runner.execute_claim(session, claim=claim, settings=settings)
            interesting = {k: v for k, v in summary.items() if _has_activity(v)}
            if interesting:
                logger.info(
                    "reconcile pass", extra={"event": "reconcile", "outcome": str(interesting)}
                )
        except Exception:
            logger.exception("reconcile failed")
        if once:
            break
        stop.wait(min(5.0, settings.worker_poll_seconds))
    logger.info("agent worker stopped", extra={"worker_id": worker_id})


def main() -> None:
    run_query_worker()


if __name__ == "__main__":
    main()
