"""Unit tests: query state machine (spec section 13)."""

from __future__ import annotations

import uuid

import pytest

from app.models.orm import QueryJob
from app.repositories import queries as queries_repo


def make_job(status: str) -> QueryJob:
    return QueryJob(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        datasource_id=uuid.uuid4(),
        purpose="query",
        status=status,
        original_sql="SELECT 1",
        validated_sql="SELECT 1 LIMIT 1000",
        bound_parameters={},
        limits={},
        policy_revision=1,
        attempt=0,
    )


@pytest.mark.parametrize(
    "current,target,allowed",
    [
        ("QUEUED", "RUNNING", True),
        ("QUEUED", "CANCELLED", True),
        ("QUEUED", "FAILED", True),
        ("QUEUED", "SUCCEEDED", False),
        ("RUNNING", "SUCCEEDED", True),
        ("RUNNING", "FAILED", True),
        ("RUNNING", "CANCEL_REQUESTED", True),
        ("RUNNING", "TIMED_OUT", True),
        ("RUNNING", "LOST", True),
        ("RUNNING", "QUEUED", False),
        ("CANCEL_REQUESTED", "CANCELLED", True),
        ("CANCEL_REQUESTED", "TIMED_OUT", True),
        ("CANCEL_REQUESTED", "LOST", True),
        ("CANCEL_REQUESTED", "SUCCEEDED", False),
        ("CANCEL_REQUESTED", "RUNNING", False),
        ("SUCCEEDED", "RUNNING", False),
        ("FAILED", "RUNNING", False),
        ("CANCELLED", "SUCCEEDED", False),
        ("TIMED_OUT", "FAILED", False),
        ("LOST", "SUCCEEDED", False),
    ],
)
def test_transitions(current, target, allowed):
    job = make_job(current)
    if allowed:
        queries_repo.transition(job, target)
        assert job.status == target
    else:
        with pytest.raises(queries_repo.InvalidTransition):
            queries_repo.transition(job, target)
        assert job.status == current


def test_terminal_transition_sets_finished_at():
    job = make_job("RUNNING")
    assert job.finished_at is None
    queries_repo.transition(job, "FAILED")
    assert job.finished_at is not None


def test_terminal_publish_resolves_a_pending_cancel(monkeypatch):
    """A terminal publish from CANCEL_REQUESTED is recorded as CANCELLED.

    FAILED is not a legal transition out of CANCEL_REQUESTED (spec 13), so an
    engine error that arrives after the monitor sent a cancel must not be
    refused: the job would look running until its lease expired and only then be
    recorded LOST. A job that is not mid-cancel still gets the status it was
    given.
    """
    from app.query import executor

    published_statuses: list[str] = []

    def fake_publish_terminal(session, claim, *, status, code, message, details=None):
        published_statuses.append(str(status))
        return True

    monkeypatch.setattr(executor.queue_repo, "publish_terminal", fake_publish_terminal)
    claim = type("Claim", (), {"query_id": uuid.uuid4()})()

    monkeypatch.setattr(executor.queries_repo, "get_query",
                        lambda session, query_id: make_job("CANCEL_REQUESTED"))
    assert executor._publish_terminal_status(
        None, claim, status="FAILED", code="EXECUTION_ERROR", message="boom", details=None,
    ) == "CANCELLED"
    assert published_statuses == ["CANCELLED"]

    published_statuses.clear()
    monkeypatch.setattr(executor.queries_repo, "get_query",
                        lambda session, query_id: make_job("RUNNING"))
    assert executor._publish_terminal_status(
        None, claim, status="FAILED", code="EXECUTION_ERROR", message="boom", details=None,
    ) == "FAILED"
    assert published_statuses == ["FAILED"]
