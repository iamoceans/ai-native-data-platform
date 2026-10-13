"""Skill library endpoints: preview, import, versions, enable/disable (design 9).

Capability: `analysis.create` (design 9: skill upload is not a new global admin
surface). Ownership: an uploaded skill is visible and manageable only by its
owner; builtins are read-only for everyone. An id the caller cannot see returns
404, never 403, so guessing a UUID reveals nothing.

Nothing in a package is executed, and the raw archive is never persisted - the
validated text lives in `skill_versions.files`.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import current_auth, get_db, get_settings_dep, get_trace_id, require_csrf
from app.api.dto import (
    SkillDetailResponse,
    SkillListResponse,
    SkillPatch,
    SkillPreviewResponse,
    SkillSummary,
    SkillVersionDetailResponse,
    SkillVersionSummary,
)
from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import ErrorCode, SkillSource
from app.errors import ApiError
from app.repositories import audit as audit_repo
from app.repositories import skills as skills_repo
from app.skills.frontmatter import SkillError
from app.skills.service import current_applicability, import_skill, preview

router = APIRouter(tags=["skills"])

MAX_UPLOAD_BYTES = 4 * 1024 * 1024  # hard read cap before the package limits apply


def _require_capability(auth: AuthContext) -> None:
    if "analysis.create" not in auth.capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, "analysis capability required")


def _raise(exc: SkillError) -> None:
    raise ApiError(exc.code, exc.message, details=exc.details or None)


async def _read_upload(upload: UploadFile, settings: Settings) -> bytes:
    """Read the upload with the configured cap enforced while reading."""
    cap = settings.skill_upload_max_bytes
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(64 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > cap:
            raise ApiError(ErrorCode.SKILL_TOO_LARGE, "the uploaded file exceeds its size limit")
        chunks.append(chunk)
    if total == 0:
        raise ApiError(ErrorCode.VALIDATION_ERROR, "the uploaded file is empty")
    return b"".join(chunks)


def _version_summary(session: Session, row) -> SkillVersionSummary:
    """`compatibility` is the *current* verdict; the stored one is kept beside it.

    Applicability depends on the metric registry, so a version imported before
    its metric existed becomes applicable once that metric is loaded - the
    frozen import-time verdict is history, not the answer to "can I use this
    now".
    """
    return SkillVersionSummary(
        id=row.id,
        version=int(row.version),
        content_hash=row.content_hash,
        manifest=row.manifest or {},
        compatibility=current_applicability(session, row.manifest or {}),
        compatibility_at_import=row.compatibility or {},
        created_at=row.created_at,
    )


def _summary(session: Session, row, *, include_versions: bool = False) -> SkillSummary:
    versions = skills_repo.versions_for(session, row.id)
    latest = versions[-1] if versions else None
    return SkillSummary(
        id=row.id,
        slug=row.slug,
        source=row.source,
        enabled=bool(row.enabled),
        archived_at=row.archived_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        latest_version=_version_summary(session, latest) if latest is not None else None,
        version_count=len(versions),
        versions=[_version_summary(session, item) for item in versions] if include_versions else [],
    )


@router.get("/skills", response_model=SkillListResponse)
def list_skills(
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
    include_archived: bool = False,
) -> SkillListResponse:
    rows = skills_repo.list_visible(session, user_id=auth.user.id, include_archived=include_archived)
    return SkillListResponse(items=[_summary(session, row) for row in rows])


@router.post("/skills/import-preview", response_model=SkillPreviewResponse)
async def preview_skill(
    request: Request,
    file: UploadFile = File(...),
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> SkillPreviewResponse:
    """Full validation with a bounded preview; nothing is stored (design 9)."""
    _require_capability(auth)
    data = await _read_upload(file, settings)
    try:
        report = preview(session, filename=file.filename or "", data=data, settings=settings)
    except SkillError as exc:
        _raise(exc)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="skill.preview",
        resource_type="skill",
        resource_id=report["content_hash"],
        trace_id=get_trace_id(request),
        outcome="success",
        details={"name": report["name"], "applicable": report["compatibility"]["applicable"]},
    )
    return SkillPreviewResponse(**report)


@router.post("/skills", response_model=SkillDetailResponse, status_code=201)
async def create_skill(
    request: Request,
    file: UploadFile = File(...),
    expected_hash: str | None = Form(default=None),
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> SkillDetailResponse:
    _require_capability(auth)
    data = await _read_upload(file, settings)
    try:
        skill, version, created = import_skill(
            session,
            actor_id=auth.user.id,
            filename=file.filename or "",
            data=data,
            expected_hash=expected_hash,
            settings=settings,
        )
    except SkillError as exc:
        _raise(exc)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="skill.create" if created else "skill.replay",
        resource_type="skill",
        resource_id=str(skill.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"slug": skill.slug, "version": int(version.version), "hash": version.content_hash},
    )
    return SkillDetailResponse(
        **_summary(session, skill, include_versions=True).model_dump(),
        body=_body_of(version),
    )


@router.post("/skills/{skill_id}/versions", response_model=SkillVersionDetailResponse, status_code=201)
async def create_version(
    skill_id: uuid.UUID,
    request: Request,
    file: UploadFile = File(...),
    expected_hash: str | None = Form(default=None),
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
    settings: Settings = Depends(get_settings_dep),
) -> SkillVersionDetailResponse:
    _require_capability(auth)
    data = await _read_upload(file, settings)
    try:
        skill, version, created = import_skill(
            session,
            actor_id=auth.user.id,
            filename=file.filename or "",
            data=data,
            expected_hash=expected_hash,
            settings=settings,
            skill_id=skill_id,
        )
    except SkillError as exc:
        _raise(exc)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="skill.version.create" if created else "skill.version.replay",
        resource_type="skill",
        resource_id=str(skill.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"version": int(version.version), "hash": version.content_hash},
    )
    return SkillVersionDetailResponse(
        **_version_summary(session, version).model_dump(), skill_id=skill.id, files=version.files or []
    )


@router.get("/skills/{skill_id}", response_model=SkillDetailResponse)
def get_skill(
    skill_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> SkillDetailResponse:
    row = skills_repo.get_visible_skill(session, skill_id, auth.user.id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "skill not found")
    latest = skills_repo.latest_version(session, row.id)
    return SkillDetailResponse(
        **_summary(session, row, include_versions=True).model_dump(),
        body=_body_of(latest) if latest is not None else None,
    )


@router.get("/skills/{skill_id}/versions/{version_id}", response_model=SkillVersionDetailResponse)
def get_version(
    skill_id: uuid.UUID,
    version_id: uuid.UUID,
    auth: AuthContext = Depends(current_auth),
    session: Session = Depends(get_db),
) -> SkillVersionDetailResponse:
    row = skills_repo.get_visible_skill(session, skill_id, auth.user.id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "skill not found")
    version = skills_repo.get_version(session, version_id)
    if version is None or version.skill_id != row.id:
        raise ApiError(ErrorCode.NOT_FOUND, "skill version not found")
    return SkillVersionDetailResponse(
        **_version_summary(session, version).model_dump(), skill_id=row.id, files=version.files or []
    )


@router.patch("/skills/{skill_id}", response_model=SkillDetailResponse)
def patch_skill(
    skill_id: uuid.UUID,
    payload: SkillPatch,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    session: Session = Depends(get_db),
) -> SkillDetailResponse:
    """Enable, disable or archive. Version content can never be rewritten."""
    _require_capability(auth)
    row = skills_repo.get_visible_skill(session, skill_id, auth.user.id)
    if row is None:
        raise ApiError(ErrorCode.NOT_FOUND, "skill not found")
    if row.source != SkillSource.UPLOAD:
        raise ApiError(ErrorCode.FORBIDDEN, "builtin skills are read-only")
    if payload.enabled is not None:
        skills_repo.set_enabled(session, row, payload.enabled)
    if payload.archived is not None:
        skills_repo.set_archived(session, row, payload.archived)
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="skill.patch",
        resource_type="skill",
        resource_id=str(row.id),
        trace_id=get_trace_id(request),
        outcome="success",
        details={"enabled": payload.enabled, "archived": payload.archived},
    )
    latest = skills_repo.latest_version(session, row.id)
    return SkillDetailResponse(
        **_summary(session, row, include_versions=True).model_dump(),
        body=_body_of(latest) if latest is not None else None,
    )


def _body_of(version) -> str | None:
    for item in version.files or []:
        if item.get("path") == "SKILL.md":
            return str(item.get("content") or "")
    return None
