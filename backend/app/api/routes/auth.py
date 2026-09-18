"""Local authentication endpoints (spec section 22)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import (
    current_auth,
    get_client_ip,
    get_db,
    get_settings_dep,
    get_trace_id,
    require_csrf,
)
from app.api.dto import LoginRequest, LoginResponse, ProfileResponse
from app.auth import sessions as sessions_service
from app.auth.ratelimit import LoginRateLimiter
from app.auth.sessions import AuthContext
from app.auth.passwords import verify_password
from app.config import Settings
from app.constants import ErrorCode
from app.errors import ApiError
from app.repositories import audit as audit_repo
from app.repositories import users as users_repo

router = APIRouter(prefix="/auth", tags=["auth"])

_limiter: LoginRateLimiter | None = None


def get_login_limiter(settings: Settings | None = None) -> LoginRateLimiter:
    global _limiter
    if _limiter is None:
        settings = settings or get_settings_dep()
        _limiter = LoginRateLimiter(
            settings.login_rate_limit_attempts, settings.login_rate_limit_window_seconds
        )
    return _limiter


def reset_login_limiter() -> None:
    global _limiter
    _limiter = None


def _profile(auth: AuthContext) -> ProfileResponse:
    return ProfileResponse(
        id=auth.user.id,
        username=auth.user.username,
        roles=auth.role_names,
        capabilities=sorted(auth.capabilities),
    )


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> LoginResponse:
    client = get_client_ip(request)
    trace_id = get_trace_id(request)
    limiter = get_login_limiter(settings)
    if not limiter.check(payload.username, client):
        audit_repo.add_audit(
            session,
            actor_id=None,
            action="auth.login",
            resource_type="user",
            resource_id=None,
            trace_id=trace_id,
            outcome="rate_limited",
            details={"username": payload.username},
        )
        raise ApiError(ErrorCode.RATE_LIMITED, "too many login attempts; try again later")

    user = users_repo.get_user_by_username(session, payload.username)
    # Uniform error for unknown user / wrong password / inactive account.
    if user is None or not user.active or not verify_password(user.password_hash, payload.password):
        limiter.record_failure(payload.username, client)
        audit_repo.add_audit(
            session,
            actor_id=user.id if user is not None else None,
            action="auth.login",
            resource_type="user",
            resource_id=str(user.id) if user is not None else None,
            trace_id=trace_id,
            outcome="denied",
            details={"username": payload.username},
        )
        raise ApiError(ErrorCode.UNAUTHENTICATED, "invalid username or password")

    session_row, token, csrf_token = sessions_service.issue_session(session, user, settings)
    limiter.reset(payload.username, client)
    audit_repo.add_audit(
        session,
        actor_id=user.id,
        action="auth.login",
        resource_type="user",
        resource_id=str(user.id),
        trace_id=trace_id,
        outcome="success",
        details={},
    )
    response.set_cookie(
        key=settings.cookie_name,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    # Readable companion cookie carrying the CSRF token so the SPA can recover
    # it after a page reload (double-submit pattern; the server still stores
    # only the hash and validates the header against it).
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        httponly=False,
        samesite="lax",
        secure=settings.cookie_secure,
        max_age=settings.session_ttl_hours * 3600,
        path="/",
    )
    roles = users_repo.roles_for_user(session, user.id)
    from app.auth.rbac import capabilities_for_roles

    capabilities = capabilities_for_roles([role.name for role in roles])
    return LoginResponse(
        profile=ProfileResponse(
            id=user.id,
            username=user.username,
            roles=[role.name for role in roles],
            capabilities=sorted(capabilities),
        ),
        csrf_token=csrf_token,
    )


@router.post("/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    users_repo.revoke_session(session, auth.session)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="auth.logout",
        resource_type="user",
        resource_id=str(auth.user.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={},
    )
    response.delete_cookie(settings.cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return Response(status_code=204)


@router.get("/me", response_model=ProfileResponse)
def me(auth: AuthContext = Depends(current_auth)) -> ProfileResponse:
    return _profile(auth)
