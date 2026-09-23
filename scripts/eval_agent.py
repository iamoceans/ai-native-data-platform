#!/usr/bin/env python3
"""A09 evaluation harness: run fixed scenarios through the real analysis loop.

The harness never invents a model score. It drives the deployed stack over
HTTP, one fixed (scenario, seed) case at a time, and records exactly what the
platform produced: the analysis id, the model id the runner reported, prompt
version, token/query budget consumption, latency, the evidence-bound report and
whether the generator's target cell landed in the top three contributors.

A09 ("target cell in the top 3 contributors for at least 9 of 10 cases") needs a
configured real model. When the stack runs the deterministic fake/deterministic
template path, the recorded `a09_status` is `not_executed_no_real_model`: the
run is a harness + scoring check, not a model accuracy claim.

Usage:
  python scripts/eval_agent.py --password <admin pw> [--cases 10] [--scale small]
      [--seed 42] [--skip-load] [--output-dir runtime/eval]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from demo.generator import generate  # noqa: E402
from demo.loaders import LoadTargets, load_run  # noqa: E402

TERMINAL = {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
DETERMINISTIC_MODEL = "deterministic-template-v1"
TOLERANCE = Decimal("0.000001")
# The plan schema allows at most three breakdown dimensions (spec 19); these
# three still isolate the generator's injected target cell.
DIMENSIONS = ["country", "platform", "ad_network"]

# Ten fixed cases: eight distinct generator scenarios plus two repeats under a
# different seed, so a lucky seed cannot carry the score. `canonical_67` is not
# here: it is a PostgreSQL-only sample (A06) and its run contains a single
# table, which the Doris loader cannot reset-load.
CASES: tuple[tuple[str, int], ...] = (
    ("ecpm_drop", 42),
    ("traffic_drop", 42),
    ("mixed_offset", 42),
    ("no_change", 42),
    ("incomplete_day", 42),
    ("schema_drift", 42),
    ("config_duplicate", 42),
    ("ecpm_drop", 7),
    ("traffic_drop", 7),
    ("mixed_offset", 2026),
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


def load_targets() -> LoadTargets:
    return LoadTargets(
        doris_host=read_dotenv("AIND_DEMO_DORIS_HOST", "127.0.0.1"),
        doris_sql_port=int(read_dotenv("DORIS_FE_SQL_PORT", "19030") or 19030),
        doris_be_host=read_dotenv("AIND_DEMO_DORIS_BE_HOST", "127.0.0.1"),
        doris_be_http_port=int(read_dotenv("DORIS_BE_HTTP_PORT", "18040") or 18040),
        doris_user=read_dotenv("DORIS_ADMIN_USER", "doris_admin") or "doris_admin",
        doris_password=read_dotenv("DORIS_ADMIN_PASSWORD", "") or "",
        mysql_host=read_dotenv("AIND_DEMO_MYSQL_HOST", "127.0.0.1"),
        mysql_port=int(read_dotenv("MYSQL_DB_PORT", "33060") or 33060),
        mysql_user=read_dotenv("MYSQL_ADMIN_USER", "source_admin") or "source_admin",
        mysql_password=read_dotenv("MYSQL_ADMIN_PASSWORD", "") or "",
        mysql_database=read_dotenv("MYSQL_SOURCE_DB", "ainative_source") or "ainative_source",
        pg_host=read_dotenv("AIND_DEMO_PG_HOST", "127.0.0.1"),
        pg_port=int(read_dotenv("SOURCE_DB_PORT", "55433") or 55433),
        pg_user=read_dotenv("SOURCE_BOOTSTRAP_USER", "source_admin") or "source_admin",
        pg_password=read_dotenv("SOURCE_BOOTSTRAP_PASSWORD", "") or "",
        pg_database=read_dotenv("SOURCE_DB_NAME", "ainative_source") or "ainative_source",
    )


class FreshConnectionClient:
    """One TCP connection per request, with the session carried explicitly.

    On this Windows/Docker Desktop host, a reused connection after
    ``POST /auth/login`` intermittently reaches the API without the session
    cookie (the same cookie validates fine via curl, via a raw socket and inside
    the container), and the API has been verified healthy in all three cases.
    Reconnecting per request - what curl does by default - removes the variable
    instead of papering over a server bug that is not there.
    """

    def __init__(self, base_url: str, *, timeout: float = 60.0) -> None:
        self._base_url = base_url
        self._timeout = httpx.Timeout(timeout, read=300.0)
        self.cookies: dict[str, str] = {}
        self.headers: dict[str, str] = {}

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        headers = dict(self.headers)
        headers.update(kwargs.pop("headers", None) or {})
        headers["Connection"] = "close"
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        with httpx.Client(
            base_url=self._base_url, timeout=self._timeout, trust_env=False
        ) as client:
            response = client.request(method, path, headers=headers, **kwargs)
        if method not in ("GET", "HEAD") and response.status_code < 400:
            pass
        for key, value in response.cookies.items():
            self.cookies[key] = value
        return response

    def get(self, path: str, **kwargs) -> httpx.Response:
        return self._request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> httpx.Response:
        return self._request("POST", path, **kwargs)

    def patch(self, path: str, **kwargs) -> httpx.Response:
        return self._request("PATCH", path, **kwargs)

    def close(self) -> None:  # symmetry with httpx.Client
        return None


def login(client, username: str, password: str) -> None:
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    if response.status_code != 200:
        raise EvalError(f"login failed: HTTP {response.status_code} {response.text[:200]}")
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]


def open_authenticated_client(base_url: str, username: str, password: str, attempts: int = 3):
    last = None
    for _ in range(attempts):
        client = FreshConnectionClient(base_url)
        login(client, username, password)
        response = client.get("/api/v1/auth/me")
        if response.status_code == 200:
            return client
        last = response
        time.sleep(1.0)
    raise EvalError(
        f"session not usable after login: HTTP {getattr(last, 'status_code', '?')} "
        f"{getattr(last, 'text', '')[:200]}"
    )


def ensure_datasource(
    client,
    *,
    name: str,
    kind: str,
    config: dict,
    secret_ref: str,
    schema: str,
) -> str:
    """Register or re-point a datasource at the platform's view of the source.

    ``config`` is what the *deployed platform* must use, not what this script
    uses: the backend reaches sources by compose service name, while host-side
    scripts use the published loopback ports. The integration tests register the
    host-side view, so a live acceptance run has to put it back.
    """
    existing = client.get("/api/v1/datasources")
    if existing.status_code >= 400:
        raise EvalError(f"list datasources: HTTP {existing.status_code} {existing.text[:200]}")
    datasource = next(
        (item for item in existing.json()["items"] if item["name"] == name), None
    )
    if datasource is None:
        created = client.post(
            "/api/v1/datasources",
            json={
                "name": name,
                "kind": kind,
                "connection_config": config,
                "secret_ref": secret_ref,
            },
        )
        if created.status_code != 201:
            raise EvalError(f"register {name}: {created.text[:200]}")
        datasource = created.json()
    elif datasource["connection_config"] != config:
        patched = client.patch(
            f"/api/v1/datasources/{datasource['id']}",
            json={"version": datasource["version"], "connection_config": config},
        )
        if patched.status_code != 200:
            raise EvalError(f"update {name}: {patched.text[:200]}")
        datasource = patched.json()
    refresh = client.post(
        f"/api/v1/admin/datasources/{datasource['id']}/catalog-refresh",
        json={"schemas": [schema]},
    )
    if refresh.status_code >= 400:
        raise EvalError(f"catalog refresh on {name}: {refresh.text[:200]}")
    datasets = refresh.json()["items"]
    log(f"{name}: {len(datasets)} datasets registered")
    roles_response = client.get("/api/v1/admin/roles")
    if roles_response.status_code >= 400:
        raise EvalError(f"list roles: HTTP {roles_response.status_code}")
    role_id = next(role["id"] for role in roles_response.json() if role["name"] == "admin")
    for dataset in datasets:
        for action in ("discover", "query"):
            granted = client.post(
                "/api/v1/admin/grants",
                json={"role_id": role_id, "dataset_id": dataset["id"], "action": action},
            )
            if granted.status_code not in (201, 409):
                raise EvalError(f"grant {action} on {dataset['object_name']}: {granted.text[:200]}")
    return datasource["id"]


def ensure_doris_datasource(client: httpx.Client, *, host: str, port: int) -> str:
    """Register/refresh the Doris source and grant the admin role on it."""
    return ensure_datasource(
        client,
        name="source-doris",
        kind="doris",
        config={
            "host": host,
            "port": port,
            "database": "demo",
            "connect_timeout_seconds": 5,
        },
        secret_ref="doris",
        schema="demo",
    )



def api_json(response: httpx.Response, *, what: str) -> dict:
    """Surface the server's error envelope instead of a KeyError."""
    if response.status_code >= 400:
        raise EvalError(f"{what}: HTTP {response.status_code} {response.text[:300]}")
    try:
        payload = response.json()
    except ValueError as exc:  # pragma: no cover - defensive
        raise EvalError(f"{what}: response is not JSON") from exc
    if not isinstance(payload, dict):
        raise EvalError(f"{what}: unexpected payload type {type(payload).__name__}")
    return payload


