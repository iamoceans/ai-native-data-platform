"""Datasource and execution-capacity persistence (spec sections 8, 13)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.orm import Datasource, ExecutionCapacity

GLOBAL_SCOPE_ID = "all"


def get_datasource(session: Session, datasource_id: uuid.UUID) -> Datasource | None:
    return session.get(Datasource, datasource_id)


def get_datasource_by_name(session: Session, name: str) -> Datasource | None:
    return session.execute(
        select(Datasource).where(Datasource.name == name)
    ).scalar_one_or_none()


def list_datasources(session: Session, limit: int, offset: int) -> tuple[list[Datasource], int]:
    total = session.execute(select(func.count()).select_from(Datasource)).scalar_one()
    rows = (
        session.execute(
            select(Datasource)
            .where(Datasource.enabled.is_(True))
            .order_by(Datasource.created_at, Datasource.id)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def create_datasource(
    session: Session,
    *,
    name: str,
    kind: str,
    connection_config: dict[str, Any],
    secret_ref: str,
    capabilities: dict[str, Any],
    enabled: bool,
    created_by: uuid.UUID,
) -> Datasource:
    row = Datasource(
        id=uuid.uuid4(),
        name=name,
        kind=kind,
        connection_config=connection_config,
        secret_ref=secret_ref,
        capabilities=capabilities,
        enabled=enabled,
        health_status="UNKNOWN",
        created_by=created_by,
        version=1,
    )
    session.add(row)
    session.flush()
    return row


def update_datasource(
    session: Session,
    row: Datasource,
    *,
    name: str | None = None,
    connection_config: dict[str, Any] | None = None,
    secret_ref: str | None = None,
    enabled: bool | None = None,
    health_status: str | None = None,
    capabilities: dict[str, Any] | None = None,
) -> None:
    if name is not None:
        row.name = name
    if connection_config is not None:
        row.connection_config = connection_config
    if secret_ref is not None:
        row.secret_ref = secret_ref
    if enabled is not None:
        row.enabled = enabled
    if health_status is not None:
        row.health_status = health_status
    if capabilities is not None:
        row.capabilities = capabilities
    row.version = int(row.version) + 1
    session.flush()


# ---------------------------------------------------------------------------
# Execution capacity (spec section 13)
# ---------------------------------------------------------------------------
def upsert_capacity(session: Session, scope_type: str, scope_id: str, max_running: int) -> None:
    row = session.get(ExecutionCapacity, (scope_type, scope_id))
    if row is None:
        session.add(
            ExecutionCapacity(scope_type=scope_type, scope_id=scope_id, max_running=max_running)
        )
    else:
        row.max_running = max_running
    session.flush()


def ensure_datasource_capacity(session: Session, datasource_id: uuid.UUID, max_running: int) -> None:
    upsert_capacity(session, "datasource", str(datasource_id), max_running)


def ensure_user_capacity(session: Session, user_id: uuid.UUID, max_running: int) -> None:
    upsert_capacity(session, "user", str(user_id), max_running)


def ensure_global_capacity(session: Session, max_running: int) -> None:
    upsert_capacity(session, "global", GLOBAL_SCOPE_ID, max_running)
