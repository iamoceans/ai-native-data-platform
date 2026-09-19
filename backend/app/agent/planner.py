"""Closed plan schema and validation for metric investigations."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: Literal["compare", "breakdown", "driver_decomposition"]
    depends_on: list[str] = Field(default_factory=list)
    metric: str | None = None
    dimension: str | None = None


class PlanningSelection(BaseModel):
    """The only planning choices delegated to a configured model."""

    model_config = ConfigDict(extra="forbid")
    dimensions: list[str] = Field(default_factory=list, max_length=3)


class InvestigationPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    objective: str = Field(min_length=1, max_length=1000)
    metric_key: str
    steps: list[PlanStep] = Field(min_length=1, max_length=12)
    stop_rules: dict[str, int] = Field(default_factory=lambda: {"max_queries": 12, "max_depth": 3})

    @model_validator(mode="after")
    def validate_graph(self) -> "InvestigationPlan":
        keys = [step.key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("plan step keys must be unique")
        known: set[str] = set()
        for step in self.steps:
            missing = set(step.depends_on) - known
            if missing:
                raise ValueError(f"step {step.key} has unknown or forward dependencies: {sorted(missing)}")
            if step.kind == "breakdown" and not step.dimension:
                raise ValueError(f"breakdown step {step.key} needs a dimension")
            known.add(step.key)
        return self


def validate_plan(
    plan: InvestigationPlan,
    *,
    available_metrics: set[str],
    allowed_dimensions: set[str],
    max_queries: int = 12,
) -> InvestigationPlan:
    if plan.metric_key not in available_metrics:
        raise ValueError(f"unknown metric {plan.metric_key}")
    for step in plan.steps:
        if step.metric and step.metric != plan.metric_key:
            raise ValueError(f"step {step.key} changes the selected metric")
        if step.dimension and step.dimension not in allowed_dimensions:
            raise ValueError(f"dimension {step.dimension} is not allowed")
    if int(plan.stop_rules.get("max_queries", max_queries)) > max_queries:
        raise ValueError("plan exceeds the query budget")
    return plan


def comparison_plan(*, question: str, metric_key: str, dimensions: list[str]) -> InvestigationPlan:
    """A compare step plus one breakdown per selected dimension.

    The breakdowns are siblings (all depend on ``totals``), not a drill-down
    chain: the runner answers them with a single grouped query per period, so
    chaining them would claim a nesting the platform never performs - and would
    silently spend the plan's depth budget on parallel work.
    """
    steps = [PlanStep(key="totals", kind="compare", metric=metric_key)]
    for index, dimension in enumerate(dimensions[:3], start=1):
        steps.append(
            PlanStep(
                key=f"breakdown_{index}",
                kind="breakdown",
                depends_on=["totals"],
                dimension=dimension,
            )
        )
    return InvestigationPlan(objective=question, metric_key=metric_key, steps=steps)
