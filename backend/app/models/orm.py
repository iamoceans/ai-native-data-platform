"""ORM models for the control database (spec section 8 DDL).

The DDL in the specification is the frozen persistence contract. This module
mirrors it one-to-one; Alembic migration 0001 is generated from this metadata
(and verified by comparing the migrated schema against the DDL list).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    text,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)


class UserRole(Base):
    __tablename__ = "user_roles"
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id"), primary_key=True
    )
    __table_args__ = (Index("ix_roles_users", "role_id", "user_id"),)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    csrf_hash: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Datasource(Base):
    __tablename__ = "datasources"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    connection_config: Mapped[dict] = mapped_column(JSONB, nullable=False)
    secret_ref: Mapped[str] = mapped_column(Text, nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    health_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="UNKNOWN", server_default=text("'UNKNOWN'")
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default=text("1"))
    __table_args__ = (
        CheckConstraint("kind IN ('postgres','mysql','doris')", name="datasources_kind_check"),
    )


class Dataset(Base):
    __tablename__ = "datasets"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    datasource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasources.id"), nullable=False
    )
    catalog_name: Mapped[str] = mapped_column(Text, nullable=False)
    schema_name: Mapped[str] = mapped_column(Text, nullable=False)
    object_name: Mapped[str] = mapped_column(Text, nullable=False)
    datahub_urn: Mapped[str | None] = mapped_column(Text, unique=True)
    object_type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_hash: Mapped[str | None] = mapped_column(Text)
    sync_status: Mapped[str] = mapped_column(
        Text, nullable=False, default="PENDING", server_default=text("'PENDING'")
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        UniqueConstraint(
            "datasource_id",
            "catalog_name",
            "schema_name",
            "object_name",
            name="datasets_datasource_id_catalog_name_schema_name_object_name_key",
        ),
    )


class Permission(Base):
    __tablename__ = "permissions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("roles.id"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasets.id"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    __table_args__ = (
        UniqueConstraint("role_id", "dataset_id", "action", name="permissions_key"),
        CheckConstraint("action IN ('discover','query')", name="permissions_action_check"),
        Index("ix_permissions_dataset", "dataset_id", "role_id"),
    )


class PolicyState(Base):
    __tablename__ = "policy_state"
    singleton: Mapped[bool] = mapped_column(Boolean, primary_key=True, default=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1, server_default=text("1"))
    __table_args__ = (
        CheckConstraint("singleton", name="policy_state_singleton_check"),
    )


class MetadataSnapshot(Base):
    __tablename__ = "metadata_snapshots"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasets.id"), nullable=False
    )
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    schema_hash: Mapped[str] = mapped_column(Text, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        Index("ix_snapshots_dataset", "dataset_id", "fetched_at"),
    )


class MetricDefinition(Base):
    __tablename__ = "metric_definitions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    metric_key: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))
    __table_args__ = (UniqueConstraint("metric_key", "version", name="metric_definitions_key"),)


class AgentSession(Base):
    __tablename__ = "agent_sessions"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AgentMessage(Base):
    __tablename__ = "agent_messages"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_sessions.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    __table_args__ = (
        CheckConstraint("role IN ('user','assistant')", name="agent_messages_role_check"),
        Index("ix_messages_session", "session_id", "created_at", "id"),
    )


class AnalysisTask(Base):
    __tablename__ = "analysis_tasks"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("agent_sessions.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("analysis_tasks.id")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    context: Mapped[dict] = mapped_column(JSONB, nullable=False)
    plan: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    budget: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    checkpoint_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    policy_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    final_report: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('CREATED','UNDERSTANDING','RETRIEVING','WAITING_INPUT','PLANNING',"
            "'EXECUTING','OBSERVING','SYNTHESIZING','COMPLETED','PARTIAL','FAILED','CANCELLED')",
            name="ck_analysis_status",
        ),
        Index("ix_analyses_owner_time", "user_id", "created_at"),
        Index("ix_analysis_session", "session_id", "created_at"),
    )


class QueryJob(Base):
    __tablename__ = "query_jobs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    datasource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasources.id"), nullable=False
    )
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("analysis_tasks.id")
    )
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    original_sql: Mapped[str] = mapped_column(Text, nullable=False)
    validated_sql: Mapped[str | None] = mapped_column(Text)
    bound_parameters: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    sql_hash: Mapped[str | None] = mapped_column(Text)
    limits: Mapped[dict] = mapped_column(JSONB, nullable=False)
    policy_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    provider_handle: Mapped[dict | None] = mapped_column(JSONB)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("purpose IN ('query','explain')", name="query_jobs_purpose_check"),
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED',"
            "'CANCEL_REQUESTED','CANCELLED','TIMED_OUT','LOST')",
            name="ck_query_status",
        ),
        Index("ix_queries_owner_time", "user_id", "created_at"),
        Index("ix_queries_analysis", "analysis_id"),
    )


class QueryDependency(Base):
    __tablename__ = "query_dependencies"
    query_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("query_jobs.id"), primary_key=True
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasets.id"), primary_key=True
    )
    metadata_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("metadata_snapshots.id")
    )
    __table_args__ = (Index("ix_dependencies_dataset", "dataset_id", "query_id"),)


class IngestionTask(Base):
    __tablename__ = "ingestion_tasks"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    datasource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasources.id"), nullable=False
    )
    requested_by: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    recipe_hash: Mapped[str] = mapped_column(Text, nullable=False)
    ingestion_version: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('QUEUED','RUNNING','SUCCEEDED','FAILED','LOST')",
            name="ingestion_tasks_status_check",
        ),
        Index("ix_ingestions_source", "datasource_id", "created_at"),
    )


class ExecutionCapacity(Base):
    __tablename__ = "execution_capacity"
    scope_type: Mapped[str] = mapped_column(Text, primary_key=True)
    scope_id: Mapped[str] = mapped_column(Text, primary_key=True)
    max_running: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        CheckConstraint(
            "scope_type IN ('global','datasource','user')",
            name="execution_capacity_scope_type_check",
        ),
        CheckConstraint("max_running > 0", name="execution_capacity_max_running_check"),
    )


class ExecutionLease(Base):
    __tablename__ = "execution_leases"
    query_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("query_jobs.id"), primary_key=True
    )
    datasource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasources.id"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    worker_id: Mapped[str] = mapped_column(Text, nullable=False)
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (
        CheckConstraint("state IN ('ACTIVE','SUSPECT','RELEASED')", name="execution_leases_state_check"),
        Index("ix_leases_source", "datasource_id", "state"),
        Index("ix_leases_user", "user_id", "state"),
    )


class TaskQueue(Base):
    __tablename__ = "task_queue"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False)
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    worker_id: Mapped[str | None] = mapped_column(Text)
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default=text("0"))
    fencing_token: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, server_default=text("0"))
    __table_args__ = (
        CheckConstraint("kind IN ('query','analysis','ingestion')", name="task_queue_kind_check"),
        CheckConstraint("state IN ('READY','LEASED','DONE','FAILED')", name="ck_queue_state"),
        UniqueConstraint("kind", "resource_id", name="task_queue_kind_resource_id_key"),
        Index("ix_queue_claim", "kind", "state", "available_at"),
    )


class QueryResult(Base):
    __tablename__ = "query_results"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    query_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("query_jobs.id"), nullable=False, unique=True
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    format: Mapped[str] = mapped_column(Text, nullable=False)
    schema_json: Mapped[dict] = mapped_column(JSONB, nullable=False)
    row_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    byte_count: Mapped[int] = mapped_column(BigInteger, nullable=False)
    truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_results_expiry", "expires_at"),)


class AnalysisStep(Base):
    __tablename__ = "analysis_steps"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("analysis_tasks.id"), nullable=False
    )
    step_key: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    tool_name: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[dict] = mapped_column(JSONB, nullable=False)
    output_refs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    observation: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[dict | None] = mapped_column(JSONB)
    __table_args__ = (
        UniqueConstraint("analysis_id", "step_key", name="analysis_steps_analysis_id_step_key_key"),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','SUCCEEDED','FAILED','SKIPPED')",
            name="ck_step_status",
        ),
    )


class AnalysisArtifact(Base):
    __tablename__ = "analysis_artifacts"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    analysis_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("analysis_tasks.id"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    dependency_query_ids: Mapped[list | dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        CheckConstraint("kind IN ('calculation','chart','evidence')", name="analysis_artifacts_kind_check"),
        Index("ix_artifacts_analysis", "analysis_id"),
    )


class TaskEvent(Base):
    __tablename__ = "task_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    resource_kind: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    __table_args__ = (Index("ix_events_resource", "resource_kind", "resource_id", "id"),)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(Text, nullable=False)
    resource_type: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    __table_args__ = (Index("ix_audit_time", "created_at"),)


class PermissionRequest(Base):
    __tablename__ = "permission_requests"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    dataset_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasets.id"), nullable=False
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    __table_args__ = (
        CheckConstraint(
            "status IN ('REQUESTED','MOCK_APPROVED','APPROVED','REJECTED')",
            name="permission_requests_status_check",
        ),
    )


class SavedQuery(Base):
    __tablename__ = "saved_queries"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), nullable=False
    )
    datasource_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("datasources.id"), nullable=False
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    sql_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id"), primary_key=True
    )
    route: Mapped[str] = mapped_column(Text, primary_key=True)
    key: Mapped[str] = mapped_column(Text, primary_key=True)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    resource_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
