"""M4 acceptance through real SQL: canonical_67, drivers, cross-source join.

- A06: the canonical 10,000 -> 9,000 / -670 / 0.67 sample is loaded into the
  source PostgreSQL and queried **through the Query Gateway**; the
  deterministic kernel then has to reproduce the specification's numbers.
- A07: on the loaded Doris demo data, impression and eCPM effects must sum to
  the revenue delta (tolerance 1e-6) for the scenario target cell.
- A14: the whitelisted cross-source join (Doris cohorts + PostgreSQL campaign
  configuration) must preserve amounts and refuse duplicate configuration keys.

Doris-backed checks skip when the demo tables are absent; nothing is marked as
passed without executing it.
"""

from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from app.analysis.compare import compare_totals
from app.analysis.contribution import decompose_contribution
from app.analysis.drivers import decompose_revenue
from app.analysis.join import join_results
from app.analysis.relations import get_relation
from app.analysis.types import AnalysisError
from tests.helpers import (
    SourceSetup,
    drain_worker,
    parse_url,
    seeded_engine,
    setup_datasource_and_grants,
    source_dsn,
    wait_for_query,
)

pytestmark = pytest.mark.integration

METADATA_DIR = Path(__file__).resolve().parents[3] / "metadata"
ROOT_DATASET = "canonical_ad_revenue"

CANONICAL_DDL = """
CREATE TABLE IF NOT EXISTS public.canonical_ad_revenue (
  dt date NOT NULL,
  country text NOT NULL,
  platform text NOT NULL,
  app_version text NOT NULL,
  ad_network text NOT NULL,
  impressions bigint NOT NULL,
  revenue_usd numeric(20,6) NOT NULL
)
"""

CANONICAL_ROWS = [
    ("2026-09-11", "US", "android", "4.2.1", "AppLovin", 100000, "2500"),
    ("2026-09-12", "US", "android", "4.2.1", "AppLovin", 100000, "1830"),
    ("2026-09-11", "DE", "android", "4.2.1", "AdMob", 200000, "4000"),
    ("2026-09-12", "DE", "android", "4.2.1", "AdMob", 200000, "3700"),
    ("2026-09-11", "JP", "android", "4.2.1", "Unity", 200000, "3500"),
    ("2026-09-12", "JP", "android", "4.2.1", "Unity", 200000, "3470"),
]

CANONICAL_SQL = (
    "SELECT country, ad_network, SUM(revenue_usd) AS revenue_usd, "
    "SUM(impressions) AS impressions "
    "FROM public.canonical_ad_revenue WHERE dt >= :start AND dt < :end "
    "GROUP BY country, ad_network ORDER BY country, ad_network"
)


def _seed_canonical(source_url: str) -> None:
    with psycopg.connect(source_dsn(source_url)) as conn:
        conn.execute(CANONICAL_DDL)
        conn.execute("DELETE FROM public.canonical_ad_revenue")
        with conn.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO public.canonical_ad_revenue "
                "(dt, country, platform, app_version, ad_network, impressions, revenue_usd) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                CANONICAL_ROWS,
            )
        conn.commit()


def _run_sql(admin_client, setup: SourceSetup, sql: str, parameters: dict) -> list[dict]:
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": setup.datasource_id,
            "sql": sql,
            "parameters": parameters,
            # The 24-country x 12-network partition exceeds the 1,000-row
            # default; decompositions must see the complete partition.
            "limits": {"max_rows": 10_000},
        },
    )
    assert submitted.status_code == 202, submitted.text
    query_id = submitted.json()["query_id"]
    drain_worker()
    detail = wait_for_query(admin_client, query_id)
    assert detail["status"] == "SUCCEEDED", detail

    # The results endpoint paginates (default 100 rows); decompositions need the
    # complete partition, so follow the cursor.
    columns: list[str] = []
    rows: list[dict] = []
    cursor = None
    while True:
        params = {"limit": 500}
        if cursor:
            params["cursor"] = cursor
        body = admin_client.get(f"/api/v1/queries/{query_id}/results", params=params).json()
        if not columns:
            columns = [column["name"] for column in body["result"]["columns"]]
        rows.extend(dict(zip(columns, row)) for row in body["result"]["rows"])
        cursor = body["result"].get("next_cursor")
        if not cursor:
            break
    assert not detail.get("truncated", False), "the test query must not be truncated"
    return rows


