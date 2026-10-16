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
from datetime import date, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.agent.evidence_access import redact_report_message
from app.agent.budget import BudgetExceeded
from app.agent.conversation import context as scope
from app.agent.conversation import explain as evidence
from app.agent.conversation import intent as intent_layer
from app.agent.conversation import skills as skill_loader
from app.agent.conversation.budget import TurnBudget
from app.agent.llm import resolve_provider, schema_instruction
from app.agent.state import TERMINAL_ANALYSIS_STATUSES
from app.config import Settings
from app.constants import (
    TERMINAL_CONVERSATION_TURN_STATUSES,
    ConversationTurnStatus,
    ErrorCode,
)
from app.ids import utcnow
from app.models.orm import AgentMessage, AnalysisTask, ConversationTurn
from app.skills.frontmatter import SkillError
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


def help_reply(
    prose: str | None = None,
    *,
    metrics: list[dict[str, Any]] | None = None,
    example: str | None = None,
) -> dict[str, Any]:
    """What the platform can do, with the registry as the honest content.

    A question like "ADS 层都有哪些数据" is answerable: the registry knows every
    metric, its unit and its allowlisted dimensions. Only the *metric* view is
    published here - dataset grants stay with the catalog endpoints - and the
    model's own prose is kept only when it survived the numeric guard.
    """
    key = str(metrics[0]["metric_key"]) if metrics else None
    default_example = f"例如：{key} 上周相比前一周的变化" if key else None
    content: dict[str, Any] = {
        "schema_version": 1,
        "kind": "help",
        "text": prose
        or (
            "我可以对已登记的指标做受治理的对比分析。告诉我指标、基准期间与当前期间即可。"
            + (f"{default_example}。" if default_example else "")
        ),
    }
    if metrics:
        content["metrics"] = metrics
    if example:
        content["example"] = example
    return content


