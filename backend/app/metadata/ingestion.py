"""Ingestion task submission (spec section 10.1).

The API creates a QUEUED ``ingestion_tasks`` row and enqueues a task_queue item
in the same transaction. Only one active ingestion per datasource is allowed
(spec 10.1). Actual execution happens in the ingestion worker image.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import ErrorCode, IngestionStatus
from app.errors import ApiError
from app.models.orm import Datasource, IngestionTask, TaskQueue
from app.metadata.recipes import build_recipe
from app.repositories import audit as audit_repo
from app.repositories import queue as queue_repo


def request_sync(
    session: Session,
    *,
    actor: AuthContext,
    datasource: Datasource,
    settings: Settings,
    trace_id: str,
) -> IngestionTask:
    if not datasource.enabled:
        raise ApiError(ErrorCode.DATASOURCE_UNAVAILABLE, "datasource is disabled")
    active = session.execute(
        select(IngestionTask).where(
            IngestionTask.datasource_id == datasource.id,
            IngestionTask.status.in_([IngestionStatus.QUEUED, IngestionStatus.RUNNING]),
        )
    ).scalar_one_or_none()
    if active is not None:
        raise ApiError(
            ErrorCode.CONFLICT,
            "an ingestion task is already queued or running for this datasource",
            details={"ingestion_task_id": str(active.id)},
        )
    rendered = build_recipe(session, datasource=datasource, settings=settings)
    task = IngestionTask(
        id=uuid.uuid4(),
        datasource_id=datasource.id,
        requested_by=actor.user.id,
        status=IngestionStatus.QUEUED,
        recipe_hash=rendered.recipe_hash,
        ingestion_version=settings.datahub_version,
    )
    # The recipe payload is written to the shared ingestion work volume before
    # the database row: the runner must never see a queued task without its
    # payload. No credentials are written (env placeholders only).
    write_task_payload(
        settings=settings,
        task_id=task.id,
        payload={
            "schema_version": 1,
            "task_id": str(task.id),
            "datasource_id": str(datasource.id),
            "kind": datasource.kind,
            "secret_ref": datasource.secret_ref,
            "platform_instance": rendered.platform_instance,
            "gms_url": settings.datahub_gms_url,
            "recipe": rendered.recipe,
            "expected_datasets": rendered.expected_datasets,
            "custom_properties": _semantic_properties(session, datasource, settings),
            "recipe_hash": rendered.recipe_hash,
        },
    )
    session.add(task)
    session.flush()
    queue_repo.enqueue(session, kind="ingestion", resource_id=task.id)
    audit_repo.add_audit(
        session,
        actor_id=actor.user.id,
        action="datasource.sync",
        resource_type="datasource",
        resource_id=str(datasource.id),
        trace_id=trace_id,
        outcome="queued",
        details={
            "ingestion_task_id": str(task.id),
            "platform_instance": rendered.platform_instance,
            "expected_datasets": len(rendered.expected_datasets),
            "recipe_hash": rendered.recipe_hash[:16],
        },
    )
    return task


def write_task_payload(*, settings: Settings, task_id: uuid.UUID, payload: dict) -> None:
    import json

    directory = settings.ingestion_work_dir / str(task_id)
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "payload.json").write_text(
            json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8"
        )
    except OSError as exc:
        raise ApiError(
            ErrorCode.STORAGE_UNAVAILABLE, f"could not write the ingestion payload: {exc}"
        ) from exc


def _semantic_properties(session: Session, datasource: Datasource, settings: Settings) -> dict:
    from app.metadata.recipes import _common_properties  # local import to avoid a cycle

    properties, _expected = _common_properties(session, datasource, settings)
    return properties


def ingestion_to_response(task: IngestionTask) -> dict:
    return {
        "id": task.id,
        "datasource_id": task.datasource_id,
        "status": task.status,
        "created_at": task.created_at,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "summary": task.summary,
        "error": task.error,
    }


def cleanup_stale_queue_rows(session: Session) -> int:
    """Consistency helper: queue rows whose ingestion task is already terminal."""
    rows = session.execute(select(TaskQueue).where(TaskQueue.kind == "ingestion")).scalars().all()
    fixed = 0
    for row in rows:
        task = session.get(IngestionTask, row.resource_id)
        if task is None:
            continue
        if task.status == IngestionStatus.SUCCEEDED and row.state != "DONE":
            row.state = "DONE"
            fixed += 1
        elif task.status in (IngestionStatus.FAILED, IngestionStatus.LOST) and row.state != "FAILED":
            row.state = "FAILED"
            fixed += 1
    session.flush()
    return fixed
