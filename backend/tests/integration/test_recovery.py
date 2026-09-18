"""Integration: recovery, leases, fencing, queue timeout and TTL (A12, spec 13)."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.config import get_settings
from app.constants import QueryStatus, QueueState
from app.db import session_scope
from app.ids import utcnow
from app.models.orm import ExecutionLease, QueryJob, QueryResult, TaskQueue
from app.query import scheduler
from app.repositories import queue as queue_repo
from app.repositories import queries as queries_repo
from app.runtime import get_result_store

pytestmark = pytest.mark.integration


def make_queued_job(user_id, datasource_id) -> uuid.UUID:
    with session_scope() as session:
        job = queries_repo.create_query_job(
            session,
            user_id=user_id,
            datasource_id=datasource_id,
            purpose="query",
            original_sql="SELECT 1",
            validated_sql="SELECT 1 LIMIT 1001",
            parameters={},
            sql_hash=None,
            limits={"max_rows": 1000, "max_bytes": 10 * 1024 * 1024, "timeout_seconds": 30},
            policy_revision=1,
        )
        queue_repo.enqueue(session, kind="query", resource_id=job.id)
        return job.id


@pytest.fixture()
def ids(clean_db):
    """A user and datasource row to hang synthetic jobs off."""
    import secrets

    from app.auth.passwords import hash_password
    from app.models.orm import Datasource
    from app.repositories import datasources as datasources_repo
    from app.repositories import users as users_repo

    settings = get_settings()
    with session_scope() as session:
        user = users_repo.create_user(session, "recovery-user", hash_password(secrets.token_urlsafe(8)))
        datasources_repo.ensure_user_capacity(session, user.id, settings.per_user_concurrency)
        datasources_repo.ensure_global_capacity(session, settings.global_concurrency)
        datasource = Datasource(
            id=uuid.uuid4(),
            name=f"recovery-ds-{uuid.uuid4().hex[:6]}",
            kind="postgres",
            connection_config={"host": "127.0.0.1", "port": 1, "database": "x"},
            secret_ref="source-postgres",
            capabilities={},
            created_by=user.id,
        )
        session.add(datasource)
        session.flush()
        datasources_repo.ensure_datasource_capacity(
            session, datasource.id, settings.per_datasource_concurrency
        )
        return {"user_id": user.id, "datasource_id": datasource.id}


def test_queue_timeout_marks_job_failed(ids):
    settings = get_settings()
    query_id = make_queued_job(ids["user_id"], ids["datasource_id"])
    with session_scope() as session:
        job = queries_repo.get_query(session, query_id)
        job.created_at = utcnow() - timedelta(seconds=settings.queue_timeout_seconds + 5)
    with session_scope() as session:
        claimed = scheduler.claim_next(session, worker_id="recovery-worker", settings=settings)
        assert claimed is None
    with session_scope() as session:
        job = queries_repo.get_query(session, query_id)
        assert job.status == QueryStatus.FAILED
        assert job.error["code"] == "QUEUE_TIMEOUT"
        queue_row = session.query(TaskQueue).filter_by(resource_id=query_id).one()
        assert queue_row.state == QueueState.FAILED


def test_lost_lease_is_reaped_and_blocks_stale_publication(ids):
    settings = get_settings()
    query_id = make_queued_job(ids["user_id"], ids["datasource_id"])
    with session_scope() as session:
        claim = scheduler.claim_next(session, worker_id="crashed-worker", settings=settings)
    assert claim is not None

    # Simulate the worker dying: lease expires, reconciler marks suspect/lost.
    with session_scope() as session:
        lease = session.get(ExecutionLease, query_id)
        lease.lease_until = utcnow() - timedelta(hours=1)
        lease.heartbeat_at = utcnow() - timedelta(hours=1)
    store = get_result_store()
    with session_scope() as session:
        summary = scheduler.reconcile_once(session, settings, store)
    assert str(query_id) in summary["leases_lost"]
    with session_scope() as session:
        job = queries_repo.get_query(session, query_id)
        assert job.status == QueryStatus.LOST
        assert job.error["code"] == "QUERY_LOST"
        lease = session.get(ExecutionLease, query_id)
        assert lease.state == "RELEASED"

    # The old worker must not be able to publish anything now (fencing).
    with session_scope() as session:
        published = queue_repo.publish_success(
            session,
            claim,
            result_id=uuid.uuid4(),
            storage_key="should-not-exist",
            fmt="arrow+json",
            schema_json={},
            row_count=0,
            byte_count=0,
            truncated=False,
            content_hash="x",
            expires_at=utcnow() + timedelta(days=1),
        )
        assert published is False
        terminal = queue_repo.publish_terminal(
            session, claim, status=QueryStatus.FAILED, code="SHOULD_NOT_APPLY", message="stale"
        )
        assert terminal is False


def test_second_worker_cannot_claim_a_leased_task(ids):
    settings = get_settings()
    query_id = make_queued_job(ids["user_id"], ids["datasource_id"])
    with session_scope() as session:
        first = scheduler.claim_next(session, worker_id="worker-a", settings=settings)
    assert first is not None
    with session_scope() as session:
        second = scheduler.claim_next(session, worker_id="worker-b", settings=settings)
    assert second is None
    with session_scope() as session:
        job = queries_repo.get_query(session, query_id)
        assert job.status == QueryStatus.RUNNING
        assert job.attempt == 1


def set_capacity(scope_type: str, scope_id: str, value: int) -> None:
    from app.repositories import datasources as datasources_repo

    with session_scope() as session:
        datasources_repo.upsert_capacity(session, scope_type, scope_id, value)


def test_per_user_capacity_blocks_extra_claims(ids):
    """Per-user capacity is enforced in the claim transaction."""
    settings = get_settings()
    set_capacity("datasource", str(ids["datasource_id"]), 10)
    set_capacity("global", "all", 10)
    set_capacity("user", str(ids["user_id"]), 2)
    for _ in range(3):
        make_queued_job(ids["user_id"], ids["datasource_id"])
    claimed = []
    with session_scope() as session:
        for _ in range(2):
            claim = scheduler.claim_next(session, worker_id="capacity-worker", settings=settings)
            assert claim is not None
            claimed.append(claim)
        blocked = scheduler.claim_next(session, worker_id="capacity-worker", settings=settings)
    assert blocked is None, "per-user capacity should block the third claim"
    with session_scope() as session:
        lease = session.get(ExecutionLease, claimed[0].query_id)
        lease.state = "RELEASED"
    # A blocked claim backs off for one second so the worker loop cannot spin.
    import time as _time

    _time.sleep(1.2)
    with session_scope() as session:
        next_claim = scheduler.claim_next(session, worker_id="capacity-worker", settings=settings)
    assert next_claim is not None


def test_global_capacity_blocks_across_users(ids):
    """Global capacity caps concurrent executions across users and datasources."""
    import secrets

    from app.auth.passwords import hash_password
    from app.repositories import datasources as datasources_repo
    from app.repositories import users as users_repo

    settings = get_settings()
    set_capacity("global", "all", 2)
    set_capacity("datasource", str(ids["datasource_id"]), 10)
    set_capacity("user", str(ids["user_id"]), 10)
    with session_scope() as session:
        second = users_repo.create_user(session, "recovery-user-2", hash_password(secrets.token_urlsafe(8)))
        second_id = second.id
    set_capacity("user", str(second_id), 10)
    for owner in (ids["user_id"], second_id):
        for _ in range(2):
            make_queued_job(owner, ids["datasource_id"])
    with session_scope() as session:
        for _ in range(2):
            claim = scheduler.claim_next(session, worker_id="global-worker", settings=settings)
            assert claim is not None, "claims should fill the global budget exactly"
        blocked = scheduler.claim_next(session, worker_id="global-worker", settings=settings)
    assert blocked is None, "global capacity should block the third execution"


def test_datasource_capacity_blocks_across_users(ids):
    import secrets

    from app.auth.passwords import hash_password
    from app.repositories import datasources as datasources_repo
    from app.repositories import users as users_repo

    settings = get_settings()
    set_capacity("global", "all", 10)
    set_capacity("datasource", str(ids["datasource_id"]), 2)
    set_capacity("user", str(ids["user_id"]), 10)
    with session_scope() as session:
        second = users_repo.create_user(session, "recovery-user-3", hash_password(secrets.token_urlsafe(8)))
        second_id = second.id
    set_capacity("user", str(second_id), 10)
    for owner in (ids["user_id"], second_id):
        make_queued_job(owner, ids["datasource_id"])
    with session_scope() as session:
        assert scheduler.claim_next(session, worker_id="ds-worker", settings=settings) is not None
        assert scheduler.claim_next(session, worker_id="ds-worker", settings=settings) is not None
        blocked = scheduler.claim_next(session, worker_id="ds-worker", settings=settings)
    assert blocked is None, "datasource capacity should block the third execution"


def test_result_ttl_expiry_and_cleanup(ids, admin_client):
    """Expired results return 410 and the reconciler removes files and rows."""
    import os

    from tests.helpers import drain_worker, setup_datasource_and_grants

    source_url = os.environ.get("AIND_TEST_SOURCE_URL")
    if not source_url:
        pytest.skip("AIND_TEST_SOURCE_URL not set")
    source = setup_datasource_and_grants(admin_client, source_url)
    response = admin_client.post(
        "/api/v1/queries",
        json={"datasource_id": source.datasource_id, "sql": "SELECT COUNT(*) AS n FROM fixture_metrics"},
    )
    query_id = response.json()["query_id"]
    drain_worker()

    with session_scope() as session:
        result = queries_repo.get_result(session, uuid.UUID(query_id))
        assert result is not None
        storage_key = result.storage_key
        result.expires_at = utcnow() - timedelta(minutes=1)

    expired = admin_client.get(f"/api/v1/queries/{query_id}/results")
    assert expired.status_code == 410
    assert expired.json()["error"]["code"] == "RESULT_EXPIRED"

    store = get_result_store()
    path = store.path_for(storage_key)
    assert path.exists()
    with session_scope() as session:
        summary = scheduler.cleanup_ttl(session, get_settings(), store)
    assert summary["results_expired"] >= 1
    assert not path.exists()
    with session_scope() as session:
        assert queries_repo.get_result(session, uuid.UUID(query_id)) is None
