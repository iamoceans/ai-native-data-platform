#!/usr/bin/env python3
"""M3 metadata verification: DataHub-backed context, lineage and metrics.

Usage: python scripts/verify_metadata.py [--base-url http://127.0.0.1:8000]

Logs in as the bootstrap admin, then for each visible dataset prints:
sync status, mapped DataHub URN, context source (datahub vs registry), semantic
fields (grain/currency/metric keys), lineage status and the metric registry.
Exit code is non-zero when a SYNCED dataset has no URN or context is stale.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
TERMINAL_OK = {"SYNCED"}


def main() -> int:
    parser = argparse.ArgumentParser(description="verify M3 metadata integration")
    parser.add_argument("--base-url", default=os.environ.get("AIND_SMOKE_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password", default=os.environ.get("AIND_SMOKE_PASSWORD") or "dev-admin-password-123")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": args.username, "password": args.password}
        )
        if login.status_code != 200:
            print(f"login failed: {login.status_code} {login.text[:200]}", file=sys.stderr)
            return 2
        client.headers.update({"X-CSRF-Token": login.json()["csrf_token"]})

        datasets = client.get("/api/v1/datasets").json()["items"]
        problems = 0
        for dataset in datasets:
            context = client.get(f"/api/v1/datasets/{dataset['id']}").json()
            line = (
                f"{dataset['object_name']:<20} sync={dataset['sync_status']:<7} "
                f"urn={'yes' if dataset['datahub_urn'] else 'NO ':<3} "
                f"source={context.get('metadata_source'):<16} stale={context.get('metadata_stale')}"
            )
            extras = []
            if context.get("grain"):
                extras.append(f"grain={'/'.join(context['grain'])}")
            if context.get("currency"):
                extras.append(f"currency={context['currency']}")
            if context.get("metric_keys"):
                extras.append(f"metrics={','.join(context['metric_keys'])}")
            if extras:
                line += "  " + " ".join(extras)
            print(line)
            if dataset["sync_status"] in TERMINAL_OK and not dataset["datahub_urn"]:
                problems += 1
            if context.get("metadata_stale"):
                problems += 1

        lineage = None
        if datasets:
            lineage = client.get(
                f"/api/v1/datasets/{datasets[0]['id']}/lineage",
                params={"direction": "upstream", "depth": 1},
            ).json()
            print(
                f"lineage sample ({datasets[0]['object_name']}): status={lineage['status']} "
                f"nodes={len(lineage['nodes'])} evidence={len(lineage['analysis_evidence'])}"
            )

        metrics = client.get("/api/v1/metrics").json()["items"]
        print(f"metrics: {', '.join(m['metric_key'] for m in metrics)}")
        if not metrics:
            problems += 1

    if problems:
        print(f"\nverify-metadata: {problems} problem(s) found")
        return 1
    print("\nverify-metadata: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
