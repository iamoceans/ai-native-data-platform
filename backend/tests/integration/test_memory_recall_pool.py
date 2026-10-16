"""Large governed recall: grants precede LIMIT without per-row DB round trips."""
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
import threading
import time
import uuid

import pytest
from sqlalchemy import event, select

from app.agent import memory, runner, semantic
from app.config import get_settings
from app.db import get_session_factory, session_scope
from app.ids import utcnow
from app.models.orm import BusinessMemory, Dataset, Permission, QueryDependency, QueryJob, Role, User
from app.repositories import memory as repository, users
from tests.helpers import drain_worker, run_pending_analyses
from tests.integration.test_priority_workflows import platform, analysis

pytestmark = pytest.mark.integration


def evidence(client):
    aid, claim = analysis(client)
    runner.run_claim(get_session_factory(), claim=claim, settings=get_settings())
    drain_worker()
    run_pending_analyses()
    return aid


def remembered(aid, owner, statement, reuse=0, status="proposed"):
    return BusinessMemory(id=uuid.uuid4(), metric_key="fixture_revenue", kind="segment",
                          statement=statement, dedupe_key=uuid.uuid4().hex, analysis_id=aid,
                          owner_user_id=owner, reuse_count=reuse, status=status)


def test_large_authorized_pool_recovers_target_beyond_old_200(platform, monkeypatch):
    allowed, denied = evidence(platform), evidence(platform)
    owner = uuid.UUID(platform.get('/api/v1/auth/me').json()['id'])
    with session_scope() as db:
        query = db.scalar(select(QueryJob).where(QueryJob.analysis_id == denied))
        hidden = Dataset(id=uuid.uuid4(), datasource_id=query.datasource_id, catalog_name="fixture",
                         schema_name="public", object_name="ungranted_auxiliary", object_type="table")
        db.add(hidden)
        db.flush()
        db.add(QueryDependency(query_id=query.id, dataset_id=hidden.id))
        allowed_rows = [remembered(allowed, owner, "Routine maintenance guidance", 10 if n < 600 else 0)
                        for n in range(1050)]
        target = remembered(allowed, owner, "US release version revenue decline", 5)
        forbidden = [remembered(denied, owner, "版本发布后营收下滑", 1000) for _ in range(650)]
        orphaned = [remembered(None, owner, "版本发布后营收下滑", 2000) for _ in range(100)]
        rejected = remembered(allowed, owner, "版本发布后营收下滑", 3000, "rejected")
        db.add_all([*allowed_rows, target, *forbidden, *orphaned, rejected])
        target_id = target.id
        forbidden_ids = {row.id for row in [*forbidden, *orphaned, rejected]}
    captured = []
    rank = semantic.rank_memories
    def inspected(rows, question):
        captured.append({row.id for row in rows})
        return rank(rows, question)
    monkeypatch.setattr(semantic, "rank_memories", inspected)
    with session_scope() as db:
        roles = users.role_ids_for_user(db, owner)
        statements = []
        connection = db.connection()
        def count(*args):
            statements.append(args[2])
        event.listen(connection, "before_cursor_execute", count)
        try:
            started = time.perf_counter()
            brief, ids = memory.planner_brief(db, metric_key="fixture_revenue", user_id=owner,
                role_ids=roles, allowed_dimensions=["country"], question="版本发布后营收下滑")
            elapsed = time.perf_counter() - started
            assert len(statements) == 1
            assert len(captured[0]) == 1000 and not captured[0] & forbidden_ids
            assert target_id in ids and len(brief) == len(ids) == 8
            assert len(repository.list_recent(db, metric_key="fixture_revenue", role_ids=roles,
                                              user_id=owner, limit=50)) == 50
            assert repository.counts(db, metric_key="fixture_revenue", role_ids=roles, user_id=owner) == {
                "proposed": 1051, "rejected": 1,
            }
            assert len(statements) == 3  # one query per retrieval/list/count operation
            _, capped = memory.planner_brief(db, metric_key="fixture_revenue", user_id=owner,
                role_ids=roles, allowed_dimensions=[], limit=10000)
            assert len(capped) == 8
            count_before = len(statements)
            assert memory.planner_brief(db, metric_key="fixture_revenue", user_id=owner,
                role_ids=roles, allowed_dimensions=[], limit=0) == ([], [])
            assert len(statements) == count_before
            print(f"recall_fixture_rows=1802 authorized_candidates=1000 injected=8 db_queries=1 seconds={elapsed:.4f}")
        finally:
            event.remove(connection, "before_cursor_execute", count)


