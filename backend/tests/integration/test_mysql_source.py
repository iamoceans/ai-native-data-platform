"""Integration: MySQL provider and multi-source loop (A01, A03, A11 subset).

Runs against the seeded source MySQL (docker full profile). Skipped when
AIND_TEST_MYSQL_URL is not set - skipped is never reported as passed.
"""

from __future__ import annotations

import os
import threading
import time

import pymysql
import pytest

from tests.helpers import (
    connect_mysql,
    drain_worker,
    seeded_engine,
    wait_for_query,
)

pytestmark = pytest.mark.integration

MYSQL_URL_ENV = "AIND_TEST_MYSQL_URL"


@pytest.fixture(scope="module")
def mysql_url() -> str:
    url = os.environ.get(MYSQL_URL_ENV)
    if not url:
        pytest.skip(f"{MYSQL_URL_ENV} not set")
    return url


@pytest.fixture()
def seed_mysql(mysql_url):
    from tests.helpers import parse_url, run_seed_script

    parts = parse_url(mysql_url)
    # Seed with the admin account from .env (the URL points at the admin user).
    run_seed_script(mysql_url=mysql_url, doris_url=None)
    return parts


@pytest.fixture()
def source(admin_client, seed_mysql):
    return seeded_engine(admin_client, kind="mysql", url=os.environ[MYSQL_URL_ENV], schema=seed_mysql["database"])


def test_connection_reports_health_and_capabilities(admin_client, seed_mysql):
    parts = seed_mysql
    created = admin_client.post(
        "/api/v1/datasources",
        json={
            "name": "mysql-health",
            "kind": "mysql",
            "connection_config": {
                "host": parts["host"],
                "port": parts["port"],
                "database": parts["database"],
                "connect_timeout_seconds": 5,
            },
            "secret_ref": "source-mysql",
        },
    )
    assert created.status_code == 201, created.text
    datasource_id = created.json()["id"]
    tested = admin_client.post(f"/api/v1/datasources/{datasource_id}/test")
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["status"] == "HEALTHY", body
    assert body["server_version"].startswith("8.4"), body
    assert body["capabilities"]["dialect"] == "mysql"
    assert body["capabilities"]["cancel"] is True


def test_schema_and_query_recompute_numbers(admin_client, source, mysql_url):
    schema = admin_client.get(f"/api/v1/datasets/{source.dataset_ids['app_release_config']}/schema")
    assert schema.status_code == 200, schema.text
    names = [column["name"] for column in schema.json()["columns"]]
    assert names == ["platform", "app_version", "release_at", "rollout_note"]

    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": (
                "SELECT platform, app_version, release_at FROM app_release_config "
                "ORDER BY platform, app_version"
            ),
        },
    )
    assert submitted.status_code == 202, submitted.text
    query_id = submitted.json()["query_id"]
    drain_worker()
    detail = wait_for_query(admin_client, query_id)
    assert detail["status"] == "SUCCEEDED", detail

    results = admin_client.get(f"/api/v1/queries/{query_id}/results").json()
    rows = results["result"]["rows"]
    assert len(rows) == 6

    with connect_mysql(mysql_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT platform, app_version, release_at FROM app_release_config "
                "ORDER BY platform, app_version"
            )
            expected = cur.fetchall()
    assert [(r[0], r[1]) for r in rows] == [(str(r[0]), str(r[1])) for r in expected]


