#!/usr/bin/env python3
"""Trigger DataHub ingestion for all registered datasources and report results.

Usage:
  python scripts/metadata_sync.py [--base-url http://127.0.0.1:8000] [--password ...]

Logs in as the bootstrap admin, queues a sync per enabled datasource, waits for
each ingestion task to reach a terminal state and prints the summaries. Exit
code is non-zero when any task failed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TERMINAL = {"SUCCEEDED", "FAILED", "LOST"}


def read_env(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return default


def main() -> int:
    parser = argparse.ArgumentParser(description="queue and await DataHub ingestions")
    parser.add_argument("--base-url", default=os.environ.get("AIND_SMOKE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=os.environ.get("AIND_SMOKE_PASSWORD") or "dev-admin-password-123")
    parser.add_argument("--timeout", type=int, default=900)
    args = parser.parse_args()

    client = httpx.Client(base_url=args.base_url, timeout=httpx.Timeout(30.0, read=120.0))
    login = client.post(
        "/api/v1/auth/login", json={"username": args.username, "password": args.password}
    )
    if login.status_code != 200:
        print(f"login failed: {login.status_code} {login.text[:200]}", file=sys.stderr)
        return 2
    client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})

    datasources = client.get("/api/v1/datasources").json()["items"]
    if not datasources:
        print("no datasources registered; run the demo/smoke setup first", file=sys.stderr)
        return 2

    tasks: dict[str, str] = {}
    for datasource in datasources:
        if not datasource["enabled"]:
            continue
        response = client.post(f"/api/v1/datasources/{datasource['id']}/sync")
        if response.status_code == 202:
            task_id = response.json()["id"]
            tasks[datasource["name"]] = task_id
            print(f"queued ingestion for {datasource['name']}: {task_id}")
        elif response.status_code == 409:
            detail = response.json().get("error", {}).get("details", {})
            task_id = detail.get("ingestion_task_id")
            if task_id:
                tasks[datasource["name"]] = task_id
                print(f"ingestion already active for {datasource['name']}: {task_id}")
        else:
            print(f"sync rejected for {datasource['name']}: {response.status_code} {response.text[:200]}")
            return 1

    failed = 0
    deadline = time.monotonic() + args.timeout
    pending = dict(tasks)
    while pending and time.monotonic() < deadline:
        for name, task_id in list(pending.items()):
            response = client.get(f"/api/v1/ingestions/{task_id}")
            if response.status_code != 200:
                continue
            task = response.json()
            if task["status"] in TERMINAL:
                pending.pop(name)
                summary = task.get("summary") or {}
                print(
                    f"[{task['status']}] {name}: mapped {summary.get('mapped')}/{summary.get('expected')} "
                    f"missing={summary.get('missing')} duration={summary.get('duration_seconds')}s"
                )
                if task["status"] != "SUCCEEDED":
                    failed += 1
                    print(f"    error: {task.get('error')}")
        if pending:
            time.sleep(3)

    if pending:
        print(f"timed out waiting for: {sorted(pending)}", file=sys.stderr)
        return 1
    print("metadata-sync complete" if failed == 0 else f"metadata-sync finished with {failed} failure(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
