"""Current-grant checks for report-derived conversation content.

Both the transcript and the model prompt use this reader. Persisted evidence is
never rewritten: a revoked reference hides the whole evidence-bearing message.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.repositories import analyses as analyses_repo
from app.repositories import users as users_repo


def report_text_permitted(
    session: Session, *, user_id: uuid.UUID, content: dict[str, Any]
) -> bool:
    references = []
    if content.get("analysis_id"):
        references.append(content["analysis_id"])
    for quote in content.get("quotes") or []:
        if not isinstance(quote, dict) or not quote.get("analysis_id"):
            return False
        references.append(quote["analysis_id"])
    if not references:
        return True
    try:
        analysis_ids = list(dict.fromkeys(uuid.UUID(str(value)) for value in references))
    except (ValueError, TypeError, AttributeError):
        return False
    for analysis_id in analysis_ids:
        analysis = analyses_repo.get_analysis(session, analysis_id)
        if analysis is None or analysis.user_id != user_id:
            return False
    role_ids = users_repo.role_ids_for_user(session, user_id)
    return all([
        analyses_repo.analysis_dataset_actions_ok(session, role_ids=role_ids, analysis_id=analysis_id)
        for analysis_id in analysis_ids
    ])


def redact_report_message(
    session: Session, *, user_id: uuid.UUID, content: dict[str, Any]
) -> dict[str, Any]:
    if report_text_permitted(session, user_id=user_id, content=content):
        return dict(content)
    redacted = {**content, "claims": [], "limitations": [], "quotes": [], "permission_revoked": True}
    if content.get("kind") in ("analysis", "analysis_result", "explanation"):
        redacted["text"] = ""
        redacted["report_available"] = False
        redacted["evidence_state"] = "permission_revoked"
    return redacted
