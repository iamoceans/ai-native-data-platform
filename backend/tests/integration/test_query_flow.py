"""Integration: the full PostgreSQL query loop (M1 gate).

Covers: submit -> queue -> worker executes on real PostgreSQL -> result files
-> API rows; independent recomputation of every number; pagination;
truncation; idempotency; parameter binding; permission boundaries; SSRF and
unsupported-kind rejections.
"""

from __future__ import annotations

import os

import psycopg
import pytest

from tests.conftest import login
from tests.helpers import (
    drain_worker,
    setup_datasource_and_grants,
    source_dsn,
    wait_for_query,
)

pytestmark = pytest.mark.integration


@pytest.fixture()
def source_url() -> str:
    url = os.environ.get("AIND_TEST_SOURCE_URL")
    if not url:
        pytest.skip("AIND_TEST_SOURCE_URL not set")
    return url


@pytest.fixture()
def source(admin_client, source_url):
    return setup_datasource_and_grants(admin_client, source_url)


def submit(admin_client, datasource_id, sql, **kwargs):
    payload = {"datasource_id": datasource_id, "sql": sql, **kwargs}
    response = admin_client.post("/api/v1/queries", json=payload)
    return response


def test_full_query_loop_recomputes_numbers(admin_client, source, source_url):
    sql = (
        "SELECT country, SUM(revenue_usd) AS revenue, SUM(impressions) AS impressions "
        "FROM fixture_metrics GROUP BY country ORDER BY country"
    )
    response = submit(admin_client, source.datasource_id, sql)
    assert response.status_code == 202, response.text
    query_id = response.json()["query_id"]
    assert response.headers["Location"] == f"/api/v1/queries/{query_id}"

    processed = drain_worker()
    assert processed == 1

    detail = wait_for_query(admin_client, query_id)
    assert detail["status"] == "SUCCEEDED", detail

    results = admin_client.get(f"/api/v1/queries/{query_id}/results")
    assert results.status_code == 200, results.text
    body = results.json()
    assert body["result"]["truncated"] is False
    rows = body["result"]["rows"]
    assert len(rows) == 2

    # Independent recomputation from the source database with a different path.
    with psycopg.connect(source_dsn(source_url)) as conn:
        expected = conn.execute(
            """
            SELECT country, SUM(revenue_usd), SUM(impressions)
            FROM public.fixture_metrics GROUP BY country ORDER BY country
            """
        ).fetchall()
    with psycopg.connect(source_dsn(source_url)) as conn:
        source_reader = conn.execute("SELECT current_user").fetchone()[0]
    assert source_reader == "source_admin"

    assert len(expected) == len(rows)
    for api_row, db_row in zip(rows, expected):
        assert api_row[0] == db_row[0]
        assert api_row[1] == str(db_row[1])  # numeric travels as string
        assert int(api_row[2]) == db_row[2]

    # Column ids are stable and include the declared types.
    columns = body["result"]["columns"]
    by_name = {column["name"]: column for column in columns}
    assert by_name["revenue"]["type"].startswith("numeric")
    assert [column["id"] for column in columns][0] == "c0"

    # Audit trail exists for the execution.
    audits = admin_client.get("/api/v1/admin/audits", params={"action": "query.execute"})
    assert any(item["resource_id"] == query_id for item in audits.json()["items"])


def test_parameter_binding_is_not_string_interpolation(admin_client, source):
    sql = (
        "SELECT COUNT(*) AS n FROM fixture_metrics "
        "WHERE country = :country AND dt >= :start"
    )
    injection = "US'; DROP TABLE fixture_metrics; --"
    response = submit(
        admin_client,
        source.datasource_id,
        sql,
        parameters={"country": injection, "start": "2026-09-01"},
    )
    assert response.status_code == 202, response.text
    query_id = response.json()["query_id"]
    drain_worker()
    detail = wait_for_query(admin_client, query_id)
    assert detail["status"] == "SUCCEEDED", detail
    result = admin_client.get(f"/api/v1/queries/{query_id}/results").json()
    assert result["result"]["rows"][0][0] == 0  # no row matches the injected string


