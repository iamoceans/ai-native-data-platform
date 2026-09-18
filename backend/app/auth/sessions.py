"""Session and CSRF token handling (spec section 22).

The raw session token is only ever held by the client cookie; the database
stores hashes. CSRF tokens are returned in the login response and validated
against the session on every unsafe method.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from app.config import Settings
from app.ids import new_token, token_hash, utcnow
from app.models.orm import AuthSession, Role, User
from app.repositories import users as users_repo


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: AuthSession
    roles: list[Role]
    capabilities: frozenset[str]

    @property
    def role_ids(self) -> list[uuid.UUID]:
        return [role.id for role in self.roles]

    @property
    def role_names(self) -> list[str]:
        return [role.name for role in self.roles]


def issue_session(
    session: Session, user: User, settings: Settings
) -> tuple[AuthSession, str, str]:
    token = new_token()
    csrf_token = new_token()
    expires_at = utcnow() + timedelta(hours=settings.session_ttl_hours)
    row = users_repo.create_session(
        session,
        user_id=user.id,
        token_hash=token_hash(token),
        csrf_hash=token_hash(csrf_token),
        expires_at=expires_at,
    )
    return row, token, csrf_token


def validate_token(session: Session, token: str) -> AuthSession | None:
    row = users_repo.get_session_by_token_hash(session, token_hash(token))
    if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
        return None
    return row


def validate_csrf(session_row: AuthSession, csrf_token: str) -> bool:
    return token_hash(csrf_token) == session_row.csrf_hash
