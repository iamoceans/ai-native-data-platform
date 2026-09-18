"""User, role and session persistence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.ids import utcnow
from app.models.orm import AuthSession, Role, User, UserRole


def get_user(session: Session, user_id: uuid.UUID) -> User | None:
    return session.get(User, user_id)


def get_user_by_username(session: Session, username: str) -> User | None:
    return session.execute(select(User).where(User.username == username)).scalar_one_or_none()


def list_users(session: Session, limit: int, offset: int) -> tuple[list[User], int]:
    total = session.execute(select(func.count()).select_from(User)).scalar_one()
    rows = (
        session.execute(select(User).order_by(User.created_at, User.id).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return list(rows), int(total)


def create_user(session: Session, username: str, password_hash: str) -> User:
    user = User(id=uuid.uuid4(), username=username, password_hash=password_hash, active=True)
    session.add(user)
    session.flush()
    return user


def set_user_active(session: Session, user: User, active: bool) -> None:
    user.active = active
    session.flush()


def roles_for_user(session: Session, user_id: uuid.UUID) -> list[Role]:
    rows = session.execute(
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user_id)
        .order_by(Role.name)
    ).scalars()
    return list(rows)


def role_ids_for_user(session: Session, user_id: uuid.UUID) -> list[uuid.UUID]:
    rows = session.execute(
        select(UserRole.role_id).where(UserRole.user_id == user_id)
    ).scalars()
    return list(rows)


def set_user_roles(session: Session, user_id: uuid.UUID, role_ids: list[uuid.UUID]) -> None:
    session.execute(delete(UserRole).where(UserRole.user_id == user_id))
    for role_id in dict.fromkeys(role_ids):
        session.add(UserRole(user_id=user_id, role_id=role_id))
    session.flush()


def list_roles(session: Session) -> list[Role]:
    return list(session.execute(select(Role).order_by(Role.name)).scalars())


def get_role(session: Session, role_id: uuid.UUID) -> Role | None:
    return session.get(Role, role_id)


def get_role_by_name(session: Session, name: str) -> Role | None:
    return session.execute(select(Role).where(Role.name == name)).scalar_one_or_none()


def create_role(session: Session, name: str) -> Role:
    role = Role(id=uuid.uuid4(), name=name)
    session.add(role)
    session.flush()
    return role


def create_session(
    session: Session,
    user_id: uuid.UUID,
    token_hash: str,
    csrf_hash: str,
    expires_at: datetime,
) -> AuthSession:
    row = AuthSession(
        id=uuid.uuid4(),
        user_id=user_id,
        token_hash=token_hash,
        csrf_hash=csrf_hash,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return row


def get_session_by_token_hash(session: Session, token_hash: str) -> AuthSession | None:
    return session.execute(
        select(AuthSession).where(AuthSession.token_hash == token_hash)
    ).scalar_one_or_none()


def revoke_session(session: Session, row: AuthSession) -> None:
    row.revoked_at = utcnow()
    session.flush()


def purge_expired_sessions(session: Session) -> int:
    result = session.execute(
        delete(AuthSession).where(AuthSession.expires_at < utcnow())
    )
    return int(result.rowcount or 0)
