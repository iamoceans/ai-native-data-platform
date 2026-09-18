"""FastAPI dependencies: DB session, auth context, CSRF, capabilities."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.auth import sessions as sessions_service
from app.auth.rbac import capabilities_for_roles
from app.auth.sessions import AuthContext
from app.config import Settings, get_settings
from app.constants import ErrorCode
from app.db import get_session_factory
from app.errors import ApiError
from app.repositories import users as users_repo

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_db() -> Iterator[Session]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_settings_dep() -> Settings:
    return get_settings()


def get_request_id(request: Request) -> str:
    return getattr(request.state, "request_id", str(uuid.uuid4()))


def get_trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", "unknown")


def get_client_ip(request: Request) -> str:
    if request.client is None:
        return "unknown"
    return request.client.host


def _read_cookie(request: Request, settings: Settings) -> str | None:
    return request.cookies.get(settings.cookie_name)


def current_auth(
    request: Request,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> AuthContext:
    token = _read_cookie(request, settings)
    if not token:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "authentication required")
    session_row = sessions_service.validate_token(session, token)
    if session_row is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "session expired or revoked")
    user = users_repo.get_user(session, session_row.user_id)
    if user is None or not user.active:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "user is not active")
    roles = users_repo.roles_for_user(session, user.id)
    capabilities = capabilities_for_roles([role.name for role in roles])
    return AuthContext(user=user, session=session_row, roles=roles, capabilities=capabilities)


def require_csrf(
    request: Request,
    auth: AuthContext = Depends(current_auth),
) -> AuthContext:
    """Origin + CSRF header validation for unsafe methods (spec section 22)."""
    if request.method not in UNSAFE_METHODS:
        return auth
    settings = get_settings()
    csrf = request.headers.get("X-CSRF-Token")
    if not csrf or not sessions_service.validate_csrf(auth.session, csrf):
        raise ApiError(ErrorCode.CSRF_INVALID, "invalid or missing CSRF token")
    origin = request.headers.get("Origin")
    if origin and origin not in settings.cors_origin_list:
        raise ApiError(ErrorCode.FORBIDDEN, "origin not allowed")
    return auth


def require_admin(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    return auth
