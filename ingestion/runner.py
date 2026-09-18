#!/usr/bin/env python3
"""AI-Native Data Platform ingestion runner (spec section 10.1).

Runs inside the pinned DataHub ingestion image
(``acryldata/datahub-ingestion:v1.7.0.1``). The API writes a *payload* per
ingestion task into the shared work volume (recipe with env placeholders,
expected datasets, semantic custom properties). This runner:

1. claims a QUEUED ingestion task from the control database (SKIP LOCKED),
2. resolves source credentials from the mounted secret files,
3. runs ``datahub ingest -c recipe.yml`` with credentials in the environment,
4. waits for the ingested assets to become searchable in DataHub,
5. writes the observed DataHub URNs back onto the platform datasets,
6. emits semantic custom properties (best effort, via the DataHub SDK),
7. finalizes the ingestion task and the queue row.

Only stdlib + psycopg2 (present in the official image) + the bundled ``datahub``
CLI are used; no platform code is imported, so the connector dependency tree
never touches the API/query-worker images.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import psycopg2  # provided by the official ingestion image

STATUS_QUEUED = "QUEUED"
STATUS_RUNNING = "RUNNING"
STATUS_SUCCEEDED = "SUCCEEDED"
STATUS_FAILED = "FAILED"
LEASE_SECONDS = 1800

DEFAULT_WORK_DIR = "/data/ingestion"
DEFAULT_SECRETS_DIR = "/run/secrets"
DEFAULT_GMS_URL = "http://datahub-gms:8080"


def log(message: str, **fields: Any) -> None:
    payload = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "message": message}
    payload.update({key: value for key, value in fields.items() if value is not None})
    print(json.dumps(payload, ensure_ascii=False), flush=True)


# --------------------------------------------------------------------------
# database helpers (raw SQL: this process does not use the platform ORM)
# --------------------------------------------------------------------------
def dsn_from_env() -> str:
    url = os.environ.get("AIND_DATABASE_URL")
    if not url:
        raise SystemExit("AIND_DATABASE_URL is required")
    return url.replace("postgresql+psycopg://", "postgresql://").replace(
        "postgresql+psycopg2://", "postgresql://"
    )


def connect():
    return psycopg2.connect(dsn_from_env())


def claim_task(conn, worker_id: str):
    with conn.cursor() as cur:
        cur.execute(
            """
            WITH claimed AS (
              SELECT id FROM task_queue
              WHERE kind = 'ingestion' AND state = 'READY' AND available_at <= now()
              ORDER BY available_at, id
              FOR UPDATE SKIP LOCKED
              LIMIT 1
            )
            UPDATE task_queue
               SET state = 'LEASED', worker_id = %s,
                   lease_until = now() + (%s || ' seconds')::interval,
                   attempt = attempt + 1, fencing_token = fencing_token + 1
             FROM claimed
             WHERE task_queue.id = claimed.id
         RETURNING task_queue.resource_id, task_queue.fencing_token, task_queue.attempt
            """,
            (worker_id, LEASE_SECONDS),
        )
        row = cur.fetchone()
        if row is None:
            conn.commit()
            return None
        resource_id, fencing_token, attempt = row
        cur.execute(
            """
            UPDATE ingestion_tasks
               SET status = 'RUNNING', started_at = COALESCE(started_at, now())
             WHERE id = %s AND status IN ('QUEUED', 'RUNNING')
            """,
            (resource_id,),
        )
        if cur.rowcount == 0:
            cur.execute(
                "UPDATE task_queue SET state = 'FAILED' WHERE resource_id = %s AND kind = 'ingestion'",
                (resource_id,),
            )
            conn.commit()
            return None
        conn.commit()
        return {
            "task_id": str(resource_id),
            "fencing_token": int(fencing_token),
            "attempt": int(attempt),
        }


def finish_task(conn, task_id: str, *, status: str, summary: dict | None, error: dict | None) -> None:
    queue_state = "DONE" if status == STATUS_SUCCEEDED else "FAILED"
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingestion_tasks
               SET status = %s, summary = %s, error = %s, finished_at = now()
             WHERE id = %s
            """,
            (status, json.dumps(summary) if summary is not None else None,
             json.dumps(error) if error is not None else None, task_id),
        )
        cur.execute(
            "UPDATE task_queue SET state = %s, lease_until = NULL WHERE resource_id = %s AND kind = 'ingestion'",
            (queue_state, task_id),
        )
        conn.commit()


