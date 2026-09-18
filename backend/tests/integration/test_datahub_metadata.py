"""M3 integration tests: DataHub catalog, context, lineage, ingestion (A02/A04).

Two execution paths are exercised:

1. **Deployed full stack** (``test_m3_catalog_sync_context_lineage_permissions``):
   the platform API and the ingestion worker run in their containers (core+full
   profile with ``AIND_DATAHUB_ENABLED=1`` and the DataHub stack up). The test
   drives the *container* API over HTTP, so catalog refresh, ingestion payload
   writing, ``datahub ingest`` execution and URN mapping are all the real
   production path. Skipped when the container API or DataHub GMS is not
   reachable (never counted as passed).

2. **In-process runner** (``test_failed_ingestion_keeps_previous_mapping``): the
   ingestion runner module is executed in-process against the control DB with a
   deliberately missing secret, verifying that a failed sync preserves the
   previous URN mapping (spec 10.1).

Requires the control DB (``AIND_DATABASE_URL``) plus, for path 1, the running
stack (defaults: API ``http://127.0.0.1:8000``, GMS ``http://127.0.0.1:18080``).
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import types
import uuid
from pathlib import Path

import httpx
import pytest

from tests.helpers import setup_datasource_and_grants

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parents[3]  # repo root (this file lives in backend/tests/integration)
CONTAINER_API_ENV = "AIND_CONTAINER_API_URL"
GMS_ENV = "AIND_DATAHUB_GMS_URL"
DEFAULT_CONTAINER_API = "http://127.0.0.1:8000"
DEFAULT_GMS = "http://127.0.0.1:18080"
TERMINAL = {"SUCCEEDED", "FAILED", "LOST"}

DORIS_DATASOURCE = {
    "name": "m3-doris",
    "kind": "doris",
    "connection_config": {
        "host": "doris-fe",
        "port": 9030,
        "database": "demo",
        "connect_timeout_seconds": 5,
    },
    "secret_ref": "doris",
}
POSTGRES_DATASOURCE = {
    "name": "m3-postgres",
    "kind": "postgres",
    "connection_config": {
        "host": "source-postgres",
        "port": 5432,
        "database": "ainative_source",
        "ssl_mode": "disable",
        "connect_timeout_seconds": 5,
    },
    "secret_ref": "source-postgres",
}


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------
@pytest.fixture(scope="module")
def container_stack() -> dict:
    """Skip unless the deployed platform API and DataHub GMS are reachable."""
    api_url = os.environ.get(CONTAINER_API_ENV, DEFAULT_CONTAINER_API).rstrip("/")
    gms_url = os.environ.get(GMS_ENV, DEFAULT_GMS).rstrip("/")
    try:
        api = httpx.get(f"{api_url}/api/v1/health/live", timeout=3)
        gms = httpx.get(f"{gms_url}/health", timeout=3)
    except httpx.HTTPError as exc:
        pytest.skip(f"deployed stack not reachable ({exc!r}); start it before running this test")
    if api.status_code != 200 or gms.status_code != 200:
        pytest.skip(
            f"deployed stack not healthy (api={api.status_code}, gms={gms.status_code})"
        )
    return {"api": f"{api_url}/api/v1", "gms": gms_url}


def _api_login(client: httpx.Client, username: str, password: str) -> None:
    response = client.post("/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def _grant_all(client: httpx.Client, role_id: str, dataset_ids: list[str]) -> None:
    for dataset_id in dataset_ids:
        for action in ("discover", "query"):
            created = client.post(
                "/admin/grants",
                json={"role_id": role_id, "dataset_id": dataset_id, "action": action},
            )
            assert created.status_code == 201, created.text


def _wait_ingestion(client: httpx.Client, task_id: str, timeout: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = client.get(f"/ingestions/{task_id}").json()
        if task["status"] in TERMINAL:
            return task
        time.sleep(3)
    raise AssertionError(f"ingestion {task_id} did not reach a terminal state in {timeout}s")


# ---------------------------------------------------------------------------
# path 1: deployed full stack
# ---------------------------------------------------------------------------
def test_m3_catalog_sync_context_lineage_permissions(container_stack, bootstrap, viewer):
    """A02 + A04: real sync maps URNs, context comes from DataHub, the view's
    upstream table is a real captured edge, and every catalog surface is
    permission filtered."""
    with httpx.Client(base_url=container_stack["api"], timeout=60) as admin, httpx.Client(
        base_url=container_stack["api"], timeout=60
    ) as viewer_client:
        _api_login(admin, bootstrap["username"], bootstrap["password"])
        _api_login(viewer_client, viewer["username"], viewer["password"])

        # -- register + refresh (real provider connections from the container)
        doris = admin.post("/datasources", json=DORIS_DATASOURCE)
        assert doris.status_code == 201, doris.text
        doris_id = doris.json()["id"]
        test = admin.post(f"/datasources/{doris_id}/test")
        assert test.status_code == 200 and test.json()["status"] == "HEALTHY", test.text

        refresh = admin.post(
            f"/admin/datasources/{doris_id}/catalog-refresh",
            json={"schemas": ["demo"], "secure_views": ["ads_revenue_by_country"]},
        )
        assert refresh.status_code == 200, refresh.text
        items = refresh.json()["items"]
        doris_ids = {item["object_name"]: item["id"] for item in items}
        assert "ads_revenue_by_country" in doris_ids, refresh.text
        assert "ads_revenue_daily" in doris_ids, refresh.text
        assert all("SENSITIVE_COLUMNS" not in skip for skip in refresh.json()["skipped"])

        pg = admin.post("/datasources", json=POSTGRES_DATASOURCE)
        assert pg.status_code == 201, pg.text
        pg_id = pg.json()["id"]
        pg_refresh = admin.post(
            f"/admin/datasources/{pg_id}/catalog-refresh", json={"schemas": ["public"]}
        )
        assert pg_refresh.status_code == 200, pg_refresh.text
        pg_ids = {item["object_name"]: item["id"] for item in pg_refresh.json()["items"]}
        assert "fixture_metrics" in pg_ids, pg_refresh.text

        roles = {role["name"]: role["id"] for role in admin.get("/admin/roles").json()}
        _grant_all(admin, roles["admin"], list(doris_ids.values()) + list(pg_ids.values()))
        _grant_all(admin, roles["viewer"], [doris_ids["ads_revenue_daily"]])

        # -- ingestion: single active task per datasource (spec 10.1)
        first = admin.post(f"/datasources/{doris_id}/sync")
        assert first.status_code == 202, first.text
        task_id = first.json()["id"]
        second = admin.post(f"/datasources/{doris_id}/sync")
        assert second.status_code == 409, second.text
        assert second.json()["error"]["details"]["ingestion_task_id"] == task_id

        task = _wait_ingestion(admin, task_id)
        assert task["status"] == "SUCCEEDED", task
        summary = task["summary"]
        assert summary["mapped"] == summary["expected"], summary
        assert summary["missing"] == [], summary

        # -- URNs mapped for Doris; the unsynced PostgreSQL source stays PENDING
        datasets = {
            row["object_name"]: row for row in admin.get("/datasets", params={"limit": 50}).json()["items"]
        }
        for name in doris_ids:
            assert datasets[name]["datahub_urn"], f"{name} has no DataHub URN after sync"
            assert datasets[name]["sync_status"] == "SYNCED", name
        assert datasets["fixture_metrics"]["datahub_urn"] is None

        # -- search is permission filtered, totals count authorized assets only
        viewer_search = viewer_client.get("/datasets", params={"q": "revenue"})
        assert viewer_search.status_code == 200
        viewer_names = {item["object_name"] for item in viewer_search.json()["items"]}
        assert viewer_names == {"ads_revenue_daily"}, viewer_names

        admin_search = admin.get("/datasets", params={"q": "revenue"}).json()["items"]
        admin_names = {item["object_name"] for item in admin_search}
        assert {"ads_revenue_daily", "ads_revenue_by_country", "iap_revenue_daily"} <= admin_names

        # -- context is DataHub-backed; deep link is admin-only (spec 10.1)
        context = admin.get(f"/datasets/{doris_ids['ads_revenue_daily']}")
        assert context.status_code == 200, context.text
        body = context.json()
        assert body["metadata_source"] == "datahub", body
        assert body["metadata_stale"] is False, body
        assert body["datahub_url"] and "urn:li:dataset:" in body["datahub_url"], body
        assert body["metric_keys"], "semantic custom properties must round-trip"
        cached = admin.get(f"/datasets/{doris_ids['ads_revenue_daily']}").json()
        assert cached["metadata_cached"] is True

        viewer_context = viewer_client.get(f"/datasets/{doris_ids['ads_revenue_daily']}")
        assert viewer_context.status_code == 200, viewer_context.text
        assert viewer_context.json()["datahub_url"] is None
        assert viewer_context.json()["metadata_source"] == "datahub"

        denied = viewer_client.get(f"/datasets/{doris_ids['ads_revenue_by_country']}")
        assert denied.status_code == 403, denied.text

        # -- lineage: real view -> table edge captured by the Doris connector
        lineage = admin.get(
            f"/datasets/{doris_ids['ads_revenue_by_country']}/lineage",
            params={"direction": "upstream", "depth": 1},
        ).json()
        assert lineage["status"] == "available", lineage
        upstream_names = {node["name"] for node in lineage["nodes"]}
        assert "ads_revenue_daily" in upstream_names, lineage
        mapped_node = next(node for node in lineage["nodes"] if node["name"] == "ads_revenue_daily")
        assert mapped_node["mapped"] is True and mapped_node["label"] == "extracted"
        assert lineage["edges"], "lineage edges must be populated"
        view_urn = datasets["ads_revenue_by_country"]["datahub_urn"]
        assert any(
            edge["target"] == view_urn and edge["source"] == mapped_node["urn"]
            for edge in lineage["edges"]
        ), lineage["edges"]

        # -- unsynced dataset reports not_ingested (honest empty state)
        pg_lineage = admin.get(
            f"/datasets/{pg_ids['fixture_metrics']}/lineage", params={"direction": "upstream"}
        ).json()
        assert pg_lineage["status"] == "not_ingested", pg_lineage

        # -- permission-filtered lineage: viewer sees only the view
        table_grant = admin.get("/admin/grants", params={"dataset_id": doris_ids["ads_revenue_daily"]})
        viewer_table_grants = [
            grant
            for grant in table_grant.json()["items"]
            if grant["role_id"] == roles["viewer"]
        ]
        for grant in viewer_table_grants:
            revoked = admin.delete(f"/admin/grants/{grant['id']}")
            assert revoked.status_code == 204, revoked.text
        _grant_all(admin, roles["viewer"], [doris_ids["ads_revenue_by_country"]])

        filtered = viewer_client.get(
            f"/datasets/{doris_ids['ads_revenue_by_country']}/lineage",
            params={"direction": "upstream", "depth": 1},
        ).json()
        assert filtered["status"] == "permission_filtered", filtered
        assert filtered["filtered_nodes"] >= 1, filtered
        assert filtered["nodes"] == [], filtered


# ---------------------------------------------------------------------------
# path 2: in-process ingestion runner (failure keeps the previous mapping)
# ---------------------------------------------------------------------------
def _load_runner_module():
    """Import ``ingestion/runner.py`` without the ingestion image's psycopg2."""
    if "psycopg2" not in sys.modules:
        stub = types.ModuleType("psycopg2")

        class _Error(Exception):
            pass

        stub.Error = _Error
        sys.modules["psycopg2"] = stub
    module_name = "ainative_ingestion_runner"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "ingestion" / "runner.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_failed_ingestion_keeps_previous_mapping(admin_client, monkeypatch):
    """A failed ingestion must not delete the last successful URN mapping."""
    import psycopg
    from sqlalchemy import text

    from app.config import get_settings
    from app.db import session_scope
    from app.repositories import datasets as datasets_repo

    source_url = os.environ.get("AIND_TEST_SOURCE_URL")
    if not source_url:
        pytest.skip("AIND_TEST_SOURCE_URL not set")
    setup = setup_datasource_and_grants(admin_client, source_url)
    dataset_id = uuid.UUID(setup.dataset_ids["fixture_metrics"])
    legacy_urn = "urn:li:dataset:(urn:li:dataPlatform:postgres,legacy.fixture_metrics,PROD)"
    with session_scope() as session:
        dataset = datasets_repo.get_dataset(session, dataset_id)
        dataset.datahub_urn = legacy_urn
        dataset.sync_status = "SYNCED"

    response = admin_client.post(f"/api/v1/datasources/{setup.datasource_id}/sync")
    assert response.status_code == 202, response.text
    task_id = response.json()["id"]

    settings = get_settings()
    payload_path = settings.ingestion_work_dir / task_id / "payload.json"
    assert payload_path.is_file(), f"ingestion payload missing at {payload_path}"
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    payload["secret_ref"] = "definitely-not-mounted"
    payload_path.write_text(json.dumps(payload), encoding="utf-8")

    runner = _load_runner_module()
    dsn = settings.database_url.replace("postgresql+psycopg://", "postgresql://")

    def fake_connect():
        return psycopg.connect(dsn)

    monkeypatch.setattr(runner, "connect", fake_connect)
    processed = runner.process_once(
        work_dir=settings.ingestion_work_dir,
        secrets_dir=settings.secrets_dir,
        worker_id="m3-test-runner",
        dry_run=False,
    )
    assert processed is True

    task = admin_client.get(f"/api/v1/ingestions/{task_id}").json()
    assert task["status"] == "FAILED", task
    assert "not mounted" in json.dumps(task.get("error") or {}), task

    from app.db import get_engine

    with get_engine().connect() as conn:
        queue_state = conn.execute(
            text("SELECT state FROM task_queue WHERE kind='ingestion' AND resource_id=:rid"),
            {"rid": task_id},
        ).scalar_one()
    assert queue_state == "FAILED"

    with session_scope() as session:
        dataset = datasets_repo.get_dataset(session, dataset_id)
        assert dataset.datahub_urn == legacy_urn
        assert dataset.sync_status == "SYNCED"
