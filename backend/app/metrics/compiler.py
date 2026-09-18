"""Metric compiler: versioned metric definitions -> validated SQL (spec section 15).

The YAML definitions are the authority; this compiler turns them into canonical
single-statement SQL (named ``:start``/``:end`` parameters, no literal
injection) that still has to pass the Query Gateway. The formula is parsed by a
small arithmetic parser whose function and identifier sets are closed:
components from the definition, numbers, ``+ - * /`` and
``nullif/coalesce/abs/round``. Nothing else is accepted, so a model can only
pick a metric key - it can never invent a formula.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.analysis.types import AnalysisError
from app.metrics.registry import MetricDefinitionModel

ALLOWED_AGGREGATIONS = {
    "sum": "SUM({column})",
    "min": "MIN({column})",
    "max": "MAX({column})",
    "avg": "AVG({column})",
    "count": "COUNT({column})",
    "count_distinct": "COUNT(DISTINCT {column})",
}

ALLOWED_FUNCTIONS: dict[str, tuple[int, int]] = {
    # name -> (min args, max args)
    "nullif": (2, 2),
    "coalesce": (2, 8),
    "abs": (1, 1),
    "round": (1, 2),
}

IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NUMBER_RE = re.compile(r"^\d+(?:\.\d+)?$")
DEFAULT_DATE_COLUMN = "dt"

POST_AGGREGATION_DAILY_AVERAGE = "daily_average"


@dataclass(frozen=True)
class CompiledQuery:
    dataset: str
    sql: str
    parameters: dict[str, str]
    group_by: tuple[str, ...]
    metric_alias: str
    components: dict[str, str]
    post_aggregation: str | None = None

    def as_dict(self) -> dict:
        return {
            "dataset": self.dataset,
            "sql": self.sql,
            "parameters": dict(self.parameters),
            "group_by": list(self.group_by),
            "metric_alias": self.metric_alias,
            "components": dict(self.components),
            "post_aggregation": self.post_aggregation,
        }


@dataclass(frozen=True)
class CompiledMetric:
    metric_key: str
    version: int
    unit: str
    currency: str | None
    aggregation_kind: str
    queries: tuple[CompiledQuery, ...]
    composition: str | None
    component_datasets: dict[str, str]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "metric_key": self.metric_key,
            "version": self.version,
            "unit": self.unit,
            "currency": self.currency,
            "aggregation_kind": self.aggregation_kind,
            "composition": self.composition,
            "component_datasets": dict(self.component_datasets),
            "queries": [query.as_dict() for query in self.queries],
            "warnings": list(self.warnings),
        }


# ---------------------------------------------------------------------------
# formula parser (closed grammar)
# ---------------------------------------------------------------------------
_TOKEN = re.compile(r"\s*(?:(\d+(?:\.\d+)?)|([A-Za-z_][A-Za-z0-9_]*)|([()+\-*/,]))")


def _tokenize(formula: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    while position < len(formula):
        match = _TOKEN.match(formula, position)
        if match is None:
            raise AnalysisError(
                "METRIC_FORMULA_INVALID",
                f"unsupported character in formula at position {position}: {formula[position]!r}",
            )
        tokens.append(match.group(1) or match.group(2) or match.group(3))
        position = match.end()
    return tokens


class _FormulaParser:
    """Recursive-descent parser rendering fully parenthesized SQL."""

    def __init__(self, tokens: list[str], components: dict[str, str]) -> None:
        self._tokens = tokens
        self._position = 0
        self._components = components

    def parse(self) -> str:
        expression = self._expr()
        if self._position != len(self._tokens):
            raise AnalysisError(
                "METRIC_FORMULA_INVALID",
                f"unexpected token '{self._tokens[self._position]}'",
            )
        return expression

    def _peek(self) -> str | None:
        if self._position < len(self._tokens):
            return self._tokens[self._position]
        return None

    def _next(self) -> str:
        token = self._peek()
        if token is None:
            raise AnalysisError("METRIC_FORMULA_INVALID", "unexpected end of formula")
        self._position += 1
        return token

    def _expr(self) -> str:
        rendered = self._term()
        while self._peek() in ("+", "-"):
            operator = self._next()
            rendered = f"({rendered} {operator} {self._term()})"
        return rendered

    def _term(self) -> str:
        rendered = self._factor()
        while self._peek() in ("*", "/"):
            operator = self._next()
            rendered = f"({rendered} {operator} {self._factor()})"
        return rendered

    def _factor(self) -> str:
        token = self._next()
        if token == "-":
            return f"(-{self._factor()})"
        if token == "(":
            rendered = self._expr()
            if self._next() != ")":
                raise AnalysisError("METRIC_FORMULA_INVALID", "missing closing parenthesis")
            return f"({rendered})"
        if NUMBER_RE.match(token):
            return token
        if IDENTIFIER_RE.match(token):
            if self._peek() == "(":
                return self._function(token)
            if token not in self._components:
                raise AnalysisError(
                    "METRIC_FORMULA_INVALID",
                    f"formula references undefined component '{token}'",
                    {"components": sorted(self._components)},
                )
            return f"({self._components[token]})"
        raise AnalysisError("METRIC_FORMULA_INVALID", f"unexpected token '{token}'")

    def _function(self, name: str) -> str:
        if name.lower() not in ALLOWED_FUNCTIONS:
            raise AnalysisError(
                "METRIC_FORMULA_INVALID", f"function '{name}' is not allowed in metric formulas"
            )
        self._next()  # consume '('
        arguments: list[str] = []
        if self._peek() != ")":
            while True:
                arguments.append(self._expr())
                if self._peek() == ",":
                    self._next()
                    continue
                break
        if self._next() != ")":
            raise AnalysisError("METRIC_FORMULA_INVALID", f"missing closing parenthesis for {name}")
        minimum, maximum = ALLOWED_FUNCTIONS[name.lower()]
        if not minimum <= len(arguments) <= maximum:
            raise AnalysisError(
                "METRIC_FORMULA_INVALID",
                f"function {name} expects {minimum}..{maximum} arguments, got {len(arguments)}",
            )
        return f"{name.upper()}({', '.join(arguments)})"


def compile_formula(formula: str, components: dict[str, str]) -> str:
    if not isinstance(formula, str) or not formula.strip():
        raise AnalysisError("METRIC_FORMULA_INVALID", "empty formula")
    return _FormulaParser(_tokenize(formula), components).parse()


# ---------------------------------------------------------------------------
# SQL assembly
# ---------------------------------------------------------------------------
def _component_sql(definition: MetricDefinitionModel) -> dict[str, str]:
    components: dict[str, str] = {}
    for name, component in definition.components.items():
        if not IDENTIFIER_RE.match(name):
            raise AnalysisError(
                "METRIC_FORMULA_INVALID", f"component name '{name}' is not a valid identifier"
            )
        if component.aggregation not in ALLOWED_AGGREGATIONS:
            raise AnalysisError(
                "METRIC_AGGREGATION_UNSUPPORTED",
                f"aggregation '{component.aggregation}' is not supported",
                {"metric_key": definition.metric_key},
            )
        if component.filter:
            raise AnalysisError(
                "METRIC_FILTER_UNSUPPORTED",
                "component filters are not supported by the V1 compiler",
                {"metric_key": definition.metric_key, "component": name},
            )
        column = component.column
        if not IDENTIFIER_RE.match(column):
            raise AnalysisError(
                "METRIC_COLUMN_INVALID", f"column '{column}' is not a valid identifier"
            )
        components[name] = ALLOWED_AGGREGATIONS[component.aggregation].format(column=column)
    return components


def _assign_components_to_datasets(
    definition: MetricDefinitionModel,
) -> dict[str, str]:
    datasets = definition.all_datasets
    names = list(definition.components)
    if len(datasets) == 1:
        return {name: datasets[0] for name in names}
    if len(names) != len(datasets):
        raise AnalysisError(
            "METRIC_COMPOSITION_INVALID",
            "multi-dataset metrics need one component per dataset (in order)",
            {"metric_key": definition.metric_key},
        )
    return {name: datasets[index] for index, name in enumerate(names)}


def _date_column(definition: MetricDefinitionModel) -> str:
    date_column = definition.freshness.date_column if definition.freshness else DEFAULT_DATE_COLUMN
    if not IDENTIFIER_RE.match(date_column):
        raise AnalysisError(
            "METRIC_COLUMN_INVALID", f"date column '{date_column}' is not a valid identifier"
        )
    return date_column


def _date_where(
    definition: MetricDefinitionModel, date_column: str, filters: dict[str, str] | None
) -> tuple[str, dict[str, str]]:
    clause = f"{date_column} >= :start AND {date_column} < :end"
    parameters = {"start": "", "end": ""}
    for column, value in (filters or {}).items():
        if not IDENTIFIER_RE.match(column):
            raise AnalysisError("METRIC_FILTER_UNSUPPORTED", f"invalid filter column '{column}'")
        if column not in definition.allowed_dimensions and column != date_column:
            raise AnalysisError(
                "METRIC_DIMENSION_NOT_ALLOWED",
                f"filter column '{column}' is not an allowed dimension",
                {"metric_key": definition.metric_key, "allowed": definition.allowed_dimensions},
            )
        clause += f" AND {column} = :filter_{column}"
        parameters[f"filter_{column}"] = str(value)
    return clause, parameters


def _assemble(dataset: str, select_columns: str, date_where: str, group_by: list[str]) -> str:
    sql = f"SELECT {select_columns} FROM {dataset} WHERE {date_where}"
    if group_by:
        sql += f" GROUP BY {', '.join(group_by)} ORDER BY {', '.join(group_by)}"
    return sql


def compile_metric(
    definition: MetricDefinitionModel,
    *,
    dimensions: list[str] | None = None,
    period_start: date | str | None = None,
    period_end: date | str | None = None,
    filters: dict[str, str] | None = None,
) -> CompiledMetric:
    """Compile a metric definition into canonical SQL for one period.

    ``period_start``/``period_end`` are a half-open ``[start, end)`` date range;
    both are required by the compiler (spec 12.4: AI queries against large fact
    tables must carry a verifiable date range).
    """
    if period_start is None or period_end is None:
        raise AnalysisError(
            "METRIC_PERIOD_REQUIRED",
            "compiling a metric requires an explicit [start, end) period",
            {"metric_key": definition.metric_key},
        )
    requested = list(dimensions or [])
    for dimension in requested:
        if dimension not in definition.allowed_dimensions:
            raise AnalysisError(
                "METRIC_DIMENSION_NOT_ALLOWED",
                f"dimension '{dimension}' is not allowed for metric {definition.metric_key}",
                {"allowed": definition.allowed_dimensions},
            )

    component_sql = _component_sql(definition)
    component_datasets = _assign_components_to_datasets(definition)
    date_column = _date_column(definition)
    date_where, parameters = _date_where(definition, date_column, filters)
    parameters["start"] = str(period_start)
    parameters["end"] = str(period_end)

    warnings: list[str] = []
    queries: list[CompiledQuery] = []
    if len(definition.all_datasets) == 1:
        group_by = list(requested)
        post_aggregation: str | None = None
        if definition.aggregation_kind == "distinct_per_day" and date_column not in group_by:
            group_by = [date_column] + group_by
            post_aggregation = POST_AGGREGATION_DAILY_AVERAGE
            warnings.append(
                "distinct_per_day metric: rows are per day; average across days, never sum them"
            )
        formula_sql = compile_formula(definition.formula, component_sql)
        select_columns = ", ".join(group_by + [f"{formula_sql} AS {definition.metric_key}"])
        sql = _assemble(definition.all_datasets[0], select_columns, date_where, group_by)
        queries.append(
            CompiledQuery(
                dataset=definition.all_datasets[0],
                sql=sql,
                parameters=parameters,
                group_by=tuple(group_by),
                metric_alias=definition.metric_key,
                components=component_sql,
                post_aggregation=post_aggregation,
            )
        )
        composition = None
    else:
        # Multi-dataset metric: one aggregate query per dataset, combined by the
        # composition formula over component aliases (spec 15: aggregate each
        # source first, never join fine-grained fact tables).
        for component_name, dataset in component_datasets.items():
            group_by = list(requested)
            select_columns = ", ".join(group_by + [f"{component_sql[component_name]} AS {component_name}"])
            sql = _assemble(dataset, select_columns, date_where, group_by)
            queries.append(
                CompiledQuery(
                    dataset=dataset,
                    sql=sql,
                    parameters=parameters,
                    group_by=tuple(group_by),
                    metric_alias=component_name,
                    components={component_name: component_sql[component_name]},
                )
            )
        composition = compile_formula(definition.formula, {name: name for name in component_sql})

    return CompiledMetric(
        metric_key=definition.metric_key,
        version=definition.version,
        unit=definition.unit,
        currency=definition.currency,
        aggregation_kind=definition.aggregation_kind,
        queries=tuple(queries),
        composition=composition,
        component_datasets=component_datasets,
        warnings=tuple(warnings),
    )