def update_dataset_urn(conn, *, datasource_id: str, qualifier: str, urn: str) -> int:
    namespace, _, table = qualifier.partition(".")
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE datasets
               SET datahub_urn = %s, sync_status = 'SYNCED', last_synced_at = now()
             WHERE datasource_id = %s
               AND object_name = %s
               AND (schema_name = %s OR catalog_name = %s)
            """,
            (urn, datasource_id, table, namespace, namespace),
        )
        updated = cur.rowcount
        conn.commit()
        return updated


def mark_sync_status(conn, *, datasource_id: str, sync_status: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE datasets SET sync_status = %s WHERE datasource_id = %s AND active",
            (sync_status, datasource_id),
        )
        conn.commit()


# --------------------------------------------------------------------------
# secrets / payload
# --------------------------------------------------------------------------
def load_secret(secrets_dir: Path, ref: str) -> dict:
    path = secrets_dir / f"{ref}.json"
    if not path.is_file():
        raise RuntimeError(f"secret '{ref}' is not mounted")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload.get("username"), str) or not isinstance(payload.get("password"), str):
        raise RuntimeError(f"secret '{ref}' is malformed")
    return payload


def datahub_token(secrets_dir: Path) -> str:
    path = secrets_dir / "datahub.json"
    if not path.is_file():
        return ""
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("token") or "")
    except json.JSONDecodeError:
        return ""


# --------------------------------------------------------------------------
# DataHub GraphQL (stdlib only)
# --------------------------------------------------------------------------
SEARCH_QUERY = """
query searchDatasets($query: String!, $start: Int!, $count: Int!) {
  searchAcrossEntities(input: {types: [DATASET], query: $query, start: $start, count: $count}) {
    total
    searchResults {
      entity {
        urn
        type
        ... on Dataset { name }
      }
    }
  }
}
"""


def graphql(gms_url: str, token: str, query: str, variables: dict) -> dict:
    request = urllib.request.Request(
        f"{gms_url.rstrip('/')}/api/graphql",
        data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed local URL
        body = json.loads(response.read().decode("utf-8"))
    if body.get("errors"):
        raise RuntimeError(f"GraphQL errors: {str(body['errors'])[:200]}")
    return body.get("data") or {}


def discover_urn(
    gms_url: str,
    token: str,
    *,
    qualifier: str,
    platform_instance: str,
    kind: str,
    env: str,
    timeout_seconds: int,
) -> str | None:
    """Find the real DataHub URN for a registered dataset.

    1. Poll ``searchAcrossEntities`` (the catalog path the spec describes); the
       platform instance lives inside the URN name segment in the current
       DataHub layout (``<platform_instance>.<namespace>.<table>,<env>``).
    2. If the search index has not caught up (observed on the reference stack
       for the postgres source), verify candidate URNs with a direct
       ``dataset(urn:)`` lookup. A candidate is only accepted when the entity
       exists in DataHub *and* its name matches the expected qualifier - the
       platform never stores an unverified URN.
    """
    table = qualifier.partition(".")[2] or qualifier
    deadline = time.monotonic() + timeout_seconds
    attempts = 0
    while time.monotonic() < deadline:
        attempts += 1
        try:
            data = graphql(gms_url, token, SEARCH_QUERY, {"query": table, "start": 0, "count": 50})
        except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
            log("discovery search failed", qualifier=qualifier, error=str(exc)[:200])
            time.sleep(3)
            continue
        results = ((data.get("searchAcrossEntities") or {}).get("searchResults")) or []
        for item in results:
            urn = str(((item.get("entity") or {}).get("urn")) or "")
            match = re.match(
                r"urn:li:dataset:\([^,]+,\s*(?P<name>[^,]+),\s*(?P<env>[^)]+)\)", urn
            )
            if not match:
                continue
            name = match.group("name")
            if name.endswith(qualifier) and name.startswith(f"{platform_instance}."):
                return urn
        time.sleep(4)

    log("search-based discovery timed out; verifying candidate URNs", qualifier=qualifier, attempts=attempts)
    return verify_candidate_urn(
        gms_url,
        token,
        qualifier=qualifier,
        platform_instance=platform_instance,
        kind=kind,
        env=env,
    )


ENTITY_QUERY = """
query getDatasetByUrn($urn: String!) {
  dataset(urn: $urn) { urn name }
}
"""


def verify_candidate_urn(
    gms_url: str,
    token: str,
    *,
    qualifier: str,
    platform_instance: str,
    kind: str,
    env: str,
) -> str | None:
    platform = {"postgres": "postgres", "mysql": "mysql", "doris": "doris"}.get(kind, kind)
    names = [f"{platform_instance}.{qualifier}", qualifier]
    envs = [env, "PROD", "DEV"]
    for name in names:
        for fabric in envs:
            candidate = f"urn:li:dataset:(urn:li:dataPlatform:{platform},{name},{fabric})"
            try:
                data = graphql(gms_url, token, ENTITY_QUERY, {"urn": candidate})
            except (urllib.error.URLError, RuntimeError, TimeoutError) as exc:
                log("candidate verification failed", urn=candidate, error=str(exc)[:200])
                continue
            entity = data.get("dataset") or {}
            entity_name = str(entity.get("name") or "")
            if entity.get("urn") and entity_name.endswith(qualifier):
                return str(entity["urn"])
    return None


# --------------------------------------------------------------------------
# semantic custom properties (best effort, SDK inside the official image)
# --------------------------------------------------------------------------
EMIT_SCRIPT = r"""
import json, sys
from datahub.emitter.mcp import MetadataChangeProposalWrapper
from datahub.emitter.rest_emitter import DatahubRestEmitter
from datahub.metadata.schema_classes import DatasetPropertiesClass