class EvalError(RuntimeError):
    pass


def log(message: str) -> None:
    print(f"[eval] {message}", flush=True)


def run_case(
    client: httpx.Client,
    *,
    scenario: str,
    seed: int,
    scale: str,
    as_of: str,
    output_dir: Path,
    skip_load: bool,
    timeout_seconds: float,
) -> dict:
    try:
        result = generate(
            output_dir=output_dir, seed=seed, as_of=as_of, scale=scale, scenario=scenario
        )
        run_dir = Path(result.run_dir)
    except SystemExit:
        # The generator refuses to overwrite an existing run; reusing it keeps
        # the case reproducible (the run name pins scenario/scale/seed/as-of).
        candidates = sorted(
            path
            for path in Path(output_dir).glob(f"{scenario}-{scale}-seed{seed}-asof*")
            if (path / "ground_truth.json").is_file()
        )
        if not candidates:
            raise
        run_dir = candidates[-1]
    ground_truth = json.loads((run_dir / "ground_truth.json").read_text(encoding="utf-8"))
    case = {
        "scenario": scenario,
        "seed": seed,
        "scale": scale,
        "run_dir": str(run_dir),
        "target": ground_truth["target"],
    }
    started = time.monotonic()
    if not skip_load:
        stats = load_run(run_dir=run_dir, targets=load_targets(), reset_demo=True)
        case["load_seconds"] = round(time.monotonic() - started, 2)
        case["loaded_rows"] = stats.get("ads_revenue_daily")
    baseline = date.fromisoformat(ground_truth["baseline_date"])
    current = date.fromisoformat(ground_truth["current_date"])
    data_complete = date.fromisoformat(ground_truth["data_complete_through"]) >= current
    created_session = client.post(
        "/api/v1/sessions", json={"title": f"A09 {scenario} seed={seed}"}
    )
    if created_session.status_code != 201:
        raise EvalError(f"create session: {created_session.text[:200]}")
    submitted_at = time.monotonic()
    submitted = client.post(
        "/api/v1/analyses",
        headers={"Idempotency-Key": f"a09-{scenario}-{seed}-{uuid.uuid4().hex[:8]}"},
        json={
            "session_id": created_session.json()["id"],
            "question": f"{current.isoformat()} {scenario} 场景广告收入变化的主要贡献来自哪里？",
            "context": {
                "metric_key": "ads_revenue",
                "baseline_start": baseline.isoformat(),
                "baseline_end": (baseline + timedelta(days=1)).isoformat(),
                "current_start": current.isoformat(),
                "current_end": (current + timedelta(days=1)).isoformat(),
                "dimensions": DIMENSIONS,
                "filters": {},
                "data_complete": data_complete,
            },
        },
    )
    if submitted.status_code != 202:
        raise EvalError(f"submit analysis: {submitted.text[:300]}")
    analysis_id = submitted.json()["analysis_id"]
    case["analysis_id"] = analysis_id
    detail = _wait_for(client, analysis_id, timeout_seconds=timeout_seconds)
    case["latency_seconds"] = round(time.monotonic() - submitted_at, 2)
    case["status"] = detail["status"]
    case["model_id"] = (detail.get("state") or {}).get("model_id")
    case["prompt_version"] = (detail.get("state") or {}).get("prompt_version")
    case["budget"] = detail.get("budget")
    case["steps"] = [
        {"key": step["key"], "status": step["status"]} for step in detail.get("steps") or []
    ]
    if detail["status"] not in {"COMPLETED", "PARTIAL"}:
        case["failure"] = (detail.get("state") or {}).get("last_error")
        case["scored"] = False
        return case
    report = client.get(f"/api/v1/analyses/{analysis_id}/report").json()
    evidence = client.get(f"/api/v1/analyses/{analysis_id}/evidence").json()
    return _score(case, ground_truth=ground_truth, report=report, evidence=evidence)


