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
