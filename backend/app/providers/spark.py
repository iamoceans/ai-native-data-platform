"""Spark Thrift Server provider for tables in a shared Hive Metastore.

The gateway owns SQL and grant validation. Scalar parameters use AST literals
with an exact parse round trip, never Impyla string formatting. TLS always verifies
the server certificate and PLAIN authentication requires encrypted transport.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Iterator
from typing import Any

from impala.dbapi import connect
from impala._thrift_gen.TCLIService.ttypes import TCancelOperationReq

from app.config import Settings
from app.models.orm import Datasource
from app.providers.base import (
    CancelOutcome, ColumnDef, ConnectionHealth, ExecutionColumn, ExecutionHandle,
    Namespace, ProviderCapabilities, ProviderCredentials, TableRef, TableSchema,
    ValidatedQuery,
)

from app.providers.sql_prep import bind_spark_parameters

logger = logging.getLogger(__name__)


class SparkOperationCancelled(RuntimeError):
    """The operation stopped because this handle was cancelled.

    Spark has no typed cancellation error to catch, so the provider raises this
    one and ``classify_execution_error`` maps it to ``cancelled``; the executor
    then decides between CANCELLED and TIMED_OUT from its own monitor state.
    """


class SparkOperationTimedOut(RuntimeError):
    """The Thrift Server aborted the operation at its own query timeout."""


# Hive's TOperationState stops at PENDING_STATE (7); the server in this preview
# also reports TIMEDOUT_STATE (8) when `spark.sql.thriftServer.queryTimeout`
# fires. Impyla 0.24.0 does not know that value, so `get_status()` raises
# `KeyError(8)` instead of returning a state (verified against
# hive-service-rpc-3.1.3.jar in the pinned Spark image, 2026-09-28).
SERVER_TIMEOUT_STATE = 8


class SparkProvider:
    def __init__(self, *, connection_config: dict[str, Any], credentials: ProviderCredentials) -> None:
        self._config = connection_config
        self._credentials = credentials

    @classmethod
    def from_datasource(
        cls, datasource: Datasource, credentials: ProviderCredentials, settings: Settings
    ) -> "SparkProvider":
        return cls(connection_config=dict(datasource.connection_config), credentials=credentials)

    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            dialect="spark", explain=True, server_timeout=False, cancel=True,
            transactional_read_only=False,
            supported_types=("boolean", "tinyint", "smallint", "int", "bigint", "float", "double",
                             "decimal", "string", "varchar", "char", "date", "timestamp", "binary",
                             "array", "map", "struct"),
        )

    def _connect(self, *, timeout_seconds: int | None = None):
        auth = self._config.get("auth_mechanism", "NOSASL")
        tls = bool(self._config.get("use_ssl", False))
        if auth not in {"NOSASL", "PLAIN"} or auth == "PLAIN" and not tls:
            raise ValueError("Spark PLAIN authentication requires verified TLS")
        options = {"use_ssl": True, "verify_cert": True, "ca_cert": self._config.get("ca_cert")} if tls else {}
        return connect(
            host=self._config["host"], port=int(self._config["port"]),
            database=self._config["database"],
            timeout=timeout_seconds or int(self._config["connect_timeout_seconds"]),
            auth_mechanism=auth, user=self._credentials.username,
            password=self._credentials.password, **options,
        )

    def test_connection(self) -> ConnectionHealth:
        started = time.perf_counter()
        try:
            connection = self._connect()
            try:
                cursor = connection.cursor()
                try:
                    cursor.execute("SELECT 1")
                    cursor.fetchone()
                finally:
                    cursor.close()
            finally:
                connection.close()
        except Exception as exc:
            return ConnectionHealth(
                status="UNAVAILABLE", latency_ms=round((time.perf_counter() - started) * 1000, 2),
                error={"code": "DATASOURCE_UNAVAILABLE", "message": str(exc)[:300]},
            )
        return ConnectionHealth(status="HEALTHY", latency_ms=round((time.perf_counter() - started) * 1000, 2))

    def list_namespaces(self) -> list[Namespace]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.get_databases()
                names = [str(row[0]) for row in cursor.fetchall()]
            finally:
                cursor.close()
        finally:
            connection.close()
        return [Namespace(catalog="spark_catalog", schema=name) for name in names]

    def list_tables(self, namespace: Namespace) -> list[TableRef]:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                cursor.get_tables(namespace.schema)
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        return [
            TableRef(catalog="spark_catalog", schema=namespace.schema,
                     name=str(row[2]), object_type="view" if str(row[3]).upper() == "VIEW" else "table")
            for row in rows if len(row) >= 4 and str(row[1]) == namespace.schema
            and str(row[3]).upper() in {"VIEW", "TABLE", "MANAGED_TABLE", "EXTERNAL_TABLE"}
        ]

    def describe_table(self, ref: TableRef) -> TableSchema:
        connection = self._connect()
        try:
            cursor = connection.cursor()
            try:
                raw = cursor.get_table_schema(ref.name, ref.schema)
            finally:
                cursor.close()
        finally:
            connection.close()
        columns = tuple(
            ColumnDef(name=str(name), type=str(type_name).lower(), ordinal=index)
            for index, (name, type_name) in enumerate(raw, start=1)
        )
        payload = json.dumps(
            [{"name": col.name, "type": col.type, "nullable": col.nullable} for col in columns],
            sort_keys=True,
        )
        return TableSchema(ref=ref, columns=columns, schema_hash=hashlib.sha256(payload.encode()).hexdigest())

    def open_execution(self, query: ValidatedQuery) -> ExecutionHandle:
        bound_sql = bind_spark_parameters(query.sql, query.parameters)
        # Impyla applies one socket timeout to connect and subsequent RPCs.
        # Result fetches need at least the query deadline to avoid failing a
        # healthy long-running SELECT while the worker monitors cancellation.
        connection = self._connect(
            timeout_seconds=max(
                int(query.timeout_seconds) + 5,
                int(self._config["connect_timeout_seconds"]),
            )
        )
        try:
            cursor = connection.cursor()
        except Exception:
            connection.close()
            raise
        return ExecutionHandle(
            query_id=query.query_id, dialect="spark", worker_id="", fencing_token=0,
            native={"connection": connection, "cursor": cursor, "bound_sql": bound_sql},
        )

    def execute(self, handle: ExecutionHandle, query: ValidatedQuery) -> Iterator[list[tuple]]:
        bound_sql = bind_spark_parameters(query.sql, query.parameters)
        cursor = handle.native["cursor"]
        if handle.native.get("cancel_requested"):
            raise SparkOperationCancelled("operation cancelled before submission")
        cursor.execute_async(bound_sql)
        # Cancellation can arrive while ExecuteStatement is publishing its
        # operation handle. Send it once the handle exists as well.
        if handle.native.get("cancel_requested"):
            self.cancel(handle)
        while True:
            try:
                executing = cursor.is_executing()
            except Exception as exc:
                self._translate_engine_failure(handle, exc)
                raise
            if not executing:
                break
            time.sleep(0.1)
        handle.columns = [
            ExecutionColumn(name=str(entry[0]), type_name=str(entry[1]).lower())
            for entry in (cursor.description or [])
        ]
        while True:
            try:
                batch = cursor.fetchmany(1_000)
            except Exception as exc:
                self._translate_engine_failure(handle, exc)
                raise
            if not batch:
                break
            yield list(batch)

    @staticmethod
    def _translate_engine_failure(handle: ExecutionHandle, exc: Exception) -> None:
        """Re-raise an Impyla failure as something the executor can classify.

        Impyla cannot name two states the server reports, and both were observed
        on the live fixture:

        * ``TIMEDOUT_STATE`` (8) after the server's own query timeout - impyla's
          enum stops at ``PENDING_STATE``, so ``get_status()`` raises
          ``KeyError(8)``;
        * anything raised after our own cancel: a cancelled operation stops
          reporting a state (``KeyError: None``), and the executor must record a
          cancellation, not fail a job that CANCEL_REQUESTED cannot fail into.
        """
        if isinstance(exc, KeyError) and exc.args and exc.args[0] == SERVER_TIMEOUT_STATE:
            raise SparkOperationTimedOut(
                "the Thrift Server stopped the operation at its query timeout"
            ) from exc
        if handle.native.get("cancel_requested"):
            raise SparkOperationCancelled(
                f"operation stopped after cancel: {type(exc).__name__}: {str(exc)[:200]}"
            ) from exc

    def explain(self, handle: ExecutionHandle, query: ValidatedQuery) -> dict[str, Any]:
        bound_sql = bind_spark_parameters(query.sql, query.parameters)
        cursor = handle.native["cursor"]
        cursor.execute(f"EXPLAIN {bound_sql}")
        return {"plan": "\n".join(str(row[0]) for row in cursor.fetchall()),
                "estimated_rows": None, "estimated_bytes": None}

    def cancel(self, handle: ExecutionHandle) -> CancelOutcome:
        handle.native["cancel_requested"] = True
        cursor = handle.native.get("cursor")
        operation = getattr(cursor, "_last_operation", None)
        if operation is None:
            return CancelOutcome(confirmed=False, detail="no Spark operation")
        # Impyla's Thrift client is not thread-safe. A monitor must never read
        # CancelOperation's response from the execution/status/fetch socket.
        # The pinned HS2 protocol addresses an operation by handle, so a fresh
        # authenticated transport can cancel it without touching cursor state.
        connection = None
        try:
            connection = self._connect(timeout_seconds=int(self._config["connect_timeout_seconds"]))
            connection.service._rpc(
                "CancelOperation", TCancelOperationReq(operationHandle=operation.handle), True,
            )
        except Exception as exc:
            return CancelOutcome(confirmed=False, detail=f"Spark cancel failed: {str(exc)[:200]}")
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    logger.warning("Spark cancel connection close failed", exc_info=True)
        return CancelOutcome(confirmed=False, detail="Spark cancel requested")

    def close(self, handle: ExecutionHandle) -> None:
        cursor = handle.native.pop("cursor", None)
        connection = handle.native.pop("connection", None)
        try:
            if cursor is not None:
                cursor.close()
        except Exception:
            logger.warning("Spark cursor close failed", exc_info=True)
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    logger.warning("Spark connection close failed", exc_info=True)

    def classify_execution_error(self, exc: BaseException) -> str:
        if isinstance(exc, SparkOperationCancelled):
            return "cancelled"
        if isinstance(exc, SparkOperationTimedOut):
            return "timeout"
        message = str(exc).lower()
        if "cancel" in message or "interrupt" in message:
            return "cancelled"
        if "timed out" in message or "timeout" in message:
            return "timeout"
        if "permission" in message or "access denied" in message:
            return "permission"
        if "not found" in message or "cannot resolve" in message:
            return "schema"
        if "parseexception" in message or "syntax" in message:
            return "syntax"
        return "error"

    def cancel_confirms_stop(self) -> bool:
        return False
