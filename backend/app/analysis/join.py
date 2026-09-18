"""Bounded cross-source join (spec section 17).

Only registered relations are accepted; both sides are already-aggregated
results (each at most 10,000 rows / 20 MiB total by default). The join:

- matches on the registered keys (NULL keys never match);
- supports ``[valid_from, valid_to)`` as-of matching for configuration tables,
  so a historic fact is explained by the configuration in effect at that time;
- requires the right side to be unique per key after the validity filter and
  refuses duplicates with ``JOIN_CARDINALITY_VIOLATION`` instead of silently
  double counting;
- emits exactly one output row per left row (outer join) and verifies that
  amount columns are preserved end to end.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.analysis.relations import JoinRelation
from app.analysis.types import EFFECT_TOLERANCE, AnalysisError, to_decimal

DEFAULT_MAX_ROWS_PER_SIDE = 10_000
DEFAULT_MAX_TOTAL_BYTES = 20 * 1024 * 1024


def _estimate_bytes(rows: Sequence[Mapping[str, Any]]) -> int:
    return sum(len(json.dumps(row, default=str, ensure_ascii=False)) for row in rows)


def _key_value(row: Mapping[str, Any], column: str, *, side: str) -> str | None:
    if column not in row:
        raise AnalysisError(
            "RELATION_KEY_MISSING",
            f"column '{column}' is missing on the {side} result",
            {"column": column, "side": side},
        )
    value = row.get(column)
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        raise AnalysisError(
            "RELATION_KEY_MISSING",
            f"column '{column}' on the {side} result is not a scalar key",
            {"column": column, "side": side},
        )
    return str(value)


def _as_datetime(value: Any) -> Any:
    """Dates/datetimes compare with each other; strings stay lexicographic ISO."""
    return value


def join_results(
    left_rows: Sequence[Mapping[str, Any]],
    right_rows: Sequence[Mapping[str, Any]],
    relation: JoinRelation,
    *,
    as_of_column: str | None = None,
    max_rows_per_side: int = DEFAULT_MAX_ROWS_PER_SIDE,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
) -> "JoinResult":
    if len(left_rows) > max_rows_per_side or len(right_rows) > max_rows_per_side:
        raise AnalysisError(
            "FEDERATION_LIMIT",
            "a join side exceeds the row budget; aggregate in the source first",
            {"left_rows": len(left_rows), "right_rows": len(right_rows), "max_rows": max_rows_per_side},
        )
    left_bytes = _estimate_bytes(left_rows)
    right_bytes = _estimate_bytes(right_rows)
    if left_bytes + right_bytes > max_total_bytes:
        raise AnalysisError(
            "FEDERATION_LIMIT",
            "the join inputs exceed the byte budget; aggregate in the source first",
            {
                "left_bytes": left_bytes,
                "right_bytes": right_bytes,
                "max_bytes": max_total_bytes,
            },
        )

    time_column = as_of_column or relation.left_time_column

    if relation.as_of and time_column is None:
        raise AnalysisError(
            "RELATION_KEY_MISSING",
            f"relation {relation.relation_id} needs an as-of column",
        )
    right_index = _build_plain_index(right_rows, relation)
    if not relation.as_of:
        # Without a validity window the right side must already be unique.
        for key, candidates in right_index.items():
            if len(candidates) > 1:
                raise AnalysisError(
                    "JOIN_CARDINALITY_VIOLATION",
                    "the right side has duplicate keys for this relation",
                    {
                        "relation_id": relation.relation_id,
                        "key": list(key),
                        "duplicates": len(candidates),
                    },
                )

    output: list[dict[str, Any]] = []
    unmatched = 0
    matched_keys = 0
    # Key columns are represented by the left side already; merging them again
    # would only create type-comparison noise.
    right_columns = [
        column for column in _right_columns(right_rows) if column not in relation.right_keys
    ]
    for left in left_rows:
        key, null_key = _left_key(left, relation)
        match: Mapping[str, Any] | None = None
        if not null_key:
            candidates = right_index.get(key, ())
            if relation.as_of:
                candidates = _interval_match(candidates, left.get(time_column or ""), relation)
            if len(candidates) > 1:
                raise AnalysisError(
                    "JOIN_CARDINALITY_VIOLATION",
                    "the right side has duplicate keys for this relation",
                    {
                        "relation_id": relation.relation_id,
                        "key": list(key),
                        "duplicates": len(candidates),
                    },
                )
            match = candidates[0] if candidates else None
        if match is None:
            unmatched += 1
        else:
            matched_keys += 1
        merged = dict(left)
        for column in right_columns:
            if column in merged and match is not None and merged[column] != match.get(column):
                raise AnalysisError(
                    "JOIN_COLUMN_COLLISION",
                    f"column '{column}' exists on both sides with different values",
                    {"column": column, "relation_id": relation.relation_id},
                )
            if match is None:
                merged.setdefault(column, None)
            else:
                merged[column] = match.get(column)
        output.append(merged)

    checks = _amount_checks(left_rows, output, relation)
    total_rows = len(left_rows) + len(right_rows)
    return JoinResult(
        relation_id=relation.relation_id,
        rows=tuple(output),
        stats={
            "left_rows": len(left_rows),
            "right_rows": len(right_rows),
            "output_rows": len(output),
            "matched": matched_keys,
            "unmatched": unmatched,
            "unmatched_rate": (Decimal(unmatched) / Decimal(len(left_rows))) if left_rows else None,
            "as_of_matching": relation.as_of,
            "input_bytes": left_bytes + right_bytes,
            "total_rows_in": total_rows,
        },
        checks=checks,
    )


def _left_key(
    left: Mapping[str, Any], relation: JoinRelation
) -> tuple[tuple[str | None, ...], bool]:
    key = tuple(
        _key_value(left, column, side="left") for column in relation.left_keys
    )
    return key, any(part is None for part in key)


def _build_plain_index(
    right_rows: Sequence[Mapping[str, Any]], relation: JoinRelation
) -> dict[tuple[str | None, ...], list[Mapping[str, Any]]]:
    index: dict[tuple[str | None, ...], list[Mapping[str, Any]]] = {}
    for row in right_rows:
        key = tuple(_key_value(row, column, side="right") for column in relation.right_keys)
        if any(part is None for part in key):
            continue  # rows without a key can never match
        index.setdefault(key, []).append(row)
    return index


def _interval_match(
    candidates: Sequence[Mapping[str, Any]],
    left_time: Any,
    relation: JoinRelation,
) -> list[Mapping[str, Any]]:
    """Return the candidates whose ``[valid_from, valid_to)`` covers left_time."""
    valid_from_column = relation.right_valid_from or ""
    valid_to_column = relation.right_valid_to
    if left_time is None:
        return []
    matches: list[Mapping[str, Any]] = []
    for row in candidates:
        start = _as_datetime(row.get(valid_from_column))
        if start is None:
            continue
        end = _as_datetime(row.get(valid_to_column)) if valid_to_column else None
        try:
            within = start <= left_time and (end is None or left_time < end)
        except TypeError as exc:
            raise AnalysisError(
                "RELATION_KEY_TYPE_MISMATCH",
                f"cannot compare '{valid_from_column}' with '{left_time!r}'",
                {"relation_id": relation.relation_id},
            ) from exc
        if within:
            matches.append(row)
    return matches


def _right_columns(right_rows: Sequence[Mapping[str, Any]]) -> list[str]:
    columns: list[str] = []
    for row in right_rows:
        for column in row:
            if column not in columns:
                columns.append(column)
    return columns


def _amount_checks(
    left_rows: Sequence[Mapping[str, Any]],
    output_rows: Sequence[Mapping[str, Any]],
    relation: JoinRelation,
) -> dict[str, Any]:
    checks: dict[str, Any] = {"amount_columns": list(relation.amount_columns)}
    preserved = True
    for column in relation.amount_columns:
        left_sum = sum(
            (to_decimal(row.get(column, 0)) for row in left_rows), Decimal(0)
        )
        output_sum = sum(
            (to_decimal(row.get(column, 0)) for row in output_rows), Decimal(0)
        )
        preserved = preserved and abs(left_sum - output_sum) <= EFFECT_TOLERANCE
        if not preserved:
            raise AnalysisError(
                "JOIN_AMOUNT_MISMATCH",
                f"joining changed the total of '{column}'",
                {
                    "column": column,
                    "left_total": str(left_sum),
                    "output_total": str(output_sum),
                    "relation_id": relation.relation_id,
                },
            )
    checks["amounts_preserved"] = preserved
    return checks


@dataclass(frozen=True)
class JoinResult:
    relation_id: str
    rows: tuple[dict[str, Any], ...]
    stats: dict[str, Any]
    checks: dict[str, Any] = field(default_factory=dict)

    def as_dict(self, *, include_rows: bool = False) -> dict:
        payload = {
            "relation_id": self.relation_id,
            "stats": {
                key: (str(value) if isinstance(value, Decimal) else value)
                for key, value in self.stats.items()
            },
            "checks": self.checks,
            "row_count": len(self.rows),
        }
        if include_rows:
            payload["rows"] = [dict(row) for row in self.rows]
        return payload
