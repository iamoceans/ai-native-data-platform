"""Datasource endpoints (spec section 22)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import current_auth, get_db, get_settings_dep, get_trace_id, require_admin, require_csrf
from app.api.dto import (
    DatasourceCreate,
    DatasourceListResponse,
    DatasourcePatch,
    DatasourceResponse,
    DatasourceTestResponse,
    ErrorBody,
    IngestionTaskResponse,
    ProviderCapabilitiesDTO,
)
from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import ErrorCode
from app.datasource import service as datasource_service
from app.errors import ApiError
from app.ids import utcnow
from app.metadata import ingestion as ingestion_service
from app.models.orm import IngestionTask
from app.repositories import datasources as datasources_repo
from app.results.cursor import decode_list_cursor, encode_list_cursor

router = APIRouter(tags=["datasources"])

PAGE_DEFAULT = 20
PAGE_MAX = 100
ROUTE_KEY = "GET /datasources"


def _page(request: Request, settings: Settings) -> tuple[int, int]:
    cursor = request.query_params.get("cursor")
    try:
        limit = int(request.query_params.get("limit", PAGE_DEFAULT))
    except ValueError as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "limit must be an integer") from exc
    if not 1 <= limit <= PAGE_MAX:
        raise ApiError(ErrorCode.VALIDATION_ERROR, f"limit must be between 1 and {PAGE_MAX}")
    offset = 0
    if cursor:
        offset = decode_list_cursor(settings.resolved_cursor_secret(), cursor, route=ROUTE_KEY)
    return limit, offset


@router.get("/datasources", response_model=DatasourceListResponse)
def list_datasources(
    request: Request,
    _auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DatasourceListResponse:
    limit, offset = _page(request, settings)
    rows, total = datasources_repo.list_datasources(session, limit=limit, offset=offset)
    items = [DatasourceResponse(**datasource_service.datasource_to_dict(row)) for row in rows]
    next_cursor = None
    if offset + len(rows) < total:
        next_cursor = encode_list_cursor(
            settings.resolved_cursor_secret(), route=ROUTE_KEY, offset=offset + len(rows)
        )
    return DatasourceListResponse(items=items, next_cursor=next_cursor)


@router.post("/datasources", response_model=DatasourceResponse, status_code=201)
def create_datasource(
    payload: DatasourceCreate,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DatasourceResponse:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    row = datasource_service.create_datasource(
        session,
        actor=auth,
        name=payload.name,
        kind=payload.kind,
        connection_config=payload.connection_config.model_dump(exclude_unset=True),
        secret_ref=payload.secret_ref,
        enabled=payload.enabled,
        settings=settings,
        trace_id=get_trace_id(request),
    )
    return DatasourceResponse(**datasource_service.datasource_to_dict(row))


@router.patch("/datasources/{datasource_id}", response_model=DatasourceResponse)
def patch_datasource(
    datasource_id: uuid.UUID,
    payload: DatasourcePatch,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DatasourceResponse:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    row = datasources_repo.get_datasource(session, datasource_id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "datasource not found")
    patch = payload.model_dump(exclude_unset=True, exclude={"version"})
    row = datasource_service.update_datasource(
        session,
        actor=auth,
        datasource=row,
        version=payload.version,
        patch={k: v for k, v in patch.items() if v is not None},
        settings=settings,
        trace_id=get_trace_id(request),
    )
    return DatasourceResponse(**datasource_service.datasource_to_dict(row))


@router.post("/datasources/{datasource_id}/test", response_model=DatasourceTestResponse)
def test_datasource(
    datasource_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DatasourceTestResponse:
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    row = datasources_repo.get_datasource(session, datasource_id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "datasource not found")
    health, capabilities = datasource_service.test_connection(
        session, actor=auth, datasource=row, settings=settings, trace_id=get_trace_id(request)
    )
    return DatasourceTestResponse(
        status=health.status,
        server_version=health.server_version,
        latency_ms=health.latency_ms,
        checked_at=utcnow(),
        capabilities=ProviderCapabilitiesDTO(**capabilities) if capabilities else None,
        error=ErrorBody(**health.error) if health.error else None,
    )


@router.post("/datasources/{datasource_id}/sync", response_model=IngestionTaskResponse, status_code=202)
def sync_datasource(
    datasource_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> IngestionTaskResponse:
    """Queue a DataHub ingestion for the datasource's registered datasets."""
    if "admin.manage" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "administrator capability required")
    datasource = datasources_repo.get_datasource(session, datasource_id)
    if datasource is None:
        raise ApiError(ErrorCode.NOT_FOUND, "datasource not found")
    task = ingestion_service.request_sync(
        session,
        actor=auth,
        datasource=datasource,
        settings=settings,
        trace_id=get_trace_id(request),
    )
    return IngestionTaskResponse(**ingestion_service.ingestion_to_response(task))


@router.get("/ingestions/{ingestion_id}", response_model=IngestionTaskResponse)
def get_ingestion(
    ingestion_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> IngestionTaskResponse:
    task = session.get(IngestionTask, ingestion_id)
    if task is None:
        raise ApiError(ErrorCode.NOT_FOUND, "ingestion task not found")
    if "admin.manage" not in auth.capabilities and task.requested_by != auth.user.id:
        raise ApiError(ErrorCode.NOT_FOUND, "ingestion task not found")
    return IngestionTaskResponse(**ingestion_service.ingestion_to_response(task))
