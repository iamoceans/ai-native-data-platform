"""One turn's budget: token accounting with a UTC deadline that survives restarts (design 10.2).

`AnalysisBudget` combines process-local elapsed time with the inherited UTC
deadline. A turn can be interrupted at any point
(the lease is released while an analysis runs), so its budget is persisted as
counters plus an absolute `deadline_at`, and the remaining wall clock is computed
from the database clock on every pass.

The analysis this turn submits gets the *remainder* as its own sub-budget, so the
turn cannot spend the platform's allowance twice (design 10.2).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from app.agent.budget import BudgetExceeded
from app.config import Settings
from app.ids import utcnow


@dataclass
class TurnBudget:
    max_input_tokens: int
    max_output_tokens: int
    max_model_calls: int
    deadline_at: datetime
    input_tokens: int = 0
    output_tokens: int = 0
    model_calls: int = 0
    # Worst-case reservation for a call that may be in flight when the worker
    # dies: a restart must not hand out that allowance again (design 10.2).
    reserved_input: int = 0
    reserved_output: int = 0
    reserved_model_calls: int = 0
    history: list[str] = field(default_factory=list)

    @classmethod
    def start(cls, settings: Settings, *, deadline_at: datetime | None = None) -> "TurnBudget":
        return cls(
            max_input_tokens=settings.agent_max_input_tokens,
            max_output_tokens=settings.agent_max_output_tokens,
            max_model_calls=settings.agent_max_tool_calls,
            deadline_at=deadline_at
            or utcnow() + timedelta(seconds=settings.conversation_turn_deadline_seconds),
        )

    @classmethod
    def from_turn(cls, turn_row, settings: Settings) -> "TurnBudget":
        """Rehydrate from the persisted row; the deadline is absolute UTC."""
        payload: dict[str, Any] = dict(turn_row.budget or {})
        deadline = payload.get("deadline_at")
        if isinstance(deadline, str):
            deadline = datetime.fromisoformat(deadline)
        return cls(
            max_input_tokens=int(payload.get("max_input_tokens", settings.agent_max_input_tokens)),
            max_output_tokens=int(payload.get("max_output_tokens", settings.agent_max_output_tokens)),
            max_model_calls=int(payload.get("max_model_calls", settings.agent_max_tool_calls)),
            deadline_at=deadline
            or (turn_row.deadline_at or utcnow() + timedelta(seconds=settings.conversation_turn_deadline_seconds)),
            input_tokens=int(payload.get("input_tokens", 0)),
            output_tokens=int(payload.get("output_tokens", 0)),
            model_calls=int(payload.get("model_calls", 0)),
            reserved_input=int(payload.get("reserved_input", 0)),
            reserved_output=int(payload.get("reserved_output", 0)),
            reserved_model_calls=int(payload.get("reserved_model_calls",
                2 if payload.get("reserved_input") or payload.get("reserved_output") else 0)),
            history=[str(item) for item in payload.get("history") or []],
        )

    # -- accounting ---------------------------------------------------------
    def ensure_time(self, *, now: datetime | None = None) -> None:
        if (now or utcnow()) >= self.deadline_at:
            raise BudgetExceeded("wall_seconds")

    def reserve_model_call(self, *, estimated_input: int, max_output: int, model_calls: int = 1) -> None:
        """Conservative reservation *before* the model call (design 10.2).

        Same name and signature as `AnalysisBudget` because the provider protocol
        reserves the call itself - a turn budget is passed to providers directly.
        """
        self.ensure_time()
        if self.model_calls + self.reserved_model_calls + model_calls > self.max_model_calls:
            raise BudgetExceeded("model_calls")
        if self.input_tokens + self.reserved_input + estimated_input > self.max_input_tokens:
            raise BudgetExceeded("input_tokens")
        if self.output_tokens + self.reserved_output + max_output > self.max_output_tokens:
            raise BudgetExceeded("output_tokens")
        self.reserved_input += estimated_input
        self.reserved_output += max_output
        self.reserved_model_calls += model_calls

    def record_model_usage(
        self, *, input_tokens: int, output_tokens: int, note: str | None = None,
        model_calls: int = 1,
    ) -> "TurnBudget":
        """Add actual usage and release the reservation; returns self (protocol shape)."""
        input_tokens = max(0, int(input_tokens))
        output_tokens = max(0, int(output_tokens))
        if self.input_tokens + input_tokens > self.max_input_tokens:
            raise BudgetExceeded("input_tokens")
        if self.output_tokens + output_tokens > self.max_output_tokens:
            raise BudgetExceeded("output_tokens")
        if self.model_calls + model_calls > self.max_model_calls:
            raise BudgetExceeded("model_calls")
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.model_calls += model_calls
        self.reserved_input = 0
        self.reserved_output = 0
        self.reserved_model_calls = 0
        if note:
            self.history.append(note)
        return self

    def spend_without_usage(self, *, note: str) -> None:
        """A call that was made but whose usage is unknown (crash mid-flight)."""
        if self.reserved_input or self.reserved_output or self.reserved_model_calls:
            self.record_model_usage(
                input_tokens=self.reserved_input, output_tokens=self.reserved_output,
                model_calls=self.reserved_model_calls or 2, note=note,
            )

    def release_reservation(self) -> None:
        """Release this attempt's local copy; its persisted crash charge remains."""
        self.reserved_input = self.reserved_output = self.reserved_model_calls = 0

    # -- sub-budget ---------------------------------------------------------
    def remaining_seconds(self, *, now: datetime | None = None) -> int:
        remaining = (self.deadline_at - (now or utcnow())).total_seconds()
        return max(0, int(remaining))

    def analysis_sub_budget(self, settings: Settings) -> dict[str, Any]:
        """What is left for the analysis: the turn's remainder, never a fresh allowance."""
        self.ensure_time()
        input_left = self.max_input_tokens - self.input_tokens - self.reserved_input
        output_left = self.max_output_tokens - self.output_tokens - self.reserved_output
        calls_left = self.max_model_calls - self.model_calls - self.reserved_model_calls
        seconds_left = min(self.remaining_seconds(), settings.agent_max_wall_seconds)
        for dimension, remaining in (
            ("input_tokens", input_left), ("output_tokens", output_left),
            ("model_calls", calls_left), ("wall_seconds", seconds_left),
        ):
            if remaining <= 0:
                raise BudgetExceeded(dimension)
        return {
            "max_input_tokens": input_left,
            "max_output_tokens": output_left,
            "max_model_calls": calls_left,
            "model_calls": 0,
            "deadline_at": min(self.deadline_at, utcnow() + timedelta(seconds=seconds_left)).isoformat(),
            "max_tool_calls": settings.agent_max_tool_calls,
            "max_queries": settings.agent_max_queries,
            "max_sql_repairs": settings.agent_max_sql_repairs,
            "max_wall_seconds": seconds_left,
            "input_tokens": 0,
            "output_tokens": 0,
            "tool_calls": 0,
            "queries": 0,
            "sql_repairs": 0,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_input_tokens": self.max_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "max_model_calls": self.max_model_calls,
            "deadline_at": self.deadline_at.isoformat(),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "model_calls": self.model_calls,
            "reserved_input": self.reserved_input,
            "reserved_output": self.reserved_output,
            "reserved_model_calls": self.reserved_model_calls,
            "history": list(self.history),
        }
