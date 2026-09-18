"""PostgreSQL provider (spec section 9.2).

Per-query dedicated connection with a read-only session, server-side
statement/lock timeouts, a server-side cursor for bounded streaming and
driver-level cancel. The provider never sees user chat content and never
accepts SQL that has not passed the gateway.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterator
from typing import Any

import psycopg
from psycopg.rows import tuple_row

from app.config import Settings
from app.ids import sha256_hex
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

try:  # psycopg >= 3.2 exposes the OID registry here
    from psycopg.postgres import types as _pg_types
except Exception:  # pragma: no cover - defensive for older builds
    _pg_types = {}

_FALLBACK_TYPES = {
    16: "boolean",
    17: "bytea",
    20: "bigint",
    21: "smallint",
    23: "integer",
    25: "text",
    114: "json",
    700: "real",
    701: "double precision",
    1042: "character",
    1043: "character varying",
    1082: "date",
    1114: "timestamp without time zone",
    1184: "timestamp with time zone",
    1186: "interval",
    1700: "numeric",
    2950: "uuid",
    3802: "jsonb",
}

_STRIP_LIMIT = 300


def _sanitize_error(exc: BaseException) -> dict[str, Any]:
    message = str(exc).strip().replace("\n", " ")
    if len(message) > _STRIP_LIMIT:
        message = message[:_STRIP_LIMIT] + "...<truncated>"
    sqlstate = getattr(exc, "sqlstate", None)
    return {
        "code": "DATASOURCE_UNAVAILABLE" if not sqlstate else "EXECUTION_ERROR",
        "message": message,
        "sqlstate": sqlstate,
    }


def type_name_for_oid(oid: int | None, scale: int | None = None) -> str:
    if oid is None:
        return "unknown"
    info = _pg_types.get(oid) if _pg_types else None
    name = getattr(info, "name", None) or _FALLBACK_TYPES.get(oid, f"oid:{oid}")
    if name == "numeric" and scale is not None:
        return f"numeric({scale})"
    return name


class PostgresProvider:
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
    ) -> "PostgresProvider":
        return cls(
            connection_config=dict(datasource.connection_config),
            credentials=credentials,
            settings=settings,
        )

    # ------------------------------------------------------------------ caps
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            dialect="postgres",
            explain=True,
            server_timeout=True,
            cancel=True,
            transactional_read_only=True,
            supported_types=(
                "boolean",
                "smallint",
                "integer",
                "bigint",
                "numeric",
                "real",
                "double precision",
                "text",
                "character varying",
                "date",
                "timestamp",
                "timestamp with time zone",
                "uuid",
                "json",
                "jsonb",
            ),
        )

    # ------------------------------------------------------------- connection
    def _connect(self) -> psycopg.Connection:
        config = self._config
        conn = psycopg.connect(
            host=config["host"],
            port=int(config.get("port", 5432)),
            dbname=config["database"],
            user=self._credentials.username,
            password=self._credentials.password,
            connect_timeout=int(
                config.get("connect_timeout_seconds", self._settings.connect_timeout_seconds)
            ),
            application_name="ainative-platform",
            autocommit=False,
            row_factory=tuple_row,
        )
        # psycopg3 applies read-only at the session level; combined with the
        # SELECT-only role this is the second line of defense (spec 9.2/12).
        conn.read_only = True
        return conn

    def test_connection(self) -> ConnectionHealth:
        started = time.perf_counter()
        try:
            with self._connect() as conn:
                row = conn.execute("SHOW server_version").fetchone()
                version = str(row[0]) if row else None
        except psycopg.Error as exc:
            return ConnectionHealth(
                status="UNAVAILABLE",
                error=_sanitize_error(exc),
                latency_ms=round((time.perf_counter() - started) * 1000, 2),
            )
        return ConnectionHealth(
            status="HEALTHY",
            server_version=version,
            latency_ms=round((time.perf_counter() - started) * 1000, 2),
        )

    # ----------------------------------------------------------- introspection
    def list_namespaces(self) -> list[Namespace]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT table_catalog, table_schema
                FROM information_schema.tables
                WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
                GROUP BY table_catalog, table_schema
                ORDER BY table_catalog, table_schema
                """
            ).fetchall()
        return [Namespace(catalog=str(r[0]), schema=str(r[1])) for r in rows]

    def list_tables(self, namespace: Namespace) -> list[TableRef]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT table_catalog, table_schema, table_name, table_type
                FROM information_schema.tables
                WHERE table_catalog = %s AND table_schema = %s
                  AND table_type IN ('BASE TABLE', 'VIEW')
                ORDER BY table_name
                """,
                (namespace.catalog, namespace.schema),
            ).fetchall()
        return [
            TableRef(
                catalog=str(r[0]),
                schema=str(r[1]),
                name=str(r[2]),
                object_type="table" if r[3] == "BASE TABLE" else "view",
            )
            for r in rows
        ]

    def describe_table(self, ref: TableRef) -> TableSchema:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT column_name, data_type, is_nullable, ordinal_position,
                       numeric_precision, numeric_scale, character_maximum_length
                FROM information_schema.columns
                WHERE table_catalog = %s AND table_schema = %s AND table_name = %s
                ORDER BY ordinal_position
                """,
                (ref.catalog, ref.schema, ref.name),
            ).fetchall()
        columns: list[ColumnDef] = []
        for row in rows:
            data_type = str(row[1])
            precision, scale = row[4], row[5]
            type_text = data_type
            if data_type == "numeric" and precision is not None:
                type_text = f"numeric({precision},{scale or 0})"
            elif data_type == "character varying" and row[6] is not None:
                type_text = f"character varying({row[6]})"
            columns.append(
                ColumnDef(
                    name=str(row[0]),
                    type=type_text,
                    nullable=(row[2] == "YES"),
                    ordinal=int(row[3]),
                )
            )
        payload = json.dumps(
            [{"name": c.name, "type": c.type, "nullable": c.nullable} for c in columns],
            sort_keys=True,
        )
        return TableSchema(
            ref=ref,
            columns=tuple(columns),
            schema_hash=hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        )

    def view_definition(self, ref: TableRef) -> str | None:
        """Controlled introspection helper for admin-confirmed secure views."""
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT pg_get_viewdef(format('%I.%I', %s::text, %s::text)::regclass, true)
                """,
                (ref.schema, ref.name),
            ).fetchone()
        return str(row[0]) if row and row[0] is not None else None

    # -------------------------------------------------------------- execution
    def open_execution(self, query: ValidatedQuery) -> ExecutionHandle:
        conn = self._connect()
        timeout_ms = max(1, int(query.timeout_seconds)) * 1000
        lock_ms = min(max(1, int(query.timeout_seconds)), 10) * 1000
        idle_ms = (max(1, int(query.timeout_seconds)) + 15) * 1000
        try:
            with conn.cursor() as cur:
                # SET does not accept parameter placeholders; the values are
                # validated integers computed here, never user SQL.
                cur.execute(f"SET statement_timeout = {timeout_ms}")
                cur.execute(f"SET lock_timeout = {lock_ms}")
                cur.execute(f"SET idle_in_transaction_session_timeout = {idle_ms}")
            conn.commit()
        except psycopg.Error:
            conn.close()
            raise
        handle = ExecutionHandle(
            query_id=query.query_id,
            dialect="postgres",
            worker_id="",
            fencing_token=0,
            connection_generation=1,
            native={
                "connection": conn,
                "backend_pid": conn.info.backend_pid,
                "generation": 1,
                "sql": prepare_driver_sql(query.sql, kind="postgres"),
            },
        )
        return handle

    def execute(
        self, handle: ExecutionHandle, query: ValidatedQuery
    ) -> Iterator[list[tuple]]:
        conn: psycopg.Connection = handle.native["connection"]
        cursor = conn.cursor(name=f"ainative_stream_{query.query_id.hex[:12]}")
        cursor.itersize = 1_000
        cursor.execute(handle.native["sql"], query.parameters or None)
        handle.columns = [
            ExecutionColumn(
                name=desc.name,
                type_name=type_name_for_oid(desc.type_code, desc.scale),
                type_code=desc.type_code,
                scale=desc.scale,
            )
            for desc in (cursor.description or [])
        ]
        try:
            while True:
                batch = cursor.fetchmany(1_000)
                if not batch:
                    break
                yield batch
        finally:
            cursor.close()

    def explain(self, handle: ExecutionHandle, query: ValidatedQuery) -> dict[str, Any]:
        conn: psycopg.Connection = handle.native["connection"]
        sql = handle.native["sql"].strip()
        if sql.upper().startswith("EXPLAIN"):
            sql = sql[len("EXPLAIN") :].strip()
        with conn.cursor() as cur:
            cur.execute(f"EXPLAIN (FORMAT JSON) {sql}", query.parameters or None)
            row = cur.fetchone()
        payload = row[0] if row else None
        conn.rollback()
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {"raw": payload}
        return {"plan": payload, "estimated_rows": None, "estimated_bytes": None}

    def cancel(self, handle: ExecutionHandle) -> CancelOutcome:
        conn: psycopg.Connection | None = handle.native.get("connection")
        if conn is None:
            return CancelOutcome(confirmed=False, detail="no live connection for handle")
        try:
            cancel_safe = getattr(conn, "cancel_safe", None)
            if callable(cancel_safe):
                cancel_safe(timeout=5.0)
            else:  # pragma: no cover - older psycopg
                conn.cancel()
        except Exception as exc:  # cancellation must never crash the worker
            return CancelOutcome(confirmed=False, detail=f"cancel request failed: {_sanitize_error(exc)['message']}")
        return CancelOutcome(
            confirmed=False,
            detail="cancel request sent; confirmation pending until the query returns",
        )

    def close(self, handle: ExecutionHandle) -> None:
        conn: psycopg.Connection | None = handle.native.pop("connection", None)
        if conn is not None:
            try:
                if not conn.closed:
                    conn.close()
            except Exception:  # pragma: no cover - defensive
                pass

    # ------------------------------------------------------------ error map
    def classify_execution_error(self, exc: BaseException) -> str:
        if isinstance(exc, psycopg.errors.QueryCanceled):
            return "cancelled"
        if isinstance(exc, psycopg.errors.ReadOnlySqlTransaction):
            return "readonly"
        if isinstance(exc, psycopg.errors.InsufficientPrivilege):
            return "permission"
        if isinstance(exc, psycopg.errors.UndefinedTable):
            return "schema"
        if isinstance(exc, psycopg.errors.SyntaxError):
            return "syntax"
        if isinstance(exc, psycopg.OperationalError):
            return "unavailable"
        return "error"

    def cancel_confirms_stop(self) -> bool:
        """PostgreSQL cancel is confirmed by the server-side statement deadline
        or by the query returning with SQLSTATE 57014."""
        return True