def test_missing_parameter_fails_validation(admin_client, source):
    response = submit(
        admin_client,
        source.datasource_id,
        "SELECT COUNT(*) FROM fixture_metrics WHERE country = :country",
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_pagination_walks_the_whole_result(admin_client, source):
    sql = "SELECT dt, country, revenue_usd FROM fixture_metrics ORDER BY dt, country, revenue_usd"
    response = submit(admin_client, source.datasource_id, sql, limits={"max_rows": 2500})
    query_id = response.json()["query_id"]
    drain_worker()
    assert wait_for_query(admin_client, query_id)["status"] == "SUCCEEDED"

    collected: list[list] = []
    cursor = None
    seen_pages = 0
    while True:
        params = {"limit": 100}
        if cursor:
            params["cursor"] = cursor
        page = admin_client.get(f"/api/v1/queries/{query_id}/results", params=params)
        assert page.status_code == 200, page.text
        body = page.json()
        collected.extend(body["result"]["rows"])
        seen_pages += 1
        cursor = body["result"]["next_cursor"]
        if not cursor:
            break
        assert seen_pages < 40, "pagination did not terminate"
    assert len(collected) == 2500
    assert seen_pages == 25
    assert body["result"]["row_count"] == 2500


def test_truncation_is_detected_and_flagged(admin_client, source):
    sql = "SELECT dt, country, revenue_usd FROM fixture_metrics ORDER BY dt"
    response = submit(admin_client, source.datasource_id, sql, limits={"max_rows": 100})
    query_id = response.json()["query_id"]
    drain_worker()
    detail = wait_for_query(admin_client, query_id)
    assert detail["status"] == "SUCCEEDED"
    assert detail["result"]["truncated"] is True

    page = admin_client.get(f"/api/v1/queries/{query_id}/results").json()
    assert len(page["result"]["rows"]) == 100
    assert any("RESULT_TRUNCATED" in warning for warning in page["warnings"])


def test_idempotency_key_replays_and_conflicts(admin_client, source):
    sql = "SELECT COUNT(*) AS n FROM fixture_metrics"
    body = {"datasource_id": source.datasource_id, "sql": sql}
    headers = {"Idempotency-Key": "fixed-key-1"}
    first = admin_client.post("/api/v1/queries", json=body, headers=headers)
    second = admin_client.post("/api/v1/queries", json=body, headers=headers)
    assert first.status_code == 202 and second.status_code == 202
    assert first.json()["query_id"] == second.json()["query_id"]
    assert second.headers.get("Idempotency-Replayed") == "true"

    conflict = admin_client.post(
        "/api/v1/queries",
        json={**body, "sql": "SELECT 1"},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
    drain_worker()


def test_query_requires_dataset_permission(admin_client, client, viewer, source_url):
    # A second datasource with grants only for admin: viewer is denied.
    source = setup_datasource_and_grants(admin_client, source_url, grant_roles=["admin"])
    csrf = login(client, viewer["username"], viewer["password"])
    response = client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": "SELECT COUNT(*) FROM fixture_metrics",
        },
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "PERMISSION_DENIED"

    # Catalog search hides unauthorized datasets from this user.
    datasets = client.get("/api/v1/datasets").json()
    assert datasets["items"] == []


def test_cross_user_query_is_invisible(admin_client, client, viewer, source):
    created = submit(admin_client, source.datasource_id, "SELECT 1 FROM fixture_metrics")
    query_id = created.json()["query_id"]
    drain_worker()
    csrf = login(client, viewer["username"], viewer["password"])
    assert client.get(f"/api/v1/queries/{query_id}").status_code == 404
    cancel = client.post(
        f"/api/v1/queries/{query_id}/cancel", headers={"X-CSRF-Token": csrf}
    )
    assert cancel.status_code == 404


def test_invalid_kind_and_ssrf_datasources_are_rejected(admin_client):
    # Unsupported engine kind is rejected by the request schema (M2 supports
    # postgres/mysql/doris only).
    unsupported = admin_client.post(
        "/api/v1/datasources",
        json={
            "name": "spark-later",
            "kind": "spark",
            "connection_config": {
                "host": "127.0.0.1",
                "port": 10000,
                "database": "x",
                "connect_timeout_seconds": 5,
            },
            "secret_ref": "source-postgres",
        },
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "VALIDATION_ERROR"

    # The network policy applies to every engine kind.
    ssrf = admin_client.post(
        "/api/v1/datasources",
        json={
            "name": "metadata-probe",
            "kind": "postgres",
            "connection_config": {
                "host": "169.254.169.254",
                "port": 5432,
                "database": "x",
                "ssl_mode": "disable",
                "connect_timeout_seconds": 5,
            },
            "secret_ref": "source-postgres",
        },
    )
    assert ssrf.status_code == 403

    ssrf_mysql = admin_client.post(
        "/api/v1/datasources",
        json={
            "name": "metadata-probe-mysql",
            "kind": "mysql",
            "connection_config": {
                "host": "169.254.169.254",
                "port": 3306,
                "database": "x",
                "connect_timeout_seconds": 5,
            },
            "secret_ref": "source-mysql",
        },
    )
    assert ssrf_mysql.status_code == 403

    unlisted = admin_client.post(
        "/api/v1/datasources",
        json={
            "name": "somewhere-else",
            "kind": "postgres",
            "connection_config": {
                "host": "10.99.99.99",
                "port": 5432,
                "database": "x",
                "ssl_mode": "disable",
                "connect_timeout_seconds": 5,
            },
            "secret_ref": "source-postgres",
        },
    )
    assert unlisted.status_code == 403

    missing_secret = admin_client.post(
        "/api/v1/datasources",
        json={
            "name": "no-secret",
            "kind": "postgres",
            "connection_config": {
                "host": "127.0.0.1",
                "port": 5433,
                "database": "x",
                "ssl_mode": "disable",
                "connect_timeout_seconds": 5,
            },
            "secret_ref": "does-not-exist",
        },
    )
    assert missing_secret.status_code == 503


def test_dataset_schema_endpoint_matches_source(admin_client, source, source_url):
    dataset_id = source.dataset_ids["fixture_metrics"]
    schema = admin_client.get(f"/api/v1/datasets/{dataset_id}/schema")
    assert schema.status_code == 200, schema.text
    payload = schema.json()
    names = [column["name"] for column in payload["columns"]]
    assert names == ["dt", "country", "platform", "note", "revenue_usd", "impressions"]
    with psycopg.connect(source_dsn(source_url)) as conn:
        db_columns = conn.execute(
            """
            SELECT column_name FROM information_schema.columns
            WHERE table_schema='public' AND table_name='fixture_metrics'
            ORDER BY ordinal_position
            """
        ).fetchall()
    assert names == [row[0] for row in db_columns]
    assert len(payload["schema_hash"]) == 64
