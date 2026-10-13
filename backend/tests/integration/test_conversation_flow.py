"""P1 acceptance: persistent conversation turns over the governed runner.

Covers design acceptance cases C01 (three turns + refresh: same session, stable
order, no rebuilt tasks), C06 (idempotent resend, two-tab conflict) and the
clarification path, plus the flag gate and the legacy-entry busy protection.
"""

from __future__ import annotations

import uuid

import pytest

from app.agent import runner as analysis_runner  # noqa: F401 - imported for parity with helpers
from app.api.deps import get_settings_dep
from app.config import get_settings
from app.db import session_scope
from app.metrics.registry import MetricDefinitionModel, sync_metric_definitions
from app.models.orm import AgentMessage
from tests.helpers import drain_conversations, run_pending_analyses, setup_datasource_and_grants

pytestmark = pytest.mark.integration

FULL_SCOPE = {
    "metric_key": "fixture_revenue",
    "baseline_start": "2026-09-01",
    "baseline_end": "2026-09-02",
    "current_start": "2026-09-02",
    "current_end": "2026-09-03",
    "dimensions": ["country"],
    "data_complete_state": "complete",
}


@pytest.fixture()
def conversation_env(admin_client, integration_env):
    """Datasource + grants + metric, with the conversational flag switched on."""
    setup_datasource_and_grants(
        admin_client, integration_env["source_url"], grant_roles=["admin"]
    )
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
    yield admin_client
    admin_client.app.dependency_overrides.pop(get_settings_dep, None)
    settings.conversational_ask_enabled = previous


def _new_session(client) -> str:
    created = client.post("/api/v1/sessions", json={"title": "Conversation"})
    assert created.status_code == 201, created.text
    return created.json()["id"]


def _submit_turn(client, session_id: str, *, text: str, key: str, scope=None, version=None):
    body: dict = {"text": text}
    if scope is not None:
        body["scope_patch"] = scope
    if version is not None:
        body["expected_context_version"] = version
    return client.post(
        f"/api/v1/sessions/{session_id}/turns",
        headers={"Idempotency-Key": key},
        json=body,
    )


def _settle_turn(client, session_id: str, turn_id: str, *, timeout_cycles: int = 6) -> dict:
    """Drive the turn and its analysis to a terminal state the way workers do."""
    for _ in range(timeout_cycles):
        drain_conversations()
        run_pending_analyses()
        drain_conversations()
        detail = client.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}").json()
        if detail["status"] in ("COMPLETED", "FAILED", "CANCELLED", "WAITING_INPUT"):
            return detail
    raise AssertionError(f"turn {turn_id} never settled: {detail}")