def test_a06_canonical_67_through_real_sql(admin_client):
    source_url = os.environ.get("AIND_TEST_SOURCE_URL")
    if not source_url:
        pytest.skip("AIND_TEST_SOURCE_URL not set")
    _seed_canonical(source_url)
    setup = setup_datasource_and_grants(admin_client, source_url)
    assert ROOT_DATASET in setup.dataset_ids, setup.dataset_ids

    baseline_rows = _run_sql(
        admin_client,
        setup,
        CANONICAL_SQL,
        {"start": "2026-09-11", "end": "2026-09-12"},
    )
    current_rows = _run_sql(
        admin_client,
        setup,
        CANONICAL_SQL,
        {"start": "2026-09-12", "end": "2026-09-13"},
    )

    baseline_total = sum((Decimal(row["revenue_usd"]) for row in baseline_rows), Decimal(0))
    current_total = sum((Decimal(row["revenue_usd"]) for row in current_rows), Decimal(0))
    comparison = compare_totals(baseline_total, current_total)
    assert comparison.baseline == Decimal("10000")
    assert comparison.current == Decimal("9000")
    assert comparison.delta == Decimal("-1000")
    assert comparison.change_pct == Decimal("-0.1")

    contribution = decompose_contribution(
        baseline_rows,
        current_rows,
        dimension=("country", "ad_network"),
        value_column="revenue_usd",
        support_column="impressions",
        min_support=1000,
        merge_below_support=False,
    )
    target = next(group for group in contribution.groups if group.key == ("US", "AppLovin"))
    assert target.delta == Decimal("-670")
    assert target.net_change_share == Decimal("0.67")
    assert target.contribution_pp == Decimal("-6.7")
    assert contribution.checks["sum_matches_parent"] is True

    def target_cell(rows: list[dict]) -> tuple[Decimal, Decimal]:
        row = next(
            item
            for item in rows
            if item["country"] == "US" and item["ad_network"] == "AppLovin"
        )
        return Decimal(row["impressions"]), Decimal(row["revenue_usd"])

    base_impressions, base_revenue = target_cell(baseline_rows)
    current_impressions, current_revenue = target_cell(current_rows)
    driver = decompose_revenue(
        impressions_baseline=base_impressions,
        impressions_current=current_impressions,
        revenue_baseline=base_revenue,
        revenue_current=current_revenue,
    )
    assert driver.impression_effect == Decimal("0")
    assert driver.ecpm_effect == Decimal("-670")
    assert abs(driver.residual) <= Decimal("0.000001")


def _doris_demo_ready(url: str) -> bool:
    parts = parse_url(url)
    import pymysql

    try:
        connection = pymysql.connect(
            host=parts["host"],
            port=parts["port"],
            user=parts["user"],
            password=parts["password"],
            database="demo",
            connect_timeout=5,
        )
    except Exception:  # noqa: BLE001 - treated as "not ready"
        return False
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM demo.ads_revenue_daily")
            return int(cursor.fetchone()[0]) > 0
    except Exception:  # noqa: BLE001
        return False
    finally:
        connection.close()


@pytest.fixture()
def doris_demo(admin_client):
    url = os.environ.get("AIND_TEST_DORIS_URL")
    if not url:
        pytest.skip("AIND_TEST_DORIS_URL not set")
    if not _doris_demo_ready(url):
        pytest.skip("the M4 demo data is not loaded (run make demo-load)")
    return seeded_engine(admin_client, kind="doris", url=url, schema="demo")


