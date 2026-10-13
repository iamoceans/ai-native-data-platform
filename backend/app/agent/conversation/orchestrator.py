"""Conversation orchestration: turn lifecycle over the governed analysis runner.

Design: `docs/superpowers/specs/2026-10-13-askdata-conversation-skills-design.md`
sections 6, 7.5 and 10. One turn = one accepted user input. It resolves a scope
(the model proposes a patch, the server merges and validates), loads the skills
the session may use, and then either replies (clarify / explain / help /
unsupported) or submits exactly one analysis through the existing governed runner.

Four properties are load-bearing:

* **The model decides nothing final.** It returns a closed decision; the patch it
  proposes is validated against the metric registry and merged by the server, and
  its prose may not carry a figure the evidence does not already contain.
* **The model call runs outside any database transaction.** Nothing holds a row
  lock while the model answers, so a cancel stays responsive (design 10.1).
* **A lease is never held while waiting.** A turn that submitted an analysis stays
  in ANALYZING; the reconciler closes it when the analysis settles.
* **A cancelled turn publishes nothing.** The turn row is re-read under its lock
  after the model call, so a late reply is discarded.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy.orm import Session

from app.agent.budget import BudgetExceeded
from app.agent.conversation import context as scope
from app.agent.conversation import explain as evidence
from app.agent.conversation import intent as intent_layer
from app.agent.conversation import skills as skill_loader
from app.agent.conversation.budget import TurnBudget
from app.agent.llm import resolve_provider
from app.agent.state import TERMINAL_ANALYSIS_STATUSES
from app.config import Settings
from app.constants import (
    TERMINAL_CONVERSATION_TURN_STATUSES,
    ConversationTurnStatus,
    ErrorCode,
)
from app.ids import utcnow
from app.models.orm import AgentMessage, AnalysisTask, ConversationTurn
from app.repositories import analyses as analyses_repo
from app.repositories import conversation as conversation_repo
from app.repositories import events as events_repo
from app.repositories import memory as memory_repo
from app.repositories import policy as policy_repo
from app.repositories import queue as queue_repo
from app.repositories import users as users_repo

logger = logging.getLogger(__name__)

PROMPT_VERSION = "conversation-v1"
HISTORY_LIMIT = 12
ANALYSIS_HISTORY_LIMIT = 5


# ---------------------------------------------------------------------------
# replies (closed shapes; numbers only ever come from stored artifacts)
# ---------------------------------------------------------------------------
def clarify_reply(missing: list[str], questions: list[str] | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "clarify",
        "missing": missing,
        "questions": list(questions or []),
        "text": "请补充分析口径：" + "、".join(missing) if missing else "请补充需要的信息。",
    }


def help_reply(prose: str | None = None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "help",
        "text": prose
        or (
            "我可以对已登记的指标做受治理的对比分析。请提供指标、基准期间与当前期间，"
            "例如：ads_revenue 在 2026-09-11 相比 2026-09-10 的变化。"
        ),
    }


def unsupported_reply(reason: str | None) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "unsupported",
        "text": reason or "这个请求超出了平台当前支持的范围。",
    }


def result_reply(
    analysis: AnalysisTask, *, skill_record: dict[str, Any] | None = None
) -> dict[str, Any]:
    report = analysis.final_report or {}
    return {
        "schema_version": 1,
        "kind": "analysis_result",
        "analysis_id": str(analysis.id),
        "status": analysis.status,
        "partial": analysis.status == "PARTIAL",
        "claims": [
            {"id": claim.get("id"), "kind": claim.get("kind"), "text": claim.get("text")}
            for claim in (report.get("claims") or [])
        ],
        "limitations": list(report.get("limitations") or []),
        "skills": skill_record or {},
    }


def failure_reply(error: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "failure",
        "code": error.get("code", ErrorCode.EXECUTION_ERROR),
        "message": error.get("message", "分析未能完成"),
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _turn_event(
    session: Session, turn: ConversationTurn, event_type: str, payload: dict[str, Any]
) -> None:
    events_repo.add_event(
        session,
        resource_kind="conversation_turn",
        resource_id=turn.id,
        event_type=event_type,
        payload={"turn_id": str(turn.id), "session_id": str(turn.session_id), **payload},
    )


def _write_assistant(
    session: Session, turn: ConversationTurn, content: dict[str, Any]
) -> AgentMessage:
    return analyses_repo.add_message(
        session,
        session_id=turn.session_id,
        role="assistant",
        content=content,
        turn_id=turn.id,
        content_schema_version=1,
    )


def _close(
    session: Session,
    turn: ConversationTurn,
    *,
    status: str,
    content: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
) -> None:
    message_id = _write_assistant(session, turn, content).id if content is not None else None
    conversation_repo.finish(
        session, turn, status=status, assistant_message_id=message_id, error=error
    )
    _turn_event(session, turn, "turn.state.changed", {"status": str(turn.status)})


def _user_content(session: Session, turn: ConversationTurn) -> dict[str, Any]:
    row = session.get(AgentMessage, turn.user_message_id)
    return analyses_repo.message_content(row) if row is not None else {}


def _history(session: Session, turn: ConversationTurn) -> list[dict[str, Any]]:
    """Recent messages as a digest; the question text is the only free text."""
    rows, _ = analyses_repo.list_messages(
        session, session_id=turn.session_id, limit=HISTORY_LIMIT, offset=0
    )
    return [
        {
            "role": row.role,
            "kind": (row.content or {}).get("kind"),
            "text": (row.content or {}).get("text") or (row.content or {}).get("question"),
        }
        for row in rows
    ][-HISTORY_LIMIT:]


def _metric_table(session: Session) -> dict[str, set[str]]:
    """Registered metrics and their allowlisted dimensions (the validation source)."""
    from sqlalchemy import select

    from app.metrics.registry import MetricDefinitionModel
    from app.models.orm import MetricDefinition

    rows = session.execute(
        select(MetricDefinition).where(MetricDefinition.active.is_(True))
    ).scalars()
    table: dict[str, set[str]] = {}
    for row in rows:
        try:
            model = MetricDefinitionModel.model_validate(row.definition)
        except Exception:  # a malformed definition is simply not offered
            continue
        table[row.metric_key] = set(model.allowed_dimensions or [])
    return table


def _recent_analyses(session: Session, turn: ConversationTurn) -> list[dict[str, Any]]:
    rows, _ = analyses_repo.list_for_user(
        session, user_id=turn.user_id, status=None, limit=ANALYSIS_HISTORY_LIMIT, offset=0
    )
    return [
        {
            "analysis_id": str(item.id),
            "status": item.status,
            "question": item.question,
            "claims": [
                {"id": claim.get("id"), "kind": claim.get("kind"), "text": claim.get("text")}
                for claim in ((item.final_report or {}).get("claims") or [])
            ],
            "limitations": list((item.final_report or {}).get("limitations") or []),
        }
        for item in rows
        if item.session_id == turn.session_id
    ]


def _focus_analysis_id(
    session: Session, turn: ConversationTurn, session_row
) -> uuid.UUID | None:
    focus = (session_row.resolved_context or {}).get("focus_analysis_id")
    if focus:
        try:
            return uuid.UUID(str(focus))
        except ValueError:
            pass
    parent = _user_content(session, turn).get("parent_analysis_id")
    if parent:
        try:
            return uuid.UUID(str(parent))
        except ValueError:
            pass
    return None


def _memory_brief(session: Session, metric_key: str | None) -> list[dict[str, Any]]:
    if not metric_key:
        return []
    rows = memory_repo.list_for_metric(session, metric_key=metric_key, limit=5)
    return [
        {"id": str(row.id), "status": row.status, "statement": row.statement} for row in rows
    ]


def _evidence_identifiers(session: Session, turn: ConversationTurn, session_row) -> set[str]:
    """Tokens model prose may quote: segment names and ids already in evidence."""
    allowed: set[str] = set()
    for item in _recent_analyses(session, turn):
        for claim in item["claims"]:
            for token in str(claim.get("text") or "").split():
                allowed.add(token.strip("，。、（）()：:%"))
    focus = _focus_analysis_id(session, turn, session_row)
    if focus is not None:
        analysis = analyses_repo.get_analysis(session, focus)
        if analysis is not None:
            observations = (analysis.state or {}).get("observations") or {}
            for segment in observations.get("segments") or []:
                for value in segment.get("key") or []:
                    allowed.add(str(value))
    return {item for item in allowed if item}


def resolve_scope_patch(
    base: dict[str, Any], user_content: dict[str, Any], model_patch: dict[str, Any] | None
) -> tuple[dict[str, Any], list[str]]:
    """Merge UI values, then the model's validated patch, over the confirmed scope."""
    merged = scope.merge_scope_patch(base, user_content.get("scope_patch"), origin="ui")
    if model_patch:
        merged = scope.merge_scope_patch(merged, model_patch, origin="model")
    return merged, scope.missing_required_fields(merged)


