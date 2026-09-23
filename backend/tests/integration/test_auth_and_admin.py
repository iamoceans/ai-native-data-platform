"""Integration: authentication, CSRF, roles and permission boundaries."""

from __future__ import annotations

import os

import pytest

from tests.conftest import login

pytestmark = pytest.mark.integration


def test_login_me_logout_flow(client, bootstrap):
    csrf = login(client, bootstrap["username"], bootstrap["password"])
    me = client.get("/api/v1/auth/me")
    assert me.status_code == 200
    payload = me.json()
    assert payload["username"] == "admin"
    assert "admin" in payload["roles"]
    assert "admin.manage" in payload["capabilities"]

    logout = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
    assert logout.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401


def test_login_rejects_wrong_password_without_leaking_usernames(client, bootstrap):
    wrong = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "nope-not-it"}
    )
    unknown = client.post(
        "/api/v1/auth/login", json={"username": "ghost-user", "password": "nope-not-it"}
    )
    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]


def test_csrf_header_required_for_unsafe_methods(client, bootstrap):
    login(client, bootstrap["username"], bootstrap["password"])
    response = client.post("/api/v1/datasources", json={})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "CSRF_INVALID"


def test_origin_must_be_allowed(client, bootstrap):
    csrf = login(client, bootstrap["username"], bootstrap["password"])
    response = client.post(
        "/api/v1/admin/roles",
        json={"name": "sneaky"},
        headers={"X-CSRF-Token": csrf, "Origin": "http://evil.example"},
    )
    assert response.status_code == 403


def test_unauthenticated_gets_401(client):
    assert client.get("/api/v1/datasources").status_code == 401
    assert client.get("/api/v1/queries").status_code == 401


def test_viewer_cannot_use_admin_endpoints(client, viewer):
    csrf = login(client, viewer["username"], viewer["password"])
    response = client.get("/api/v1/admin/users", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 403
    response = client.post(
        "/api/v1/admin/roles", json={"name": "role-x"}, headers={"X-CSRF-Token": csrf}
    )
    assert response.status_code == 403


def test_admin_can_create_user_and_assign_roles(admin_client):
    roles = admin_client.get("/api/v1/admin/roles").json()
    analyst = next(role for role in roles if role["name"] == "analyst")
    created = admin_client.post(
        "/api/v1/admin/users",
        json={
            "username": "new-analyst",
            "password": "a-strong-password-42",
            "role_ids": [analyst["id"]],
        },
    )
    assert created.status_code == 201, created.text
    user = created.json()
    assert [role["name"] for role in user["roles"]] == ["analyst"]

    duplicate = admin_client.post(
        "/api/v1/admin/users",
        json={"username": "new-analyst", "password": "a-strong-password-42", "role_ids": []},
    )
    assert duplicate.status_code == 409

    viewer_role = next(role for role in roles if role["name"] == "viewer")
    updated = admin_client.put(
        f"/api/v1/admin/users/{user['id']}/roles", json={"role_ids": [viewer_role["id"]]}
    )
    assert updated.status_code == 200
    assert [role["name"] for role in updated.json()["roles"]] == ["viewer"]


def test_login_rate_limit_blocks_after_repeated_failures(client, bootstrap):
    from app.api.routes.auth import reset_login_limiter

    reset_login_limiter()
    for _ in range(5):
        response = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "wrong-one"}
        )
        assert response.status_code == 401
    blocked = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "wrong-one"}
    )
    assert blocked.status_code == 429
    reset_login_limiter()


def test_audit_log_records_login_and_admin_actions(admin_client):
    audits = admin_client.get("/api/v1/admin/audits", params={"action": "auth.login"})
    assert audits.status_code == 200
    items = audits.json()["items"]
    assert any(item["outcome"] == "success" for item in items)


def test_health_endpoints(client):
    live = client.get("/health/live")
    assert live.status_code == 200 and live.json()["status"] == "ok"
    ready = client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["control_db"] == "ok"


