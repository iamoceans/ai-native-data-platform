"""Change contribution decomposition over a mutually exclusive partition.

Implements spec section 16.2 exactly:

- ``delta_i = current_i - baseline_i`` (missing categories are zero-filled);
- ``net_change_share_i = delta_i / delta_total`` (``None`` when the total change
  is exactly zero - never a fabricated 100%);
- ``gross_decline_share_i = max(-delta_i, 0) / sum_j max(-delta_j, 0)`` (only
  declines, ``None`` when nothing declined);
- ``contribution_pp_i = delta_i / baseline_total * 100``.

Groups are mutually exclusive by construction (one partition at a time). NULL
members form their own bucket; small groups can be merged into ``OTHER`` so the
output stays bounded, and the sum of all group deltas is verified against the
parent delta (a mismatch is refused, not hidden in a residual).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable, Mapping, Sequence

from app.analysis.types import (
    NULL_LABEL,
    OTHER_LABEL,
    RESIDUAL_LABEL,
    AnalysisError,
    to_decimal,
)


@dataclass(frozen=True)
class GroupContribution:
    key: tuple[str | None, ...]
    label: str | None
    baseline: Decimal
    current: Decimal
    delta: Decimal
    net_change_share: Decimal | None
    gross_decline_share: Decimal | None
    contribution_pp: Decimal | None
    support: Decimal | None
    low_support: bool

    def as_dict(self) -> dict:
        return {
            "key": list(self.key),
            "label": self.label,
            "baseline": str(self.baseline),
            "current": str(self.current),
            "delta": str(self.delta),
            "net_change_share": None if self.net_change_share is None else str(self.net_change_share),
            "gross_decline_share": (
                None if self.gross_decline_share is None else str(self.gross_decline_share)
            ),
            "contribution_pp": (
                None if self.contribution_pp is None else str(self.contribution_pp)
            ),
            "support": None if self.support is None else str(self.support),
            "low_support": self.low_support,
        }


@dataclass(frozen=True)
class ContributionResult:
    dimension: tuple[str, ...]
    baseline_total: Decimal
    current_total: Decimal
    delta_total: Decimal
    groups: tuple[GroupContribution, ...]
    residual: Decimal
    checks: dict[str, Any]

    def as_dict(self) -> dict:
        return {
            "dimension": list(self.dimension),
            "baseline_total": str(self.baseline_total),
            "current_total": str(self.current_total),
            "delta_total": str(self.delta_total),
            "groups": [group.as_dict() for group in self.groups],
            "residual": str(self.residual),
            "checks": self.checks,
        }


def _group_key(row: Mapping[str, Any], dimension: Sequence[str]) -> tuple[str | None, ...]:
    key: list[str | None] = []
    for column in dimension:
        value = row.get(column)
        if value is None:
            key.append(None)
        elif isinstance(value, str):
            key.append(value)
        else:
            key.append(str(value))
    return tuple(key)


def _label_for(key: tuple[str | None, ...]) -> str | None:
    if all(part is None for part in key):
        return NULL_LABEL
    return None


def _aggregate(
    rows: Iterable[Mapping[str, Any]], dimension: Sequence[str], value_column: str
) -> dict[tuple[str | None, ...], Decimal]:
    totals: dict[tuple[str | None, ...], Decimal] = {}
    for row in rows:
        key = _group_key(row, dimension)
        totals[key] = totals.get(key, Decimal(0)) + to_decimal(row.get(value_column))
    return totals


def _aggregate_support(
    rows: Iterable[Mapping[str, Any]],
    dimension: Sequence[str],
    support_column: str,
) -> dict[tuple[str | None, ...], Decimal]:
    totals: dict[tuple[str | None, ...], Decimal] = {}
    for row in rows:
        key = _group_key(row, dimension)
        totals[key] = totals.get(key, Decimal(0)) + to_decimal(row.get(support_column))
    return totals


def decompose_contribution(
    baseline_rows: Iterable[Mapping[str, Any]],
    current_rows: Iterable[Mapping[str, Any]],
    *,
    dimension: Sequence[str],
    value_column: str,
    parent_delta: Decimal | int | str | None = None,
    support_column: str | None = None,
    min_support: Decimal | int | str = 1_000,
    merge_below_support: bool = True,
) -> ContributionResult:
    """Decompose the change of ``value_column`` over one exclusive partition."""
    dimension = tuple(dimension)
    if not dimension:
        raise AnalysisError(
            "ANALYSIS_INVALID_ARGUMENT", "at least one dimension column is required"
        )

    baseline_map = _aggregate(baseline_rows, dimension, value_column)
    current_map = _aggregate(current_rows, dimension, value_column)
    support_map: dict[tuple[str | None, ...], Decimal] = {}
    if support_column:
        support_map = _aggregate_support(baseline_rows, dimension, support_column)
        for key, value in _aggregate_support(current_rows, dimension, support_column).items():
            support_map[key] = support_map.get(key, Decimal(0)) + value

    keys = sorted(
        set(baseline_map) | set(current_map),
        key=lambda key: tuple("" if part is None else part for part in key),
    )
    if not keys:
        raise AnalysisError("ANALYSIS_NO_DATA", "no rows to decompose", {"dimension": list(dimension)})

    baseline_total = sum(baseline_map.values(), Decimal(0))
    current_total = sum(current_map.values(), Decimal(0))
    delta_total = current_total - baseline_total
    expected = to_decimal(parent_delta) if parent_delta is not None else delta_total
    if expected != delta_total:
        raise AnalysisError(
            "GROUP_SUM_MISMATCH",
            "group deltas do not add up to the parent delta",
            {
                "parent_delta": str(expected),
                "group_delta_sum": str(delta_total),
                "dimension": list(dimension),
            },
        )

    deltas: dict[tuple[str | None, ...], Decimal] = {}
    for key in keys:
        deltas[key] = current_map.get(key, Decimal(0)) - baseline_map.get(key, Decimal(0))

    total_decline = sum((max(-delta, Decimal(0)) for delta in deltas.values()), Decimal(0))
    min_support_value = to_decimal(min_support)

    buckets: list[dict[str, Any]] = []
    other: dict[str, Any] | None = None
    for key in keys:
        baseline = baseline_map.get(key, Decimal(0))
        current = current_map.get(key, Decimal(0))
        delta = deltas[key]
        support = support_map.get(key) if support_column else None
        low = bool(support_column and support is not None and support < min_support_value)
        is_null_bucket = all(part is None for part in key)
        if merge_below_support and low and not is_null_bucket:
            if other is None:
                other = {
                    "key": (),
                    "label": OTHER_LABEL,
                    "baseline": Decimal(0),
                    "current": Decimal(0),
                    "delta": Decimal(0),
                    "support": Decimal(0),
                    "low_support": False,
                    "merged": 0,
                }
            other["baseline"] += baseline
            other["current"] += current
            other["delta"] += delta
            other["support"] += support or Decimal(0)
            other["merged"] += 1
            continue
        buckets.append(
            {
                "key": key,
                "label": _label_for(key),
                "baseline": baseline,
                "current": current,
                "delta": delta,
                "support": support,
                "low_support": low,
                "merged": 0,
            }
        )

    groups: list[GroupContribution] = []
    for bucket in buckets + ([other] if other else []):
        delta = bucket["delta"]
        if delta_total == 0:
            net_share = None
        else:
            net_share = delta / delta_total
        gross_share = None if total_decline == 0 else max(-delta, Decimal(0)) / total_decline
        contribution_pp = None if baseline_total == 0 else delta / baseline_total * 100
        groups.append(
            GroupContribution(
                key=bucket["key"],
                label=bucket["label"],
                baseline=bucket["baseline"],
                current=bucket["current"],
                delta=delta,
                net_change_share=net_share,
                gross_decline_share=gross_share,
                contribution_pp=contribution_pp,
                support=bucket["support"],
                low_support=bucket["low_support"],
            )
        )

    # Groups are a partition: the recomputed sum must equal the parent delta.
    group_sum = sum((group.delta for group in groups), Decimal(0))
    if group_sum != delta_total:
        raise AnalysisError(
            "GROUP_SUM_MISMATCH",
            "rendered groups do not add up to the parent delta",
            {"group_delta_sum": str(group_sum), "parent_delta": str(delta_total)},
        )
    residual = delta_total - group_sum

    checks = {
        "mutually_exclusive": True,
        "sum_matches_parent": group_sum == delta_total,
        "residual_label": RESIDUAL_LABEL,
        "null_bucket_present": any(all(part is None for part in group.key) for group in groups),
        "group_count": len(groups),
    }
    return ContributionResult(
        dimension=dimension,
        baseline_total=baseline_total,
        current_total=current_total,
        delta_total=delta_total,
        groups=tuple(groups),
        residual=residual,
        checks=checks,
    )
