"""Integration: the attack corpus through the API on all three engines (A03).

Each forbidden statement must be rejected by the gateway before any job is
created, and the platform must contain no query row afterwards. This runs the
same corpus against PostgreSQL, MySQL and Doris datasources.
"""

from __future__ import annotations

import os

import pytest

from tests.helpers import seeded_engine, setup_datasource_and_grants

pytestmark = pytest.mark.integration

# (sql, expected error code) - identical across engines because the gateway
# rejects these shapes before any engine-specific work happens.
ATTACKS = [
    ("DROP TABLE any_table", "SQL_FORBIDDEN"),
    ("DELETE FROM any_table", "SQL_FORBIDDEN"),
    ("UPDATE any_table SET x = 1", "SQL_FORBIDDEN"),
    ("INSERT INTO any_table VALUES (1)", "SQL_FORBIDDEN"),
    ("SELECT 1; DELETE FROM any_table", "SQL_FORBIDDEN"),
    ("WITH w AS (DELETE FROM any_table RETURNING *) SELECT * FROM w", "SQL_FORBIDDEN"),
    ("SELECT * FROM any_table FOR UPDATE", "SQL_FORBIDDEN"),
    ("SELECT /*! SLEEP(1) */ 1", "SQL_FORBIDDEN"),
    ("SELECT SLEEP(10)", "SQL_FORBIDDEN"),
    ("SELECT pg_sleep(10)", "SQL_FORBIDDEN"),
    ("SHOW PROCESSLIST", "SQL_FORBIDDEN"),
    ("SET @x = 1", "SQL_FORBIDDEN"),
    ("SELECT * FROM any_table INTO OUTFILE '/tmp/leak'", "SQL_SYNTAX_ERROR"),
    ("SELECT * FROM information_schema.tables", "DATASET_NOT_REGISTERED"),
]


def assert_corpus_rejected(client, datasource_id: str) -> None:
    for sql, code in ATTACKS:
        response = client.post(
            "/api/v1/queries",
            json={"datasource_id": datasource_id, "sql": sql},
        )
        assert response.status_code in (403, 404, 422), (sql, response.status_code, response.text)
        assert response.json()["error"]["code"] == code, (sql, response.json())
    # Nothing was enqueued: every rejection happened before job creation.
    history = client.get("/api/v1/queries").json()
    assert history["items"] == []


@pytest.fixture()
def pg_source(admin_client):
    url = os.environ.get("AIND_TEST_SOURCE_URL")
    if not url:
        pytest.skip("AIND_TEST_SOURCE_URL not set")
    return setup_datasource_and_grants(admin_client, url, grant_roles=["admin"])


@pytest.fixture()
def mysql_source(admin_client):
    url = os.environ.get("AIND_TEST_MYSQL_URL")
    if not url:
        pytest.skip("AIND_TEST_MYSQL_URL not set")
    from tests.helpers import parse_url, run_seed_script

    run_seed_script(mysql_url=url, doris_url=None)
    return seeded_engine(admin_client, kind="mysql", url=url, schema=parse_url(url)["database"])


@pytest.fixture()
def doris_source(admin_client):
    url = os.environ.get("AIND_TEST_DORIS_URL")
    if not url:
        pytest.skip("AIND_TEST_DORIS_URL not set")
    from tests.helpers import run_seed_script

    run_seed_script(mysql_url=None, doris_url=url)
    return seeded_engine(admin_client, kind="doris", url=url, schema="demo")


def test_attack_corpus_rejected_postgres(admin_client, pg_source):
    assert_corpus_rejected(admin_client, pg_source.datasource_id)


def test_attack_corpus_rejected_mysql(admin_client, mysql_source):
    assert_corpus_rejected(admin_client, mysql_source.datasource_id)


def test_attack_corpus_rejected_doris(admin_client, doris_source):
    assert_corpus_rejected(admin_client, doris_source.datasource_id)
