"""P3 acceptance: model-backed intent, inheritance, explanations, skills, SSE.

Cases from design 13.2: C03 (inheritance and set/clear), C04 (relative dates
frozen), C05 (explain without a new query), C09-lite (SSE resume), C11 (no model
degrades loudly), plus skill loading and the late-reply guard.
"""

from __future__ import annotations

import json
import threading
import time
import uuid

import pytest

from app.agent.conversation import orchestrator
from app.agent.conversation.intent import PROMPT_VERSION as INTENT_PROMPT_VERSION
from app.agent.llm import FakeLLMProvider, ProviderResolution
from app.api.deps import get_settings_dep
from app.config import get_settings
from app.db import session_scope
from app.metrics.registry import MetricDefinitionModel, sync_metric_definitions
from app.repositories import conversation as conversation_repo
from app.repositories import queue as queue_repo
from app.skills.service import sync_builtins
from tests.helpers import drain_conversations, run_pending_analyses, setup_datasource_and_grants

pytestmark = pytest.mark.integration

class ScriptedProvider(FakeLLMProvider):
    """FakeLLMProvider reading a shared script list (the base class copies it)."""

    def __init__(self, script: list[dict], model_id: str = "fake-intent-v1") -> None:
        self._outputs = script
        self._model_id = model_id


SCOPE = {
    "metric_key": "fixture_revenue",
    "baseline_start": "2026-09-01",
    "baseline_end": "2026-09-02",
    "current_start": "2026-09-02",
    "current_end": "2026-09-03",
    "dimensions": ["country"],
    "data_complete_state": "complete",
}


@pytest.fixture()
def env(admin_client, integration_env, monkeypatch):
    """Datasource + grants + metric, with a scripted model and the flag on."""
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
        sync_builtins(session, settings=get_settings())

    settings = get_settings()
    previous = settings.conversational_ask_enabled
    settings.conversational_ask_enabled = True
    admin_client.app.dependency_overrides[get_settings_dep] = lambda: settings

    scripted: list[dict] = []
    provider = ScriptedProvider(scripted)
    monkeypatch.setattr(
        orchestrator, "resolve_provider", lambda settings: ProviderResolution(provider=provider)
    )
    admin_client.scripted = scripted
    yield admin_client
    admin_client.app.dependency_overrides.pop(get_settings_dep, None)
    settings.conversational_ask_enabled = previous


