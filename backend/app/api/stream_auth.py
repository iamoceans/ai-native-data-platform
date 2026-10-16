"""Read-time authentication checks for streams across API replicas."""
import uuid
from sqlalchemy import select, func
from sqlalchemy.orm import Session
from app.models.orm import AuthSession, User


def stream_session_active(session: Session, auth_session_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return session.scalar(select(AuthSession.id).join(User, User.id==AuthSession.user_id).where(
        AuthSession.id==auth_session_id, AuthSession.user_id==user_id,
        AuthSession.revoked_at.is_(None), AuthSession.expires_at>func.now(), User.active.is_(True),
    )) is not None
