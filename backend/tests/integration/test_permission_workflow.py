"""Integration: the access-request workflow (request -> approve/reject -> grant).

Spec 24 requires the Permissions screen to keep real grants and mock states
visibly apart, and a requester to see what happened to their request. This file
walks the whole loop against real services:

- a viewer asks for a dataset it cannot query,
- the request is visible to that viewer (and only to that viewer) and to the
  administrator queue,
- approving it creates the *real* grant, bumps the policy revision and lets the
  viewer run the query,
- rejecting creates nothing,
- a viewer cannot approve or reject anything.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from tests.conftest import login
from tests.helpers import drain_worker, setup_datasource_and_grants, wait_for_query

pytestmark = pytest.mark.integration


@pytest.fixture()
def source(admin_client):
    url = os.environ.get("AIND_TEST_SOURCE_URL")
    if not url:
        pytest.skip("AIND_TEST_SOURCE_URL not set")
    # Only the administrator gets grants: the viewer starts with default deny.
    return setup_datasource_and_grants(admin_client, url, grant_roles=["admin"])


def _viewer_client(app, viewer):
    client = TestClient(app, raise_server_exceptions=False)
    csrf = login(client, viewer["username"], viewer["password"])
    client.headers.update({"X-CSRF-Token": csrf})
    return client


def _run_query(client, datasource_id: str, sql: str):
    submitted = client.post(
        "/api/v1/queries", json={"datasource_id": datasource_id, "sql": sql}
    )
    if submitted.status_code != 202:
        return submitted
    drain_worker()
    return wait_for_query(client, submitted.json()["query_id"])


def test_request_approval_creates_the_real_grant_and_unlocks_the_query(
    admin_client, app, viewer, source
):
    dataset_id = source.dataset_ids["fixture_metrics"]
    sql = "SELECT COUNT(*) AS n FROM fixture_metrics"
    with _viewer_client(app, viewer) as viewer_client:
        # 1. default deny: the viewer cannot query and cannot see the dataset
        denied = _run_query(viewer_client, source.datasource_id, sql)
        assert denied.status_code == 403, denied.text
        assert denied.json()["error"]["code"] == "PERMISSION_DENIED"
        assert dataset_id not in {
            item["id"] for item in viewer_client.get("/api/v1/datasets").json()["items"]
        }

        # 2. the viewer files a request and can see it, and only it
        created = viewer_client.post(
            "/api/v1/permission-requests",
            json={"dataset_id": dataset_id, "reason": "weekly revenue report"},
        )
        assert created.status_code == 201, created.text
        request_id = created.json()["id"]
        assert created.json()["status"] == "REQUESTED"

        mine = viewer_client.get("/api/v1/permission-requests")
        assert mine.status_code == 200, mine.text
        assert [item["id"] for item in mine.json()["items"]] == [request_id]
        assert mine.json()["items"][0]["user_id"] == created.json()["user_id"]

        # 3. a viewer cannot decide anything
        forbidden = viewer_client.post(
            f"/api/v1/admin/permission-requests/{request_id}/approve",
            json={"role_id": created.json()["user_id"], "action": "query"},
        )
        assert forbidden.status_code == 403
        assert viewer_client.post(
            f"/api/v1/admin/permission-requests/{request_id}/reject", json={}
        ).status_code == 403

        # 4. the administrator sees it in the queue
        queue = admin_client.get("/api/v1/admin/permission-requests").json()["items"]
        assert request_id in {item["id"] for item in queue}

        # 5. approving creates the real grant (discover implied by query)
        role_id = next(
            role["id"]
            for role in admin_client.get("/api/v1/admin/roles").json()
            if role["name"] == "viewer"
        )
        approved = admin_client.post(
            f"/api/v1/admin/permission-requests/{request_id}/approve",
            json={"role_id": role_id, "action": "query"},
        )
        assert approved.status_code == 200, approved.text
        assert approved.json()["status"] == "APPROVED"

        grants = admin_client.get("/api/v1/admin/grants").json()["items"]
        granted_actions = {
            grant["action"]
            for grant in grants
            if grant["role_id"] == role_id and grant["dataset_id"] == dataset_id
        }
        assert granted_actions == {"discover", "query"}, granted_actions

        # the same request cannot be approved twice
        again = admin_client.post(
            f"/api/v1/admin/permission-requests/{request_id}/approve",
            json={"role_id": role_id, "action": "query"},
        )
        assert again.status_code == 409

        # 6. the grant is real: the viewer can now discover and query
        assert dataset_id in {
            item["id"] for item in viewer_client.get("/api/v1/datasets").json()["items"]
        }
        finished = _run_query(viewer_client, source.datasource_id, sql)
        assert finished["status"] == "SUCCEEDED", finished

        # 7. the requester sees the outcome
        mine = viewer_client.get("/api/v1/permission-requests").json()["items"]
        assert mine[0]["status"] == "APPROVED"


def test_request_rejection_grants_nothing(admin_client, app, viewer, source):
    dataset_id = source.dataset_ids["fixture_heavy"]
    with _viewer_client(app, viewer) as viewer_client:
        created = viewer_client.post(
            "/api/v1/permission-requests",
            json={"dataset_id": dataset_id, "reason": "exploratory analysis"},
        )
        assert created.status_code == 201, created.text
        request_id = created.json()["id"]

        rejected = admin_client.post(
            f"/api/v1/admin/permission-requests/{request_id}/reject",
            json={"note": "not needed for this role"},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "REJECTED"

        role_id = next(
            role["id"]
            for role in admin_client.get("/api/v1/admin/roles").json()
            if role["name"] == "viewer"
        )
        grants = admin_client.get("/api/v1/admin/grants").json()["items"]
        assert not [
            grant
            for grant in grants
            if grant["role_id"] == role_id and grant["dataset_id"] == dataset_id
        ], "a rejected request must not create a grant"

        # the requester is told, and still cannot query
        mine = viewer_client.get("/api/v1/permission-requests").json()["items"]
        assert mine[0]["status"] == "REJECTED"
        denied = _run_query(
            viewer_client, source.datasource_id, "SELECT COUNT(*) AS n FROM fixture_heavy"
        )
        assert denied.status_code == 403, denied.text
