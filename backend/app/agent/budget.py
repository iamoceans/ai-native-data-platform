"""Immutable accounting for model and tool budgets."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace


class BudgetExceeded(RuntimeError):
    def __init__(self, dimension: str) -> None:
        super().__init__(f"analysis budget exhausted: {dimension}")
        self.dimension = dimension


@dataclass(frozen=True)
class AnalysisBudget:
    max_input_tokens: int = 60_000
    max_output_tokens: int = 12_000
    max_tool_calls: int = 20
    max_queries: int = 12
    max_sql_repairs: int = 2
    max_wall_seconds: int = 180
    input_tokens: int = 0
    output_tokens: int = 0
    tool_calls: int = 0
    queries: int = 0
    sql_repairs: int = 0
    started_monotonic: float = 0.0

    @classmethod
    def start(cls, **limits: int) -> "AnalysisBudget":
        return cls(started_monotonic=time.monotonic(), **limits)

    def reserve_model_call(self, *, estimated_input: int, max_output: int) -> None:
        self.ensure_time()
        if self.input_tokens + estimated_input > self.max_input_tokens:
            raise BudgetExceeded("input_tokens")
        if self.output_tokens + max_output > self.max_output_tokens:
            raise BudgetExceeded("output_tokens")

    def record_model_usage(self, *, input_tokens: int, output_tokens: int) -> "AnalysisBudget":
        if self.input_tokens + input_tokens > self.max_input_tokens:
            raise BudgetExceeded("input_tokens")
        if self.output_tokens + output_tokens > self.max_output_tokens:
            raise BudgetExceeded("output_tokens")
        return replace(
            self,
            input_tokens=self.input_tokens + input_tokens,
            output_tokens=self.output_tokens + output_tokens,
        )

    def consume_tool(self, *, queries: int = 0, sql_repairs: int = 0) -> "AnalysisBudget":
        self.ensure_time()
        if self.tool_calls + 1 > self.max_tool_calls:
            raise BudgetExceeded("tool_calls")
        if self.queries + queries > self.max_queries:
            raise BudgetExceeded("queries")
        if self.sql_repairs + sql_repairs > self.max_sql_repairs:
            raise BudgetExceeded("sql_repairs")
        return replace(
            self,
            tool_calls=self.tool_calls + 1,
            queries=self.queries + queries,
            sql_repairs=self.sql_repairs + sql_repairs,
        )

    def ensure_time(self, *, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        if self.started_monotonic and current - self.started_monotonic > self.max_wall_seconds:
            raise BudgetExceeded("wall_seconds")

    def as_dict(self) -> dict[str, int]:
        return {
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_tool_calls": self.max_tool_calls,
            "max_queries": self.max_queries,
            "max_sql_repairs": self.max_sql_repairs,
            "max_wall_seconds": self.max_wall_seconds,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "tool_calls": self.tool_calls,
            "queries": self.queries,
            "sql_repairs": self.sql_repairs,
        }

