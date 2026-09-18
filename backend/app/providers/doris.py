"""Doris provider (spec sections 3, 9.2, 26.2).

The FE speaks the MySQL protocol, but Doris is an independent engine: this
provider is implemented separately and never inherits the MySQL provider.
Verified against the pinned release (apache/doris:fe-3.1.4):

- namespace is ``internal`` catalog + database (external catalogs are rejected)
- session ``query_timeout`` is the server-side deadline
- ``KILL QUERY <connection_id>`` cancels a running query from another connection
- no PostgreSQL-style transactions are emulated; safety comes from SELECT-only
  grants
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pymysql
from pymysql.constants import FIELD_TYPE
from pymysql.cursors import SSCursor

from app.config import Settings
from app.models.orm import Datasource
from app.providers.base import (
    CancelOutcome,
    ColumnDef,
    ConnectionHealth,
    ExecutionColumn,
    ExecutionHandle,
    Namespace,
    ProviderCapabilities,
    ProviderCredentials,
    TableRef,
    TableSchema,
    ValidatedQuery,
)
from app.providers.sql_prep import prepare_driver_sql

INTERNAL_CATALOG = "internal"

_FIELD_TYPES: dict[int, str] = {
    FIELD_TYPE.DECIMAL: "decimal",
    FIELD_TYPE.NEWDECIMAL: "decimal",
    FIELD_TYPE.TINY: "tinyint",
    FIELD_TYPE.SHORT: "smallint",
    FIELD_TYPE.LONG: "int",
    FIELD_TYPE.INT24: "int",
    FIELD_TYPE.LONGLONG: "bigint",  # Doris LARGEINT arrives as text via driver converters
    FIELD_TYPE.FLOAT: "float",
    FIELD_TYPE.DOUBLE: "double",
    FIELD_TYPE.TIMESTAMP: "datetime",
    FIELD_TYPE.DATE: "date",
    FIELD_TYPE.NEWDATE: "date",
    FIELD_TYPE.TIME: "time",
    FIELD_TYPE.DATETIME: "datetime",
    FIELD_TYPE.VARCHAR: "varchar",
    FIELD_TYPE.VAR_STRING: "varchar",
    FIELD_TYPE.STRING: "string",
    FIELD_TYPE.BLOB: "string",
    FIELD_TYPE.JSON: "json",
    FIELD_TYPE.BIT: "boolean",
}

# Doris reports most statement failures through the generic 1105 code, so the
# message text is part of the classification and is verified by integration
# tests against the pinned release.
_ERR = 1105


def _short(exc: BaseException, limit: int = 300) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")


def _type_name(entry) -> str:
    type_code = entry[1] if len(entry) > 1 else None
    scale = entry[5] if len(entry) > 5 else None
    name = _FIELD_TYPES.get(type_code, f"type:{type_code}")
    if name == "decimal" and isinstance(scale, int):
        return f"decimal({scale})"
    return name


class DorisProvider:
    def __init__(
        self,
        *,
        connection_config: dict[str, Any],
        credentials: ProviderCredentials,
        settings: Settings,
    ) -> None:
        self._config = connection_config
        self._credentials = credentials
        self._settings = settings

    @classmethod
    def from_datasource(
        cls, datasource: Datasource, credentials: ProviderCredentials, settings: Settings
    ) -> "DorisProvider":
        return cls(
            connection_config=dict(datasource.connection_config),
            credentials=credentials,
            settings=settings,
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            dialect="doris",
            explain=True,
            server_timeout=True,
            cancel=True,
            transactional_read_only=False,  # Doris does not emulate PG transactions
            supported_types=(
                "boolean",
                "tinyint",
                "smallint",
                "int",
                "bigint",
                "largeint",
                "decimal",
                "float",
                "double",
                "date",
                "datetime",
                "char",
                "varchar",
                "string",
                "json",
            ),
        )

    def _connect(self) -> pymysql.connections.Connection:
        config = self._config
        return pymysql.connect(
            host=config["host"],
            port=int(config.get("port", 9030)),
            user=self._credentials.username,
            password=self._credentials.password,
            database=config.get("database") or None,
            connect_timeout=int(
                config.get("connect_timeout_seconds", self._settings.connect_timeout_seconds)
            ),
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.Cursor,
        )

    def test_connection(self) -> ConnectionHealth:
        import time

        started = time.perf_counter()
        try:
            with self._connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT VERSION()")
                    version = cur.fetchone()[0]
        except pymysql.Error as exc:
            return ConnectionHealth(
                status="UNAVAILABLE",
                error={"code": "DATASOURCE_UNAVAILABLE", "message": _short(exc)},
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        return ConnectionHealth(
            status="HEALTHY",
            server_version=str(version),
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    def list_namespaces(self) -> list[Namespace]:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SHOW DATABASES")
                schemas = [
                    str(row[0])
                    for row in cur.fetchall()
                    if str(row[0]) not in ("information_schema", "mysql", "__internal_schema", "sys")
                ]
        return [Namespace(catalog=INTERNAL_CATALOG, schema=schema) for schema in sorted(schemas)]

    def list_tables(self, namespace: Namespace) -> list[TableRef]:
        if namespace.catalog not in (INTERNAL_CATALOG, ""):
            raise ValueError(f"external catalog '{namespace.catalog}' is not supported in V1")
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_name, table_type FROM information_schema.tables
                    WHERE table_schema = %s AND table_type IN ('BASE TABLE', 'VIEW')
                    ORDER BY table_name
                    """,
                    (namespace.schema,),
                )
                rows = cur.fetchall()
        return [
            TableRef(
                catalog=INTERNAL_CATALOG,
                schema=namespace.schema,
                name=str(row[0]),
                object_type="table" if str(row[1]).upper() == "BASE TABLE" else "view",
            )
            for row in rows
        ]

    def describe_table(self, ref: TableRef) -> TableSchema:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name, column_type, is_nullable, ordinal_position
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (ref.schema, ref.name),
                )
                rows = cur.fetchall()
        import hashlib
        import json

        columns = [
            ColumnDef(
                name=str(row[0]),
                type=str(row[1]).lower(),
                nullable=str(row[2]).upper() == "YES",
                ordinal=int(row[3]),
            )
            for row in rows
        ]
        payload = json.dumps(
            [{"name": c.name, "type": c.type, "nullable": c.nullable} for c in columns],
            sort_keys=True,
        )
        return TableSchema(
            ref=ref,
            columns=tuple(columns),
            schema_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )

    # ------------------------------------------------------------------ exec
    def open_execution(self, query: ValidatedQuery) -> ExecutionHandle:
        conn = self._connect()
        timeout_seconds = max(1, int(query.timeout_seconds))
        try:
            with conn.cursor() as cur:
                cur.execute(f"SET query_timeout = {timeout_seconds}")
                cur.execute("SELECT CONNECTION_ID()")
                connection_id = int(cur.fetchone()[0])
        except pymysql.Error:
            conn.close()
            raise
        return ExecutionHandle(
            query_id=query.query_id,
            dialect="doris",
            worker_id="",
            fencing_token=0,
            connection_generation=1,
            native={
                "connection": conn,
                "connection_id": connection_id,
                "sql": prepare_driver_sql(query.sql, kind="doris"),
            },
        )

    def execute(self, handle: ExecutionHandle, query: ValidatedQuery) -> Iterator[list[tuple]]:
        conn: pymysql.connections.Connection = handle.native["connection"]
        sql: str = handle.native["sql"]
        cursor = conn.cursor(SSCursor)
        cursor.execute(sql, query.parameters or None)
        handle.columns = [
            ExecutionColumn(
                name=entry[0],
                type_name=_type_name(entry),
                type_code=entry[1] if len(entry) > 1 else None,
                scale=entry[5] if len(entry) > 5 and isinstance(entry[5], int) else None,
            )
            for entry in (cursor.description or [])
        ]
        try:
            while True:
                batch = cursor.fetchmany(1_000)
                if not batch:
                    break
                yield list(batch)
        finally:
            cursor.close()

    def explain(self, handle: ExecutionHandle, query: ValidatedQuery) -> dict[str, Any]:
        conn: pymysql.connections.Connection = handle.native["connection"]
        sql: str = handle.native["sql"].strip()
        if sql.upper().startswith("EXPLAIN"):
            sql = sql[len("EXPLAIN") :].strip()
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN {sql}", query.parameters or None)
            rows = cur.fetchall()
        plan = [list(row) for row in (rows or [])]
        return {"plan": plan, "estimated_rows": None, "estimated_bytes": None}

    def cancel(self, handle: ExecutionHandle) -> CancelOutcome:
        connection_id = handle.native.get("connection_id")
        if connection_id is None:
            return CancelOutcome(confirmed=False, detail="no connection id recorded")
        try:
            killer = self._connect()
        except pymysql.Error as exc:
            return CancelOutcome(confirmed=False, detail=f"cancel connection failed: {_short(exc)}")
        try:
            with killer.cursor() as cur:
                cur.execute(f"KILL QUERY {int(connection_id)}")
        except pymysql.Error as exc:
            return CancelOutcome(confirmed=False, detail=f"KILL QUERY failed: {_short(exc)}")
        finally:
            killer.close()
        return CancelOutcome(
            confirmed=False,
            detail="KILL QUERY sent; Doris cancellation is confirmed when the query returns",
        )

    def close(self, handle: ExecutionHandle) -> None:
        conn = handle.native.pop("connection", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

    def view_definition(self, ref: TableRef) -> str | None:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(f"SHOW CREATE VIEW `{ref.schema}`.`{ref.name}`")
                row = cur.fetchone()
        if not row:
            return None
        return " ".join(str(value) for value in row)

    # ------------------------------------------------------------ error map
    def classify_execution_error(self, exc: BaseException) -> str:
        if isinstance(exc, pymysql.Error):
            errno = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
            message = _short(exc).lower()
            if "timeout" in message or "timed out" in message:
                return "timeout"
            if "cancel" in message or "killed" in message:
                return "cancelled"
            if "denied" in message or "permission" in message or "no privilege" in message:
                return "permission"
            if "cannot execute" in message and "read" in message:
                return "readonly"
            if "unknown table" in message or "unknown column" in message:
                return "schema"
            if "syntax" in message:
                return "syntax"
            if isinstance(exc, pymysql.OperationalError) and errno in (2002, 2003, 2006, 2013):
                return "unavailable"
        return "error"

    def cancel_confirms_stop(self) -> bool:
        return False
