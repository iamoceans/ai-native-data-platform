#!/usr/bin/env python3
"""A12 live acceptance: kill the query worker mid-query and watch the platform.

This is the live counterpart of the in-process recovery suite. It pauses the
running `query-worker` container while a real query is executing, then checks
that the platform behaves exactly as the spec promises:

  1. the lease expires and the reconciler marks it SUSPECT,
  2. after the safe grace window (lease + source deadline + margin) the query is
     reaped as LOST, without any result being published,
  3. when the frozen worker is released it cannot publish anything afterwards -
     the fencing token rejects the stale write, so the job stays LOST and no
     result row ever appears.

Usage:
  python scripts/verify_a12_worker_kill.py [--worker ainative-query-worker-1]
      [--datasource source-postgres] [--base-url http://127.0.0.1:8000]
      [--password <admin pw>] [--sql "SELECT ..."]

Exit code is non-zero on the first failed check; every step is printed with the
observed value, and nothing is simulated.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval_agent import EvalError, ensure_datasource, open_authenticated_client  # noqa: E402

TERMINAL = {"SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST"}
DEFAULT_SQL = (
    "SELECT h.id, h.bucket, h.revenue_usd FROM fixture_heavy h "
    "ORDER BY h.revenue_usd DESC, h.bucket, h.id"
)


def check(condition: bool, message: str) -> None:
    marker = "ok  " if condition else "FAIL"
    print(f"[{marker}] {message}", flush=True)
    if not condition:
        raise SystemExit(1)



def assert_no_result(client, query_id: str, *, when: str) -> None:
    """A LOST query must expose no rows at all.

    The results endpoint answers 200 with ``result: null`` and a QUERY_LOST
    warning (the envelope always describes the query); a 404/410 is equally
    acceptable. What must never happen is a payload with rows in it.
    """
    response = client.get(f"/api/v1/queries/{query_id}/results")
    if response.status_code == 200:
        payload = response.json()
        check(
            payload.get("result") is None,
            f"no rows were published {when} (result={payload.get('result')!r})",
        )
    else:
        check(
            response.status_code in {404, 410},
            f"no rows were published {when} (HTTP {response.status_code})",
        )


def read_dotenv(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return default


def docker(*args: str) -> str:
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=120
    )
    if result.returncode != 0:
        raise SystemExit(f"docker {' '.join(args)} failed: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


def psql(container: str, sql: str) -> str:
    user = read_dotenv("CONTROL_DB_USER", "ainative")
    database = read_dotenv("CONTROL_DB_NAME", "ainative_control")
    return docker("exec", container, "psql", "-U", user, "-d", database, "-tAc", sql)


def main() -> int:
    parser = argparse.ArgumentParser(description="A12 live worker-kill acceptance")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=read_dotenv("AIND_SMOKE_PASSWORD"))
    parser.add_argument("--worker", default="ainative-query-worker-1")
    parser.add_argument("--control-container", default="ainative-control-postgres-1")
    parser.add_argument("--datasource", default="source-postgres")
    parser.add_argument("--sql", default=DEFAULT_SQL)
    parser.add_argument("--timeout", type=int, default=60, help="query timeout in seconds")
    parser.add_argument("--grace-wait", type=int, default=240, help="max seconds to wait for LOST")
    args = parser.parse_args()
    if not args.password:
        raise SystemExit("admin password required (--password or AIND_SMOKE_PASSWORD)")

    client = open_authenticated_client(args.base_url, args.username, args.password)
    # Integration runs re-point the datasource at the host's loopback ports; a
    # live acceptance run needs the platform's own view of the source.
    datasource_id = ensure_datasource(
        client,
        name=args.datasource,
        kind="postgres",
        config={
            "host": "source-postgres",
            "port": 5432,
            "database": "ainative_source",
            "ssl_mode": "disable",
            "connect_timeout_seconds": 5,
        },
        secret_ref="source-postgres",
        schema="public",
    )
    check(bool(datasource_id), f"datasource '{args.datasource}' is registered and reachable")

    submitted = client.post(
        "/api/v1/queries",
        json={
            "datasource_id": datasource_id,
            "sql": args.sql,
            "limits": {"max_rows": 100000, "timeout_seconds": args.timeout},
            "purpose": "query",
        },
        headers={"Idempotency-Key": f"a12-live-{uuid.uuid4().hex[:8]}"},
    )
    if submitted.status_code != 202:
        raise SystemExit(f"submit failed: HTTP {submitted.status_code} {submitted.text[:200]}")
    query_id = submitted.json()["query_id"]
    print(f"       query {query_id} submitted", flush=True)

    paused = False
    try:
        deadline = time.monotonic() + 30
        status = ""
        while time.monotonic() < deadline:
            status = client.get(f"/api/v1/queries/{query_id}").json()["status"]
            if status == "RUNNING":
                break
            if status in TERMINAL:
                break
            time.sleep(0.02)
        check(status == "RUNNING", f"query reached RUNNING before the kill (saw {status})")

        # Freeze the worker: no heartbeat, no completion, no publication.
        docker("pause", args.worker)
        paused = True
        print(f"       {args.worker} paused mid-execution", flush=True)

        lease_state = psql(
            args.control_container,
            f"select state from execution_leases where query_id = '{query_id}'",
        ).strip()
        check(
            lease_state in {"ACTIVE", "SUSPECT"},
            f"execution lease exists while the worker is frozen (state={lease_state})",
        )

        # 1. lease expiry -> SUSPECT
        suspect_seen = False
        suspect_deadline = time.monotonic() + args.grace_wait
        while time.monotonic() < suspect_deadline:
            lease_state = psql(
                args.control_container,
                f"select state from execution_leases where query_id = '{query_id}'",
            ).strip()
            if lease_state == "SUSPECT":
                suspect_seen = True
                break
            if lease_state in {"RELEASED", "EXPIRED"}:
                break
            time.sleep(2)
        check(suspect_seen, "the reconciler marked the expired lease SUSPECT")

        # 2. grace window -> LOST, with no result published
        lost_seen = False
        status = ""
        while time.monotonic() < suspect_deadline:
            status = client.get(f"/api/v1/queries/{query_id}").json()["status"]
            if status == "LOST":
                lost_seen = True
                break
            if status in TERMINAL and status != "LOST":
                break
            time.sleep(2)
        check(lost_seen, f"the query was reaped as LOST after the grace window (saw {status})")
        detail = client.get(f"/api/v1/queries/{query_id}").json()
        error_code = (detail.get("error") or {}).get("code")
        check(error_code == "QUERY_LOST", f"the terminal error code is QUERY_LOST (got {error_code})")
        assert_no_result(client, query_id, when="for a LOST query")
    finally:
        if paused:
            docker("unpause", args.worker)
            print(f"       {args.worker} unpaused", flush=True)

    # 3. the stale worker must not be able to publish anything now
    time.sleep(15)
    after = client.get(f"/api/v1/queries/{query_id}").json()
    check(after["status"] == "LOST", f"fencing kept the job LOST (got {after['status']})")
    assert_no_result(client, query_id, when="after the stale worker resumed")
    published = int(
        psql(
            args.control_container,
            f"select count(*) from query_results qr join query_jobs qj on qj.id = qr.query_id "
            f"where qj.id = '{query_id}'",
        ).strip()
        or 0
    )
    check(published == 0, f"no result row exists after the stale worker resumed ({published})")

    print("\na12-live: all checks passed (lease -> SUSPECT -> LOST, no publication, fencing held)")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EvalError as exc:
        raise SystemExit(f"a12-live failed: {exc}")
