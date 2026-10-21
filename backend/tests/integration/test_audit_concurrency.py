"""Audit regressions against PostgreSQL, with independent transactions.

Run serially against a disposable control database: clean_db truncates it.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import uuid

import pytest
from sqlalchemy import func, select

from app.agent.state import AnalysisStatus
from app.constants import QueryStatus
from app.db import get_session_factory, session_scope
from app.models.orm import QueryJob, TaskQueue, User
from app.repositories import analyses, idempotency, queries
from tests.helpers import setup_datasource_and_grants

pytestmark = pytest.mark.integration


@pytest.fixture()
def source(admin_client, integration_env):
    return setup_datasource_and_grants(admin_client, integration_env["source_url"])


@pytest.mark.parametrize("initial,worker_status,expected", [
    (QueryStatus.RUNNING, QueryStatus.SUCCEEDED, QueryStatus.SUCCEEDED),
    (QueryStatus.QUEUED, QueryStatus.RUNNING, QueryStatus.CANCEL_REQUESTED),
])
def test_cancel_reloads_worker_state(admin_client, source, initial, worker_status, expected):
    response = admin_client.post('/api/v1/queries', json={
        'datasource_id': source.datasource_id, 'sql': 'SELECT 1',
    })
    assert response.status_code == 202, response.text
    query_id = uuid.UUID(response.json()['query_id'])
    with session_scope() as worker:
        job = queries.get_query_for_update(worker, query_id)
        if initial == QueryStatus.RUNNING:
            queries.transition(job, initial)
    # Simulate the API ownership read preceding a worker commit.
    with get_session_factory()() as api:
        cached = queries.get_query(api, query_id)
        assert cached.status == initial
        with session_scope() as worker:
            job = queries.get_query_for_update(worker, query_id)
            queries.transition(job, worker_status)
            finished_at = job.finished_at
        result = queries.request_cancel(api, cached)
        assert result.status == expected
        if expected == QueryStatus.SUCCEEDED:
            assert result.finished_at == finished_at
            assert result.cancel_requested_at is None
        else:
            requested_at = result.cancel_requested_at
            assert requested_at is not None
            assert queries.request_cancel(api, result).cancel_requested_at == requested_at
        api.commit()
    with session_scope() as verify:
        assert queries.get_query(verify, query_id).status == expected


def test_analysis_cancel_preserves_worker_completion(bootstrap):
    with session_scope() as session:
        owner = session.scalar(select(User).where(User.username == 'admin'))
        conversation = analyses.create_session(session, user_id=owner.id, title='audit')
        task = analyses.create_analysis(session, session_id=conversation.id, user_id=owner.id,
            question='audit', context={}, parent_id=None, budget={}, policy_revision=1)
        for state in [AnalysisStatus.UNDERSTANDING, AnalysisStatus.RETRIEVING,
                      AnalysisStatus.PLANNING, AnalysisStatus.EXECUTING,
                      AnalysisStatus.OBSERVING, AnalysisStatus.SYNTHESIZING]:
            analyses.set_status(task, state)
        task_id = task.id
    with get_session_factory()() as api:
        cached = analyses.get_analysis(api, task_id)
        assert cached.status == AnalysisStatus.SYNTHESIZING
        with session_scope() as worker:
            task = analyses.get_analysis_for_update(worker, task_id)
            analyses.set_status(task, AnalysisStatus.COMPLETED)
            checkpoint, finished_at = task.checkpoint_version, task.finished_at
        assert analyses.cancel_task(api, cached) is False
        assert cached.status == AnalysisStatus.COMPLETED
        assert (cached.checkpoint_version, cached.finished_at) == (checkpoint, finished_at)
        api.commit()


@pytest.mark.parametrize('different_body', [False, True])
def test_concurrent_query_idempotency_replays_or_conflicts(admin_client, source, monkeypatch, different_body):
    rendezvous = Barrier(2)
    original = idempotency.lookup

    def together(*args, **kwargs):
        rendezvous.wait(timeout=10)
        return original(*args, **kwargs)

    monkeypatch.setattr(idempotency, 'lookup', together)

    def submit(index):
        return admin_client.post('/api/v1/queries', headers={'Idempotency-Key': 'audit-concurrent'},
            json={'datasource_id': source.datasource_id, 'sql': f'SELECT {index + 1 if different_body else 1}'})

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, [0, 1]))
    assert sorted(r.status_code for r in responses) == ([202, 409] if different_body else [202, 202]), [r.text for r in responses]
    if not different_body:
        assert responses[0].json()['query_id'] == responses[1].json()['query_id']
    else:
        assert next(r for r in responses if r.status_code == 409).json()['error']['code'] == 'IDEMPOTENCY_CONFLICT'
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(QueryJob)) == 1
        assert session.scalar(select(func.count()).select_from(TaskQueue).where(TaskQueue.kind == 'query')) == 1


def test_analysis_cancel_includes_child_committed_after_api_read(admin_client, source):
    with session_scope() as session:
        owner = session.scalar(select(User).where(User.username == 'admin'))
        conversation = analyses.create_session(session, user_id=owner.id, title='audit child boundary')
        task = analyses.create_analysis(session, session_id=conversation.id, user_id=owner.id,
            question='audit', context={}, parent_id=None, budget={}, policy_revision=1)
        task_id = task.id
    with get_session_factory()() as api:
        cached = analyses.get_analysis(api, task_id)
        assert analyses.list_queries(api, task_id) == []
        # The worker owns the parent while adding a new query, after the API's
        # original parent read. Cancellation must enumerate under that same lock.
        with session_scope() as worker:
            task = analyses.get_analysis_for_update(worker, task_id)
            child = queries.create_query_job(worker, user_id=task.user_id,
                datasource_id=uuid.UUID(source.datasource_id), purpose='query',
                original_sql='SELECT 1', validated_sql='SELECT 1', parameters={},
                sql_hash=None, limits={}, policy_revision=1, analysis_id=task.id)
            child_id = child.id
        assert analyses.cancel_task(api, cached) is True
        assert analyses.get_analysis_for_update(api, task_id).status == AnalysisStatus.CANCELLED
        assert queries.get_query(api, child_id).status == QueryStatus.CANCELLED
        assert analyses.cancel_task(api, cached) is False
        api.commit()


def test_idempotency_lock_is_released_on_rollback(bootstrap):
    with session_scope() as session:
        user_id = session.scalar(select(User.id).where(User.username == 'admin'))
    arguments = dict(user_id=user_id, route='POST /queries', key='rolled-back', request_hash='same')
    with get_session_factory()() as session:
        assert idempotency.lookup(session, **arguments) is None
        idempotency.store(session, **arguments, resource_id=uuid.uuid4(), ttl_hours=1)
        session.rollback()
    # A new connection may reserve the same key after rollback, and the
    # uncommitted resource must never be replayed.
    with session_scope() as session:
        assert idempotency.lookup(session, **arguments) is None
        resource_id = uuid.uuid4()
        idempotency.store(session, **arguments, resource_id=resource_id, ttl_hours=1)
    with session_scope() as session:
        assert idempotency.lookup(session, **arguments) == resource_id
