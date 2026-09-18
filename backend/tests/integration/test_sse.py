"""Integration: SSE event stream (spec section 23, A15).

Replay by Last-Event-ID, live delivery and permission revocation closing the
stream.
"""

from __future__ import annotations

import json
import os
import threading
import time

from fastapi.testclient import TestClient
import pytest

from tests.conftest import login
from tests.helpers import drain_worker, setup_datasource_and_grants

pytestmark = pytest.mark.integration


def parse_sse(lines: list[str]) -> list[dict]:
    events: list[dict] = []
    current: dict = {}
    for line in lines:
        if line.startswith("id: "):
            current["id"] = int(line[4:])
        elif line.startswith("event: "):
            current["event"] = line[7:]
        elif line.startswith("data: "):
            current["data"] = json.loads(line[6:])
        elif line == "" and current:
            events.append(current)
            current = {}
    if current:
        events.append(current)
    return events


@pytest.fixture()
def source(admin_client):
    url = os.environ.get("AIND_TEST_SOURCE_URL")
    if not url:
        pytest.skip("AIND_TEST_SOURCE_URL not set")
    return setup_datasource_and_grants(admin_client, url)


@pytest.fixture()
def viewer_client(app, viewer):
    """A second client authenticated as a single-role viewer."""
    with TestClient(app, raise_server_exceptions=False) as second:
        csrf = login(second, viewer["username"], viewer["password"])
        second.headers.update({"X-CSRF-Token": csrf})
        yield second


def _submit_and_finish(admin_client, datasource_id) -> str:
    response = admin_client.post(
        "/api/v1/queries",
        json={"datasource_id": datasource_id, "sql": "SELECT COUNT(*) AS n FROM fixture_metrics"},
    )
    query_id = response.json()["query_id"]
    drain_worker()
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        detail = admin_client.get(f"/api/v1/queries/{query_id}").json()
        if detail["status"] == "SUCCEEDED":
            break
        time.sleep(0.1)
    return query_id


def test_sse_replays_events_by_last_event_id(admin_client, source):
    query_id = _submit_and_finish(admin_client, source.datasource_id)
    events: list[dict] = []
    with admin_client.stream(
        "GET", f"/api/v1/queries/{query_id}/events", headers={"Last-Event-ID": "0"}
    ) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            events.append(line)
            if "stream.end" in line:
                break
    parsed = parse_sse(events)
    types = [event["event"] for event in parsed]
    assert "query.queued" in types
    assert "query.started" in types
    assert "query.finished" in types
    ids = [event["id"] for event in parsed if "id" in event]
    assert ids == sorted(ids)
    assert len(ids) == len({*ids})  # no duplicates on replay
    finished = next(event for event in parsed if event["event"] == "query.finished")
    assert finished["data"]["status"] == "SUCCEEDED"


def test_sse_live_stream_finishes_when_query_completes(admin_client, source):
    response = admin_client.post(
        "/api/v1/queries",
        json={"datasource_id": source.datasource_id, "sql": "SELECT 1 AS one FROM fixture_metrics"},
    )
    query_id = response.json()["query_id"]

    def drain():
        time.sleep(0.3)
        drain_worker()

    worker = threading.Thread(target=drain, daemon=True)
    worker.start()

    lines: list[str] = []
    with admin_client.stream("GET", f"/api/v1/queries/{query_id}/events") as stream:
        assert stream.status_code == 200
        for line in stream.iter_lines():
            lines.append(line)
            if "stream.end" in line:
                break
    worker.join(timeout=10)
    parsed = parse_sse(lines)
    assert any(event["event"] == "query.finished" for event in parsed)


def test_permission_revocation_closes_the_stream(admin_client, viewer_client, source):
    """Revoking the dataset grant while a query is live must cancel the query,
    close the SSE stream and block result downloads (spec 12.3/23)."""
    # A slow query keeps the stream open long enough to revoke mid-flight;
    # the viewer holds exactly one role, so revoking that role's grant removes
    # the permission completely.
    response = viewer_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": (
                "SELECT COUNT(*) AS n FROM fixture_heavy a JOIN fixture_heavy b "
                "ON a.bucket = b.bucket"
            ),
            "limits": {"timeout_seconds": 120},
        },
    )
    assert response.status_code == 202, response.text
    query_id = response.json()["query_id"]

    grants = admin_client.get("/api/v1/admin/grants").json()["items"]
    dataset_id = source.dataset_ids["fixture_heavy"]
    roles = {role["name"]: role["id"] for role in admin_client.get("/api/v1/admin/roles").json()}
    viewer_role_id = roles["viewer"]
    target = next(
        grant
        for grant in grants
        if grant["dataset_id"] == dataset_id
        and grant["action"] == "query"
        and grant["role_id"] == viewer_role_id
    )

    lines: list[str] = []
    stream_ended = threading.Event()

    def revoke_later():
        time.sleep(1.0)
        revoked = admin_client.delete(f"/api/v1/admin/grants/{target['id']}")
        assert revoked.status_code == 204, revoked.text

    revoker = threading.Thread(target=revoke_later, daemon=True)
    revoker.start()

    with viewer_client.stream("GET", f"/api/v1/queries/{query_id}/events") as stream:
        assert stream.status_code == 200
        for line in stream.iter_lines():
            lines.append(line)
            if "stream.closed" in line or "stream.end" in line:
                stream_ended.set()
                break
    revoker.join(timeout=10)
    assert stream_ended.is_set(), f"stream stayed open after revocation: {lines[-5:]}"

    # The revocation cancelled the associated live query.
    detail = viewer_client.get(f"/api/v1/queries/{query_id}").json()
    assert detail["status"] == "CANCELLED", detail

    # Result downloads are blocked after revocation.
    results = viewer_client.get(f"/api/v1/queries/{query_id}/results")
    assert results.status_code == 403
    assert results.json()["error"]["code"] == "PERMISSION_DENIED"


def test_sse_unknown_query_is_404(admin_client, source):
    response = admin_client.get("/api/v1/queries/00000000-0000-0000-0000-000000000000/events")
    assert response.status_code == 404
