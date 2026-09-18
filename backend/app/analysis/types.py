"""Shared analysis types and errors (spec sections 16, 29).

The kernel is deliberately dependency-free: rows are plain mappings of column
name to value, arithmetic is ``Decimal``, and every failure carries a stable
error code from the spec's error table (``JOIN_CARDINALITY_VIOLATION``,
``FEDERATION_LIMIT``, ...).
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# Decomposition uses Decimal with this precision; rounding happens only at the
# presentation layer (spec 16.2: "分解使用 Decimal 保留至少 6 位").
DECIMAL_PRECISION = 28

# Absolute tolerance for the cross-check "effects sum to the revenue delta"
# (spec A07: error <= 0.000001 USD).
EFFECT_TOLERANCE = Decimal("0.000001")


class AnalysisError(Exception):
    """Deterministic analysis failure with a stable code."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": self.details}


# Null dimension members are a distinct bucket, never silently dropped
# (spec 16.1: "NULL 单独分组").
NULL_LABEL = "NULL"
# Small groups may be merged into this bucket so the output stays bounded
# (spec 16.1: "小组归入 OTHER").
OTHER_LABEL = "OTHER"
# Residual/unexplained change is reported explicitly.
RESIDUAL_LABEL = "residual"


def to_decimal(value: Any) -> Decimal:
    """Convert a query result cell to Decimal without float detours."""
    if value is None:
        raise AnalysisError("ANALYSIS_NULL_VALUE", "expected a numeric value, got NULL")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise AnalysisError("ANALYSIS_TYPE_ERROR", "boolean is not a numeric value")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        # Query results arrive as strings for exact types; a float here means the
        # caller lost precision upstream. Convert via str to keep the intent.
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception as exc:  # noqa: BLE001 - reported as a stable error
            raise AnalysisError(
                "ANALYSIS_TYPE_ERROR", f"cannot parse '{value}' as a decimal"
            ) from exc
    raise AnalysisError("ANALYSIS_TYPE_ERROR", f"unsupported value type {type(value)!r}")