def rule_decision(
    merged: dict[str, Any], missing: list[str]
) -> intent_layer.ConversationDecision:
    """The deterministic path: the structured scope decides, nothing is guessed."""
    if not missing:
        return intent_layer.ConversationDecision(intent="analyze")
    if merged.get("metric_key") or merged.get("baseline_start"):
        return intent_layer.ConversationDecision(intent="clarify", clarify_questions=[])
    return intent_layer.ConversationDecision(intent="help")


def _fail(session: Session, turn: ConversationTurn, *, code: str, message: str) -> None:
    error = {"code": code, "message": message}
    _close(
        session, turn, status=ConversationTurnStatus.FAILED,
        content=failure_reply(error), error=error,
    )


def _submit_analysis(
    session: Session,
    turn: ConversationTurn,
    merged: dict[str, Any],
    settings: Settings,
    budget: TurnBudget,
    *,
    skill_block: str,
) -> None:
    """Create the one analysis this turn owns, with the turn's remaining budget."""
    revision = policy_repo.get_revision(session)
    analysis = analyses_repo.create_analysis(
        session,
        session_id=turn.session_id,
        user_id=turn.user_id,
        question=str(_user_content(session, turn).get("text") or ""),
        context=scope.scope_to_analysis_context(merged),
        parent_id=None,
        budget=budget.analysis_sub_budget(settings),
        policy_revision=int(revision),
        conversation_turn_id=turn.id,
    )
    if skill_block:
        # Advisory context for the planner; the allowlist still decides legality.
        state = dict(analysis.state or {})
        state["conversation_skill_block"] = skill_block
        state["conversation_skill_snapshot"] = turn.skill_snapshot
        analysis.state = state
    queue_repo.enqueue(session, kind="analysis", resource_id=analysis.id)
    turn.context_after = merged
    turn.budget = budget.as_dict()
    turn.updated_at = utcnow()
    session.flush()
    _turn_event(session, turn, "analysis.linked", {"analysis_id": str(analysis.id)})


