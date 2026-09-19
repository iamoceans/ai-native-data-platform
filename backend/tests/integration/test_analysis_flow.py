"""M5 acceptance: API -> analysis worker -> Query Gateway -> evidence report."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from app.agent import runner as analysis_runner
from app.analysis.types import EFFECT_TOLERANCE
from app.config import get_settings
from app.db import session_scope
from app.metrics.registry import MetricDefinitionModel, sync_metric_definitions
from app.repositories import queue as queue_repo
from tests.helpers import drain_worker, setup_datasource_and_grants

pytestmark = pytest.mark.integration


def test_analysis_closes_loop_through_real_query_gateway(admin_client, integration_env):
    setup_datasource_and_grants(
        admin_client,
        integration_env["source_url"],
        grant_roles=["admin"],
    )
    definition = MetricDefinitionModel.model_validate(
        {
            "metric_key": "fixture_revenue",
            "version": 1,
            "name": "Fixture revenue",
            "dataset": "public.fixture_metrics",
            "components": {"revenue": {"aggregation": "sum", "column": "revenue_usd"}},
            "formula": "revenue",
            "unit": "USD",
            "currency": "USD",
            "timezone": "UTC",
            "grain": ["dt", "country", "platform"],
            "allowed_dimensions": ["country", "platform"],
            "aggregation_kind": "sum",
            "freshness": {"date_column": "dt", "completeness_source": "fixture"},
        }
    )
    with session_scope() as session:
        sync_metric_definitions(session, [definition])

    created_session = admin_client.post("/api/v1/sessions", json={"title": "Revenue check"})
    assert created_session.status_code == 201, created_session.text
    submitted = admin_client.post(
        "/api/v1/analyses",
        headers={"Idempotency-Key": "analysis-flow-1"},
        json={
            "session_id": created_session.json()["id"],
            "question": "Did fixture revenue change?",
            "context": {
                "metric_key": "fixture_revenue",
                "baseline_start": "2026-09-01",
                "baseline_end": "2026-09-02",
                "current_start": "2026-09-02",
                "current_end": "2026-09-03",
                "dimensions": [],
                "filters": {},
                "data_complete": True,
            },
        },
    )
    assert submitted.status_code == 202, submitted.text
    analysis_id = submitted.json()["analysis_id"]
    settings = get_settings()

    with session_scope() as session:
        claim = queue_repo.claim_next_analysis(
            session, worker_id=f"analysis-test-{uuid.uuid4().hex[:6]}", settings=settings
        )
    assert claim is not None
    with session_scope() as session:
        analysis_runner.execute_claim(session, claim=claim, settings=settings)

    assert drain_worker() == 2

    with session_scope() as session:
        claim = queue_repo.claim_next_analysis(
            session, worker_id=f"analysis-test-{uuid.uuid4().hex[:6]}", settings=settings
        )
    assert claim is not None
    with session_scope() as session:
        analysis_runner.execute_claim(session, claim=claim, settings=settings)

    detail = admin_client.get(f"/api/v1/analyses/{analysis_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "COMPLETED"
    assert len(detail.json()["steps"]) == 2
    report = admin_client.get(f"/api/v1/analyses/{analysis_id}/report")
    assert report.status_code == 200, report.text
    assert report.json()["claims"][0]["query_ids"]
    evidence = admin_client.get(f"/api/v1/analyses/{analysis_id}/evidence")
    assert evidence.status_code == 200, evidence.text
    assert len(evidence.json()["queries"]) == 2
    assert evidence.json()["calculations"][0]["kind"] == "period_comparison"


def _metric(payload: dict) -> MetricDefinitionModel:
    return MetricDefinitionModel.model_validate(payload)


def _submit_analysis(admin_client, *, key: str, question: str, context: dict) -> str:
    created = admin_client.post("/api/v1/sessions", json={"title": question[:40]})
    assert created.status_code == 201, created.text
    submitted = admin_client.post(
        "/api/v1/analyses",
        headers={"Idempotency-Key": key},
        json={"session_id": created.json()["id"], "question": question, "context": context},
    )
    assert submitted.status_code == 202, submitted.text
    return submitted.json()["analysis_id"]


def _run_analysis_until_settled(admin_client, analysis_id: str, *, max_rounds: int = 6) -> dict:
    """Claim, execute, drain query jobs, repeat - exactly what the worker does."""
    settings = get_settings()
    for _ in range(max_rounds):
        with session_scope() as session:
            claim = queue_repo.claim_next_analysis(
                session, worker_id=f"analysis-test-{uuid.uuid4().hex[:6]}", settings=settings
            )
        if claim is None or claim.analysis_id != uuid.UUID(analysis_id):
            break
        with session_scope() as session:
            analysis_runner.execute_claim(session, claim=claim, settings=settings)
        drain_worker()
        detail = admin_client.get(f"/api/v1/analyses/{analysis_id}").json()
        if detail["status"] in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}:
            return detail
    return admin_client.get(f"/api/v1/analyses/{analysis_id}").json()


def test_analysis_adapts_with_driver_decomposition(admin_client, integration_env):
    """A material change plus a declared impressions metric buys one more step."""
    setup_datasource_and_grants(
        admin_client,
        integration_env["source_url"],
        grant_roles=["admin"],
    )
    with session_scope() as session:
        sync_metric_definitions(
            session,
            [
                _metric(
                    {
                        "metric_key": "fixture_ads_revenue",
                        "version": 1,
                        "name": "Fixture ads revenue",
                        "dataset": "public.fixture_metrics",
                        "components": {
                            "revenue": {"aggregation": "sum", "column": "revenue_usd"}
                        },
                        "formula": "revenue",
                        "unit": "USD",
                        "currency": "USD",
                        "timezone": "UTC",
                        "grain": ["dt", "country", "platform"],
                        "allowed_dimensions": ["country", "platform"],
                        "aggregation_kind": "sum",
                        "freshness": {"date_column": "dt", "completeness_source": "fixture"},
                        "driver_decomposition": {"impressions_metric": "fixture_impressions"},
                    }
                ),
                _metric(
                    {
                        "metric_key": "fixture_impressions",
                        "version": 1,
                        "name": "Fixture impressions",
                        "dataset": "public.fixture_metrics",
                        "components": {
                            "impressions": {"aggregation": "sum", "column": "impressions"}
                        },
                        "formula": "impressions",
                        "unit": "count",
                        "timezone": "UTC",
                        "grain": ["dt", "country", "platform"],
                        "allowed_dimensions": ["country", "platform"],
                        "aggregation_kind": "sum",
                        "freshness": {"date_column": "dt", "completeness_source": "fixture"},
                    }
                ),
            ],
        )
    analysis_id = _submit_analysis(
        admin_client,
        key="analysis-drivers-1",
        question="Why did fixture ads revenue change by country?",
        context={
            "metric_key": "fixture_ads_revenue",
            "baseline_start": "2026-09-01",
            "baseline_end": "2026-09-02",
            "current_start": "2026-09-09",
            "current_end": "2026-09-11",
            "dimensions": ["country"],
            "filters": {},
            "data_complete": True,
        },
    )
    detail = _run_analysis_until_settled(admin_client, analysis_id)
    assert detail["status"] == "COMPLETED", detail.get("state", {}).get("last_error")
    step_keys = [step["key"] for step in detail["steps"]]
    assert step_keys == ["baseline_0", "baseline_drivers_0", "current_0", "current_drivers_0"]
    assert any(step["kind"] == "driver_decomposition" for step in detail["plan"]["steps"])
    assert detail["budget"]["queries"] == 4

    evidence = admin_client.get(f"/api/v1/analyses/{analysis_id}/evidence")
    assert evidence.status_code == 200, evidence.text
    kinds = {item["kind"] for item in evidence.json()["calculations"]}
    assert kinds == {"period_comparison", "driver_decomposition"}
    drivers = next(
        item
        for item in evidence.json()["calculations"]
        if item["kind"] == "driver_decomposition"
    )
    assert drivers["status"] == "ok"
    residual = abs(
        Decimal(str(drivers["impression_effect"]))
        + Decimal(str(drivers["ecpm_effect"]))
        - Decimal(str(drivers["delta"]))
    )
    assert residual <= EFFECT_TOLERANCE

    report = admin_client.get(f"/api/v1/analyses/{analysis_id}/report").json()
    driver_claim = next(claim for claim in report["claims"] if claim["id"] == "claim-3")
    assert driver_claim["calculation_id"] == drivers["id"]
    assert driver_claim["value_pointer"] in {"/impression_effect", "/ecpm_effect"}
    assert drivers["impressions_metric"] == "fixture_impressions"
    assert all(claim["query_ids"] for claim in report["claims"])


DRIFT_DDL = """
DROP TABLE IF EXISTS public.drift_probe;
CREATE TABLE public.drift_probe (
  dt date NOT NULL,
  country text NOT NULL,
  revenue_usd numeric(20,6) NOT NULL,
  legacy_dim text NOT NULL
);
INSERT INTO public.drift_probe (dt, country, revenue_usd, legacy_dim)
SELECT
  DATE '2026-09-01' + (gs % 4),
  CASE WHEN gs % 2 = 0 THEN 'US' ELSE 'DE' END,
  ((gs % 501)::numeric / 4)::numeric(20,6),
  'legacy-' || (gs % 3)
