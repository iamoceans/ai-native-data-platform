"""Integration: DB-level read-only enforcement (A05) and sensitive-data policy."""

from __future__ import annotations

import os

import psycopg
import pytest

from app.config import get_settings
from app.datasource.secrets import SecretResolver
from tests.helpers import setup_datasource_and_grants, source_dsn

pytestmark = pytest.mark.integration


def _source_url() -> str:
    url = os.environ.get("AIND_TEST_SOURCE_URL")
    if not url:
        pytest.skip("AIND_TEST_SOURCE_URL not set")
    return url


def _reader_dsn() -> str:
    """DSN for the read-only source account (source_reader)."""
    credentials = SecretResolver(get_settings().secrets_dir).resolve("source-postgres")
    base = _source_url()
    prefixless = base.split("://", 1)[1]
    _, hostpart = prefixless.split("@", 1)
    hostport, database = hostpart.split("/", 1)
    return (
        f"host={hostport.split(':')[0]} port={hostport.split(':')[1]} dbname={database} "
        f"user={credentials.username} password={credentials.password}"
    )


def test_source_reader_role_rejects_writes():
    """A05: even bypassing the AST layer, the database blocks writes."""
    with psycopg.connect(_reader_dsn()) as conn:
        for statement in (
            "CREATE TABLE public.should_not_exist (x int)",
            "INSERT INTO public.fixture_metrics VALUES ('2026-01-01','US','ios',NULL,1,1)",
            "DROP TABLE public.fixture_metrics",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege) as excinfo:
                conn.execute(statement)
            assert excinfo.value.sqlstate == "42501", statement
            conn.rollback()


def test_provider_read_only_session_rejects_write():
    """The provider's session is read-only (SQLSTATE 25006) on top of the role."""
    with psycopg.connect(source_dsn(_source_url())) as conn:
        conn.read_only = True
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("UPDATE public.fixture_metrics SET note = 'x'")
        conn.rollback()


def test_source_reader_can_read():
    with psycopg.connect(_reader_dsn()) as conn:
        count = conn.execute("SELECT COUNT(*) FROM public.fixture_metrics").fetchone()[0]
    assert count == 2500


def test_sensitive_columns_and_unconfirmed_views_are_not_registered(admin_client):
    """Spec 12.3: sensitive base tables and unconfirmed views must not become
    queryable datasets; a confirmed secure view is registered with its basis."""
    source_url = _source_url()
    with psycopg.connect(source_dsn(source_url)) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS public.users_pii (
              user_id bigint NOT NULL,
              email text NOT NULL,
              country text NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE OR REPLACE VIEW public.public_metrics_view AS
              SELECT country, SUM(revenue_usd) AS revenue
              FROM public.fixture_metrics GROUP BY country
            """
        )
        conn.commit()

    setup = setup_datasource_and_grants(admin_client, source_url)
    refresh = admin_client.post(
        f"/api/v1/admin/datasources/{setup.datasource_id}/catalog-refresh",
        json={"schemas": ["public"]},
    )
    assert refresh.status_code == 200, refresh.text
    body = refresh.json()
    names = [item["object_name"] for item in body["items"]]
    assert "users_pii" not in names, "table with sensitive columns must not be registered"
    assert "public_metrics_view" not in names, "unconfirmed view must not be registered"
    skipped = {entry["name"]: entry["reason"] for entry in body["skipped"]}
    assert skipped.get("users_pii") == "SENSITIVE_COLUMNS"
    assert skipped.get("public_metrics_view") == "VIEW_REQUIRES_CONFIRMATION"

    confirmed = admin_client.post(
        f"/api/v1/admin/datasources/{setup.datasource_id}/catalog-refresh",
        json={"schemas": ["public"], "secure_views": ["public_metrics_view"]},
    )
    assert confirmed.status_code == 200, confirmed.text
    items = confirmed.json()["items"]
    assert "public_metrics_view" in [item["object_name"] for item in items]

    # Querying the sensitive table fails: it is not a registered dataset.
    denied = admin_client.post(
        "/api/v1/queries",
        json={"datasource_id": setup.datasource_id, "sql": "SELECT email FROM users_pii"},
    )
    assert denied.status_code == 404
    assert denied.json()["error"]["code"] == "DATASET_NOT_REGISTERED"

    # The confirmed view is queryable once granted (grants were created by the
    # helper for the original refresh only, so grant explicitly here).
    view_id = next(
        item["id"] for item in items if item["object_name"] == "public_metrics_view"
    )
    roles = {role["name"]: role["id"] for role in admin_client.get("/api/v1/admin/roles").json()}
    for action in ("discover", "query"):
        grant = admin_client.post(
            "/api/v1/admin/grants",
            json={"role_id": roles["admin"], "dataset_id": view_id, "action": action},
        )
        assert grant.status_code == 201, grant.text
    submitted = admin_client.post(
        "/api/v1/queries",
        json={
            "datasource_id": setup.datasource_id,
            "sql": "SELECT country, revenue FROM public_metrics_view ORDER BY country",
        },
    )
    assert submitted.status_code == 202, submitted.text