def test_parameter_binding_and_percent_literal(admin_client, source, mysql_url):
    """A literal % must survive parameter binding on MySQL (pyformat)."""
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": (
                "SELECT COUNT(*) AS n FROM app_release_config "
                "WHERE platform = :platform AND release_at IS NOT NULL"
            ),
            "parameters": {"platform": "ios"},
        },
    )
    assert submitted.status_code == 202, submitted.text
    query_id = submitted.json()["query_id"]
    drain_worker()
    detail = wait_for_query(admin_client, query_id)
    assert detail["status"] == "SUCCEEDED", detail
    results = admin_client.get(f"/api/v1/queries/{query_id}/results").json()
    assert results["result"]["rows"][0][0] == 3

    with connect_mysql(mysql_url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DATE_FORMAT(release_at, '%Y-%m-%d') FROM app_release_config LIMIT 1")
            (formatted,) = cur.fetchone()
    assert formatted


def test_cancel_running_mysql_query(admin_client, source):
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": (
                "SELECT COUNT(*) AS n FROM m2_heavy a JOIN m2_heavy b ON a.bucket = b.bucket "
                "JOIN m2_heavy c ON b.bucket = c.bucket"
            ),
            "limits": {"timeout_seconds": 120},
        },
    )
    assert submitted.status_code == 202, submitted.text
    query_id = submitted.json()["query_id"]

    worker = threading.Thread(target=drain_worker, kwargs={"max_cycles": 1}, daemon=True)
    worker.start()

    deadline = time.monotonic() + 20
    detail = {}
    while time.monotonic() < deadline:
        detail = admin_client.get(f"/api/v1/queries/{query_id}").json()
        if detail["status"] == "RUNNING":
            break
        time.sleep(0.2)
    assert detail.get("status") == "RUNNING", detail

    cancel = admin_client.post(f"/api/v1/queries/{query_id}/cancel")
    assert cancel.status_code in (200, 202), cancel.text
    worker.join(timeout=60)
    assert not worker.is_alive(), "worker did not stop after cancel"

    final = wait_for_query(admin_client, query_id, timeout=30)
    assert final["status"] == "CANCELLED", final
    assert final["error"]["code"] == "QUERY_CANCELLED"


def test_timeout_enforced_by_max_execution_time(admin_client, source):
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": (
                "SELECT COUNT(*) AS n FROM m2_heavy a JOIN m2_heavy b ON a.bucket = b.bucket "
                "JOIN m2_heavy c ON b.bucket = c.bucket"
            ),
            "limits": {"timeout_seconds": 2},
        },
    )
    assert submitted.status_code == 202, submitted.text
    query_id = submitted.json()["query_id"]
    drain_worker(max_cycles=1)
    final = wait_for_query(admin_client, query_id, timeout=60)
    assert final["status"] == "TIMED_OUT", final
    assert final["error"]["code"] == "QUERY_TIMEOUT"


def test_reader_account_blocks_writes(seed_mysql):
    """A05 for MySQL: even bypassing the AST layer the database refuses writes."""
    import json
    from pathlib import Path

    credentials = json.loads(
        (Path(__file__).resolve().parents[3] / "infra" / "local-secrets" / "source-mysql.json").read_text(
            encoding="utf-8"
        )
    )
    parts = seed_mysql
    conn = pymysql.connect(
        host=parts["host"],
        port=parts["port"],
        user=credentials["username"],
        password=credentials["password"],
        database=parts["database"],
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            with pytest.raises(pymysql.Error) as create_error:
                cur.execute("CREATE TABLE should_not_exist (x int)")
            assert create_error.value.args[0] == 1792  # read-only transaction
            with pytest.raises(pymysql.Error) as insert_error:
                cur.execute(
                    "INSERT INTO app_release_config VALUES ('ios','9.9.9',NOW(),NULL)"
                )
            assert insert_error.value.args[0] == 1142  # command denied
    finally:
        conn.close()


def test_audit_records_mysql_execution(admin_client, source):
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": "SELECT COUNT(*) AS n FROM app_release_config",
        },
    )
    assert submitted.status_code == 202, submitted.text
    query_id = submitted.json()["query_id"]
    drain_worker()
    assert wait_for_query(admin_client, query_id)["status"] == "SUCCEEDED"

    audits = admin_client.get("/api/v1/admin/audits", params={"action": "query.execute"})
    assert audits.status_code == 200
    assert any(
        item["outcome"] == "SUCCEEDED" and item["resource_id"] == query_id
        for item in audits.json()["items"]
    )
