"""Session scope: merge, versioning and the completeness bridge (design 6.1-6.2).

`resolved_context` is the *confirmed* scope a conversation carries forward. The
merge rule is explicit and one-directional:

    this turn's UI values > this turn's explicit instruction > confirmed scope
    > verifiable catalog defaults

A patch distinguishes `set` from `clear`; a field the patch does not mention is
inherited. Every field records its origin so a reply can say where a date came
from instead of presenting an inherited value as if the user had just chosen it.

Data completeness is three-state (`unknown / complete / incomplete`) because the
platform cannot verify a user's claim. The existing boolean
`AnalysisContext.data_complete` is a bridge: only `complete` maps to true, and
the report keeps distinguishing "unknown" from "known incomplete".
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.constants import CompletenessState

COMPLETENESS_UNKNOWN = CompletenessState.UNKNOWN.value
COMPLETENESS_COMPLETE = CompletenessState.COMPLETE.value
COMPLETENESS_INCOMPLETE = CompletenessState.INCOMPLETE.value
COMPLETENESS_STATES = tuple(state.value for state in CompletenessState)

# Fields an analysis needs before it can be submitted (design 7.2 required_inputs).
REQUIRED_ANALYSIS_FIELDS = (
    "metric_key",
    "baseline_start",
    "baseline_end",
    "current_start",
    "current_end",
)

PATCHABLE_FIELDS = (
    "metric_key",
    "baseline_start",
    "baseline_end",
    "current_start",
    "current_end",
    "comparison",
    "dimensions",
    "filters",
    "data_complete_state",
    "data_complete_source",
)

COMPARISONS = ("explicit", "previous_period", "year_over_year")


def empty_context() -> dict[str, Any]:
    return {"field_origins": {}}


def merge_scope_patch(
    base: dict[str, Any] | None,
    patch: dict[str, Any] | None,
    *,
    origin: str = "ui",
) -> dict[str, Any]:
    """Apply a `set`/`clear` patch to the confirmed scope; missing = inherit.

    `clear` always wins over a value carried in the same patch, so "去掉筛选"
    cannot be misread as "no information" and silently inherit the filter back.
    """
    merged: dict[str, Any] = dict(base or {})
    origins: dict[str, str] = dict(merged.get("field_origins") or {})
    if patch:
        cleared = {str(name) for name in (patch.get("clear") or [])}
        for field in PATCHABLE_FIELDS:
            if field in cleared:
                merged.pop(field, None)
                origins.pop(field, None)
                continue
            if field in patch and patch[field] is not None:
                merged[field] = patch[field]
                origins[field] = origin
    merged["field_origins"] = origins
    return merged


def completeness_from_bool(value: bool | None, *, source: str) -> str:
    """A caller's boolean is a claim, not a verification (design 6.1)."""
    return CompletenessState.from_bool(value).value


def bridge_data_complete(context: dict[str, Any]) -> bool:
    """Only a verified `complete` becomes true for the existing analysis API."""
    return context.get("data_complete_state") == COMPLETENESS_COMPLETE


def analysis_completeness(context: dict[str, Any]) -> str:
    """The three-state value the analysis context carries (design 6.1).

    Bridging to a bare boolean loses the difference between "nobody said" and
    "known incomplete", and the report must keep them apart: unverified data
    still reports the observed change, a known-incomplete window does not.
    """
    state = context.get("data_complete_state")
    if state in COMPLETENESS_STATES:
        return str(state)
    return completeness_from_bool(context.get("data_complete"), source="")


def invalid_period_fields(context: dict[str, Any]) -> list[str]:
    """Fields whose pair cannot describe a non-empty half-open window.

    The analysis contract is `[start, end)` - the direct API rejects an empty
    range - but a model (or a person) reading the dates as inclusive produces
    `end == start`. That query matches no rows, so the change would be published
    as "变化 0" instead of the period never being comparable at all.
    """
    invalid: list[str] = []
    for start_field, end_field in (
        ("baseline_start", "baseline_end"),
        ("current_start", "current_end"),
    ):
        start = context.get(start_field)
        end = context.get(end_field)
        if not start or not end:
            continue
        try:
            ordered = date.fromisoformat(str(start)) < date.fromisoformat(str(end))
        except ValueError:
            ordered = False
        if not ordered:
            invalid.extend([start_field, end_field])
    return invalid


def missing_required_fields(context: dict[str, Any]) -> list[str]:
    """What still blocks an analysis: an absent or unusable field."""
    missing = [
        field for field in REQUIRED_ANALYSIS_FIELDS if context.get(field) in (None, "")
    ]
    for field in invalid_period_fields(context):
        if field not in missing:
            missing.append(field)
    return missing


def scope_to_analysis_context(context: dict[str, Any]) -> dict[str, Any]:
    """The AnalysisContext payload for the governed runner, from resolved scope."""
    return {
        "metric_key": context["metric_key"],
        "baseline_start": context["baseline_start"],
        "baseline_end": context["baseline_end"],
        "current_start": context["current_start"],
        "current_end": context["current_end"],
        "dimensions": list(context.get("dimensions") or [])[:3],
        "filters": dict(context.get("filters") or {}),
        "data_complete": bridge_data_complete(context),
        "completeness": analysis_completeness(context),
    }


def describe_scope(context: dict[str, Any]) -> dict[str, Any]:
    """A number-free, display-only summary of the scope (for replies/UI)."""
    return {
        "metric_key": context.get("metric_key"),
        "baseline": [context.get("baseline_start"), context.get("baseline_end")],
        "current": [context.get("current_start"), context.get("current_end")],
        "comparison": context.get("comparison"),
        "dimensions": list(context.get("dimensions") or []),
        "filters": dict(context.get("filters") or {}),
        "data_complete_state": context.get("data_complete_state", COMPLETENESS_UNKNOWN),
        "origins": dict(context.get("field_origins") or {}),
    }
