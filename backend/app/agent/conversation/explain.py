"""Explanations: answer from stored evidence, never with a new query (design 6.5).

An explanation turn resolves references the model produced (`analysis_id` plus
`claim_id`), checks them the way the evidence endpoint does - ownership *and* the
current discover/query grants on every dataset the analysis depends on - and then
renders the answer from stored artifacts:

* the model's prose carries no figure (validated by the caller);
* every number the user sees is copied verbatim from the stored report claim.

If any reference is not permitted, the whole explanation degrades to a clear
status instead of falling back to numbers from the transcript (design 6.5:
"证据缺失、过期、权限撤销时返回明确状态，不靠历史聊天里的数字兜底").
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

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
    """The stored message: model prose plus verbatim quotes from the report."""
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
    return "引用的分析或结论已不可用，无法引用其证据。"
