"""Turn budget: conservative reservation, UTC deadline, sub-budget remainder."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.agent.budget import BudgetExceeded
from app.agent.conversation.budget import TurnBudget
from app.config import get_settings
from app.ids import utcnow
from app.models.orm import ConversationTurn


def make_turn(**overrides) -> ConversationTurn:
    turn = ConversationTurn(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        user_message_id=uuid.uuid4(),
        status="RESOLVING",
        request_hash="h",
        idempotency_key="k",
        base_context_version=0,
        context_before={},
        skill_snapshot={},
        budget={},
        checkpoint_version=0,
    )
    for key, value in overrides.items():
        setattr(turn, key, value)
    return turn


def test_reservation_is_conservative_and_released_on_record():
    budget = TurnBudget.start(get_settings())
    budget.reserve_model_call(estimated_input=1_000, max_output=500)
    assert budget.reserved_input == 1_000
    # A second reservation is checked against the first one still in flight.
    budget.reserve_model_call(estimated_input=1_000, max_output=500)
    assert budget.reserved_input == 2_000
    budget.record_model_usage(input_tokens=900, output_tokens=100)
    assert budget.reserved_input == 0 and budget.reserved_output == 0
    assert budget.input_tokens == 900 and budget.model_calls == 1


def test_reservation_refuses_when_the_allowance_is_gone():
    budget = TurnBudget.start(get_settings())
    budget.reserve_model_call(estimated_input=budget.max_input_tokens, max_output=0)
    with pytest.raises(BudgetExceeded):
        budget.reserve_model_call(estimated_input=1, max_output=0)


def test_deadline_is_absolute_utc_and_survives_a_restart():
    settings = get_settings()
    deadline = utcnow() + timedelta(seconds=42)
    turn = make_turn(deadline_at=deadline, budget=TurnBudget.start(settings, deadline_at=deadline).as_dict())
    restored = TurnBudget.from_turn(turn, settings)
    assert restored.deadline_at == deadline
    assert 1 <= restored.remaining_seconds() <= 42

    # An in-flight reservation is not handed out again after a restart.
    spent = TurnBudget.start(settings, deadline_at=deadline)
    spent.reserve_model_call(estimated_input=5_000, max_output=500)
    turn.budget = spent.as_dict()
    again = TurnBudget.from_turn(turn, settings)
    assert again.reserved_input == 5_000
    assert again.analysis_sub_budget(settings)["max_input_tokens"] <= (
        settings.agent_max_input_tokens - 5_000
    )


def test_expired_turn_budget_fails_time():
    settings = get_settings()
    turn = make_turn(
        deadline_at=utcnow() - timedelta(seconds=1),
        budget=TurnBudget.start(settings, deadline_at=utcnow() - timedelta(seconds=1)).as_dict(),
    )
    with pytest.raises(BudgetExceeded):
        TurnBudget.from_turn(turn, settings).ensure_time()


def test_analysis_sub_budget_is_the_remainder_not_a_fresh_allowance():
    settings = get_settings()
    budget = TurnBudget.start(settings)
    budget.record_model_usage(input_tokens=10_000, output_tokens=2_000)
    sub = budget.analysis_sub_budget(settings)
    assert sub["max_input_tokens"] == settings.agent_max_input_tokens - 10_000
    assert sub["max_output_tokens"] == settings.agent_max_output_tokens - 2_000
    assert sub["max_wall_seconds"] <= settings.agent_max_wall_seconds
    assert sub["input_tokens"] == 0  # the analysis starts from zero *within* its allowance