payload = json.load(open(sys.argv[1], encoding="utf-8"))
emitter = DatahubRestEmitter(gms_server=payload["gms_url"], token=payload.get("token") or None)
for entry in payload["entries"]:
    props = DatasetPropertiesClass(customProperties=entry["properties"])
    emitter.emit(MetadataChangeProposalWrapper(entityUrn=entry["urn"], aspect=props))
print(f"emitted {len(payload['entries'])} dataset properties")
"""


def emit_semantic_properties(
    *, gms_url: str, token: str, entries: list[dict], work_dir: Path
) -> str | None:
    if not entries:
        return None
    script = work_dir / "emit_properties.py"
    script.write_text(EMIT_SCRIPT, encoding="utf-8")
    payload_path = work_dir / "properties.json"
    payload_path.write_text(
        json.dumps({"gms_url": gms_url, "token": token, "entries": entries}), encoding="utf-8"
    )
    try:
        result = subprocess.run(
            [sys.executable, str(script), str(payload_path)],
            capture_output=True,
            text=True,
            timeout=300,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"emitter failed: {exc}"
    if result.returncode != 0:
        return f"emitter exited {result.returncode}: {result.stderr.strip()[:200]}"
    return None


# --------------------------------------------------------------------------
# task execution
# --------------------------------------------------------------------------
def run_task(conn, payload: dict, *, work_dir: Path, secrets_dir: Path, dry_run: bool) -> dict:
    datasource_id = payload["datasource_id"]
    # The shared work volume is written by the API (root in the backend image);
    # this runner only reads the payload and keeps its scratch files in the
    # system temporary directory it owns (in the ingestion image that is /tmp;
    # the default also keeps the runner executable under host-side tests).
    task_dir = Path(tempfile.mkdtemp(prefix=f"{payload['task_id']}-"))
    recipe_path = task_dir / "recipe.yml"

    credentials = load_secret(secrets_dir, payload["secret_ref"])
    token = datahub_token(secrets_dir)
    recipe = payload["recipe"]
    if not token:
        # Quickstart ships with GMS auth disabled; dropping the placeholder
        # avoids `expandvars` UnboundVariable errors for an unset variable.
        sink_config = (recipe.get("sink") or {}).get("config") or {}
        sink_config.pop("token", None)

    # Recipe is stored as JSON in the payload; dump it as YAML for the CLI.
    try:
        import yaml  # provided by the ingestion image
    except ImportError:  # pragma: no cover - defensive
        raise RuntimeError("the ingestion image must provide PyYAML") from None
    recipe_path.write_text(yaml.safe_dump(recipe, sort_keys=False), encoding="utf-8")

    env = dict(os.environ)
    env.update(
        {
            "SOURCE_USERNAME": credentials["username"],
            "SOURCE_PASSWORD": credentials["password"],
            "DATAHUB_GMS_URL": payload.get("gms_url") or os.environ.get("DATAHUB_GMS_URL", DEFAULT_GMS_URL),
            # Telemetry retries added ~5 minutes of delay on a restricted
            # network before failing (observed on the reference machine).
            "DATAHUB_TELEMETRY_ENABLED": os.environ.get("AIND_DATAHUB_TELEMETRY_ENABLED", "false"),
        }
    )
    if token:
        env["DATAHUB_TOKEN"] = token
    command = ["datahub", "ingest", "-c", str(recipe_path), "--no-progress"]
    if dry_run:
        command.append("--dry-run")
    started = time.monotonic()
    log("ingestion starting", task_id=payload["task_id"], command=" ".join(command[:3]))
    result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=3600)
    duration = round(time.monotonic() - started, 1)
    combined_log = task_dir / "ingest.log"
    combined_log.write_text(
        (result.stdout or "") + "\n--- stderr ---\n" + (result.stderr or ""), encoding="utf-8"
    )
    output_tail = (result.stdout or "")[-4000:]
    error_tail = (result.stderr or "")[-2000:]
    error_highlights = [
        line.strip()
        for line in (result.stderr or "").splitlines()
        if re.search(r"error|exception|unbound|traceback", line, re.IGNORECASE)
    ][:6]
    log(
        "ingestion finished",
        task_id=payload["task_id"],
        exit_code=result.returncode,
        duration_seconds=duration,
    )
    if result.returncode != 0:
        return {
            "status": STATUS_FAILED,
            "summary": {
                "exit_code": result.returncode,
                "duration_seconds": duration,
                "output_tail": output_tail,
                "error_highlights": error_highlights,
            },
            "error": {
                "code": "INGESTION_FAILED",
                "message": f"datahub ingest exited {result.returncode}",
                "details": {"stderr_tail": error_tail[:1200]},
            },
        }

    if dry_run:
        return {
            "status": STATUS_SUCCEEDED,
            "summary": {"dry_run": True, "duration_seconds": duration, "output_tail": output_tail},
            "error": None,
        }

    gms_url = env["DATAHUB_GMS_URL"]
    timeout_seconds = int(os.environ.get("AIND_INGESTION_VISIBILITY_TIMEOUT", "60"))
    mapped: dict[str, str] = {}
    missing: list[str] = []
    source_env = str(((payload.get("recipe") or {}).get("source") or {}).get("config", {}).get("env") or "PROD")
    for qualifier in payload.get("expected_datasets", []):
        urn = discover_urn(
            gms_url,
            token,
            qualifier=qualifier,
            platform_instance=payload["platform_instance"],
            kind=payload["kind"],
            env=source_env,
            timeout_seconds=timeout_seconds,
        )
        if urn is None:
            missing.append(qualifier)
            continue
        mapped[qualifier] = urn
        update_dataset_urn(conn, datasource_id=datasource_id, qualifier=qualifier, urn=urn)

    properties_error = None
    entries = [
        {"urn": mapped[qualifier], "properties": json.loads(properties)}
        for qualifier, properties in (payload.get("custom_properties") or {}).items()
        if qualifier in mapped
    ]
    properties_error = emit_semantic_properties(
        gms_url=gms_url, token=token, entries=entries, work_dir=task_dir
    )
    if properties_error:
        log("semantic property emit failed", task_id=payload["task_id"], error=properties_error)

    summary = {
        "duration_seconds": duration,
        "expected": len(payload.get("expected_datasets", [])),
        "mapped": len(mapped),
        "missing": missing,
        "custom_properties_emitted": len(entries) if not properties_error else 0,
        "custom_properties_error": properties_error,
        "output_tail": output_tail,
    }
    if missing:
        # Assets were ingested but not observed yet: the sync is still a success
        # (mapping can catch up in the next run), reported explicitly.
        summary["warning"] = "some datasets were not discovered within the visibility window"
    return {"status": STATUS_SUCCEEDED, "summary": summary, "error": None}


def process_once(*, work_dir: Path, secrets_dir: Path, worker_id: str, dry_run: bool) -> bool:
    conn = connect()
    try:
        claim = claim_task(conn, worker_id)
        if claim is None:
            return False
        task_id = claim["task_id"]
        payload_path = work_dir / task_id / "payload.json"
        if not payload_path.is_file():
            finish_task(
                conn,
                task_id,
                status=STATUS_FAILED,
                summary=None,
                error={"code": "PAYLOAD_MISSING", "message": "ingestion payload not found"},
            )
            return True
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        try:
            outcome = run_task(conn, payload, work_dir=work_dir, secrets_dir=secrets_dir, dry_run=dry_run)
        except Exception as exc:  # noqa: BLE001 - the runner must never crash the loop
            log("ingestion task crashed", task_id=task_id, error=str(exc)[:400])
            finish_task(
                conn,
                task_id,
                status=STATUS_FAILED,
                summary=None,
                error={"code": "INGESTION_CRASHED", "message": str(exc)[:400]},
            )
            return True
        finish_task(
            conn,
            task_id,
            status=outcome["status"],
            summary=outcome["summary"],
            error=outcome["error"],
        )
        log("task finalized", task_id=task_id, status=outcome["status"])
        return True
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-Native ingestion runner")
    parser.add_argument("--once", action="store_true", help="process at most one task and exit")
    parser.add_argument("--dry-run", action="store_true", help="validate the recipe without ingesting")
    parser.add_argument("--work-dir", default=os.environ.get("AIND_INGESTION_WORK_DIR", DEFAULT_WORK_DIR))
    parser.add_argument("--secrets-dir", default=os.environ.get("AIND_SECRETS_DIR", DEFAULT_SECRETS_DIR))
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()

    work_dir = Path(args.work_dir)
    secrets_dir = Path(args.secrets_dir)
    worker_id = f"iw-{os.environ.get('HOSTNAME', 'local')}-{os.getpid()}"
    log("ingestion runner started", worker_id=worker_id, work_dir=str(work_dir), dry_run=args.dry_run)

    while True:
        try:
            processed = process_once(
                work_dir=work_dir,
                secrets_dir=secrets_dir,
                worker_id=worker_id,
                dry_run=args.dry_run,
            )
        except psycopg2.Error as exc:
            log("database error", error=str(exc)[:300])
            processed = False
        if args.once:
            return 0
        if not processed:
            time.sleep(args.poll_seconds)


if __name__ == "__main__":
    sys.exit(main())
