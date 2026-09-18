"""Dataset catalog endpoints (spec section 22)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.api.deps import current_auth, get_db, get_settings_dep
from app.api.dto import (
    DatasetContext,
    DatasetListResponse,
    DatasetSchemaResponse,
    DatasetSummary,
    LineageResponse,
)
from app.auth.rbac import ensure_dataset_action, is_admin
from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import DatasetAction, ErrorCode
from app.errors import ApiError
from app.metadata import service as metadata_service
from app.repositories import datasets as datasets_repo
from app.repositories import datasources as datasources_repo
from app.results.cursor import decode_list_cursor, encode_list_cursor

router = APIRouter(tags=["datasets"])

ROUTE_KEY = "GET /datasets"


@router.get("/datasets", response_model=DatasetListResponse)
def search_datasets(
    request: Request,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DatasetListResponse:
    cursor = request.query_params.get("cursor")
    try:
        limit = int(request.query_params.get("limit", 20))
    except ValueError as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "limit must be an integer") from exc
    if not 1 <= limit <= 100:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "limit must be between 1 and 100")
    offset = (
        decode_list_cursor(settings.resolved_cursor_secret(), cursor, route=ROUTE_KEY)
        if cursor
        else 0
    )
    query = request.query_params.get("q")
    datasource_id = request.query_params.get("datasource_id")
    datasource_uuid = uuid.UUID(datasource_id) if datasource_id else None
    rows, _total, has_more = metadata_service.search_datasets(
        session,
        role_ids=auth.role_ids,
        query=query,
        datasource_id=datasource_uuid,
        limit=limit,
        offset=offset,
        settings=settings,
    )
    next_cursor = None
    if has_more:
        next_cursor = encode_list_cursor(
            settings.resolved_cursor_secret(), route=ROUTE_KEY, offset=offset + len(rows)
        )
    return DatasetListResponse(
        items=[DatasetSummary(**metadata_service.dataset_summary(row)) for row in rows],
        next_cursor=next_cursor,
    )


def _load_dataset(session: Session, auth: AuthContext, dataset_id: uuid.UUID):
    dataset = datasets_repo.get_dataset(session, dataset_id)
    if dataset is None or not dataset.active:
        raise ApiError(ErrorCode.NOT_FOUND, "dataset not found or not visible")
    ensure_dataset_action(
        session, role_ids=auth.role_ids, dataset=dataset, action=DatasetAction.DISCOVER
    )
    return dataset


@router.get("/datasets/{dataset_id}", response_model=DatasetContext)
def get_dataset(
    dataset_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DatasetContext:
    dataset = _load_dataset(session, auth, dataset_id)
    datasource = datasources_repo.get_datasource(session, dataset.datasource_id)
    if datasource is None:
        raise ApiError(ErrorCode.NOT_FOUND, "datasource not found")
    context = metadata_service.dataset_context(
        session,
        role_ids=auth.role_ids,
        dataset=dataset,
        datasource=datasource,
        settings=settings,
        include_datahub_link=is_admin(auth.role_names),
    )
    return DatasetContext(**context)


@router.get("/datasets/{dataset_id}/schema", response_model=DatasetSchemaResponse)
def get_dataset_schema(
    dataset_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> DatasetSchemaResponse:
    dataset = _load_dataset(session, auth, dataset_id)
    datasource = datasources_repo.get_datasource(session, dataset.datasource_id)
    if datasource is None:
        raise ApiError(ErrorCode.NOT_FOUND, "datasource not found")
    view = metadata_service.get_schema_view(
        session, dataset=dataset, datasource=datasource, settings=settings
    )
    return DatasetSchemaResponse(
        dataset_id=dataset.id,
        schema_hash=view.schema_hash,
        columns=view.columns,
        fetched_at=view.fetched_at,
        expires_at=view.expires_at,
        source=view.source,
    )


@router.get("/datasets/{dataset_id}/lineage", response_model=LineageResponse)
def get_dataset_lineage(
    dataset_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> LineageResponse:
    dataset = _load_dataset(session, auth, dataset_id)
    direction = request.query_params.get("direction", "upstream")
    depth_param = request.query_params.get("depth", "1")
    try:
        depth = int(depth_param)
    except ValueError:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "depth must be an integer")
    payload = metadata_service.dataset_lineage(
        session,
        role_ids=auth.role_ids,
        dataset=dataset,
        settings=settings,
        direction=direction,
        depth=depth,
    )
    return LineageResponse(**payload)
