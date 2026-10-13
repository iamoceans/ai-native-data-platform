"""Regression coverage for the 2026-10-14 review: failure, grants and scope."""
from __future__ import annotations

import uuid
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest
from fastapi import Response
from starlette.requests import Request

from app.agent import runner
from app.agent.conversation import orchestrator
from app.agent.conversation.explain import explanation_message
from app.api.dto import AnalysisCreate, SessionPatch
from app.api.routes import analyses, conversations
from app.config import Settings
from app.constants import ErrorCode
from app.errors import ApiError
from app.ids import utcnow


@pytest.fixture(autouse=True)
def no_active_analysis(monkeypatch):
    monkeypatch.setattr(analyses.analyses_repo, "active_analysis", lambda *args: None, raising=False)


def task_for(user_id=None, session_id=None):
    return NS(id=uuid.uuid4(), user_id=user_id or uuid.uuid4(),
              session_id=session_id or uuid.uuid4(), parent_id=None,
              status="COMPLETED", question="Revenue change?", context={}, plan={}, budget={},
              state={"observations": {"revenue": "100"}}, checkpoint_version=0,
              policy_revision=1, final_report={"claims": [{"text": "Revenue 100"}]},
              created_at=utcnow(), finished_at=utcnow())


def auth_for(user_id):
    return NS(user=NS(id=user_id), role_ids=[uuid.uuid4()], capabilities={"analysis.create"})


@pytest.mark.parametrize("failure,code", [(ValueError("broken metric"), ErrorCode.ANALYSIS_EXECUTION_ERROR),
                                          (runner.LLMUnavailable("offline"), ErrorCode.LLM_UNAVAILABLE)])
def test_analysis_failure_is_published_and_claim_released(monkeypatch, failure, code):
    task = task_for()
    task.status = "CREATED"
    monkeypatch.setattr(runner.analyses_repo, "get_analysis_for_update", lambda *args: task)
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(runner, "_prepare", fail)
    event = Mock()
    release = Mock()
    monkeypatch.setattr(runner, "_event", event)
    monkeypatch.setattr(runner.queue_repo, "release_analysis_claim", release)
    assert runner.execute_claim(None, claim=NS(analysis_id=task.id), settings=Settings())
    assert task.status == "FAILED"
    assert task.state["last_error"]["code"] == code
    assert task.finished_at is not None
    release.assert_called_once_with(None, NS(analysis_id=task.id), terminal=True, failed=True)
    assert event.call_args.args[2] == "analysis.failed"


@pytest.mark.parametrize("endpoint", ["detail", "list", "cancel"])
def test_every_analysis_detail_surface_redacts_revoked_results(monkeypatch, endpoint):
    task = task_for()
    auth = auth_for(task.user_id)
    monkeypatch.setattr(analyses.analyses_repo, "get_analysis", lambda *args: task)
    monkeypatch.setattr(analyses.analyses_repo, "list_for_user", lambda *args, **kwargs: ([task], 1))
    monkeypatch.setattr(analyses.analyses_repo, "list_steps", lambda *args: [
        NS(step_key="compare", status="COMPLETED", tool_name="kernel",
           observation={"revenue": "100"}, error=None)])
    monkeypatch.setattr(analyses.analyses_repo, "analysis_dataset_actions_ok", lambda *args, **kw: False)
    if endpoint == "detail":
        result = analyses.get_analysis(task.id, auth=auth, session=None)
    elif endpoint == "cancel":
        result = analyses.cancel_analysis(task.id, auth=auth, session=None)
    else:
        result = analyses.list_analyses(auth=auth, session=None, settings=Settings(),
                                       cursor=None, status=None, limit=20).items[0]
    assert result.status == "COMPLETED"
    assert result.final_report is None
    assert result.steps == []
    assert result.state == {"permission_revoked": True}
    assert "Revenue 100" not in result.model_dump_json()
    assert task.final_report["claims"][0]["text"] == "Revenue 100"  # stored evidence unchanged


def test_explanation_rechecks_all_quote_sources_after_revocation(monkeypatch):
    owner = uuid.uuid4()
    first, second = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(conversations.analyses_repo, "get_analysis", lambda *args: NS(user_id=owner))
    checked = []
    def permitted(*args, analysis_id, **kwargs):
        checked.append(analysis_id)
        return analysis_id != second
    monkeypatch.setattr(conversations.users_repo, "role_ids_for_user", lambda *args: [])
    monkeypatch.setattr(conversations.analyses_repo, "analysis_dataset_actions_ok", permitted)
    content = explanation_message(prose="Evidence summary", quotes=[
        {"analysis_id": str(first), "text": "Revenue 100"},
        {"analysis_id": str(second), "text": "Revenue 90"}], dropped=[], limitations=["Revenue incomplete"])
    result = conversations._redact_if_revoked(None, auth_for(owner), content)
    assert result["quotes"] == []
    assert result["limitations"] == []
    assert result["permission_revoked"] is True
    assert result["text"] == ""
    assert set(checked) == {first, second}
    assert len(content["quotes"]) == 2  # redaction never rewrites persisted messages


