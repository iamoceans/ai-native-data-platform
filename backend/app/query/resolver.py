"""Table resolution: AST table references -> registered datasets (spec 7, 12.1).

Uses SQLGlot scope analysis so CTE names and aliases are never mistaken for
physical tables, then rewrites every physical table reference to its fully
qualified registered name in the engine's own convention:

- PostgreSQL: ``schema.table`` (catalog = database, schema = real schema)
- MySQL:      ``database.table`` (catalog = database, schema = empty)
- Doris:      ``internal.database.table`` (catalog = internal, schema = database);
              external catalogs are rejected in V1.
"""

from __future__ import annotations

import uuid
import re
from dataclasses import dataclass

from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from app.constants import ErrorCode
from app.errors import ApiError
from app.models.orm import Dataset


@dataclass(frozen=True)
class ResolvedRelation:
    dataset: Dataset
    node: exp.Table
    quoted: bool


def _match(actual: str, expected: str, quoted: bool) -> bool:
    if quoted:
        return actual == expected
    return actual.lower() == expected.lower()


def _quoted(identifier: exp.Expression | None) -> bool:
    return isinstance(identifier, exp.Identifier) and bool(identifier.quoted)


_SIMPLE_FOLDED_IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_$]*$")


def _needs_quoting(value: str) -> bool:
    """Return whether emitting an unquoted identifier could change its identity."""
    return _SIMPLE_FOLDED_IDENTIFIER.fullmatch(value) is None


def collect_real_tables(tree: exp.Expression) -> list[exp.Table]:
    tables: dict[int, exp.Table] = {}
    for scope in traverse_scope(tree):
        for source in scope.sources.values():
            if isinstance(source, exp.Table):
                tables.setdefault(id(source), source)
    return list(tables.values())


def _pick_unique(candidates: list[Dataset], table_name: str) -> Dataset:
    if not candidates:
        raise ApiError(
            ErrorCode.DATASET_NOT_REGISTERED,
            f"table '{table_name}' is not a registered, authorized dataset",
            details={"table": table_name},
        )
    if len(candidates) > 1:
        raise ApiError(
            ErrorCode.AMBIGUOUS_TABLE_REFERENCE,
            f"table name '{table_name}' matches multiple registered datasets; qualify it",
            details={"matches": [f"{c.catalog_name}.{c.schema_name}.{c.object_name}" for c in candidates]},
        )
    return candidates[0]


def _resolve_postgres(
    datasets: list[Dataset], *, table_name: str, ref_db: str | None, ref_catalog: str | None,
    name_quoted: bool, db_quoted: bool, catalog_quoted: bool, database: str,
) -> Dataset:
    if ref_catalog and not _match(ref_catalog, database, catalog_quoted):
        raise ApiError(
            ErrorCode.DATASET_NOT_REGISTERED,
            f"cross-database reference '{ref_catalog}' is not allowed",
            details={"table": table_name},
        )
    candidates = [d for d in datasets if _match(table_name, d.object_name, name_quoted)]
    if ref_db:
        candidates = [d for d in candidates if _match(ref_db, d.schema_name, db_quoted)]
    return _pick_unique(candidates, table_name)


def _resolve_mysql(
    datasets: list[Dataset], *, table_name: str, ref_db: str | None, ref_catalog: str | None,
    name_quoted: bool, db_quoted: bool, catalog_quoted: bool,
) -> Dataset:
    if ref_catalog:
        raise ApiError(
            ErrorCode.DATASET_NOT_REGISTERED,
            "three-part names are not allowed on MySQL; use database.table",
            details={"table": table_name},
        )
    candidates = [d for d in datasets if d.schema_name == "" and _match(table_name, d.object_name, name_quoted)]
    if ref_db:
        candidates = [d for d in candidates if _match(ref_db, d.catalog_name, db_quoted)]
    return _pick_unique(candidates, table_name)


def _resolve_doris(
    datasets: list[Dataset], *, table_name: str, ref_db: str | None, ref_catalog: str | None,
    name_quoted: bool, db_quoted: bool, catalog_quoted: bool, internal_catalog: str,
) -> Dataset:
    if ref_catalog and ref_catalog.lower() != internal_catalog:
        raise ApiError(
            ErrorCode.DATASET_NOT_REGISTERED,
            f"external catalog '{ref_catalog}' is not supported in V1",
            details={"table": table_name},
        )
    candidates = [d for d in datasets if _match(table_name, d.object_name, name_quoted)]
    if ref_db:
        candidates = [d for d in candidates if _match(ref_db, d.schema_name, db_quoted)]
    return _pick_unique(candidates, table_name)


