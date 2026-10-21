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
    QueryDependency,
    QueryJob,
)


def create_session(session: Session, *, user_id: uuid.UUID, title: str) -> AgentSession:
    row = AgentSession(id=uuid.uuid4(), user_id=user_id, title=title)
    session.add(row)
    session.flush()
    return row


def get_session(session: Session, session_id: uuid.UUID) -> AgentSession | None:
    return session.get(AgentSession, session_id)


def get_session_for_update(session: Session, session_id: uuid.UUID) -> AgentSession | None:
    """Lock the session row: message sequence and busy checks allocate under it."""
    return session.execute(
        select(AgentSession)
        .where(AgentSession.id == session_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()


def list_sessions(
    session: Session,
    *,
    user_id: uuid.UUID,
    include_archived: bool,
    limit: int,
    offset: int,
) -> tuple[list[AgentSession], int]:
    filters = [AgentSession.user_id == user_id]
    if not include_archived:
        filters.append(AgentSession.archived_at.is_(None))
    total = session.execute(
        select(func.count()).select_from(AgentSession).where(*filters)
    ).scalar_one()
    rows = session.execute(
        select(AgentSession)
        .where(*filters)
        .order_by(AgentSession.updated_at.desc(), AgentSession.id)
        .limit(limit)
        .offset(offset)
    ).scalars()
    return list(rows), int(total)


def touch_session(session: Session, row: AgentSession) -> None:
    row.updated_at = utcnow()
    session.flush()


def update_session_scope(
    session: Session,
    row: AgentSession,
    *,
    resolved_context: dict[str, Any] | None = None,
    pending_clarification: dict[str, Any] | None = None,
    clear_pending_clarification: bool = False,
    skill_config: dict[str, Any] | None = None,
) -> None:
    """Write scope/skill state and bump `context_version` in one place.

    Every scope-changing write goes through here so the version can never drift
    from the data a client is holding (design 8.3: concurrent version check).
    """
    if resolved_context is not None:
        row.resolved_context = resolved_context
    if pending_clarification is not None:
        row.pending_clarification = pending_clarification
    elif clear_pending_clarification:
        row.pending_clarification = None
    if skill_config is not None:
        row.skill_config = skill_config
    row.context_version = int(row.context_version) + 1
    row.updated_at = utcnow()
    session.flush()


def add_message(
    session: Session,
    *,
    session_id: uuid.UUID,
    role: str,
    content: dict[str, Any],
    turn_id: uuid.UUID | None = None,
    content_schema_version: int = 1,
) -> AgentMessage:
    """Append a message with the next session-scoped sequence number.

    The caller must hold (or this call takes) the session row lock so two
    concurrent writers cannot allocate the same sequence.
    """
    get_session_for_update(session, session_id)
    next_sequence = session.execute(
        select(func.coalesce(func.max(AgentMessage.sequence), 0) + 1).where(
            AgentMessage.session_id == session_id
        )
    ).scalar_one()
    row = AgentMessage(
        id=uuid.uuid4(),
        session_id=session_id,
        role=role,
        content=content,
        turn_id=turn_id,
        sequence=int(next_sequence),
        content_schema_version=content_schema_version,
    )
    session.add(row)
    session.flush()
    return row


def list_messages(
    session: Session, *, session_id: uuid.UUID, limit: int, offset: int
) -> tuple[list[AgentMessage], int]:
    """Stable order: `(sequence, id)`, which the backfill made gap-free.

    Rows written before migration `c4d9a17e6f20` carry sequence 0 after
    conversion; `created_at, id` keeps them in their original order.
    """
    total = session.execute(
        select(func.count()).select_from(AgentMessage).where(AgentMessage.session_id == session_id)
    ).scalar_one()
    rows = session.execute(
        select(AgentMessage)
        .where(AgentMessage.session_id == session_id)
        .order_by(AgentMessage.sequence, AgentMessage.created_at, AgentMessage.id)
        .limit(limit)
        .offset(offset)
    ).scalars()
    return list(rows), int(total)


def list_messages_before(session: Session, *, session_id: uuid.UUID, before: int | None, limit: int):
    """Newest window, returned chronologically; stable under new appends."""
    stmt = select(AgentMessage).where(AgentMessage.session_id == session_id)
    if before is not None:
        stmt = stmt.where(AgentMessage.sequence < before)
    rows = list(session.execute(stmt.order_by(AgentMessage.sequence.desc()).limit(limit + 1)).scalars())
    return list(reversed(rows[:limit])), len(rows) > limit


def message_content(row: AgentMessage) -> dict[str, Any]:
    """Read a message through the compatibility layer (design 8.1).

    Messages written before the conversation layer are free-form dicts
    (`{"question", "analysis_id"}` / `{"analysis_id", "status"}`); the closed
    message DTOs introduced with it carry `content_schema_version >= 1` and are
    returned as-is. The conversion never invents content.
    """
    content = dict(row.content or {})
    if int(row.content_schema_version or 0) >= 1:
        return content
    if row.role == "user":
        converted: dict[str, Any] = {"schema_version": 0, "text": content.get("question", "")}
        if content.get("analysis_id"):
            converted["analysis_id"] = content["analysis_id"]
        if content.get("drilldown"):
            converted["drilldown"] = content["drilldown"]
        return converted
    converted = {
        "schema_version": 0,
        "kind": "analysis",
        "analysis_id": content.get("analysis_id"),
        "status": content.get("status"),
        "report_available": bool(content.get("report_available")),
    }
    return converted


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
    conversation_turn_id: uuid.UUID | None = None,
) -> AnalysisTask:
    row = AnalysisTask(
        id=uuid.uuid4(),
        session_id=session_id,
        user_id=user_id,
        parent_id=parent_id,
        conversation_turn_id=conversation_turn_id,
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


def cancel_task(session: Session, task: AnalysisTask) -> bool:
    """Cancel an unfinished analysis the same way the API does.

    Shared by `POST /analyses/{id}/cancel` and the conversation turn cancel so
    the two paths cannot drift. Returns True when this call changed the status.
    """
    from app.repositories import queries as queries_repo

    # Workers hold the parent lock while creating queries and publishing. Take
    # the same parent -> child order and refresh any API identity-map snapshot.
    locked = get_analysis_for_update(session, task.id)
    if locked is None or locked.status in TERMINAL_ANALYSIS_STATUSES:
        return False
    for query in list_queries(session, locked.id):
        queries_repo.request_cancel(session, query)
    set_status(locked, AnalysisStatus.CANCELLED)
    return True


def get_analysis_for_update(session: Session, analysis_id: uuid.UUID) -> AnalysisTask | None:
    return session.execute(
        select(AnalysisTask).where(AnalysisTask.id == analysis_id).with_for_update()
        .execution_options(populate_existing=True)
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



def active_analysis(session: Session, session_id: uuid.UUID) -> AnalysisTask | None:
    """Read while holding the session lock, including tasks from legacy entry points."""
    return session.execute(
        select(AnalysisTask)
        .where(AnalysisTask.session_id == session_id,
               AnalysisTask.status.not_in(tuple(TERMINAL_ANALYSIS_STATUSES)))
        .order_by(AnalysisTask.created_at, AnalysisTask.id)
        .limit(1)
    ).scalar_one_or_none()

def list_recent_terminal(session: Session, *, limit: int) -> list[AnalysisTask]:
    """Freshest finished analyses first; the learning loop's catch-up source."""
    rows = session.execute(
        select(AnalysisTask)
        .where(AnalysisTask.status.in_(tuple(TERMINAL_ANALYSIS_STATUSES)))
        .order_by(AnalysisTask.finished_at.desc().nullslast(), AnalysisTask.created_at.desc())
        .limit(limit)
    ).scalars()
    return list(rows)


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



def list_dependencies(session: Session, query_ids: list[uuid.UUID]) -> list[QueryDependency]:
    """Datasets a set of queries touched (used by evidence permission checks)."""
    if not query_ids:
        return []
    return list(
        session.execute(
            select(QueryDependency).where(QueryDependency.query_id.in_(query_ids))
        ).scalars()
    )


def list_session_turn_ids(session: Session, session_id: uuid.UUID) -> list[uuid.UUID]:
    from app.models.orm import ConversationTurn

    return list(
        session.execute(
            select(ConversationTurn.id).where(ConversationTurn.session_id == session_id)
        ).scalars()
    )


def list_session_analysis_ids(session: Session, session_id: uuid.UUID) -> list[uuid.UUID]:
    return list(
        session.execute(
            select(AnalysisTask.id).where(AnalysisTask.session_id == session_id)
        ).scalars()
    )


def analysis_dataset_actions_ok(
    session: Session, *, role_ids: list[uuid.UUID], analysis_id: uuid.UUID
) -> bool:
    """Do the roles still hold discover/query on every dataset this analysis read?

    Shared by the evidence endpoints, the explanation layer and the conversation
    transcript: report-derived text must never be served after a revocation
    (design 11.2/11.3).
    """
    from app.constants import DatasetAction
    from app.repositories import datasets as datasets_repo

    query_ids = [row.id for row in list_queries(session, analysis_id)]
    if not query_ids:
        return True
    for dependency in list_dependencies(session, query_ids):
        if not datasets_repo.has_dataset_action(
            session, role_ids, dependency.dataset_id, DatasetAction.QUERY
        ) or not datasets_repo.has_dataset_action(
            session, role_ids, dependency.dataset_id, DatasetAction.DISCOVER
        ):
            return False
    return True
