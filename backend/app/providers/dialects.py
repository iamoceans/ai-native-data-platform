"""Dialect helpers for the three engines (spec sections 3, 9, 22).

Parsing uses the stock SQLGlot dialects. Code generation needs one adjustment:
psycopg and PyMySQL both bind named parameters in pyformat style
(``%(name)s``), while SQLGlot renders ``:name`` for MySQL/Doris. The custom
generator classes below render the driver style directly, so the validated SQL
that executes is exactly what the AST checker produced.

Doris uses its own dialect (spec section 3: never treat Doris as MySQL).
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp
from sqlglot.dialects.doris import Doris
from sqlglot.dialects.mysql import MySQL

from app.constants import SourceKind, ErrorCode
from app.errors import ApiError


class PyformatMySQL(MySQL):
    class Generator(MySQL.Generator):
        def placeholder_sql(self, expression: exp.Placeholder) -> str:
            return f"%({expression.name})s"


class PyformatDoris(Doris):
    class Generator(Doris.Generator):
        def placeholder_sql(self, expression: exp.Placeholder) -> str:
            return f"%({expression.name})s"


_PARSE_DIALECTS: dict[str, str] = {
    SourceKind.POSTGRES: "postgres",
    SourceKind.MYSQL: "mysql",
    SourceKind.DORIS: "doris",
}

# Generation dialect: postgres natively renders %(name)s (psycopg style);
# MySQL/Doris use the pyformat generator classes above.
_CODEGEN_DIALECTS: dict[str, object] = {
    SourceKind.POSTGRES: "postgres",
    SourceKind.MYSQL: PyformatMySQL,
    SourceKind.DORIS: PyformatDoris,
}


def parse_dialect_for(kind: str) -> str:
    dialect = _PARSE_DIALECTS.get(str(kind))
    if dialect is None:
        raise ApiError(ErrorCode.DATASOURCE_UNSUPPORTED, f"no SQL dialect for kind '{kind}'")
    return dialect


def codegen_dialect_for(kind: str):
    dialect = _CODEGEN_DIALECTS.get(str(kind))
    if dialect is None:
        raise ApiError(ErrorCode.DATASOURCE_UNSUPPORTED, f"no SQL dialect for kind '{kind}'")
    return dialect


def render(sql: str, *, kind: str, pretty: bool = False) -> str:
    """Parse with the engine dialect and render the driver parameter style."""
    return sqlglot.parse_one(sql, read=parse_dialect_for(kind)).sql(
        dialect=codegen_dialect_for(kind), pretty=pretty
    )
