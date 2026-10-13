"""Skill lifecycle: preview, import, versions, builtin sync (design 7, 12).

The package parser is pure; this service adds what needs the database:

* **Applicability.** A declared metric must exist and each preferred dimension
  must be in that metric's allowlist. A package that names unknown things is
  still saved (the design allows "not applicable in this environment"), but it
  is recorded as not applicable and can never be auto-loaded for that metric.
* **Quotas and identity.** Per-user skill count, versions per skill, and the
  rule that an upload may not shadow a builtin slug.
* **Immutability.** Importing the same content hash returns the existing
  version; different content creates the next version.

Nothing here executes package content, and no file is written outside the
database.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.constants import ErrorCode, SkillSource
from app.ids import utcnow
from app.models.orm import Skill, SkillVersion
from app.repositories import skills as skills_repo
from app.skills.frontmatter import SkillError
from app.skills.package import ParsedSkill, parse_skill_package

PREVIEW_BODY_CHARS = 2_000


def _metric_definition(session: Session, metric_key: str):
    """The active metric contract, validated - the same shape the runner uses."""
    from sqlalchemy import select

    from app.metrics.registry import MetricDefinitionModel
    from app.models.orm import MetricDefinition

    row = session.execute(
        select(MetricDefinition)
        .where(MetricDefinition.metric_key == metric_key, MetricDefinition.active.is_(True))
        .order_by(MetricDefinition.version.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    return MetricDefinitionModel.model_validate(row.definition)


def applicability_from_declaration(
    session: Session, *, metric_keys: list[str], preferred_dimensions: list[str]
) -> dict[str, Any]:
    """How far a declared extension matches the *current* environment (design 7.2).

    Read-time evaluation matters because the metric registry changes: a skill
    imported before its metric existed must become applicable once the metric is
    loaded, not stay "unknown" forever.
    """
    unknown_metrics: list[str] = []
    invalid_dimensions: dict[str, list[str]] = {}
    for metric_key in metric_keys:
        definition = _metric_definition(session, metric_key)
        if definition is None:
            unknown_metrics.append(metric_key)
            continue
        allowed = set(definition.allowed_dimensions or [])
        rejected = sorted(set(preferred_dimensions) - allowed)
        if rejected:
            invalid_dimensions[metric_key] = rejected
    return {
        "applicable": not unknown_metrics and not invalid_dimensions,
        "generic": not metric_keys and not preferred_dimensions,
        "unknown_metric_keys": unknown_metrics,
        "invalid_dimensions": invalid_dimensions,
        "checked_at": utcnow().isoformat(),
    }


def current_applicability(session: Session, manifest: dict[str, Any]) -> dict[str, Any]:
    """The verdict for a stored version, evaluated against the live registry."""
    return applicability_from_declaration(
        session,
        metric_keys=[str(item) for item in manifest.get("metric_keys") or []],
        preferred_dimensions=[str(item) for item in manifest.get("preferred_dimensions") or []],
    )


def applicability(session: Session, parsed: ParsedSkill) -> dict[str, Any]:
    """Record how far this package matches this environment (design 7.2)."""
    unknown_metrics: list[str] = []
    invalid_dimensions: dict[str, list[str]] = {}
    for metric_key in parsed.metric_keys:
        definition = _metric_definition(session, metric_key)
        if definition is None:
            unknown_metrics.append(metric_key)
            continue
        allowed = set(definition.allowed_dimensions or [])
        rejected = sorted(set(parsed.preferred_dimensions) - allowed)
        if rejected:
            invalid_dimensions[metric_key] = rejected
    generic = not parsed.metric_keys and not parsed.preferred_dimensions
    return {
        "applicable": not unknown_metrics and not invalid_dimensions,
        "generic": generic,
        "unknown_metric_keys": unknown_metrics,
        "invalid_dimensions": invalid_dimensions,
        "checked_at": utcnow().isoformat(),
    }


def preview(session: Session, *, filename: str, data: bytes, settings: Settings) -> dict[str, Any]:
    """Validate a package end to end and describe it; nothing is persisted."""
    parsed = parse_skill_package(filename=filename, data=data, settings=settings)
    report = applicability(session, parsed)
    return {
        "name": parsed.name,
        "description": parsed.description,
        "content_hash": parsed.content_hash,
        "manifest": parsed.manifest,
        "compatibility": report,
        "metric_keys": list(parsed.metric_keys),
        "preferred_dimensions": list(parsed.preferred_dimensions),
        "required_inputs": list(parsed.required_inputs),
        "references": list(parsed.references),
        "body_preview": parsed.body[:PREVIEW_BODY_CHARS],
        "body_truncated": len(parsed.body) > PREVIEW_BODY_CHARS,
        "files": [{"path": item.path, "size": item.size, "sha256": item.sha256} for item in parsed.files],
    }


def import_skill(
    session: Session,
    *,
    actor_id: uuid.UUID,
    filename: str,
    data: bytes,
    expected_hash: str | None,
    settings: Settings,
    skill_id: uuid.UUID | None = None,
) -> tuple[Skill, SkillVersion, bool]:
    """Create a personal skill with its first version, or add a version to one.

    Returns `(skill, version, created)` where `created` is False when the same
    content was already imported.
    """
    parsed = parse_skill_package(filename=filename, data=data, settings=settings)
    if expected_hash is not None and expected_hash != parsed.content_hash:
        raise SkillError(
            ErrorCode.SKILL_INVALID,
            "the uploaded file changed since the preview; upload it again",
            details={"expected_hash": expected_hash, "actual_hash": parsed.content_hash},
        )

    if skill_id is None:
        existing = skills_repo.get_owned_by_slug(
            session, owner_user_id=actor_id, slug=parsed.name
        )
        if existing is None:
            if skills_repo.get_builtin_by_slug(session, parsed.name) is not None:
                raise SkillError(
                    ErrorCode.CONFLICT,
                    f"'{parsed.name}' is a builtin skill name; choose another name",
                )
            if skills_repo.count_owned(session, actor_id) >= settings.skills_per_user_max:
                raise SkillError(
                    ErrorCode.SKILL_TOO_LARGE,
                    f"you already have the maximum of {settings.skills_per_user_max} skills",
                )
            skill = skills_repo.create_skill(
                session, slug=parsed.name, source=SkillSource.UPLOAD, owner_user_id=actor_id
            )
        else:
            skill = existing
    else:
        skill = skills_repo.get_visible_skill(session, skill_id, actor_id)
        if skill is None:
            raise SkillError(ErrorCode.NOT_FOUND, "skill not found")
        if skill.source != SkillSource.UPLOAD:
            raise SkillError(ErrorCode.FORBIDDEN, "builtin skills are read-only")
        if skill.slug != parsed.name:
            raise SkillError(
                ErrorCode.SKILL_INVALID,
                "a new version must keep the skill's name in its front matter",
                details={"expected": skill.slug, "found": parsed.name},
            )

    duplicate = skills_repo.find_version_by_hash(
        session, skill_id=skill.id, content_hash=parsed.content_hash
    )
    if duplicate is not None:
        return skill, duplicate, False

    if skills_repo.version_count(session, skill.id) >= settings.skill_versions_max:
        raise SkillError(
            ErrorCode.SKILL_TOO_LARGE,
            f"this skill already has the maximum of {settings.skill_versions_max} versions",
        )

    report = applicability(session, parsed)
    version = skills_repo.create_version(
        session,
        skill_id=skill.id,
        manifest=parsed.manifest,
        files=[
            {"path": item.path, "media_type": item.media_type, "content": item.content,
             "sha256": item.sha256}
            for item in parsed.files
        ],
        content_hash=parsed.content_hash,
        compatibility=report,
        created_by=actor_id,
    )
    skill.updated_at = utcnow()
    session.flush()
    return skill, version, True


def sync_builtins(session: Session, *, settings: Settings) -> dict[str, int]:
    """Load `metadata/skills/*.md` as builtin identities and versions (design 12).

    Idempotent: identical content is skipped, changed content becomes the next
    version, and a builtin is never owned by a user. A missing directory is an
    error, not an empty success: `metadata_dir` is relative to the working
    directory, and silently loading nothing is how a deployment ends up with an
    empty skill library while the command reports success.
    """
    skills_dir = settings.metadata_dir / "skills"
    if not skills_dir.is_dir():
        raise SkillError(
            ErrorCode.SERVICE_NOT_READY,
            f"no skill directory at {skills_dir} (metadata_dir is relative to the "
            "working directory; run from the repository root or set AIND_METADATA_DIR)",
        )
    created = updated = skipped = 0
    for path in sorted(skills_dir.glob("*.md")):
        data = path.read_bytes()
        parsed = parse_skill_package(filename="SKILL.md", data=data, settings=settings)
        skill = skills_repo.get_builtin_by_slug(session, parsed.name)
        if skill is None:
            skill = skills_repo.create_skill(
                session, slug=parsed.name, source=SkillSource.BUILTIN, owner_user_id=None
            )
            created += 1
        if skills_repo.find_version_by_hash(
            session, skill_id=skill.id, content_hash=parsed.content_hash
        ) is not None:
            skipped += 1
            continue
        report = applicability(session, parsed)
        skills_repo.create_version(
            session,
            skill_id=skill.id,
            manifest=parsed.manifest,
            files=[
                {"path": item.path, "media_type": item.media_type, "content": item.content,
                 "sha256": item.sha256}
                for item in parsed.files
            ],
            content_hash=parsed.content_hash,
            compatibility=report,
            created_by=_system_actor(session),
        )
        updated += 1
    return {"created": created, "updated": updated, "skipped": skipped}


def _system_actor(session: Session) -> uuid.UUID:
    """Builtin versions need a `created_by`; use the bootstrap administrator."""
    from app.repositories import users as users_repo

    for username in ("admin", "system"):
        user = users_repo.get_user_by_username(session, username)
        if user is not None:
            return user.id
    raise SkillError(
        ErrorCode.SERVICE_NOT_READY,
        "no administrator exists yet; run bootstrap before loading builtin skills",
    )
