"""Explanations: answer from stored evidence, never with a new query (design 6.5).

An explanation turn resolves references the model produced (`analysis_id` plus
`claim_id`), checks them the way the evidence endpoint does - ownership *and* the
current discover/query grants on every dataset the analysis depends on - and then
renders the answer from stored artifacts:

* the model's prose carries no figure (validated by the caller);
* every number comes verbatim from the stored claim or its bound calculation.

If any reference is not permitted, the whole explanation degrades to a clear
status instead of falling back to numbers from the transcript (design 6.5:
"证据缺失、过期、权限撤销时返回明确状态，不靠历史聊天里的数字兜底").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.ids import utcnow
from app.constants import ErrorCode
from app.repositories import analyses as analyses_repo

MAX_QUOTES = 5


class EvidenceUnavailable(RuntimeError):
    def __init__(self, message: str, *, code: str = ErrorCode.RESULT_UNAVAILABLE) -> None:
        super().__init__(message)
        self.code = code


def resolve_references(
    session: Session,
    *,
    user_id: uuid.UUID,
    role_ids: list[uuid.UUID],
    session_id: uuid.UUID,
    references: list[Any],
    focus_analysis_id: uuid.UUID | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return the quotable evidence and the reasons a reference was dropped."""
    quotes: list[dict[str, Any]] = []
    dropped: list[str] = []
    seen: set[tuple[str, str]] = set()
    for reference in references[:MAX_QUOTES]:
        analysis_id = reference.analysis_id or focus_analysis_id
        if analysis_id is None:
            dropped.append("no_analysis")
            continue
        analysis = analyses_repo.get_analysis(session, analysis_id)
        if analysis is None or analysis.user_id != user_id or analysis.session_id != session_id:
            dropped.append(f"analysis_not_in_session:{analysis_id}")
            continue
        expires = (analysis.final_report or {}).get("evidence_available_until")
        if expires:
            try:
                expired = datetime.fromisoformat(str(expires).replace("Z", "+00:00")) <= utcnow()
            except (ValueError, TypeError):
                expired = True
            if expired:
                dropped.append(f"evidence_expired:{analysis_id}")
                continue
        if analysis.final_report is None:
            dropped.append(f"no_report:{analysis_id}")
            continue
        if not analyses_repo.analysis_dataset_actions_ok(
            session, role_ids=role_ids, analysis_id=analysis_id
        ):
            dropped.append(f"permission_revoked:{analysis_id}")
            continue
        claims = {str(claim.get("id")): claim for claim in analysis.final_report.get("claims") or []}
        if reference.claim_id is None:
            # No specific claim: offer the analysis's first claim as the anchor.
            claim_id = next(iter(claims), None)
        else:
            claim_id = str(reference.claim_id)
        if claim_id is None or claim_id not in claims:
            dropped.append(f"unknown_claim:{reference.claim_id}")
            continue
        key = (str(analysis_id), claim_id)
        if key in seen:
            continue
        seen.add(key)
        quotes.append(
            {
                "analysis_id": str(analysis_id),
                "claim_id": claim_id,
                "kind": claims[claim_id].get("kind"),
                "text": claims[claim_id].get("text"),
                "value_pointer": claims[claim_id].get("value_pointer"),
                "explanation": evidence_template(session, analysis, claims[claim_id]),
            }
        )
    return quotes, dropped


def explanation_message(
    *,
    prose: str | None,
    quotes: list[dict[str, Any]],
    dropped: list[str],
    limitations: list[str] | None = None,
) -> dict[str, Any]:
    """The stored message: figure-free prose, claims and evidence templates."""
    return {
        "schema_version": 1,
        "kind": "explanation",
        "text": prose or "",
        "quotes": quotes,
        "dropped_references": dropped,
        "limitations": list(limitations or []),
        "evidence_state": "ok" if quotes else "unavailable",
    }


def status_note(dropped: list[str]) -> str:
    if not dropped:
        return ""
    if any(item.startswith("permission_revoked") for item in dropped):
        return "相关数据的权限已被收回，无法再引用那次分析的证据。"
    if any(item.startswith("evidence_expired") for item in dropped):
        return "引用的分析证据已过期，无法继续引用其中的数字。"
    return "引用的分析或结论已不可用，无法引用其证据。"


def evidence_template(session, analysis, claim: dict[str, Any]) -> str:
    """Numbers remain verbatim strings from the immutable calculation snapshot."""
    observation = {}
    try:
        artifact_id = uuid.UUID(str(claim.get("calculation_id")))
        artifact = analyses_repo.get_artifact(session, artifact_id)
        if artifact is not None and artifact.analysis_id == analysis.id:
            observation = artifact.content or {}
    except (ValueError, TypeError):
        pass
    if claim.get("kind") == "observed_change":
        totals = observation.get("totals") or observation
        # Missing historical artifacts retain a nonnumeric template; values
        # must never be reconstructed from the transcript or other state.
        if all(key in totals and totals[key] is not None for key in ("baseline", "current", "change")):
            return (f"基准期为 {totals['baseline']}，当前期为 {totals['current']}；"
                    f"变化量按当前期减基准期计算，结果为 {totals['change']}。")
        return "这是两期指标的观测变化；变化量按当前期减基准期计算。"
    pointer = str(claim.get("value_pointer") or "")
    if "net_change_share" in pointer:
        return "净变化占比是该分组变化量除以整体净变化量；反向变化可能产生负占比或超过整体的占比。这是统计贡献，不能单独证明因果关系。"
    if "effect" in pointer:
        return "因子效应来自已存的确定性分解计算；展示量效应与单价效应共同解释收入变化，不能单独证明业务因果关系。"
    return "此结论引用已存的分析证据；未重新查询数据。"
