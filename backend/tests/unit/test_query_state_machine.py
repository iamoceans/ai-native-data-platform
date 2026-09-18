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
