"""Lost model attempts cannot refill budgets or overwrite a newer resolution."""
from dataclasses import replace
from datetime import date
from types import SimpleNamespace as NS
from unittest.mock import MagicMock
import uuid

import httpx
import pytest

from app.agent.budget import BudgetExceeded
from app.agent.conversation import intent, orchestrator
from app.agent.conversation.budget import TurnBudget
from app.agent.llm import ProviderResolution
from app.agent.runner import LLMUnavailable, _call_provider
from app.config import Settings


def preparation(**changes):
    return orchestrator.TurnPreparation(
        turn_id=uuid.uuid4(), session_id=uuid.uuid4(), user_id=uuid.uuid4(),
        question="compare", context_before={}, user_scope_patch=None, user_skill_selection=None,
        metrics={"revenue": {"country"}}, messages=[], skill_block="",
        skill_snapshot={"mode": "off"}, metric_hint=None, degraded=None,
        checkpoint_version=1, **changes,
    )


@pytest.mark.parametrize("status,version", [("RESOLVING", 2), ("ANALYZING", 1), ("CANCELLED", 1)])
def test_old_success_and_failure_cannot_mutate_current_turn(monkeypatch, status, version):
    prep = preparation()
    turn = NS(status=status, checkpoint_version=version)
    monkeypatch.setattr(orchestrator.conversation_repo, "get_turn_for_update", lambda *args: turn)
    publish = MagicMock()
    monkeypatch.setattr(orchestrator, "_fail", publish)
    budget = TurnBudget.start(Settings(_env_file=None))
    assert orchestrator._apply_turn(None, prep, None, settings=Settings(_env_file=None), budget=budget) == status
    assert orchestrator._fail_turn(MagicMock(), prep.turn_id, code="EXECUTION_ERROR", message="old error", checkpoint_version=1) == status
    publish.assert_not_called()


@pytest.mark.parametrize("start", ["not-a-date", "2026-99-01", "9999-12-31"])
def test_invalid_relative_dates_are_validated_without_crashing(start):
    decision = intent.ConversationDecision(intent="analyze", scope_patch={
        "comparison": "previous_period", "current_start": start, "current_end": "2026-09-15",
    })
    assert intent.resolve_relative_periods(decision, now=date(2026, 9, 15)) == []
    patch, reasons = intent.validate_patch(decision, metrics={})
    assert "current_start" not in patch and "invalid_date:current_start" in reasons


def test_invalid_model_date_does_not_reuse_confirmed_scope_but_explicit_ui_wins(monkeypatch):
    base = {"metric_key": "revenue", "baseline_start": "2026-09-01", "baseline_end": "2026-09-02",
            "current_start": "2026-09-02", "current_end": "2026-09-03"}
    prep = replace(preparation(), context_before=base, messages=[{"role": "user", "content": "compare"}])
    monkeypatch.setattr(orchestrator, "resolve_provider", lambda *args: ProviderResolution(provider=object()))
    monkeypatch.setattr(intent, "call_model", lambda *args, **kwargs: intent.ConversationDecision(
        intent="analyze", scope_patch={"comparison": "previous_period", "current_start": "invalid", "current_end": "2026-09-15"}))
    resolved = orchestrator._decide_turn(prep, settings=Settings(_env_file=None), budget=TurnBudget.start(Settings(_env_file=None)))
    _, missing = orchestrator.resolve_scope_patch(base, {}, resolved.model_patch)
    assert "current_start" in missing
    merged, missing = orchestrator.resolve_scope_patch(base, {"scope_patch": {"current_start": "2026-09-14"}}, resolved.model_patch)
    assert merged["current_start"] == "2026-09-14" and "current_start" not in missing


@pytest.mark.parametrize("path", ["intent", "analysis"])
def test_provider_failures_do_not_echo_arbitrary_exception_text(path):
    provider = NS(generate_structured=MagicMock(side_effect=RuntimeError("Bearer fixture-secret-only")))
    budget = TurnBudget.start(Settings(_env_file=None))
    error_type = intent.IntentUnavailable if path == "intent" else LLMUnavailable
    with pytest.raises(error_type) as error:
        if path == "intent":
            intent.call_model(provider, messages=[], budget=budget)
        else:
            _call_provider(provider, task=None, messages=[], schema=intent.ConversationDecision,
                           budget=budget, max_output_tokens=100)
    assert "RuntimeError" in str(error.value) and "fixture-secret-only" not in str(error.value)


def test_provider_http_failure_keeps_status_without_response_body():
    request = httpx.Request("POST", "https://fixture.invalid/v1/chat/completions")
    response = httpx.Response(401, request=request, text="fixture-secret-only")
    provider = NS(generate_structured=MagicMock(side_effect=httpx.HTTPStatusError(
        "fixture-secret-only", request=request, response=response)))
    with pytest.raises(intent.IntentUnavailable, match="HTTP 401") as error:
        intent.call_model(provider, messages=[], budget=TurnBudget.start(Settings(_env_file=None)))
    assert "fixture-secret-only" not in str(error.value)


def test_budget_refusal_retains_its_typed_failure():
    provider = NS(generate_structured=MagicMock(side_effect=BudgetExceeded("input_tokens")))
    with pytest.raises(BudgetExceeded, match="input_tokens"):
        intent.call_model(provider, messages=[], budget=TurnBudget.start(Settings(_env_file=None)))
