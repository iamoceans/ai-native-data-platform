"""Effective query limits (spec section 4)."""

from __future__ import annotations

from dataclasses import dataclass

from app.api.dto import QueryLimits
from app.config import Settings


@dataclass(frozen=True)
class EffectiveLimits:
    max_rows: int
    max_bytes: int
    timeout_seconds: int

    def as_dict(self) -> dict:
        return {
            "max_rows": self.max_rows,
            "max_bytes": self.max_bytes,
            "timeout_seconds": self.timeout_seconds,
        }


def effective_limits(requested: QueryLimits | None, settings: Settings) -> EffectiveLimits:
    max_rows = settings.default_max_rows
    max_bytes = settings.hard_max_bytes
    timeout = settings.default_timeout_seconds
    if requested is not None:
        if requested.max_rows is not None:
            max_rows = min(requested.max_rows, settings.hard_max_rows)
        if requested.max_bytes is not None:
            max_bytes = min(requested.max_bytes, settings.hard_max_bytes)
        if requested.timeout_seconds is not None:
            timeout = min(requested.timeout_seconds, settings.hard_max_timeout_seconds)
    return EffectiveLimits(max_rows=max_rows, max_bytes=max_bytes, timeout_seconds=timeout)
