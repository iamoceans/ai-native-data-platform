"""API request/response DTOs (spec sections 22-25).

These are the only types exposed through OpenAPI. Requests forbid unknown
fields so that misspelled parameters fail loudly instead of being ignored.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing_extensions import TypeAliasType

# Named recursive type (PEP 695 style via typing_extensions) so schema
# generation terminates on Python 3.11.
JsonValue = TypeAliasType(
    "JsonValue",
    "str | int | float | bool | None | list[JsonValue] | dict[str, JsonValue]",
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Errors (unified envelope, spec section 22)
# ---------------------------------------------------------------------------
class ErrorBody(BaseModel):
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    error: ErrorBody
    request_id: str
    trace_id: str


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginRequest(StrictModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=1024)


class ProfileResponse(BaseModel):
    id: uuid.UUID
    username: str
    roles: list[str]
    capabilities: list[str]


class LoginResponse(BaseModel):
    profile: ProfileResponse
    csrf_token: str


# ---------------------------------------------------------------------------
# Datasources
# ---------------------------------------------------------------------------
from app.datasource.config_models import (  # noqa: E402
    DorisConnectionConfig,
    MySQLConnectionConfig,
    PostgresConnectionConfig,
)

# The API accepts any engine config here; the service validates the payload
# against the config model for the declared kind (extra fields forbidden).
ConnectionConfig = PostgresConnectionConfig | MySQLConnectionConfig | DorisConnectionConfig


class DatasourceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    kind: Literal["postgres", "mysql", "doris"]
    connection_config: ConnectionConfig
    secret_ref: str = Field(
        min_length=3, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    enabled: bool = True


class DatasourcePatch(StrictModel):
    version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    connection_config: ConnectionConfig | None = None
    secret_ref: str | None = Field(
        default=None, min_length=3, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$"
    )
    enabled: bool | None = None


class ProviderCapabilitiesDTO(BaseModel):
    dialect: str
    explain: bool
    server_timeout: bool
    cancel: bool
    transactional_read_only: bool
    supported_types: list[str]


class DatasourceResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    connection_config: dict[str, Any]
    secret_ref: str
    capabilities: dict[str, Any]
    enabled: bool
    health_status: str
    created_by: uuid.UUID
    created_at: datetime
    version: int


class DatasourceListResponse(BaseModel):
    items: list[DatasourceResponse]
    next_cursor: str | None = None


class DatasourceTestResponse(BaseModel):
    status: str
    server_version: str | None
    latency_ms: float | None
    checked_at: datetime
    capabilities: ProviderCapabilitiesDTO | None = None
    error: ErrorBody | None = None


class CatalogRefreshRequest(StrictModel):
    schemas: list[str] = Field(default_factory=lambda: ["public"], min_length=1, max_length=10)
    secure_views: list[str] = Field(
        default_factory=list,
        description="View names the administrator has verified as safe to expose (no sensitive columns).",
    )


class CatalogRefreshResponse(BaseModel):
    datasource_id: uuid.UUID
    registered: int
    updated: int
    deactivated: int
    items: list["DatasetSummary"]
    skipped: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Datasets / metadata
# ---------------------------------------------------------------------------
class ColumnInfo(BaseModel):
    id: str | None = None  # stable column id (c0, c1, ...) for result sets
    name: str
    type: str
    nullable: bool | None = None
    scale: int | None = None
    classification: str | None = None


class DatasetSummary(BaseModel):
    id: uuid.UUID
    datasource_id: uuid.UUID
    catalog: str
    schema_name: str
    object_name: str
    object_type: str
    datahub_urn: str | None = None
    sync_status: str
    schema_hash: str | None = None
    active: bool
    last_synced_at: datetime | None = None


class DatasetContext(DatasetSummary):
    schema_version: int = 1
    description: str | None = None
    columns: list[ColumnInfo] = Field(default_factory=list)
    grain: list[str] | None = None
    business_timezone: str | None = None
    currency: str | None = None
    metric_keys: list[str] = Field(default_factory=list)
    join_keys: list[str] = Field(default_factory=list)
    lineage_status: str = "unknown"
    metadata_source: str = "platform_registry"
    metadata_stale: bool = False
    metadata_cached: bool = False
    schema_drift: bool = False
    datahub_url: str | None = None
    owners: list[str] = Field(default_factory=list)


class DatasetListResponse(BaseModel):
    items: list[DatasetSummary]
    next_cursor: str | None = None


class DatasetSchemaResponse(BaseModel):
    dataset_id: uuid.UUID
    schema_hash: str
    columns: list[ColumnInfo]
    fetched_at: datetime
    expires_at: datetime
    source: Literal["snapshot", "introspection"]


# ---------------------------------------------------------------------------
# Lineage (spec section 11)
# ---------------------------------------------------------------------------
class LineageNode(BaseModel):
    urn: str
    dataset_id: uuid.UUID | None = None
    name: str
    namespace: str | None = None
    platform: str | None = None
    degree: int = 1
    label: str = "unknown"
    mapped: bool = False


class AnalysisEvidenceEdge(BaseModel):
    query_id: str
    label: str = "analysis_evidence"
    status: str
    created_at: datetime | None = None


class LineageResponse(BaseModel):
    schema_version: int = 1
    dataset_id: uuid.UUID
    direction: str
    depth: int
    status: str
    message: str | None = None
    nodes: list[LineageNode] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    filtered_nodes: int = 0
    labels: list[str] = Field(default_factory=list)
    analysis_evidence: list[AnalysisEvidenceEdge] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Metrics (spec section 15)
# ---------------------------------------------------------------------------
class MetricSummary(BaseModel):
    metric_key: str
    version: int
    name: str
    unit: str
    currency: str | None = None
    timezone: str = "UTC"
    grain: list[str] = Field(default_factory=list)
    allowed_dimensions: list[str] = Field(default_factory=list)
    aggregation_kind: str
    datasets: list[str] = Field(default_factory=list)
    formula: str
    # The definition's own caveat (for example "禁止 AVG(每日或各组 eCPM)").
    # Surfaced so a user picking dimensions can read the aggregation constraint
    # the governance layer attached to the metric.
    notes: str | None = None


class MetricListResponse(BaseModel):
    items: list[MetricSummary]


# ---------------------------------------------------------------------------
# Agent sessions and analyses (spec sections 19-23)
# ---------------------------------------------------------------------------
class AgentSessionCreate(StrictModel):
    title: str = Field(min_length=1, max_length=200)


class AgentSessionResponse(BaseModel):
    id: uuid.UUID
    title: str
    created_at: datetime


class AgentMessageResponse(BaseModel):
    id: uuid.UUID
    role: str
    content: dict[str, Any]
    created_at: datetime


class AgentMessageListResponse(BaseModel):
    items: list[AgentMessageResponse]
    next_cursor: str | None = None


class AnalysisContext(StrictModel):
    metric_key: str = Field(min_length=2, max_length=128)
    baseline_start: date
    baseline_end: date
    current_start: date
    current_end: date
    dimensions: list[str] = Field(default_factory=list, max_length=3)
    filters: dict[str, str] = Field(default_factory=dict)
    data_complete: bool = True

    @model_validator(mode="after")
    def validate_periods(self) -> "AnalysisContext":
        if self.baseline_start >= self.baseline_end:
            raise ValueError("baseline period must be a non-empty half-open range")
        if self.current_start >= self.current_end:
            raise ValueError("current period must be a non-empty half-open range")
        return self


class AnalysisCreate(StrictModel):
    session_id: uuid.UUID
    question: str = Field(min_length=1, max_length=4000)
    context: AnalysisContext
    parent_id: uuid.UUID | None = None


class AnalysisSubmitResponse(BaseModel):
    analysis_id: uuid.UUID
    status: str


class AnalysisStepResponse(BaseModel):
    key: str
    status: str
    tool_name: str
    observation: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


class AnalysisDetail(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    parent_id: uuid.UUID | None = None
    status: str
    question: str
    context: dict[str, Any]
    plan: dict[str, Any]
    budget: dict[str, Any]
    state: dict[str, Any]
    checkpoint_version: int
    policy_revision: int
    final_report: dict[str, Any] | None = None
    steps: list[AnalysisStepResponse] = Field(default_factory=list)
    created_at: datetime
    finished_at: datetime | None = None


class ChartDrilldownRequest(StrictModel):
    dimension: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=256)
    period: Literal["baseline", "current"] = "current"


class ChartDrilldownResponse(BaseModel):
    analysis_id: uuid.UUID
    status: str
    parent_id: uuid.UUID
    filters: dict[str, str]


class AnalysisListResponse(BaseModel):
    items: list[AnalysisDetail]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Ingestion (spec sections 10, 22)
# ---------------------------------------------------------------------------
class IngestionTaskResponse(BaseModel):
    id: uuid.UUID
    datasource_id: uuid.UUID
    status: str
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    summary: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
class QueryLimits(BaseModel):
    max_rows: int | None = Field(default=None, ge=1, le=100_000)
    max_bytes: int | None = Field(default=None, ge=1024, le=100 * 1024 * 1024)
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)


class QuerySubmitRequest(StrictModel):
    datasource_id: uuid.UUID
    sql: str = Field(min_length=1)
    parameters: dict[str, JsonValue] = Field(default_factory=dict)
    limits: QueryLimits = Field(default_factory=QueryLimits)
    purpose: Literal["query", "explain"] = "query"


class QuerySubmitResponse(BaseModel):
    query_id: uuid.UUID
    status: str


class ResultSummary(BaseModel):
    id: uuid.UUID
    row_count: int
    byte_count: int
    truncated: bool
    content_hash: str
    created_at: datetime
    expires_at: datetime


class QueryDetail(BaseModel):
    id: uuid.UUID
    datasource_id: uuid.UUID
    analysis_id: uuid.UUID | None = None
    status: str
    purpose: str
    sql: str
    validated_sql: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    sql_hash: str | None = None
    limits: dict[str, Any] = Field(default_factory=dict)
    policy_revision: int
    attempt: int
    error: ErrorBody | None = None
    result: ResultSummary | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancel_requested_at: datetime | None = None


class QueryListResponse(BaseModel):
    items: list[QueryDetail]
    next_cursor: str | None = None


class ResultPayload(BaseModel):
    id: uuid.UUID
    row_count: int
    truncated: bool
    columns: list[ColumnInfo]
    rows: list[list[JsonValue]]
    next_cursor: str | None = None
    expires_at: datetime


class QueryResultResponse(BaseModel):
    query_id: uuid.UUID
    status: str
    result: ResultPayload | None = None
    warnings: list[str] = Field(default_factory=list)
    trace_id: str


# ---------------------------------------------------------------------------
# Saved queries
# ---------------------------------------------------------------------------
class SavedQueryCreate(StrictModel):
    datasource_id: uuid.UUID
    title: str = Field(min_length=1, max_length=200)
    sql: str = Field(min_length=1)


class SavedQueryResponse(BaseModel):
    id: uuid.UUID
    datasource_id: uuid.UUID
    title: str
    sql: str
    created_at: datetime


class SavedQueryListResponse(BaseModel):
    items: list[SavedQueryResponse]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Permission requests
# ---------------------------------------------------------------------------
class PermissionRequestCreate(StrictModel):
    dataset_id: uuid.UUID
    reason: str = Field(min_length=1, max_length=2000)


class PermissionRequestResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    dataset_id: uuid.UUID
    reason: str
    status: str
    created_at: datetime


class PermissionRequestListResponse(BaseModel):
    items: list[PermissionRequestResponse]
    next_cursor: str | None = None


class PermissionRequestApprove(StrictModel):
    """Approving a request creates a real grant for the requester's role.

    The administrator names the role and the action explicitly: the platform
    never infers "what the user probably needs" from the request text.
    """

    role_id: uuid.UUID
    action: Literal["discover", "query"] = "query"
    expires_at: datetime | None = None


class PermissionRequestReject(StrictModel):
    note: str | None = Field(default=None, max_length=500)


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
class RoleResponse(BaseModel):
    id: uuid.UUID
    name: str
    capabilities: list[str]


class RoleCreate(StrictModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_-]*$")


class UserCreate(StrictModel):
    username: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._-]+$")
    password: str = Field(min_length=12, max_length=1024)
    role_ids: list[uuid.UUID] = Field(default_factory=list)


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    active: bool
    roles: list[RoleResponse]
    created_at: datetime


class UserListResponse(BaseModel):
    items: list[UserResponse]
    next_cursor: str | None = None


class UserRolesUpdate(StrictModel):
    role_ids: list[uuid.UUID]


class GrantCreate(StrictModel):
    role_id: uuid.UUID
    dataset_id: uuid.UUID
    action: Literal["discover", "query"]
    expires_at: datetime | None = None


class GrantResponse(BaseModel):
    id: uuid.UUID
    role_id: uuid.UUID
    dataset_id: uuid.UUID
    action: str
    expires_at: datetime | None = None
    created_by: uuid.UUID


class GrantListResponse(BaseModel):
    items: list[GrantResponse]
    next_cursor: str | None = None


class AuditEntry(BaseModel):
    id: int
    actor_id: uuid.UUID | None = None
    action: str
    resource_type: str
    resource_id: str | None = None
    trace_id: str
    outcome: str
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class AuditListResponse(BaseModel):
    items: list[AuditEntry]
    next_cursor: str | None = None


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
class HealthResponse(BaseModel):
    status: str
    checks: dict[str, str] = Field(default_factory=dict)


CatalogRefreshResponse.model_rebuild()
