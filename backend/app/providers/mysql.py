"""MySQL provider (spec section 9.2).

Dedicated connection per query, read-only session, `max_execution_time` as the
server-side deadline and `KILL QUERY` from a second connection for
cancellation. MySQL cancellation is asynchronous: `KILL QUERY` keeps the
connection alive and the kill only takes effect when the server processes it,
so `cancel()` never claims the query has stopped (spec section 9.2).
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

_FIELD_TYPES: dict[int, str] = {
    FIELD_TYPE.DECIMAL: "decimal",
    FIELD_TYPE.NEWDECIMAL: "decimal",
    FIELD_TYPE.TINY: "tinyint",
    FIELD_TYPE.SHORT: "smallint",
    FIELD_TYPE.LONG: "int",
    FIELD_TYPE.INT24: "mediumint",
    FIELD_TYPE.LONGLONG: "bigint",
    FIELD_TYPE.FLOAT: "float",
    FIELD_TYPE.DOUBLE: "double",
    FIELD_TYPE.NULL: "null",
    FIELD_TYPE.TIMESTAMP: "timestamp",
    FIELD_TYPE.DATE: "date",
    FIELD_TYPE.TIME: "time",
    FIELD_TYPE.DATETIME: "datetime",
    FIELD_TYPE.YEAR: "year",
    FIELD_TYPE.VARCHAR: "varchar",
    FIELD_TYPE.VAR_STRING: "varchar",
    FIELD_TYPE.STRING: "char",
    FIELD_TYPE.BLOB: "blob",
    FIELD_TYPE.TINY_BLOB: "tinyblob",
    FIELD_TYPE.MEDIUM_BLOB: "mediumblob",
    FIELD_TYPE.LONG_BLOB: "longblob",
    FIELD_TYPE.JSON: "json",
    FIELD_TYPE.BIT: "bit",
    FIELD_TYPE.NEWDATE: "date",
}

# MySQL client/server error numbers used for classification.
ER_QUERY_INTERRUPTED = 1317
ER_QUERY_TIMEOUT = 3024
ER_CANT_EXECUTE_IN_READ_ONLY_TRANSACTION = 1792
ER_TABLEACCESS_DENIED = 1142
ER_COLUMNACCESS_DENIED = 1143
ER_SPECIFIC_ACCESS_DENIED = 1227
ER_DBACCESS_DENIED = 1044
ER_NO_SUCH_TABLE = 1146
ER_BAD_FIELD_ERROR = 1054
ER_PARSE_ERROR = 1064


def _type_name(description_entry) -> str:
    type_code = description_entry[1] if len(description_entry) > 1 else None
    scale = description_entry[5] if len(description_entry) > 5 else None
    name = _FIELD_TYPES.get(type_code, f"type:{type_code}")
    if name in ("decimal",) and scale is not None:
        return f"decimal({scale})"
    return name


def _short(exc: BaseException, limit: int = 300) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:limit] + ("..." if len(text) > limit else "")


class MySQLProvider:
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
    ) -> "MySQLProvider":
        return cls(
            connection_config=dict(datasource.connection_config),
            credentials=credentials,
            settings=settings,
        )

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            dialect="mysql",
            explain=True,
            server_timeout=True,
            cancel=True,
            transactional_read_only=True,
            supported_types=(
                "tinyint",
                "smallint",
                "mediumint",
                "int",
                "bigint",
                "decimal",
                "float",
                "double",
                "char",
                "varchar",
                "text",
                "blob",
                "date",
                "time",
                "datetime",
                "timestamp",
                "year",
                "json",
                "bit",
            ),
        )

    def _connect(self) -> pymysql.connections.Connection:
        config = self._config
        return pymysql.connect(
            host=config["host"],
            port=int(config.get("port", 3306)),
            user=self._credentials.username,
            password=self._credentials.password,
            database=config["database"],
            connect_timeout=int(
                config.get("connect_timeout_seconds", self._settings.connect_timeout_seconds)
            ),
            read_timeout=None,
            write_timeout=None,
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
                cur.execute(
                    """
                    SELECT table_schema FROM information_schema.tables
                    WHERE table_schema NOT IN ('mysql', 'information_schema', 'performance_schema', 'sys')
                    GROUP BY table_schema ORDER BY table_schema
                    """
                )
                schemas = [row[0] for row in cur.fetchall()]
        # Spec section 7: MySQL catalog = database, schema = empty string.
        return [Namespace(catalog=schema, schema="") for schema in schemas]

    def list_tables(self, namespace: Namespace) -> list[TableRef]:
        database = namespace.catalog or namespace.schema
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_schema, table_name, table_type
                    FROM information_schema.tables
                    WHERE table_schema = %s AND table_type IN ('BASE TABLE', 'VIEW')
                    ORDER BY table_name
                    """,
                    (database,),
                )
                rows = cur.fetchall()
        return [
            TableRef(
                catalog=str(row[0]),
                schema="",
                name=str(row[1]),
                object_type="table" if row[2] == "BASE TABLE" else "view",
            )
            for row in rows
        ]

    def describe_table(self, ref: TableRef) -> TableSchema:
        database = ref.catalog or ref.schema
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT column_name, column_type, is_nullable, ordinal_position
                    FROM information_schema.columns
                    WHERE table_schema = %s AND table_name = %s
                    ORDER BY ordinal_position
                    """,
                    (database, ref.name),
                )
                rows = cur.fetchall()
        import hashlib
        import json

        columns = [
            ColumnDef(
                name=str(row[0]),
                type=str(row[1]).lower(),
                nullable=(row[2] == "YES"),
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
        timeout_ms = max(1, int(query.timeout_seconds)) * 1000
        try:
            with conn.cursor() as cur:
                # Read-only session (spec 9.2: SELECT privilege + read-only
                # session; Doris/Galera semantics are not emulated here).
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                # Server-side deadline for SELECT statements (milliseconds).
                cur.execute(f"SET SESSION MAX_EXECUTION_TIME = {timeout_ms}")
                cur.execute("SELECT CONNECTION_ID()")
                connection_id = int(cur.fetchone()[0])
        except pymysql.Error:
            conn.close()
            raise
        return ExecutionHandle(
            query_id=query.query_id,
            dialect="mysql",
            worker_id="",
            fencing_token=0,
            connection_generation=1,
            native={
                "connection": conn,
                "connection_id": connection_id,
                "sql": prepare_driver_sql(query.sql, kind="mysql"),
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
        import json

        conn: pymysql.connections.Connection = handle.native["connection"]
        sql: str = handle.native["sql"].strip()
        if sql.upper().startswith("EXPLAIN"):
            sql = sql[len("EXPLAIN") :].strip()
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN FORMAT=JSON {sql}", query.parameters or None)
            row = cur.fetchone()
        payload: Any = row[0] if row else None
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="replace")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {"raw": payload}
        return {"plan": payload, "estimated_rows": None, "estimated_bytes": None}

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
                cur.execute("KILL QUERY %s", (int(connection_id),))
        except pymysql.Error as exc:
            return CancelOutcome(confirmed=False, detail=f"KILL QUERY failed: {_short(exc)}")
        finally:
            killer.close()
        return CancelOutcome(
            confirmed=False,
            detail="KILL QUERY sent; MySQL cancellation is asynchronous until the query returns",
        )

    def close(self, handle: ExecutionHandle) -> None:
        conn = handle.native.pop("connection", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

    # ------------------------------------------------------------ error map
    def classify_execution_error(self, exc: BaseException) -> str:
        if isinstance(exc, pymysql.Error):
            errno = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
            if errno == ER_QUERY_TIMEOUT:
                return "timeout"
            if errno == ER_QUERY_INTERRUPTED:
                return "cancelled"
            if errno == ER_CANT_EXECUTE_IN_READ_ONLY_TRANSACTION:
                return "readonly"
            if errno in (
                ER_TABLEACCESS_DENIED,
                ER_COLUMNACCESS_DENIED,
                ER_SPECIFIC_ACCESS_DENIED,
                ER_DBACCESS_DENIED,
            ):
                return "permission"
            if errno in (ER_NO_SUCH_TABLE, ER_BAD_FIELD_ERROR):
                return "schema"
            if errno == ER_PARSE_ERROR:
                return "syntax"
            if isinstance(exc, pymysql.OperationalError) and errno in (2002, 2003, 2006, 2013):
                return "unavailable"
        return "error"

    def cancel_confirms_stop(self) -> bool:
        return False