def resolve_relations(
    tree: exp.Expression,
    *,
    datasets: list[Dataset],
    database: str,
    engine: str = "postgres",
) -> list[ResolvedRelation]:
    resolved: list[ResolvedRelation] = []
    for node in collect_real_tables(tree):
        name_quoted = _quoted(node.this)
        db_identifier = node.args.get("db")
        catalog_identifier = node.args.get("catalog")
        ref_db = node.db or None
        ref_catalog = node.catalog or None
        table_name = node.name

        if engine == "mysql":
            dataset = _resolve_mysql(
                datasets,
                table_name=table_name,
                ref_db=ref_db,
                ref_catalog=ref_catalog,
                name_quoted=name_quoted,
                db_quoted=_quoted(db_identifier),
                catalog_quoted=_quoted(catalog_identifier),
            )
            node.set("catalog", None)
            node.set(
                "db",
                exp.Identifier(
                    this=dataset.catalog_name, quoted=_needs_quoting(dataset.catalog_name)
                ),
            )
        elif engine == "doris":
            dataset = _resolve_doris(
                datasets,
                table_name=table_name,
                ref_db=ref_db,
                ref_catalog=ref_catalog,
                name_quoted=name_quoted,
                db_quoted=_quoted(db_identifier),
                catalog_quoted=_quoted(catalog_identifier),
                internal_catalog="internal",
            )
            node.set("catalog", exp.Identifier(this="internal", quoted=False))
            node.set(
                "db",
                exp.Identifier(this=dataset.schema_name, quoted=_needs_quoting(dataset.schema_name)),
            )
        elif engine == "spark":
            if ref_catalog and not _match(ref_catalog, "spark_catalog", _quoted(catalog_identifier)):
                raise ApiError(ErrorCode.DATASET_NOT_REGISTERED, "only spark_catalog is supported")
            candidates = [d for d in datasets if _match(table_name, d.object_name, name_quoted)]
            if ref_db:
                candidates = [d for d in candidates if _match(ref_db, d.schema_name, _quoted(db_identifier))]
            dataset = _pick_unique(candidates, table_name)
            node.set("catalog", None)
            node.set("db", exp.Identifier(this=dataset.schema_name, quoted=_needs_quoting(dataset.schema_name)))
        elif engine == "postgres":
            dataset = _resolve_postgres(
                datasets,
                table_name=table_name,
                ref_db=ref_db,
                ref_catalog=ref_catalog,
                name_quoted=name_quoted,
                db_quoted=_quoted(db_identifier),
                catalog_quoted=_quoted(catalog_identifier),
                database=database,
            )
            node.set("catalog", None)
            node.set(
                "db",
                exp.Identifier(this=dataset.schema_name, quoted=_needs_quoting(dataset.schema_name)),
            )
        else:
            raise ApiError(ErrorCode.DATASOURCE_UNSUPPORTED, f"no resolver for kind '{engine}'")

        # Catalog names come from database introspection and therefore carry
        # exact identifier semantics. Always quoting the rewritten physical
        # name prevents PostgreSQL from folding a registered mixed-case name
        # (for example "Orders") to a different lower-case object at runtime.
        physical_quoted = _needs_quoting(dataset.object_name)
        node.set("this", exp.Identifier(this=dataset.object_name, quoted=physical_quoted))
        if physical_quoted and node.args.get("alias") is None:
            # SQLGlot otherwise derives a folded alias and can no longer map
            # unqualified columns to a quoted mixed-case table name.
            node.set(
                "alias",
                exp.TableAlias(
                    this=exp.Identifier(this=dataset.object_name, quoted=True)
                ),
            )
        resolved.append(ResolvedRelation(dataset=dataset, node=node, quoted=physical_quoted))
    return resolved


def schema_dict_for(
    relations: list[ResolvedRelation],
    schemas: dict[uuid.UUID, list[dict]],
    *,
    dialect: str,
) -> dict:
    """Build the nested sqlglot schema mapping for column expansion.

    Keys follow the *rewritten* reference form, so qualification works for
    ``schema.table`` (PostgreSQL), ``database.table`` (MySQL) and
    ``internal.database.table`` (Doris).
    """
    schema_map: dict = {}
    for relation in relations:
        node = relation.node
        columns = schemas.get(relation.dataset.id) or []
        mapping = {column["name"]: column["type"] for column in columns}
        name = node.this.sql(dialect=dialect) if _quoted(node.this) else node.name
        db_node = node.args.get("db")
        db = db_node.sql(dialect=dialect) if _quoted(db_node) else node.db
        catalog_node = node.args.get("catalog")
        catalog = (
            catalog_node.sql(dialect=dialect) if _quoted(catalog_node) else node.catalog
        )
        if node.catalog and node.db:
            schema_map.setdefault(catalog, {}).setdefault(db, {})[name] = mapping
        elif node.db:
            schema_map.setdefault(db, {})[name] = mapping
        else:
            schema_map[name] = mapping
    return schema_map
