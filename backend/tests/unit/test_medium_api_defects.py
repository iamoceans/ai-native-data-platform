"""API/resource regressions for the second 2026-10-15 review."""
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import uuid

import pytest
from starlette.requests import Request

from app.api.routes import datasources, queries
from app.auth.ratelimit import LoginRateLimiter
from app.config import Settings
from app.errors import ApiError


def test_results_require_discover_as_well_as_query(monkeypatch):
    dependency = NS(dataset_id=uuid.uuid4())
    db = NS(query=lambda *args: NS(filter_by=lambda **kw: NS(all=lambda: [dependency])))
    monkeypatch.setattr(queries.datasets_repo, "has_dataset_action", lambda db, roles, id, action: action == "query")
    with pytest.raises(ApiError) as caught:
        queries.ensure_query_dataset_permission(db, NS(role_ids=[]), uuid.uuid4())
    assert caught.value.code == "PERMISSION_DENIED"


def test_nonadmin_datasource_response_omits_topology(monkeypatch):
    id = uuid.uuid4()
    data = dict(id=id, name="source", kind="postgres", connection_config={"host": "private-host", "port": 5432, "database": "private-db"}, secret_ref="private-file", capabilities={}, enabled=True, health_status="UNKNOWN", created_by=uuid.uuid4(), created_at="2026-10-15T00:00:00Z", version=1)
    monkeypatch.setattr(datasources.datasources_repo, "list_datasources", lambda *args, **kw: ([NS(**data)], 1))
    monkeypatch.setattr(datasources.datasource_service, "datasource_to_dict", lambda *args: data.copy())
    result = datasources.list_datasources(Request({"type": "http", "query_string": b""}), _auth=NS(capabilities=set()), session=None, settings=Settings())
    serialized = result.model_dump_json(exclude_none=True)
    assert "private-host" not in serialized and "private-db" not in serialized and "private-file" not in serialized


def test_limiter_does_not_allocate_for_checks_and_stays_bounded():
    limiter = LoginRateLimiter(3, 60, max_keys=2)
    for i in range(100):
        assert limiter.check(str(i), "client")
    assert len(limiter._attempts) == 0
    limiter.record_failure("first", "client")
    limiter.record_failure("second", "client")
    assert not limiter.check("third", "client")
    for i in range(100):
        limiter.record_failure(str(i), "client")
    assert len(limiter._attempts) <= 2


def test_optional_driver_analysis_error_is_skipped(monkeypatch):
    from app.agent import runner
    from app.agent.planner import comparison_plan
    from app.analysis.types import AnalysisError
    from app.metrics.registry import load_metric_definitions
    definitions = {d.metric_key: d for d in load_metric_definitions(Path(__file__).resolve().parents[3] / "metadata")}
    monkeypatch.setattr(runner, "_metric_definition", lambda db, key: (None, definitions[key]))
    monkeypatch.setattr(runner, "_auth", lambda *args: NS())
    def fail(*args, **kwargs):
        raise AnalysisError("METRIC_FILTER_UNSUPPORTED", "cannot compile companion")
    monkeypatch.setattr(runner, "_compile_period", fail)
    task = NS(id=uuid.uuid4(), state={"metric_key": "ads_revenue", "observations": {"material": True, "target": {"dimensions": ["country"], "key": ["US"]}}}, context={}, budget={}, plan=comparison_plan(question="why", metric_key="ads_revenue", dimensions=["country"]).model_dump())
    assert runner._extend_with_driver_step(NS(begin_nested=nullcontext), task=task, settings=Settings()) is False


@pytest.mark.parametrize("declared", [False, True])
def test_body_limit_rejects_before_endpoint_even_without_length(declared):
    import asyncio
    from app.api.body_limit import RequestBodyLimitMiddleware
    called = []
    async def app(scope, receive, send):
        called.append(True)
    chunks = iter([{"type": "http.request", "body": b"123", "more_body": True}, {"type": "http.request", "body": b"456", "more_body": False}])
    sent = []
    async def receive():
        return next(chunks)
    async def send(message):
        sent.append(message)
    scope = {"type": "http", "method": "POST", "headers": [(b"content-length", b"6")] if declared else [], "state": {}}
    asyncio.run(RequestBodyLimitMiddleware(app, max_bytes=5)(scope, receive, send))
    assert not called
    assert sent[0]["status"] == 413


