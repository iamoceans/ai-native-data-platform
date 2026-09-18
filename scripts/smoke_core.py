#!/usr/bin/env python3
"""Core-stack smoke test (M1).

Runs against a live stack (default http://127.0.0.1:8000):

  1. /health/live and /api/v1/health/ready
  2. login as the bootstrap admin
  3. list datasources
  4. with --demo: register the configured source PostgreSQL, refresh the
     catalog, grant role permissions, submit a real query, wait for the worker
     to finish and print the result rows

Exit code is non-zero on any failed step; nothing is simulated.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASE_URL = os.environ.get("AIND_SMOKE_BASE_URL", "http://127.0.0.1:8000")
STATUS_TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST"}


class SmokeFailure(RuntimeError):
    pass


def check(condition: bool, message: str) -> None:
    marker = "ok  " if condition else "FAIL"
    print(f"[{marker}] {message}")
    if not condition:
        raise SmokeFailure(message)


def read_dotenv(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return default


def login(client: httpx.Client, username: str, password: str) -> str:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    check(response.status_code == 200, f"login as {username}: HTTP {response.status_code}")
    body = response.json()
    return body["csrf_token"]


def main() -> int:
    parser = argparse.ArgumentParser(description="core stack smoke test")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--username", default=os.environ.get("AIND_SMOKE_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("AIND_SMOKE_PASSWORD"))
    parser.add_argument("--demo", action="store_true", help="register the demo datasource and run a query")
    parser.add_argument(
        "--kind", choices=["postgres", "mysql", "doris"], default="postgres",
        help="engine to register with --demo (full stack: mysql/doris need the full profile)",
    )
    parser.add_argument("--schema", default=None, help="catalog unit to refresh (default: engine-specific)")
    parser.add_argument("--sql", default=None)
    args = parser.parse_args()

    password = args.password or read_dotenv("AIND_SMOKE_PASSWORD") or "dev-admin-password-123"
    timeout = httpx.Timeout(30.0, read=120.0)
    default_sql = {
        "postgres": "SELECT 1 AS one",
        "mysql": "SELECT COUNT(*) AS releases FROM app_release_config",
        "doris": "SELECT country, SUM(revenue_usd) AS revenue FROM demo.ads_revenue_daily GROUP BY country ORDER BY country",
    }[args.kind]

    with httpx.Client(base_url=args.base_url, timeout=timeout) as client:
        live = client.get("/health/live")
        check(live.status_code == 200, "/health/live")
        ready = client.get("/api/v1/health/ready")
        check(ready.status_code == 200, f"/api/v1/health/ready: {ready.text[:120]}")
        print(f"       ready checks: {ready.json().get('checks')}")

        csrf = login(client, args.username, password)
        client.headers.update({"X-CSRF-Token": csrf})

        me = client.get("/api/v1/auth/me")
        check(me.status_code == 200, "/api/v1/auth/me")
        check("admin" in me.json()["roles"], "admin role present")

        datasources = client.get("/api/v1/datasources")
        check(datasources.status_code == 200, "/api/v1/datasources")

        if args.demo:
            _run_demo(client, kind=args.kind, sql=args.sql or default_sql, schema=args.schema)

        queries = client.get("/api/v1/queries")
        check(queries.status_code == 200, "/api/v1/queries")

    print("\nsmoke-core: all checks passed")
    return 0


def _engine_target(kind: str) -> dict:
    """Connection target + secret ref for the demo datasource, host-side or
    in-cluster depending on AIND_SMOKE_IN_CLUSTER."""
    in_cluster = os.environ.get("AIND_SMOKE_IN_CLUSTER") == "1"
    if kind == "postgres":
        return {
            "name": "source-postgres",
            "secret_ref": "source-postgres",
            "schema": "public",
            "config": {
                "host": "source-postgres" if in_cluster else "127.0.0.1",
                "port": 5432 if in_cluster else int(read_dotenv("SOURCE_DB_PORT", "55433")),
                "database": read_dotenv("SOURCE_DB_NAME", "ainative_source"),
                "ssl_mode": "disable",
                "connect_timeout_seconds": 5,
            },
        }
    if kind == "mysql":
        return {
            "name": "source-mysql",
            "secret_ref": "source-mysql",
            "schema": read_dotenv("MYSQL_SOURCE_DB", "ainative_source"),
            "config": {
                "host": "source-mysql" if in_cluster else "127.0.0.1",
                "port": 3306 if in_cluster else int(read_dotenv("MYSQL_DB_PORT", "33060")),
                "database": read_dotenv("MYSQL_SOURCE_DB", "ainative_source"),
                "connect_timeout_seconds": 5,
            },
        }
    return {
        "name": "source-doris",
        "secret_ref": "doris",
        "schema": "demo",
        "config": {
            "host": "doris-fe" if in_cluster else "127.0.0.1",
            "port": 9030 if in_cluster else int(read_dotenv("DORIS_FE_SQL_PORT", "19030")),
            "database": "demo",
            "connect_timeout_seconds": 5,
        },
    }


def _run_demo(client: httpx.Client, *, kind: str, sql: str, schema: str | None = None) -> None:
    target = _engine_target(kind)
    config = target["config"]
    datasource_name = target["name"]
    refresh_schema = schema or target["schema"]

    existing = client.get("/api/v1/datasources").json()["items"]
    datasource = next((item for item in existing if item["name"] == datasource_name), None)
    if datasource is None:
        created = client.post(
            "/api/v1/datasources",
            json={
                "name": datasource_name,
                "kind": kind,
                "connection_config": config,
                "secret_ref": target["secret_ref"],
            },
        )
        check(created.status_code == 201, f"register datasource: {created.text[:200]}")
        datasource = created.json()
    elif datasource["connection_config"] != config:
        patched = client.patch(
            f"/api/v1/datasources/{datasource['id']}",
            json={"version": datasource["version"], "connection_config": config},
        )
        check(patched.status_code == 200, f"update datasource: {patched.text[:200]}")
        datasource = patched.json()

    test = client.post(f"/api/v1/datasources/{datasource['id']}/test")
    check(test.status_code == 200, f"datasource test: {test.text[:200]}")
    check(test.json()["status"] == "HEALTHY", f"datasource health: {test.json()}")

    refresh = client.post(
        f"/api/v1/admin/datasources/{datasource['id']}/catalog-refresh",
        json={"schemas": [refresh_schema]},
    )
    check(refresh.status_code == 200, f"catalog refresh: {refresh.text[:200]}")
    datasets = refresh.json()["items"]
    check(len(datasets) > 0, "catalog refresh registered at least one dataset")

    roles = {role["name"]: role["id"] for role in client.get("/api/v1/admin/roles").json()}
    for role_name in ("viewer", "analyst", "admin"):
        if role_name not in roles:
            continue
        for dataset in datasets:
            for action in ("discover", "query"):
                response = client.post(
                    "/api/v1/admin/grants",
                    json={"role_id": roles[role_name], "dataset_id": dataset["id"], "action": action},
                )
                if response.status_code not in (201, 409):
                    raise SmokeFailure(f"grant failed: {response.text[:200]}")

    submitted = client.post(
        "/api/v1/queries", json={"datasource_id": datasource["id"], "sql": sql}
    )
    check(submitted.status_code == 202, f"submit query: {submitted.text[:200]}")
    query_id = submitted.json()["query_id"]

    deadline = time.monotonic() + 60
    detail: dict = {}
    while time.monotonic() < deadline:
        detail = client.get(f"/api/v1/queries/{query_id}").json()
        if detail["status"] in STATUS_TERMINAL:
            break
        time.sleep(0.5)
    check(detail.get("status") == "SUCCEEDED", f"query finished: {json.dumps(detail)[:300]}")

    results = client.get(f"/api/v1/queries/{query_id}/results")
    check(results.status_code == 200, f"fetch results: {results.text[:200]}")
    payload = results.json()["result"]
    print(f"       query {query_id}: {payload['row_count']} rows, truncated={payload['truncated']}")
    for row in payload["rows"][:5]:
        print(f"       {row}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SmokeFailure as exc:
        print(f"\nsmoke-core FAILED: {exc}")
        sys.exit(1)
    except httpx.HTTPError as exc:
        print(f"\nsmoke-core FAILED: transport error: {exc}")
        sys.exit(1)
