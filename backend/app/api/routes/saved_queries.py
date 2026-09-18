"""Saved queries and permission requests (spec section 22)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import current_auth, get_db, get_settings_dep, get_trace_id, require_csrf
from app.api.dto import (
    PermissionRequestCreate,
    PermissionRequestResponse,
    SavedQueryCreate,
    SavedQueryListResponse,
    SavedQueryResponse,
)
from app.api.serializers import permission_request_to_response, saved_query_to_response
from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import ErrorCode
from app.errors import ApiError
from app.models.orm import PermissionRequest, SavedQuery
from app.repositories import audit as audit_repo
from app.repositories import datasources as datasources_repo
from app.repositories import datasets as datasets_repo
from app.results.cursor import decode_list_cursor, encode_list_cursor

router = APIRouter(tags=["saved-queries", "permissions"])

SAVED_ROUTE = "GET /saved-queries"


@router.get("/saved-queries", response_model=SavedQueryListResponse)
def list_saved_queries(
    request: Request,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> SavedQueryListResponse:
    cursor = request.query_params.get("cursor")
    try:
        limit = int(request.query_params.get("limit", 20))
    except ValueError as exc:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "limit must be an integer") from exc
    if not 1 <= limit <= 100:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "limit must be between 1 and 100")
    offset = (
        decode_list_cursor(settings.resolved_cursor_secret(), cursor, route=SAVED_ROUTE)
        if cursor
        else 0
    )
    query = (
        session.query(SavedQuery)
        .filter(SavedQuery.user_id == auth.user.id)
        .order_by(SavedQuery.created_at.desc())
    )
    total = query.count()
    rows = query.limit(limit).offset(offset).all()
    next_cursor = (
        encode_list_cursor(settings.resolved_cursor_secret(), route=SAVED_ROUTE, offset=offset + len(rows))
        if offset + len(rows) < total
        else None
    )
    return SavedQueryListResponse(
        items=[SavedQueryResponse(**saved_query_to_response(row)) for row in rows],
        next_cursor=next_cursor,
    )


@router.post("/saved-queries", response_model=SavedQueryResponse, status_code=201)
def create_saved_query(
    payload: SavedQueryCreate,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> SavedQueryResponse:
    datasource = datasources_repo.get_datasource(session, payload.datasource_id)
    if datasource is None or not datasource.enabled:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "unknown or disabled datasource")
    row = SavedQuery(
        id=uuid.uuid4(),
        user_id=auth.user.id,
        datasource_id=payload.datasource_id,
        title=payload.title,
        sql_text=payload.sql,
    )
    session.add(row)
    session.flush()
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="saved_query.create",
        resource_type="saved_query",
        resource_id=str(row.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"datasource_id": str(payload.datasource_id)},
    )
    return SavedQueryResponse(**saved_query_to_response(row))


@router.delete("/saved-queries/{saved_query_id}", status_code=204)
def delete_saved_query(
    saved_query_id: uuid.UUID,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> Response:
    row = session.get(SavedQuery, saved_query_id)
    if row is None or row.user_id != auth.user.id:
        raise ApiError(ErrorCode.NOT_FOUND, "saved query not found or not visible")
    session.delete(row)
    return Response(status_code=204)


@router.post("/permission-requests", response_model=PermissionRequestResponse, status_code=201)
def create_permission_request(
    payload: PermissionRequestCreate,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> PermissionRequestResponse:
    dataset = datasets_repo.get_dataset(session, payload.dataset_id)
    if dataset is None or not dataset.active:
        raise ApiError(ErrorCode.NOT_FOUND, "dataset not found or not visible")
    row = PermissionRequest(
        id=uuid.uuid4(),
        user_id=auth.user.id,
        dataset_id=payload.dataset_id,
        reason=payload.reason,
        status="REQUESTED",
    )
    session.add(row)
    session.flush()
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="permission_request.create",
        resource_type="permission_request",
        resource_id=str(row.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"dataset_id": str(payload.dataset_id)},
    )
    return PermissionRequestResponse(**permission_request_to_response(row))
