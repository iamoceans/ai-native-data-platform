"""Persistence for skill identities and their immutable versions (design 8.2)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.constants import SkillSource
from app.ids import utcnow
from app.models.orm import Skill, SkillVersion


def create_skill(
    session: Session,
    *,
    slug: str,
    source: str,
    owner_user_id: uuid.UUID | None,
    enabled: bool = True,
) -> Skill:
    row = Skill(
        id=uuid.uuid4(),
        owner_user_id=owner_user_id,
        source=source,
        slug=slug,
        enabled=enabled,
    )
    session.add(row)
    session.flush()
    return row


def get_skill(session: Session, skill_id: uuid.UUID) -> Skill | None:
    return session.get(Skill, skill_id)


def get_visible_skill(session: Session, skill_id: uuid.UUID, user_id: uuid.UUID) -> Skill | None:
    """Builtin skills are visible to everyone; uploads only to their owner."""
    row = session.get(Skill, skill_id)
    if row is None:
        return None
    if row.source == SkillSource.BUILTIN or row.owner_user_id == user_id:
        return row
    return None


def get_builtin_by_slug(session: Session, slug: str) -> Skill | None:
    return session.execute(
        select(Skill).where(
            Skill.slug == slug, Skill.source == SkillSource.BUILTIN
        )
    ).scalar_one_or_none()


def get_owned_by_slug(session: Session, *, owner_user_id: uuid.UUID, slug: str) -> Skill | None:
    return session.execute(
        select(Skill).where(
            Skill.owner_user_id == owner_user_id, Skill.slug == slug
        )
    ).scalar_one_or_none()


def list_visible(
    session: Session, *, user_id: uuid.UUID, include_archived: bool = False
) -> list[Skill]:
    filters = [
        (Skill.owner_user_id == user_id) | (Skill.source == SkillSource.BUILTIN)
    ]
    if not include_archived:
        filters.append(Skill.archived_at.is_(None))
    return list(
        session.execute(
            select(Skill)
            .where(*filters)
            .order_by(Skill.source, Skill.slug, Skill.id)
        ).scalars()
    )


def count_owned(session: Session, user_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(Skill).where(Skill.owner_user_id == user_id)
        ).scalar_one()
    )


def set_enabled(session: Session, row: Skill, enabled: bool) -> None:
    row.enabled = enabled
    row.updated_at = utcnow()
    session.flush()


def set_archived(session: Session, row: Skill, archived: bool) -> None:
    row.archived_at = utcnow() if archived else None
    row.updated_at = utcnow()
    session.flush()


def versions_for(session: Session, skill_id: uuid.UUID) -> list[SkillVersion]:
    return list(
        session.execute(
            select(SkillVersion)
            .where(SkillVersion.skill_id == skill_id)
            .order_by(SkillVersion.version)
        ).scalars()
    )


def latest_version(session: Session, skill_id: uuid.UUID) -> SkillVersion | None:
    return session.execute(
        select(SkillVersion)
        .where(SkillVersion.skill_id == skill_id)
        .order_by(SkillVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_version(session: Session, version_id: uuid.UUID) -> SkillVersion | None:
    return session.get(SkillVersion, version_id)


def find_version_by_hash(
    session: Session, *, skill_id: uuid.UUID, content_hash: str
) -> SkillVersion | None:
    return session.execute(
        select(SkillVersion).where(
            SkillVersion.skill_id == skill_id, SkillVersion.content_hash == content_hash
        )
    ).scalar_one_or_none()


def create_version(
    session: Session,
    *,
    skill_id: uuid.UUID,
    manifest: dict[str, Any],
    files: list[dict[str, Any]],
    content_hash: str,
    compatibility: dict[str, Any],
    created_by: uuid.UUID,
) -> SkillVersion:
    """Append the next immutable version; content is never updated afterwards."""
    next_version = int(
        session.execute(
            select(func.coalesce(func.max(SkillVersion.version), 0) + 1).where(
                SkillVersion.skill_id == skill_id
            )
        ).scalar_one()
    )
    row = SkillVersion(
        id=uuid.uuid4(),
        skill_id=skill_id,
        version=next_version,
        manifest=manifest,
        files=files,
        content_hash=content_hash,
        compatibility=compatibility,
        created_by=created_by,
    )
    session.add(row)
    session.flush()
    return row


def version_count(session: Session, skill_id: uuid.UUID) -> int:
    return int(
        session.execute(
            select(func.count()).select_from(SkillVersion).where(SkillVersion.skill_id == skill_id)
        ).scalar_one()
    )
