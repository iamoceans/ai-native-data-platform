"""Persistence operations for agent sessions and analysis tasks."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent.state import AnalysisStatus, TERMINAL_ANALYSIS_STATUSES, transition
from app.ids import utcnow
from app.models.orm import (
    AgentMessage,
    AgentSession,
    AnalysisArtifact,
    AnalysisStep,
    AnalysisTask,
    QueryJob,
)


def create_session(session: Session, *, user_id: uuid.UUID, title: str) -> AgentSession:
    row = AgentSession(id=uuid.uuid4(), user_id=user_id, title=title)
    session.add(row)
    session.flush()
    return row


def get_session(session: Session, session_id: uuid.UUID) -> AgentSession | None:
    return session.get(AgentSession, session_id)


def add_message(
    session: Session, *, session_id: uuid.UUID, role: str, content: dict[str, Any]
) -> AgentMessage:
    row = AgentMessage(
        id=uuid.uuid4(), session_id=session_id, role=role, content=content
    )
    session.add(row)
    session.flush()
    return row


def list_messages(
    session: Session, *, session_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[AgentMessage], int]:
    total = session.execute(
        select(func.count()).select_from(AgentMessage).where(AgentMessage.session_id == session_id)
    ).scalar_one()
    rows = session.execute(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.created_at, AgentMessage.id)
        .limit(limit)
        .offset(offset)
    ).scalars()
    return list(rows), int(total)


def create_analysis(
    session: Session,
    *,
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    question: str,
    context: dict[str, Any],
    parent_id: uuid.UUID | None,
    budget: dict[str, Any],
    policy_revision: int,
) -> AnalysisTask:
    row = AnalysisTask(
        id=uuid.uuid4(),
        session_id=session_id,
        user_id=user_id,
        parent_id=parent_id,
        status=AnalysisStatus.CREATED,
        question=question,
        context=context,
        plan={},
        budget=budget,
        state={"schema_version": 1, "warnings": [], "pending_query_ids": []},
        checkpoint_version=0,
        policy_revision=policy_revision,
    )
    session.add(row)
    session.flush()
    return row


def get_analysis(session: Session, analysis_id: uuid.UUID) -> AnalysisTask | None:
    return session.get(AnalysisTask, analysis_id)


def get_analysis_for_update(session: Session, analysis_id: uuid.UUID) -> AnalysisTask | None:
    return session.execute(
        select(AnalysisTask).where(AnalysisTask.id == analysis_id).with_for_update()
    ).scalar_one_or_none()


def list_for_user(
    session: Session,
    *,
    user_id: uuid.UUID,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[AnalysisTask], int]:
    filters = [AnalysisTask.user_id == user_id]
    if status:
        filters.append(AnalysisTask.status == status)
    total = session.execute(
        select(func.count()).select_from(AnalysisTask).where(*filters)
    ).scalar_one()
    rows = session.execute(
        select(AnalysisTask)
        .where(*filters)
        .order_by(AnalysisTask.created_at.desc(), AnalysisTask.id)
        .limit(limit)
        .offset(offset)
    ).scalars()
    return list(rows), int(total)


def set_status(task: AnalysisTask, target: str) -> None:
    task.status = transition(task.status, target)
    task.checkpoint_version = int(task.checkpoint_version) + 1
    if task.status in TERMINAL_ANALYSIS_STATUSES:
        task.finished_at = utcnow()


def add_step(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    step_key: str,
    tool_name: str,
    arguments: dict[str, Any],
    output_refs: dict[str, Any],
    status: str = "PENDING",
) -> AnalysisStep:
    row = AnalysisStep(
        id=uuid.uuid4(),
        analysis_id=analysis_id,
        step_key=step_key,
        status=status,
        tool_name=tool_name,
        arguments=arguments,
        output_refs=output_refs,
    )
    session.add(row)
    session.flush()
    return row


def list_steps(session: Session, analysis_id: uuid.UUID) -> list[AnalysisStep]:
    return list(
        session.execute(
            select(AnalysisStep)
            .where(AnalysisStep.analysis_id == analysis_id)
            .order_by(AnalysisStep.step_key)
        ).scalars()
    )


def add_artifact(
    session: Session,
    *,
    analysis_id: uuid.UUID,
    kind: str,
    content: dict[str, Any],
    dependency_query_ids: list[str],
    content_hash: str,
    artifact_id: uuid.UUID | None = None,
) -> AnalysisArtifact:
    row = AnalysisArtifact(
        id=artifact_id or uuid.uuid4(),
        analysis_id=analysis_id,
        kind=kind,
        schema_version=1,
        content=content,
        dependency_query_ids=dependency_query_ids,
        content_hash=content_hash,
    )
    session.add(row)
    session.flush()
    return row


def list_artifacts(session: Session, analysis_id: uuid.UUID) -> list[AnalysisArtifact]:
    return list(
        session.execute(
            select(AnalysisArtifact).where(AnalysisArtifact.analysis_id == analysis_id)
        ).scalars()
    )


def get_artifact(session: Session, artifact_id: uuid.UUID) -> AnalysisArtifact | None:
    return session.get(AnalysisArtifact, artifact_id)


def find_artifact(
    session: Session, analysis_id: uuid.UUID, *, content_hash: str
) -> AnalysisArtifact | None:
    """Idempotency guard: re-entering synthesis must not duplicate evidence."""
    return (
        session.execute(
            select(AnalysisArtifact).where(
                AnalysisArtifact.analysis_id == analysis_id,
                AnalysisArtifact.content_hash == content_hash,
            )
        )
        .scalars()
        .first()
    )


def list_queries(session: Session, analysis_id: uuid.UUID) -> list[QueryJob]:
    return list(
        session.execute(
            select(QueryJob)
            .where(QueryJob.analysis_id == analysis_id)
            .order_by(QueryJob.created_at, QueryJob.id)
        ).scalars()
    )

