"""Query Gateway submission path (spec sections 12.1, 22).

The gateway is the only entry point that turns user SQL into a query job.
It performs validation, authorization, limit enforcement and enqueues the
job; execution happens in the worker through the same ValidatedQuery type.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Callable

from sqlalchemy.orm import Session

from app.api.dto import QuerySubmitRequest
from app.auth.rbac import ensure_dataset_action
from app.auth.sessions import AuthContext
from app.config import Settings
from app.constants import DatasetAction, ErrorCode
from app.errors import ApiError
from app.ids import utcnow
from app.metadata.service import get_schema_view
from app.models.orm import Dataset, QueryJob
from app.query.limits import EffectiveLimits, effective_limits
from app.query.validator import ValidatedStatement, validate_query
from app.repositories import audit as audit_repo
from app.repositories import datasources as datasources_repo
from app.repositories import datasets as datasets_repo
from app.repositories import events as events_repo
from app.repositories import policy as policy_repo
from app.repositories import queries as queries_repo
from app.repositories import queue as queue_repo


class _SchemaCache:
    """Loads and caches dataset schemas inside one validation pass."""

    def __init__(self, session: Session, dataset: Dataset, datasource, settings: Settings) -> None:
        self._session = session
        self._dataset = dataset
        self._datasource = datasource
        self._settings = settings

    @property
    def dataset(self) -> Dataset:
        return self._dataset

    def load(self) -> list[dict]:
        view = get_schema_view(
            self._session,
            dataset=self._dataset,
            datasource=self._datasource,
            settings=self._settings,
        )
        return view.columns


@dataclass
class CompilationResult:
    validated: ValidatedStatement
    limits: EffectiveLimits
    revision: int
    schema_snapshot_ids: dict[uuid.UUID, uuid.UUID | None]


def compile_query(
    session: Session,
    *,
    auth: AuthContext,
    datasource,
    sql: str,
    limits: EffectiveLimits,
    settings: Settings,
    parameters: dict | None = None,
) -> CompilationResult:
    """Validate and authorize a query against the datasource's engine dialect."""
    from app.providers.dialects import parse_dialect_for

    datasets = [row for row in datasets_repo.list_datasets_for_datasource(session, datasource.id) if row.active]
    schema_cache: dict[uuid.UUID, list[dict]] = {}
    snapshot_ids: dict[uuid.UUID, uuid.UUID | None] = {}

    def schema_loader(dataset: Dataset) -> list[dict]:
        if dataset.id in schema_cache:
            return schema_cache[dataset.id]
        view = get_schema_view(session, dataset=dataset, datasource=datasource, settings=settings)
        schema_cache[dataset.id] = list(view.columns)
        snapshot_ids[dataset.id] = view.snapshot_id
        return schema_cache[dataset.id]

    validated = validate_query(
        sql=sql,
        dialect=parse_dialect_for(datasource.kind),
        datasets=datasets,
        schema_loader=schema_loader,
        database=str(datasource.connection_config.get("database", "")),
        max_rows=limits.max_rows,
        max_relations=settings.max_relations,
        max_subquery_depth=settings.max_subquery_depth,
        engine=str(datasource.kind),
    )
    if datasource.kind == "spark" and (validated.placeholder_names or parameters):
        raise ApiError(ErrorCode.SQL_UNSUPPORTED, "Spark parameterized queries are not supported")
    # Authorization: query + discover on every referenced dataset (spec 12.1).
    for relation in validated.relations:
        ensure_dataset_action(
            session, role_ids=auth.role_ids, dataset=relation.dataset, action=DatasetAction.QUERY
        )
        ensure_dataset_action(
            session, role_ids=auth.role_ids, dataset=relation.dataset, action=DatasetAction.DISCOVER
        )
    if parameters is not None:
        provided = set(parameters)
        required = set(validated.placeholder_names)
        missing = sorted(required - provided)
        unknown = sorted(provided - required)
        if missing or unknown:
            raise ApiError(
                ErrorCode.VALIDATION_ERROR,
                "query parameters do not match the SQL placeholders",
                details={"missing": missing, "unknown": unknown},
            )
    revision = policy_repo.get_revision(session)
    return CompilationResult(
        validated=validated,
        limits=limits,
        revision=revision,
        schema_snapshot_ids=snapshot_ids,
    )


