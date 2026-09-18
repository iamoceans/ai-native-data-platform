"""Driver SQL preparation (spec section 22).

The validator produces *canonical* SQL: the AST rendered in the engine dialect
with ``:name`` placeholders (PostgreSQL natively renders ``%(name)s``, which is
also psycopg's style). Before execution the provider converts the canonical SQL
to the driver's parameter style with a deterministic AST round-trip - never
string interpolation - and escapes literal ``%`` characters when named
parameters are bound (both psycopg and PyMySQL scan the statement text for
placeholders in that mode).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

from app.providers.dialects import codegen_dialect_for, parse_dialect_for


def escape_percent_literals(tree: exp.Expression) -> None:
    for literal in tree.find_all(exp.Literal):
        if literal.is_string and isinstance(literal.this, str) and "%" in literal.this:
            literal.set("this", literal.this.replace("%", "%%"))


def has_placeholders(tree: exp.Expression) -> bool:
    return any(True for _ in tree.find_all(exp.Placeholder))


def prepare_driver_sql(sql: str, *, kind: str) -> str:
    """Convert canonical SQL to the driver's placeholder style.

    ``:name`` -> ``%(name)s`` for MySQL/Doris (PyMySQL pyformat) and no-op for
    PostgreSQL (already pyformat). Literal ``%`` is doubled only when the
    statement actually binds parameters, because with ``params=None`` neither
    driver performs placeholder scanning.
    """
    tree = sqlglot.parse_one(sql, read=parse_dialect_for(kind))
    if has_placeholders(tree):
        escape_percent_literals(tree)
    return tree.sql(dialect=codegen_dialect_for(kind))
