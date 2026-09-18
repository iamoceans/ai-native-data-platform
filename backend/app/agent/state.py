"""Explicit analysis state machine (spec section 19)."""

from __future__ import annotations

from enum import StrEnum


class AnalysisStatus(StrEnum):
    CREATED = "CREATED"
    UNDERSTANDING = "UNDERSTANDING"
    RETRIEVING = "RETRIEVING"
    WAITING_INPUT = "WAITING_INPUT"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    OBSERVING = "OBSERVING"
    SYNTHESIZING = "SYNTHESIZING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


TERMINAL_ANALYSIS_STATUSES = frozenset(
    {
        AnalysisStatus.COMPLETED,
        AnalysisStatus.PARTIAL,
        AnalysisStatus.FAILED,
        AnalysisStatus.CANCELLED,
    }
)

_TRANSITIONS: dict[AnalysisStatus, frozenset[AnalysisStatus]] = {
    AnalysisStatus.CREATED: frozenset({AnalysisStatus.UNDERSTANDING}),
    AnalysisStatus.UNDERSTANDING: frozenset(
        {AnalysisStatus.RETRIEVING, AnalysisStatus.WAITING_INPUT}
    ),
    AnalysisStatus.RETRIEVING: frozenset(
        {AnalysisStatus.PLANNING, AnalysisStatus.WAITING_INPUT}
    ),
    AnalysisStatus.WAITING_INPUT: frozenset({AnalysisStatus.UNDERSTANDING}),
    AnalysisStatus.PLANNING: frozenset({AnalysisStatus.EXECUTING}),
    AnalysisStatus.EXECUTING: frozenset(
        {AnalysisStatus.OBSERVING, AnalysisStatus.PARTIAL}
    ),
    AnalysisStatus.OBSERVING: frozenset(
        {AnalysisStatus.EXECUTING, AnalysisStatus.SYNTHESIZING, AnalysisStatus.PARTIAL}
    ),
    AnalysisStatus.SYNTHESIZING: frozenset(
        {AnalysisStatus.COMPLETED, AnalysisStatus.PARTIAL}
    ),
}


class InvalidAnalysisTransition(ValueError):
    pass


def transition(current: str | AnalysisStatus, target: str | AnalysisStatus) -> AnalysisStatus:
    source = AnalysisStatus(current)
    destination = AnalysisStatus(target)
    if source == destination:
        return source
    if source in TERMINAL_ANALYSIS_STATUSES:
        raise InvalidAnalysisTransition(f"terminal analysis cannot transition: {source}")
    if destination in {AnalysisStatus.FAILED, AnalysisStatus.CANCELLED}:
        return destination
    if destination not in _TRANSITIONS.get(source, frozenset()):
        raise InvalidAnalysisTransition(f"invalid analysis transition {source} -> {destination}")
    return destination