def _session(client) -> str:
    created = client.post("/api/v1/sessions", json={"title": "P3"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _turn(client, session_id: str, text: str, key: str, **extra):
    body = {"text": text, **extra}
    return client.post(
        f"/api/v1/sessions/{session_id}/turns",
        headers={"Idempotency-Key": key},
        json=body,
    )


def _settle(client, session_id: str, turn_id: str, cycles: int = 6) -> dict:
    for _ in range(cycles):
        drain_conversations()
        run_pending_analyses()
        drain_conversations()
        detail = client.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}").json()
        if detail["status"] in ("COMPLETED", "FAILED", "CANCELLED", "WAITING_INPUT"):
            return detail
    raise AssertionError(f"turn never settled: {detail}")


def test_c03_inheritance_and_set_clear_through_the_model_patch(env):
    client = env
    session_id = _session(client)

    # Turn 1: the model proposes the full scope.
    client.scripted.append({"intent": "analyze", "scope_patch": SCOPE})
    first = _turn(client, session_id, "收入变化来自哪里？", "p3-1")
    assert first.status_code == 202, first.text
    settled = _settle(client, session_id, first.json()["id"])
    assert settled["status"] == "COMPLETED", settled
    detail = client.get(f"/api/v1/sessions/{session_id}").json()
    assert detail["resolved_context"]["metric_key"] == "fixture_revenue"
    assert detail["resolved_context"]["dimensions"] == ["country"]

    # Turn 2 "只看安卓": the model sets only the filter, everything else inherits.
    client.scripted.append(
        {"intent": "analyze", "scope_patch": {"filters": {"platform": "android"}}}
    )
    second = _turn(client, session_id, "只看安卓", "p3-2")
    settled = _settle(client, session_id, second.json()["id"])
    assert settled["status"] == "COMPLETED", settled
    analysis = client.get(f"/api/v1/analyses/{settled['analysis_id']}").json()
    assert analysis["context"]["filters"] == {"platform": "android"}
    assert analysis["context"]["metric_key"] == "fixture_revenue"  # inherited
    assert analysis["context"]["dimensions"] == ["country"]  # inherited

    # Turn 3 "去掉筛选": clear wins over inheritance.
    client.scripted.append({"intent": "analyze", "scope_patch": {"clear": ["filters"]}})
    third = _turn(client, session_id, "去掉筛选", "p3-3")
    settled = _settle(client, session_id, third.json()["id"])
    assert settled["status"] == "COMPLETED", settled
    analysis = client.get(f"/api/v1/analyses/{settled['analysis_id']}").json()
    assert analysis["context"]["filters"] == {}


def test_c04_relative_period_is_frozen_before_the_analysis_runs(env):
    client = env
    session_id = _session(client)
    client.scripted.append(
        {
            "intent": "analyze",
            "scope_patch": {
                "metric_key": "fixture_revenue",
                "comparison": "previous_period",
                "current_start": "2026-09-08",
                "current_end": "2026-09-15",
            },
        }
    )
    submitted = _turn(client, session_id, "上周怎么样", "p3-4")
    settled = _settle(client, session_id, submitted.json()["id"])
    assert settled["status"] == "COMPLETED", settled
    analysis = client.get(f"/api/v1/analyses/{settled['analysis_id']}").json()
    # The relation was resolved server-side into absolute dates.
    assert analysis["context"]["baseline_start"] == "2026-09-01"
    assert analysis["context"]["baseline_end"] == "2026-09-08"
    scope = client.get(f"/api/v1/sessions/{session_id}").json()["resolved_context"]
    assert scope["comparison"] == "previous_period"
    assert scope["field_origins"]["baseline_start"] == "model"


def test_c05_explain_answers_from_evidence_without_a_new_query(env):
    client = env
    session_id = _session(client)
    client.scripted.append({"intent": "analyze", "scope_patch": SCOPE})
    first = _turn(client, session_id, "先分析一次", "p3-5")
    settled = _settle(client, session_id, first.json()["id"])
    analysis_id = settled["analysis_id"]
    before = len(client.get("/api/v1/queries", params={"limit": 100}).json()["items"])

    # Explain: the model points at a claim of that analysis.
    report = client.get(f"/api/v1/analyses/{analysis_id}/report").json()
    claim_id = report["claims"][0]["id"]
    client.scripted.append(
        {
            "intent": "explain",
            "references": [{"analysis_id": analysis_id, "claim_id": claim_id}],
            "reply": "这条结论来自上一次分析的结果。",
        }
    )
    explained = _turn(client, session_id, "为什么这样判断？", "p3-6")
    settled = _settle(client, session_id, explained.json()["id"])
    assert settled["status"] == "COMPLETED", settled
    assert settled["analysis_id"] is None  # no new analysis

    after = len(client.get("/api/v1/queries", params={"limit": 100}).json()["items"])
    assert after == before  # and no new query

    message = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"][-1]
    assert message["content"]["kind"] == "explanation"
    assert message["content"]["evidence_state"] == "ok"
    quote = message["content"]["quotes"][0]
    assert quote["claim_id"] == claim_id
    assert quote["text"] == report["claims"][0]["text"]  # verbatim from the report


def test_explain_degrades_when_the_reference_is_not_permitted(env):
    client = env
    session_id = _session(client)
    client.scripted.append(
        {
            "intent": "explain",
            "references": [{"analysis_id": str(uuid.uuid4()), "claim_id": "claim-1"}],
            "reply": "看看这次分析。",
        }
    )
    submitted = _turn(client, session_id, "解释一下", "p3-7")
    settled = _settle(client, session_id, submitted.json()["id"])
    assert settled["status"] == "COMPLETED"
    message = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"][-1]
    assert message["content"]["evidence_state"] == "unavailable"
    assert message["content"]["quotes"] == []
    assert message["content"]["dropped_references"]


def test_skill_selection_is_loaded_snapshotted_and_injected(env):
    client = env
    session_id = _session(client)
    skills = client.get("/api/v1/skills").json()["items"]
    builtin = next(row for row in skills if row["slug"] == "revenue-change-reading")
    version_id = builtin["latest_version"]["id"]

    client.scripted.append({"intent": "analyze", "scope_patch": SCOPE})
    submitted = _turn(
        client,
        session_id,
        "按技能方法分析",
        "p3-8",
        skill_selection={"mode": "manual", "version_ids": [version_id]},
    )
    settled = _settle(client, session_id, submitted.json()["id"])
    assert settled["status"] == "COMPLETED", settled
    snapshot = settled["skill_snapshot"]
    assert snapshot["mode"] == "manual"
    assert snapshot["loaded"][0]["version_id"] == version_id
    assert snapshot["loaded"][0]["files"] == ["SKILL.md"]
    assert snapshot["bytes_loaded"] > 0

    # The block reached the analysis state, labelled as advisory context.
    analysis = client.get(f"/api/v1/analyses/{settled['analysis_id']}").json()
    block = analysis["state"]["conversation_skill_block"]
    assert "untrusted data" in block
    assert "revenue-change-reading" in block


def test_c11_without_a_model_the_rule_path_still_runs_and_says_so(env, monkeypatch):
    client = env
    session_id = _session(client)
    monkeypatch.setattr(
        orchestrator,
        "resolve_provider",
        lambda settings: ProviderResolution(provider=None, warning="no model configured"),
    )
    submitted = _turn(client, session_id, "结构化的范围", "p3-9", scope_patch=SCOPE)
    settled = _settle(client, session_id, submitted.json()["id"])
    assert settled["status"] == "COMPLETED", settled  # the deterministic path ran
    assert settled["analysis_id"]

    detail = client.get(f"/api/v1/analyses/{settled['analysis_id']}").json()
    # No model was used: the deterministic template produced the plan.
    assert detail["state"]["model_id"] == "deterministic-template-v1"
    messages = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"]
    assert messages[-1]["content"]["kind"] == "analysis_result"


def test_late_model_reply_after_cancel_publishes_nothing(env):
    """A cancel that lands during the model call wins (design 10.1).

    Deterministic on purpose: the provider cancels the turn *inside* the call, so
    the orchestrator's post-call re-read is what decides - no threads, no timing.
    """
    client = env
    session_id = _session(client)
    submitted = _turn(client, session_id, "会被取消的一轮", "p3-10")
    turn_id = submitted.json()["id"]
    settings = get_settings()
    with session_scope() as session:
        claim = queue_repo.claim_next_turn(session, worker_id="p3-late", settings=settings)
    assert claim is not None

    class CancellingProvider(ScriptedProvider):
        def generate_structured(self, **kwargs):
            with session_scope() as session:
                row = conversation_repo.get_turn_for_update(session, claim.turn_id)
                conversation_repo.finish(
                    session, row, status="CANCELLED",
                    error={"code": "QUERY_CANCELLED", "message": "turn cancelled by the user"},
                )
            return super().generate_structured(**kwargs)

    client.scripted.append({"intent": "analyze", "scope_patch": SCOPE})
    original = orchestrator.resolve_provider
    orchestrator.resolve_provider = lambda settings: ProviderResolution(
        provider=CancellingProvider(client.scripted, model_id="cancelling-fake")
    )
    try:
        from app.db import get_session_factory

        status = orchestrator.run_turn(
            get_session_factory(), turn_id=claim.turn_id, settings=settings
        )
    finally:
        orchestrator.resolve_provider = original

    assert status == "CANCELLED"
    final = client.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}").json()
    assert final["status"] == "CANCELLED"
    assert final["analysis_id"] is None  # nothing was created after the cancel
    messages = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"]
    assert [m["role"] for m in messages] == ["user"]  # and nothing was published