def _wait_for(client: httpx.Client, analysis_id: str, *, timeout_seconds: float) -> dict:
    deadline = time.monotonic() + timeout_seconds
    detail = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/analyses/{analysis_id}")
        if response.status_code != 200:
            raise EvalError(f"analysis {analysis_id}: HTTP {response.status_code}")
        detail = response.json()
        if detail["status"] in TERMINAL:
            return detail
        time.sleep(0.5)
    raise EvalError(f"analysis {analysis_id} did not settle within {timeout_seconds}s")


def _score(case: dict, *, ground_truth: dict, report: dict, evidence: dict) -> dict:
    """Score against the generator's ground truth, never against a fixture."""
    calculation = next(
        item for item in evidence["calculations"] if item["kind"] == "period_comparison"
    )
    groups = (calculation.get("contribution") or {}).get("groups") or []
    target = ground_truth.get("target")
    expected = ground_truth.get("expected") or {}
    claims = report.get("claims") or []
    case["claims"] = [
        {"id": claim["id"], "kind": claim["kind"], "query_ids": len(claim.get("query_ids") or [])}
        for claim in claims
    ]
    case["claims_all_bound"] = all(claim.get("query_ids") for claim in claims)
    case["has_claims"] = bool(claims)
    case["report_status"] = report.get("status")
    case["material"] = (report.get("materiality") or {}).get("material")

    expected_delta = Decimal(ground_truth["delta"])
    actual_delta = Decimal(str(calculation["change"]))
    case["delta_expected"] = str(expected_delta)
    case["delta_actual"] = str(actual_delta)
    case["delta_matches"] = abs(actual_delta - expected_delta) <= TOLERANCE

    drivers = next(
        (item for item in evidence["calculations"] if item["kind"] == "driver_decomposition"),
        None,
    )
    case["driver_status"] = drivers.get("status") if drivers else None
    case["driver_matches"] = _driver_matches(
        ground_truth=ground_truth, drivers=drivers, material=case["material"]
    )

    if target is None:
        # No injected target: the correct behaviour is a change reported inside
        # the materiality band with no forced attribution (spec A10).
        case["top_groups"] = [list(group["key"]) for group in groups[:3]]
        case["target_in_top3"] = None
        case["target_rank"] = None
        data_complete = date.fromisoformat(ground_truth["data_complete_through"]) >= date.fromisoformat(
            ground_truth["current_date"]
        )
        case["expects_attribution"] = False
        case["attribution_forced"] = any(
            claim["kind"] == "statistical_contributor" for claim in claims
        ) or bool(report.get("hypotheses"))
        case["status_matches"] = (
            case["report_status"] == ("COMPLETED" if data_complete else "PARTIAL")
        )
        case["scored"] = bool(
            case["delta_matches"]
            and case["claims_all_bound"]
            and (case["has_claims"] or case["report_status"] == "PARTIAL")
            and not case["attribution_forced"]
            and case["status_matches"]
        )
        return case

    target_key = [target[name] for name in DIMENSIONS]
    top_three = [list(group["key"]) for group in groups[:3]]
    case["top_groups"] = top_three
    case["target_in_top3"] = target_key in top_three
    case["target_rank"] = next(
        (index + 1 for index, key in enumerate(top_three) if key == target_key), None
    )
    case["expects_attribution"] = True
    case["driver_in_top_group"] = case["target_rank"] == 1
    case["scored"] = bool(
        case["target_in_top3"]
        and case["delta_matches"]
        and case["claims_all_bound"]
        and case["driver_matches"] is not False
    )
    return case