@pytest.mark.parametrize("action", ["discover", "query"])
def test_expired_source_grant_is_excluded_before_recall_and_counts(platform, action):
    aid = evidence(platform)
    owner = uuid.UUID(platform.get('/api/v1/auth/me').json()['id'])
    with session_scope() as db:
        row = remembered(aid, owner, "US release revenue decline")
        db.add(row)
        db.flush()
        roles = users.role_ids_for_user(db, owner)
        assert repository.memory_dataset_actions_ok(db, role_ids=roles, row=row, user_id=owner)
        dataset = db.scalar(select(QueryDependency.dataset_id).join(QueryJob).where(QueryJob.analysis_id == aid))
        permission = db.scalar(select(Permission).where(Permission.dataset_id == dataset,
                                Permission.role_id.in_(roles), Permission.action == action))
        permission.expires_at = utcnow() - timedelta(seconds=1)
    with session_scope() as db:
        roles = users.role_ids_for_user(db, owner)
        assert memory.planner_brief(db, metric_key="fixture_revenue", allowed_dimensions=[],
                                   user_id=owner, role_ids=roles) == ([], [])
        assert repository.list_recent(db, user_id=owner, role_ids=roles) == []
        assert repository.counts(db, user_id=owner, role_ids=roles) == {}
    assert platform.get('/api/v1/memory').json()['items'] == []


def test_different_roles_can_supply_the_two_required_grants(platform):
    aid = evidence(platform)
    owner = uuid.UUID(platform.get('/api/v1/auth/me').json()['id'])
    with session_scope() as db:
        original = users.role_ids_for_user(db, owner)
        extra = uuid.uuid4()
        db.add(Role(id=extra, name='recall-extra-' + extra.hex))
        db.flush()
        dataset = db.scalar(select(QueryDependency.dataset_id).join(QueryJob).where(QueryJob.analysis_id == aid))
        grant = db.scalar(select(Permission).where(Permission.dataset_id == dataset,
                          Permission.role_id.in_(original), Permission.action == 'query'))
        grant.role_id = extra
        row = remembered(aid, owner, "US revenue decline")
        db.add(row)
        db.flush()
        assert repository.list_for_metric(db, metric_key="fixture_revenue", user_id=owner, role_ids=original) == []
        both = [*original, extra]
        assert repository.list_for_metric(db, metric_key="fixture_revenue", user_id=owner, role_ids=both) == [row]
        assert repository.memory_dataset_actions_ok(db, role_ids=both, row=row, user_id=owner)
        assert repository.list_for_metric(db, metric_key="fixture_revenue", user_id=owner, role_ids=[]) == []
        assert repository.list_for_metric(db, metric_key="fixture_revenue", user_id=None, role_ids=both) == []
        db.delete(grant)
        db.flush()
        assert repository.list_for_metric(db, metric_key="fixture_revenue", user_id=owner, role_ids=both) == []
        assert repository.counts(db, user_id=owner, role_ids=both) == {}
        db.add(Permission(id=uuid.uuid4(), role_id=extra, dataset_id=dataset, action="query", created_by=owner))
        db.flush()
        assert repository.list_for_metric(db, metric_key="fixture_revenue", user_id=owner, role_ids=both) == [row]
        db.get(User, owner).active = False
        db.flush()
        assert repository.list_for_metric(db, metric_key="fixture_revenue", user_id=owner, role_ids=both) == []
        assert repository.counts(db, user_id=owner, role_ids=both) == {}


def test_concurrent_reuse_increments_survive_stale_instances_and_rollback(bootstrap):
    future = utcnow() + timedelta(hours=1)
    with session_scope() as db:
        owner = db.scalar(select(User.id).where(User.username == bootstrap['username']))
        rows = [remembered(None, owner, 'Concurrent reuse fixture') for _ in range(3)]
        for row in rows:
            row.last_used_at = future
        db.add_all(rows)
        ids = [row.id for row in rows]
    barrier = threading.Barrier(4)
    def reuse(index):
        with session_scope() as db:
            # Hold old ORM instances in every session before any writer runs.
            preloaded = list(db.scalars(select(BusinessMemory).where(BusinessMemory.id.in_(ids))))
            assert len(preloaded) == 3 and all(row.reuse_count == 0 for row in preloaded)
            barrier.wait(timeout=10)
            ordered = ids if index % 2 else list(reversed(ids))
            repository.mark_used(db, [*ordered, ordered[0]])
    with ThreadPoolExecutor(max_workers=4) as writers:
        list(writers.map(reuse, range(4)))
    with session_scope() as db:
        rows = list(db.scalars(select(BusinessMemory).where(BusinessMemory.id.in_(ids))))
        assert all(row.reuse_count == 4 and row.last_used_at == future for row in rows)
        repository.mark_used(db, ids)
        db.rollback()
    with session_scope() as db:
        assert all(row.reuse_count == 4 for row in db.scalars(select(BusinessMemory).where(BusinessMemory.id.in_(ids))))
