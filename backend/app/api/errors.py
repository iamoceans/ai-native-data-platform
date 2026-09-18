"""Unified error rendering (spec section 22)."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.constants import ErrorCode
from app.errors import ApiError

logger = logging.getLogger(__name__)


def _payload(code: str, message: str, retryable: bool, details: dict, request: Request):
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details,
        },
        "request_id": getattr(request.state, "request_id", "unknown"),
        "trace_id": getattr(request.state, "trace_id", "unknown"),
    }


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=_payload(exc.code, exc.message, exc.retryable, exc.details, request),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = {"errors": _clean_validation_errors(exc.errors())}
        return JSONResponse(
            status_code=422,
            content=_payload(
                ErrorCode.VALIDATION_ERROR.value,
                "request validation failed",
                False,
                details,
                request,
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {
            401: ErrorCode.UNAUTHENTICATED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.NOT_FOUND,
        }.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return JSONResponse(
            status_code=exc.status_code,
            content=_payload(str(code), str(exc.detail), False, {}, request),
        )

    @app.exception_handler(SQLAlchemyError)
    async def db_error_handler(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.exception("database error", extra={"error_code": "DATABASE_ERROR"})
        return JSONResponse(
            status_code=503,
            content=_payload(
                ErrorCode.SERVICE_NOT_READY.value,
                "control database unavailable",
                True,
                {},
                request,
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"error_code": "INTERNAL_ERROR"})
        return JSONResponse(
            status_code=500,
            content=_payload(
                ErrorCode.INTERNAL_ERROR.value,
                "internal error",
                False,
                {},
                request,
            ),
        )


def _clean_validation_errors(errors: list[dict]) -> list[dict]:
    cleaned = []
    for error in errors[:20]:
        cleaned.append(
            {
                "loc": [str(part) for part in error.get("loc", [])],
                "msg": str(error.get("msg", "")),
                "type": str(error.get("type", "")),
            }
        )
    return cleaned
