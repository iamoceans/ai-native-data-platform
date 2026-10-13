"""P5 acceptance: worker recovery (C07) and permission revocation (C10).

C07: an orchestrator that dies between phases must leave the turn recoverable,
with exactly one analysis, one result message, and a budget that is not reset.

C10: report-derived text in the transcript obeys the *current* dataset grants -
a revoked grant blanks the claims and quotes while keeping the message.
"""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

import pytest

from app.agent.conversation import orchestrator
from app.agent.conversation.budget import TurnBudget
from app.agent.llm import FakeLLMProvider, ProviderResolution
from app.api.deps import get_settings_dep
from app.config import get_settings
from app.db import get_session_factory, session_scope
from app.ids import utcnow
from app.metrics.registry import MetricDefinitionModel, sync_metric_definitions
from app.repositories import conversation as conversation_repo
from app.repositories import queue as queue_repo
from tests.helpers import drain_conversations, run_pending_analyses, setup_datasource_and_grants

pytestmark = pytest.mark.integration

SCOPE = {
    "metric_key": "fixture_revenue",
    "baseline_start": "2026-09-01",
    "baseline_end": "2026-09-02",
    "current_start": "2026-09-02",
    "current_end": "2026-09-03",
    "dimensions": ["country"],
    "data_complete_state": "complete",
}


class ScriptedProvider(FakeLLMProvider):
    """FakeLLMProvider reading a shared script list (the base class copies it)."""

    def __init__(self, script: list[dict], model_id: str = "fake-intent-v1") -> None:
        self._outputs = script
        self._model_id = model_id


@pytest.fixture()
def env(admin_client, integration_env, monkeypatch):
    setup_datasource_and_grants(admin_client, integration_env["source_url"], grant_roles=["admin"])
    definition = MetricDefinitionModel.model_validate(
        {
            "metric_key": "fixture_revenue",
            "version": 1,
            "name": "Fixture revenue",
            "dataset": "public.fixture_metrics",
            "components": {"revenue": {"aggregation": "sum", "column": "revenue_usd"}},
            "formula": "revenue",
            "unit": "USD",
            "currency": "USD",
            "timezone": "UTC",
            "grain": ["dt", "country", "platform"],
            "allowed_dimensions": ["country", "platform"],
            "aggregation_kind": "sum",
            "freshness": {"date_column": "dt", "completeness_source": "fixture"},
        }
    )
    with session_scope() as session:
        sync_metric_definitions(session, [definition])

    settings = get_settings()
    previous = settings.conversational_ask_enabled
    settings.conversational_ask_enabled = True
    admin_client.app.dependency_overrides[get_settings_dep] = lambda: settings
    scripted: list[dict] = []
    monkeypatch.setattr(
        orchestrator,
        "resolve_provider",
        lambda settings: ProviderResolution(provider=ScriptedProvider(scripted)),
    )
    admin_client.scripted = scripted
    yield admin_client
    admin_client.app.dependency_overrides.pop(get_settings_dep, None)
    settings.conversational_ask_enabled = previous