def test_a07_driver_decomposition_on_loaded_demo(admin_client, doris_demo):
    sql = (
        "SELECT country, platform, app_version, ad_network, SUM(revenue_usd) AS revenue_usd, "
        "SUM(impressions) AS impressions FROM demo.ads_revenue_daily "
        "WHERE dt >= :start AND dt < :end "
        "GROUP BY country, platform, app_version, ad_network"
    )
    baseline_rows = _run_sql(
        admin_client, doris_demo, sql, {"start": "2026-09-11", "end": "2026-09-12"}
    )
    current_rows = _run_sql(
        admin_client, doris_demo, sql, {"start": "2026-09-12", "end": "2026-09-13"}
    )
    target_key = ("US", "android", "4.2.1", "AppLovin")
    baseline_by_key = {
        (row["country"], row["platform"], row["app_version"], row["ad_network"]): row
        for row in baseline_rows
    }
    current_by_key = {
        (row["country"], row["platform"], row["app_version"], row["ad_network"]): row
        for row in current_rows
    }
    assert target_key in baseline_by_key, (
        f"target missing from {len(baseline_rows)} baseline rows; "
        f"sample={baseline_rows[0] if baseline_rows else None}"
    )
    assert target_key in current_by_key, f"target missing from {len(current_rows)} current rows"
    baseline = baseline_by_key[target_key]
    current = current_by_key[target_key]
    driver = decompose_revenue(
        impressions_baseline=baseline["impressions"],
        impressions_current=current["impressions"],
        revenue_baseline=baseline["revenue_usd"],
        revenue_current=current["revenue_usd"],
    )
    assert driver.status == "ok"
    assert abs(driver.impression_effect + driver.ecpm_effect - driver.delta) <= Decimal("0.000001")
    # The scenario injects an eCPM drop with stable impressions.
    assert abs(driver.impression_effect) <= max(abs(driver.delta) * Decimal("0.05"), Decimal(1))
    assert abs(driver.ecpm_effect) >= abs(driver.delta) * Decimal("0.9")

    contribution = decompose_contribution(
        baseline_rows,
        current_rows,
        dimension=("country", "platform", "app_version", "ad_network"),
        value_column="revenue_usd",
        support_column="impressions",
        min_support=1000,
        merge_below_support=False,
    )
    assert contribution.checks["sum_matches_parent"] is True
    target_group = next(group for group in contribution.groups if group.key == target_key)
    top_decline = min(contribution.groups, key=lambda group: group.delta)
    assert top_decline.key == target_key, "the injected target must be the largest decline"


def test_a14_cross_source_join_through_real_sql(admin_client):
    pg_url = os.environ.get("AIND_TEST_SOURCE_URL")
    doris_url = os.environ.get("AIND_TEST_DORIS_URL")
    if not pg_url or not doris_url:
        pytest.skip("AIND_TEST_SOURCE_URL and AIND_TEST_DORIS_URL are required")
    if not _doris_demo_ready(doris_url):
        pytest.skip("the M4 demo data is not loaded (run make demo-load)")

    pg_setup = setup_datasource_and_grants(admin_client, pg_url)
    if "campaign_config" not in pg_setup.dataset_ids:
        pytest.skip("public.campaign_config is not present; run make demo-load first")
    doris_setup = seeded_engine(admin_client, kind="doris", url=doris_url, schema="demo")

    # The as-of relation matches on cohort_date against the configuration's
    # [valid_from, valid_to) window, so the left side is grouped per cohort day.
    cohorts = _run_sql(
        admin_client,
        doris_setup,
        (
            "SELECT campaign_id, cohort_date, SUM(revenue_usd) AS revenue_usd, "
            "SUM(cost_usd) AS cost_usd FROM demo.campaign_cohort_daily "
            "GROUP BY campaign_id, cohort_date ORDER BY campaign_id, cohort_date"
        ),
        {},
    )
    config = _run_sql(
        admin_client,
        pg_setup,
        (
            "SELECT campaign_id, valid_from, valid_to, channel FROM public.campaign_config "
            "ORDER BY campaign_id, valid_from"
        ),
        {},
    )
    assert cohorts and config

    relation = get_relation(METADATA_DIR, "campaign_cohort_to_config")
    left = [dict(row) for row in cohorts]
    right = [dict(row) for row in config]
    result = join_results(left, right, relation)
    assert result.stats["matched"] == len(left), result.stats
    assert result.checks["amounts_preserved"] is True
    channels = {row["channel"] for row in result.rows}
    assert channels and None not in channels

    # A duplicate, overlapping configuration must be refused (not double count).
    with psycopg.connect(source_dsn(pg_url)) as conn:
        conn.execute("DELETE FROM public.campaign_config WHERE target = 'dup-probe'")
        conn.execute(
            "INSERT INTO public.campaign_config "
            "(campaign_id, valid_from, valid_to, channel, target, daily_budget_usd) "
            "VALUES ('cmp_001', '2026-09-05', NULL, 'google_uac', 'dup-probe', 1)"
        )
        conn.commit()
    try:
        duplicate_config = _run_sql(
            admin_client,
            pg_setup,
            (
                "SELECT campaign_id, valid_from, valid_to, channel FROM public.campaign_config "
                "ORDER BY campaign_id, valid_from"
            ),
            {},
        )
        with pytest.raises(AnalysisError) as excinfo:
            join_results(left, [dict(row) for row in duplicate_config], relation)
        assert excinfo.value.code == "JOIN_CARDINALITY_VIOLATION"
    finally:
        with psycopg.connect(source_dsn(pg_url)) as conn:
            conn.execute("DELETE FROM public.campaign_config WHERE target = 'dup-probe'")
            conn.commit()
