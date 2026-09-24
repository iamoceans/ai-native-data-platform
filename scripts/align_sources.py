#!/usr/bin/env python3
"""Point the registered datasources at the platform's own view of the sources.

Why this exists: the integration suite registers datasources with the *host's*
loopback ports (it runs the app in-process, so `127.0.0.1:55433` is correct
there). Those rows live in the control database, which the deployed stack shares
- so after any integration run, the platform's own ingestion, analysis and the
browser journeys cannot reach the sources until the connection config is put
back to the compose service names.

Usage:
  python scripts/align_sources.py [--base-url http://127.0.0.1:8000] [--password ...]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "scripts"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from eval_agent import EvalError, ensure_datasource, open_authenticated_client  # noqa: E402

SOURCES = (
    {
        "name": "source-postgres",
        "kind": "postgres",
        "config": {
            "host": "source-postgres",
            "port": 5432,
            "database": "ainative_source",
            "ssl_mode": "disable",
            "connect_timeout_seconds": 5,
        },
        "secret_ref": "source-postgres",
        "schema": "public",
    },
    {
        "name": "source-mysql",
        "kind": "mysql",
        "config": {
            "host": "source-mysql",
            "port": 3306,
            "database": "ainative_source",
            "connect_timeout_seconds": 5,
        },
        "secret_ref": "source-mysql",
        "schema": "ainative_source",
    },
    {
        "name": "source-doris",
        "kind": "doris",
        "config": {
            "host": "doris-fe",
            "port": 9030,
            "database": "demo",
            "connect_timeout_seconds": 5,
        },
        "secret_ref": "doris",
        "schema": "demo",
    },
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


def main() -> int:
    parser = argparse.ArgumentParser(description="align datasources with the platform's view")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=read_dotenv("AIND_SMOKE_PASSWORD"))
    parser.add_argument("--only", action="append", default=[], help="limit to a datasource name")
    args = parser.parse_args()
    if not args.password:
        raise SystemExit("admin password required (--password or AIND_SMOKE_PASSWORD)")

    client = open_authenticated_client(args.base_url, args.username, args.password)
    wanted = [source for source in SOURCES if not args.only or source["name"] in args.only]
    for source in wanted:
        datasource_id = ensure_datasource(
            client,
            name=source["name"],
            kind=source["kind"],
            config=source["config"],
            secret_ref=source["secret_ref"],
            schema=source["schema"],
        )
        print(f"{source['name']}: aligned ({datasource_id})")
    datasets = client.get("/api/v1/datasets").json()["items"]
    print(f"datasets visible: {len(datasets)}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except EvalError as exc:
        raise SystemExit(f"align-sources failed: {exc}")
