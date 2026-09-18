"""Period comparison (spec section 16.2).

`change_pct = delta / baseline` as a ratio; when the baseline is zero the ratio
is undefined and reported as `None` with `baseline_zero=true` instead of an
invented percentage. Rounding never happens here.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.analysis.types import to_decimal


@dataclass(frozen=True)
class ComparisonResult:
    baseline: Decimal
    current: Decimal
    delta: Decimal
    change_pct: Decimal | None
    baseline_zero: bool

    def as_dict(self) -> dict:
        return {
            "baseline": str(self.baseline),
            "current": str(self.current),
            "delta": str(self.delta),
            "change_pct": None if self.change_pct is None else str(self.change_pct),
            "baseline_zero": self.baseline_zero,
        }


def compare_totals(baseline_total, current_total) -> ComparisonResult:
    """Compare two period totals exactly (values may be Decimal/int/str)."""
    baseline = to_decimal(baseline_total)
    current = to_decimal(current_total)
    delta = current - baseline
    if baseline == 0:
        return ComparisonResult(
            baseline=baseline,
            current=current,
            delta=delta,
            change_pct=None,
            baseline_zero=True,
        )
    return ComparisonResult(
        baseline=baseline,
        current=current,
        delta=delta,
        change_pct=delta / baseline,
        baseline_zero=False,
    )
