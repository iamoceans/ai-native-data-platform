"""Read-time, self-scoped account access; never returns datasource credentials."""
import uuid
from datetime import datetime
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.engine import Row

from app.models.orm import AuditLog, Dataset, Datasource, Permission, Role, UserRole


def effective_grants(
    session: Session, *, user_id: uuid.UUID, role_ids: list[uuid.UUID], now: datetime
) -> Sequence[Row]:
    return session.execute(
        select(Dataset, Datasource.name, Datasource.kind, Datasource.enabled,
               Role.name, Permission.action, Permission.expires_at)
        .join(Permission, Permission.dataset_id == Dataset.id)
        .join(Role, Role.id == Permission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .join(Datasource, Datasource.id == Dataset.datasource_id)
        .where(UserRole.user_id == user_id, Role.id.in_(role_ids), Dataset.active.is_(True),
               or_(Permission.expires_at.is_(None), Permission.expires_at > now))
        .order_by(Datasource.name, Dataset.catalog_name, Dataset.schema_name,
                  Dataset.object_name, Dataset.id, Role.name, Permission.action)
    ).all()


def last_successful_login(session: Session, *, user_id: uuid.UUID) -> datetime | None:
    return session.scalar(select(func.max(AuditLog.created_at)).where(
        AuditLog.actor_id == user_id, AuditLog.action == "auth.login", AuditLog.outcome == "success",
    ))
