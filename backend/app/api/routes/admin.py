"""Admin endpoints: users, roles, grants, audits, catalog refresh (spec section 22)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db, get_settings_dep, get_trace_id, require_admin, require_csrf
from app.api.dto import (
    AuditEntry,
    AuditListResponse,
    CatalogRefreshRequest,
    PermissionRequestListResponse,
    CatalogRefreshResponse,
    DatasetSummary,
    GrantCreate,
    GrantListResponse,
    GrantResponse,
    PermissionRequestResponse,
    RoleCreate,
    RoleResponse,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserRolesUpdate,
)
from app.api.serializers import (
    audit_to_response,
    grant_to_response,
    permission_request_to_response,
    role_to_response,
    user_to_response,
)
from app.auth.passwords import hash_password
from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import DatasetAction, ErrorCode, RoleName
from app.errors import ApiError
from app.ids import utcnow
from app.metadata import service as metadata_service
from app.repositories import audit as audit_repo
from app.repositories import datasources as datasources_repo
from app.repositories import datasets as datasets_repo
from app.repositories import policy as policy_repo
from app.repositories import queries as queries_repo
from app.repositories import users as users_repo
from app.results.cursor import decode_list_cursor, encode_list_cursor
from app.api.serializers import uuid_or_none

router = APIRouter(prefix="/admin", tags=["admin"])

USERS_ROUTE = "GET /admin/users"
AUDITS_ROUTE = "GET /admin/audits"
GRANTS_ROUTE = "GET /admin/grants"


def _page(request: Request, settings: Settings, route: str, default: int = 20) -> tuple[int, int, int]:
    cursor = request.query_params.get("cursor")
    try:
        limit = int(request.query_params.get("limit", default))
    except ValueError as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "limit must be an integer") from exc
    if not 1 <= limit <= 100:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "limit must be between 1 and 100")
    offset = decode_list_cursor(settings.resolved_cursor_secret(), cursor, route=route) if cursor else 0
    return limit, offset, limit


# ---------------------------------------------------------------------------
# Users and roles
# ---------------------------------------------------------------------------
@router.get("/users", response_model=UserListResponse)
def list_users(
    request: Request,
    auth: AuthContext = Depends(require_admin),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> UserListResponse:
    limit, offset, _ = _page(request, settings, USERS_ROUTE)
    rows, total = users_repo.list_users(session, limit=limit, offset=offset)
    items = [UserResponse(**user_to_response(row, users_repo.roles_for_user(session, row.id))) for row in rows]
    next_cursor = (
        encode_list_cursor(settings.resolved_cursor_secret(), route=USERS_ROUTE, offset=offset + len(rows))
        if offset + len(rows) < total
        else None
    )
    return UserListResponse(items=items, next_cursor=next_cursor)


@router.post("/users", response_model=UserResponse, status_code=201)
def create_user(
    payload: UserCreate,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> UserResponse:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    if users_repo.get_user_by_username(session, payload.username) is not None:
        raise ApiError(ErrorCode.CONFLICT, "username already exists")
    roles = []
    for role_id in payload.role_ids:
        role = users_repo.get_role(session, role_id)
        if role is None:
            raise ApiError(ErrorCode.VALIDATION_ERROR, f"unknown role id {role_id}")
        roles.append(role)
    user = users_repo.create_user(session, payload.username, hash_password(payload.password))
    users_repo.set_user_roles(session, user.id, [role.id for role in roles])
    datasources_repo.ensure_user_capacity(session, user.id, settings.per_user_concurrency)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="user.create",
        resource_type="user",
        resource_id=str(user.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"username": payload.username, "roles": [role.name for role in roles]},
    )
    return UserResponse(**user_to_response(user, roles))


@router.put("/users/{user_id}/roles", response_model=UserResponse)
def update_user_roles(
    user_id: uuid.UUID,
    payload: UserRolesUpdate,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> UserResponse:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    user = users_repo.get_user(session, user_id)
    if user is None:
        raise ApiError(ErrorCode.NOT_FOUND, "user not found")
    roles = []
    for role_id in payload.role_ids:
        role = users_repo.get_role(session, role_id)
        if role is None:
            raise ApiError(ErrorCode.VALIDATION_ERROR, f"unknown role id {role_id}")
        roles.append(role)
    users_repo.set_user_roles(session, user.id, [role.id for role in roles])
    session.flush()
    policy_repo.bump_revision(session)
    role_ids = [role.id for role in roles]
    cancelled: list[str] = []
    from app.models.orm import QueryDependency

    for job in queries_repo.list_active_queries_for_user(session, user.id):
        dependencies = session.query(QueryDependency).filter_by(query_id=job.id).all()
        authorized = all(
            datasets_repo.has_dataset_action(
                session, role_ids, dependency.dataset_id, DatasetAction.QUERY
            )
            and datasets_repo.has_dataset_action(
                session, role_ids, dependency.dataset_id, DatasetAction.DISCOVER
            )
            for dependency in dependencies
        )
        if not authorized:
            queries_repo.request_cancel(session, job)
            cancelled.append(str(job.id))
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="user.roles_update",
        resource_type="user",
        resource_id=str(user.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"roles": [role.name for role in roles], "cancelled_queries": cancelled},
    )
    return UserResponse(**user_to_response(user, roles))


@router.get("/roles", response_model=list[RoleResponse])
def list_roles(
    auth: AuthContext = Depends(require_admin),
    session: Session = Depends(get_db),
) -> list[RoleResponse]:
    return [RoleResponse(**role_to_response(role)) for role in users_repo.list_roles(session)]


@router.post("/roles", response_model=RoleResponse, status_code=201)
def create_role(
    payload: RoleCreate,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> RoleResponse:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    if users_repo.get_role_by_name(session, payload.name) is not None:
        raise ApiError(ErrorCode.CONFLICT, "role already exists")
    role = users_repo.create_role(session, payload.name)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="role.create",
        resource_type="role",
        resource_id=str(role.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"name": payload.name},
    )
    return RoleResponse(**role_to_response(role))


# ---------------------------------------------------------------------------
# Dataset grants
# ---------------------------------------------------------------------------
@router.post("/grants", response_model=GrantResponse, status_code=201)
def create_grant(
    payload: GrantCreate,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> GrantResponse:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    role = users_repo.get_role(session, payload.role_id)
    if role is None:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "unknown role")
    dataset = datasets_repo.get_dataset(session, payload.dataset_id)
    if dataset is None:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "unknown dataset")
    if payload.action == DatasetAction.QUERY:
        existing_discover = datasets_repo.has_dataset_action(
            session, [role.id], dataset.id, DatasetAction.DISCOVER
        )
        if not existing_discover and payload.action != DatasetAction.DISCOVER:
            # spec 12.1: granting query requires discover to exist as well
            datasets_repo.create_grant(
                session,
                role_id=role.id,
                dataset_id=dataset.id,
                action=DatasetAction.DISCOVER,
                expires_at=payload.expires_at,
                created_by=auth.user.id,
            )
    if datasets_repo.has_dataset_action(session, [role.id], dataset.id, payload.action):
        raise ApiError(ErrorCode.CONFLICT, "this grant already exists")
    grant = datasets_repo.create_grant(
        session,
        role_id=payload.role_id,
        dataset_id=payload.dataset_id,
        action=payload.action,
        expires_at=payload.expires_at,
        created_by=auth.user.id,
    )
    policy_repo.bump_revision(session)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="grant.create",
        resource_type="permission",
        resource_id=str(grant.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={
            "role": role.name,
            "dataset_id": str(dataset.id),
            "action": payload.action,
        },
    )
    return GrantResponse(**grant_to_response(grant))


@router.get("/grants", response_model=GrantListResponse)
def list_grants(
    request: Request,
    auth: AuthContext = Depends(require_admin),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> GrantListResponse:
    limit, offset, _ = _page(request, settings, GRANTS_ROUTE, default=100)
    dataset_param = request.query_params.get("dataset_id")
    rows, total = datasets_repo.list_grants(
        session, limit=limit, offset=offset, dataset_id=uuid_or_none(dataset_param)
    )
    next_cursor = (
        encode_list_cursor(settings.resolved_cursor_secret(), route=GRANTS_ROUTE, offset=offset + len(rows))
        if offset + len(rows) < total
        else None
    )
    return GrantListResponse(items=[GrantResponse(**grant_to_response(row)) for row in rows], next_cursor=next_cursor)


@router.delete("/grants/{grant_id}", status_code=204)
def delete_grant(
    grant_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> Response:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    grant = datasets_repo.get_grant(session, grant_id)
    if grant is None:
        raise ApiError(ErrorCode.NOT_FOUND, "grant not found")
    dataset_id = grant.dataset_id
    role_id = grant.role_id
    datasets_repo.delete_grant(session, grant)
    policy_repo.bump_revision(session)
    # Revocation stops queries only when the owner no longer has equivalent
    # access through another role.
    cancelled: list[str] = []
    for job in queries_repo.list_active_queries_for_dataset(session, dataset_id):
        effective_roles = users_repo.role_ids_for_user(session, job.user_id)
        authorized = datasets_repo.has_dataset_action(
            session, effective_roles, dataset_id, DatasetAction.QUERY
        ) and datasets_repo.has_dataset_action(
            session, effective_roles, dataset_id, DatasetAction.DISCOVER
        )
        if not authorized:
            queries_repo.request_cancel(session, job)
            cancelled.append(str(job.id))
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="grant.delete",
        resource_type="permission",
        resource_id=str(grant_id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={
            "role_id": str(role_id),
            "dataset_id": str(dataset_id),
            "cancelled_queries": cancelled,
        },
    )
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Audits
# ---------------------------------------------------------------------------
@router.get("/audits", response_model=AuditListResponse)
def list_audits(
    request: Request,
    auth: AuthContext = Depends(require_admin),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> AuditListResponse:
    limit, offset, _ = _page(request, settings, AUDITS_ROUTE, default=50)
    action = request.query_params.get("action")
    rows, total = audit_repo.list_audits(session, action=action, limit=limit, offset=offset)
    next_cursor = (
        encode_list_cursor(settings.resolved_cursor_secret(), route=AUDITS_ROUTE, offset=offset + len(rows))
        if offset + len(rows) < total
        else None
    )
    return AuditListResponse(items=[AuditEntry(**audit_to_response(row)) for row in rows], next_cursor=next_cursor)


# ---------------------------------------------------------------------------
# Catalog refresh (M1 interim path; DataHub ingestion replaces it in M3)
# ---------------------------------------------------------------------------
@router.post("/datasources/{datasource_id}/catalog-refresh", response_model=CatalogRefreshResponse)
def catalog_refresh(
    datasource_id: uuid.UUID,
    payload: CatalogRefreshRequest,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> CatalogRefreshResponse:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    datasource = datasources_repo.get_datasource(session, datasource_id)
    if datasource is None:
        raise ApiError(ErrorCode.NOT_FOUND, "datasource not found")
    result = metadata_service.refresh_catalog(
        session,
        datasource=datasource,
        settings=settings,
        schemas=payload.schemas,
        secure_views=payload.secure_views,
    )
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="datasource.catalog_refresh",
        resource_type="datasource",
        resource_id=str(datasource.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={
            "registered": result.registered,
            "updated": result.updated,
            "deactivated": result.deactivated,
            "skipped": result.skipped,
        },
    )
    return CatalogRefreshResponse(
        datasource_id=datasource.id,
        registered=result.registered,
        updated=result.updated,
        deactivated=result.deactivated,
        items=[DatasetSummary(**metadata_service.dataset_summary(row)) for row in result.datasets],
        skipped=result.skipped,
    )


# ---------------------------------------------------------------------------
# Permission requests (mock approval state only; never writes permissions)
# ---------------------------------------------------------------------------
PERMISSION_REQUESTS_ROUTE = "GET /admin/permission-requests"


@router.get("/permission-requests", response_model=PermissionRequestListResponse)
def list_permission_requests(
    request: Request,
    auth: AuthContext = Depends(require_admin),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> PermissionRequestListResponse:
    """The administrator's queue: what people asked for, and in which state.

    The state is either a real request state or the explicitly named
    ``MOCK_APPROVED``; approving here never creates a grant (spec 22/24).
    """
    limit, offset, _ = _page(request, settings, PERMISSION_REQUESTS_ROUTE, default=50)
    status = request.query_params.get("status")
    if status is not None and status not in {"REQUESTED", "MOCK_APPROVED", "REJECTED"}:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "unknown permission request status")
    rows, total = datasets_repo.list_permission_requests(
        session, limit=limit, offset=offset, status=status
    )
    next_cursor = (
        encode_list_cursor(
            settings.resolved_cursor_secret(),
            route=PERMISSION_REQUESTS_ROUTE,
            offset=offset + len(rows),
        )
        if offset + len(rows) < total
        else None
    )
    return PermissionRequestListResponse(
        items=[PermissionRequestResponse(**permission_request_to_response(row)) for row in rows],
        next_cursor=next_cursor,
    )

@router.post("/permission-requests/{request_id}/mock-approve", response_model=PermissionRequestResponse)
def mock_approve_permission_request(
    request_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> PermissionRequestResponse:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    from app.models.orm import PermissionRequest

    row = session.get(PermissionRequest, request_id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "permission request not found")
    row.status = "MOCK_APPROVED"
    session.flush()
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="permission_request.mock_approve",
        resource_type="permission_request",
        resource_id=str(row.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"note": "mock state only; no grant was created"},
    )
    return PermissionRequestResponse(**permission_request_to_response(row))
