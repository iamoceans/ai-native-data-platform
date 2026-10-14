"""Queue locking, publication failure and monitor regression coverage."""
from types import SimpleNamespace as NS
from unittest.mock import Mock
import uuid

import pytest

from app.config import Settings
from app.constants import ErrorCode, LeaseState, QueryStatus, QueueState
from app.query import executor
from app.repositories import queue
from app.models.orm import QueryJob, TaskQueue, ExecutionLease


@pytest.mark.parametrize("failure", [OSError("disk full"), ValueError("Arrow conversion failed")])
@pytest.mark.parametrize("cancel_at_lock", [False, True])
def test_storage_failure_publishes_fenced_terminal_failure(monkeypatch, failure, cancel_at_lock):
    claim = NS(query_id=uuid.uuid4(), user_id=uuid.uuid4(), worker_id="worker", fencing_token=1, attempt=1)
    session = Mock()
    prepared = NS(job=NS(id=claim.query_id, bound_parameters={}, policy_revision=1, purpose="query"),
                  datasource=NS(secret_ref="test"), validated=NS(validated_sql="SELECT 1", dataset_ids=[]),
                  limits=NS(max_rows=10, max_bytes=1024, timeout_seconds=5), user_id=claim.user_id)
    monkeypatch.setattr(executor, "prepare_execution", lambda *a, **kw: prepared)
    monkeypatch.setattr(executor.SecretResolver, "resolve", lambda *a: {})
    handle = NS(native={}, dialect="postgres", connection_generation=1, columns=[])
    provider = Mock()
    provider.open_execution.return_value = handle
    provider.execute.return_value = []
    monkeypatch.setattr(executor, "build_provider", lambda *a: provider)
    monkeypatch.setattr(executor, "_persist_handle", lambda *a: None)
    monkeypatch.setattr(executor._ExecutionMonitor, "start", lambda *a: None)
    job = NS(id=claim.query_id, status=QueryStatus.RUNNING, cancel_requested_at=None, error=None)
    row = NS(state=QueueState.LEASED)
    lease = NS(state=LeaseState.ACTIVE, worker_id=claim.worker_id, fencing_token=claim.fencing_token)
    values = iter((job, row, lease, row))
    def locked_select(statement):
        value = next(values)
        if cancel_at_lock and value is job:
            # Cancellation commits after executor's optimistic read, before
            # publish_terminal acquires/refreshes the authoritative job lock.
            job.status = QueryStatus.CANCEL_REQUESTED
            job.cancel_requested_at = object()
        return NS(scalar_one_or_none=lambda: value)
    session.execute.side_effect = locked_select
    monkeypatch.setattr(executor.queries_repo, "get_query", lambda *a: job)
    monkeypatch.setattr(executor.audit_repo, "add_audit", audit := Mock())
    monkeypatch.setattr(executor.events_repo, "add_event", event := Mock())
    store = Mock()
    store.publish.side_effect = failure
    executor.execute_claim(session_factory=lambda: session, settings=Settings(), store=store, claim=claim)
    expected_status = QueryStatus.CANCELLED if cancel_at_lock else QueryStatus.FAILED
    assert job.status == expected_status
    if cancel_at_lock:
        assert job.error["code"] == ErrorCode.QUERY_CANCELLED
        assert job.error["details"]["cause"]["code"] == ErrorCode.STORAGE_UNAVAILABLE
    else:
        assert job.error["code"] == ErrorCode.STORAGE_UNAVAILABLE
    assert lease.state == LeaseState.RELEASED
    assert row.state == QueueState.FAILED
    assert audit.call_args.kwargs["outcome"] == expected_status
    assert event.call_args.kwargs["event_type"] == "query.finished"
    session.add.assert_not_called()
    store.delete.assert_not_called()


def test_cancel_retries_after_failed_and_successful_attempts(monkeypatch):
    provider = Mock()
    provider.cancel.side_effect = [OSError("temporary"), None, None]
    monitor = executor._ExecutionMonitor(session_factory=Mock(), settings=Settings(),
        claim=NS(query_id=uuid.uuid4()), provider=provider, handle=object(), deadline=100)
    clock = iter([10.0, 10.1, 12.0, 14.0])
    monkeypatch.setattr(executor.time, "monotonic", lambda: next(clock))
    for _ in range(4):
        monitor._cancel()
    assert provider.cancel.call_count == 3
    assert monitor.cancel_sent


class FakeSession:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.statements = []
    def execute(self, statement):
        self.statements.append(statement)
        row = next(self.rows)
        return NS(scalar_one_or_none=lambda: row)
    def flush(self):
        pass


