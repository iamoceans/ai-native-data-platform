"""SQL validation pipeline for the Query Gateway (spec section 12.1).

Order:
  1. raw-text guards (executable comments, NUL)
  2. parse with the pinned target dialect; exactly one statement
  3. statement kind allowlist (SELECT / UNION / CTE + subqueries)
  4. AST node denylist (writes, DDL, sessions, locks, SELECT INTO, ...)
  5. recursive CTE, function allowlist, join policy, structural budget
  6. table resolution against registered datasets (resolver)
  7. star expansion + column validation + qualification (sqlglot qualify)
  8. outer LIMIT rewrite, percentage-literal escaping for psycopg
  9. serialize and re-parse the validated SQL (defense in depth)
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import OptimizeError, ParseError
from sqlglot.optimizer.qualify import qualify

from app.constants import ErrorCode
from app.errors import ApiError
from app.models.orm import Dataset
from app.query.resolver import (
    ResolvedRelation,
    resolve_relations,
    schema_dict_for,
)

DIALECTS = {"postgres": "postgres", "mysql": "mysql", "doris": "doris", "spark": "spark"}

_ALLOWED_FUNC_CLASSES = frozenset(
    {
        # sqlglot models operators and conditionals as exp.Func subclasses
        "And",
        "Or",
        "Xor",
        "Not",
        "Case",
        "If",
        "Exists",
        "Any",
        "All",
        # real functions
        "Sum",
        "Count",
        "Min",
        "Max",
        "Avg",
        "Abs",
        "Round",
        "Floor",
        "Ceil",
        "Coalesce",
        "Nullif",
        "Cast",
        "TryCast",
        "Least",
        "Greatest",
        "TimestampTrunc",
        "DateTrunc",
        "Extract",
        "Date",
        "CurrentDate",
        "Lower",
        "Upper",
        "Trim",
        "Length",
        "Substring",
        "Concat",
    }
)

_ALLOWED_FUNCTION_NAMES = frozenset(
    {
        "sum",
        "count",
        "min",
        "max",
        "avg",
        "abs",
        "round",
        "floor",
        "ceil",
        "ceiling",
        "coalesce",
        "nullif",
        "cast",
        "try_cast",
        "least",
        "greatest",
        "date_trunc",
        "date_part",
        "extract",
        "date",
        "current_date",
        "lower",
        "upper",
        "trim",
        "ltrim",
        "rtrim",
        "length",
        "substring",
        "concat",
    }
)

_DENIED_NODE_NAMES = (
    "Insert",
    "Update",
    "Delete",
    "Merge",
    "Create",
    "Drop",
    "Alter",
    "AlterColumn",
    "TruncateTable",
    "Grant",
    "Revoke",
    "Copy",
    "Into",
    "Transaction",
    "Commit",
    "Rollback",
    "Set",
    "SetItem",
    "Lock",
    "Use",
    "Kill",
    "Pragma",
    "Describe",
    "Show",
    "Comment",
    "Fetch",
    "Declare",
    "Call",
    "Analyze",
    "Vacuum",
    "Refresh",
    "Cache",
    "Uncache",
    "Export",
    "LoadData",
    "Attach",
    "Detach",
    "Command",
    "AlterSession",
    "UncacheTable",
    "Format",
    # session / user variables and row sampling are never part of a governed
    # analytical statement (MySQL/Doris parse these into dedicated nodes)
    "Parameter",
    "SessionParameter",
    "TableSample",
)


def _denied_classes() -> tuple[type, ...]:
    classes = []
    for name in _DENIED_NODE_NAMES:
        cls = getattr(exp, name, None)
        if isinstance(cls, type):
            classes.append(cls)
    return tuple(classes)


DENIED_CLASSES = _denied_classes()


@dataclass
class ValidatedStatement:
    tree: exp.Expression
    original_sql: str
    validated_sql: str
    sql_hash: str
    relations: list[ResolvedRelation]
    placeholder_names: list[str] = field(default_factory=list)

    @property
    def dataset_ids(self) -> tuple[uuid.UUID, ...]:
        seen: dict[uuid.UUID, None] = {}
        for relation in self.relations:
            seen.setdefault(relation.dataset.id, None)
        return tuple(seen)


def _reject(rule: str, message: str, code: str = ErrorCode.SQL_FORBIDDEN) -> ApiError:
    return ApiError(code, message, details={"rule_id": rule})


def parse_sql(sql: str, dialect: str) -> exp.Expression:
    try:
        statements = sqlglot.parse(sql, read=dialect, error_level=sqlglot.ErrorLevel.RAISE)
    except ParseError as exc:
        raise ApiError(
            ErrorCode.SQL_SYNTAX_ERROR,
            f"SQL could not be parsed: {exc}",
            details={"rule_id": "PARSE_ERROR"},
        ) from exc
    statements = [statement for statement in statements if statement is not None]
    if len(statements) != 1:
        raise _reject("SINGLE_STATEMENT_ONLY", "exactly one SQL statement is allowed")
    tree = statements[0]
    if isinstance(tree, exp.Subquery) and isinstance(tree.this, (exp.Select, exp.Union)):
        return tree
    if isinstance(tree, (exp.Select, exp.Union)):
        return tree
    raise _reject(
        "AST_SELECT_ONLY",
        "only SELECT / UNION queries are allowed",
    )


def check_raw_text(sql: str) -> None:
    if "\x00" in sql:
        raise _reject("NUL_BYTE", "SQL contains a NUL byte")
    if "/*!" in sql:
        raise _reject("EXECUTABLE_COMMENT", "engine-specific executable comments are not allowed")


def check_nodes(tree: exp.Expression) -> None:
    for node in tree.walk():
        if isinstance(node, DENIED_CLASSES):
            raise _reject(
                "STATEMENT_NODE_DENIED",
                f"statement part '{type(node).__name__}' is not allowed",
            )
        if isinstance(node, exp.With) and node.args.get("recursive"):
            raise _reject("RECURSIVE_CTE", "recursive CTEs are not allowed")
        if isinstance(node, exp.Lock):
            raise _reject("LOCKING_READ", "locking reads (FOR UPDATE/SHARE) are not allowed")


def check_functions(tree: exp.Expression) -> None:
    for node in tree.walk():
        if isinstance(node, exp.Anonymous):
            name = (node.name or "").lower()
            if name not in _ALLOWED_FUNCTION_NAMES:
                raise _reject(
                    "FUNCTION_NOT_ALLOWED",
                    f"function '{name}' is not in the allowlist",
                    code=ErrorCode.SQL_FORBIDDEN,
                )
        elif isinstance(node, exp.Func):
            class_name = type(node).__name__
            if class_name not in _ALLOWED_FUNC_CLASSES:
                raise _reject(
                    "FUNCTION_NOT_ALLOWED",
                    f"function '{class_name.lower()}' is not in the allowlist",
                    code=ErrorCode.SQL_FORBIDDEN,
                )


def check_joins(tree: exp.Expression) -> None:
    for join in tree.find_all(exp.Join):
        if str(join.args.get("kind") or "").upper() == "CROSS":
            raise _reject("CROSS_JOIN", "CROSS JOIN is not allowed")
        if str(join.args.get("method") or "").upper() == "NATURAL":
            raise _reject("NATURAL_JOIN", "NATURAL JOIN is not allowed")
        on_clause = join.args.get("on")
        using = join.args.get("using")
        if on_clause is None and not using:
            raise _reject("JOIN_WITHOUT_ON", "JOIN without ON/USING is not allowed")
        if isinstance(on_clause, exp.Boolean):
            raise _reject("CONSTANT_JOIN_CONDITION", "JOIN ON a constant condition is not allowed")


def check_budget(
    tree: exp.Expression, *, max_relations: int, max_subquery_depth: int
) -> None:
    if max_subquery_depth >= 1:
        depth = 0
        for select in tree.find_all(exp.Select):
            current = 0
            parent = select.parent
            while parent is not None:
                if isinstance(parent, exp.Subquery):
                    current += 1
                parent = parent.parent
            depth = max(depth, current)
        if depth > max_subquery_depth:
            raise _reject(
                "SUBQUERY_DEPTH",
                f"subquery nesting depth {depth} exceeds the limit {max_subquery_depth}",
            )


def _rewrite_limit(tree: exp.Expression, max_rows: int) -> None:
    """Bound the outer result set.

    The internal cap is max_rows + 1 so the executor can detect truncation with
    the N+1 rule (spec 14) by seeing one row beyond the effective limit; that
    probe row is dropped before the result is published. Nothing beyond
    max_rows is ever returned to a client.
    """
    probe_limit = max_rows + 1
    limit = tree.args.get("limit")
    if isinstance(limit, exp.Limit):
        expression = limit.args.get("expression")
        if isinstance(expression, exp.Literal) and expression.is_int:
            if int(expression.this) <= max_rows:
                return
        limit.set("expression", exp.Literal.number(probe_limit))
        return
    tree.set("limit", exp.Limit(expression=exp.Literal.number(probe_limit)))


def _qualify(tree: exp.Expression, schema_map: dict, dialect: str) -> exp.Expression:
    try:
        return qualify(
            tree,
            schema=schema_map or None,
            dialect=dialect,
            expand_stars=True,
            validate_qualify_columns=True,
            quote_identifiers=False,
            identify=False,
        )
    except OptimizeError as exc:
        message = str(exc)
        rule = "UNKNOWN_COLUMN_REFERENCE" if "could not be resolved" in message else "QUALIFY_FAILED"
        raise ApiError(
            ErrorCode.SQL_UNSUPPORTED,
            f"query could not be validated against the registered schema: {message}",
            details={"rule_id": rule},
        ) from exc


def _check_stars(tree: exp.Expression) -> None:
    for star in tree.find_all(exp.Star):
        if star.find_ancestor(exp.Count) is None:
            raise _reject(
                "UNEXPANDED_STAR",
                "star expansion failed; use explicit columns",
                code=ErrorCode.SQL_UNSUPPORTED,
            )


def validate_query(
    *,
    sql: str,
    dialect: str,
    datasets: list[Dataset],
    schema_loader,
    database: str,
    max_rows: int,
    max_relations: int,
    max_subquery_depth: int,
    engine: str = "postgres",
) -> ValidatedStatement:
    """Validate canonical SQL.

    The output is canonical (engine dialect, ``:name`` placeholders); the
    provider converts it to the driver parameter style at execution time
    (``app.providers.sql_prep``).
    """
    check_raw_text(sql)
    tree = parse_sql(sql, dialect)
    check_nodes(tree)
    check_functions(tree)
    check_joins(tree)
    check_budget(tree, max_relations=max_relations, max_subquery_depth=max_subquery_depth)

    relations = resolve_relations(tree, datasets=datasets, database=database, engine=engine)
    if len(relations) > max_relations:
        raise _reject(
            "RELATION_BUDGET",
            f"query references {len(relations)} relations; the limit is {max_relations}",
        )

    schemas: dict[uuid.UUID, list[dict]] = {}
    for relation in relations:
        if relation.dataset.id not in schemas:
            schemas[relation.dataset.id] = schema_loader(relation.dataset)
    schema_map = schema_dict_for(relations, schemas, dialect=dialect)
    tree = _qualify(tree, schema_map, dialect)
    _check_stars(tree)
    _rewrite_limit(tree, max_rows)
    validated_sql = tree.sql(dialect=dialect)

    # Re-parse the serialized SQL: what we execute is exactly what we checked.
    reparsed = parse_sql(validated_sql, dialect)
    check_nodes(reparsed)
    check_functions(reparsed)
    check_joins(reparsed)

    statements = [s for s in sqlglot.parse(validated_sql, read=dialect) if s is not None]
    if len(statements) != 1:
        raise _reject("REPARSE_MULTI", "validated SQL is not a single statement")

    placeholders = sorted(
        {
            placeholder.name
            for placeholder in tree.find_all(exp.Placeholder)
            if placeholder.name
        }
    )
    return ValidatedStatement(
        tree=tree,
        original_sql=sql,
        validated_sql=validated_sql,
        sql_hash=hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        relations=relations,
        placeholder_names=placeholders,
    )