def test_session_events_resume_with_last_event_id(env):
    """C09-lite: persisted events, monotonic ids, and a resume that repeats nothing.

    Each stream is read to its own `stream.end` instead of being abandoned early:
    an abandoned SSE generator keeps a database session open, which then fights
    the next test's truncate.
    """
    client = env
    session_id = _session(client)
    client.scripted.append({"intent": "analyze", "scope_patch": SCOPE})
    submitted = _turn(client, session_id, "产生事件的一轮", "p3-11")
    _settle(client, session_id, submitted.json()["id"])

    def read_stream(headers=None):
        ids = []
        with client.stream(
            "GET", f"/api/v1/sessions/{session_id}/events", headers=headers or {}
        ) as response:
            assert response.status_code == 200
            for line in response.iter_lines():
                if line.startswith("id: "):
                    ids.append(line[4:])
                if line.startswith("event: stream.end"):
                    break
        return ids

    ids = read_stream()
    assert ids, "the stream produced no events"
    assert ids == sorted(ids, key=int)

    resumed = read_stream({"Last-Event-ID": ids[-1]})
    assert ids[-1] not in resumed
    assert all(int(item) > int(ids[-1]) for item in resumed)


def test_turn_budget_is_persisted_with_a_utc_deadline(env):
    client = env
    session_id = _session(client)
    client.scripted.append({"intent": "analyze", "scope_patch": SCOPE})
    submitted = _turn(client, session_id, "预算记录", "p3-12")
    turn_id = submitted.json()["id"]
    _settle(client, session_id, turn_id)
    with session_scope() as session:
        turn = conversation_repo.get_turn(session, uuid.UUID(turn_id))
        budget = dict(turn.budget or {})
    assert budget["deadline_at"]
    assert budget["model_calls"] >= 1
    assert budget["input_tokens"] > 0
    assert INTENT_PROMPT_VERSION in " ".join(budget["history"])
