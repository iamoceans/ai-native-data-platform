"""Regressions for the six defects reported on 2026-10-15."""
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
import uuid

import pytest
from fastapi import Response
from starlette.requests import Request

from app.metrics.compiler import compile_metric
from app.metrics.registry import load_metric_definitions
from app.agent.conversation import orchestrator
from app.api.routes import auth, memory
from app.config import Settings
from app.errors import ApiError


def test_nonadditive_breakdown_has_independent_period_total():
    definitions = {d.metric_key: d for d in load_metric_definitions(Path(__file__).resolve().parents[3] / "metadata")}
    for key in ("ecpm", "dau"):
        compiled = compile_metric(definitions[key], dimensions=["country"], period_start="2026-09-01", period_end="2026-09-08")
        totals = [q for q in compiled.queries if q.period_total]
        assert len(totals) == 1
        assert "country" not in totals[0].group_by


@pytest.mark.parametrize("formula,expected", [("aa-bb", "7"), ("aa / nullif(bb, 0)", "3.333333333333333333333333333"), ("coalesce(aa / nullif(bb, 0), 0)", "0")])
def test_composition_is_evaluated(formula, expected):
    from app.metrics.evaluator import evaluate_formula
    values = {"aa": Decimal(10), "bb": Decimal(0 if expected == "0" else 3)}
    assert evaluate_formula(formula, values) == Decimal(expected)


@pytest.mark.parametrize("allowed", [False, True])
def test_memory_listing_and_counts_use_grants(monkeypatch, allowed):
    checked = []
    monkeypatch.setattr(memory.memory_repo, "list_recent", lambda *args, **kw: checked.append(kw) or [])
    monkeypatch.setattr(memory.memory_repo, "counts", lambda *args, **kw: checked.append(kw) or {})
    roles = [uuid.uuid4()] if allowed else []
    memory.list_memory(Request({"type": "http"}), _auth=NS(role_ids=roles), session=None)
    assert all(item.get("role_ids") == roles for item in checked)


@pytest.mark.parametrize("limited", [False, True])
def test_denied_login_commits_audit_before_error(monkeypatch, limited):
    db = Mock()
    monkeypatch.setattr(auth, "get_login_limiter", lambda *args: NS(check=lambda *args: not limited, record_failure=lambda *args: None))
    monkeypatch.setattr(auth.users_repo, "get_user_by_username", lambda *args: None)
    monkeypatch.setattr(auth.audit_repo, "add_audit", Mock())
    from app.api.dto import LoginRequest
    with pytest.raises(ApiError):
        auth.login(LoginRequest(username="missing", password="bad-password"), Request({"type": "http"}), Response(), session=db, settings=Settings())
    db.commit.assert_called_once()


def test_history_fetches_latest_window(monkeypatch):
    calls = []
    rows = [NS(role="user", content={"text": str(i)}, content_schema_version=1) for i in range(20)]
    def messages(*args, limit, offset, **kw):
        calls.append(offset)
        return rows[offset:offset+limit], len(rows)
    monkeypatch.setattr(orchestrator.analyses_repo, "list_messages", messages)
    result = orchestrator._history(None, NS(session_id=uuid.uuid4(), user_id=uuid.uuid4()))
    assert result[-1]["text"] == "19"
    assert len(result) == orchestrator.HISTORY_LIMIT


@pytest.mark.parametrize("column", ["revenue", "secret_column"])
def test_unauthorized_query_never_introspects(monkeypatch, column):
    from app.query import gateway
    from app.query.limits import effective_limits
    from app.api.dto import QueryLimits
    dataset = NS(id=uuid.uuid4(), active=True, object_name="hidden", schema_name="private", catalog_name="db")
    source = NS(id=uuid.uuid4(), kind="postgres", connection_config={"database": "db"})
    monkeypatch.setattr(gateway.datasets_repo, "list_datasets_for_datasource", lambda *args: [dataset])
    monkeypatch.setattr(gateway.datasets_repo, "has_dataset_action", lambda *args: False)
    loader = Mock(side_effect=AssertionError("unauthorized introspection"))
    monkeypatch.setattr(gateway, "get_schema_view", loader)
    with pytest.raises(ApiError) as caught:
        gateway.compile_query(None, auth=NS(role_ids=[]), datasource=source, sql=f"SELECT {column} FROM private.hidden", limits=effective_limits(QueryLimits(), Settings()), settings=Settings())
    assert caught.value.code == "PERMISSION_DENIED"
    assert caught.value.details == {}
    loader.assert_not_called()