def test_turn_submission_is_idempotent_and_the_session_is_busy_while_active(
    conversation_env,
):
    """C06: same key + same body -> one turn; same key + different body -> 409."""
    client = conversation_env
    session_id = _new_session(client)

    first = _submit_turn(
        client, session_id, text="收入变化来自哪里？", key="turn-key-1", scope=FULL_SCOPE
    )
    assert first.status_code == 202, first.text
    turn_id = first.json()["id"]
    assert first.json()["status"] == "QUEUED"

    replay = _submit_turn(
        client, session_id, text="收入变化来自哪里？", key="turn-key-1", scope=FULL_SCOPE
    )
    assert replay.status_code == 202
    assert replay.json()["id"] == turn_id  # replayed, not duplicated

    conflict = _submit_turn(
        client, session_id, text="另一个问题", key="turn-key-1", scope=FULL_SCOPE
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"

    # A different key while the first turn is still active: the session is busy.
    busy = _submit_turn(
        client, session_id, text="再来一个问题", key="turn-key-2", scope=FULL_SCOPE
    )
    assert busy.status_code == 409
    assert busy.json()["error"]["code"] == "SESSION_BUSY"
    assert busy.json()["error"]["details"]["turn_id"] == turn_id

    # The legacy entry point takes part in the same protection.
    legacy = client.post(
        "/api/v1/analyses",
        headers={"Idempotency-Key": f"legacy-{uuid.uuid4().hex[:6]}"},
        json={
            "session_id": session_id,
            "question": "legacy submit",
            "context": {
                "metric_key": "fixture_revenue",
                "baseline_start": "2026-09-01",
                "baseline_end": "2026-09-02",
                "current_start": "2026-09-02",
                "current_end": "2026-09-03",
                "dimensions": [],
                "filters": {},
                "data_complete": True,
            },
        },
    )
    assert legacy.status_code == 409
    assert legacy.json()["error"]["code"] == "SESSION_BUSY"

    # Exactly one user message exists for the replayed key.
    messages = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"]
    assert [m["role"] for m in messages] == ["user"]


def test_three_turns_keep_one_session_and_a_stable_transcript(conversation_env):
    """C01: three questions, refresh, same session, stable order, no rebuilt task."""
    client = conversation_env
    session_id = _new_session(client)

    turn_ids = []
    for index, text in enumerate(["第一次提问", "第二次提问", "第三次提问"], start=1):
        submitted = _submit_turn(
            client, session_id, text=text, key=f"c01-key-{index}", scope=FULL_SCOPE
        )
        assert submitted.status_code == 202, submitted.text
        turn_ids.append(submitted.json()["id"])
        settled = _settle_turn(client, session_id, turn_ids[-1])
        assert settled["status"] == "COMPLETED", settled
        assert settled["analysis_id"], settled

    # Refresh: the transcript is stable and gap-free, alternating user/assistant.
    messages = client.get(
        f"/api/v1/sessions/{session_id}/messages", params={"limit": 50}
    ).json()["items"]
    assert [m["role"] for m in messages] == ["user", "assistant"] * 3
    sequences = [m["sequence"] for m in messages]
    assert sequences == sorted(sequences) == list(range(1, 7))
    assert [m["turn_id"] for m in messages if m["role"] == "user"] == turn_ids
    assistant = [m for m in messages if m["role"] == "assistant"]
    assert all(m["content"]["kind"] == "analysis_result" for m in assistant)
    assert all(m["content"]["analysis_id"] for m in assistant)

    # One analysis per turn: nothing was rebuilt or duplicated.
    analyses = client.get("/api/v1/analyses", params={"limit": 50}).json()["items"]
    assert len(analyses) == 3
    assert {a["id"] for a in analyses} == {a["content"]["analysis_id"] for a in assistant}

    # The session carries the confirmed scope and no active turn after settling.
    detail = client.get(f"/api/v1/sessions/{session_id}").json()
    assert detail["active_turn_id"] is None
    assert detail["context_version"] >= 1
    assert detail["resolved_context"]["metric_key"] == "fixture_revenue"
    assert detail["resolved_context"]["field_origins"]["metric_key"] == "ui"

    listing = client.get("/api/v1/sessions").json()["items"]
    assert [row["id"] for row in listing] == [session_id]


def test_partial_scope_asks_for_clarification_without_running_anything(conversation_env):
    """C02: a missing period is clarified; no analysis is queued."""
    client = conversation_env
    session_id = _new_session(client)

    submitted = _submit_turn(
        client, session_id, text="看下收入", key="clarify-1",
        scope={"metric_key": "fixture_revenue"},
    )
    assert submitted.status_code == 202
    settled = _settle_turn(client, session_id, submitted.json()["id"])
    assert settled["status"] == "WAITING_INPUT", settled
    assert settled["analysis_id"] is None

    messages = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"]
    assert messages[-1]["role"] == "assistant"
    assert messages[-1]["content"]["kind"] == "clarify"
    assert "baseline_start" in messages[-1]["content"]["missing"]

    session_detail = client.get(f"/api/v1/sessions/{session_id}").json()
    assert session_detail["resolved_context"] == {}  # nothing was confirmed
    # The clarification is a draft; the next turn completes the scope.
    follow_up = _submit_turn(
        client, session_id, text="用九月的两天", key="clarify-2",
        scope={
            "baseline_start": "2026-09-01",
            "baseline_end": "2026-09-02",
            "current_start": "2026-09-02",
            "current_end": "2026-09-03",
            "metric_key": "fixture_revenue",
        },
    )
    assert follow_up.status_code == 202, follow_up.text
    settled = _settle_turn(client, session_id, follow_up.json()["id"])
    assert settled["status"] == "COMPLETED", settled


def test_context_version_conflict_is_rejected(conversation_env):
    client = conversation_env
    session_id = _new_session(client)
    stale = _submit_turn(
        client, session_id, text="第一次", key="version-1", scope=FULL_SCOPE, version=0
    )
    assert stale.status_code == 202
    _settle_turn(client, session_id, stale.json()["id"])

    conflict = _submit_turn(
        client, session_id, text="用过期的版本号", key="version-2",
        scope=FULL_SCOPE, version=0,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CONTEXT_VERSION_CONFLICT"


def test_turn_write_is_gated_by_the_flag(client, admin_client):
    """Design 14: the write API is off unless the deployment turns it on.

    The flag is forced off here instead of trusting the ambient default: a host
    `.env` may legitimately have it on (the browser journeys need it).
    """
    client = admin_client
    settings = get_settings()
    previous = settings.conversational_ask_enabled
    settings.conversational_ask_enabled = False
    client.app.dependency_overrides[get_settings_dep] = lambda: settings
    try:
        session_id = _new_session(client)
        response = _submit_turn(
            client, session_id, text="随便问问", key="flag-off-1", scope=FULL_SCOPE
        )
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "CONVERSATIONAL_DISABLED"
    finally:
        client.app.dependency_overrides.pop(get_settings_dep, None)
        settings.conversational_ask_enabled = previous


def test_cancel_settles_the_turn_and_its_analysis(conversation_env):
    """C08 (turn scope): cancelling a turn cancels its unfinished analysis."""
    client = conversation_env
    session_id = _new_session(client)
    submitted = _submit_turn(
        client, session_id, text="会取消的一轮", key="cancel-1", scope=FULL_SCOPE
    )
    assert submitted.status_code == 202
    turn_id = submitted.json()["id"]
    drain_conversations()  # RESOLVING -> ANALYZING, analysis created and queued

    cancelled = client.post(f"/api/v1/sessions/{session_id}/turns/{turn_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "CANCELLED"
    assert cancelled.json()["error"]["code"] == "QUERY_CANCELLED"

    analysis_id = cancelled.json()["analysis_id"]
    assert analysis_id
    detail = client.get(f"/api/v1/analyses/{analysis_id}").json()
    assert detail["status"] == "CANCELLED"

    # Idempotent: cancelling again returns the same terminal turn.
    again = client.post(f"/api/v1/sessions/{session_id}/turns/{turn_id}/cancel")
    assert again.status_code == 200
    assert again.json()["status"] == "CANCELLED"


def test_legacy_messages_read_through_the_compatibility_layer(conversation_env):
    """Design 8.1: rows written before the conversation layer still read."""
    client = conversation_env
    session_id = _new_session(client)
    with session_scope() as session:
        session.add(
            AgentMessage(
                id=uuid.uuid4(),
                session_id=uuid.UUID(session_id),
                role="user",
                content={"question": "老问题", "analysis_id": str(uuid.uuid4())},
                sequence=1,
                content_schema_version=0,
            )
        )
        session.add(
            AgentMessage(
                id=uuid.uuid4(),
                session_id=uuid.UUID(session_id),
                role="assistant",
                content={"analysis_id": str(uuid.uuid4()), "status": "COMPLETED",
                         "report_available": True},
                sequence=2,
                content_schema_version=0,
            )
        )
    messages = client.get(f"/api/v1/sessions/{session_id}/messages").json()["items"]
    assert [m["content_schema_version"] for m in messages] == [0, 0]
    assert messages[0]["content"]["text"] == "老问题"
    assert messages[0]["content"]["schema_version"] == 0
    assert messages[1]["content"]["kind"] == "analysis"
    assert messages[1]["content"]["report_available"] is False
    assert messages[1]["content"]["permission_revoked"] is True  # missing analysis fails closed
