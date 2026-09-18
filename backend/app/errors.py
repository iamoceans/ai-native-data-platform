"""API and domain errors (spec sections 22, 29).

Every business failure is expressed as an ApiError with a stable code from
constants.ErrorCode; the HTTP layer renders the unified error envelope:

    {"error": {"code": ..., "message": ..., "retryable": ..., "details": {...}},
     "request_id": ..., "trace_id": ...}
"""

from __future__ import annotations

from typing import Any

from app.constants import ErrorCode

STATUS_BY_CODE: dict[str, int] = {
    ErrorCode.UNAUTHENTICATED: 401,
    ErrorCode.FORBIDDEN: 403,
    ErrorCode.PERMISSION_DENIED: 403,
    ErrorCode.CSRF_INVALID: 403,
    ErrorCode.RATE_LIMITED: 429,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.VALIDATION_ERROR: 422,
    ErrorCode.CONFLICT: 409,
    ErrorCode.IDEMPOTENCY_CONFLICT: 409,
    ErrorCode.SQL_SYNTAX_ERROR: 422,
    ErrorCode.SQL_FORBIDDEN: 403,
    ErrorCode.SQL_UNSUPPORTED: 422,
    ErrorCode.QUERY_TOO_LARGE: 413,
    ErrorCode.DATASET_NOT_REGISTERED: 404,
    ErrorCode.AMBIGUOUS_TABLE_REFERENCE: 422,
    ErrorCode.SCHEMA_CHANGED: 409,
    ErrorCode.LIMIT_INVALID: 422,
    ErrorCode.DATASOURCE_UNAVAILABLE: 503,
    ErrorCode.DATASOURCE_UNSUPPORTED: 422,
    ErrorCode.METADATA_UNAVAILABLE: 503,
    ErrorCode.METADATA_SYNC_NOT_AVAILABLE: 501,
    ErrorCode.NOT_IMPLEMENTED: 501,
    ErrorCode.INTERNAL_ERROR: 500,
    ErrorCode.SERVICE_NOT_READY: 503,
    ErrorCode.STORAGE_UNAVAILABLE: 503,
    ErrorCode.RESULT_UNAVAILABLE: 410,
    ErrorCode.RESULT_EXPIRED: 410,
    ErrorCode.EVENT_CURSOR_EXPIRED: 410,
}

RETRYABLE_CODES = frozenset(
    {
        ErrorCode.DATASOURCE_UNAVAILABLE,
        ErrorCode.METADATA_UNAVAILABLE,
        ErrorCode.SERVICE_NOT_READY,
        ErrorCode.STORAGE_UNAVAILABLE,
        ErrorCode.RATE_LIMITED,
    }
)


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = message
        self.status = status or STATUS_BY_CODE.get(self.code, 400)
        self.details = details or {}
        self.retryable = (
            retryable if retryable is not None else self.code in RETRYABLE_CODES
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "retryable": self.retryable,
                "details": self.details,
            }
        }


def not_found(what: str = "resource") -> ApiError:
    return ApiError(ErrorCode.NOT_FOUND, f"{what} not found or not visible")


def forbidden(message: str = "not allowed") -> ApiError:
    return ApiError(ErrorCode.FORBIDDEN, message)
