"""Query execution in the worker (spec sections 9, 13, 14, 29).

The executor re-checks authorization and schema at execution time, opens a
dedicated source connection, streams a bounded result, publishes the result
files atomically and only then commits the terminal status. A background
monitor thread owns heartbeats, cancel requests and the deadline.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable, Iterator

from app.auth.rbac import ensure_dataset_action
from app.config import Settings
from app.constants import DatasetAction, ErrorCode, QueryStatus
from app.datasource.secrets import SecretResolver
from app.datasource.service import build_provider
from app.errors import ApiError
from app.ids import utcnow
from app.metadata.service import get_schema_view
from app.models.orm import QueryJob, QueryResult
from app.providers.base import ValidatedQuery
from app.query.gateway import compile_query
from app.query.limits import EffectiveLimits
from app.query.validator import ValidatedStatement, validate_query
from app.repositories import audit as audit_repo
from app.repositories import datasets as datasets_repo
from app.repositories import events as events_repo
from app.repositories import queries as queries_repo
from app.repositories import queue as queue_repo
from app.results import types as result_types
from app.results.store import ResultStore, columns_payload

logger = logging.getLogger(__name__)

SESSION_FACTORY = Callable[[], object]


@contextmanager
def _tx(session_factory) -> Iterator:
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@dataclass
class PreparedExecution:
    job: QueryJob
    datasource: object
    validated: ValidatedStatement
    limits: EffectiveLimits
    user_id: uuid.UUID


def prepare_execution(session, *, settings: Settings, claim) -> PreparedExecution:
    from app.models.orm import QueryDependency
    from app.repositories import datasources as datasources_repo
    from app.repositories import users as users_repo

    job = queries_repo.get_query(session, claim.query_id)
    if job is None:
        raise ApiError(ErrorCode.NOT_FOUND, "query job disappeared")
    datasource = datasources_repo.get_datasource(session, job.datasource_id)
    if datasource is None or not datasource.enabled:
        raise ApiError(ErrorCode.DATASOURCE_UNAVAILABLE, "datasource is missing or disabled")

    role_ids = users_repo.role_ids_for_user(session, job.user_id)

    dependencies = list(
        session.query(QueryDependency).filter_by(query_id=job.id).all()
    )
    for dependency in dependencies:
        dataset = datasets_repo.get_dataset(session, dependency.dataset_id)
        if dataset is None or not dataset.active:
            raise ApiError(
                ErrorCode.DATASET_NOT_REGISTERED, "a dependency dataset is no longer registered"
            )
        ensure_dataset_action(
            session, role_ids=role_ids, dataset=dataset, action=DatasetAction.QUERY
        )
        ensure_dataset_action(
            session, role_ids=role_ids, dataset=dataset, action=DatasetAction.DISCOVER
        )

    limits = EffectiveLimits(
        max_rows=int(job.limits.get("max_rows", settings.default_max_rows)),
        max_bytes=int(job.limits.get("max_bytes", settings.hard_max_bytes)),
        timeout_seconds=int(job.limits.get("timeout_seconds", settings.default_timeout_seconds)),
    )

    # Re-validate the exact SQL we will execute against the current schema.
    class _AuthShim:
        def __init__(self, role_ids: list[uuid.UUID]) -> None:
            self.role_ids = role_ids

    try:
        compilation = compile_query(
            session,
            auth=_AuthShim(role_ids),
            datasource=datasource,
            sql=job.validated_sql or job.original_sql,
            limits=limits,
            settings=settings,
            parameters=job.bound_parameters or {},
        )
    except ApiError as exc:
        if exc.code in (ErrorCode.PERMISSION_DENIED,):
            raise
        raise ApiError(
            ErrorCode.SCHEMA_CHANGED,
            "the query plan is no longer valid against the current schema or permissions",
            details={"cause": exc.code, "detail": exc.message},
        ) from exc
    validated = compilation.validated
    _check_parameters(validated, job.bound_parameters)
    return PreparedExecution(
        job=job, datasource=datasource, validated=validated, limits=limits, user_id=job.user_id
    )


def _check_parameters(validated: ValidatedStatement, parameters: dict) -> None:
    provided = set(parameters or {})
    required = set(validated.placeholder_names)
    missing = required - provided
    unknown = provided - required
    if missing or unknown:
        raise ApiError(
            ErrorCode.VALIDATION_ERROR,
            "query parameters do not match the SQL placeholders",
            details={"missing": sorted(missing), "unknown": sorted(unknown)},
        )


class _ExecutionMonitor(threading.Thread):
    def __init__(self, *, session_factory, settings: Settings, claim, provider, handle, deadline: float):
        super().__init__(name=f"monitor-{claim.query_id}", daemon=True)
        self._session_factory = session_factory
        self._settings = settings
        self._claim = claim
        self._provider = provider
        self._handle = handle
        self._deadline = deadline
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self.cancel_sent = False
        self.lease_lost = False
        self.deadline_exceeded = False
        self.user_cancel_requested = False
        self.last_cancel_requested = False

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:  # pragma: no cover - thread behaviour
        while not self._stop.wait(self._settings.cancel_poll_seconds):
            try:
                with _tx(self._session_factory) as session:
                    alive = queue_repo.heartbeat(session, self._claim, self._settings)
                    cancel_requested = queue_repo.check_cancel_requested(session, self._claim.query_id)
            except Exception:
                logger.exception("monitor heartbeat failed", extra={"query_id": str(self._claim.query_id)})
                continue
            if not alive:
                with self._lock:
                    self.lease_lost = True
                self._cancel()
                return
            if cancel_requested:
                with self._lock:
                    self.user_cancel_requested = True
                    self.last_cancel_requested = True
                self._cancel()
            if time.monotonic() > self._deadline:
                with self._lock:
                    self.deadline_exceeded = True
                self._cancel()

    def _cancel(self) -> None:
        with self._lock:
            if self.cancel_sent:
                return
            self.cancel_sent = True
        try:
            self._provider.cancel(self._handle)
        except Exception:  # pragma: no cover - defensive
            logger.exception("provider cancel failed", extra={"query_id": str(self._claim.query_id)})


def execute_claim(
    *,
    session_factory,
    settings: Settings,
    store: ResultStore,
    claim,
) -> None:
    started = time.monotonic()
    try:
        with _tx(session_factory) as session:
            prepared = prepare_execution(session, settings=settings, claim=claim)
    except ApiError as exc:
        _publish_terminal(
            session_factory, claim, status=QueryStatus.FAILED, code=exc.code, message=exc.message,
            details=exc.details, duration_ms=(time.monotonic() - started) * 1000,
        )
        return
    except Exception as exc:  # defensive: never crash the worker loop
        logger.exception("prepare failed", extra={"query_id": str(claim.query_id)})
        _publish_terminal(
            session_factory, claim, status=QueryStatus.FAILED, code=ErrorCode.INTERNAL_ERROR,
            message="execution preparation failed", details=None,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        return

    query = ValidatedQuery(
        query_id=prepared.job.id,
        sql=prepared.validated.validated_sql,
        parameters=dict(prepared.job.bound_parameters or {}),
        dataset_ids=prepared.validated.dataset_ids,
        schema_hashes={},
        policy_revision=int(prepared.job.policy_revision),
        max_rows=prepared.limits.max_rows,
        max_bytes=prepared.limits.max_bytes,
        timeout_seconds=prepared.limits.timeout_seconds,
    )

    resolver = SecretResolver(settings.secrets_dir)
    try:
        credentials = resolver.resolve(prepared.datasource.secret_ref)
        provider = build_provider(prepared.datasource, credentials, settings)
    except ApiError as exc:
        _publish_terminal(
            session_factory, claim, status=QueryStatus.FAILED, code=exc.code, message=exc.message,
            details=exc.details, duration_ms=(time.monotonic() - started) * 1000,
        )
        return
    except Exception as exc:
        logger.exception("provider construction failed", extra={"query_id": str(claim.query_id)})
        _publish_terminal(
            session_factory, claim, status=QueryStatus.FAILED,
            code=ErrorCode.DATASOURCE_UNAVAILABLE,
            message=f"could not build the source provider: {_short(exc)}", details=None,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        return

    try:
        handle = provider.open_execution(query)
    except ApiError as exc:
        _publish_terminal(
            session_factory, claim, status=QueryStatus.FAILED, code=exc.code, message=exc.message,
            details=exc.details, duration_ms=(time.monotonic() - started) * 1000,
        )
        return
    except Exception as exc:
        classification = provider.classify_execution_error(exc)
        code = (
            ErrorCode.DATASOURCE_UNAVAILABLE
            if classification == "unavailable"
            else ErrorCode.EXECUTION_ERROR
        )
        _publish_terminal(
            session_factory, claim, status=QueryStatus.FAILED, code=code,
            message=f"could not open a source connection: {_short(exc)}", details=None,
            duration_ms=(time.monotonic() - started) * 1000,
        )
        return

    handle.worker_id = claim.worker_id
    handle.fencing_token = claim.fencing_token
    _persist_handle(session_factory, claim, handle)

    deadline = started + prepared.limits.timeout_seconds + 5
    monitor = _ExecutionMonitor(
        session_factory=session_factory,
        settings=settings,
        claim=claim,
        provider=provider,
        handle=handle,
        deadline=deadline,
    )
    monitor.start()

    rows_json: list[list] = []
    rows_typed: list[tuple] = []
    truncated = False
    failure: tuple[str, str, dict | None] | None = None
    status_override: str | None = None

    try:
        if prepared.job.purpose == "explain":
            plan = provider.explain(handle, query)
            rows_typed = [(result_types.serialize_value(plan),)]
            rows_json = [[result_types.serialize_value(plan)]]
            columns = [{"id": "c0", "name": "plan", "type": "json"}]
        else:
            approx_bytes = 0
            columns = []
            stop = False
            iterator = provider.execute(handle, query)
            for batch in iterator:
                for row in batch:
                    if len(rows_json) >= query.max_rows:
                        truncated = True
                        stop = True
                        break
                    json_row = result_types.serialize_row(row)
                    approx_bytes += result_types.estimate_json_bytes(json_row)
                    if approx_bytes > query.max_bytes:
                        truncated = True
                        stop = True
                        break
                    rows_json.append(json_row)
                    rows_typed.append(tuple(row))
                if stop:
                    break
            columns = columns_payload(handle.columns or [])
    except ApiError as exc:
        failure = (exc.code, exc.message, exc.details)
    except Exception as exc:
        classification = provider.classify_execution_error(exc)
        if classification in ("cancelled", "timeout"):
            # The monitor-decided outcome handles the cancel/complete race and
            # distinguishes user cancel from the engine-enforced deadline.
            status_override = _cancel_outcome(monitor, session_factory, claim)
        elif classification == "error" and monitor.cancel_sent:
            # Some engines cannot name their own cancellation: Spark's Impyla
            # cursor raises an untyped error once the cancelled operation stops
            # reporting a state. Once the monitor has sent a cancel, that cancel
            # decides the outcome - FAILED is not even a legal transition out of
            # CANCEL_REQUESTED, so failing here would strand the job until the
            # lease expires. Specific engine verdicts (permission, schema,
            # syntax, unavailable) keep their own codes above.
            status_override = _cancel_outcome(monitor, session_factory, claim)
        elif classification == "permission":
            failure = (
                ErrorCode.PERMISSION_DENIED,
                f"the source database denied access: {_short(exc)}",
                None,
            )
        elif classification == "readonly":
            failure = (
                ErrorCode.SQL_FORBIDDEN,
                "the read-only session rejected this statement",
                None,
            )
        elif classification == "unavailable":
            failure = (
                ErrorCode.DATASOURCE_UNAVAILABLE,
                f"source connection failed: {_short(exc)}",
                None,
            )
        elif classification == "schema":
            # The registered snapshot still had the object; the source no longer
            # does. The code is what lets an analysis repair its own statement.
            failure = (
                ErrorCode.SCHEMA_CHANGED,
                f"the source rejected the object reference: {_short(exc)}",
                None,
            )
        elif classification == "syntax":
            failure = (
                ErrorCode.SQL_SYNTAX_ERROR,
                f"the source rejected the statement syntax: {_short(exc)}",
                None,
            )
        else:
            logger.exception("execution error", extra={"query_id": str(claim.query_id)})
            failure = (
                ErrorCode.EXECUTION_ERROR,
                f"source execution failed ({classification}): {_short(exc)}",
                None,
            )
    finally:
        monitor.stop()
        provider.close(handle)

    duration_ms = (time.monotonic() - started) * 1000
    if status_override is not None:
        code = {
            QueryStatus.CANCELLED: ErrorCode.QUERY_CANCELLED,
            QueryStatus.TIMED_OUT: ErrorCode.QUERY_TIMEOUT,
            QueryStatus.LOST: ErrorCode.QUERY_LOST,
        }.get(status_override, ErrorCode.EXECUTION_ERROR)
        message = {
            QueryStatus.CANCELLED: "query was cancelled",
            QueryStatus.TIMED_OUT: "query exceeded its deadline",
            QueryStatus.LOST: "worker lost the lease during execution",
        }.get(status_override, "execution stopped")
        target = status_override
        if target == QueryStatus.LOST:
            # publish_terminal resolves LOST only from RUNNING/CANCEL_REQUESTED;
            # if a cancel is pending, record it as CANCELLED instead.
            with _tx(session_factory) as session:
                job = queries_repo.get_query(session, claim.query_id)
                if job is not None and job.cancel_requested_at is not None:
                    target = QueryStatus.CANCELLED
                    code = ErrorCode.QUERY_CANCELLED
                    message = "query was cancelled"
        _publish_terminal(
            session_factory,
            claim,
            status=target,
            code=code,
            message=message,
            details=None,
            duration_ms=duration_ms,
        )
        return
    if failure is not None:
        code, message, details = failure
        _publish_terminal(
            session_factory, claim, status=QueryStatus.FAILED, code=code, message=message,
            details=details, duration_ms=duration_ms,
        )
        return

    stored = store.publish(
        columns=columns, rows_json=rows_json, rows_typed=rows_typed, truncated=truncated
    )
    expires_at = utcnow() + timedelta(days=settings.result_ttl_days)
    published = False
    try:
        with _tx(session_factory) as session:
            published = queue_repo.publish_success(
                session,
                claim,
                result_id=stored.result_id,
                storage_key=stored.storage_key,
                fmt="arrow+json",
                schema_json={"schema_version": 1, "columns": columns},
                row_count=stored.row_count,
                byte_count=stored.byte_count,
                truncated=stored.truncated,
                content_hash=stored.content_hash,
                expires_at=expires_at,
            )
            if published:
                audit_repo.add_audit(
                    session,
                    actor_id=prepared.user_id,
                    action="query.execute",
                    resource_type="query",
                    resource_id=str(claim.query_id),
                    trace_id=f"worker:{claim.worker_id}",
                    outcome="SUCCEEDED",
                    details={
                        "row_count": stored.row_count,
                        "truncated": stored.truncated,
                        "duration_ms": round(duration_ms, 2),
                    },
                )
    except Exception:
        store.delete(stored.storage_key)
        raise
    if not published:
        store.delete(stored.storage_key)
        with _tx(session_factory) as session:
            job = queries_repo.get_query(session, claim.query_id)
            if job is not None and job.cancel_requested_at is not None:
                queue_repo.publish_terminal(
                    session,
                    claim,
                    status=QueryStatus.CANCELLED,
                    code=ErrorCode.QUERY_CANCELLED,
                    message="query was cancelled before the result could be published",
                )


def _cancel_outcome(monitor: _ExecutionMonitor, session_factory, claim) -> str:
    """Classify a QueryCanceled outcome.

    Priority: lost lease -> LOST; explicit user cancel -> CANCELLED; deadline
    (client monitor or server statement_timeout) -> TIMED_OUT.
    """
    if monitor.lease_lost:
        return QueryStatus.LOST
    if monitor.user_cancel_requested:
        return QueryStatus.CANCELLED
    if monitor.deadline_exceeded:
        return QueryStatus.TIMED_OUT
    # No client-side cancel seen: either the server statement_timeout fired or
    # the cancel flag was committed after our last poll. Ask the database.
    try:
        with _tx(session_factory) as session:
            if queue_repo.check_cancel_requested(session, claim.query_id):
                return QueryStatus.CANCELLED
    except Exception:  # pragma: no cover - defensive
        logger.exception("cancel outcome lookup failed", extra={"query_id": str(claim.query_id)})
    return QueryStatus.TIMED_OUT


def _persist_handle(session_factory, claim, handle) -> None:
    try:
        with _tx(session_factory) as session:
            job = queries_repo.get_query(session, claim.query_id)
            if job is not None:
                job.provider_handle = {
                    "worker_id": claim.worker_id,
                    "fencing_token": claim.fencing_token,
                    "attempt": claim.attempt,
                    "dialect": handle.dialect,
                    "backend_pid": handle.native.get("backend_pid"),
                    "generation": handle.connection_generation,
                    "opened_at": utcnow().isoformat(),
                }
    except Exception:  # handle persistence must not abort an otherwise healthy execution
        logger.exception("could not persist provider handle", extra={"query_id": str(claim.query_id)})


def _publish_terminal(
    session_factory,
    claim,
    *,
    status: str,
    code: str,
    message: str,
    details: dict | None,
    duration_ms: float,
) -> None:
    try:
        with _tx(session_factory) as session:
            published = _publish_terminal_status(
                session, claim, status=status, code=code, message=message, details=details
            )
            if published is not None:
                audit_repo.add_audit(
                    session,
                    actor_id=claim.user_id,
                    action="query.execute",
                    resource_type="query",
                    resource_id=str(claim.query_id),
                    trace_id=f"worker:{claim.worker_id}",
                    outcome=published,
                    details={"code": code, "duration_ms": round(duration_ms, 2)},
                )
            if published == QueryStatus.LOST:
                events_repo.add_event(
                    session,
                    resource_kind="query",
                    resource_id=claim.query_id,
                    event_type="query.lost",
                    payload={"query_id": str(claim.query_id), "status": "LOST"},
                )
    except Exception:
        logger.exception("could not publish terminal status", extra={"query_id": str(claim.query_id)})


def _publish_terminal_status(
    session,
    claim,
    *,
    status: str,
    code: str,
    message: str,
    details: dict | None,
) -> str | None:
    """Write the terminal status, resolving a pending cancel instead of refusing.

    A job in CANCEL_REQUESTED may only become CANCELLED/TIMED_OUT/LOST (spec 13
    allow-table), so an engine error that arrives after the monitor sent a cancel
    must not be written as FAILED: the transition would be refused and the job
    would look running until its lease expired, then be recorded LOST minutes
    later. Returns the status actually written, or None when nothing was.
    """
    job = queries_repo.get_query(session, claim.query_id)
    if (
        job is not None
        and job.status == QueryStatus.CANCEL_REQUESTED
        and QueryStatus(status) not in (QueryStatus.CANCELLED, QueryStatus.TIMED_OUT, QueryStatus.LOST)
    ):
        published = queue_repo.publish_terminal(
            session, claim, status=QueryStatus.CANCELLED,
            code=ErrorCode.QUERY_CANCELLED, message="query was cancelled", details=details,
        )
        return QueryStatus.CANCELLED if published else None
    published = queue_repo.publish_terminal(
        session, claim, status=status, code=code, message=message, details=details
    )
    return status if published else None


def _short(exc: BaseException) -> str:
    text = str(exc).strip().replace("\n", " ")
    return text[:250] + ("..." if len(text) > 250 else "")
