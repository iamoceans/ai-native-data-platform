"""Health endpoints (spec section 22)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.api.dto import HealthResponse
from app.constants import QueryStatus
from app.models.orm import QueryJob

router = APIRouter(tags=["health"])


@router.get("/health/live", response_model=HealthResponse)
def live() -> HealthResponse:
    return HealthResponse(status="ok", checks={"process": "alive"})


@router.get("/health/ready", response_model=HealthResponse)
def ready(session: Session = Depends(get_db)) -> HealthResponse:
    checks: dict[str, str] = {}
    session.execute(text("SELECT 1"))
    checks["control_db"] = "ok"
    running = session.execute(
        select(func.count()).select_from(QueryJob).where(QueryJob.status == QueryStatus.RUNNING)
    ).scalar_one()
    checks["queries_running"] = str(int(running))
    queued = session.execute(
        select(func.count()).select_from(QueryJob).where(QueryJob.status == QueryStatus.QUEUED)
    ).scalar_one()
    checks["queries_queued"] = str(int(queued))
    return HealthResponse(status="ok", checks=checks)
