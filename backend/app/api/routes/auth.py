"""Local authentication endpoints (spec section 22)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import (
    current_auth,
    get_client_ip,
    get_db,
    get_settings_dep,
    get_trace_id,
    require_csrf,
)
from app.api.dto import AccountAccessResponse, LoginRequest, LoginResponse, PasswordChange, ProfileResponse, ProfileUpdate
from app.auth import sessions as sessions_service
from app.auth.ratelimit import LoginRateLimiter, DatabaseLoginRateLimiter
from app.auth.sessions import AuthContext
from app.auth.passwords import hash_password, verify_password
from app.config import Settings
from app.constants import ErrorCode, ROLE_CAPABILITIES
from app.errors import ApiError
from app.repositories import audit as audit_repo
from app.repositories import users as users_repo

from app.ids import utcnow
from app.metadata.service import declared_domains
from app.metadata.semantic import dataset_namespace, dataset_qualifier
from app.repositories import profile as profile_repo
from app.results.cursor import decode_list_cursor, encode_list_cursor

router = APIRouter(prefix="/auth", tags=["auth"])

_limiter: LoginRateLimiter | None = None


def get_login_limiter(settings: Settings | None = None, session: Session | None = None) -> LoginRateLimiter | DatabaseLoginRateLimiter:
    if session is not None:
        settings = settings or get_settings_dep()
        return DatabaseLoginRateLimiter(session, settings.login_rate_limit_attempts, settings.login_rate_limit_window_seconds)
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
        display_name=getattr(auth.user, "display_name", ""),
        analysis_defaults=getattr(auth.user, "analysis_defaults", {}) or {},
        locale=getattr(auth.user, "locale", None),
        timezone=getattr(auth.user, "timezone", None),
        settings_version=getattr(auth.user, "settings_version", 1),
    )


@router.get("/me/access", response_model=AccountAccessResponse)
def account_access(
    response: Response,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
    cursor: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
) -> AccountAccessResponse:
    """Current account only. Grants remain separate so expiry is never ambiguous."""
    response.headers["Cache-Control"] = "no-store"
    route = f"account-access:{auth.user.id}"
    offset = decode_list_cursor(settings.resolved_cursor_secret(), cursor, route=route) if cursor else 0
    now = utcnow()
    rows = profile_repo.effective_grants(
        session, user_id=auth.user.id, role_ids=auth.role_ids, now=now,
    )
    datasets = {row[0].id: row[0] for row in rows}
    domains = declared_domains(settings, list(datasets.values()))
    items = {}
    for dataset, source_name, kind, enabled, role, action, expires in rows:
        item = items.setdefault(dataset.id, {
            "dataset_id": dataset.id, "datasource_id": dataset.datasource_id,
            "datasource_name": source_name, "datasource_kind": kind,
            "datasource_enabled": enabled,
            "catalog": dataset.catalog_name,
            "namespace": dataset_namespace(dataset.catalog_name, dataset.schema_name),
            "qualifier": dataset_qualifier(dataset.catalog_name, dataset.schema_name, dataset.object_name),
            "domain": domains.get(dataset.id), "actions": [], "grants": [], "query_available": False,
        })
        if action not in item["actions"]:
            item["actions"].append(action)
        item["grants"].append({"role": role, "action": action, "expires_at": expires})
    summary = {}
    for item in items.values():
        item["actions"].sort()
        item["query_available"] = (item["datasource_enabled"] and "query.submit" in auth.capabilities
                                   and {"discover", "query"}.issubset(item["actions"]))
        domain = item["domain"]
        count = summary.setdefault(domain, {"domain": domain, "dataset_count": 0, "queryable_count": 0})
        count["dataset_count"] += 1
        count["queryable_count"] += int(item["query_available"])
    values = list(items.values())
    next_cursor = (encode_list_cursor(settings.resolved_cursor_secret(), route=route, offset=offset + limit)
                   if offset + limit < len(values) else None)
    return AccountAccessResponse(
        profile=_profile(auth), active=auth.user.active, created_at=auth.user.created_at,
        last_login_at=profile_repo.last_successful_login(session, user_id=auth.user.id), checked_at=now,
        roles=[{"name": role.name, "capabilities": sorted(ROLE_CAPABILITIES.get(role.name, ())) }
               for role in sorted(auth.roles, key=lambda role: role.name)],
        items=values[offset:offset + limit], domains=sorted(summary.values(), key=lambda item: item["domain"] or ""),
        datasource_count=len({item["datasource_id"] for item in values}), total=len(values), next_cursor=next_cursor,
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
    limiter = get_login_limiter(settings, session)
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
        # Only the denial audit was written; persist it before dependency rollback.
        session.commit()
        raise ApiError(ErrorCode.RATE_LIMITED, "too many login attempts; try again later")

    user = users_repo.get_user_by_username(session, payload.username)
    if user is not None:
        # Serialize password verification/session issuance with password rotation.
        # Otherwise an old-password login can publish a new session just after
        # rotation revoked the previously existing sessions.
        user = users_repo.get_user_for_update(session, user.id)
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
        session.commit()
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
            display_name=user.display_name,
            analysis_defaults=user.analysis_defaults or {},
            locale=user.locale,
            timezone=user.timezone,
            settings_version=user.settings_version,
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
    response.status_code = 204
    return response


@router.get("/me", response_model=ProfileResponse)
def me(auth: AuthContext = Depends(current_auth)) -> ProfileResponse:
    return _profile(auth)


@router.patch("/me", response_model=ProfileResponse)
def update_me(
    payload: ProfileUpdate, request: Request,
    auth: AuthContext = Depends(require_csrf), session: Session = Depends(get_db),
) -> ProfileResponse:
    user = users_repo.get_user_for_update(session, auth.user.id)
    if user is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "account is unavailable")
    if user.settings_version != payload.expected_settings_version:
        raise ApiError(ErrorCode.CONFLICT, "settings changed; reload before saving")
    changes = payload.model_dump(exclude_unset=True, exclude={"expected_settings_version"})
    if "analysis_defaults" in changes:
        from app.repositories.users import validate_analysis_defaults
        validate_analysis_defaults(session, changes["analysis_defaults"])
    if changes:
        users_repo.update_profile(session, user, changes)
        audit_repo.add_audit(
            session, actor_id=user.id, action="user.profile.update", resource_type="user",
            resource_id=str(user.id), trace_id=get_trace_id(request), outcome="success",
            details={"fields": sorted(changes)},
        )
    return _profile(auth)


@router.post("/password", status_code=204)
def change_password(
    payload: PasswordChange, request: Request, response: Response,
    auth: AuthContext = Depends(require_csrf), session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> Response:
    limiter = get_login_limiter(settings, session)
    bucket = f"password-change:{auth.user.id}"
    client = get_client_ip(request)
    if not limiter.check(bucket, client):
        raise ApiError(ErrorCode.RATE_LIMITED, "too many password attempts; try again later")
    user = users_repo.get_user_for_update(session, auth.user.id)
    if user is None:
        raise ApiError(ErrorCode.UNAUTHENTICATED, "account is unavailable")
    if not verify_password(user.password_hash, payload.current_password.get_secret_value()):
        limiter.record_failure(bucket, client)
        audit_repo.add_audit(session, actor_id=user.id, action="auth.password.change",
            resource_type="user", resource_id=str(user.id), trace_id=get_trace_id(request),
            outcome="denied", details={})
        session.commit()
        raise ApiError(ErrorCode.VALIDATION_ERROR, "current password is incorrect")
    if payload.current_password.get_secret_value() == payload.new_password.get_secret_value():
        raise ApiError(ErrorCode.VALIDATION_ERROR, "new password must differ from current password")
    revoked = users_repo.rotate_password(session, user, hash_password(payload.new_password.get_secret_value()))
    limiter.reset(bucket, client)
    audit_repo.add_audit(session, actor_id=user.id, action="auth.password.change",
        resource_type="user", resource_id=str(user.id), trace_id=get_trace_id(request),
        outcome="success", details={"revoked_sessions": revoked})
    response.delete_cookie(settings.cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    response.status_code = 204
    return response
