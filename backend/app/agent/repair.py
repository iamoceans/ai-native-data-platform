"""Bounded SQL repair policy (spec section 20).

Only capability failures may be repaired: a statement that failed to parse, or
one that referenced a column the live schema no longer has. Policy refusals -
permission denials, forbidden functions, resource limits - are never retried
with a rewritten statement, because the whole point of the refusal is that
changing the query body must not talk the platform out of it.

The repair itself is deliberately narrow. The analysis runner compiles metric
SQL from a closed grammar, so the realistic capability failure is a grouped
dimension that no longer exists in the source (schema drift). The repair drops
that dimension and recompiles; it never invents columns, tables or expressions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from app.constants import ErrorCode

REPAIRABLE_CODES = frozenset(
    {
        ErrorCode.SQL_SYNTAX_ERROR,
        ErrorCode.SCHEMA_CHANGED,
    }
)

POLICY_CODES = frozenset(
    {
        ErrorCode.PERMISSION_DENIED,
        ErrorCode.SQL_FORBIDDEN,
        ErrorCode.SQL_UNSUPPORTED,
        ErrorCode.QUERY_TOO_LARGE,
        ErrorCode.LIMIT_INVALID,
        ErrorCode.DATASET_NOT_REGISTERED,
    }
)

RESOURCE_CODES = frozenset(
    {
        ErrorCode.QUERY_TIMEOUT,
        ErrorCode.QUERY_CANCELLED,
        ErrorCode.QUERY_LOST,
        ErrorCode.QUEUE_TIMEOUT,
        ErrorCode.DATASOURCE_UNAVAILABLE,
        ErrorCode.EXECUTION_ERROR,
        ErrorCode.STORAGE_UNAVAILABLE,
        ErrorCode.SERVICE_NOT_READY,
    }
)

MAX_REPAIRS_DEFAULT = 2


class FailureClass(StrEnum):
    REPAIRABLE = "repairable"
    POLICY = "policy"
    RESOURCE = "resource"
    UNKNOWN = "unknown"


def classify_failure(code: str | None) -> FailureClass:
    if code in REPAIRABLE_CODES:
        return FailureClass.REPAIRABLE
    if code in POLICY_CODES:
        return FailureClass.POLICY
    if code in RESOURCE_CODES:
        return FailureClass.RESOURCE
    return FailureClass.UNKNOWN


@dataclass(frozen=True)
class RepairDecision:
    """What the runner is allowed to do about a failed query."""

    dimension: str | None = None
    reason: str = ""

    @property
    def possible(self) -> bool:
        return self.dimension is not None


def plan_repair(
    *,
    failure_code: str | None,
    group_by: Sequence[str],
    schema_columns: set[str],
    repairs_used: int,
    max_repairs: int = MAX_REPAIRS_DEFAULT,
) -> RepairDecision:
    """Decide whether a grouped column can be dropped and retried once.

    ``group_by`` is the failed query's grouping (dimensions, plus the date column
    for per-day metrics). A column that the live schema still has cannot be the
    cause; a column that disappeared can. When nothing in the grouping explains
    the failure the problem sits in the metric definition itself, which an
    analysis run must report rather than paper over.
    """
    if classify_failure(failure_code) is not FailureClass.REPAIRABLE:
        return RepairDecision(reason=f"failure '{failure_code}' is not repairable")
    if repairs_used >= max_repairs:
        return RepairDecision(reason=f"repair budget exhausted ({repairs_used}/{max_repairs})")
    for column in group_by:
        if column not in schema_columns:
            return RepairDecision(
                dimension=column,
                reason=f"column '{column}' is missing from the live schema",
            )
    return RepairDecision(reason="no missing grouped column explains the failure")
