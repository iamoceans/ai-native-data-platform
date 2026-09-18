"""Provider protocol and shared types (spec section 9.1)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator, Protocol


@dataclass(frozen=True)
class ExecutionContext:
    user_id: uuid.UUID
    request_id: uuid.UUID
    trace_id: str
    policy_revision: int
    deadline_at: datetime


@dataclass(frozen=True)
class ProviderCapabilities:
    dialect: str
    explain: bool
    server_timeout: bool
    cancel: bool
    transactional_read_only: bool
    supported_types: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "dialect": self.dialect,
            "explain": self.explain,
            "server_timeout": self.server_timeout,
            "cancel": self.cancel,
            "transactional_read_only": self.transactional_read_only,
            "supported_types": list(self.supported_types),
        }


@dataclass(frozen=True)
class ValidatedQuery:
    query_id: uuid.UUID
    sql: str
    parameters: dict[str, object]
    dataset_ids: tuple[uuid.UUID, ...]
    schema_hashes: dict[str, str]
    policy_revision: int
    max_rows: int
    max_bytes: int
    timeout_seconds: int


@dataclass(frozen=True)
class ProviderCredentials:
    username: str
    password: str


@dataclass(frozen=True)
class ConnectionHealth:
    status: str
    server_version: str | None = None
    latency_ms: float | None = None
    error: dict[str, Any] | None = None


@dataclass(frozen=True)
class Namespace:
    catalog: str
    schema: str


@dataclass(frozen=True)
class TableRef:
    catalog: str
    schema: str
    name: str
    object_type: str = "table"


@dataclass(frozen=True)
class ColumnDef:
    name: str
    type: str
    nullable: bool | None = None
    ordinal: int = 0


@dataclass(frozen=True)
class TableSchema:
    ref: TableRef
    columns: tuple[ColumnDef, ...]
    schema_hash: str


@dataclass
class ExecutionColumn:
    name: str
    type_name: str
    type_code: int | None = None
    scale: int | None = None


@dataclass
class ExecutionHandle:
    query_id: uuid.UUID
    dialect: str
    worker_id: str
    fencing_token: int
    connection_generation: int = 1
    columns: list[ExecutionColumn] | None = None
    native: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CancelOutcome:
    confirmed: bool
    detail: str


class QueryProvider(Protocol):
    def capabilities(self) -> ProviderCapabilities: ...

    def test_connection(self) -> ConnectionHealth: ...

    def list_namespaces(self) -> list[Namespace]: ...

    def list_tables(self, namespace: Namespace) -> list[TableRef]: ...

    def describe_table(self, ref: TableRef) -> TableSchema: ...

    def open_execution(self, query: ValidatedQuery) -> ExecutionHandle: ...

    def execute(self, handle: ExecutionHandle, query: ValidatedQuery) -> Iterator[list[tuple]]: ...

    def explain(self, handle: ExecutionHandle, query: ValidatedQuery) -> dict[str, Any]: ...

    def cancel(self, handle: ExecutionHandle) -> CancelOutcome: ...

    def close(self, handle: ExecutionHandle) -> None: ...

    def classify_execution_error(self, exc: BaseException) -> str:
        """Engine-specific classification: cancelled / timeout / permission /
        readonly / schema / syntax / unavailable / error."""
        ...

    def cancel_confirms_stop(self) -> bool:
        """True when the engine confirms cancellation synchronously (PostgreSQL
        SQLSTATE 57014); False when cancellation is asynchronous (MySQL/Doris)."""
        ...