def test_role_revocation_cancels_queries_that_lose_effective_access(
    app, admin_client, integration_env
):
    from fastapi.testclient import TestClient

    from tests.helpers import setup_datasource_and_grants

    source = setup_datasource_and_grants(
        admin_client, integration_env["source_url"], grant_roles=["analyst"]
    )
    roles = admin_client.get("/api/v1/admin/roles").json()
    analyst = next(role for role in roles if role["name"] == "analyst")
    created = admin_client.post(
        "/api/v1/admin/users",
        json={
            "username": "revoked-analyst",
            "password": "strong-password-for-revocation",
            "role_ids": [analyst["id"]],
        },
    )
    assert created.status_code == 201, created.text

    with TestClient(app, raise_server_exceptions=False) as analyst_client:
        csrf = login(analyst_client, "revoked-analyst", "strong-password-for-revocation")
        analyst_client.headers.update({"X-CSRF-Token": csrf})
        submitted = analyst_client.post(
            "/api/v1/queries",
            json={
                "datasource_id": source.datasource_id,
                "sql": "SELECT country, SUM(revenue_usd) AS revenue FROM fixture_metrics GROUP BY country",
                "parameters": {},
                "limits": {"max_rows": 100},
            },
        )
        assert submitted.status_code == 202, submitted.text
        query_id = submitted.json()["query_id"]

        revoked = admin_client.put(
            f"/api/v1/admin/users/{created.json()['id']}/roles", json={"role_ids": []}
        )
        assert revoked.status_code == 200, revoked.text
        detail = analyst_client.get(f"/api/v1/queries/{query_id}")
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "CANCELLED"


def test_permission_request_queue_is_admin_only_and_stays_mock(admin_client, app, viewer):
    """The administrator queue shows real request states; mock approval never grants.

    Spec 24 requires the Permissions screen to keep "real grant" and "mock state"
    visibly apart, so the test checks both halves: the queue is readable by an
    administrator only, and marking a request does not create a permission.
    """
    from fastapi.testclient import TestClient

    from tests.helpers import setup_datasource_and_grants

    source_url = os.environ.get("AIND_TEST_SOURCE_URL")
    if not source_url:
        pytest.skip("AIND_TEST_SOURCE_URL not set")
    setup = setup_datasource_and_grants(admin_client, source_url, grant_roles=["admin"])
    dataset_id = setup.dataset_ids["fixture_metrics"]

    # A second client: logging the viewer in on the shared one would replace the
    # administrator session these assertions need.
    with TestClient(app, raise_server_exceptions=False) as viewer_client:
        viewer_csrf = login(viewer_client, viewer["username"], viewer["password"])
        return _assert_permission_request_queue(
            admin_client, viewer_client, viewer_csrf, setup, dataset_id
        )


def _assert_permission_request_queue(admin_client, client, viewer_csrf, setup, dataset_id):
    created = client.post(
        "/api/v1/permission-requests",
        json={"dataset_id": dataset_id, "reason": "need revenue for the weekly report"},
        headers={"X-CSRF-Token": viewer_csrf},
    )
    assert created.status_code == 201, created.text
    request_id = created.json()["id"]
    assert created.json()["status"] == "REQUESTED"

    forbidden = client.get("/api/v1/admin/permission-requests", headers={"X-CSRF-Token": viewer_csrf})
    assert forbidden.status_code == 403

    queue = admin_client.get("/api/v1/admin/permission-requests")
    assert queue.status_code == 200, queue.text
    items = {item["id"]: item for item in queue.json()["items"]}
    assert request_id in items
    assert items[request_id]["reason"].startswith("need revenue")
    assert items[request_id]["user_id"]

    empty = admin_client.get("/api/v1/admin/permission-requests", params={"status": "REJECTED"})
    assert empty.status_code == 200
    assert empty.json()["items"] == []
    invalid = admin_client.get("/api/v1/admin/permission-requests", params={"status": "MAYBE"})
    assert invalid.status_code == 422

    approved = admin_client.post(f"/api/v1/admin/permission-requests/{request_id}/mock-approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "MOCK_APPROVED"

    # The mock state is a label, not a grant: the viewer still cannot query.
    grants = admin_client.get("/api/v1/admin/grants").json()["items"]
    role_id = next(
        role["id"] for role in admin_client.get("/api/v1/admin/roles").json() if role["name"] == "viewer"
    )
    assert not [
        grant
        for grant in grants
        if grant["dataset_id"] == dataset_id and grant["role_id"] == role_id
    ], "mock approval must not create a real grant"
    denied = client.post(
        "/api/v1/queries",
        json={"datasource_id": setup.datasource_id, "sql": "SELECT COUNT(*) AS n FROM fixture_metrics"},
        headers={"X-CSRF-Token": viewer_csrf},
    )
    assert denied.status_code == 403, denied.text