def test_recent_analyses_and_history_exclude_revoked_evidence(monkeypatch):
    task = task_for()
    monkeypatch.setattr(orchestrator.analyses_repo, "get_analysis", lambda *args: task)
    turn = NS(user_id=task.user_id, session_id=task.session_id)
    monkeypatch.setattr(orchestrator.analyses_repo, "list_for_user", lambda *args, **kw: ([task], 1))
    monkeypatch.setattr(orchestrator.users_repo, "role_ids_for_user", lambda *args: [])
    monkeypatch.setattr(orchestrator.analyses_repo, "analysis_dataset_actions_ok", lambda *args, **kw: False)
    content = {"kind": "explanation", "text": "Revenue 100",
               "quotes": [{"analysis_id": str(task.id), "text": "Revenue 100"}]}
    monkeypatch.setattr(orchestrator.analyses_repo, "list_messages", lambda *args, **kw: ([NS(role="assistant", content=content, content_schema_version=1)], 1))
    assert orchestrator._recent_analyses(None, turn) == []
    assert "Revenue 100" not in str(orchestrator._history(None, turn))


def test_ui_scope_values_and_clear_override_model_proposals():
    merged, _ = orchestrator.resolve_scope_patch(
        {"metric_key": "old", "filters": {"platform": "ios"}},
        {"scope_patch": {"metric_key": "user", "clear": ["filters"]}},
        {"metric_key": "model", "filters": {"platform": "android"}, "dimensions": ["country"]},
    )
    assert merged["metric_key"] == "user"
    assert merged["field_origins"]["metric_key"] == "ui"
    assert "filters" not in merged
    assert merged["dimensions"] == ["country"]


def test_legacy_submit_checks_busy_after_locking_session(monkeypatch):
    user_id, session_id = uuid.uuid4(), uuid.uuid4()
    locked = False
    def lock(*args):
        nonlocal locked
        locked = True
        return NS(user_id=user_id, id=session_id, archived_at=None)
    monkeypatch.setattr(analyses.analyses_repo, "get_session", lambda *args: NS(user_id=user_id))
    monkeypatch.setattr(analyses.analyses_repo, "get_session_for_update", lock)
    monkeypatch.setattr(conversations.conversation_repo, "active_turn", lambda *args:
                        NS(id=uuid.uuid4(), status="QUEUED") if locked else None)
    def unexpected(*args, **kwargs):
        pytest.fail("legacy submission escaped busy protection before acquiring the session lock")
    monkeypatch.setattr(analyses.policy_repo, "get_revision", lambda *args: 1)
    monkeypatch.setattr(analyses.analyses_repo, "create_analysis", unexpected)
    payload = AnalysisCreate(session_id=session_id, question="why", context={
        "metric_key": "revenue", "baseline_start": "2026-09-01", "baseline_end": "2026-09-02",
        "current_start": "2026-09-02", "current_end": "2026-09-03"})
    with pytest.raises(ApiError) as caught:
        analyses.create_analysis(payload, Request({"type": "http"}), Response(),
                                 auth=auth_for(user_id), session=None, settings=Settings(), idempotency_key=None)
    assert caught.value.code == ErrorCode.SESSION_BUSY


def test_patch_uses_current_version_after_acquiring_lock(monkeypatch):
    user_id, session_id = uuid.uuid4(), uuid.uuid4()
    stale = NS(id=session_id, user_id=user_id, context_version=1)
    current = NS(id=session_id, user_id=user_id, context_version=2)
    monkeypatch.setattr(conversations.analyses_repo, "get_session", lambda *args: stale)
    monkeypatch.setattr(conversations.analyses_repo, "get_session_for_update", lambda *args: current)
    monkeypatch.setattr(conversations.conversation_repo, "active_turn", lambda *args: None)
    monkeypatch.setattr(conversations.analyses_repo, "update_session_scope",
                        lambda *args, **kw: pytest.fail("stale patch reached a write"))
    with pytest.raises(ApiError) as caught:
        conversations.patch_session(session_id, SessionPatch(expected_context_version=1, skill_config={"mode": "off"}),
            Request({"type": "http"}), auth=auth_for(user_id), session=None)
    assert caught.value.code == ErrorCode.CONTEXT_VERSION_CONFLICT