def _explanation(
    session: Session, turn: ConversationTurn, decision, session_row
) -> dict[str, Any]:
    """Answer from stored evidence; never run a query (design 6.5)."""
    role_ids = users_repo.role_ids_for_user(session, turn.user_id)
    quotes, dropped = evidence.resolve_references(
        session,
        user_id=turn.user_id,
        role_ids=role_ids,
        session_id=turn.session_id,
        references=list(decision.references),
        focus_analysis_id=_focus_analysis_id(session, turn, session_row),
    )
    limitations: list[str] = []
    for quote in quotes:
        analysis = analyses_repo.get_analysis(session, uuid.UUID(quote["analysis_id"]))
        if analysis is not None and analysis.final_report:
            limitations.extend(list(analysis.final_report.get("limitations") or []))
    prose = decision.reply
    note = evidence.status_note(dropped)
    if note:
        prose = f"{prose}\n{note}" if prose else note
    return evidence.explanation_message(
        prose=prose, quotes=quotes, dropped=dropped, limitations=sorted(set(limitations))
    )


# ---------------------------------------------------------------------------
# lifecycle: prepare (transaction) -> decide (no transaction) -> apply (transaction)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TurnPreparation:
    """Everything the model call needs, read in one short transaction."""

    turn_id: uuid.UUID
    session_id: uuid.UUID
    user_id: uuid.UUID
    question: str
    context_before: dict[str, Any]
    user_scope_patch: dict[str, Any] | None
    user_skill_selection: dict[str, Any] | None
    metrics: dict[str, set[str]]
    messages: list[dict[str, str]]
    skill_block: str
    skill_snapshot: dict[str, Any]
    metric_hint: str | None
    degraded: str | None


@dataclass(frozen=True)
class TurnResolution:
    """The validated outcome of the model call (or the rule path)."""

    decision: intent_layer.ConversationDecision
    model_patch: dict[str, Any]
    patch_reasons: list[str]