FROM generate_series(1, 800) AS gs;
"""


def _source_execute(source_url: str, sql: str) -> None:
    import psycopg

    from tests.helpers import source_dsn

    with psycopg.connect(source_dsn(source_url)) as conn:
        conn.execute(sql)
        conn.commit()


def test_analysis_repairs_a_dropped_dimension_within_budget(admin_client, integration_env):
    """Schema drift: the registered snapshot still has the column, the source
    does not. The analysis drops that dimension, says so, and still answers."""
    setup = setup_datasource_and_grants(
        admin_client, integration_env["source_url"], grant_roles=["admin"]
    )
    _source_execute(integration_env["source_url"], DRIFT_DDL)
    refresh = admin_client.post(
        f"/api/v1/admin/datasources/{setup.datasource_id}/catalog-refresh",
        json={"schemas": ["public"]},
    )
    assert refresh.status_code == 200, refresh.text
    probe_id = next(
        item["id"] for item in refresh.json()["items"] if item["object_name"] == "drift_probe"
    )
    role_id = next(
        role["id"]
        for role in admin_client.get("/api/v1/admin/roles").json()
        if role["name"] == "admin"
    )
    for action in ("discover", "query"):
        granted = admin_client.post(
            "/api/v1/admin/grants",
            json={"role_id": role_id, "dataset_id": probe_id, "action": action},
        )
        assert granted.status_code == 201, granted.text
    with session_scope() as session:
        sync_metric_definitions(
            session,
            [
                _metric(
                    {
                        "metric_key": "fixture_drift_revenue",
                        "version": 1,
                        "name": "Fixture drift revenue",
                        "dataset": "public.drift_probe",
                        "components": {
                            "revenue": {"aggregation": "sum", "column": "revenue_usd"}
                        },
                        "formula": "revenue",
                        "unit": "USD",
                        "currency": "USD",
                        "timezone": "UTC",
                        "grain": ["dt", "country", "legacy_dim"],
                        "allowed_dimensions": ["country", "legacy_dim"],
                        "aggregation_kind": "sum",
                        "freshness": {"date_column": "dt", "completeness_source": "fixture"},
                    }
                )
            ],
        )
    try:
        analysis_id = _submit_analysis(
            admin_client,
            key="analysis-repair-1",
            question="Did drift revenue change by legacy dimension?",
            context={
                "metric_key": "fixture_drift_revenue",
                "baseline_start": "2026-09-01",
                "baseline_end": "2026-09-02",
                "current_start": "2026-09-03",
                "current_end": "2026-09-04",
                "dimensions": ["legacy_dim"],
                "filters": {},
                "data_complete": True,
            },
        )
        # Prepare first (the two queries must exist), then let the source drift:
        # this is the window between submission and execution that the executor
        # re-validation is there to catch.
        settings = get_settings()
        with session_scope() as session:
            claim = queue_repo.claim_next_analysis(
                session, worker_id="repair-prep", settings=settings
            )
        with session_scope() as session:
            analysis_runner.execute_claim(session, claim=claim, settings=settings)
        _source_execute(
            integration_env["source_url"], "ALTER TABLE public.drift_probe DROP COLUMN legacy_dim"
        )
        detail = _run_analysis_until_settled(admin_client, analysis_id)
        assert detail["status"] == "COMPLETED", detail.get("state", {}).get("last_error")
        repairs = detail["state"]["repairs"]
        applied = [item for item in repairs if item["applied"]]
        assert [item["dropped_dimension"] for item in applied] == ["legacy_dim"]
        assert len(applied[0]["replacement_query_ids"]) == 2
        assert detail["budget"]["sql_repairs"] == 1
        assert detail["budget"]["queries"] == 4
        statuses = {step["key"]: step["status"] for step in detail["steps"]}
        assert statuses["baseline_0"] == "SKIPPED"
        assert statuses["baseline_repair_0"] == "SUCCEEDED"
        report = admin_client.get(f"/api/v1/analyses/{analysis_id}/report").json()
        assert report["status"] == "COMPLETED"
        assert any("丢弃列 legacy_dim" in item for item in report["limitations"])
        assert report["claims"][0]["query_ids"]
    finally:
        _source_execute(integration_env["source_url"], "DROP TABLE IF EXISTS public.drift_probe")
