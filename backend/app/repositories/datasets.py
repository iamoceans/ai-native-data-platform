"""Dataset registry, metadata snapshots and dataset permissions."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.ids import utcnow
from app.models.orm import Dataset, MetadataSnapshot, Permission, PermissionRequest


def get_dataset(session: Session, dataset_id: uuid.UUID) -> Dataset | None:
    return session.get(Dataset, dataset_id)


def find_dataset(
    session: Session,
    datasource_id: uuid.UUID,
    catalog_name: str,
    schema_name: str,
    object_name: str,
) -> Dataset | None:
    return session.execute(
        select(Dataset).where(
            Dataset.datasource_id == datasource_id,
            Dataset.catalog_name == catalog_name,
            Dataset.schema_name == schema_name,
            Dataset.object_name == object_name,
        )
    ).scalar_one_or_none()


def find_datasets_by_name(
    session: Session, datasource_id: uuid.UUID, object_name: str
) -> list[Dataset]:
    return list(
        session.execute(
            select(Dataset).where(
                Dataset.datasource_id == datasource_id,
                Dataset.object_name == object_name,
                Dataset.active.is_(True),
            )
        ).scalars()
    )


def list_datasets(
    session: Session,
    *,
    datasource_id: uuid.UUID | None,
    query: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Dataset], int]:
    filters = [Dataset.active.is_(True)]
    if datasource_id is not None:
        filters.append(Dataset.datasource_id == datasource_id)
    if query:
        pattern = f"%{query}%"
        filters.append(
            or_(
                Dataset.object_name.ilike(pattern),
                Dataset.schema_name.ilike(pattern),
                Dataset.catalog_name.ilike(pattern),
            )
        )
    where = and_(*filters)
    total = session.execute(select(func.count()).select_from(Dataset).where(where)).scalar_one()
    rows = (
        session.execute(
            select(Dataset).where(where).order_by(Dataset.object_name, Dataset.id).limit(limit).offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def list_datasets_for_datasource(session: Session, datasource_id: uuid.UUID) -> list[Dataset]:
    return list(
        session.execute(
            select(Dataset).where(Dataset.datasource_id == datasource_id).order_by(Dataset.object_name)
        ).scalars()
    )


def upsert_dataset(
    session: Session,
    *,
    datasource_id: uuid.UUID,
    catalog_name: str,
    schema_name: str,
    object_name: str,
    object_type: str,
    schema_hash: str | None,
    sync_status: str,
    datahub_urn: str | None = None,
) -> tuple[Dataset, bool]:
    existing = find_dataset(session, datasource_id, catalog_name, schema_name, object_name)
    now = utcnow()
    if existing is None:
        row = Dataset(
            id=uuid.uuid4(),
            datasource_id=datasource_id,
            catalog_name=catalog_name,
            schema_name=schema_name,
            object_name=object_name,
            datahub_urn=datahub_urn,
            object_type=object_type,
            schema_hash=schema_hash,
            sync_status=sync_status,
            active=True,
            last_synced_at=now,
        )
        session.add(row)
        session.flush()
        return row, True
    existing.active = True
    existing.sync_status = sync_status
    existing.last_synced_at = now
    existing.schema_hash = schema_hash or existing.schema_hash
    if datahub_urn:
        existing.datahub_urn = datahub_urn
    session.flush()
    return existing, False


def deactivate_missing(
    session: Session, datasource_id: uuid.UUID, keep_ids: set[uuid.UUID]
) -> int:
    rows = list(
        session.execute(
            select(Dataset).where(Dataset.datasource_id == datasource_id, Dataset.active.is_(True))
        ).scalars()
    )
    count = 0
    for row in rows:
        if row.id not in keep_ids:
            row.active = False
            count += 1
    session.flush()
    return count


# ---------------------------------------------------------------------------
# Metadata snapshots
# ---------------------------------------------------------------------------
def latest_snapshot(session: Session, dataset_id: uuid.UUID) -> MetadataSnapshot | None:
    return session.execute(
        select(MetadataSnapshot)
        .where(MetadataSnapshot.dataset_id == dataset_id)
        .order_by(MetadataSnapshot.fetched_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def create_snapshot(
    session: Session,
    *,
    dataset_id: uuid.UUID,
    schema_version: int,
    content: dict[str, Any],
    schema_hash: str,
    fetched_at: datetime,
    expires_at: datetime,
) -> MetadataSnapshot:
    row = MetadataSnapshot(
        id=uuid.uuid4(),
        dataset_id=dataset_id,
        schema_version=schema_version,
        content=content,
        schema_hash=schema_hash,
        fetched_at=fetched_at,
        expires_at=expires_at,
    )
    session.add(row)
    session.flush()
    return row


# ---------------------------------------------------------------------------
# Dataset permissions (default deny)
# ---------------------------------------------------------------------------
def dataset_ids_with_action(
    session: Session, role_ids: list[uuid.UUID], action: str
) -> set[uuid.UUID]:
    if not role_ids:
        return set()
    rows = session.execute(
        select(Permission.dataset_id).where(
            Permission.role_id.in_(role_ids),
            Permission.action == action,
            or_(Permission.expires_at.is_(None), Permission.expires_at > utcnow()),
        )
    ).scalars()
    return set(rows)


def has_dataset_action(
    session: Session, role_ids: list[uuid.UUID], dataset_id: uuid.UUID, action: str
) -> bool:
    if not role_ids:
        return False
    row = session.execute(
        select(Permission.id).where(
            Permission.role_id.in_(role_ids),
            Permission.dataset_id == dataset_id,
            Permission.action == action,
            or_(Permission.expires_at.is_(None), Permission.expires_at > utcnow()),
        )
    ).first()
    return row is not None


def create_grant(
    session: Session,
    *,
    role_id: uuid.UUID,
    dataset_id: uuid.UUID,
    action: str,
    expires_at: datetime | None,
    created_by: uuid.UUID,
) -> Permission:
    row = Permission(
        id=uuid.uuid4(),
        role_id=role_id,
        dataset_id=dataset_id,
        action=action,
        expires_at=expires_at,
        created_by=created_by,
    )
    session.add(row)
    session.flush()
    return row


def get_grant(session: Session, grant_id: uuid.UUID) -> Permission | None:
    return session.get(Permission, grant_id)


def delete_grant(session: Session, row: Permission) -> None:
    session.delete(row)
    session.flush()


def list_permission_requests(
    session: Session, limit: int, offset: int, status: str | None = None
) -> tuple[list[PermissionRequest], int]:
    """Requests for data access, newest first (administrator view)."""
    filters = [] if status is None else [PermissionRequest.status == status]
    stmt = select(PermissionRequest)
    count_stmt = select(func.count()).select_from(PermissionRequest)
    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)
    total = session.execute(count_stmt).scalar_one()
    rows = (
        session.execute(
            stmt.order_by(PermissionRequest.created_at.desc(), PermissionRequest.id)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def list_permission_requests_for_user(
    session: Session, user_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[PermissionRequest], int]:
    """A requester's own requests, newest first (spec 24: the user sees status)."""
    total = session.execute(
        select(func.count()).select_from(PermissionRequest).where(PermissionRequest.user_id == user_id)
    ).scalar_one()
    rows = (
        session.execute(
            select(PermissionRequest)
            .where(PermissionRequest.user_id == user_id)
            .order_by(PermissionRequest.created_at.desc(), PermissionRequest.id)
            .limit(limit)
            .offset(offset)
        )
        .scalars()
        .all()
    )
    return list(rows), int(total)


def list_grants(
    session: Session, limit: int, offset: int, dataset_id: uuid.UUID | None = None
) -> tuple[list[Permission], int]:
    filters = [] if dataset_id is None else [Permission.dataset_id == dataset_id]
    stmt = select(Permission)
    count_stmt = select(func.count()).select_from(Permission)
    if filters:
        stmt = stmt.where(*filters)
        count_stmt = count_stmt.where(*filters)
    total = session.execute(count_stmt).scalar_one()
    rows = (
        session.execute(stmt.order_by(Permission.role_id, Permission.dataset_id).limit(limit).offset(offset))
        .scalars()
        .all()
    )
    return list(rows), int(total)
