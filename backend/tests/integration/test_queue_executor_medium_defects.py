"""Real PostgreSQL lock regressions; no source database is required."""
from datetime import timedelta
import uuid

import pytest
from sqlalchemy import select, text

from app.config import Settings
from app.constants import QueryStatus, QueueState
from app.db import get_session_factory, session_scope
from app.ids import utcnow
from app.models.orm import Datasource, User, QueryJob, TaskQueue, AnalysisTask
from app.query import scheduler
from app.repositories import analyses, queries, queue

pytestmark = pytest.mark.integration


@pytest.fixture()
def queued_query(bootstrap):
    with session_scope() as session:
        user = session.execute(select(User).where(User.username == bootstrap["username"])).scalar_one()
        source = Datasource(id=uuid.uuid4(), name="lock-regression", kind="postgres",
            connection_config={}, secret_ref="not-used-by-control-test", capabilities={}, created_by=user.id)
        session.add(source)
        session.flush()
        job = queries.create_query_job(session, user_id=user.id, datasource_id=source.id,
            purpose="query", original_sql="SELECT 1", validated_sql="SELECT 1", parameters={},
            sql_hash=None, limits={}, policy_revision=1)
        job.created_at = utcnow() - timedelta(seconds=120)
        queue.enqueue(session, kind="query", resource_id=job.id)
        return job.id


def test_expiration_job_lock_does_not_make_claimer_hold_queue(queued_query):
    settings = Settings(queue_timeout_seconds=60)
    factory = get_session_factory()
    with factory() as cleaner, factory() as worker:
        cleaner.execute(text("SET LOCAL lock_timeout = '500ms'"))
        worker.execute(text("SET LOCAL lock_timeout = '500ms'"))
        assert queue.list_expired_queued(cleaner, 60)[0].id == queued_query
        # The old queue-first claim waits for the cleaner while holding queue.
        assert queue.claim_next_query(worker, worker_id="contender", settings=settings) is None
        assert scheduler.expire_queued(cleaner, settings) == [str(queued_query)]
        cleaner.commit()
        worker.rollback()
    with factory() as session:
        assert session.get(QueryJob, queued_query).status == QueryStatus.FAILED
        row = session.execute(select(TaskQueue).where(TaskQueue.resource_id == queued_query)).scalar_one()
        assert row.state == QueueState.FAILED


def test_model_checkpoint_task_lock_does_not_block_claim_release(bootstrap):
    settings = Settings()
    factory = get_session_factory()
    with session_scope() as session:
        user = session.execute(select(User).where(User.username == bootstrap["username"])).scalar_one()
        agent_session = analyses.create_session(session, user_id=user.id, title="locks")
        task = analyses.create_analysis(session, session_id=agent_session.id, user_id=user.id,
            question="locks", context={}, parent_id=None, budget={}, policy_revision=1)
        row = queue.enqueue(session, kind="analysis", resource_id=task.id)
        task_id = task.id
    with session_scope() as session:
        claim = queue.claim_next_analysis(session, worker_id="model-worker", settings=settings)
        row = session.get(TaskQueue, claim.queue_id)
        row.lease_until = utcnow() - timedelta(seconds=1)
    with factory() as first, factory() as contender:
        first.execute(text("SET LOCAL lock_timeout = '500ms'"))
        contender.execute(text("SET LOCAL lock_timeout = '500ms'"))
        first.execute(select(AnalysisTask).where(AnalysisTask.id == task_id).with_for_update()).scalar_one()
        # Simulates the first worker still in its model call after lease expiry.
        assert queue.claim_next_analysis(contender, worker_id="second", settings=settings) is None
        assert queue.release_analysis_claim(first, claim)
        first.commit()
        contender.rollback()
    with factory() as session:
        row = session.get(TaskQueue, claim.queue_id)
        assert row.state == QueueState.READY
        assert row.fencing_token == claim.fencing_token