def test_authorized_detail_keeps_report_and_observations(monkeypatch):
    task = task_for()
    monkeypatch.setattr(analyses.analyses_repo, "get_analysis", lambda *args: task)
    monkeypatch.setattr(analyses.analyses_repo, "list_steps", lambda *args: [])
    monkeypatch.setattr(analyses.analyses_repo, "analysis_dataset_actions_ok", lambda *args, **kw: True)
    result = analyses.get_analysis(task.id, auth=auth_for(task.user_id), session=None)
    assert result.final_report == task.final_report
    assert result.state == task.state


@pytest.mark.parametrize("revoked_action", ["query", "discover"])
def test_evidence_access_requires_both_dataset_grants(monkeypatch, revoked_action):
    from app.repositories import datasets
    analysis_id, dataset_id = uuid.uuid4(), uuid.uuid4()
    monkeypatch.setattr(analyses.analyses_repo, "list_queries", lambda *args: [NS(id=uuid.uuid4())])
    monkeypatch.setattr(analyses.analyses_repo, "list_dependencies", lambda *args: [NS(dataset_id=dataset_id)])
    monkeypatch.setattr(datasets, "has_dataset_action", lambda db, roles, id, action: action != revoked_action)
    assert not analyses.analyses_repo.analysis_dataset_actions_ok(None, role_ids=[], analysis_id=analysis_id)


@pytest.mark.parametrize("content", [
    {"kind": "explanation", "quotes": [{"text": "Revenue 100"}]},
    {"kind": "explanation", "quotes": [{"analysis_id": "invalid", "text": "Revenue 100"}]},
])
def test_malformed_evidence_references_fail_closed(content):
    redacted = conversations._redact_if_revoked(None, auth_for(uuid.uuid4()), content)
    assert redacted["quotes"] == []
    assert redacted["permission_revoked"]


def test_chart_drilldown_checks_session_busy_before_creating_child(monkeypatch):
    from app.api.routes import charts
    from app.api.dto import ChartDrilldownRequest
    parent = task_for()
    monkeypatch.setattr(charts, "_chart_for", lambda *args: ({"spec": {}}, parent))
    monkeypatch.setattr(charts.analyses_repo, "get_session_for_update", lambda *args:
                        NS(id=parent.session_id, user_id=parent.user_id, archived_at=None))
    monkeypatch.setattr(conversations.conversation_repo, "active_turn", lambda *args:
                        NS(id=uuid.uuid4(), status="RESOLVING"))
    monkeypatch.setattr(charts.policy_repo, "get_revision", lambda *args: 1)
    monkeypatch.setattr(charts.analyses_repo, "create_analysis", lambda *args, **kw:
                        pytest.fail("chart drilldown bypassed the active session turn"))
    with pytest.raises(ApiError) as caught:
        charts.drilldown(uuid.uuid4(), ChartDrilldownRequest(dimension="country", value="US"),
                         Request({"type": "http"}), Response(), auth=auth_for(parent.user_id),
                         session=None, settings=Settings())
    assert caught.value.code == ErrorCode.SESSION_BUSY


@pytest.mark.parametrize("endpoint", ["turn", "legacy", "patch", "chart"])
def test_active_legacy_analysis_blocks_other_session_writes(monkeypatch, endpoint):
    from app.api.routes import charts
    from app.api.dto import ChartDrilldownRequest, TurnCreate
    task = task_for()
    task.status = "EXECUTING"
    row = NS(id=task.session_id, user_id=task.user_id, archived_at=None, context_version=0)
    monkeypatch.setattr(analyses.analyses_repo, "get_session", lambda *args: row)
    monkeypatch.setattr(analyses.analyses_repo, "get_session_for_update", lambda *args: row)
    monkeypatch.setattr(analyses.analyses_repo, "active_analysis", lambda *args: task)
    monkeypatch.setattr(conversations.conversation_repo, "active_turn", lambda *args: None)
    monkeypatch.setattr(conversations.conversation_repo, "find_by_idempotency", lambda *args, **kw: None)
    monkeypatch.setattr(analyses.policy_repo, "get_revision", lambda *args: 1)
    monkeypatch.setattr(charts, "_chart_for", lambda *args: ({"spec": {}}, task))
    def unexpected(*args, **kwargs):
        pytest.fail("write bypassed an active legacy analysis")
    monkeypatch.setattr(analyses.analyses_repo, "create_analysis", unexpected)
    monkeypatch.setattr(analyses.analyses_repo, "add_message", unexpected)
    monkeypatch.setattr(analyses.analyses_repo, "update_session_scope", unexpected)
    auth = auth_for(task.user_id)
    request = Request({"type": "http"})
    settings = Settings(conversational_ask_enabled=True)
    with pytest.raises(ApiError) as caught:
        if endpoint == "turn":
            conversations.create_turn(task.session_id, TurnCreate(text="follow up"), request,
                                      auth=auth, session=None, settings=settings, idempotency_key="new-turn")
        elif endpoint == "patch":
            conversations.patch_session(task.session_id, SessionPatch(skill_config={"mode": "off"}),
                                        request, auth=auth, session=None)
        elif endpoint == "chart":
            charts.drilldown(uuid.uuid4(), ChartDrilldownRequest(dimension="country", value="US"),
                             request, Response(), auth=auth, session=None, settings=settings)
        else:
            payload = AnalysisCreate(session_id=task.session_id, question="why", context={
                "metric_key": "revenue", "baseline_start": "2026-09-01", "baseline_end": "2026-09-02",
                "current_start": "2026-09-02", "current_end": "2026-09-03"})
            analyses.create_analysis(payload, request, Response(), auth=auth, session=None,
                                     settings=settings, idempotency_key=None)
    assert caught.value.code == ErrorCode.SESSION_BUSY
    assert caught.value.details["analysis_id"] == str(task.id)


