"""Persistence for conversation turns (design 8.2, 8.3, 10).

One row per accepted user input. Status writes go through the explicit
transition table in `app.constants`; the partial unique index
`uq_conversation_turns_active` (created by migration `c4d9a17e6f20`) is what
actually enforces "at most one active turn per session" when two requests race,
so the API's SESSION_BUSY check is a fast path, not the guarantee.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import (
    ACTIVE_CONVERSATION_TURN_STATUSES,
    CONVERSATION_TURN_TRANSITIONS,
    TERMINAL_CONVERSATION_TURN_STATUSES,
    ConversationTurnStatus,
)
from app.agent.state import TERMINAL_ANALYSIS_STATUSES
from app.ids import utcnow
from app.models.orm import AnalysisTask, ConversationTurn


class InvalidTurnTransition(RuntimeError):
    def __init__(self, current: str, target: str) -> None:
        super().__init__(f"invalid conversation turn transition {current} -> {target}")
        self.current = current
        self.target = target


def transition(turn: ConversationTurn, target: str) -> None:
    """Apply the explicit state machine; any other write is a bug."""
    current = ConversationTurnStatus(turn.status)
    target_enum = ConversationTurnStatus(target)
    if target_enum == current:
        return
    if target_enum not in CONVERSATION_TURN_TRANSITIONS.get(current, frozenset()):
        raise InvalidTurnTransition(turn.status, target)
    turn.status = target_enum
    turn.updated_at = utcnow()
    if target_enum in TERMINAL_CONVERSATION_TURN_STATUSES:
        turn.finished_at = utcnow()


def create_turn(
    session: Session,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    user_message_id: uuid.UUID,
    request_hash: str,
    idempotency_key: str,
    base_context_version: int,
    context_before: dict[str, Any],
    deadline_at=None,
) -> ConversationTurn:
    row = ConversationTurn(
        id=uuid.uuid4(),
        session_id=session_id,
        user_id=user_id,
        user_message_id=user_message_id,
        status=ConversationTurnStatus.QUEUED,
        request_hash=request_hash,
        idempotency_key=idempotency_key,
        base_context_version=base_context_version,
        context_before=context_before,
        deadline_at=deadline_at,
    )
    session.add(row)
    session.flush()
    return row


def get_turn(session: Session, turn_id: uuid.UUID) -> ConversationTurn | None:
    return session.get(ConversationTurn, turn_id)


def get_turn_for_update(session: Session, turn_id: uuid.UUID) -> ConversationTurn | None:
    return session.execute(
        select(ConversationTurn).where(ConversationTurn.id == turn_id).with_for_update()
    ).scalar_one_or_none()


def find_by_idempotency(
    session: Session, *, session_id: uuid.UUID, idempotency_key: str
) -> ConversationTurn | None:
    return session.execute(
        select(ConversationTurn).where(
            ConversationTurn.session_id == session_id,
            ConversationTurn.idempotency_key == idempotency_key,
        )
    ).scalar_one_or_none()


def active_turn(session: Session, session_id: uuid.UUID) -> ConversationTurn | None:
    """The one active turn of a session, if any (design 9: SESSION_BUSY)."""
    return session.execute(
        select(ConversationTurn).where(
            ConversationTurn.session_id == session_id,
            ConversationTurn.status.in_([str(s) for s in ACTIVE_CONVERSATION_TURN_STATUSES]),
        )
    ).scalar_one_or_none()


def analysis_for_turn(session: Session, turn_id: uuid.UUID) -> AnalysisTask | None:
    return session.execute(
        select(AnalysisTask).where(AnalysisTask.conversation_turn_id == turn_id)
    ).scalar_one_or_none()


def list_awaiting_analysis(session: Session, *, limit: int = 200) -> list[uuid.UUID]:
    """Turns stuck in ANALYZING whose analysis settled (or never appeared).

    The orchestrator does not poll: it leaves the turn in ANALYZING and this
    reconciler query closes it once the analysis is terminal, so a worker never
    occupies a lease while waiting (design 10.1).
    """
    rows = session.execute(
        select(ConversationTurn.id, AnalysisTask.id, AnalysisTask.status)
        .outerjoin(AnalysisTask, AnalysisTask.conversation_turn_id == ConversationTurn.id)
        .where(ConversationTurn.status == ConversationTurnStatus.ANALYZING)
        .order_by(ConversationTurn.created_at, ConversationTurn.id)
        .limit(limit)
    ).all()
    return [
        turn_id
        for turn_id, analysis_id, analysis_status in rows
        if analysis_id is None or analysis_status in TERMINAL_ANALYSIS_STATUSES
    ]


def latest_turn(session: Session, session_id: uuid.UUID) -> ConversationTurn | None:
    return session.execute(
        select(ConversationTurn)
        .where(ConversationTurn.session_id == session_id)
        .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def set_intent(session: Session, turn: ConversationTurn, intent: str) -> None:
    turn.intent = intent
    turn.updated_at = utcnow()
    session.flush()


def finish(
    session: Session,
    turn: ConversationTurn,
    *,
    status: str,
    assistant_message_id: uuid.UUID | None = None,
    context_after: dict[str, Any] | None = None,
    skill_snapshot: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
) -> None:
    """Move the turn to a terminal status and record what it produced."""
    transition(turn, status)
    if assistant_message_id is not None:
        turn.assistant_message_id = assistant_message_id
    if context_after is not None:
        turn.context_after = context_after
    if skill_snapshot is not None:
        turn.skill_snapshot = skill_snapshot
    if error is not None:
        turn.error = error
    if checkpoint is not None:
        turn.checkpoint_version = int(checkpoint.get("version", turn.checkpoint_version))
    session.flush()


def save_checkpoint(session: Session, turn: ConversationTurn, checkpoint: dict[str, Any]) -> None:
    turn.checkpoint_version = int(turn.checkpoint_version) + 1
    turn.updated_at = utcnow()
    session.flush()


def turn_statuses(session, turn_ids):
    if not turn_ids:
        return {}
    return dict(session.execute(
        select(ConversationTurn.id, ConversationTurn.status).where(ConversationTurn.id.in_(turn_ids))
    ).all())
