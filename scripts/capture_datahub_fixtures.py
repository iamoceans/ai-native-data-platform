#!/usr/bin/env python3
"""Capture real DataHub GraphQL responses as contract-test fixtures (M3).

Usage:
  python scripts/capture_datahub_fixtures.py [--target ads_revenue_daily]
      [--base-url http://127.0.0.1:8000] [--gms-url http://127.0.0.1:18080]

Requires the platform stack with a completed DataHub sync *and* the pinned
DataHub GMS reachable. Writes ``backend/tests/fixtures/datahub/*.json`` with the
response payload, the exact request variables and the release version, so the
contract tests can replay them through the adapter without a live DataHub.
Re-run this whenever the pinned DataHub release changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
GRAPHQL_DIR = ROOT / "backend" / "app" / "metadata" / "graphql"
OUT_DIR = ROOT / "backend" / "tests" / "fixtures" / "datahub"


def main() -> int:
    parser = argparse.ArgumentParser(description="capture DataHub GraphQL fixtures")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--gms-url", default="http://127.0.0.1:18080")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default="dev-admin-password-123")
    parser.add_argument("--target", default="ads_revenue_daily")
    parser.add_argument("--suffix", default="", help="fixture name suffix, e.g. _view")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    gms = args.gms_url.rstrip("/") + "/api/graphql"

    with httpx.Client(timeout=30) as client:
        login = client.post(
            f"{args.base_url}/api/v1/auth/login",
            json={"username": args.username, "password": args.password},
        )
        login.raise_for_status()
        client.headers["X-CSRF-Token"] = login.json()["csrf_token"]
        datasets = client.get(f"{args.base_url}/api/v1/datasets", params={"limit": 50}).json()["items"]
        mapped = [row for row in datasets if row["datahub_urn"]]
        if not mapped:
            print("no mapped datasets; run metadata-sync first", file=sys.stderr)
            return 2
        target = next((row for row in mapped if row["object_name"] == args.target), None)
        if target is None:
            print(f"target '{args.target}' is not mapped; mapped: {[r['object_name'] for r in mapped]}", file=sys.stderr)
            return 2
        urn = target["datahub_urn"]
        print(f"target: {target['object_name']} {urn}")

        def post_gql(query_name: str, variables: dict) -> dict:
            query = (GRAPHQL_DIR / f"{query_name}.graphql").read_text(encoding="utf-8")
            response = client.post(gms, json={"query": query, "variables": variables})
            response.raise_for_status()
            payload = response.json()
            if payload.get("errors"):
                raise SystemExit(f"{query_name}: GraphQL errors {payload['errors']}")
            return payload

        cases = {
            f"search{args.suffix}": ("search", {"query": target["object_name"], "start": 0, "count": 20}),
            f"dataset{args.suffix}": ("dataset", {"urn": urn}),
            f"lineage_upstream{args.suffix}": (
                "lineage",
                {"urn": urn, "direction": "UPSTREAM", "count": 20, "start": 0},
            ),
            f"lineage_downstream{args.suffix}": (
                "lineage",
                {"urn": urn, "direction": "DOWNSTREAM", "count": 20, "start": 0},
            ),
        }
        captured_at = datetime.now(timezone.utc).isoformat()
        for fixture_name, (query_name, variables) in cases.items():
            payload = post_gql(query_name, variables)
            document = {
                "captured_at": captured_at,
                "datahub_version": "v1.7.0.1",
                "query_name": query_name,
                "request": {"variables": variables},
                "response": payload,
            }
            path = OUT_DIR / f"{fixture_name}.json"
            path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            print(f"wrote {path.name} ({path.stat().st_size} bytes)")

        (OUT_DIR / f"target{args.suffix}.json").write_text(
            json.dumps(
                {"qualifier": f"{target['schema_name']}.{target['object_name']}", "urn": urn},
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
