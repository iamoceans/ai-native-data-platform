"""Impression x eCPM symmetric factor decomposition (spec section 16.3).

For one group, ``R = I * E / 1000`` where ``I`` is impressions and ``E`` is the
eCPM computed from total revenue over total impressions. The symmetric split

    impression_effect = (I1 - I0) * (E0 + E1) / 2 / 1000
    ecpm_effect       = (E1 - E0) * (I0 + I1) / 2 / 1000

assigns the interaction term to both factors by construction, so the two
effects sum exactly to the revenue delta (up to the documented tolerance) with
no ordering dependency. Zero impressions make eCPM undefined; the function
reports that instead of dividing by zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.analysis.types import EFFECT_TOLERANCE, AnalysisError, to_decimal


@dataclass(frozen=True)
class DriverDecomposition:
    revenue_baseline: Decimal
    revenue_current: Decimal
    delta: Decimal
    impressions_baseline: Decimal
    impressions_current: Decimal
    ecpm_baseline: Decimal | None
    ecpm_current: Decimal | None
    impression_effect: Decimal | None
    ecpm_effect: Decimal | None
    residual: Decimal | None
    status: str
    message: str | None = None

    def as_dict(self) -> dict:
        def render(value: Decimal | None) -> str | None:
            return None if value is None else str(value)

        return {
            "revenue_baseline": str(self.revenue_baseline),
            "revenue_current": str(self.revenue_current),
            "delta": str(self.delta),
            "impressions_baseline": str(self.impressions_baseline),
            "impressions_current": str(self.impressions_current),
            "ecpm_baseline": render(self.ecpm_baseline),
            "ecpm_current": render(self.ecpm_current),
            "impression_effect": render(self.impression_effect),
            "ecpm_effect": render(self.ecpm_effect),
            "residual": render(self.residual),
            "status": self.status,
            "message": self.message,
        }


def decompose_revenue(
    *, impressions_baseline, impressions_current, revenue_baseline, revenue_current
) -> DriverDecomposition:
    i0 = to_decimal(impressions_baseline)
    i1 = to_decimal(impressions_current)
    r0 = to_decimal(revenue_baseline)
    r1 = to_decimal(revenue_current)
    delta = r1 - r0

    if i0 == 0 or i1 == 0:
        return DriverDecomposition(
            revenue_baseline=r0,
            revenue_current=r1,
            delta=delta,
            impressions_baseline=i0,
            impressions_current=i1,
            ecpm_baseline=None,
            ecpm_current=None,
            impression_effect=None,
            ecpm_effect=None,
            residual=delta,
            status="undefined_impressions",
            message="eCPM is undefined when a period has no impressions; only the revenue delta is reported",
        )

    e0 = r0 * 1000 / i0
    e1 = r1 * 1000 / i1
    impression_effect = (i1 - i0) * (e0 + e1) / 2 / 1000
    ecpm_effect = (e1 - e0) * (i0 + i1) / 2 / 1000
    residual = delta - impression_effect - ecpm_effect
    if abs(residual) > EFFECT_TOLERANCE:
        raise AnalysisError(
            "DRIVER_DECOMPOSITION_MISMATCH",
            "impression and eCPM effects do not sum to the revenue delta",
            {
                "residual": str(residual),
                "tolerance": str(EFFECT_TOLERANCE),
                "impression_effect": str(impression_effect),
                "ecpm_effect": str(ecpm_effect),
                "delta": str(delta),
            },
        )
    return DriverDecomposition(
        revenue_baseline=r0,
        revenue_current=r1,
        delta=delta,
        impressions_baseline=i0,
        impressions_current=i1,
        ecpm_baseline=e0,
        ecpm_current=e1,
        impression_effect=impression_effect,
        ecpm_effect=ecpm_effect,
        residual=residual,
        status="ok",
    )
