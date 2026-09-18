#!/usr/bin/env python3
"""DataHub access token helper (M3) - OPTIONAL.

The pinned DataHub quickstart stack ships with GMS authentication **disabled**
(``METADATA_SERVICE_AUTH_ENABLED=false``), and the platform works without any
token in that mode. This helper is only needed when the deployment enables GMS
authentication:

  python scripts/datahub_token.py --check
      Probe the GMS and report whether anonymous access is accepted.

  python scripts/datahub_token.py --token <PAT>
      Verify a personal access token against the GMS and, when accepted, store
      it in ``infra/local-secrets/datahub.json`` (the mounted secret the platform
      reads). The token value is never printed or written to logs.

Create the token in the DataHub UI (Settings -> Access Tokens) after enabling
authentication; the platform then sends it as ``Authorization: Bearer``.

Exit codes: 0 = token not required or verification succeeded; 1 = the provided
token was rejected or the GMS is unreachable; 2 = bad usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

DATASET_QUERY = """
query ainativeTokenProbe($urn: String!) {
  dataset(urn: $urn) { urn }
}
"""

KNOWN_URNS = [
    "urn:li:dataset:(urn:li:dataPlatform:doris,ainative.demo.probe,DEV)",
]


def read_env(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return default


def probe(gms_url: str, token: str | None) -> tuple[bool, str]:
    """Return (ok, message). Ok means the GMS answered the GraphQL call."""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {"query": DATASET_QUERY, "variables": {"urn": KNOWN_URNS[0]}}
    try:
        with httpx.Client(timeout=15) as client:
            response = client.post(f"{gms_url.rstrip('/')}/api/graphql", json=payload, headers=headers)
    except httpx.HTTPError as exc:
        return False, f"DataHub GMS is unreachable: {exc}"
    if response.status_code in (401, 403):
        return False, "the GMS rejected the request (authentication required or token invalid)"
    if response.status_code >= 500:
        return False, f"the GMS returned a server error ({response.status_code})"
    try:
        body = response.json()
    except json.JSONDecodeError:
        return False, "the GMS returned a non-JSON response"
    if body.get("errors"):
        messages = [str(item.get("message", item)) for item in body["errors"][:2]]
        return False, f"GraphQL errors: {messages}"
    return True, "the GMS answered the GraphQL call"


def main() -> int:
    parser = argparse.ArgumentParser(description="optional DataHub access token helper")
    parser.add_argument(
        "--gms-url",
        default=read_env("AIND_DATAHUB_GMS_URL", "http://127.0.0.1:18080"),
        help="DataHub GMS base URL (host-side default: the loopback port)",
    )
    parser.add_argument("--token", default=None, help="personal access token to verify and store")
    parser.add_argument(
        "--secrets-dir",
        default=str(ROOT / "infra" / "local-secrets"),
        help="directory that is mounted as /run/secrets",
    )
    parser.add_argument("--check", action="store_true", help="only probe anonymous access")
    args = parser.parse_args()

    gms_url = args.gms_url

    if args.check or not args.token:
        ok, message = probe(gms_url, token=None)
        if ok:
            print(f"token not required: {message} (GMS authentication appears disabled)")
            print("the platform works without a DataHub token in this mode; nothing written")
            return 0
        print(f"anonymous access failed: {message}")
        print("GMS authentication appears enabled; create a token in the DataHub UI")
        print("(Settings -> Access Tokens) and re-run: python scripts/datahub_token.py --token <PAT>")
        return 1

    ok, message = probe(gms_url, token=args.token)
    if not ok:
        print(f"token verification failed: {message}", file=sys.stderr)
        return 1
    secrets_dir = Path(args.secrets_dir)
    secrets_dir.mkdir(parents=True, exist_ok=True)
    target = secrets_dir / "datahub.json"
    payload: dict = {}
    if target.is_file():
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
    payload["token"] = args.token
    payload.setdefault("description", "DataHub personal access token (platform reads it as bearer)")
    target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"token verified and stored at {target} (value not printed)")
    print("restart the backend/ingestion containers to pick up the new secret")
    return 0


if __name__ == "__main__":
    sys.exit(main())
