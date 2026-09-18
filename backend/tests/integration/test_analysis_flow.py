"""M5 acceptance: API -> analysis worker -> Query Gateway -> evidence report."""

from __future__ import annotations

import uuid

import pytest

from app.agent import runner as analysis_runner
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