def test_explanation_missing_source_is_not_treated_as_public(monkeypatch):
    monkeypatch.setattr(conversations.analyses_repo, "get_analysis", lambda *args: None)
    monkeypatch.setattr(conversations.users_repo, "role_ids_for_user", lambda *args: [])
    monkeypatch.setattr(conversations.analyses_repo, "analysis_dataset_actions_ok", lambda *args, **kw: True)
    result = conversations._redact_if_revoked(None, auth_for(uuid.uuid4()),
        explanation_message(prose="Evidence", quotes=[{"analysis_id": str(uuid.uuid4()), "text": "Revenue 100"}], dropped=[]))
    assert result["quotes"] == []
    assert result["permission_revoked"]


def test_model_memory_context_excludes_revoked_source_evidence(monkeypatch):
    from app.agent import memory

    row = NS(id=uuid.uuid4(), kind="segment", status="confirmed", statement="Private segment decline",
             scope_dimensions=[], analysis_id=uuid.uuid4())
    monkeypatch.setattr(memory.memory_repo, "list_for_metric", lambda *args, **kw: [row])
    monkeypatch.setattr(memory.memory_repo, "memory_dataset_actions_ok", lambda *args, **kw: False,
                        raising=False)
    assert orchestrator._memory_brief(None, "revenue") == []
    assert memory.planner_brief(None, metric_key="revenue", allowed_dimensions=[]) == ([], [])


def test_memory_without_registered_source_evidence_is_not_prompt_context():
    from app.repositories import memory

    assert not memory.memory_dataset_actions_ok(None, role_ids=[], row=NS(analysis_id=None))


@pytest.mark.parametrize("grants", [{"query"}, {"discover"}, {"query", "discover"}])
def test_memory_prompt_context_uses_current_source_dataset_grants(monkeypatch, grants):
    from app.agent import memory as planner_memory
    from app.repositories import datasets, memory

    role_id, dataset_id, analysis_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    row = NS(id=uuid.uuid4(), analysis_id=analysis_id, kind="segment", status="confirmed",
             statement="Shared segment observation", scope_dimensions=[])
    monkeypatch.setattr(memory, "list_for_metric", lambda *args, **kw: [row])
    monkeypatch.setattr(analyses.analyses_repo, "get_analysis", lambda *args: task_for())
    monkeypatch.setattr(analyses.analyses_repo, "list_queries", lambda *args: [NS(id=uuid.uuid4())])
    monkeypatch.setattr(analyses.analyses_repo, "list_dependencies", lambda *args: [NS(dataset_id=dataset_id)])
    monkeypatch.setattr(datasets, "has_dataset_action", lambda db, roles, id, action:
                        roles == [role_id] and id == dataset_id and action in grants)
    conversation_context = orchestrator._memory_brief(None, "revenue", role_ids=[role_id])
    planner_context, ids = planner_memory.planner_brief(
        None, metric_key="revenue", allowed_dimensions=[], role_ids=[role_id]
    )
    permitted = grants == {"query", "discover"}
    assert bool(conversation_context) == permitted
    assert bool(planner_context) == permitted
    assert ids == ([row.id] if permitted else [])


def test_memory_source_without_dependencies_fails_closed(monkeypatch):
    from app.repositories import memory

    monkeypatch.setattr(analyses.analyses_repo, "get_analysis", lambda *args: task_for())
    monkeypatch.setattr(analyses.analyses_repo, "list_queries", lambda *args: [])
    monkeypatch.setattr(analyses.analyses_repo, "list_dependencies", lambda *args: [])
    assert not memory.memory_dataset_actions_ok(None, role_ids=[], row=NS(analysis_id=uuid.uuid4()))