def _prepare_turn(
    session: Session, *, turn_id: uuid.UUID, settings: Settings
) -> TurnPreparation | str:
    """Phase 1: state, inputs, skill candidates and the worst-case reservation."""
    turn = conversation_repo.get_turn_for_update(session, turn_id)
    if turn is None:
        return "MISSING"
    if turn.status in TERMINAL_CONVERSATION_TURN_STATUSES:
        return str(turn.status)
    if turn.status not in (ConversationTurnStatus.QUEUED, ConversationTurnStatus.RESOLVING):
        # ANALYZING/RESPONDING: the reconciler owns the turn now. Re-running the
        # resolution would call the model again and try to create a second
        # analysis (the unique index would refuse it) - a retry must be a no-op.
        return str(turn.status)
    if turn.status == ConversationTurnStatus.QUEUED:
        conversation_repo.transition(turn, ConversationTurnStatus.RESOLVING)
        _turn_event(session, turn, "turn.state.changed", {"status": "RESOLVING"})

    session_row = analyses_repo.get_session_for_update(session, turn.session_id)
    user_content = _user_content(session, turn)
    budget = TurnBudget.from_turn(turn, settings)
    # Worst-case reservation for the call about to happen: a crash while the
    # model answers must not hand the allowance out again (design 10.2).
    try:
        budget.reserve_model_call(
            estimated_input=settings.agent_max_input_tokens // 4, max_output=700
        )
    except BudgetExceeded:
        pass
    turn.budget = budget.as_dict()
    session.flush()

    metric_hint = (turn.context_before or {}).get("metric_key") or (
        user_content.get("scope_patch") or {}
    ).get("metric_key")
    try:
        skill_block, skill_record = skill_loader.load_for_turn(
            session,
            settings=settings,
            session_row=session_row,
            owner_user_id=turn.user_id,
            metric_key=metric_hint,
            user_selection=user_content.get("skill_selection"),
            model_choice=None,
        )
    except Exception as exc:
        from app.skills.frontmatter import SkillError

        if isinstance(exc, SkillError):
            _fail(session, turn, code=exc.code, message=exc.message)
            return str(turn.status)
        raise

    resolution = resolve_provider(settings)
    metrics = _metric_table(session)
    # What the model sees as the current scope already includes the values the
    # user just typed in the scope panel; otherwise it would ask for what the
    # turn itself provided (design 6.2: this turn's UI values are the first
    # priority, above the confirmed scope).
    effective, _ = resolve_scope_patch(
        turn.context_before or {}, user_content, None
    )
    messages: list[dict[str, str]] = []
    if resolution.warning:
        _turn_event(session, turn, "turn.llm.degraded", {"reason": resolution.warning})
    if resolution.provider is not None:
        messages = intent_layer.build_messages(
            question=str(user_content.get("text") or ""),
            resolved_scope=scope.describe_scope(effective),
            this_turn_patch=user_content.get("scope_patch"),
            history=_history(session, turn),
            metrics=[
                {"metric_key": key, "allowed_dimensions": sorted(value)}
                for key, value in metrics.items()
            ],
            analyses=_recent_analyses(session, turn),
            now=utcnow().isoformat(),
            skill_candidates=skill_record.candidates,
            memory=_memory_brief(session, metric_hint),
        )

    turn.skill_snapshot = skill_record.as_snapshot()
    session.flush()
    return TurnPreparation(
        turn_id=turn.id,
        session_id=turn.session_id,
        user_id=turn.user_id,
        question=str(user_content.get("text") or ""),
        context_before=dict(turn.context_before or {}),
        user_scope_patch=user_content.get("scope_patch"),
        user_skill_selection=user_content.get("skill_selection"),
        metrics=metrics,
        messages=messages,
        skill_block=skill_block,
        skill_snapshot=skill_record.as_snapshot(),
        metric_hint=metric_hint,
        degraded=resolution.warning,
    )


