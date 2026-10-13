"""Session busy checks shared by conversational and legacy write entry points."""
from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.constants import ErrorCode
from app.errors import ApiError
from app.repositories import analyses as analyses_repo
from app.repositories import conversation as conversation_repo


def ensure_session_idle(session: Session, session_id: uuid.UUID) -> None:
    """The caller must hold the session row lock until its write commits."""
    turn = conversation_repo.active_turn(session, session_id)
    if turn is not None:
        raise ApiError(ErrorCode.SESSION_BUSY, "a conversation turn is active in this session",
                       details={"turn_id": str(turn.id), "status": str(turn.status)})
    analysis = analyses_repo.active_analysis(session, session_id)
    if analysis is not None:
        raise ApiError(ErrorCode.SESSION_BUSY, "an analysis is active in this session",
                       details={"analysis_id": str(analysis.id), "status": str(analysis.status)})