def unsupported_reply(
    reason: str | None, *, metrics: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    """A refusal still points at what the platform does hold, when there is any."""
    content: dict[str, Any] = {
        "schema_version": 1,
        "kind": "unsupported",
        "text": reason or "这个请求超出了平台当前支持的范围。",
    }
    if metrics:
        content["metrics"] = metrics
    return content


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
    rows, total = analyses_repo.list_messages(
        session, session_id=turn.session_id, limit=HISTORY_LIMIT, offset=0
    )
    if total > HISTORY_LIMIT:
        rows, _ = analyses_repo.list_messages(
            session, session_id=turn.session_id, limit=HISTORY_LIMIT,
            offset=total - HISTORY_LIMIT,
        )
    history = []
    for row in rows:
        content = redact_report_message(
            session, user_id=turn.user_id, content=analyses_repo.message_content(row)
        ) if row.role == "assistant" else analyses_repo.message_content(row)
        history.append({
            "role": row.role, "kind": content.get("kind"),
            "text": content.get("text") or content.get("question"),
        })
    return history[-HISTORY_LIMIT:]


def _metric_catalog(session: Session) -> list[dict[str, Any]]:
    """The registered metrics as the platform can describe them.

    This is the honest source for "what data do you have": names, units and
    allowlisted dimensions come from the active metric definitions, never from
    the model. A malformed definition is simply not offered.
    """
    from sqlalchemy import select

    from app.metrics.registry import MetricDefinitionModel
    from app.models.orm import MetricDefinition

    rows = session.execute(
        select(MetricDefinition).where(MetricDefinition.active.is_(True))
    ).scalars()
    catalog: list[dict[str, Any]] = []
    for row in rows:
        try:
            model = MetricDefinitionModel.model_validate(row.definition)
        except Exception:
            continue
        catalog.append(
            {
                "metric_key": row.metric_key,
                "name": model.name,
                "unit": model.unit,
                "datasets": model.all_datasets,
                "dimensions": sorted(model.allowed_dimensions or []),
            }
        )
    catalog.sort(key=lambda item: str(item["metric_key"]))
    return catalog


def _metric_table(session: Session) -> dict[str, set[str]]:
    """Registered metrics and their allowlisted dimensions (the validation source)."""
    return {
        str(item["metric_key"]): set(item["dimensions"]) for item in _metric_catalog(session)
    }


def _recent_analyses(session: Session, turn: ConversationTurn) -> list[dict[str, Any]]:
    rows, _ = analyses_repo.list_for_user(
        session, user_id=turn.user_id, status=None, limit=ANALYSIS_HISTORY_LIMIT, offset=0
    )
    role_ids = users_repo.role_ids_for_user(session, turn.user_id)
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
        and analyses_repo.analysis_dataset_actions_ok(session, role_ids=role_ids, analysis_id=item.id)
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


def _memory_brief(
    session: Session, metric_key: str | None, *, role_ids: list[uuid.UUID] | None = None,
    user_id: uuid.UUID | None = None, question: str = ""
) -> list[dict[str, Any]]:
    if not metric_key:
        return []
    from app.agent.memory import planner_brief
    brief, _ = planner_brief(session, metric_key=metric_key, allowed_dimensions=[],
                             role_ids=role_ids, user_id=user_id, question=question, limit=5)
    return brief


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
        if (analysis is not None and analysis.user_id == turn.user_id
            and analysis.session_id == turn.session_id
            and analyses_repo.analysis_dataset_actions_ok(
                session, role_ids=users_repo.role_ids_for_user(session, turn.user_id), analysis_id=focus
            )):
            observations = (analysis.state or {}).get("observations") or {}
            for segment in observations.get("segments") or []:
                for value in segment.get("key") or []:
                    allowed.add(str(value))
    return {item for item in allowed if item}


def resolve_scope_patch(
    base: dict[str, Any], user_content: dict[str, Any], model_patch: dict[str, Any] | None
) -> tuple[dict[str, Any], list[str]]:
    """UI values and explicit clears take precedence over model proposals."""
    merged = scope.merge_scope_patch(base, model_patch, origin="model")
    merged = scope.merge_scope_patch(merged, user_content.get("scope_patch"), origin="ui")
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
    analysis_budget = budget.analysis_sub_budget(settings)
    revision = policy_repo.get_revision(session)
    analysis = analyses_repo.create_analysis(
        session,
        session_id=turn.session_id,
        user_id=turn.user_id,
        question=str(_user_content(session, turn).get("text") or ""),
        context=scope.scope_to_analysis_context(merged),
        parent_id=None,
        budget=analysis_budget,
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
    checkpoint_version: int | None = None
    budget: TurnBudget | None = None
    claim: queue_repo.TurnClaim | None = None


@dataclass(frozen=True)
class TurnResolution:
    """The validated outcome of the model call (or the rule path)."""

    decision: intent_layer.ConversationDecision
    model_patch: dict[str, Any]
    patch_reasons: list[str]


def _prepare_turn(
    session: Session, *, turn_id: uuid.UUID, settings: Settings,
    claim: queue_repo.TurnClaim | None = None,
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
    lease = queue_repo.lock_turn_claim(session, claim) if claim is not None else None
    if claim is not None and lease is None:
        return str(turn.status)
    if turn.status == ConversationTurnStatus.QUEUED:
        conversation_repo.transition(turn, ConversationTurnStatus.RESOLVING)
        _turn_event(session, turn, "turn.state.changed", {"status": "RESOLVING"})

    session_row = analyses_repo.get_session_for_update(session, turn.session_id)
    user_content = _user_content(session, turn)
    budget = TurnBudget.from_turn(turn, settings)
    # A prior attempt may have reached the provider before its worker vanished.
    # Keep its worst-case charge; a later success cannot erase that expenditure.
    try:
        budget.spend_without_usage(note="lost_intent_attempt")
        budget.ensure_time()
    except BudgetExceeded as exc:
        turn.budget = budget.as_dict()
        _fail(session, turn, code=ErrorCode.EXECUTION_ERROR, message=intent_layer.budget_error(exc))
        return str(turn.status)

    effective, _ = resolve_scope_patch(turn.context_before or {}, user_content, None)
    metric_hint = effective.get("metric_key")
    try:
        skill_block, skill_record = skill_loader.load_for_turn(
            session,
            settings=settings,
            session_row=session_row,
            owner_user_id=turn.user_id,
            metric_key=metric_hint,
            user_selection=user_content.get("skill_selection"),
            model_choice=None,
            question=str(user_content.get("text") or ""),
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
            memory=_memory_brief(
                session, metric_hint, role_ids=users_repo.role_ids_for_user(session, turn.user_id),
                user_id=turn.user_id, question=str(user_content.get("text") or "")
            ),
        )

    if messages:
        # Persist a bounded allowance for both possible physical requests.
        # The adapter checks each actual request against the remaining budget.
        schema_tokens = len(schema_instruction(intent_layer.ConversationDecision)) // 4 + 1
        estimated = intent_layer.estimate_input_tokens(messages) + schema_tokens
        input_left = budget.max_input_tokens - budget.input_tokens
        output_left = budget.max_output_tokens - budget.output_tokens
        calls_left = budget.max_model_calls - budget.model_calls
        try:
            for dimension, remaining in (("input_tokens", input_left),
                                         ("output_tokens", output_left), ("model_calls", calls_left)):
                if remaining <= 0:
                    raise BudgetExceeded(dimension)
            budget.reserve_model_call(
                estimated_input=min(2 * (estimated + schema_tokens + 300), input_left),
                max_output=min(1400, output_left), model_calls=min(2, calls_left),
            )
        except BudgetExceeded as exc:
            turn.budget = budget.as_dict()
            _fail(session, turn, code=ErrorCode.EXECUTION_ERROR, message=intent_layer.budget_error(exc))
            return str(turn.status)
    turn.budget = budget.as_dict()
    turn.skill_snapshot = skill_record.as_snapshot()
    if lease is not None:
        lease.lease_until = utcnow() + timedelta(seconds=max(
            settings.lease_seconds, int(settings.llm_timeout_seconds * 2) + 15,
        ))
    conversation_repo.save_checkpoint(session, turn, {})
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
        checkpoint_version=int(turn.checkpoint_version),
        budget=budget,
        claim=claim,
    )


def _decide_turn(
    preparation: TurnPreparation, *, settings: Settings, budget: TurnBudget
) -> TurnResolution:
    """Phase 2: the model call, deliberately outside any database transaction.

    Holding the turn row lock across a network call would block the cancel route
    for as long as the model takes (design 10.1: model requests run outside the
    transaction).
    """
    # Only the local snapshot is released. The committed reservation remains
    # available to charge conservatively if this worker disappears mid-request.
    budget.release_reservation()
    budget.ensure_time()
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
        # Invalid model dates must ask for clarification, not silently inherit
        # an older confirmed date. A valid explicit UI value still wins later.
        invalid_dates = [reason.partition(":")[2] for reason in reasons if reason.startswith("invalid_date:")]
        if invalid_dates:
            model_patch["clear"] = list(dict.fromkeys([*(model_patch.get("clear") or []), *invalid_dates]))
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
    if preparation.checkpoint_version is not None and (
        turn.status != ConversationTurnStatus.RESOLVING
        or int(turn.checkpoint_version) != preparation.checkpoint_version
    ):
        return str(turn.status)
    if preparation.claim is not None and queue_repo.lock_turn_claim(session, preparation.claim) is None:
        return str(turn.status)
    budget.ensure_time()

    session_row = analyses_repo.get_session_for_update(session, turn.session_id)
    user_content = _user_content(session, turn)
    decision = resolution.decision

    # The model's skill choice is only a choice among the candidates it was shown.
    # With no choice, the block and snapshot from the preparation stand: replacing
    # them with an empty record would silently drop what was already loaded.
    skill_block = preparation.skill_block
    skill_snapshot = dict(preparation.skill_snapshot)
    offered = set(preparation.skill_snapshot.get("candidates") or [])
    model_choice = [str(item) for item in decision.skill_version_ids if str(item) in offered]
    if model_choice or skill_snapshot.get("mode") == "manual":
        try:
            skill_block, skill_record = skill_loader.load_for_turn(
                session,
                settings=settings,
                session_row=session_row,
                owner_user_id=turn.user_id,
                metric_key=preparation.metric_hint,
                user_selection=user_content.get("skill_selection"),
                model_choice=model_choice,
                question=preparation.question,
            )
            skill_snapshot = skill_record.as_snapshot()
        except SkillError as exc:
            _fail(session, turn, code=exc.code, message=exc.message)
            return str(turn.status)

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
    if skill_snapshot.get("mode") == "auto" and merged.get("metric_key"):
        previous = list(skill_snapshot.get("loaded") or [])
        permitted = list(skill_snapshot.get("selected") or [])
        skill_block, skill_record = skill_loader.load_for_turn(
            session, settings=settings, session_row=session_row, owner_user_id=turn.user_id,
            metric_key=merged["metric_key"], user_selection=user_content.get("skill_selection"),
            model_choice=permitted, question=preparation.question, allowed_version_ids=permitted,
        )
        loaded_ids = {item["version_id"] for item in skill_record.loaded}
        for item in previous:
            if item["version_id"] not in loaded_ids and item.get("metric_keys") and merged["metric_key"] not in item["metric_keys"]:
                skill_record.skipped.append({"version_id": item["version_id"], "reason": "metric_not_applicable"})
        skill_snapshot = skill_record.as_snapshot()
        skill_snapshot["candidates"] = preparation.skill_snapshot.get("candidates", [])
    skill_missing, skill_questions = skill_loader.required_input_issues(
        skill_snapshot, merged, user_content.get("scope_patch"),
    )
    missing = sorted(set(missing) | set(skill_missing))
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
            content=clarify_reply(
                missing,
                list(decision.clarify_questions)
                + skill_questions
                + (
                    ["期间按左闭右开理解（含开始日期、不含结束日期）：只看一天请填 该日 到 次日。"]
                    if scope.invalid_period_fields(merged)
                    else []
                ),
            ),
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
            content=unsupported_reply(
                decision.unsupported_reason, metrics=_metric_catalog(session)
            ),
        )
        return str(turn.status)

    conversation_repo.transition(turn, ConversationTurnStatus.RESPONDING)
    _close(
        session, turn,
        status=ConversationTurnStatus.COMPLETED,
        content=help_reply(decision.reply, metrics=_metric_catalog(session)),
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


def _fail_turn(session_factory, turn_id: uuid.UUID, *, code: str, message: str,
               checkpoint_version: int | None = None, claim: queue_repo.TurnClaim | None = None) -> str:
    with session_factory() as session:
        turn = conversation_repo.get_turn_for_update(session, turn_id)
        if turn is not None and (
            turn.status in TERMINAL_CONVERSATION_TURN_STATUSES
            or (checkpoint_version is not None and (
                turn.status != ConversationTurnStatus.RESOLVING
                or int(turn.checkpoint_version) != checkpoint_version
            ))
        ):
            return str(turn.status)
        if turn is not None and claim is not None and queue_repo.lock_turn_claim(session, claim) is None:
            return str(turn.status)
        if turn is not None and turn.status not in TERMINAL_CONVERSATION_TURN_STATUSES:
            _fail(session, turn, code=code, message=message)
        session.commit()
    return "FAILED"


def run_turn(session_factory, *, turn_id: uuid.UUID, settings: Settings,
             claim: queue_repo.TurnClaim | None = None) -> str:
    """The worker entry point: three phases, two short transactions.

    Nothing holds a row lock while the model answers, so a cancel stays
    responsive and a crash cannot leave the turn locked.
    """
    with session_factory() as session:
        preparation = _prepare_turn(session, turn_id=turn_id, settings=settings, claim=claim)
        session.commit()
    if isinstance(preparation, str):
        return preparation

    budget = getattr(preparation, "budget", None) or _budget_for_turn(session_factory, turn_id, settings)
    checkpoint_version = getattr(preparation, "checkpoint_version", None)
    try:
        resolution = _decide_turn(preparation, settings=settings, budget=budget)
    except intent_layer.IntentUnavailable as exc:
        return _fail_turn(
            session_factory, turn_id, code=ErrorCode.LLM_UNAVAILABLE, message=str(exc),
            checkpoint_version=checkpoint_version,
            claim=claim,
        )
    except BudgetExceeded as exc:
        return _fail_turn(
            session_factory,
            turn_id,
            code=ErrorCode.EXECUTION_ERROR,
            message=intent_layer.budget_error(exc),
            checkpoint_version=checkpoint_version,
            claim=claim,
        )

    try:
        with session_factory() as session:
            status = _apply_turn(session, preparation, resolution, settings=settings, budget=budget)
            session.commit()
    except BudgetExceeded as exc:
        # Roll back a tentative ANALYZING transition/queue publication first,
        # then settle the turn instead of retrying the spent intent call.
        return _fail_turn(
            session_factory, turn_id, code=ErrorCode.EXECUTION_ERROR,
            message=intent_layer.budget_error(exc),
            checkpoint_version=checkpoint_version,
            claim=claim,
        )
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
        content={**failure_reply(error), "analysis_id": str(analysis.id)}, error=error,
    )
    return str(turn.status)