def test_claim_locks_job_before_queue_and_skips_busy_job():
    candidate = NS(resource_id=uuid.uuid4(), id=uuid.uuid4())
    session = FakeSession([None, None])
    assert queue.claim_next_query(session, worker_id="worker", settings=Settings()) is None
    assert session.statements[0].column_descriptions[0]["entity"] is QueryJob
    assert session.statements[0]._for_update_arg.skip_locked
    assert session.statements[1].column_descriptions[0]["entity"] is TaskQueue


def test_terminal_lock_order_job_queue_lease():
    claim = NS(query_id=uuid.uuid4(), queue_id=uuid.uuid4())
    session = FakeSession([NS(), NS(), NS()])
    queue._lock_lease_and_job(session, claim)
    assert [s.column_descriptions[0]["entity"] for s in session.statements] == [QueryJob, TaskQueue, ExecutionLease]


def test_queue_transition_rejects_terminal_resurrection():
    row = NS(state=QueueState.FAILED)
    with pytest.raises(RuntimeError):
        queue.transition(row, QueueState.READY)


def test_stale_fence_cannot_fail_reclaimed_job(monkeypatch):
    claim = NS(query_id=uuid.uuid4(), worker_id="old", fencing_token=1)
    job = NS(status=QueryStatus.RUNNING)
    row = NS(state=QueueState.LEASED)
    lease = NS(state=LeaseState.ACTIVE, worker_id="new", fencing_token=2)
    session = FakeSession([job, row, lease])
    assert not queue.publish_terminal(session, claim, status=QueryStatus.FAILED,
        code=ErrorCode.STORAGE_UNAVAILABLE, message="storage failed")
    assert job.status == QueryStatus.RUNNING
    assert row.state == QueueState.LEASED
    assert lease.state == LeaseState.ACTIVE


def test_analysis_claim_skips_task_lock_before_touching_queue():
    from app.models.orm import AnalysisTask
    session = FakeSession([None, None])
    assert queue.claim_next_analysis(session, worker_id="worker", settings=Settings()) is None
    statement = session.statements[0]
    assert statement.column_descriptions[0]["entity"] is AnalysisTask
    assert statement._for_update_arg.skip_locked
    assert list(statement._for_update_arg.of)[0].name == AnalysisTask.__tablename__


def test_monitor_polls_cancel_without_renewing_every_poll(monkeypatch):
    session = Mock()
    monitor = executor._ExecutionMonitor(session_factory=lambda: session,
        settings=Settings(cancel_poll_seconds=0.5, heartbeat_seconds=5),
        claim=NS(query_id=uuid.uuid4()), provider=Mock(), handle=object(), deadline=100)
    waits = iter([False] * 12 + [True])
    monkeypatch.setattr(monitor._stop_event, "wait", lambda *a: next(waits))
    clock = iter([n * 0.5 for n in range(12)])
    monkeypatch.setattr(executor.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(queue, "heartbeat", heartbeat := Mock(return_value=True))
    monkeypatch.setattr(queue, "check_cancel_requested", cancel := Mock(return_value=False))
    monitor.run()
    assert heartbeat.call_count == 2
    assert cancel.call_count == 12


def test_socket_failure_resolves_locked_pending_cancel_and_releases_lease(monkeypatch):
    claim = NS(query_id=uuid.uuid4(), worker_id="worker", fencing_token=1)
    job = NS(status=QueryStatus.CANCEL_REQUESTED, cancel_requested_at=object())
    row = NS(state=QueueState.LEASED)
    lease = NS(state=LeaseState.ACTIVE, worker_id=claim.worker_id, fencing_token=claim.fencing_token)
    session = FakeSession([job, row, lease, row])
    monkeypatch.setattr(queue.events_repo, "add_event", event := Mock())
    assert queue.publish_terminal(session, claim, status=QueryStatus.FAILED,
        code=ErrorCode.DATASOURCE_UNAVAILABLE, message="source socket timed out",
        details={"phase": "execute"})
    assert job.status == QueryStatus.CANCELLED
    assert lease.state == LeaseState.RELEASED
    assert row.state == QueueState.FAILED
    assert job.error["code"] == ErrorCode.QUERY_CANCELLED
    assert job.error["details"]["cause"] == {
        "code": ErrorCode.DATASOURCE_UNAVAILABLE,
        "message": "source socket timed out",
        "details": {"phase": "execute"},
    }
    assert event.call_args.kwargs["payload"]["status"] == QueryStatus.CANCELLED
