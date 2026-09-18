"""Test helpers: worker draining, source fixtures, permissions setup."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg

SOURCE_FIXTURE_DDL = """
CREATE TABLE IF NOT EXISTS public.fixture_metrics (
  dt date NOT NULL,
  country text NOT NULL,
  platform text NOT NULL,
  note text,
  revenue_usd numeric(20,6) NOT NULL,
  impressions bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS public.fixture_heavy (
  id bigint NOT NULL,
  bucket integer NOT NULL,
  revenue_usd numeric(20,6) NOT NULL
);
"""

SOURCE_FIXTURE_DATA = """
TRUNCATE TABLE public.fixture_metrics;
INSERT INTO public.fixture_metrics (dt, country, platform, note, revenue_usd, impressions)
SELECT
  DATE '2026-09-01' + (gs % 10),
  CASE WHEN gs % 3 = 0 THEN 'US' ELSE 'DE' END,
  CASE WHEN gs % 2 = 0 THEN 'android' ELSE 'ios' END,
  CASE WHEN gs % 5 = 0 THEN NULL ELSE 'note-' || gs END,
  ((gs % 997)::numeric / 10)::numeric(20,6),
  (gs * 10)::bigint
FROM generate_series(1, 2500) AS gs;

TRUNCATE TABLE public.fixture_heavy;
INSERT INTO public.fixture_heavy (id, bucket, revenue_usd)
SELECT gs, gs % 100, ((gs % 10000)::numeric / 7)::numeric(20,6)
FROM generate_series(1, 100000) AS gs;
"""


def source_dsn(source_url: str) -> str:
    prefixless = source_url.split("://", 1)[1]
    credentials, hostpart = prefixless.split("@", 1)
    user, password = credentials.split(":", 1)
    hostport, database = hostpart.split("/", 1)
    host, port = hostport.split(":", 1)
    return f"host={host} port={port} dbname={database} user={user} password={password}"


def parse_url(url: str) -> dict:
    """Parse mysql://user:password@host:port/database (or postgresql://...)."""
    import urllib.parse

    parsed = urllib.parse.urlparse(url)
    return {
        "scheme": parsed.scheme,
        "user": urllib.parse.unquote(parsed.username or ""),
        "password": urllib.parse.unquote(parsed.password or ""),
        "host": parsed.hostname or "127.0.0.1",
        "port": parsed.port or 3306,
        "database": (parsed.path or "/").lstrip("/"),
    }


def connect_mysql(url: str):
    import pymysql

    parts = parse_url(url)
    return pymysql.connect(
        host=parts["host"],
        port=parts["port"],
        user=parts["user"],
        password=parts["password"],
        database=parts["database"] or None,
        charset="utf8mb4",
        autocommit=True,
    )


def seeded_engine(admin_client, *, kind: str, url: str, schema: str):
    """Register an engine datasource, refresh its catalog and grant access.

    Returns a SourceSetup-like record. The engine tables must be present (the
    session fixture runs scripts/seed_sources.py first).
    """
    parts = parse_url(url)
    response = admin_client.post(
        "/api/v1/datasources",
        json={
            "name": f"source-{kind}",
            "kind": kind,
            "connection_config": {
                "host": parts["host"],
                "port": parts["port"],
                "database": parts["database"],
                "connect_timeout_seconds": 5,
            },
            "secret_ref": "source-mysql" if kind == "mysql" else "doris",
        },
    )
    assert response.status_code == 201, response.text
    datasource_id = response.json()["id"]

    test = admin_client.post(f"/api/v1/datasources/{datasource_id}/test")
    assert test.status_code == 200, test.text
    assert test.json()["status"] == "HEALTHY", test.text

    refresh = admin_client.post(
        f"/api/v1/admin/datasources/{datasource_id}/catalog-refresh",
        json={"schemas": [schema]},
    )
    assert refresh.status_code == 200, refresh.text
    items = refresh.json()["items"]
    dataset_ids = {item["object_name"]: item["id"] for item in items}
    assert dataset_ids, refresh.text

    roles = {role["name"]: role["id"] for role in admin_client.get("/api/v1/admin/roles").json()}
    for dataset_id in dataset_ids.values():
        for action in ("discover", "query"):
            created = admin_client.post(
                "/api/v1/admin/grants",
                json={"role_id": roles["admin"], "dataset_id": dataset_id, "action": action},
            )
            assert created.status_code == 201, created.text

    return SourceSetup(
        datasource_id=datasource_id,
        dataset_ids=dataset_ids,
        host=parts["host"],
        port=parts["port"],
        database=parts["database"],
    )


def run_seed_script(*, mysql_url: str | None, doris_url: str | None) -> None:
    """Run scripts/seed_sources.py against the given engines (idempotent)."""
    import subprocess
    import sys

    root = Path(__file__).resolve().parents[2]
    args = [sys.executable, str(root / "scripts" / "seed_sources.py")]
    if mysql_url:
        parts = parse_url(mysql_url)
        args += ["--mysql-host", parts["host"], "--mysql-port", str(parts["port"])]
    if doris_url:
        parts = parse_url(doris_url)
        args += ["--doris-host", parts["host"], "--doris-port", str(parts["port"])]
    env = dict(os.environ)
    if mysql_url:
        parts = parse_url(mysql_url)
        env["MYSQL_ADMIN_USER"] = parts["user"]
        env["MYSQL_ADMIN_PASSWORD"] = parts["password"]
        env["MYSQL_SOURCE_DB"] = parts["database"]
    if doris_url:
        parts = parse_url(doris_url)
        env["DORIS_ADMIN_USER"] = parts["user"]
        env["DORIS_ADMIN_PASSWORD"] = parts["password"]
    result = subprocess.run(args, cwd=root, capture_output=True, text=True, env=env, timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"seed_sources.py failed: {result.stdout}\n{result.stderr}")


@dataclass
class SourceSetup:
    datasource_id: str
    dataset_ids: dict[str, str]
    host: str
    port: int
    database: str


def seed_source(source_url: str) -> dict:
    """Create the deterministic fixture tables in the source database."""
    with psycopg.connect(source_dsn(source_url)) as conn:
        conn.execute(SOURCE_FIXTURE_DDL)
        conn.execute(SOURCE_FIXTURE_DATA)
        conn.commit()
    prefixless = source_url.split("://", 1)[1]
    hostpart = prefixless.split("@", 1)[1]
    hostport, database = hostpart.split("/", 1)
    host, port = hostport.split(":", 1)
    return {"host": host, "port": int(port), "database": database}


def setup_datasource_and_grants(
    admin_client, source_url: str, *, grant_roles: list[str] | None = None
) -> SourceSetup:
    target = seed_source(source_url)
    response = admin_client.post(
        "/api/v1/datasources",
        json={
            "name": "source-postgres",
            "kind": "postgres",
            "connection_config": {
                "host": target["host"],
                "port": target["port"],
                "database": target["database"],
                "ssl_mode": "disable",
                "connect_timeout_seconds": 5,
            },
            "secret_ref": "source-postgres",
        },
    )
    assert response.status_code == 201, response.text
    datasource_id = response.json()["id"]

    test = admin_client.post(f"/api/v1/datasources/{datasource_id}/test")
    assert test.status_code == 200, test.text
    assert test.json()["status"] == "HEALTHY", test.text

    refresh = admin_client.post(
        f"/api/v1/admin/datasources/{datasource_id}/catalog-refresh",
        json={"schemas": ["public"]},
    )
    assert refresh.status_code == 200, refresh.text
    items = refresh.json()["items"]
    dataset_ids = {item["object_name"]: item["id"] for item in items}
    assert "fixture_metrics" in dataset_ids, refresh.text

    roles = admin_client.get("/api/v1/admin/roles")
    role_map = {role["name"]: role["id"] for role in roles.json()}
    wanted = grant_roles if grant_roles is not None else ["admin", "analyst", "viewer"]
    for role_name in wanted:
        for dataset_id in dataset_ids.values():
            for action in ("discover", "query"):
                created = admin_client.post(
                    "/api/v1/admin/grants",
                    json={
                        "role_id": role_map[role_name],
                        "dataset_id": dataset_id,
                        "action": action,
                    },
                )
                assert created.status_code == 201, created.text

    return SourceSetup(
        datasource_id=datasource_id,
        dataset_ids=dataset_ids,
        host=target["host"],
        port=target["port"],
        database=target["database"],
    )


def drain_worker(max_cycles: int = 50, worker_id: str | None = None) -> int:
    """Run claim+execute synchronously until the queue is empty."""
    from app.config import get_settings
    from app.db import get_session_factory, session_scope
    from app.query import executor, scheduler
    from app.runtime import get_result_store

    settings = get_settings()
    session_factory = get_session_factory()
    store = get_result_store()
    processed = 0
    for _ in range(max_cycles):
        with session_scope() as session:
            claim = scheduler.claim_next(
                session, worker_id=worker_id or f"test-worker-{uuid.uuid4().hex[:6]}", settings=settings
            )
        if claim is None:
            break
        executor.execute_claim(
            session_factory=session_factory, settings=settings, store=store, claim=claim
        )
        processed += 1
    return processed


def wait_for_query(client, query_id: str, timeout: float = 20.0) -> dict:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/queries/{query_id}")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["status"] in {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST"}:
            return payload
        time.sleep(0.2)
    raise AssertionError(f"query {query_id} did not reach a terminal state")
