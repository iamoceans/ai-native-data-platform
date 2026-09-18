"""Integration: Doris provider and multi-source loop (A01, A03, A11 subset).

Runs against the seeded Doris FE (docker full profile). Skipped when
AIND_TEST_DORIS_URL is not set.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pymysql
import pytest

from tests.helpers import connect_mysql, drain_worker, parse_url, run_seed_script, seeded_engine, wait_for_query

pytestmark = pytest.mark.integration

DORIS_URL_ENV = "AIND_TEST_DORIS_URL"

# A JOIN (not CROSS JOIN) keeps the statement inside the gateway allowlist while
# still producing a multi-billion row intermediate result on the BE.
HEAVY_DORIS_SQL = (
    "SELECT COUNT(*) AS n FROM demo.ads_revenue_daily a "
    "JOIN demo.ads_revenue_daily b ON a.country = b.country "
    "JOIN demo.ads_revenue_daily c ON b.country = c.country "
    "JOIN demo.ads_revenue_daily d ON c.country = d.country"
)


@pytest.fixture(scope="module")
def doris_url() -> str:
    url = os.environ.get(DORIS_URL_ENV)
    if not url:
        pytest.skip(f"{DORIS_URL_ENV} not set")
    return url


@pytest.fixture()
def seed_doris(doris_url):
    run_seed_script(mysql_url=None, doris_url=doris_url)
    return parse_url(doris_url)


@pytest.fixture()
def source(admin_client, seed_doris):
    return seeded_engine(
        admin_client, kind="doris", url=os.environ[DORIS_URL_ENV], schema="demo"
    )


def test_connection_reports_doris_capabilities(admin_client, seed_doris):
    parts = seed_doris
    created = admin_client.post(
        "/api/v1/datasources",
        json={
            "name": "doris-health",
            "kind": "doris",
            "connection_config": {
                "host": parts["host"],
                "port": parts["port"],
                "database": "demo",
                "connect_timeout_seconds": 5,
            },
            "secret_ref": "doris",
        },
    )
    assert created.status_code == 201, created.text
    datasource_id = created.json()["id"]
    tested = admin_client.post(f"/api/v1/datasources/{datasource_id}/test")
    assert tested.status_code == 200, tested.text
    body = tested.json()
    assert body["status"] == "HEALTHY", body
    capabilities = body["capabilities"]
    assert capabilities["dialect"] == "doris"
    assert capabilities["cancel"] is True
    assert capabilities["server_timeout"] is True
    # Doris does not emulate PostgreSQL transaction semantics (spec 9.2).
    assert capabilities["transactional_read_only"] is False


def test_catalog_refresh_registers_doris_tables(admin_client, source):
    expected = {"ads_revenue_daily", "iap_revenue_daily", "user_daily", "m2_types_probe"}
    assert expected <= set(source.dataset_ids), source.dataset_ids


def test_schema_reports_doris_column_types(admin_client, source):
    schema = admin_client.get(f"/api/v1/datasets/{source.dataset_ids['ads_revenue_daily']}/schema")
    assert schema.status_code == 200, schema.text
    columns = {column["name"]: column["type"] for column in schema.json()["columns"]}
    assert columns["impressions"] == "bigint(20)"
    assert columns["revenue_usd"] == "decimalv3(20, 6)"
    assert columns["dt"] == "date"


def test_query_recomputes_revenue_from_doris(admin_client, source, doris_url):
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            # Scoped to the M2 seed countries so the check is data-set independent
            # (the M4 generator loads many more countries).
            "sql": (
                "SELECT country, SUM(revenue_usd) AS revenue, SUM(impressions) AS impressions "
                "FROM demo.ads_revenue_daily WHERE country IN ('US', 'DE', 'JP') "
                "GROUP BY country ORDER BY country"
            ),
        },
    )
    assert submitted.status_code == 202, submitted.text
    query_id = submitted.json()["query_id"]
    drain_worker()
    detail = wait_for_query(admin_client, query_id)
    assert detail["status"] == "SUCCEEDED", detail
    # The validated SQL must be fully qualified with the internal catalog.
    assert "internal.demo.ads_revenue_daily" in detail["validated_sql"]

    results = admin_client.get(f"/api/v1/queries/{query_id}/results").json()
    rows = results["result"]["rows"]
    assert len(rows) == 3

    with connect_mysql(doris_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT country, SUM(revenue_usd), SUM(impressions) "
                "FROM internal.demo.ads_revenue_daily WHERE country IN ('US', 'DE', 'JP') "
                "GROUP BY country ORDER BY country"
            )
            expected = cur.fetchall()
    for api_row, db_row in zip(rows, expected):
        assert api_row[0] == db_row[0]
        # DECIMAL travels as a string and must match exactly.
        assert api_row[1] == str(db_row[1])
        assert int(api_row[2]) == int(db_row[2])


def test_doris_type_mapping_largeint_and_decimal(admin_client, source):
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": "SELECT id, amount, created, label, flag, dt FROM demo.m2_types_probe ORDER BY id DESC",
        },
    )
    assert submitted.status_code == 202, submitted.text
    query_id = submitted.json()["query_id"]
    drain_worker()
    detail = wait_for_query(admin_client, query_id)
    assert detail["status"] == "SUCCEEDED", detail

    body = admin_client.get(f"/api/v1/queries/{query_id}/results").json()
    columns = {column["name"]: column for column in body["result"]["columns"]}
    assert columns["amount"]["type"].startswith("decimal")
    rows = body["result"]["rows"]
    large = next(row for row in rows if row[3] == "large")
    # LARGEINT beyond the JS safe range must stay a string (spec 9.2).
    assert large[0] == "9223372036854775808"
    assert isinstance(large[0], str)
    assert large[1] == "1234567890123.456789"
    assert large[4] in (1, True, "1")


def test_cancel_running_doris_query(admin_client, source):
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": HEAVY_DORIS_SQL,
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


def test_timeout_enforced_by_query_timeout(admin_client, source):
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": HEAVY_DORIS_SQL,
            "limits": {"timeout_seconds": 2},
        },
    )
    assert submitted.status_code == 202, submitted.text
    query_id = submitted.json()["query_id"]
    drain_worker(max_cycles=1)
    final = wait_for_query(admin_client, query_id, timeout=90)
    assert final["status"] == "TIMED_OUT", final
    assert final["error"]["code"] == "QUERY_TIMEOUT"


def test_explain_runs_through_the_same_path(admin_client, source):
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": "SELECT COUNT(*) AS n FROM demo.ads_revenue_daily",
            "purpose": "explain",
        },
    )
    assert submitted.status_code == 202, submitted.text
    query_id = submitted.json()["query_id"]
    drain_worker()
    detail = wait_for_query(admin_client, query_id)
    assert detail["status"] == "SUCCEEDED", detail
    body = admin_client.get(f"/api/v1/queries/{query_id}/results").json()
    assert body["result"]["rows"], body
    plan_text = json.dumps(body["result"]["rows"])
    assert "PLAN FRAGMENT" in plan_text


def test_reader_account_blocks_write_and_ddl(seed_doris):
    credentials = json.loads(
        (Path(__file__).resolve().parents[3] / "infra" / "local-secrets" / "doris.json").read_text(
            encoding="utf-8"
        )
    )
    parts = seed_doris
    conn = pymysql.connect(
        host=parts["host"],
        port=parts["port"],
        user=credentials["username"],
        password=credentials["password"],
        database="demo",
        autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            with pytest.raises(pymysql.Error) as insert_error:
                cur.execute("INSERT INTO demo.m2_types_probe VALUES (9,1.0,NOW(),'x',false,'2026-01-01')")
            assert "denied" in str(insert_error.value.args[1]).lower()
            with pytest.raises(pymysql.Error) as create_error:
                cur.execute("CREATE TABLE demo.should_not_exist (x int)")
            assert "denied" in str(create_error.value.args[1]).lower()
    finally:
        conn.close()


def test_external_catalog_is_rejected(admin_client, source):
    response = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": source.datasource_id,
            "sql": "SELECT * FROM hive.demo.ads_revenue_daily",
        },
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "DATASET_NOT_REGISTERED"