def _session(client) -> str:
    created = client.post("/api/v1/sessions", json={"title": "P5"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _turn(client, session_id: str, text: str, key: str, **extra):
    return client.post(
        f"/api/v1/sessions/{session_id}/turns",
        headers={"Idempotency-Key": key},
        json={"text": text, **extra},
    )


def _expire_turn_lease(turn_id: uuid.UUID) -> None:
    """Simulate a worker that died mid-lease: the queue row's lease has lapsed."""
    from sqlalchemy import select, update

    from app.models.orm import TaskQueue

    with session_scope() as session:
        session.execute(
            update(TaskQueue)
            .where(TaskQueue.kind == "conversation", TaskQueue.resource_id == turn_id)
            .values(lease_until=utcnow() - timedelta(seconds=1))
        )
        session.flush()


def test_c07_a_crash_after_prepare_is_recovered_without_duplicates(env):
    """The model call happens outside the transaction, so a crash there is safe."""
    client = env
    session_id = _session(client)
    settings = get_settings()
    client.scripted.append({"intent": "analyze", "scope_patch": SCOPE})
    submitted = _turn(client, session_id, "先问一次", "p5-c07-1")
    turn_id = uuid.UUID(submitted.json()["id"])

    with session_scope() as session:
        claim = queue_repo.claim_next_turn(session, worker_id="p5-a", settings=settings)
    assert claim is not None

    # Phase 1 only: the turn is RESOLVING with the worst-case reservation written,
    # then the worker "dies" (no decide, no apply) and its lease lapses.
    with session_scope() as session:
        preparation = orchestrator._prepare_turn(session, turn_id=turn_id, settings=settings)
        session.commit()
    assert not isinstance(preparation, str)
    _expire_turn_lease(turn_id)

    with session_scope() as session:
        requeued = queue_repo.requeue_stale_turns(session)
    assert str(turn_id) in {str(item) for item in requeued}

    # Recovery: the second attempt finishes the turn.
    drain_conversations()
    run_pending_analyses()
    drain_conversations()

    detail = client.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}").json()
    assert detail["status"] == "COMPLETED", detail
    analyses = client.get("/api/v1/analyses").json()["items"]
    assert len(analyses) == 1  # exactly one analysis, no duplicate
    messages = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"]
    assert [m["role"] for m in messages] == ["user", "assistant"]

    with session_scope() as session:
        row = conversation_repo.get_turn(session, turn_id)
        budget = dict(row.budget or {})
    # The budget was not reset by the recovery: the reservation from the crashed
    # attempt is still accounted for, and the model call is counted once.
    assert budget["model_calls"] == 1
    assert budget["reserved_input"] == 0
    assert budget["input_tokens"] > 0


def test_c07_running_the_same_turn_twice_is_a_no_op(env):
    """A retry of a turn the reconciler already owns must not call the model again."""
    client = env
    session_id = _session(client)
    settings = get_settings()
    factory = get_session_factory()

    client.scripted.append({"intent": "analyze", "scope_patch": SCOPE})
    submitted = _turn(client, session_id, "只处理一次", "p5-c07-2")
    turn_id = uuid.UUID(submitted.json()["id"])
    drain_conversations()  # now ANALYZING with one analysis

    # A second execution pass with an empty script: if it tried to call the model
    # it would fail the turn (the fake has no output).
    status = orchestrator.run_turn(factory, turn_id=turn_id, settings=settings)
    assert status == "ANALYZING"
    assert client.scripted == []  # the scripted decision was consumed once

    analyses = client.get("/api/v1/analyses").json()["items"]
    assert len(analyses) == 1


def test_c10_revoking_the_grant_blanks_report_text_in_the_transcript(env):
    client = env
    session_id = _session(client)
    client.scripted.append({"intent": "analyze", "scope_patch": SCOPE})
    submitted = _turn(client, session_id, "先分析", "p5-c10-1")
    turn_id = submitted.json()["id"]
    drain_conversations()
    run_pending_analyses()
    drain_conversations()
    detail = client.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}").json()
    assert detail["status"] == "COMPLETED", detail

    before = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"][-1]
    assert before["content"]["claims"], "the result message should carry claims"

    # Revoke every query grant: the source registers more than one table, and the
    # analysis must lose access to the one it actually read.
    grants = client.get("/api/v1/admin/grants").json()["items"]
    query_grants = [item for item in grants if item["action"] == "query"]
    assert query_grants, "expected at least one query grant"
    for grant in query_grants:
        revoked = client.delete(f"/api/v1/admin/grants/{grant['id']}")
        assert revoked.status_code == 204, revoked.text

    after = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"][-1]
    assert after["content"]["permission_revoked"] is True
    assert after["content"]["claims"] == []
    assert after["content"]["limitations"] == []
    # The message itself stays: the transcript is the user's own record.
    assert after["content"]["kind"] == "analysis_result"
    assert after["content"]["analysis_id"] == before["content"]["analysis_id"]

    # The report and evidence endpoints refuse the same way they always did.
    analysis_id = after["content"]["analysis_id"]
    assert client.get(f"/api/v1/analyses/{analysis_id}/report").status_code == 403
    assert client.get(f"/api/v1/analyses/{analysis_id}/evidence").status_code == 403

    # The live stream announces the revocation instead of letting a watching
    # client believe the analysis is still readable (design C10, SSE). A
    # Last-Event-ID is supplied so the stream still terminates on its own
    # (`stream.end`) - an abandoned generator would keep a database session open
    # and deadlock the next test's truncate.
    seen_event = False
    payload = None
    with client.stream(
        "GET", f"/api/v1/sessions/{session_id}/events", headers={"Last-Event-ID": "1"}
    ) as response:
        assert response.status_code == 200
        for line in response.iter_lines():
            if line.startswith("event: analysis.permission.revoked"):
                seen_event = True
            elif seen_event and line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
            elif line.startswith("event: stream.end"):
                break
    assert seen_event, "the stream did not announce the revocation"
    assert payload["analysis_id"] == analysis_id
    assert payload["session_id"] == session_id