def submit_query(
    session: Session,
    *,
    auth: AuthContext,
    request: QuerySubmitRequest,
    settings: Settings,
    request_id: uuid.UUID,
    trace_id: str,
    idempotency_key: str | None = None,
    analysis_id: uuid.UUID | None = None,
) -> tuple[QueryJob, bool]:
    """Returns (job, created). Idempotent replays return created=False."""
    if len(request.sql.encode("utf-8")) > settings.request_max_bytes:
        raise ApiError(
            ErrorCode.QUERY_TOO_LARGE,
            f"SQL exceeds the {settings.request_max_bytes} byte request limit",
        )
    if not auth.user.active:
        raise ApiError(ErrorCode.FORBIDDEN, "user is not active")

    datasource = datasources_repo.get_datasource(session, request.datasource_id)
    if datasource is None:
        raise ApiError(ErrorCode.NOT_FOUND, "datasource not found or not visible")
    if not datasource.enabled:
        raise ApiError(ErrorCode.DATASOURCE_UNAVAILABLE, "datasource is disabled")

    limits = effective_limits(request.limits, settings)

    if idempotency_key:
        import json

        from app.repositories import idempotency as idempotency_repo

        request_hash = _request_hash(request)
        existing = idempotency_repo.lookup(
            session,
            user_id=auth.user.id,
            route="POST /queries",
            key=idempotency_key,
            request_hash=request_hash,
        )
        if existing is not None:
            job = queries_repo.get_query(session, existing)
            if job is None:
                raise ApiError(ErrorCode.CONFLICT, "idempotency record points to a missing query")
            return job, False

    compilation = compile_query(
        session,
        auth=auth,
        datasource=datasource,
        sql=request.sql,
        limits=limits,
        settings=settings,
        parameters=request.parameters,
    )

    job = queries_repo.create_query_job(
        session,
        user_id=auth.user.id,
        datasource_id=datasource.id,
        purpose=request.purpose,
        original_sql=request.sql,
        validated_sql=compilation.validated.validated_sql,
        parameters=request.parameters,
        sql_hash=compilation.validated.sql_hash,
        limits={**limits.as_dict(), "policy": "gateway_v1"},
        policy_revision=compilation.revision,
        analysis_id=analysis_id,
    )
    queue_repo.enqueue(session, kind="query", resource_id=job.id)
    queries_repo.add_dependencies(
        session,
        query_id=job.id,
        dependencies=[
            (relation.dataset.id, compilation.schema_snapshot_ids.get(relation.dataset.id))
            for relation in compilation.validated.relations
        ],
    )
    events_repo.add_event(
        session,
        resource_kind="query",
        resource_id=job.id,
        event_type="query.queued",
        payload={
            "query_id": str(job.id),
            "status": str(job.status),
            "datasource_id": str(datasource.id),
        },
    )
    audit_repo.add_audit(
        session,
        actor_id=auth.user.id,
        action="query.submit",
        resource_type="query",
        resource_id=str(job.id),
        trace_id=trace_id,
        outcome="success",
        details={
            "datasource_id": str(datasource.id),
            "sql_hash": compilation.validated.sql_hash,
            "max_rows": limits.max_rows,
            "timeout_seconds": limits.timeout_seconds,
            "dataset_ids": [str(dataset_id) for dataset_id in compilation.validated.dataset_ids],
        },
    )
    if idempotency_key:
        from app.repositories import idempotency as idempotency_repo

        idempotency_repo.store(
            session,
            user_id=auth.user.id,
            route="POST /queries",
            key=idempotency_key,
            request_hash=_request_hash(request),
            resource_id=job.id,
            ttl_hours=settings.idempotency_ttl_hours,
        )
    return job, True


def _request_hash(request: QuerySubmitRequest) -> str:
    import json

    from app.ids import sha256_hex

    canonical = json.dumps(
        {
            "datasource_id": str(request.datasource_id),
            "sql": request.sql,
            "parameters": request.parameters,
            "limits": request.limits.model_dump(),
            "purpose": request.purpose,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256_hex(canonical)
