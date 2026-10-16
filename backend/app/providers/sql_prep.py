"""Driver SQL preparation (spec section 22).

The validator produces *canonical* SQL: the AST rendered in the engine dialect
with ``:name`` placeholders (PostgreSQL natively renders ``%(name)s``, which is
also psycopg's style). Before execution the provider converts the canonical SQL
to the driver's parameter style with a deterministic AST round-trip - never
string interpolation - and escapes non-placeholder ``%`` characters when named
parameters are bound (both psycopg and PyMySQL scan the statement text for
placeholders in that mode).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from app.providers.dialects import codegen_dialect_for, parse_dialect_for


def has_placeholders(tree: exp.Expression) -> bool:
    return any(True for _ in tree.find_all(exp.Placeholder))


def prepare_driver_sql(sql: str, *, kind: str) -> str:
    """Convert canonical SQL to the driver's placeholder style.

    ``:name`` -> ``%(name)s`` for MySQL/Doris (PyMySQL pyformat) and no-op for
    PostgreSQL (already pyformat). Non-placeholder ``%`` is doubled only when the
    statement actually binds parameters, because with ``params=None`` neither
    driver performs placeholder scanning.
    """
    tree = sqlglot.parse_one(sql, read=parse_dialect_for(kind))
    placeholders = list(tree.find_all(exp.Placeholder))
    if not placeholders:
        return tree.sql(dialect=codegen_dialect_for(kind))

    # Drivers scan all SQL text, including comments and quoted identifiers.
    # Protect only genuine AST placeholders; text that resembles a binding in
    # a literal or comment must still be escaped. Choose a prefix absent from
    # the input so restoration cannot accidentally change user SQL text.
    prefix = "__ainative_parameter_"
    while prefix in sql:
        prefix = "_" + prefix
    bindings: list[tuple[str, str]] = []
    for index, placeholder in enumerate(placeholders):
        name = placeholder.name
        sentinel = f"{prefix}{index}"
        placeholder.set("this", sentinel)
        bindings.append((f"%%({sentinel})s", f"%({name})s"))
    prepared = tree.sql(dialect=codegen_dialect_for(kind)).replace("%", "%%")
    for sentinel, binding in bindings:
        prepared = prepared.replace(sentinel, binding)
    return prepared


def bind_spark_parameters(sql: str, parameters: dict) -> str:
    """Bind scalar values as AST literals, then require an exact AST round trip.

    Impyla does not offer server-side binding. No value is interpolated into SQL
    text. A serializer/parser disagreement fails closed before source execution.
    """
    import math
    tree = sqlglot.parse_one(sql, read="spark")
    placeholders = list(tree.find_all(exp.Placeholder))
    if {item.name for item in placeholders} != set(parameters):
        raise ValueError("Spark parameters must match named placeholders exactly")
    for item in placeholders:
        value = parameters[item.name]
        if not isinstance(value, (str, int, float, bool, type(None))) or isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Spark parameters must be finite JSON scalars")
        item.replace(exp.convert(value))
    rendered = tree.sql(dialect="spark")
    reparsed = sqlglot.parse(rendered, read="spark")
    if len(reparsed) != 1 or reparsed[0] != tree:
        raise ValueError("Spark literal binding did not preserve the validated AST")
    return rendered
