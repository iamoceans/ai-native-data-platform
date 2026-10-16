#!/usr/bin/env python3
"""Check/store a DataHub token, or verify an authentication-enabled GMS.

--check probes anonymous GraphQL without writing secrets. --verify-auth requires
anonymous and random-invalid tokens to be denied, followed by a valid-token
GraphQL success; it writes nothing. Use --token-file (plain PAT or datahub.json),
DATAHUB_TOKEN, or the existing mounted datahub.json for verification. The legacy
--token option remains compatible; prefer file/environment input over arguments.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
from dataclasses import dataclass
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATASET_QUERY = """
query ainativeTokenProbe($urn: String!) {
  dataset(urn: $urn) { urn }
}
"""
KNOWN_URNS = ["urn:li:dataset:(urn:li:dataPlatform:doris,ainative.demo.probe,DEV)"]


@dataclass(frozen=True)
class Probe:
    accepted: bool
    status: int | None
    message: str


def read_env(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return default


def probe_details(gms_url: str, token: str | None, *, client: httpx.Client | None = None) -> Probe:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = {"query": DATASET_QUERY, "variables": {"urn": KNOWN_URNS[0]}}
    try:
        manager = nullcontext(client) if client is not None else httpx.Client(timeout=15)
        with manager as active:
            response = active.post(f"{gms_url.rstrip('/')}/api/graphql", json=payload, headers=headers)
    except httpx.HTTPError:
        return Probe(False, None, "DataHub GMS is unreachable")
    if response.status_code in (401, 403):
        return Probe(False, response.status_code, "GMS rejected authentication or access")
    if response.status_code != 200:
        return Probe(False, response.status_code, "GMS returned an unexpected HTTP status")
    try:
        body = response.json()
    except ValueError:
        return Probe(False, 200, "GMS returned a non-JSON response")
    if not isinstance(body, dict) or body.get("errors"):
        return Probe(False, 200, "GMS returned GraphQL errors or an invalid envelope")
    data = body.get("data")
    if not isinstance(data, dict) or "dataset" not in data:
        return Probe(False, 200, "GMS response is missing the requested dataset field")
    dataset = data["dataset"]
    if dataset is not None and (not isinstance(dataset, dict) or dataset.get("urn") != KNOWN_URNS[0]):
        return Probe(False, 200, "GMS returned an invalid dataset field")
    return Probe(True, 200, "GMS answered the GraphQL call")


def probe(gms_url: str, token: str | None) -> tuple[bool, str]:
    result = probe_details(gms_url, token)
    return result.accepted, result.message


def verify_auth(gms_url: str, token: str, *, client: httpx.Client | None = None) -> tuple[bool, str]:
    if not token.strip():
        return False, "a valid-token input is required"
    anonymous = probe_details(gms_url, None, client=client)
    if anonymous.status not in (401, 403):
        return False, "anonymous request was not explicitly denied with HTTP 401/403"
    invalid = probe_details(gms_url, "invalid-acceptance-" + secrets.token_urlsafe(24), client=client)
    if invalid.status not in (401, 403):
        return False, "invalid token was not explicitly denied with HTTP 401/403"
    valid = probe_details(gms_url, token, client=client)
    if not valid.accepted:
        return False, "valid-token GraphQL probe failed: " + valid.message
    return True, "anonymous and invalid-token requests denied; valid-token GraphQL succeeded"


def token_from_file(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if text.startswith("{"):
        value = json.loads(text).get("token")
    else:
        value = text
    if not isinstance(value, str) or not value.strip() or "\n" in value or "\r" in value:
        raise ValueError("Token file must contain a nonempty token")
    return value.strip()


def store_token(secrets_dir: Path, token: str) -> Path:
    secrets_dir.mkdir(parents=True, exist_ok=True)
    target = secrets_dir / "datahub.json"
    payload = json.loads(target.read_text(encoding="utf-8")) if target.is_file() else {}
    if not isinstance(payload, dict):
        raise ValueError("Existing DataHub secret must be an object")
    payload["token"] = token
    payload.setdefault("description", "DataHub personal access token (platform reads it as bearer)")
    fd, temporary = tempfile.mkstemp(prefix=".datahub-", dir=secrets_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gms-url", default=read_env("AIND_DATAHUB_GMS_URL", "http://127.0.0.1:18080"))
    token_group = parser.add_mutually_exclusive_group()
    token_group.add_argument("--token", help="legacy PAT input; prefer --token-file or DATAHUB_TOKEN")
    token_group.add_argument("--token-file", type=Path, help="private plain PAT or datahub.json file")
    parser.add_argument("--secrets-dir", type=Path, default=ROOT / "infra" / "local-secrets")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="probe anonymous access; write nothing")
    mode.add_argument("--verify-auth", action="store_true", help="verify denied/valid auth paths; write nothing")
    args = parser.parse_args()
    try:
        token = None if args.check else args.token or (
            token_from_file(args.token_file) if args.token_file else os.environ.get("DATAHUB_TOKEN")
        )
        if args.verify_auth:
            if not token:
                token = token_from_file(args.secrets_dir / "datahub.json")
            ok, message = verify_auth(args.gms_url, token)
            print(f"auth verification {'passed' if ok else 'failed'}: {message}")
            return 0 if ok else 1
        if args.check or not token:
            ok, message = probe(args.gms_url, None)
            if ok:
                print("anonymous GraphQL accepted; GMS authentication appears disabled; nothing written")
                return 0
            print(f"anonymous probe failed: {message}; authentication state is not established")
            return 1
        ok, message = probe(args.gms_url, token)
        if not ok:
            print(f"token verification failed: {message}", file=sys.stderr)
            return 1
        target = store_token(args.secrets_dir, token)
        print(f"token verified and stored at {target} (value not printed)")
        print("restart backend/ingestion containers to load the new secret")
        return 0
    except (OSError, ValueError, TypeError, AttributeError):
        print("token operation failed: check input file, token format and secret directory permissions", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
