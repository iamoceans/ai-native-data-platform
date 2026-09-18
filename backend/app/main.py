"""FastAPI application factory (spec sections 22, 28)."""

from __future__ import annotations

import logging
import uuid

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.routes import analyses, admin, auth, datasets, datasources, health, metrics, queries, saved_queries
from app.config import get_settings
from app.constants import SCHEMA_VERSION
from app.logging_setup import configure_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging("api", settings.log_level)
    app = FastAPI(
        title="AI-Native Data Platform API",
        version="0.1.0",
        description=(
            "Governed local data platform: authentication and RBAC, multi-engine "
            "query gateway, DataHub-backed catalog and lineage, versioned metrics, "
            "and checkpointed evidence-bound analyses."
        ),
        openapi_url="/api/v1/openapi.json",
        docs_url="/api/v1/docs",
        redoc_url=None,
        openapi_tags=[
            {"name": "auth", "description": "Local accounts, sessions, CSRF"},
            {"name": "datasources", "description": "Datasource registry and health"},
            {"name": "datasets", "description": "Catalog and schemas"},
            {"name": "queries", "description": "SQL submission, results and events"},
            {"name": "metrics", "description": "Versioned governed metric definitions"},
            {"name": "analyses", "description": "Sessions, analysis tasks, reports and evidence"},
            {"name": "admin", "description": "Users, roles, grants, audits"},
            {"name": "health", "description": "Liveness and readiness"},
        ],
    )

    if settings.cors_origin_list:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origin_list,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
            allow_headers=["Content-Type", "X-CSRF-Token", "Idempotency-Key", "X-Trace-Id", "Last-Event-ID"],
            expose_headers=["Location", "X-Request-Id", "X-Trace-Id", "Idempotency-Replayed"],
        )

    @app.middleware("http")
    async def request_context(request: Request, call_next) -> Response:
        request_id = str(uuid.uuid4())
        trace_id = request.headers.get(settings.trace_id_header) or request_id
        request.state.request_id = request_id
        request.state.trace_id = trace_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        response.headers["X-Trace-Id"] = trace_id
        return response

    register_error_handlers(app)

    api = FastAPI  # noqa: F841 - readability anchor
    prefix = "/api/v1"
    app.include_router(health.router)
    app.include_router(health.router, prefix=prefix)
    app.include_router(auth.router, prefix=prefix)
    app.include_router(datasources.router, prefix=prefix)
    app.include_router(datasets.router, prefix=prefix)
    app.include_router(queries.router, prefix=prefix)
    app.include_router(metrics.router, prefix=prefix)
    app.include_router(saved_queries.router, prefix=prefix)
    app.include_router(analyses.router, prefix=prefix)
    app.include_router(admin.router, prefix=prefix)

    @app.get("/api/v1/meta", include_in_schema=False)
    def meta() -> dict:
        return {"schema_version": SCHEMA_VERSION, "service": "ainative-api"}

    return app


app = create_app()
