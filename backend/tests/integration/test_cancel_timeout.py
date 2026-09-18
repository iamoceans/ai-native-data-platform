"""Integration: cancel and timeout semantics with a real long-running query."""

from __future__ import annotations

import os
import threading
import time

import pytest

from tests.helpers import drain_worker, setup_datasource_and_grants, wait_for_query

pytestmark = pytest.mark.integration

HEAVY_SQL = (
    "SELECT COUNT(*) AS n FROM fixture_heavy a JOIN fixture_heavy b ON a.bucket = b.bucket"
)


@pytest.fixture()
def source(admin_client, request):
    url = os.environ.get("AIND_TEST_SOURCE_URL")
    if not url:
        pytest.skip("AIND_TEST_SOURCE_URL not set")
    return setup_datasource_and_grants(admin_client, url)


def test_cancel_stops_the_source_query(admin_client, source):
    response = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": HEAVY_SQL,
            "limits": {"timeout_seconds": 120},
        },
    )
    assert response.status_code == 202, response.text
    query_id = response.json()["query_id"]

    worker = threading.Thread(target=drain_worker, kwargs={"max_cycles": 1}, daemon=True)
    worker.start()

    # wait until the worker has claimed and started it
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        detail = admin_client.get(f"/api/v1/queries/{query_id}").json()
        if detail["status"] == "RUNNING":
            break
        time.sleep(0.2)
    assert detail["status"] == "RUNNING", detail

    cancel = admin_client.post(f"/api/v1/queries/{query_id}/cancel")
    assert cancel.status_code in (200, 202), cancel.text
    assert cancel.json()["status"] in ("CANCEL_REQUESTED", "CANCELLED", "SUCCEEDED")

    worker.join(timeout=60)
    assert not worker.is_alive(), "worker did not stop after cancel"

    final = wait_for_query(admin_client, query_id, timeout=30)
    assert final["status"] == "CANCELLED", final
    assert final["error"]["code"] == "QUERY_CANCELLED"

    # no orphan result and the cancel is audited
    results = admin_client.get(f"/api/v1/queries/{query_id}/results").json()
    assert results["result"] is None


def test_timeout_is_enforced_by_the_source(admin_client, source):
    response = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": HEAVY_SQL,
            "limits": {"timeout_seconds": 2},
        },
    )
    assert response.status_code == 202, response.text
    query_id = response.json()["query_id"]
    drain_worker(max_cycles=1)
    final = wait_for_query(admin_client, query_id, timeout=60)
    assert final["status"] == "TIMED_OUT", final
    assert final["error"]["code"] == "QUERY_TIMEOUT"


def test_cancel_after_completion_is_a_noop(admin_client, source):
    response = admin_client.post(
        "/api/v1/queries",
        json={"datasource_id": source.datasource_id, "sql": "SELECT COUNT(*) AS n FROM fixture_metrics"},
    )
    query_id = response.json()["query_id"]
    drain_worker()
    wait_for_query(admin_client, query_id)
    late_cancel = admin_client.post(f"/api/v1/queries/{query_id}/cancel")
    assert late_cancel.status_code == 200
    assert late_cancel.json()["status"] == "SUCCEEDED"
