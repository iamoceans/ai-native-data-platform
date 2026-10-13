"""Conversation turn state machine and the message compatibility reader."""

from __future__ import annotations

import uuid

import pytest

from app.constants import (
    ACTIVE_CONVERSATION_TURN_STATUSES,
    CONVERSATION_TURN_TRANSITIONS,
    TERMINAL_CONVERSATION_TURN_STATUSES,
    ConversationTurnStatus,
)
from app.models.orm import AgentMessage, ConversationTurn
from app.repositories import analyses as analyses_repo
from app.repositories import conversation as conversation_repo


def make_turn(status: str) -> ConversationTurn:
    return ConversationTurn(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        user_message_id=uuid.uuid4(),
        status=status,
        request_hash="hash",
        idempotency_key="key",
        base_context_version=0,
        context_before={},
        skill_snapshot={},
        budget={},
        checkpoint_version=0,
    )


@pytest.mark.parametrize(
    "current,target,allowed",
    [
        ("QUEUED", "RESOLVING", True),
        ("QUEUED", "CANCELLED", True),
        ("QUEUED", "ANALYZING", False),
        ("RESOLVING", "WAITING_INPUT", True),
        ("RESOLVING", "ANALYZING", True),
        ("RESOLVING", "RESPONDING", True),
        ("RESOLVING", "COMPLETED", False),
        ("ANALYZING", "RESPONDING", True),
        ("ANALYZING", "COMPLETED", False),
        ("ANALYZING", "FAILED", True),
        ("RESPONDING", "COMPLETED", True),
        ("RESPONDING", "WAITING_INPUT", False),
        ("COMPLETED", "FAILED", False),
        ("FAILED", "QUEUED", False),
        ("CANCELLED", "RESOLVING", False),
        ("WAITING_INPUT", "ANALYZING", False),
    ],
)
def test_turn_transitions(current, target, allowed):
    turn = make_turn(current)
    if allowed:
        conversation_repo.transition(turn, target)
        assert turn.status == target
    else:
        with pytest.raises(conversation_repo.InvalidTurnTransition):
            conversation_repo.transition(turn, target)
        assert turn.status == current


def test_terminal_statuses_finish_the_turn():
    paths = {
        "WAITING_INPUT": "RESOLVING",
        "COMPLETED": "RESPONDING",
        "FAILED": "QUEUED",
        "CANCELLED": "QUEUED",
    }
    for status, start in paths.items():
        turn = make_turn(start)
        conversation_repo.transition(turn, status)
        assert turn.status == status
        assert turn.finished_at is not None


def test_active_and_terminal_sets_are_disjoint_and_cover_the_enum():
    active = {str(s) for s in ACTIVE_CONVERSATION_TURN_STATUSES}
    terminal = {str(s) for s in TERMINAL_CONVERSATION_TURN_STATUSES}
    assert active & terminal == set()
    assert active | terminal == {str(s) for s in ConversationTurnStatus}


def test_every_transition_target_is_a_known_status():
    for current, targets in CONVERSATION_TURN_TRANSITIONS.items():
        assert current in ConversationTurnStatus
        for target in targets:
            assert target in ConversationTurnStatus


def test_legacy_user_message_is_converted_without_inventing_content():
    row = AgentMessage(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        role="user",
        content={"question": "Why did revenue drop?", "analysis_id": str(uuid.uuid4())},
        sequence=1,
        content_schema_version=0,
    )
    converted = analyses_repo.message_content(row)
    assert converted["schema_version"] == 0
    assert converted["text"] == "Why did revenue drop?"
    assert converted["analysis_id"] == row.content["analysis_id"]


def test_legacy_assistant_pointer_is_converted():
    analysis_id = str(uuid.uuid4())
    row = AgentMessage(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        role="assistant",
        content={"analysis_id": analysis_id, "status": "COMPLETED", "report_available": True},
        sequence=2,
        content_schema_version=0,
    )
    converted = analyses_repo.message_content(row)
    assert converted == {
        "schema_version": 0,
        "kind": "analysis",
        "analysis_id": analysis_id,
        "status": "COMPLETED",
        "report_available": True,
    }


def test_current_messages_pass_through_unchanged():
    content = {"schema_version": 1, "kind": "user", "text": "只看安卓", "scope_patch": None}
    row = AgentMessage(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        role="user",
        content=content,
        sequence=3,
        content_schema_version=1,
    )
    assert analyses_repo.message_content(row) == content