def _decide_turn(
    preparation: TurnPreparation, *, settings: Settings, budget: TurnBudget
) -> TurnResolution:
    """Phase 2: the model call, deliberately outside any database transaction.

    Holding the turn row lock across a network call would block the cancel route
    for as long as the model takes (design 10.1: model requests run outside the
    transaction).
    """
    resolution = resolve_provider(settings)
    if resolution.provider is None or not preparation.messages:
        merged, missing = resolve_scope_patch(
            preparation.context_before, {"scope_patch": preparation.user_scope_patch}, None
        )
        return TurnResolution(
            decision=rule_decision(merged, missing), model_patch={}, patch_reasons=[]
        )

    decision = intent_layer.call_model(
        resolution.provider, messages=preparation.messages, budget=budget
    )
    patch_reasons: list[str] = []
    model_patch: dict[str, Any] = {}
    if decision.scope_patch is not None:
        patch_reasons.extend(intent_layer.resolve_relative_periods(decision, now=date.today()))
        model_patch, reasons = intent_layer.validate_patch(decision, metrics=preparation.metrics)
        patch_reasons.extend(reasons)
        if "dimensions" not in model_patch and decision.scope_patch.dimensions:
            decision.scope_patch.dimensions = None
    return TurnResolution(decision=decision, model_patch=model_patch, patch_reasons=patch_reasons)


def _apply_turn(
    session: Session,
    preparation: TurnPreparation,
    resolution: TurnResolution,
    *,
    settings: Settings,
    budget: TurnBudget,
) -> str:
    """Phase 3: re-read under the lock, drop a late reply, then act."""
    turn = conversation_repo.get_turn_for_update(session, preparation.turn_id)
    if turn is None:
        return "MISSING"
    if turn.status in TERMINAL_CONVERSATION_TURN_STATUSES:
        # A cancel that landed during the model call wins; nothing is published.
        return str(turn.status)

    session_row = analyses_repo.get_session_for_update(session, turn.session_id)
    user_content = _user_content(session, turn)
    decision = resolution.decision

    # The model's skill choice is only a choice among the candidates it was shown.
    # With no choice, the block and snapshot from the preparation stand: replacing
    # them with an empty record would silently drop what was already loaded.
    skill_block = preparation.skill_block
    skill_snapshot = dict(preparation.skill_snapshot)
    model_choice = [str(item) for item in decision.skill_version_ids]
    if model_choice:
        try:
            skill_block, skill_record = skill_loader.load_for_turn(
                session,
                settings=settings,
                session_row=session_row,
                owner_user_id=turn.user_id,
                metric_key=preparation.metric_hint,
                user_selection=user_content.get("skill_selection"),
                model_choice=model_choice,
            )
            skill_snapshot = skill_record.as_snapshot()
        except Exception:  # pragma: no cover - auto mode never raises for capacity
            logger.warning("skill reload failed", exc_info=True)

    allowed_identifiers = _evidence_identifiers(session, turn, session_row)
    prose_reasons = intent_layer.validate_prose(decision, allowed_identifiers=allowed_identifiers)
    if prose_reasons:
        _turn_event(session, turn, "turn.prose.rejected", {"reasons": prose_reasons})
        decision.reply = None
        for reference in decision.references:
            reference.note = None

    merged, missing = resolve_scope_patch(
        preparation.context_before, user_content, resolution.model_patch
    )
    intent = str(decision.intent)
    if resolution.patch_reasons:
        _turn_event(session, turn, "turn.patch.adjusted", {"reasons": resolution.patch_reasons})
    if intent == "analyze" and missing:
        # The model asked to analyse without a complete scope: ask instead.
        intent = "clarify"
    elif intent == "clarify" and not missing:
        # ...and the reverse: nothing is missing, so there is nothing to ask.
        _turn_event(session, turn, "turn.intent.adjusted", {"from": "clarify", "to": "analyze"})
        intent = "analyze"
    conversation_repo.set_intent(session, turn, intent)
    _turn_event(
        session,
        turn,
        "turn.scope.resolved",
        {"scope": scope.describe_scope(merged), "missing": missing},
    )
    turn.skill_snapshot = skill_snapshot
    turn.budget = budget.as_dict()
    session.flush()

    if intent == "analyze":
        conversation_repo.transition(turn, ConversationTurnStatus.ANALYZING)
        _submit_analysis(session, turn, merged, settings, budget, skill_block=skill_block)
        return str(turn.status)

    if intent == "clarify":
        analyses_repo.update_session_scope(
            session,
            session_row,
            pending_clarification={"scope": merged, "missing": missing, "turn_id": str(turn.id)},
        )
        _close(
            session, turn,
            status=ConversationTurnStatus.WAITING_INPUT,
            content=clarify_reply(missing, decision.clarify_questions),
        )
        return str(turn.status)

    if intent == "explain":
        conversation_repo.transition(turn, ConversationTurnStatus.RESPONDING)
        content = _explanation(session, turn, decision, session_row)
        _close(session, turn, status=ConversationTurnStatus.COMPLETED, content=content)
        return str(turn.status)

    if intent == "unsupported":
        conversation_repo.transition(turn, ConversationTurnStatus.RESPONDING)
        _close(
            session, turn,
            status=ConversationTurnStatus.COMPLETED,
            content=unsupported_reply(decision.unsupported_reason),
        )
        return str(turn.status)

    conversation_repo.transition(turn, ConversationTurnStatus.RESPONDING)
    _close(
        session, turn,
        status=ConversationTurnStatus.COMPLETED,
        content=help_reply(decision.reply),
    )
    return str(turn.status)