def test_body_limit_preserves_small_chunked_request():
    import asyncio
    from app.api.body_limit import RequestBodyLimitMiddleware
    received = []
    async def app(scope, receive, send):
        while True:
            chunk = await receive()
            received.append(chunk["body"])
            if not chunk.get("more_body", False):
                break
    chunks = iter([{"type": "http.request", "body": b"12", "more_body": True}, {"type": "http.request", "body": b"3", "more_body": False}])
    async def receive():
        return next(chunks)
    async def send(message):
        raise AssertionError(message)
    asyncio.run(RequestBodyLimitMiddleware(app, max_bytes=5)({"type": "http", "headers": []}, receive, send))
    assert b"".join(received) == b"123"


@pytest.mark.parametrize("field", ["skill_config", "parameters", "filters"])
def test_persisted_or_bound_mappings_are_size_bounded(field):
    from app.api.dto import AgentSessionCreate, QuerySubmitRequest, TurnScopePatch
    from pydantic import ValidationError
    payload = {"k": "x" * 70_000}
    with pytest.raises(ValidationError):
        if field == "skill_config":
            AgentSessionCreate(title="test", skill_config=payload)
        elif field == "parameters":
            QuerySubmitRequest(datasource_id=uuid.uuid4(), sql="select :k", parameters=payload)
        else:
            TurnScopePatch(filters=payload)


def test_ecpm_null_breakdown_does_not_crash_observer(monkeypatch):
    from app.agent import runner
    descriptors, jobs, payloads = [], {}, {}
    for period, total in [("baseline", "5"), ("current", "4")]:
        for is_total in [False, True]:
            id = uuid.uuid4()
            descriptors.append(dict(query_id=str(id), metric_alias="ecpm", period=period, period_total=is_total, dataset_id=str(uuid.uuid4()), role="primary", source_row_count_alias="__aind_source_rows"))
            jobs[id] = NS(id=id, status="SUCCEEDED")
            payloads[str(id)] = {"columns": [{"name": "country"}, {"name": "ecpm"}, {"name": "__aind_source_rows"}], "rows": [["all", total, 2]] if is_total else [["zero-impressions", None, 1], ["other", "10", 1]]}
    results = iter(NS(storage_key=d["query_id"], expires_at=None) for d in descriptors)
    db = NS(execute=lambda *args: NS(scalar_one=lambda: next(results)))
    monkeypatch.setattr(runner, "get_result_store", lambda: NS(read_json=lambda key: payloads[key]))
    monkeypatch.setattr(runner.analyses_repo, "list_steps", lambda *args: [])
    monkeypatch.setattr(runner, "_event", lambda *args, **kw: None)
    task = NS(id=uuid.uuid4(), state={"dimensions": ["country"], "metric_definition": {"aggregation_kind": "ratio_of_sums"}})
    runner._observe(db, task=task, descriptors=descriptors, jobs=jobs)
    assert task.state["observations"]["baseline"] == "5"
    assert task.state["observations"]["current"] == "4"


def test_body_limit_integrates_with_fastapi_and_streams():
    from fastapi import FastAPI, Request
    from fastapi.responses import StreamingResponse
    from fastapi.testclient import TestClient
    from app.api.body_limit import RequestBodyLimitMiddleware
    app = FastAPI()
    app.add_middleware(RequestBodyLimitMiddleware, max_bytes=10)
    called = []
    @app.post("/echo")
    async def echo(request: Request):
        called.append(True)
        return {"size": len(await request.body())}
    @app.get("/stream")
    def stream():
        return StreamingResponse(iter(["event: stream.end\ndata: {}\n\n"]), media_type="text/event-stream")
    with TestClient(app) as client:
        assert client.post("/echo", content=b"123").json() == {"size": 3}
        assert client.post("/echo", content=b"x" * 11).status_code == 413
        assert len(called) == 1
        response = client.get("/stream")
        assert response.status_code == 200 and "stream.end" in response.text


def test_limiter_recovers_expired_keys(monkeypatch):
    from app.auth import ratelimit
    now = [0.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])
    limiter = LoginRateLimiter(2, 10, max_keys=1)
    limiter.record_failure("first", "client")
    assert not limiter.check("new", "client")
    now[0] = 11
    assert limiter.check("new", "client")
    assert limiter._attempts == {}


def test_optional_driver_kernel_error_does_not_fail_primary_analysis(monkeypatch):
    from app.agent import runner
    from app.analysis.types import AnalysisError
    def fail(**kwargs):
        raise AnalysisError("INVALID_IMPRESSIONS", "invalid companion values")
    monkeypatch.setattr(runner, "decompose_revenue", fail)
    observation = {"target": {"key": ["US"], "revenue_baseline": "10", "revenue_current": "9"}}
    assert runner._driver_observation(NS(id=uuid.uuid4()), observation=observation, dimension_values={"baseline": "1", "current": "1"}, grouped={}) is None
    assert "INVALID_IMPRESSIONS" in observation["driver_warning"]