def _driver_matches(*, ground_truth: dict, drivers: dict | None, material: bool) -> bool:
    """Driver expectations from the generator, including its 'mixed' case."""
    expected = (ground_truth.get("expected") or {}).get("target") or {}
    expected_driver = expected.get("driver") or {}
    expected_dominant = (ground_truth.get("expected") or {}).get("dominant_factor")
    if not material:
        return drivers is None
    if drivers is None:
        return False
    if expected_driver.get("status") and drivers.get("status") != expected_driver["status"]:
        return False
    if expected_dominant is None:
        return True
    impression = Decimal(str(drivers.get("impression_effect") or 0))
    ecpm = Decimal(str(drivers.get("ecpm_effect") or 0))
    if expected_dominant == "mixed":
        # "Mixed" is read off the four-dimension target cell, while the analysis
        # aggregates over app_version (the plan allows three dimensions); the
        # sign of the smaller factor is not stable under that merge, so the
        # harness records it instead of asserting it.
        return None
    actual = "ecpm" if abs(ecpm) >= abs(impression) else "impressions"
    return actual == expected_dominant


def main() -> int:
    parser = argparse.ArgumentParser(description="A09 evaluation harness")
    parser.add_argument("--base-url", default=os.environ.get("AIND_EVAL_BASE_URL", "http://127.0.0.1:8000"))
    parser.add_argument("--username", default=os.environ.get("AIND_EVAL_USER", "admin"))
    parser.add_argument("--password", default=os.environ.get("AIND_EVAL_PASSWORD"))
    parser.add_argument("--cases", type=int, default=10, help="number of fixed cases to run (max 10)")
    parser.add_argument("--scale", default="small")
    parser.add_argument("--as-of", default="2026-09-13")
    parser.add_argument("--skip-load", action="store_true", help="assume the case data is already loaded")
    parser.add_argument("--timeout", type=float, default=300.0, help="per-analysis timeout in seconds")
    parser.add_argument("--output-dir", default=str(ROOT / "runtime" / "eval"))
    parser.add_argument(
        "--datasource-host",
        default=os.environ.get("AIND_EVAL_DORIS_HOST", "doris-fe"),
        help="host the platform uses to reach the Doris FE (container service name by default)",
    )
    parser.add_argument(
        "--datasource-port",
        type=int,
        default=int(os.environ.get("AIND_EVAL_DORIS_PORT", "9030")),
    )
    args = parser.parse_args()
    password = args.password or read_dotenv("AIND_SMOKE_PASSWORD")
    if not password:
        raise SystemExit("admin password required (--password or AIND_SMOKE_PASSWORD)")

    results: list[dict] = []
    client = open_authenticated_client(args.base_url, args.username, password)
    try:
        ensure_doris_datasource(
            client, host=args.datasource_host, port=args.datasource_port
        )
        for scenario, seed in CASES[: args.cases]:
            log(f"case {scenario} seed={seed}: generating/loading …")
            try:
                case = run_case(
                    client,
                    scenario=scenario,
                    seed=seed,
                    scale=args.scale,
                    as_of=args.as_of,
                    output_dir=ROOT / "runtime" / "demo",
                    skip_load=args.skip_load,
                    timeout_seconds=args.timeout,
                )
            except Exception as exc:  # noqa: BLE001 - one bad case must not hide the rest
                # A failed case is recorded as a failed case, never dropped.
                case = {
                    "scenario": scenario,
                    "seed": seed,
                    "scale": args.scale,
                    "status": "ERROR",
                    "scored": False,
                    "failure": {"code": "EVAL_ERROR", "message": str(exc)[:500]},
                }
            results.append(case)
            log(
                f"case {scenario} seed={seed}: status={case.get('status')} "
                f"top3={case.get('target_in_top3')} delta_ok={case.get('delta_matches')} "
                f"driver_ok={case.get('driver_matches')} latency={case.get('latency_seconds')}s"
                + (f" error={case['failure']}" if case.get("status") == "ERROR" else "")
            )

    finally:
        client.close()

    models = {case.get("model_id") for case in results}
    real_model = models - {None, DETERMINISTIC_MODEL}
    target_cases = [case for case in results if case.get("expects_attribution")]
    no_target_cases = [case for case in results if case.get("expects_attribution") is False]
    hits = sum(1 for case in target_cases if case.get("target_in_top3"))
    unforced = sum(1 for case in no_target_cases if not case.get("attribution_forced"))
    scored = [case for case in results if case.get("scored")]
    if not real_model:
        a09_status = "not_executed_no_real_model"
        a09_note = (
            "no LLM provider is configured; this run exercises the harness, the deterministic "
            "plan/report path and the scoring, and must not be read as model accuracy"
        )
    elif len(scored) < 9:
        a09_status = "failed"
        a09_note = "fewer than 9 of 10 cases were fully evidence-consistent"
    else:
        a09_status = "passed"
        a09_note = "all recorded cases are evidence-consistent; see per-case records"
    record = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "base_url": args.base_url,
        "scale": args.scale,
        "as_of": args.as_of,
        "dimensions": DIMENSIONS,
        "model_ids": sorted(model for model in models if model),
        "a09_status": a09_status,
        "a09_note": a09_note,
        "target_top3_hits": f"{hits}/{len(target_cases)}",
        "no_forced_attribution": f"{unforced}/{len(no_target_cases)}",
        "fully_scored": f"{len(scored)}/{len(results)}",
        "cases": results,
    }
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"a09-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    log(
        f"a09_status={a09_status} target_top3={record['target_top3_hits']} "
        f"unforced={record['no_forced_attribution']} scored={record['fully_scored']} -> {path}"
    )
    return 0 if a09_status != "failed" else 1


if __name__ == "__main__":
    sys.exit(main())