def _budget_for_turn(session_factory, turn_id: uuid.UUID, settings: Settings) -> TurnBudget:
    with session_factory() as session:
        turn = conversation_repo.get_turn(session, turn_id)
        return (
            TurnBudget.from_turn(turn, settings)
            if turn is not None
            else TurnBudget.start(settings)
        )


def _fail_turn(session_factory, turn_id: uuid.UUID, *, code: str, message: str) -> str:
    with session_factory() as session:
        turn = conversation_repo.get_turn_for_update(session, turn_id)
        if turn is not None and turn.status not in TERMINAL_CONVERSATION_TURN_STATUSES:
            _fail(session, turn, code=code, message=message)
        session.commit()
    return "FAILED"


def run_turn(session_factory, *, turn_id: uuid.UUID, settings: Settings) -> str:
    """The worker entry point: three phases, two short transactions.

    Nothing holds a row lock while the model answers, so a cancel stays
    responsive and a crash cannot leave the turn locked.
    """
    with session_factory() as session:
        preparation = _prepare_turn(session, turn_id=turn_id, settings=settings)
        session.commit()
    if isinstance(preparation, str):
        return preparation

    budget = _budget_for_turn(session_factory, turn_id, settings)
    try:
        resolution = _decide_turn(preparation, settings=settings, budget=budget)
    except intent_layer.IntentUnavailable as exc:
        return _fail_turn(
            session_factory, turn_id, code=ErrorCode.LLM_UNAVAILABLE, message=str(exc)
        )
    except BudgetExceeded as exc:
        return _fail_turn(
            session_factory,
            turn_id,
            code=ErrorCode.EXECUTION_ERROR,
            message=intent_layer.budget_error(exc),
        )

    with session_factory() as session:
        status = _apply_turn(session, preparation, resolution, settings=settings, budget=budget)
        session.commit()
    return status


def reconcile_turn(session: Session, turn_id: uuid.UUID) -> str:
    """Close a turn whose linked analysis reached a terminal state (design 10.1.6)."""
    turn = conversation_repo.get_turn_for_update(session, turn_id)
    if turn is None:
        return "MISSING"
    if turn.status != ConversationTurnStatus.ANALYZING:
        return str(turn.status)

    analysis = conversation_repo.analysis_for_turn(session, turn.id)
    if analysis is None:
        _fail(
            session, turn, code=ErrorCode.EXECUTION_ERROR,
            message="analysis was never created",
        )
        return str(turn.status)
    if analysis.status not in TERMINAL_ANALYSIS_STATUSES:
        return str(turn.status)

    if analysis.status in ("COMPLETED", "PARTIAL"):
        conversation_repo.transition(turn, ConversationTurnStatus.RESPONDING)
        _close(
            session, turn,
            status=ConversationTurnStatus.COMPLETED,
            content=result_reply(analysis, skill_record=turn.skill_snapshot or {}),
        )
        session_row = analyses_repo.get_session_for_update(session, turn.session_id)
        confirmed = dict(turn.context_after or turn.context_before or {})
        confirmed["focus_analysis_id"] = str(analysis.id)
        analyses_repo.update_session_scope(
            session, session_row,
            resolved_context=confirmed,
            clear_pending_clarification=True,
        )
        return str(turn.status)

    error = {"code": ErrorCode.EXECUTION_ERROR, "message": f"analysis ended {analysis.status}"}
    _close(
        session, turn, status=ConversationTurnStatus.FAILED,
        content=failure_reply(error), error=error,
    )
    return str(turn.status)
