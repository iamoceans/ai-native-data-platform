"""Shared constants: enums, statuses, error codes (spec sections 8, 13, 22, 29)."""

from __future__ import annotations

from enum import StrEnum

SCHEMA_VERSION = 1


class SourceKind(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    DORIS = "doris"
    SPARK = "spark"


class RoleName(StrEnum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    ADMIN = "admin"


class DatasetAction(StrEnum):
    DISCOVER = "discover"
    QUERY = "query"


class QueryStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    LOST = "LOST"


TERMINAL_QUERY_STATUSES = frozenset(
    {
        QueryStatus.SUCCEEDED,
        QueryStatus.FAILED,
        QueryStatus.CANCELLED,
        QueryStatus.TIMED_OUT,
        QueryStatus.LOST,
    }
)

# Explicit state machine (spec section 13). Nothing else may write a status.
QUERY_TRANSITIONS: dict[QueryStatus, frozenset[QueryStatus]] = {
    QueryStatus.QUEUED: frozenset(
        {QueryStatus.RUNNING, QueryStatus.CANCELLED, QueryStatus.FAILED}
    ),
    QueryStatus.RUNNING: frozenset(
        {
            QueryStatus.SUCCEEDED,
            QueryStatus.FAILED,
            QueryStatus.CANCEL_REQUESTED,
            QueryStatus.TIMED_OUT,
            QueryStatus.LOST,
        }
    ),
    QueryStatus.CANCEL_REQUESTED: frozenset(
        {QueryStatus.CANCELLED, QueryStatus.TIMED_OUT, QueryStatus.LOST}
    ),
}


class QueueState(StrEnum):
    READY = "READY"
    LEASED = "LEASED"
    DONE = "DONE"
    FAILED = "FAILED"


class DatasetSyncStatus(StrEnum):
    PENDING = "PENDING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"


class LeaseState(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPECT = "SUSPECT"
    RELEASED = "RELEASED"


class IngestionStatus(StrEnum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    LOST = "LOST"


class HealthStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNAVAILABLE = "UNAVAILABLE"


class PermissionRequestStatus(StrEnum):
    REQUESTED = "REQUESTED"
    MOCK_APPROVED = "MOCK_APPROVED"
    REJECTED = "REJECTED"


class ConversationTurnStatus(StrEnum):
    """Lifecycle of one accepted user input in a conversation (design 8.3)."""

    QUEUED = "QUEUED"
    RESOLVING = "RESOLVING"
    WAITING_INPUT = "WAITING_INPUT"
    ANALYZING = "ANALYZING"
    RESPONDING = "RESPONDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class CompletenessState(StrEnum):
    """Three-state data completeness for an analysis window (design 6.1).

    The platform cannot verify a user's claim, so "unverified" is its own state.
    Only `complete` unlocks attribution; `unknown` still reports the observed
    change, while `incomplete` withholds even that.
    """

    UNKNOWN = "unknown"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"

    @classmethod
    def from_bool(cls, value: bool | None) -> "CompletenessState":
        """The legacy `data_complete` boolean: a claim, never a verification."""
        if value is None:
            return cls.UNKNOWN
        return cls.COMPLETE if value else cls.INCOMPLETE


# WAITING_INPUT is terminal for the turn: the clarification message is committed
# and the next user message opens a new turn.
TERMINAL_CONVERSATION_TURN_STATUSES = frozenset(
    {
        ConversationTurnStatus.WAITING_INPUT,
        ConversationTurnStatus.COMPLETED,
        ConversationTurnStatus.FAILED,
        ConversationTurnStatus.CANCELLED,
    }
)

# At most one turn per session may be in these states (partial unique index).
ACTIVE_CONVERSATION_TURN_STATUSES = frozenset(
    {
        ConversationTurnStatus.QUEUED,
        ConversationTurnStatus.RESOLVING,
        ConversationTurnStatus.ANALYZING,
        ConversationTurnStatus.RESPONDING,
    }
)

# Explicit state machine; nothing else may write a turn status.
CONVERSATION_TURN_TRANSITIONS: dict[
    ConversationTurnStatus, frozenset[ConversationTurnStatus]
] = {
    ConversationTurnStatus.QUEUED: frozenset(
        {ConversationTurnStatus.RESOLVING, ConversationTurnStatus.FAILED,
         ConversationTurnStatus.CANCELLED}
    ),
    ConversationTurnStatus.RESOLVING: frozenset(
        {ConversationTurnStatus.WAITING_INPUT, ConversationTurnStatus.ANALYZING,
         ConversationTurnStatus.RESPONDING, ConversationTurnStatus.FAILED,
         ConversationTurnStatus.CANCELLED}
    ),
    ConversationTurnStatus.ANALYZING: frozenset(
        {ConversationTurnStatus.RESPONDING, ConversationTurnStatus.FAILED,
         ConversationTurnStatus.CANCELLED}
    ),
    ConversationTurnStatus.RESPONDING: frozenset(
        {ConversationTurnStatus.COMPLETED, ConversationTurnStatus.FAILED,
         ConversationTurnStatus.CANCELLED}
    ),
}


class SkillSource(StrEnum):
    """Where a skill identity came from (design 8.2)."""

    BUILTIN = "builtin"
    UPLOAD = "upload"


class ErrorCode(StrEnum):
    REQUEST_TOO_LARGE = "REQUEST_TOO_LARGE"
    # auth / policy
    UNAUTHENTICATED = "UNAUTHENTICATED"
    FORBIDDEN = "FORBIDDEN"
    CSRF_INVALID = "CSRF_INVALID"
    RATE_LIMITED = "RATE_LIMITED"
    NOT_FOUND = "NOT_FOUND"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    CONFLICT = "CONFLICT"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"
    # gateway
    SQL_SYNTAX_ERROR = "SQL_SYNTAX_ERROR"
    SQL_FORBIDDEN = "SQL_FORBIDDEN"
    SQL_UNSUPPORTED = "SQL_UNSUPPORTED"
    QUERY_TOO_LARGE = "QUERY_TOO_LARGE"
    DATASET_NOT_REGISTERED = "DATASET_NOT_REGISTERED"
    AMBIGUOUS_TABLE_REFERENCE = "AMBIGUOUS_TABLE_REFERENCE"
    SCHEMA_CHANGED = "SCHEMA_CHANGED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    LIMIT_INVALID = "LIMIT_INVALID"
    # execution
    DATASOURCE_UNAVAILABLE = "DATASOURCE_UNAVAILABLE"
    DATASOURCE_UNSUPPORTED = "DATASOURCE_UNSUPPORTED"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    QUERY_CANCELLED = "QUERY_CANCELLED"
    QUEUE_TIMEOUT = "QUEUE_TIMEOUT"
    QUERY_LOST = "QUERY_LOST"
    EXECUTION_ERROR = "EXECUTION_ERROR"
    RESULT_TRUNCATED = "RESULT_TRUNCATED"
    RESULT_UNAVAILABLE = "RESULT_UNAVAILABLE"
    RESULT_EXPIRED = "RESULT_EXPIRED"
    EVENT_CURSOR_EXPIRED = "EVENT_CURSOR_EXPIRED"
    # metadata
    METADATA_UNAVAILABLE = "METADATA_UNAVAILABLE"
    METADATA_SYNC_NOT_AVAILABLE = "METADATA_SYNC_NOT_AVAILABLE"
    # agent / conversation
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    ANALYSIS_EXECUTION_ERROR = "ANALYSIS_EXECUTION_ERROR"
    # conversation (design 9)
    CONVERSATIONAL_DISABLED = "CONVERSATIONAL_DISABLED"
    SESSION_BUSY = "SESSION_BUSY"
    CONTEXT_VERSION_CONFLICT = "CONTEXT_VERSION_CONFLICT"
    # skills (design 9)
    SKILL_TOO_LARGE = "SKILL_TOO_LARGE"
    SKILL_INVALID = "SKILL_INVALID"
    SKILL_UNSUPPORTED = "SKILL_UNSUPPORTED"
    # server
    INTERNAL_ERROR = "INTERNAL_ERROR"
    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    SERVICE_NOT_READY = "SERVICE_NOT_READY"
    STORAGE_UNAVAILABLE = "STORAGE_UNAVAILABLE"


# Capabilities: product-level permissions implied by role names.
# Data access is separate (permissions table, default deny).
ROLE_CAPABILITIES: dict[str, frozenset[str]] = {
    RoleName.VIEWER: frozenset(
        {"query.submit", "metadata.read", "saved_queries.manage", "permission.request"}
    ),
    RoleName.ANALYST: frozenset(
        {
            "query.submit",
            "metadata.read",
            "saved_queries.manage",
            "permission.request",
            "analysis.create",
        }
    ),
    RoleName.ADMIN: frozenset(
        {
            "query.submit",
            "metadata.read",
            "saved_queries.manage",
            "permission.request",
            "analysis.create",
            "admin.manage",
        }
    ),
}

SUPPORTED_PROVIDER_KINDS_V1 = frozenset(
    {SourceKind.POSTGRES, SourceKind.MYSQL, SourceKind.DORIS, SourceKind.SPARK}
)