def test_memory_filters_before_limit_and_counts_hide_revoked_rows(monkeypatch):
    from app.repositories import memory as repo
    rows = [NS(id=i, status="confirmed") for i in range(4)]
    db = NS(execute=lambda *args: NS(scalars=lambda: iter(rows)))
    grants = {2, 3}
    monkeypatch.setattr(repo, "memory_dataset_actions_ok", lambda *args, row, **kw: row.id in grants)
    assert [r.id for r in repo.list_recent(db, role_ids=[], limit=1)] == [2]
    assert repo.counts(db, role_ids=[]) == {"confirmed": 2}
    grants.clear()
    assert repo.list_recent(db, role_ids=[], limit=1) == []
    assert repo.counts(db, role_ids=[]) == {}


@pytest.mark.parametrize("limited", [False, True])
def test_login_denial_survives_real_dependency_rollback(monkeypatch, limited):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from app.api import deps
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE denied_audit (outcome TEXT)"))
    factory = sessionmaker(engine)
    monkeypatch.setattr(deps, "get_session_factory", lambda: factory)
    monkeypatch.setattr(auth, "get_login_limiter", lambda *args: NS(check=lambda *args: not limited, record_failure=lambda *args: None))
    monkeypatch.setattr(auth.users_repo, "get_user_by_username", lambda *args: None)
    monkeypatch.setattr(auth.audit_repo, "add_audit", lambda db, **kw: db.execute(text("INSERT INTO denied_audit VALUES (:outcome)"), {"outcome": kw["outcome"]}))
    app = FastAPI()
    app.include_router(auth.router)
    app.dependency_overrides[auth.get_settings_dep] = lambda: Settings()
    @app.exception_handler(ApiError)
    async def handle(request, exc):
        from fastapi.responses import JSONResponse
        return JSONResponse(exc.to_payload(), status_code=exc.status)
    with TestClient(app) as client:
        assert client.post("/auth/login", json={"username": "unknown", "password": "wrong-password"}).status_code == (429 if limited else 401)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT outcome FROM denied_audit")).scalars().all() == ["rate_limited" if limited else "denied"]
    engine.dispose()


@pytest.mark.parametrize("kind,formula,expected", [("ratio_of_sums", None, ("5", "4", "-1")), ("sum_of_aggregates", "aa-bb", ("7", "5", "-2")), ("ratio_of_sums", "aa/nullif(bb,0)", ("5", "4", "-1")), ("sum_of_aggregates", "coalesce(aa,bb)", ("3", "3", "0")), ("ratio_of_sums", "coalesce(aa/nullif(bb,0),0)", ("0", "0", "0"))])
def test_observer_uses_period_total_and_composition(monkeypatch, kind, formula, expected):
    from app.agent import runner
    descriptors, jobs, payloads = [], {}, {}
    def add(period, alias, values, total=False):
        id = uuid.uuid4()
        descriptors.append({"query_id": str(id), "metric_alias": alias, "period": period, "period_total": total, "composition": formula, "dataset_id": str(uuid.uuid4()), "role": "primary"})
        jobs[id] = NS(id=id, status="SUCCEEDED")
        payloads[str(id)] = {"columns": [{"name": "country"}, {"name": alias}], "rows": [["US", value] for value in values]}
    for period, value in [("baseline", "5"), ("current", "4")]:
        if formula:
            add(period, "aa", [None if formula.startswith("coalesce") else ("10" if period == "baseline" else "8")])
            add(period, "bb", ["3"] if kind == "sum_of_aggregates" else ["2"])
        else:
            add(period, "ecpm", ["2", "8"])
            add(period, "ecpm", [value], total=True)
    results = iter([NS(storage_key=item["query_id"], expires_at=None) for item in descriptors])
    db = NS(execute=lambda *args: NS(scalar_one=lambda: next(results)))
    monkeypatch.setattr(runner, "get_result_store", lambda: NS(read_json=lambda key: payloads[key]))
    monkeypatch.setattr(runner.analyses_repo, "list_steps", lambda *args: [])
    monkeypatch.setattr(runner, "_event", lambda *args, **kw: None)
    task = NS(id=uuid.uuid4(), state={"dimensions": ["country"] if formula is None else [], "metric_definition": {"aggregation_kind": kind}})
    runner._observe(db, task=task, descriptors=descriptors, jobs=jobs)
    observation = task.state["observations"]
    assert tuple(observation[k] for k in ("baseline", "current", "delta")) == expected
    if kind == "ratio_of_sums":
        assert "contribution" not in observation


@pytest.mark.parametrize("formula,expected", [("coalesce(aa,bb)", "3"), ("coalesce(aa/nullif(bb,0),0)", "0")])
def test_null_components_reach_formula_evaluator(formula, expected):
    from app.metrics.evaluator import evaluate_formula
    assert evaluate_formula(formula, {"aa": None, "bb": Decimal(3)}) == Decimal(expected)


@pytest.mark.parametrize("formula,expected", [("aa+bb", True), ("aa-bb", True), ("aa/bb", False), ("abs(aa)", False)])
def test_nonlinear_composition_is_not_additive_attribution(formula, expected):
    from app.metrics.evaluator import additive_composition
    assert additive_composition(formula) is expected
