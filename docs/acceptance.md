# Acceptance record (M0-M6)

Evidence below was produced on the reference machine: M0-M2 on 2026-09-14,
M3/M4 on 2026-09-16. Anything not listed as "executed" is **not executed** and
must not be reported as passing. Commands are reproducible from the repository
root (Windows PowerShell for the recorded runs; the Makefile mirrors them for
WSL2/Linux).

## Environment under test

- Docker Desktop 28.3.2 (WSL2 backend), Compose v2.38.2-desktop.1
- `postgres:16.15` for the control and source databases (separate containers and volumes)
- `mysql:8.4.11` source (M2), `apache/doris:fe-3.1.4` + `be-3.1.4` (M2)
- Backend image built from `python:3.11.15-slim-bookworm`, frontend served by `nginx:1.29.8-alpine`
- DataHub v1.7.0.1 quickstart stack (GMS/frontend/OpenSearch/Kafka/MySQL/actions), loopback-only ports
- Ingestion image `ainative-ingestion:0.1.0` (pinned official ingestion image + platform runner/SDK publisher)
- Tests executed from the Windows host against the compose databases and stack;
  DataHub-dependent tests ran against the containerized platform API at 127.0.0.1:8000

## What was executed

| # | Check | Command | Result |
|---|---|---|---|
| 1 | Control schema matches the spec DDL | `uv run --frozen python scripts/verify_schema.py` | OK: 27 tables, 4 named state checks, 17 indexes |
| 2 | Unit + security + contract suites | `uv run --frozen pytest tests/unit tests/security tests/contract -q` | **171 passed** |
| 3 | Integration suites (PostgreSQL + MySQL + Doris) | `uv run --frozen pytest tests/integration -q` | **58 passed** (38 PG + 7 MySQL + 10 Doris + 3 API corpus) |
| 4 | Core stack end-to-end | `scripts/smoke_core.py --demo` | **all checks passed** (PostgreSQL) |
| 5 | Browser E2E (Playwright/Chromium) | `E2E_BASE_URL=http://127.0.0.1:3000 npx playwright test` | **2 passed**: login -> run query -> results -> history; forbidden write shows `SQL_FORBIDDEN` |
| 6 | Doctor / host report | `uv run --frozen python scripts/doctor.py` | executed; report captured in `docs/compatibility.md` |
| 7 | Frontend type generation + build | `make api-spec types`, `npm run build` | executed; `src/api/schema.d.ts` generated, production build succeeded |
| 8 | Three-source seed | `uv run --frozen python scripts/seed_sources.py` | executed: MySQL 6 + 200k heavy rows; Doris 648/216/72/2 rows across 4 tables |

### Detail: integration test coverage (38 tests)

| File | Focus | Tests |
|---|---|---|
| `test_auth_and_admin.py` | login/logout/me, CSRF, Origin, rate limit, RBAC boundaries, audits, health | 10 |
| `test_query_flow.py` | full loop + independent recomputation, parameters, pagination (25 pages), truncation, idempotency, permission denial, cross-user invisibility, SSRF/unsupported kind rejection, schema endpoint | 10 |
| `test_cancel_timeout.py` | cancel a running query, source timeout, cancel-after-completion no-op | 3 |
| `test_recovery.py` | queue timeout, lost lease + fencing, single-claim, per-user/datasource/global capacity, TTL expiry and cleanup | 7 |
| `test_sse.py` | replay by Last-Event-ID, live stream, revocation closes stream and blocks results, 404 | 4 |
| `test_source_permissions.py` | SELECT-only role blocks writes (`42501`), read-only session (`25006`), sensitive column and view policy, confirmed secure view queryable | 4 |
| `test_mysql_source.py` (M2) | connection/capabilities, schema, query recomputation, parameter binding + literal `%`, cancel (`1317`), timeout (`3024`), write rejection (`1792`/`1142`), audit | 7 |
| `test_doris_source.py` (M2) | connection/capabilities, catalog, type mapping (LARGEINT/DECIMAL), query recomputation, cancel, timeout (`query_timeout`), EXPLAIN, write rejection, external catalog rejected | 10 |
| `test_attack_corpus_api.py` (M2) | 14 forbidden statements rejected with no job created, on all three engines | 3 |
| `test_datahub_metadata.py` (M3) | full-stack register -> refresh (secure view) -> real ingestion -> URN mapping -> permission-filtered search -> DataHub context -> admin-only deep link -> lineage edges -> permission-filtered lineage; failed ingestion keeps the previous URN mapping | 2 |
| `test_analysis_sql.py` (M4) | canonical_67 through real SQL (A06), driver decomposition on the loaded demo (A07), cross-source as-of join + duplicate refusal (A14) | 3 |

### Detail: security corpus (26 cases in `tests/security`)

Multi-statement, write CTE, `SELECT INTO`, executable comments, user functions,
recursive CTE, unauthorized subquery, system tables, `information_schema`, cross-database
reference, `dblink`, locking read, session commands, `SHOW`, `CROSS JOIN`, `pg_sleep`,
file functions, `COPY ... PROGRAM`, unknown-function default-deny, setting reads,
`UPDATE`, DDL, `DROP`, comma joins with CTE, case-sensitive quoted identifiers.
All rejected with the expected error codes.

## M3 evidence - DataHub (executed 2026-09-16)

```powershell
# static suites (unit + security + contract, no services)
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# -> 223 passed  (includes 25 contract tests, 9 of them replaying real
#    DataHub responses captured from the pinned v1.7.0.1 stack)

# full integration matrix (PG + MySQL + Doris + DataHub full stack running,
# compose query/agent workers stopped)
uv run --project backend --frozen pytest backend/tests/integration -m integration -q
# -> 63 passed (incl. 2 DataHub full-stack tests and 3 M4 SQL acceptance tests)

# catalog + context + lineage over the deployed stack
uv run --project backend --frozen python scripts/metadata_sync.py
# -> postgres mapped 4/4, mysql 2/2, doris 8/8 (incl. the secure view)
uv run --project backend --frozen python scripts/verify_metadata.py
# -> 14/14 datasets SYNCED with a DataHub URN; context source=datahub,
#    stale=false; semantic grain/currency/metric keys round-tripped;
#    lineage sample (ads_revenue_by_country): status=available, nodes=1;
#    metrics: ads_revenue, dau, ecpm, iap_revenue, impressions, new_users, total_revenue
```

Recorded lineage facts (all through `GET /api/v1/datasets/{id}/lineage`):

| Edge | Source | Status | Label |
|---|---|---|---|
| `ads_revenue_by_country` (view) <- `ads_revenue_daily` (table) | Doris connector, `SHOW_VIEW_PRIV` + `include_view_lineage` | available | `extracted` |
| `revenue_daily_total` <- `ads_revenue_daily` | declared demo pipeline (DataHub SDK, recorded SQL hash) | available | `declared_by_demo_pipeline` |
| `revenue_daily_total` <- `iap_revenue_daily` | declared demo pipeline | available | `declared_by_demo_pipeline` |

## M4 evidence - demo generator and analysis kernel (executed 2026-09-16)

```powershell
# generate + verify every scenario (offline, deterministic)
foreach ($s in canonical_67, ecpm_drop, traffic_drop, mixed_offset, no_change,
               incomplete_day, config_duplicate, schema_drift) {
  uv run --project backend --frozen python scripts/demo_generate.py --scenario $s --scale small --force
  uv run --project backend --frozen python scripts/demo_verify.py --run-dir "runtime/demo/$s-small-seed42-asof2026-09-13"
}
# -> demo-verify: all checks passed for all 8 scenarios
#    (canonical_67: total 10,000 -> 9,000, target -670, share 0.67,
#     contribution -6.7 pp, impression effect 0, eCPM effect -670, others -330)

# determinism: regenerate the same run twice -> identical manifest hashes
# -> ads_revenue_daily.csv sha256 unchanged across runs (42/2026-09-13/small/ecpm_drop)

# load into the real sources (Doris Stream Load + config tables + derived table)
uv run --project backend --frozen python scripts/demo_load.py --run-dir "runtime/demo/ecpm_drop-medium-..." --reset-demo
# -> ads 864,000 / iap 43,200 / user 8,640 / retention 8,640 / campaign 1,488
#    derived revenue_daily_total 43,200; PG campaign_config 6; MySQL app_release_config 6
```

A17 resource report (medium scale, reference host):

| Measurement | Value |
|---|---|
| `demo-generate` wall time | 17.3 s (94.5 s when re-run under `tracemalloc` for the memory figure) |
| Generator output | 53.9 MiB on disk (CSV + Parquet), 927,456 rows across fact/dimension tables |
| Python-tracked peak allocations during generation | 22.0 MiB (`tracemalloc` peak) |
| `demo-load` into Doris (Stream Load + transform) | 4.6 s |
| Doris BE storage after load | 97 MiB (`du -sh` in the BE container) |
| Timed aggregates through the platform API | country 7-day aggregate (48 rows): 0.50 / 1.17 / 1.17 s (min/median/max of 3 runs); 90-day daily totals (90 rows): 1.03 / 1.16 / 1.30 s — within the 5 s target |

## Spec acceptance matrix (section 30) - status per item

| # | Item | Status | Notes / evidence |
|---|---|---|---|
| A01 | Three-source connectors | **passed** (M2) | PostgreSQL, MySQL 8.4.11 and Doris 3.1.4: connection, schema, query, types, timeout and cancel each executed; see the two M2 test files |
| A02 | DataHub metadata | **passed** (M3) | Three sources ingested (Doris 8/8 incl. the confirmed secure view, PostgreSQL 4/4, MySQL 2/2); search/schema/description served from DataHub; a real `extracted` view->table edge and two `declared_by_demo_pipeline` demo edges verified through the API |
| A03 | SQL safety corpus + engines | **passed** | 14-statement corpus rejected on all three engines through the API + engine-specific validator corpus (Doris dialect, MySQL dialect) |
| A04 | Permissions across users/roles | **passed, request workflow included** (2026-09-25) | SQL, catalog, results, SSE, context and lineage are permission filtered, with the admin-only DataHub deep link and `permission_filtered` lineage verified in M3. The access-request workflow is now closed end to end: a requester files a request and sees only their own (`GET /permission-requests`), an administrator approves it into a **real grant** (discover implied by query, policy revision bumped) or rejects it (creating nothing), and the mock state stays a label that provably grants nothing. Verified live: viewer 403 before approval -> request -> approve -> viewer query SUCCEEDED, plus 2 integration tests and the browser journey |
| A05 | Read-only account blocks writes | **passed** | PG `42501` + `25006`; MySQL `1792` + `1142`; Doris SELECT_PRIV "denied" |
| A06 | Canonical 67 numerical check | **passed** (M4) | Loaded into the source PostgreSQL and queried **through the Query Gateway**: total 10,000 -> 9,000 (delta -1,000, change_pct -0.1); target -670, net decline share 0.67, contribution -6.7 pp; `test_analysis_sql.py` + canonical_67 generator scenario |
| A07 | Factor decomposition | **passed** (M4) | Symmetric impression x eCPM split; unit tests assert the 1e-6 USD tolerance over random Decimal inputs; live check on the loaded Doris demo reproduces it for the scenario target |
| A08 | Additive groups | **passed** (M4) | Group deltas must sum to the parent delta (mismatch raises `GROUP_SUM_MISMATCH`); NULL bucket and zero-fill covered by unit tests; demo-verify asserts the partition sum for every scenario |
| A09 | Real-agent accuracy | **passed** (2026-09-27) | `scripts/eval_agent.py` + `make eval-agent` run ten fixed (scenario, seed) cases through the deployed loop and score them against the generator's ground truth (model id, prompt version, tokens, latency, per-case failure recorded). Executed against DeepSeek (`AIND_LLM_MODEL=deepseek-chat`, which the endpoint serves as `deepseek-flash`) on the full profile (MySQL 8.4.11 + Doris 3.1.4, DataHub not started): `a09_status=passed`, target-in-top3 **7/7** target cases (six ranked 1st, `schema_drift` 3rd), no forced attribution **3/3** no-target cases, evidence-consistent **10/10**, fully scored **10/10**, 9 COMPLETED + 1 PARTIAL (`incomplete_day`, A10's expected outcome), 2,169 input / 140 output tokens over 34 gateway queries, per-case latency 2.19-5.98 s, prompt version `m5-v1` (`runtime/eval/a09-20260927-224114.json`). The earlier 2026-09-19 record was the deterministic baseline `not_executed_no_real_model` with the same 7/7 - 3/3 - 10/10 |
| A10 | No-anomaly / incomplete-day handling | **passed** (M5, 2026-09-19) | Generator/kernel paths still pass; the runner now applies a documented 0.5% materiality band, so a change inside it is reported as immaterial with no contributor claim and no hypotheses. Verified through the deployed stack by the A09 harness: `no_change` and `config_duplicate` end COMPLETED with no forced attribution, `incomplete_day` ends PARTIAL without numeric claims (3/3 no-target cases) |
| A11 | Cancel on three engines | **passed** (M2) | PostgreSQL `57014`-based, MySQL `KILL QUERY` (1317), Doris `KILL QUERY` ("cancel query by user"); timeouts verified per engine |
| A12 | Restart recovery | **passed** (live, 2026-09-23) | `scripts/verify_a12_worker_kill.py` pauses the real `query-worker` container mid-execution: the lease goes ACTIVE -> SUSPECT (reconciler) -> LOST after the grace window, the terminal code is `QUERY_LOST`, nothing is published, and after the frozen worker resumes the fencing token keeps the job LOST with zero result rows. The M5 runner also re-enters any phase after a claim loss (EXECUTING re-reads the query set, OBSERVING re-decides, SYNTHESIZING rewrites artifacts idempotently by content hash) |
| A13 | Idempotency + concurrency | **passed** (M1/M2) | idempotent submit, single claim, capacity caps verified; three engines share the same scheduler; ingestion concurrency (409 for a second active sync) verified in M3 |
| A14 | Cross-source joins | **passed** (M4) | Whitelisted as-of join (Doris cohorts + PostgreSQL configuration) executed through two real query results: amounts preserved, unmatched rate reported; a duplicate overlapping configuration raises `JOIN_CARDINALITY_VIOLATION` instead of double counting |
| A15 | SSE reconnect/expiry/revocation | **passed** (live, 2026-09-23) | replay, revocation and cursor expiry verified in the integration suite, plus the live drop test `scripts/verify_a15_sse_drop.py`: the client resets TCP mid-stream (SO_LINGER 0), the query finishes while nobody listens, and the reconnect with `Last-Event-ID` resumes at the next id with no gap and no duplicate, delivering the terminal event |
| A16 | Full UI journey | **passed for the specified routes** (2026-09-24) | login -> SQL -> results -> history, analysis -> chart -> table view -> drilldown, the two administration routes (`/admin/datasources`, `/admin/permissions`) and the capability gate; Playwright **5 journeys passed** 2026-09-24. The spec's `/datasets/:id` deep link is served by the catalog detail panel instead of its own route; shared/dashboard screens do not exist in the V1 spec and were not invented |
| A17 | Medium dataset resource report | **passed, recorded** (M4) | Medium run generated (927,456 rows, 53.9 MiB, 17.3 s), loaded into Doris in 4.6 s, BE storage 97 MiB; timed platform aggregates 0.50-1.30 s (target < 5 s); generator peak tracked allocations 22.0 MiB |
| A18 | Reproducible from blank volumes | **passed, DataHub included** (2026-09-24) | Business stack: literal blank-volume run (`down -v` -> up -> migrate -> bootstrap -> schema check -> seed -> smoke -> demo load/verify -> analysis with driver decomposition and chart). DataHub part, rerun 2026-09-24 against the pinned v1.7.0.1 stack: three-source ingestion mapped **13/13** datasets (PG 4/4, MySQL 2/2, Doris 8/8 including the administrator-confirmed secure view), `verify_metadata.py` all checks passed (context `source=datahub`, `metadata_stale=false`, semantic round-trip), a real `extracted` view->table edge, and two `declared_by_demo_pipeline` edges published through the DataHub SDK and read back by the platform. Honest caveat: the declared edges needed ~10 minutes before the DataHub search index answered them, so the lineage endpoint reported `no_upstream` during that window (see compatibility.md) |

## Skipped or unverified (explicit)

- Spark/Hive preview (2026-09-28): `uv run --project backend --frozen pytest
  backend/tests/unit backend/tests/security backend/tests/contract -q -p
  no:cacheprovider` reported **275 passed** after the connector tests, before
  the lineage visibility regression test was added; that focused test reported
  **1 passed**. `npm.cmd run build` passed. These are static results only. The
  normal `docker` shim was denied by the host, but the installed Docker CLI was
  able to reach daemon 28.3.2 with elevated sandbox permission. The existing
  `ainative-ingestion:0.1.0` image reports DataHub CLI `1.7.0.1+docker` and
  `datahub check plugins --source hive-metastore` reported **enabled**. An
  isolated `datahub ingest --dry-run` with a file sink accepted the Spark/HMS
  recipe fields and reported **Source configured successfully**; it then failed
  as expected when connecting to the deliberately absent HMS endpoint
  `127.0.0.1:9`. Stateful ingestion with the real REST sink, real Spark/HMS
  service, URN mapping, query, cancellation, Spark listener lineage, migration
  and E2E are not counted as passing. Final rerun after the lineage
  visibility guard and provider protocol test: **277 passed, 2 dependency
  deprecation warnings** with the same command. `uv run --project backend
  --frozen alembic heads` reported `e9c5b2310d42 (head)`; the migration was
  not applied to a live control database. OpenAPI export from `backend` and
  `npm.cmd run build` completed successfully.

- DataHub authentication-enabled mode: the pinned quickstart runs with GMS auth
  disabled, so `scripts/datahub_token.py --token` is documented as best-effort
  and was not executed against an auth-enabled GMS.
- DataHub profiling: disabled in the recipes by policy; not verified.
- MySQL `KILL QUERY` for other users' connections (needs PROCESS) is out of scope;
  the platform only kills its own dedicated connections.
- Doris multi-BE topologies, workload groups and external catalogs are out of V1 scope.
- A09 was executed against a real model on 2026-09-27 (DeepSeek), so the
  "no real LLM provider was configured" caveat that stood here is closed. The
  harness still records what happened, including the model the endpoint actually
  served (`deepseek-flash` for the requested `deepseek-chat`). Reaching it needed
  one adapter fix: the OpenAI-compatible adapter sent
  `response_format: {"type": "json_schema"}`, an OpenAI structured-outputs feature
  that DeepSeek rejects with HTTP 400 (`This response_format type is unavailable
  now`). The portable `json_object` mode is now the default
  (`AIND_LLM_RESPONSE_FORMAT`): the schema travels in the prompt and the reply is
  validated against it locally, with `json_schema` kept for providers that
  implement strict structured outputs. A reply that does not validate fails that
  analysis loudly - there is no format-repair loop, only the SQL repair budget.

- 2026-09-27 real-model run inputs, for reproducibility: stack `compose.yaml +
  compose.dev.yaml --profile full` (MySQL 8.4.11 + Doris 3.1.4, DataHub not
  started, `AIND_DATAHUB_ENABLED=0`), backend image rebuilt from the working tree
  so the metric registry matches `metadata/` (10 definitions), datasources
  re-aligned with `scripts/align_sources.py`, then
  `python scripts/eval_agent.py --cases 10 --scale small`.
- The full three-engine matrix was rerun on 2026-09-19 (67 passed, 1 skipped);
  the skipped case is the DataHub full-stack test, which needs the DataHub stack
  up (`make datahub-up`). DataHub-backed behaviour is still covered by its own
  M3 evidence and the GraphQL contract fixtures, but it is not re-verified in
  that run.
- Environment note: on this Windows/Docker Desktop host, an httpx client that
  reuses a connection after `POST /auth/login` is intermittently answered with
  401 even though the same cookie validates via curl, via a raw socket and
  inside the container. The reference scripts (`scripts/smoke_core.py`,
  `scripts/eval_agent.py`) therefore set `trust_env=False` and
  `Connection: close` / one connection per request; browsers are unaffected.
- `make` was not executed under WSL on this host; equivalent `scripts/dev.ps1`
  targets and direct commands were used.
- The M3/M4 integration tests that need the deployed stack skip when it is down;
  a skipped run is not a passed run (the recorded numbers above are from runs
  with the stack up).

## How to reproduce this record

```bash
make setup-secrets && make doctor
make up-core && make migrate && make bootstrap
# PostgreSQL-only suites
docker compose stop query-worker agent-worker
make test-core
docker compose start query-worker agent-worker

# three-engine matrix (full profile: builds/pulls MySQL + Doris)
AIND_DATAHUB_ENABLED=1 make up-full
make test-full             # runs the whole integration matrix incl. MySQL/Doris

# M3/M4 demo pipeline + metadata (DataHub stack up)
make demo-generate && make demo-load --reset-demo && make demo-verify
make metadata-sync && make demo-lineage
uv run --project backend --frozen python scripts/verify_metadata.py

# smoke + browser journey against the running stack
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<admin pw> uv run --project backend --frozen python scripts/smoke_core.py --demo
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<admin pw> uv run --project backend --frozen python scripts/smoke_core.py --demo --kind mysql
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<admin pw> uv run --project backend --frozen python scripts/smoke_core.py --demo --kind doris
cd frontend && E2E_BASE_URL=http://127.0.0.1:3000 E2E_ADMIN_PASSWORD=<admin pw> npx playwright test
```

After the A12/A15/A18 work (2026-09-23) the three-engine matrix was rerun from
the freshly seeded blank-volume state: **67 passed, 1 skipped** (the DataHub
full-stack test, DataHub not started), and both live acceptances passed.

After the approval workflow (2026-09-25): **243 static passed**, the three-engine
matrix reported **70 passed, 1 skipped**, and **5 Playwright journeys passed**
(the administration journey now files a request and approves it into a real
grant).

After the administration screens (2026-09-24): **242 static passed**, the
three-engine matrix reported **68 passed, 1 skipped** (one new integration test
covers the administrator request queue), and **5 Playwright journeys passed**
(2026-09-25: three consecutive runs, 5/5 each, after the E2E harness fixes
described in compatibility.md).
That journey pair also caught a real UI defect: signing out and signing in as
another account left the previous profile (and its capabilities) in the React
Query cache, so the shell could briefly render administration links for a
viewer. Sign-out and sign-in now clear the cache.

The last full-profile record (2026-09-16) is **223 static + 63 integration = 286
backend tests passed**, **2 Playwright journeys passed**, three engine smokes
passed, `verify_metadata.py` all checks passed (Doris 8/8, PostgreSQL 4/4,
MySQL 2/2 mapped), and `demo-verify` passed for all 8 generator scenarios.
After the M5 slice the records are (2026-09-19): **238 static passed** and
**45 PostgreSQL integration passed / 22 optional-service skipped** on the core
profile, plus the A09 harness run against the full profile (MySQL 8.4.11 +
Doris 3.1.4, DataHub not started): `a09_status=not_executed_no_real_model`,
target-in-top3 **7/7** target cases, no forced attribution **3/3** no-target
cases, evidence-consistent **10/10**
(`runtime/eval/a09-20260919-175727.json`).

The 2026-09-27 record adds the real-model run on the same full profile, after the
`json_object` adapter change: **248 static passed** (`pytest backend/tests/unit
backend/tests/security backend/tests/contract -q`) and `a09_status=passed` against
`deepseek-flash` - target-in-top3 **7/7**, no forced attribution **3/3**,
evidence-consistent **10/10**, 2,169 input / 140 output tokens, 34 gateway
queries, 2.19-5.98 s per case (`runtime/eval/a09-20260927-224114.json`). The
PostgreSQL integration profile and the full-profile *test matrix* have not been
rerun after the adapter change and are not represented as current.

## Business memory - the learning loop (executed 2026-09-28)

Added after the 2026-09-27 record: `business_memory` (migration
`a3f8c1d27b90`), the extraction/retrieval module `app/agent/memory.py`, the
worker hook, `GET /memory` + `POST /memory/{id}/confirm|reject`, and
`GET /analyses/{id}/memory`, plus the 业务记忆 panels on the Ask and analysis
screens.

```powershell
# static suites after the change
uv run --project backend --frozen pytest backend/tests/unit -m "not integration" -q
# -> 214 passed
uv run --project backend --frozen pytest backend/tests/security -m "not integration" -q
# -> 26 passed                       (240 static tests in total)
cd frontend; npx tsc -b --noEmit; npx vite build        # -> clean, dist built
# schema contract, against the migrated control DB
docker exec ainative-backend-1 python /app/scripts/verify_schema.py
# -> schema verification OK: 28 tables, 7 named state checks, 18 indexes
```

Live run on the deployed stack (control PostgreSQL + Doris, `deepseek-flash` via
`AIND_LLM_*`), two analyses of `ads_revenue` on 2026-09-11..12 vs 2026-09-12..13:

| Round | dimensions chosen by the model | memory recalled | statements learned | refused |
|---|---|---|---|---|
| 1 (cold) `c78da443` | country, platform, ad_network | 0 | 3 | 0 |
| 2 (warm) `49068d91` | country, platform, ad_network | 3 | 2 | 1 (`near_duplicate`) |

Round 1 learned, for example: "US android traffic on AppLovin is a notable down
segment for ads_revenue." Round 2's planner prompt carried that row (the API
reports it under `used`, `reuse_count` rose to 1) and produced
"US android traffic on AppLovin is a notable mover for ads revenue, alongside US
android on Mintegral and IronSource." Learning costs one model call per finished
analysis: 1,181/127 and 1,258/143 input/output tokens for the two runs above.

Curating from the UI was exercised on the same rows: `确认` moved a statement to
`已确认`, `否决` hid it from the working list (toggle `显示已否决`) and the store
reported `已确认 1 · 待确认 0 · 已否决 60` afterwards.

Two findings from the live run, both fixed before this record:

1. The first extraction pass wrote 60 paraphrases of the platform's own method
   ("decomposition describes correlation, not causation", "movement concentrates
   in a few segments"). The prompt now refuses method restatements, requires every
   statement to name a value from the analysis, and near-duplicates are rejected by
   a bigram-similarity check against the metric's whole history - those 60 rows
   were rejected through the curation path rather than deleted.
2. An extraction crash (a `logging` reserved key in the counter's `extra`) rolled
   back the write and the catch-up pass retried it every five seconds, calling the
   model each time. The worker now records a non-retryable attempt marker for
   unexpected failures and only retries provider errors, at most 3 times. The
   regression test pins the logging path at INFO level, which is the container's
   level and not pytest's default.

---

## Restart, Spark/Hive preview live run and a memory rerun (executed 2026-09-28)

The stack was rebuilt from the working tree (the backend image gained `impyla`
and `app/providers/spark.py`, the frontend bundle the Spark fields) and the new
migration was applied:

```powershell
docker compose -f compose.yaml --profile core up -d --build
docker compose -f compose.yaml run --rm --no-deps backend alembic upgrade head
# -> Running upgrade a3f8c1d27b90 -> e9c5b2310d42, Allow Spark Thrift Server datasource registrations
docker exec ainative-backend-1 python /app/scripts/verify_schema.py
# -> schema verification OK: 28 tables, 7 named state checks, 18 indexes
```

Two restart details cost time and are worth remembering: `up --build` left
`agent-worker` and `frontend` on their old images (they were recreated with
`--force-recreate --no-deps`), and the base compose file publishes no database
port, so host-side runs need `-f compose.dev.yaml` or `127.0.0.1:55430/55433`
refuse the connection.

### Static and integration suites

```powershell
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q --basetemp=runtime/pytest-tmp
# -> 277 passed, 2 warnings
# (without --basetemp pytest cannot create its tmpdir on this host:
#  PermissionError on %TEMP%\pytest-of-<user>)

# full matrix, compose workers stopped, MySQL + Doris + DataHub live:
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --basetemp=runtime/pytest-tmp
# -> 72 passed, 25 deselected, 2 warnings in 180.50s
```

Three environment preconditions each showed up as a failure before they were
fixed, so they are recorded rather than hidden: the database ports come from
`compose.dev.yaml`; `test_datahub_metadata` asserts the Doris datasource is
HEALTHY (a stopped Doris fails it, it does not skip); and
`test_a07_driver_decomposition_on_loaded_demo` assumes the loaded demo data is
the `ecpm_drop` scenario - with the `mixed_offset` run loaded it reported
`impression_effect=-231.256500`, which is mixed_offset's ground truth exactly,
against the test's 5%-of-delta band for an eCPM-only drop. Loading
`runtime/demo/ecpm_drop-small-seed42-asof2026-09-13` with
`scripts/demo_load.py --run-dir ... --reset-demo` restored the documented
scenario and the matrix went green.

### Business memory, rerun end to end (`runtime/accept_business_memory.py`)

Metric `ecpm` (no learned statements at the start), window 2026-09-10..11 vs
2026-09-11..12, `deepseek-flash`:

| Round | status | dimensions | recalled | learned | refused |
|---|---|---|---|---|---|
| 1 (cold) | COMPLETED | country, platform | 0 | 3 | 0 |
| 2 (warm) | COMPLETED | country, platform | 3 (reuse 1) | 3 | 0 |
| 3 (after curation) | COMPLETED | country, platform | 5 | 0 | 3 (`unverifiable_figure`) |

Round 1 learned statements such as "US android is a notable up segment for eCPM
in the demo.ads_revenue_daily dataset." Round 2's planner prompt carried all
three back (`memory_used_ids` in `state`, `reuse_count` 1). Curation through the
API moved one row to `confirmed` and one to `rejected`
(`POST /memory/{id}/confirm|reject`, audited); round 3 then recalled the
confirmed row and the three proposed rows with `reuse_count` 1/2/2/2 and did
**not** recall the rejected one - the store's counts after round 3 were
`{confirmed: 1, proposed: 4, rejected: 1}`.

Two findings from this rerun:

1. `memory.confirmed` / `memory.rejected` audit rows recorded `previous` as the
   *new* status (`{"previous": "rejected"}` on a reject), because `_curate` read
   `row.status` after `memory_repo.set_status` had mutated it in place.
   **Fixed 2026-09-29**: the route captures the previous status before the write,
   pinned by `test_curation_audit_records_the_previous_status`.
2. Round 3's extraction produced three otherwise-useful statements that were all
   refused because they named the analysis window ("...in the 2026-09-11
   country-by-platform breakdown") and the date is not an allowed identifier in
   the digest. The numeric guard is doing its job; whether the window belongs in
   `evidence_identifiers` is a design decision, not a bug.

### Spark/Hive preview live acceptance

Fixture: `infra/spark-preview/` (`apache/hive:3.1.3` metastore + `apache/spark:3.5.3`
Thrift Server, `NOSASL`, one shared warehouse volume, both services as root
because they share that volume). Spark 3.5.3's bundled Hive 2.3.9 client talks
to the Hive 3.1.3 metastore without extra jars. Data for the run: a table
created through the Hive CLI against the shared metastore (`demo.hive_orders`,
4 rows), a Spark-created table (`demo.spark_revenue`, 4 rows) and
`demo.slow_events` (2,000,000 rows) for the cancellation paths.

Verified against the running stack (`runtime/accept_spark.py`,
`runtime/acceptance/spark-live-*.json`):

- registration of kind `spark` with `metastore_host`/`metastore_port`; connection
  test `HEALTHY` (127 ms, `dialect=spark`, `cancel=True`);
- catalog refresh for schema `demo` registered 3 tables, including the one the
  Hive CLI created - the shared-metastore claim;
- default-deny holds: a `viewer` identity got `403 PERMISSION_DENIED` on
  `demo.hive_orders` until the administrator role was granted `query`;
- `SELECT` through the worker returned the expected aggregates from the
  Hive-created table and from the Spark-created table;
- rejections: `DROP TABLE` -> 403 `SQL_FORBIDDEN`/`AST_SELECT_ONLY`; two
  statements -> 403 `SINGLE_STATEMENT_ONLY`; unregistered table -> 404
  `DATASET_NOT_REGISTERED`; parameterized SQL -> 422 `SQL_SYNTAX_ERROR`
  (`%(name)s` does not parse for the spark dialect, and the provider refuses
  bound parameters as well);
- the Thrift Server recognizes `spark.sql.thriftServer.queryTimeout=120s` and
  `spark.sql.thriftServer.interruptOnCancel=true` (`SET` through Impyla);
- DataHub cycle against the pinned `v1.7.0.1` stack (GMS + opensearch + kafka +
  mysql): the `hive-metastore` recipe ingested the shared metastore, the task
  reported `mapped 3 / expected 3 / missing []`, every table mapped to one `hive`
  URN under the datasource's platform instance
  (`urn:li:dataset:(urn:li:dataPlatform:hive,ainative-abb6eefa.demo.hive_orders,DEV)`),
  and `searchAcrossEntities` finds them. With `AIND_DATAHUB_ENABLED=1` the
  platform's lineage endpoint answers for the Spark dataset
  (`status=no_upstream`, as expected: no views, no Spark listener).

**Two defects, both in the cancellation path, both reproduced twice:**

1. Cancelling a long Spark query does not end as `CANCELLED`. The user cancel
   reaches the provider (`impala.hiveserver2: Canceling active operation`), but
   the executing thread then calls `cursor.is_executing()`, which raises
   `KeyError: None` inside impyla 0.24.0 (`TOperationState._VALUES_TO_NAMES[None]`
   once the operation state is unknown). `SparkProvider.execute`
   (`app/providers/spark.py:157`) lets that escape;
   `classify_execution_error` sees the message `"None"`, matches no rule and
   returns `"error"`; the executor publishes `FAILED`, which
   `queue_repo.publish_terminal` refuses from `CANCEL_REQUESTED`
   (`InvalidTransition`), so no terminal status is ever written and the job
   sits until the claim lease expires - the reconciler then records
   `LOST`/`QUERY_LOST` about 3.5 minutes later.
2. The platform's 120-second ceiling ends the job `FAILED`
   (`EXECUTION_ERROR`, "source execution failed (error): 8") after 120.8 s
   instead of `TIMED_OUT`/`QUERY_TIMEOUT`: the deadline did fire and the
   operation was interrupted, but the post-cancel state is again classified as a
   generic error.

Fix sketch (not applied): make the Spark polling loop cancel-aware (treat the
impyla unknown-state `KeyError`, and any error raised after `cancel()`, as a
cancellation outcome), and make the executor's failure publish tolerate a job
already in `CANCEL_REQUESTED` (fall back to `CANCELLED`) so no provider error
can strand a job to `LOST`.

Still not verified, and not represented as passing: read-only source
authorization (the fixture has no authorizer and NOSASL ignores the identity),
Spark job lineage from the DataHub Spark listener, browser E2E for the Spark
screens, and a Kyuubi/HiveServer2 execution connector.

### Both defects fixed and re-verified (2026-09-29)

Three changes, none of which touch SQL validation or grants:

1. `app/providers/spark.py` - the polling and fetch loops now translate the two
   Impyla failures the server causes: an unknown operation state after our own
   cancel becomes `SparkOperationCancelled`, and the state the server reports
   when `spark.sql.thriftServer.queryTimeout` fires becomes
   `SparkOperationTimedOut`. The second one needed the state identified: Hive's
   `TOperationState` stops at `PENDING_STATE` (7), Spark also reports
   `TIMEDOUT_STATE` (8), and impyla 0.24.0's enum has no name for it - so
   `get_status()` raises `KeyError(8)`. Verified by extracting the enum from
   `hive-service-rpc-3.1.3.jar` in the pinned Spark image.
2. `app/query/executor.py` - when the monitor has already sent a cancel, a
   generic engine error resolves through `_cancel_outcome` instead of failing
   the job (`FAILED` is not a legal transition out of `CANCEL_REQUESTED`).
   Engine verdicts that name a cause (permission, schema, syntax, unavailable)
   keep their own codes.
3. `app/query/executor.py` - `_publish_terminal` resolves a job that is still in
   `CANCEL_REQUESTED` to `CANCELLED` instead of letting the refused transition
   leave it running until its lease expires, so no future provider quirk can
   reproduce the `LOST`-after-3.5-minutes failure mode.

Regression tests: `backend/tests/unit/test_spark_connector.py` (cancel
translation, the same error without a cancel staying an error, and the
server-timeout state), `backend/tests/unit/test_query_state_machine.py` (the
terminal-publish fallback), and
`backend/tests/integration/test_cancel_timeout.py::test_cancel_survives_a_provider_that_cannot_name_it`
(a stub provider that fails with the fixture's own `RuntimeError("None")` after
a cancel must still end `CANCELLED`).

Re-run evidence:

```powershell
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q --basetemp=runtime/pytest-tmp
# -> 281 passed, 2 warnings
# full matrix, workers stopped, MySQL + Doris + DataHub live:
# -> 73 passed, 25 deselected (includes the new cancel regression test)
```

Live, on the same fixture and datasource:

| Step | Before | After |
|---|---|---|
| user cancel of a long query | `LOST`/`QUERY_LOST` ~3.5 min later | `CANCELLED`/`QUERY_CANCELLED`, terminal 2.2 s after the cancel |
| platform 120 s ceiling | `FAILED`/`EXECUTION_ERROR` at 120.8 s | `TIMED_OUT`/`QUERY_TIMEOUT` at 120.6 s |
| server backstop, no platform (`runtime/accept_spark_server_timeout.py`) | not measured | the Thrift Server aborted the join at **120.0 s** with `TIMEDOUT_STATE` (8) |

The ceiling test needed data the 2M-row table could not provide: its self-join
finishes in ~118 s, so it sometimes completed before the ceiling (one run ended
`SUCCEEDED` at 116.4 s and proved nothing). `runtime/accept_spark.py` and the
server probe now use `demo.slow_events_big` (8M rows), which reliably outlives
both 120-second timers.

### The DataHub step's cost

Bringing the full DataHub quickstart up next to Doris, the core stack and the
Spark fixture exceeded this host's 15.35 GiB VM ceiling: the Docker daemon
dropped its pipe mid-startup and every container restarted. The acceptance then
ran with Doris stopped and only `mysql`/`opensearch`/`kafka`/`system-update`/
`gms` up (no frontend, no actions), which fits. That is compatibility finding 12
observed from the outside.

### A09 harness re-run with the learning loop active (executed 2026-10-13)

The stack was restarted from the committed tree (core + full profile with
`compose.dev.yaml`, Spark fixture up, DataHub down) and the harness was run
unchanged, with an empty memory store:

```powershell
uv run --project backend --frozen python scripts/eval_agent.py --cases 10 --scale small --password <admin>
# -> [eval] a09_status=passed target_top3=7/7 unforced=3/3 scored=10/10
#    runtime/eval/a09-20261013-012136.json
```

Every case reproduced the 2026-09-27 real-model run field for field: same
statuses (9 COMPLETED + 1 PARTIAL for `incomplete_day`), same ranks (six 1st,
`schema_drift` 3rd, no target claimed for the three no-target cases), same claim
counts and gateway queries per case, `deepseek-flash`, prompt `m5-v1`; latencies
2.75-6.64 s against 2.19-5.98 s before.

Because business memory is now part of the loop, the same ten analyses also
exercised it end to end - and this is the first time the harness and the learning
loop ran together:

| Case (in order) | memory recalled | statements learned |
|---|---|---|
| 1 `ecpm_drop`-42 | 0 (cold) | 3 |
| 2 `traffic_drop`-42 | 3 | 3 |
| 3 `mixed_offset`-42 | 6 | 1 |
| 4 `no_change`-42 | 7 | 3 |
| 5-10 | 8 (the retrieval cap) | 12 |

All ten finished analyses recorded an extraction (`analysis.memory` events: 10),
the store ended at 23 `proposed` statements, and **the scores are identical to
the run without memory** - the advisory brief changed no outcome, which is what
the design claims (memories only inform dimension choice; the allowlist and the
deterministic kernel still decide everything).

One property of running them together is worth knowing: the harness resets the
demo data per case while the memory store persists across cases, so a statement
learned from one scenario's data is handed to the next case, whose data differs.
Nothing in the score depends on it, but a reader looking at the store after a
harness run should not read those statements as facts about one coherent
business - they are what each synthetic scenario taught.

---

## Conversational Ask, P1 slice (executed 2026-10-13)

Implements the P1 slice of
`docs/superpowers/specs/2026-10-13-askdata-conversation-skills-design.md`:
persistent sessions, conversation turns, idempotency, busy protection, stable
message order and the legacy-message reader. **P2-P5 are not implemented**, so
the requirement as a whole is not delivered - this section records only what
ran.

What changed: migration `c4d9a17e6f20` (session scope + `conversation_turns` +
message `sequence`/`turn_id` + `analysis_tasks.conversation_turn_id` +
`task_queue.kind='conversation'`); `app/agent/conversation/` (scope merge,
orchestrator); `app/api/routes/conversations.py`; the worker claims turns and a
reconciler closes them when their analysis settles; the analysis runner no
longer writes its own assistant pointer for a turn-owned analysis; the turn API
is gated by `AIND_CONVERSATIONAL_ASK_ENABLED` (default off).

```powershell
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q --basetemp=runtime/pytest-tmp
# -> 311 passed, 2 warnings
# full matrix (PostgreSQL + MySQL + Doris; workers stopped; ecpm_drop loaded):
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --basetemp=runtime/pytest-tmp
# -> 79 passed, 1 skipped, 25 deselected in 163.59s
uv run --project backend --frozen python backend/scripts/verify_schema.py
# -> schema verification OK: 29 tables, 8 named state checks, 22 indexes
```

The one skip is the deployed-stack DataHub test: the running API container was
started with `AIND_DATAHUB_ENABLED=0` and still runs the pre-P1 image, so it
cannot exercise this slice. Not verified, and not represented as passing:
anything in P2-P5 (skill packages and versions, model-backed intent parsing,
scope resolution from natural language, explanations, shared budget, session
SSE, the `/ask/:sessionId` and `/skills` UI, and the C03-C05/C07/C09-C11,
S01-S09, R01-R02 cases).

Behavior verified live by the integration tests (host-side worker passes, real
control DB):

| Case | Evidence |
|---|---|
| C01 | three turns on one session; transcript `user/assistant` x3 with `sequence` 1-6; one analysis per turn, none rebuilt; after settling the session reports `active_turn_id=None`, `context_version >= 1` and the confirmed scope with `field_origins.metric_key = "ui"` |
| C02 | a turn with only a metric ends `WAITING_INPUT` with a `clarify` reply naming the missing periods, creates no analysis, and leaves `resolved_context` empty; the next turn completes the scope and runs |
| C06 | same `Idempotency-Key` + same body replays the same turn; same key + different body is 409 `IDEMPOTENCY_CONFLICT`; a second key while a turn is active is 409 `SESSION_BUSY` (with the active turn id); the legacy `POST /analyses` path is refused the same way |
| C08 (turn scope) | cancelling a turn marks it `CANCELLED`/`QUERY_CANCELLED`, cancels the linked analysis, and repeats idempotently |
| 8.1 legacy read | rows with `content_schema_version = 0` are returned converted (`text`, `kind=analysis`) and never rewritten |

Context merging is pinned by unit tests: absent fields inherit, `clear` beats a
value in the same patch, origins are recorded, and only a `complete` state
bridges to the analysis API's `data_complete = true`.

## Conversational Ask, P2 slice: skill packages and versions (executed 2026-10-13)

Implements the P2 slice of the design: skill packages, immutable versions, the
ownership and capability rules, the three builtin skills and the `/skills` UI.
P3-P5 remain unimplemented, so the requirement as a whole is still not
delivered.

What changed: `backend/app/skills/` (package parsing with every design-7.3 limit,
strict front matter, applicability); migration `b7e2f40c91a8` (`skills`,
`skill_versions`); `app/api/routes/skills.py` (7 endpoints); the `/skills` page
plus `apiUpload` and regenerated `openapi.json`/types; `metadata/skills/*.md`
with `python -m app.cli load-skills` and a bootstrap hook; `python-multipart`
pinned at 0.0.32.

```powershell
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q --basetemp=runtime/pytest-tmp
# -> 350 passed, 2 warnings
# full matrix (PostgreSQL + MySQL + Doris; workers stopped; ecpm_drop loaded):
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --basetemp=runtime/pytest-tmp
# -> 86 passed, 1 skipped, 25 deselected in 193.61s
uv run --project backend --frozen python backend/scripts/verify_schema.py
# -> schema verification OK: 31 tables, 11 named state checks, 25 indexes
cd frontend && npm run build     # -> tsc + vite clean
```

The skip is the deployed-stack DataHub test (the API container runs the pre-P2
image with DataHub disabled), so the live UI has **not** been exercised - the
`/skills` page was verified by typecheck and build only, not in a browser.

Verified by tests (design 13.2 cases):

| Case | Evidence |
|---|---|
| S01 | preview validates a ZIP bundle (name, files, hash, applicability) then import stores version 1; the version detail returns both files; the skill appears in `GET /skills` |
| S02 | the same content re-uploaded returns the existing version (count stays 1); changed content creates version 2 and version 1's stored body is unchanged |
| S03 | a second analyst gets 404 for read, list, patch and version upload of another user's skill |
| S04 | zip slip, absolute/drive/UNC paths, symlinks, case-folded duplicates, reserved names, ADS streams, trailing dots, deep nesting, nested archives and a zip bomb are all rejected |
| S05 | `<script>`/`<iframe>`/`javascript:` rejected; YAML aliases, custom tags and duplicate keys rejected; injection prose is stored as data and the manifest carries only strings/lists |
| S06 | script packages, binary/HTML references and oversized bodies are rejected as unsupported or too large |
| S07 | disable and archive are identity-level (content untouched); archived skills leave the default list and return with `include_archived` |
| design 7.2 | unknown metrics and non-allowlisted dimensions are recorded, never block saving, and never extend the registry; a builtin slug cannot be shadowed (409) and builtins are read-only (403) |

Two things the live run taught:

1. `sync_builtins` silently reported success when `metadata/skills` did not
   exist (the directory is relative to the working directory). It now raises,
   the CLI exits non-zero with the resolved path, and bootstrap reports the
   problem without failing.
2. Applicability was frozen at import time, so the builtins imported before
   their metrics were loaded stayed "unknown metric" forever. The declaration
   now travels in the version manifest and the API returns the *current* verdict
   (`compatibility`) beside the import-time one (`compatibility_at_import`);
   live check: `channel-contribution` was `at_import=false` and is `now=true`
   after bootstrap loaded the metrics. One builtin also declared `ad_network`
   for `total_revenue`, which does not allow it - the check caught it, the
   content was fixed, and the re-sync created version 2 while keeping version 1.

## Conversational Ask, P3 slice: model-backed orchestration (executed 2026-10-13)

Implements the P3 slice of the design: intent parsing, scope resolution and
merging, explanations from stored evidence, skill loading, the turn budget, the
session event stream and the worker lifecycle. P4 (the conversational UI) and P5
(the full acceptance matrix) remain, so the requirement as a whole is still not
delivered.

What changed: `app/agent/conversation/{intent,skills,budget,explain}.py`; the
orchestrator split into `prepare -> decide -> apply` with `run_turn` as the worker
entry point; the analysis runner honours a persisted sub-budget and receives the
skill block as advisory context; `GET /sessions/{id}/events`; `ErrorCode`
gains `LLM_UNAVAILABLE`/`ANALYSIS_EXECUTION_ERROR` (the strings the runner already
emitted).

```powershell
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q --basetemp=runtime/pytest-tmp
# -> 364 passed, 2 warnings
# full matrix (PostgreSQL + MySQL + Doris; workers stopped):
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --basetemp=runtime/pytest-tmp
# -> 95 passed, 1 skipped, 25 deselected in 203.71s
```

The skip is the deployed-stack DataHub test (container image predates P1-P3, flag
off). Not verified and not represented as passing: the P4 UI, the browser E2E
cases, and the parts of the design's matrix that need them (C03-C05/C09 are
covered at the API level below, C07 only as `requeue_stale_turns`).

Verified against the real control database with a scripted model (fake provider
injected at the orchestrator's `resolve_provider`), host-side workers stopped:

| Case | Evidence |
|---|---|
| C03 | turn 1 sets the full scope; turn 2 ("只看安卓") sets only `filters` and the analysis inherits metric/window/dimensions; turn 3 (`clear: [filters]`) removes it - the stored analysis context shows each outcome |
| C04 | `comparison=previous_period` with only a current period is frozen server-side into `2026-09-01..2026-09-08`, recorded with `field_origins.baseline_start = "model"`; explicit dates are never overwritten |
| C05 | an explain turn creates no analysis and no query, returns `kind=explanation` with `evidence_state=ok`, and its quote text equals the stored claim text verbatim; an unresolvable reference yields `evidence_state=unavailable` with the drop reasons |
| C11 | with no provider the rule path runs the structured scope through the deterministic template (`model_id=deterministic-template-v1`) and records the degradation |
| skills | a manual selection is loaded, snapshotted (mode, version id, files, bytes) and injected into the analysis state inside a block labelled as untrusted data |
| late reply | a turn cancelled *inside* the model call publishes nothing: no analysis, no assistant message, status stays CANCELLED |
| C09-lite | session SSE emits persisted events with monotonic ids and a `Last-Event-ID` resume that repeats nothing |
| budget | the turn row carries `deadline_at`, model calls, tokens and the prompt-version history |

Four defects were found and fixed while making this pass - all in the new code:

1. **A model call inside the transaction that held the turn row lock.** The
   cancel route does `SELECT ... FOR UPDATE` on the same row, so a cancel would
   block for as long as the model took (and the test surfaced it as
   `idle_in_transaction_session_timeout`). The lifecycle is now
   prepare/decide/apply with the model call outside every transaction.
2. **`execute_turn` re-ran on an ANALYZING turn.** A worker retry (or a second
   drain pass) called the model again and tried to create a second analysis for
   the same turn; only the unique index would have stopped it, and the retry
   failed the turn instead. The function now returns early for states the
   reconciler owns.
3. **The skill snapshot was clobbered.** `_apply_turn` built an empty record and
   wrote it over the preparation's snapshot when the model made no skill choice,
   so a loaded skill vanished from the record while the analysis still received
   its text.
4. **`TurnBudget` did not match the provider protocol** (`reserve_model_call`),
   so every intent call failed with `LLM_UNAVAILABLE`; the reservation is now
   taken by the provider itself and the actual usage is recorded afterwards.

Two operational notes for the next run: the compose `query-worker`/`agent-worker`
must be **stopped** before host-side integration tests - leaving them up produced
`DATASOURCE_UNAVAILABLE` failures and Postgres deadlocks against the test
truncate, and the container image predates P1-P3 so its workers run the old
orchestrator; and an SSE stream abandoned mid-read keeps a database session open,
so the test reads each stream to its own `stream.end`.

## Conversational Ask, P4 slice: the conversational UI (executed 2026-10-13)

Implements the P4 slice: the Askdata workspace (sessions, thread, scope and skill
panels), live updates, the flag-off fallback and the browser journeys. P5 (the
remaining acceptance matrix, the A09 regression and a real-model multi-turn run)
is still open, so the requirement as a whole is not delivered.

What changed: `frontend/src/pages/AskPage.tsx` rewritten as a conversation
workspace; `components/ConversationPanels.tsx` (session rail, scope panel, skill
picker); `hooks/useSessionEvents.ts`; conversation API wrappers and regenerated
`openapi.json`/types; routes `/ask` and `/ask/:sessionId`; conversation styles.
Two backend fixes came out of the browser run: the intent call now sees the
*effective* scope (this turn's panel values merged over the confirmed scope)
instead of only the confirmed one, and a `clarify` intent that asks for nothing
missing is downgraded to `analyze`.

```powershell
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q --basetemp=runtime/pytest-tmp
# -> 364 passed, 2 warnings
# full matrix (PostgreSQL + MySQL + Doris; workers stopped):
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --basetemp=runtime/pytest-tmp
# -> 95 passed, 1 skipped, 25 deselected in 233.29s
# browser journeys against the deployed stack (containers rebuilt with P1-P4,
# AIND_CONVERSATIONAL_ASK_ENABLED=1, dev overlay for the raised login limit):
cd frontend; npx playwright test
# -> 7 passed (24.0s)
```

The browser journeys (the first real UI evidence for this feature):

| Journey | What it exercised |
|---|---|
| conversational Ask | new conversation created and addressable by URL; an explicit scope submitted from the panel; the turn settles into a transcript entry with claims, a deep link and the skills line; a refresh restores the same session; a follow-up ("只看安卓") inherits the scope and produces a second result |
| skills page | the builtin list renders and builtins expose no enable/disable controls (read-only) |
| analysis and charts (rewritten) | the same analysis started *from the conversation*, its chart and table view, and a drilldown that creates a child analysis ("下钻：") - the R01 regression path now runs through the new surface |
| SQL workspace, administration, capability gate | unchanged journeys, all still passing |

Three harness defects surfaced while making the browser suite pass - none of them
product bugs, all worth knowing:

1. **The suite's capability-gate journey signs out the shared session**, so any
   spec that ran after it got a 401 and a redirect to `/login`. That journey now
   runs in its own browser context (`storageState` cleared) and signs in itself.
2. **Navigating away mid-login aborts the request** (the trace showed the login
   POST with status -1), leaving the context unauthenticated; the journey waits
   for the URL to leave `/login` before continuing.
3. **Two turns with a real model exceed the 60 s default timeout**, so the
   conversation spec sets its own (240 s) - the assertions inside still fail fast.

Two product-level lessons recorded while running the journeys: the send button is
disabled while the composer is empty (an idle state, not a stuck state), and an
analysis window that includes a day with no data ends PARTIAL unless the user
declares the period complete - the journey now uses a covered window and sets the
completeness selector, which is what a user who knows the data would do.

## Conversational Ask, P5 slice: regression and acceptance (executed 2026-10-13)

Closes the design's P1-P5 checklist: the recovery and security items C07/C10 and
S03-S05, the A09 regression with the conversational layer deployed, and a
real-model multi-turn conversation. Commands below were executed on the working
tree (no commits were made - the design does not authorize them); the containers
were rebuilt from it and the control database was
repaired (`align_sources` + `bootstrap` + `load-metrics` + `load-skills`) after
the host-side integration run rotated it.

### C07 recovery and C10 revocation

```powershell
uv run --project backend --frozen pytest backend/tests/integration/test_conversation_recovery.py -m integration -q --basetemp=runtime/pytest-tmp
# -> 3 passed in 10.76s
```

- C07: a crash after `_prepare_turn` (turn row written, nothing else) is recovered
  by `requeue_stale_turns` + the orchestrator drain: the turn ends COMPLETED with
  exactly one analysis, one assistant message, `budget["model_calls"] == 1` and
  `reserved_input == 0` - the recovery neither duplicates work nor resets the
  budget. Re-running the same turn is a no-op.
- C10: after `DELETE /admin/grants/{id}` removes every query grant the analysis
  read, the transcript's assistant message is redacted
  (`content["permission_revoked"] is True`, claims and quotes blanked), the
  report and evidence endpoints answer 403, and the session event stream emits
  `analysis.permission.revoked` with the analysis id instead of leaving a
  watching client believing the result is still live. Two defects were found and
  fixed while making this pass: the test's first version revoked only the *first*
  query grant in the list (the source registers more than one table, so the
  analysis kept a live grant - it now revokes all query grants), and the SSE
  revocation branch referenced an unbound `revoked` name, so any session stream
  raised `NameError` on its first poll. The stream now recomputes the revocation
  set per poll against the current grants and announces each flip once. Both are
  covered by the test; the browser journeys were re-run against the rebuilt
  image (7 passed, below).

### Static suite and the full matrix

```powershell
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q --basetemp=runtime/pytest-tmp
# -> 364 passed, 2 warnings in 5.40s
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --basetemp=runtime/pytest-tmp
# -> 98 passed, 1 skipped, 25 deselected in 210.02s
```

The matrix ran with PostgreSQL + MySQL + Doris live, the `ecpm_drop` demo loaded,
workers stopped and `--basetemp=runtime/pytest-tmp`; the skip is the
deployed-stack DataHub test (DataHub not started), and the 25 deselected are the
DataHub integration tests that need `AIND_DATAHUB_ENABLED=1` with the stack up.
(The first attempt at this re-run left the compose `query-worker`/`agent-worker`
up from the A09 run; they stole host-side test claims and produced three failures
and three errors. Stopping them restored the 98/1/25 result - the same
precondition the earlier records call out.)
No code in this change set touches `app/metadata` or the DataHub adapter, so that
subset was not re-run in this pass and is **not** counted as verified here.

### A09 regression with the conversational layer deployed

```powershell
uv run --project backend --frozen python scripts/eval_agent.py --cases 10 --scale small --password <admin pw>
# -> [eval] a09_status=passed target_top3=7/7 unforced=3/3 scored=10/10
#    runtime/eval/a09-20261013-215859.json
```

The deployed containers were rebuilt from the working tree (conversation + skills
code present, `AIND_CONVERSATIONAL_ASK_ENABLED=1` in the stack's `.env`), so the
analysis pipeline and the conversational layer ran side by side. The score is
field-identical to the 2026-09-27 and earlier 2026-10-13 records: 7/7 target
cases in the top three, 3/3 no-target cases without forced attribution, 10/10
fully scored, `incomplete_day` PARTIAL as designed. R02 (no_change /
incomplete_day / driver decomposition) is inside this run; R01 (the original
analysis and drilldown path, now reachable from the conversation) is the
rewritten Playwright journey recorded in the P4 section.

### Real-model multi-turn conversation

`runtime/accept_conversation.py` (host-side driver, gitignored like the other
acceptance scripts) loads the `ecpm_drop` scenario (seed 42, as_of 2026-09-13, small), imports a
skill package over the API, and drives two sessions against the deployed stack
over HTTP only. Ground truth stays in the script and is compared at the end; it
is never sent to the platform. Record:
`runtime/acceptance/conversation-20261013-220711.json`.

Windows baseline 2026-09-11..09-12 / current 09-12..09-13; generator delta
-2515.294902 on a baseline of 48919.256121.

| Turn | Status / intent | Latency | Tokens in/out | Skills loaded (hash prefix) | Analysis |
|---|---|---|---|---|---|
| analyze-scoped (manual skill, scope panel) | COMPLETED / analyze | 8.38 s | 2563 / 158 | revenue-watch `3836b918` | COMPLETED, target rank 1, `scored=true` |
| follow-up-inherit ("那各渠道的贡献") | COMPLETED / analyze | 8.39 s | 2728 / 270 | revenue-watch `3836b918` | COMPLETED, windows inherited |
| explain (parent analysis, no new query) | COMPLETED / explain | 4.22 s | 2991 / 425 | revenue-watch `3836b918` | none (`analysis_id` null) |
| auto-skill-vague ("收入怎么样？") | WAITING_INPUT / clarify | 3.19 s | 1930 / 138 | 3 builtins loaded, none used yet | none - asks for metric and periods |
| auto-skill-scoped (prose windows) | WAITING_INPUT / clarify | 2.16 s | 2050 / 224 | channel-contribution | none - only `metric_key` still missing |
| draft-completion ("广告收入，ads_revenue。") | COMPLETED / analyze | 6.30 s | 2074 / 200 | 2 builtins | COMPLETED, context merged from the draft |

What this records, per the design's P5 evidence list:

- **Model and prompt version**: the analysis reports `model_id=deepseek-flash`,
  `prompt_version=m5-v1` (the runner's); the conversational intent layer records
  `intent:conversation-intent-v1` in each turn's budget history.
- **Evidence consistency**: turn 1's analysis scored against the generator's
  ground truth - `delta_matches`, `target_in_top3` (rank 1),
  `driver_matches`, `claims_all_bound`, all three claims carry query ids - the
  same scorer the A09 harness uses.
- **Skill hashes**: every loaded skill is recorded per turn with its content
  hash; the imported package's preview hash equals its create hash
  (`3836b9182a6970dc1660c9ea42cf7b2e6f5d8ad0e42edfb4c8e9e53084dcc867`), and
  four consecutive runs of the script replayed the same `skill_id` and
  `version_id` - same content is idempotent (S02).
- **Clarification is honest**: with no scope the model asks for everything; after
  a prose answer supplying windows and dimensions it narrows to the one field
  still missing (`metric_key`) and does not guess; the windows persist in
  `pending_clarification` (design 8.3) and the next turn merges draft + new input
  into a completed analysis. A failed first attempt of the driver also proved the
  negative path: a manual selection of an uploaded skill that was not bound to
  the session fails the turn with `SKILL_INVALID` instead of silently loading
  something else.
- **Budgets**: every turn used one model call; the analysis ran on the turn's
  remaining sub-budget (`max_input_tokens 57437` of the 60000 turn allowance) and
  4 of its 12 allowed queries.

The driver lives under `runtime/` (gitignored, like the other acceptance
scripts) and is re-runnable with `--skip-load` when the demo data is already in
place.

### Browser journeys, re-run after the SSE fix

The P4 record's journeys were re-run against the image rebuilt from the tree
(the SSE fix included), on the repaired stack:

```powershell
cd frontend
$env:E2E_BASE_URL="http://127.0.0.1:3000"; $env:E2E_ADMIN_PASSWORD="<admin pw>"; npx playwright test
# -> 7 passed (28.1s)
```

The same seven journeys as the P4 record: the conversational Ask flow (26.5 s),
the rewritten analysis + drilldown journey (12.3 s), the skills page, the SQL
workspace, administration, and the capability gate.

## 2026-10-14 code-review fixes — local regression record

This record covers the nine findings from the current-tree code review. It does
not supersede the historical live-stack acceptance above. No commit, deployment
or real-model request was made during this fix run.

Implemented and covered by regression tests:

- Generic/model-unavailable analysis failures now publish `FAILED` and release
  the queue claim instead of raising an unbound `ErrorCode` in the handler.
- Detail/list/cancel responses retain lifecycle metadata and redact report,
  context, plan, state and steps after revocation. Report, evidence, analysis
  memory and chart reads check both discover and query grants.
- Transcript explanations re-check every quoted analysis, reject missing or
  foreign sources, and redact report-derived prose and quotes without rewriting
  stored evidence. Model history, recent reports and focus identifiers omit
  revoked evidence. Conversation and legacy-planner memory retrieval also check
  the source analysis's registered dataset dependencies against the requesting
  user's current discover/query grants. Missing source evidence is excluded.
- UI scope values and explicit clears override model proposals. The UI stores
  user overrides over the latest confirmed scope; reverting an edit removes its
  override. Late send/binding responses cannot change another session's state.
- Legacy submission, new turns, patch and chart drilldown acquire a refreshed
  session row lock before a shared active-turn/nonterminal-analysis busy check.
  Idempotent replays are resolved first. Unit regressions cover both entry-point
  directions and stale version checks; these are not a live lock-contention test.
- Filters retain raw typed text, validate on blur/submit, clear explicitly and
  also reach the legacy fallback. Invalid or duplicate entries do not submit.
- Enabled personal skills can be selected manually or saved as exact session
  versions with optimistic context checks. Pins precede the candidate cap;
  disabled, archived and private versions cannot load. Explicit empty manual
  selection does not inherit saved skills.
- Numeric and complex YAML mapping keys return `SKILL_INVALID`, including
  nested keys, rather than an unhandled `TypeError`.

Observed commands (Windows PowerShell):

```powershell
uv --cache-dir .uv-cache run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q --basetemp=runtime/review-fix-final-20261014 --tb=short -p no:cacheprovider
# -> 400 passed, 2 warnings in 4.48s (existing Starlette/anyio deprecations)

cd frontend
node node_modules/@playwright/test/cli.js test --config=playwright.regression.config.ts
# -> 15 passed (27.2s); API mocked, Vite without a backend proxy, installed Edge
npm.cmd run build
# -> passed: API type generation, TypeScript and Vite production build
```

The failing cases were executed before their fixes: the initial backend run
caught the failure-handler, redaction, scope, lock and YAML defects; subsequent
red runs covered skill pins, reverse legacy busy conflicts and explicit empty
manual selection. Browser red runs caught stale context, character erasure,
missing personal selection, cross-session callbacks, reverted overrides and
lost legacy filters before the corresponding green runs.
The final backend run first encountered an ACL error preparing a reused
`--basetemp` directory (399 passed, 1 setup error); the fresh directory in the
command above resolved it without changing code or relaxing the test.

**Not executed against live services:** the affected conversation/intent/skills
integration command reported **23 skipped** because `AIND_DATABASE_URL` was not
set or the control DB was unreachable. Docker engine pipes were unavailable in
this host session. Live PostgreSQL lock contention, full engine/DataHub matrix,
live-stack browser journeys and A09/real-model conversation re-evaluation remain
pending for this tree. Isolated browser tests and skipped integration tests are
not counted as live acceptance.

```powershell
uv --cache-dir .uv-cache run --project backend --frozen pytest backend/tests/integration/test_conversation_flow.py backend/tests/integration/test_conversation_intent_flow.py backend/tests/integration/test_skills_api.py -q --basetemp=runtime/review-integration --tb=short -p no:cacheprovider
# -> 23 skipped in 1.27s; no passing service result
```

## 2026-10-14 review fixes — live re-verification (executed)

The review record above was written without Docker (its integration run reported
23 skipped). This section closes that gap: the same tree was re-run against the
live stack, and two defects found while doing it were fixed.

### Two fixes made during this re-verification

- **Chart drilldown validated input after session state.** The new busy check ran
  before the drilldown-dimension check, so an invalid dimension answered
  `409 SESSION_BUSY` instead of `422` whenever the session happened to be busy
  (`test_analysis_flow.py::test_chart_artifact_reads_and_drills_down`, 1 failed in
  the first matrix run). Input is now validated first: an invalid dimension is
  refused the same way in every session state, and the session lock is still held
  from before the busy check through the write.
- **The A09 harness compared group keys as ordered tuples.** The platform never
  pins the order of dimensions inside a group key - the planner's selection
  decides it - and on this run the model ordered four target cases as
  `[ad_network, platform, country]` instead of `[country, platform, ad_network]`,
  so the same target cells scored as misses (`target_top3=4/7`). The evidence's
  `contribution.dimension` list gives the order actually used; the harness now
  matches the target cell by dimension name (and still requires the analysis to
  have used exactly the case's three dimensions, so a coarser grouping cannot
  pass). Re-run: `passed 7/7` with the target cell ranked 1st in all seven
  target cases, `schema_drift` 3rd - the same cells and ranks as every earlier
  record. Each case now records `dimensions_used` as evidence.

### Commands and results

```powershell
# static suites
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q --basetemp=runtime/review-verify-tmp
# -> 400 passed, 2 warnings in 5.89s

# the integration suites the review record could not run (workers stopped, live PG + MySQL + Doris)
uv run --project backend --frozen pytest backend/tests/integration/test_conversation_flow.py backend/tests/integration/test_conversation_intent_flow.py backend/tests/integration/test_conversation_recovery.py backend/tests/integration/test_skills_api.py -m integration -q --basetemp=runtime/review-verify-tmp
# -> 26 passed in 58.43s

# full matrix
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --basetemp=runtime/review-verify-tmp
# -> 98 passed, 1 skipped, 25 deselected in 234.72s

# browser: the review's isolated regressions, then the live journeys
cd frontend
node node_modules/@playwright/test/cli.js test --config=playwright.regression.config.ts
# -> 15 passed (32.4s)
npm.cmd run build
# -> passed (API type generation, TypeScript, Vite build)
$env:E2E_BASE_URL="http://127.0.0.1:3000"; $env:E2E_ADMIN_PASSWORD="<admin pw>"; npx playwright test
# -> 7 passed (26.5s)

# real model
uv run --project backend --frozen python scripts/eval_agent.py --cases 10 --scale small --password <admin pw>
# -> [eval] a09_status=passed target_top3=7/7 unforced=3/3 scored=10/10
#    runtime/eval/a09-20261014-014108.json
uv run --project backend --frozen python runtime/accept_conversation.py --password <admin pw>
# -> runtime/acceptance/conversation-20261014-014412.json
```

The stack was rebuilt from this tree (`docker compose -f compose.yaml
-f compose.dev.yaml --profile full build backend frontend`) with the workers
stopped for the host-side suites and started for the journeys and the harness;
the control database was repaired after the matrix run (`set-password`,
`bootstrap`, `load-metrics`, `load-skills`, `align_sources`).

### Real-model multi-turn conversation, re-run

Same script and scenario as the P5 record (ecpm_drop, seed 42, as_of 2026-09-13).
All properties hold: the scoped analyze is evidence-scored with the target cell
ranked 1st; the explanation starts no query; the vague question is answered with
a clarification whose draft persists (`missing_after_prose=['metric_key']`) and
the next turn merges it into a completed analysis; every loaded skill is recorded
with its content hash.

One outcome differs from the P5 record, and it is model variance rather than a
regression: the follow-up ("那各渠道的贡献分别是什么？") was answered as an
**explain** turn reusing the stored evidence, not as a new analysis. Both are
design-consistent (the first turn had already broken the revenue change down by
ad_network), so the driver now records which outcome occurred instead of
asserting the analyze path: `outcome=explain - reuses stored evidence, no new
query`.

### What this section does not claim

DataHub-dependent integration tests remain deselected (stack not started), the
Spark/Hive preview was not re-run, and no live PostgreSQL lock-*contention*
scenario was executed beyond the busy-check regressions - the busy path is
covered by unit tests and by the suites above, not by a concurrent load test.


## 2026-10-15 reported defects — static re-verification

Implemented fixes for nonadditive period aggregation and named multi-source
composition, disabled Ask fallback repetition, rollback-lost login audits,
business-memory HTTP authorization, oldest-first model history, and unauthorized
schema probing. Independent review also caught and resolved NULL composition
semantics, repair-step key collisions and fallback scope changes during a pending
request. Contributions are limited to additive metrics/compositions; undefined
period values and incompatible old checkpoints fail explicitly.

Observed commands on this working tree:

- `uv --cache-dir runtime/uv-cache run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q -p no:cacheprovider --basetemp=runtime/pytest-<unique timestamp>`
  -> **425 passed, 2 warnings** (existing Starlette/httpx/anyio deprecations).
  Output: `runtime/defects-static-20261015.txt`. A work-local uv cache and fresh
  temporary directory avoid existing cache/tmp ACL failures.
- `cd frontend; npm.cmd run typecheck` -> **exit 0**; independently repeated
  `node node_modules/typescript/bin/tsc -b --noEmit` after the snapshot error
  handler -> **exit 0**.
- `cd frontend; npm.cmd run build` -> **exit 0**, Vite production build complete.
- `git diff --check` -> **exit 0** (line-ending notices only).

Regressions cover period-total SQL shape for eCPM/DAU; observer totals for eCPM,
subtraction/division and NULL-aware composition; nonadditive attribution refusal;
real FastAPI dependency rollback with SQLite persistence for 401 and 429 audits;
memory filtering before page limit and visibility-scoped counts; latest history;
and denied-column probes with zero schema introspection. Browser cases were
added for fallback rerenders, idempotency headers and immutable sent scope.

**Not verified:** browser runs started on isolated ports 3021/3023 but stalled
without a final result; partial progress is not counted as passing. Shell launch
attempts intermittently failed with `helper_unknown_error: setup refresh had errors`.
The full-service PostgreSQL/MySQL/Doris/DataHub matrix, deployed journeys and A09
were **not executed** for these fixes. The audit persistence regression uses local
HTTP + SQLite, not live PostgreSQL. No deployment, commit or historical-report
rewrite was performed. Rerun affected historical analyses for corrected reports.


## 2026-10-15 medium defects — regression verification

Confirmed and fixed: optional driver `AnalysisError` handling (both compilation
and deterministic calculation), full-text percent escaping with AST-only bindings,
PostgreSQL TLS-mode forwarding, MySQL/Doris socket I/O bounds, queue/resource lock
order, explicit queue transitions, fenced publication failure, current
query+discover result/SSE access, nonadmin datasource-summary redaction, terminal
SSE closure and historical-analysis polling, HTTP body/map limits and bounded
login-rate-limit storage. The backend image disables forwarded-header rewriting.
The reported NULL eCPM breakdown conversion was already fixed by the earlier
2026-10-15 change; a zero-impressions/NULL breakdown observer regression now
checks that independent period totals remain usable.

Independent review reproduced a cancellation/publication-failure race and idle
cross-tab session staleness. Both were fixed before acceptance: cancellation wins
under the authoritative row lock with the underlying error retained as a cause,
and idle tabs refresh session state every 15 seconds. Model RPCs still retain
the analysis task lock; reclaim now skips it rather than introducing a queue
lock cycle. Optional driver submissions use a savepoint to prevent half-plans.

Observed checks on the final source tree:

- `uv --cache-dir runtime/uv-cache run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q -p no:cacheprovider --basetemp=runtime/pytest-<timestamp>`
  -> **469 passed, 2 warnings** (existing dependency deprecations). Output:
  `runtime/medium-static-20261015.txt`. Includes SQL/Spark/skill attack corpora.
- `uv --cache-dir runtime/uv-cache run --project backend --frozen pytest backend/tests/unit/test_medium_api_defects.py backend/tests/integration/test_queue_executor_medium_defects.py -q -p no:cacheprovider --basetemp=runtime/pytest-<timestamp>`
  -> **14 passed / 2 skipped**; the two PostgreSQL contention cases require
  `AIND_DATABASE_URL`. They are not live passes.
- `cd frontend; npm.cmd run test:unit` (equivalently
  `node --test tests/unit/stream-events.test.cjs`) -> **7 passed**. These execute
  transpiled hooks with mocked React effects/EventSource, covering terminal close,
  transient reconnect/unmount and terminal polling; they are not browser E2E.
  Output: `runtime/medium-frontend-unit-20261015.txt`.
- From `backend`: `uv --cache-dir ../runtime/uv-cache run --frozen python -m app.openapi_export --output ../frontend/openapi.json`
  -> **exit 0**, updated OpenAPI (189062 bytes); generated frontend API types.
- `cd frontend; node node_modules/typescript/bin/tsc -b --noEmit` -> **exit 0**.
- `cd frontend; npm.cmd run build` -> **exit 0**, production build (90 modules).
- Independent general review approved the cancellation/body/SSE fixes after
  **26 focused tests passed**.

Live boundary: `docker ps --format "{{.Names}} {{.Status}}"` returned no running
containers. The engine matrix, actual metadata-lock stalls, TLS negotiation and
PostgreSQL contention were **not verified live**. Browser E2E was not rerun; the
earlier stalled browser attempts do not count. Spark concurrent cancellation
remains **suspected / pending**: pinned Impyla shares one unsynchronized RPC
transport, but a live Spark operation/cancel race and independent-transport
authorization were not reproduced. Spark code is unchanged. No deployment or
commit was performed. The proxy-header launch flag takes effect when the updated
image is built/run (native launchers must supply it explicitly).

## 2026-10-15 Shared control styles: native file input and leaking label default

Two related defects were found on the running platform and fixed together.

The Skills page was the one screen whose controls came from the browser instead
of the workspace stylesheet. Rendered and inspected with a mocked API
(headless Chromium via the installed Edge build, 1440px and 390px), before the
fix:

- the upload row rendered the native `<input type="file">` - a browser-drawn
  选择文件 / 未选择文件 control with its own OS chrome - next to the styled
  buttons;
- each row's 新版本 was a `<label class="ghost small">`, and the stylesheet only
  matches `button.ghost`, so it rendered as plain muted text beside the bordered
  停用 / 归档 buttons and was not keyboard reachable (its input is `hidden`);
- 显示已归档 stacked its checkbox above its text: `.inline-check` set `display`
  and `align-items` but not `flex-direction`, so the global
  `label { flex-direction: column }` still applied;
- `dl.kv` (preview facts) and `header.page-head` had no rules at all, so the
  definition list kept the UA's 40px indent and the page title rendered at the
  same 16px as the in-card headings;
- the page nested two `.page` containers (the shell's `main.page` plus its own),
  leaving every card 40px narrower and offset relative to all other pages;
- at 390px the wrapped column `.form-row` sized both children to their
  max-content width (probe: 328px row inside a 276px content box), so the upload
  row hung ~52px past the card border;
- on the Ask page the 技能 panel's skill chips stacked their checkbox above the
  name (live probe: 136x69px with `flex-direction: column`, i.e. a tall pill
  with a floating box, instead of a 176x25px pill). `.chip` set `display`,
  `align-items` and `gap` but not `flex-direction`, so the global
  `label { flex-direction: column }` applied. The same leak hit every other
  `<label>`-based inline control: the scope panel's dimension chips, and the
  administration screens' `.check` role/group pickers and `.inline-label`
  filters (`.inline` and, now, `.inline-check` were the only ones that reset it).

Changed: `frontend/src/pages/SkillsPage.tsx` (hidden input plus a real button
through a local `FileButton`; the picked file name shown in the shared mono
style; 新版本 is a real button, disabled while another action is in flight; the
page container is `.skills`), `frontend/src/styles.css` (`.file-pick`,
`.file-name`, `.page-head`, `.kv`, `.inline-check` direction,
`.form-row { flex-wrap: nowrap }` in the column branch, and one rule listing the
inline label classes - `label.check`, `label.chip`, `label.inline-check`,
`label.inline-label` - that must stay on one row). Picking the same file twice
now re-runs validation in both places (`event.target.value = ""` after reading
the file).

Observed checks:

- `cd frontend; npx playwright test --config=playwright.regression.config.ts`
  -> **23 passed** (18 pre-existing, 4 new in `tests/regression/skills.spec.ts`,
  1 new in `tests/regression/ask.spec.ts`). Output:
  `runtime/skills-ui-regression-20261015.txt`. The new Skills cases assert: no
  non-hidden `input[type=file]` on the page; the picker button plus file-name
  display; re-picking the same file re-validating; 停用 / 归档 / 新版本 sharing
  one computed border, background and padding; focus + Enter opening the picker
  and creating a version; `.kv` label and value on one row; 显示已归档 on one
  line; and at 390px the row inside the card's content box with no sideways
  scroll of the `main` container. The new Ask case measures every
  `.conversation-side .chip` (scope dimension and skill version chips): row
  direction, under 30px tall, checkbox vertically centred. That case was run
  against the unfixed stylesheet and fails there (`column` received), so it
  guards the fix rather than the current rendering; the Skills cases address
  testids (`skill-pick-file`, `skill-file-name`) that did not exist before the
  change, so they cannot pass unfixed by construction.
- `cd frontend; npm run test:unit` -> **7 passed**.
- `cd frontend; npm run typecheck` -> **exit 0** (regenerated
  `src/api/schema.d.ts`); `npm run build` -> **exit 0**, 90 modules,
  `dist/assets/index-DscJurKw.css` 14.36 kB.
- Rendered states inspected: 1440px and 390px, empty and picked-file, preview
  open.

Live deployment checks (same day, twice - once per fix): the frontend image was
rebuilt from this working tree and only the frontend container was recreated
(`docker compose -f compose.yaml -f compose.dev.yaml --profile full build
frontend`, then `up -d --no-deps frontend`, compose project `ainative`), so the
backend and both workers kept running. After the first rebuild the served
`index.html` referenced `assets/index-DscJurKw.css` and
`assets/index-DT32nYP0.js`; that CSS carried `.file-pick`, `.file-name`,
`.page-head`, `.kv` and `.form-row{flex-flow:column;align-items:stretch}` (the
minifier folds `flex-direction: column` plus `flex-wrap: nowrap` into
`flex-flow`), and the bundle carried the new `未选择文件` placeholder. A browser
run against the live stack (`E2E_BASE_URL=http://127.0.0.1:3000`, sign-in through
the suite's global setup) rendered the page with its four real skills: the picker
button, the placeholder, the three per-row buttons sharing one style, and the
显示已归档 checkbox on one line. Picking
`metadata/skills/channel-contribution.md` as `SKILL.md` returned a real preview
(名称 / 说明 / 适用性 / 内容哈希 / 文件 in the two-column layout, body preview,
保存为新技能 enabled); the same file named `channel-contribution.md` was rejected
by the real validator with `SKILL_UNSUPPORTED` ("a single-file skill must be
named SKILL.md") and that error rendered in the notice. The preview path is
read-only - nothing was saved.

The second live probe opened an existing session on `/ask` (no session was
created and no scope was submitted): the 技能 panel's first chip measured
`136x69` with `flex-direction: column`, checkbox top-left and the skill name
below it. After the second rebuild the same probe returned `176x25` with
`flex-direction: row`, and the served CSS carried
`label.check,label.chip,label.inline-check,label.inline-label{flex-direction:row}`
(new asset hash `index-D2SnToFv.css`).

A narrow-width probe of the live stack found one pre-existing shell defect the
mocked suite cannot see: `.nav` (`display: flex`, no wrap) cannot shrink below
the min-content width of its 7 links, so at a 390px viewport every page scrolls
sideways by ~190px (`NAV.nav right=580`; `/sql` and the admin pages behave the
same). It is unrelated to this change, was left unfixed and is listed in
`docs/todo.md`. Because the mocked shell only has the 5-link visitor navigation,
the narrow regression asserts the `main` container rather than the document.

Not verified here: no PostgreSQL/MySQL/Doris/DataHub matrix, no A09 and no
deployed journey were run - the change touches no API, worker or SQL code. The
deployed image carries this tree's content (it was built from the working copy
before this change was committed). The administration screens were probed live
for the same corrections (read-only visits, no form submitted): on
`/admin/permissions` every `.check` role picker
rendered 19px tall with the checkbox centred and the `.inline-label` status
filter 34px tall - the height of the `<select>` it wraps - all with
`flex-direction: row`; `/admin/datasources` and `/catalog` expose no
label-based inline control (their `.chip` and `mono small` classes sit on spans
and cells). The admin pages' column `.form-row` branch was not exercised, as
none of these visits hit a narrow viewport. Class names with no rule remain in
`AdminDatasourcesPage.tsx` (`mono small`, only `.mono-id` exists) and
`ResultTable.tsx` (`.result-wrap`, `.col-name`) plus `.message.assistant`; they
were left unchanged and are listed in `docs/todo.md`.

## 2026-10-15 Chart panel: values, precision and the hidden read window

The contribution chart inside an Ask conversation rendered as overlapping text.
Measured on the live stack before the fix (panel 524px wide, conversation
message):

- each row was a 4-column grid whose computed columns were
  `180px 15.2656px 264.734px 0px`: the label took its maximum, the bar column
  collapsed to 15px, and the share text took 265px;
- the bar element itself was 21px wide with `scrollWidth` 159, because the value
  `▼ -2475.870515 USD` lived **inside** the bar - so the text painted over the
  neighbouring 净变化占比 column;
- `net_change_share` was rendered as the raw Decimal `0.98432613727771` (14
  significant digits), which is what starved the bar column in the first place;
- `.chart-bar.negative` referenced `var(--danger, #b42318)`, a token this
  stylesheet never declares (the app's is `--bad`), so negative bars used a
  fallback colour;
- the same analysis holds 576 groups while the client's bounded read
  (`GET /charts/{id}?limit=200`) returns 200: the panel showed 200 rows, the
  footer said 共 576 点, and nothing reported the missing 376.
  `build_bounded_view` reports the stored chart's own cap in `truncated`
  (`MAX_CHART_POINTS = 1000`), not the window, so the flag stays false here.

Changed: `frontend/src/components/ChartPanel.tsx` - the bar is now a pure
length (`aria-hidden`, no text) on its own full-width track, so every row shares
one track and the lengths stay comparable; the signed value is text in the row
head, formatted with two decimals (`▼ -2,475.87 USD`) with the exact Decimal in
`title`; the share is a percentage (`净变化占比 98.4%`, one decimal, `0.4%`-level
values stay visible) with the exact fraction in `title`; the table view uses the
same two formatters and keeps the exact values in `title`; the panel computes
`total_points - data.length - offset` itself and warns about the points the
window leaves out; the footer now reads `图表 200 / 共 576 点` plus the rounding
rule. `frontend/src/styles.css` - `.chart-row-head` (flex, wraps), `.chart-bar`
as a 12px block, and the old
`minmax(120px, 180px) minmax(0, 1fr) auto auto` grid with its 720px override
removed; sign colours now use the declared `--bad` / `--accent` tokens.

Observed checks:

- `cd frontend; npx playwright test --config=playwright.regression.config.ts`
  -> **29 passed** (19 Ask, 6 new in `tests/regression/chart.spec.ts`, 4 Skills).
  Output: `runtime/skills-ui-regression-20261015.txt`. The chart cases assert
  the formatted value and share strings, the exact values in `title`, the table
  view carrying the same numbers, no bar containing text, no row cell
  overflowing its column, no bar leaving its row, the 390px panel staying inside
  its own box, and the window/cap notices appearing and disappearing correctly.
- Live, after redeploy: 200 rows measured with zero overflowing cells and zero
  bars past the panel edge; values `▼ -2,475.87 USD` / `▲ 22.56 USD`; shares
  `98.4%` / `-0.9%` / `0.4%`; `title` = `-2475.870515 USD` and
  `0.9843261372777194934258249453`; notice
  `按变化量从大到小只显示前 200 个分组，另有 376 个未显示（未合并、未补零）。`;
  footer `单位 USD · 证据 2 条 · 数据引用 calculation:d6b97a66 · 图表 200 / 共 576 点`.
- `cd frontend; npm run typecheck` and `npm run build` exit 0.
- Rendered and inspected at 1440px (conversation message) and 390px (mocked
  AnalysisPage).

The 200-point read window was left in place - only made honest. Rendering up to
200 two-line rows inside a chat message is still long; a top-N cap is a product
decision and was not taken (recorded in `docs/todo.md`).

Not verified here: no engine matrix, no A09, no deployed journey; the deployed
image carries this tree's content (built from the working copy before this
change was committed). The chart's own `truncated`/`dropped_points` path was
exercised only against the mocked payload, not against a stored chart that hit
the 1000-point cap.

## 2026-10-15 — Chinese/English interface localization

Started from a clean worktree at `7df6326`; fetched `origin` and fast-forwarded
`work` to `origin/main` at `00bee00` before implementing this change.

Delivered: the login and unauthenticated gate plus all workspace routes use a
shared Chinese/English catalog (`frontend/src/i18n/messages.json`, 461 entries).
The selector switches labels, statuses, notices, validation and error guidance
without clearing drafts. Browser language detection, persistent selection,
cross-tab updates and storage-disabled fallback are implemented. Document
language/title, dates and chart display formatting follow the locale. API and
multipart requests send `Accept-Language`; raw backend error details remain
inspectable with an accessible “Original error” button. Source SQL, data, user
content, metadata, model prose and stored report/evidence quotes are retained.
The server does not negotiate or regenerate model output in response to that
header; this change is interface localization, not automatic content translation.

Observed checks in the Linux cloud workspace:

- `npm --prefix frontend run typecheck` -> **passed** (OpenAPI generation and
  TypeScript compilation, no errors).
- `npm --prefix frontend run build` -> **passed**; Vite transformed 94 modules
  and built the production bundle. JS 427.77 kB (129.64 kB gzip), CSS 14.94 kB
  (3.60 kB gzip).
- `npm --prefix frontend run test:unit` -> **11 passed, 0 failed**. The four new
  localization checks cover translation/placeholder completeness, literal UI
  and status-label keys, coverage of every backend ErrorCode, unknown-key
  fallback, single-pass interpolation and preservation of user values.
- `PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium npm --prefix frontend run test:regression`
  -> **42 passed**: the existing 29 Ask/chart/Skills regressions plus 13 new
  localization cases. Existing tests now explicitly request Chinese, removing
  their dependency on the host's browser locale. The new cases switch both
  languages on Ask, SQL, catalog, history, Skills, datasource administration,
  permission administration and analysis; exercise login errors and input
  retention, SQL/error retention, localized request headers, reload/navigation
  persistence, cross-tab changes, Chinese browser detection with disabled
  storage, and the 390px layout in both languages.
- The 390px screenshot `/tmp/platform-i18n-mobile.png` was visually inspected:
  navigation wraps within the viewport, the selector remains visible, SQL
  controls fit their card, and the page has no horizontal overflow.
- `git diff --check` -> **passed**.

Not executed: deployed-stack browser journeys, database integration suites and
real-model acceptance. The browser suite mocks API traffic, and its passing
result does not establish backend service availability or a model's output
language. No backend execution, permission, SQL validation or persisted evidence
contract was changed.

## 2026-10-16 Ask 自然语言可用性：三个缺陷与修复（executed）

Requested from the running UI: "智能问数这里目前没有办法使用自然语言查询". Three
separate defects sat behind that one sentence, and two of them had to be fixed
before any natural-language question could produce a usable answer.

### What was actually wrong

1. **The landing page could not send.** `/` and the top-nav 智能问数 link both
   open `/ask` with no session in the URL. The composer accepted text, but
   发送 was `disabled={busy || !text.trim() || !sessionId}` — permanently
   disabled, with nothing on screen saying why, and no scope panel or thread
   either (`session.data` is undefined without a session). The screenshot the
   report came with shows exactly that state: typed question, faded button, no
   scope panel.
2. **A default conversational turn produced an empty answer.** The conversational
   scope carries completeness as three states (`unknown / complete / incomplete`,
   design 6.1), and the bridge to the analysis boolean maps only `complete` to
   true. Since a session starts at `unknown`, every analyze turn ran with
   `data_complete=false`, and `report.py` emitted **no claims at all** in that
   branch — not even the observed change. Observed live: turns ending
   `PARTIAL` with `"claims": []` and one limitation line, i.e. an empty bubble.
3. **The model's half-open/`inclusive` period mismatch was published as 变化 0.**
   Given "ads_revenue 在 2026-09-11 相比 2026-09-10 的变化", the intent model
   produced `current=[2026-09-11, 2026-09-11]` and `baseline=[2026-09-10,
   2026-09-10]` — empty ranges, which match no rows, so the report said
   `ads_revenue 变化 0` (`materiality.absolute_threshold` `±0.000`). The
   platform's own contract is `[start, end)` and the direct API rejects an empty
   range; only the conversational path bypassed that check. The same question
   with server-built dates (`45bfadef`, a drilldown) returned three claims, which
   is how the two runs were told apart.

### Fixes (all on the committed tree, working copy)

- `frontend/src/pages/AskPage.tsx`: with no session in the URL, 发送 now opens
  the conversation the question belongs to (`POST /sessions` titled with the
  first 60 characters, then `POST /sessions/{id}/turns`), so one click asks. The
  empty state says so. `submitTurn` deliberately keeps `scopePatchFromDraft`
  inside the caller's `try/catch` — it validates the filter text by throwing, and
  a throw escaping a React event handler is an uncaught page error (the first
  draft of this change did exactly that, and six existing regression cases
  caught it).
- `backend/app/agent/report.py`: `completeness` replaces the bare boolean and
  decides what may be claimed. `complete` is unchanged; `unknown` still publishes
  the observed change (a governed observation) and the materiality verdict but
  attributes nothing; `incomplete` withholds even the change. The limitation text
  distinguishes the two, which design 6.1 asked for and the code did not do.
  `_completeness` in `runner.py` reads the tri-state from the context when
  present and falls back to the legacy boolean (whose `false` is an explicit
  claim of incompleteness).
- `backend/app/agent/conversation/orchestrator.py`: `help` and `unsupported`
  replies now carry the metric registry (key, name, unit, dimensions, datasets —
  the same fields `GET /metrics` already returns) so "有哪些指标 / ADS 层都有哪些数据"
  is answered with real content. `_metric_table` is derived from the same
  `_metric_catalog`, so the validation source and the published list cannot drift.
- `backend/app/agent/conversation/context.py`: `invalid_period_fields` treats a
  non-positive span as blocking, and `missing_required_fields` includes it, so an
  empty/inverted period asks instead of running an empty query. The clarify reply
  states the convention. `intent.py` tells the model the convention (prompt rule
  2a) and records `empty_period:...` as a patch reason for audit.
- `frontend/src/pages/AskPage.tsx`: a result with no claims renders its
  limitations instead of an empty bubble, and help/unsupported replies render the
  registry list; the scope panel states the half-open convention.

### Evidence

- Static: `uv run --project backend --frozen pytest backend/tests/unit
  backend/tests/security backend/tests/contract -q --basetemp=runtime/pytest-tmp`
  → **488 passed** (new: `tests/unit/test_ask_natural_language.py`, plus
  completeness/period cases in `test_conversation_context.py` and
  `test_agent_core.py`).
- Mocked browser regressions: `cd frontend && npx playwright test
  --config=playwright.regression.config.ts` → `ask.spec.ts` **22 passed** (3 new
  cases: the landing-page send, the help registry list, the no-claims message);
  the whole suite reports **51 passed / 1 failed** on this working tree. The one
  failure is `catalog.spec.ts › labels the row and the detail panel with the
  declared domain`, which belongs to the in-flight catalog work sharing this tree:
  it fails identically with and without this change's stylesheet additions (A/B
  runs of the full suite, 1.1 min each), and passes when that file is run alone —
  a test-order dependence in that spec, not an effect of this change. Frontend
  typecheck and production build exit 0.
- Live, deployed stack (both images rebuilt and the four app containers
  recreated), `runtime/verify_ask_nl.py`, real model (`deepseek-chat`):
  - "ads 层都有哪些数据？" → `intent=help`, reply carries the 10 registered
    metrics (`ads_revenue … total_revenue`).
  - "现在平台上有哪些指标？" → the same list.
  - "ads_revenue 在 2026-09-12 当天相比 2026-09-11 的变化" → `intent=analyze`,
    scope `baseline=2026-09-11..2026-09-12 current=2026-09-12..2026-09-13`, report
    `PARTIAL` with `claim-1 observed_change "ads_revenue 变化 -2515.294902（-5.14%）"`
    and the limitation "数据完整性未验证：仅报告观测到的变化，未做归因结论".
  - "ads_revenue 在 2026-09-11 相比 2026-09-10 的变化" → the same question that
    previously produced an empty window now reports
    `变化 41.362403（0.08%）` with "变化 41.362403 在材料性阈值 ±244.389468590 内，
    不构成需要归因的变化".
- Live, empty-period guard (`runtime/verify_empty_period.py`, a UI-style
  `scope_patch` with `start == end`): turn ends `WAITING_INPUT`/`clarify`,
  `analysis_id` is `null`, message `missing` lists all four date fields and the
  question says "期间按左闭右开理解（含开始日期、不含结束日期）：只看一天请填 该日 到 次日。"
- Live click-through on the deployed UI (in-app browser, zh-CN, signed in as
  admin) — the exact path the report came from: opened `/ask` with no session,
  typed "ads 层都有哪些数据？" → 发送 was **enabled** (in the screenshot it was the
  faded, disabled button) → the click opened
  `/ask/61f013b9-6f1d-4e91-8e22-a61946460d25`, titled with the question, and the
  answer rendered the 10-row registry list (`[data-testid=message-metrics]` → 10
  `li`; first row `ads_revenue 广告收入 USD 维度：ad_network、app_version、country、
  platform`). Then "ads_revenue 在 2026-09-12 当天相比 2026-09-11 的变化" →
  `部分完成 / 部分结果`, claim `ads_revenue 变化 -2515.294902（-5.14%）`, a 限制与口径
  disclosure and the contribution chart. Console clean: no `error` events and no
  `console.error` during a reload. Screenshot kept as
  `runtime/acceptance/ask-nl-20261016.png`.

One deployment precondition, unchanged by this work: the conversational layer is
opt-in (`AIND_CONVERSATIONAL_ASK_ENABLED`, code default `0`). The local `.env`
sets it to `1`; a deployment that leaves it off still refuses `POST
/sessions/{id}/turns` with `CONVERSATIONAL_DISABLED`, and the page then falls back
to the one-shot analysis path, which needs a scope the panel can only render once
a session exists.

### A pre-existing layout defect found by the required width check
The §13 rule (`documentElement.scrollWidth <= innerWidth`) failed on this page at
1280px (29px) and 390px (46px): `.conversation-side` is a fixed 320px grid track,
and the scope panel's 指标 `<select>` reports its widest option
("归因收入（cohort 观测口径）（campaign_attributed_revenue）") as its content-based
minimum, which widened the column to 416px. It predates this change (neither the
select nor the grid was touched by it) and is closed here because it sits on the
page under repair: `min-width: 0` on the column and its children plus
`grid-template-columns: minmax(0, 1fr)` on the panel's `.stack`, so the rows wrap
instead of pushing the page. Re-measured on the rebuilt image: overflow **0 at
1440 / 1280 / 1024 / 768 / 390px**, with no offending element at any of them.

Not executed for this change, and not implied by the above: the service
integration matrix and A09 were not re-run — the control DB holds the live
conversation history, and the matrix's `clean_db` fixture wipes it, so
`test_conversation_flow.py`, `test_conversation_intent_flow.py`,
`test_conversation_recovery.py` and `test_analysis_flow.py` are
read-but-not-executed against these changes (every one of them drives
`data_complete(_state) = complete`, so the completeness change does not reach
them, but that is reasoning rather than a run). The deployed Playwright journeys
in `tests/e2e/` were not re-run either; the browser evidence above is the mocked
regression suite plus the live click-through. Known and not fixed: a period that
lies entirely outside the loaded data still reports `变化 0` rather than "该期间没有
数据" — the calculation carries no row coverage, so telling "genuinely zero" apart
from "no rows" needs a new evidence signal; it is recorded in `docs/todo.md`.

## 2026-10-16 Dataset deep links

The M6 `/datasets/:id` gap is implemented. Catalog items now link to the dataset
URL, and direct entry uses the existing governed detail endpoints. Schema,
semantic context, lineage and access requests remain on the same detail surface.
Browser history and refresh retain the selected ID. A keyed dataset view resets
request drafts, errors and notices when changing datasets. IDs are encoded as URL
path segments. Context failures render an error and hide the form; schema and
lineage reads wait for successful context, and their failures render errors.
No backend API, permission contract or database migration changed.

Executed in `/workspace/ai-native-data-platform/frontend`:

```bash
npm run typecheck
# exit 0
npm run build
# exit 0 (includes generated API types and TypeScript compilation)
npm run test:unit
# 11 passed
PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium npm run test:regression
# 49 passed / 1 failed (50 total)
```

The five new catalog cases all pass: catalog link + reload + return; direct entry
and back/forward across datasets with isolated drafts; missing dataset; denied
dataset without secondary reads; and access submission targeting the current ID
with isolated success notices and a translated return link. All Ask, chart and
i18n cases passed. The remaining Skills case times out at
`page.waitForEvent('filechooser')` after keyboard Enter on the new-version button.
The initial catalog-only invocation could not launch Playwright's absent bundled
headless shell. The completed run above explicitly used installed system Chromium.

Deployed browser journeys, the service integration matrix and A09 were not
executed for this frontend-only change. Mocked permission tests verify UI behavior;
server-side authorization is unchanged and was not revalidated on live services.

Failure follow-up: exported the unchanged `HEAD` frontend into
`/tmp/dataset-route-baseline/frontend` and ran the failing Skills case alone with
the same system Chromium; **1 passed**. The identical single-case command on the
current frontend also reports **1 passed**:

```bash
PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium npm run test:regression -- skills.spec.ts -g 'renders per-skill actions'
# baseline: 1 passed; current: 1 passed
```

This establishes an isolated pass on both trees, not the cause of the full-suite
timeout. The full-suite result remains **49 passed / 1 failed**; no Skills code
or assertions were changed to suppress it. `git diff --check` exits 0.

## 2026-10-16 Empty-period source coverage

Previously an empty grouped query yielded no rows and an ungrouped aggregate
could yield one NULL or zero row; the observer converted both into a numeric
zero. A question outside loaded dates could therefore claim `变化 0`, and one
empty period could resemble a full drop or increase.

Metric SQL now includes a collision-safe `COUNT(*)` evidence column with exactly
the same dates and filters. Submission checkpoints its alias. Observation
retains per-period/per-required-source counts, dataset IDs and query IDs; it
uses period-total evidence for nonadditive metrics and avoids counting the
breakdown a second time. Missing required input prevents composition from
zero-filling a missing source, skips contribution/driver extension, and leaves
comparison values undefined. Synthesis persists coverage in its calculation
artifact and emits PARTIAL, naming the affected half-open period with no change,
attribution, hypothesis or chart. This overrides even an explicit complete
flag. A real zero with matching source rows remains a valid zero comparison.

No extra query is submitted, and no database migration is needed. In-flight
queries or calculation checkpoints predating coverage must be resubmitted
(calculation version 3). Completed historical reports are not rewritten.
Row presence is not day-by-day completeness; counts summed over bounded grouped
results can be lower bounds. Populated undefined nonadditive/formula values
retain their existing failure behavior.

Executed from `/workspace/ai-native-data-platform`:

```bash
UV_CACHE_DIR=/tmp/aind-uv-cache uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# 509 passed, 2 dependency deprecation warnings, 6.58s
git diff --check
# exit 0
```

The new `backend/tests/unit/test_empty_periods.py` contributes 30 cases covering
empty grouped/ungrouped aggregates under all completeness states; populated
zero values; either period missing; empty eCPM and daily-average periods;
composition with a missing source (including COALESCE); filters; COUNT(column)
zero over actual NULL-bearing rows; alias collisions; invalid/missing coverage;
old checkpoint rejection; final synthesis and evidence payload retention; and
submission carrying the alias without extra queries. The SQL cases execute
compiled SQL against in-memory SQLite tables before invoking the real observer
and report builder. Two older observer fixtures now carry the count column and
descriptor alias that new queries return, preserving their existing numeric
assertions.

The first targeted run exposed those six old mocked-result cases missing the
new evidence column; their fixtures were updated. A subsequent run found a new
SQLite fixture using the wrong DAU table name, corrected to `demo.user_daily`.
The final complete static run above has no failures. The default uv cache was
read-only in this executor, so commands use the writable `/tmp` cache.

PostgreSQL/MySQL/Doris/Spark live acceptance, deployed browser journeys and
real-model A09 were **not executed** for this fix. No live dataset or historical
report was modified.

## 2026-10-16 High/medium priority workflows — current revision

Analysis planning now commits its reservation and bounded lease, closes its
session for the model call, then checks owner/fencing/expiry/checkpoint and
terminal status in a new transaction before submitting queries. Real PostgreSQL
regressions acquire the task lock with a 500 ms lock timeout while a controlled
provider waits on an event, then cancel or reclaim the task. Both late successes
and late errors leave cancellation intact; a replacement owner submits exactly
one query pair and preserves the earlier model-call charge. Memory extraction
uses its own bounded claim token/expiry and rejects the previous owner's reply.
Provider calls remain bounded by their configured HTTP timeout.

Skills rank candidates by description/question lexical overlap (English tokens,
Chinese bigrams), metric and preferred dimensions within the pinned-before-builtin
tiers. Required fields are unioned, explicit clears are retained, and missing
inputs or incompatible loaded metric scopes clarify. Final scope/applicability
and offered model selection IDs are rechecked. This is lexical ranking, not an
embedding service; skill prose cannot add arbitrary input constraints.

The transcript uses signed, session/direction-bound backward sequence cursors;
125-message integration and 130-message browser fixtures verify complete history
and isolation across sessions. Retry creates a new idempotent turn from frozen
original scope/skills; nonterminal origins, foreign sessions, missing keys and
busy sessions are rejected. Mobile scope uses native dialog focus handling;
closing or resizing retains edits. Authorized per-message diagnostics show the
analysis token/query/tool/repair counters and deadline. Browser regressions cover
pagination, retry preserving the unsent draft, drawer keyboard/focus behavior and
diagnostics. Skills keyboard activation targets its button directly, removing the
cross-test focus race without sleeps or dropping filechooser assertions.

Executed with isolated validation databases and generated credentials under the
ignored `runtime/validation/` directory. Existing control/source databases were
not truncated. Commands below require the usual `AIND_TEST_*`/database settings;
credentials were loaded internally rather than printed or committed.

```bash
UV_CACHE_DIR=/tmp/aind-uv-cache uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# 515 passed, 2 dependency deprecation warnings, 6.93s
uv run --project backend --frozen pytest backend/tests/integration/test_provider_network_bounds.py -q
# 3 passed, 14.00s (actual stalled sockets and TLS-enabled PostgreSQL)
cd frontend
npm run test:unit
# 11 passed
npm run build
# exit 0: generated API types, TypeScript and Vite production bundle
npm run test:regression -- --repeat-each=2
# 108 passed (54 cases twice), 4.6m, system Chromium
```

The prior 49-pass/one-failure Skills chooser result is superseded by the complete
repeated run. Live journeys exposed stale mixed-language expectations in the
administration test; it now selects zh-CN explicitly and checks the translated
catalog, health and approval labels. API/security integration assertions were
updated for existing unknown-dataset permission denial, unique repair keys and
configured source identities; permission isolation was not relaxed. Source
seeding supports repeated `--engine` selection so PostgreSQL/MySQL validation
does not require an unavailable Doris service.

The LLM adapter now supports the redacted process secret `AIND_LLM_API_KEY`,
which takes precedence over mounted files and can carry the platform proxy
placeholder unchanged. Tests verify it is absent from Settings repr/JSON and
model request bodies and present only in the authorization header. Existing
file-key tests still pass. No actual credential was written to source or docs.

**Real-model A09 was explicitly deferred by the user for later.** It was not
executed for this revision. The requested provider is
DeepSeek, model "v4.1 flash"; existing configuration names `deepseek-chat`, which
has not been confirmed against the endpoint's model list. The key file has no
usable credential, no environment key is bound, and the current HTTPS proxy
policy rejects `api.deepseek.com`. The saved environment configuration draft
adds only that destination and the `DEEPSEEK_API_KEY` requirement targeting
`AIND_LLM_API_KEY`. It preserves existing installation/start instructions and
other network settings. Draft save succeeded with `requires_publish=true`;
review/save and publish in environment settings are still needed to apply it.
Saving a draft neither supplies the credential nor changes this running runtime.
The live-service browser runs use the deterministic provider; historical A09
passes are not evidence for this revision or for the requested model version.

Full Doris/DataHub acceptance remains unexecuted. Docker uses vfs on a 32 GB disk;
fetching the Doris FE alone brought usage to roughly 22 GB, leaving insufficient
space for its BE and DataHub stack. Only the newly fetched unused FE image was
removed and its BE pull stopped, returning usage to roughly 5 GB. Existing Docker
storage/daemon settings and pre-existing containers were preserved. The Doris
socket-stall test exercises the adapter protocol, not a deployed Doris server.
Spark was not rerun. These limits must remain visible beside passing counts.

Live-service browser verification (API + query/agent workers + Vite, isolated
PostgreSQL databases, deterministic provider):

```bash
# With E2E_BASE_URL, E2E_ADMIN_USER/PASSWORD, E2E_METRIC,
# E2E_ANALYSIS_METRIC and PLAYWRIGHT_EXECUTABLE_PATH configured:
cd frontend
npx playwright test
# 7 passed, 35.0s
```

This covers SQL/results/history, write rejection, analysis/chart/drilldown,
conversation/follow-up, Skills upload preview, administration/grants/request queue
and viewer capability gating. It is a locally running service acceptance, not a
remote deployment or a real-model run.

Final service matrix (includes the three protocol/TLS tests above):

```bash
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --tb=short
# 97 passed, 14 skipped, 25 deselected, 2 dependency warnings, 193.55s
```

Thirteen skips require Doris and one requires the deployed DataHub stack.
PostgreSQL and MySQL were real services; the 97 passes include the new
priority-workflow tests and the existing PostgreSQL lock-contention cases.
The earlier 94-pass run preceded adding the three network-bound tests; the
final 97-pass run supersedes it.

## 2026-10-16 Account settings, session-list pagination and Spark cancel RPC race

This round implements the requested high-priority product gaps: personal settings,
per-account preferences, and access to session history beyond the first 20. It also
reproduces and fixes Spark's suspected cancellation transport race. Full deployed
Doris/DataHub acceptance remains pending for the resource reason below. DeepSeek
and real-model A09 remain explicitly deferred by the user.

Migration `d2e6a9f18c40` adds display_name, nullable locale/timezone and
settings_version to existing users without adding a table. GET `/auth/me` and
login return them. PATCH `/auth/me` accepts only those editable fields plus the
required expected version; it locks the authenticated user, validates locale and
IANA timezone, rejects privileged/foreign fields, and records changed field names
in audit. Stale saves return 409 and cannot overwrite a newer version. Existing
users default to an empty name and browser language/timezone. Migration was
applied to the isolated integration and live-service databases, preserving the
existing live accounts. Schema verification: **31 tables, 11 named state checks,
25 indexes**, exit 0.

`/settings` (and `/profile` redirect) shows read-only account identity/roles and
human-readable capabilities, with editable display name, language and timezone.
Changes persist to the user row and restore on a fresh browser context/device.
A dirty form retains its original version across background refreshes; failed or
conflicting saves retain its contents, and explicit reload discards them. Header
language changes save only that account's locale. Unset language follows browser
language rather than a previous guest's stored preference; unset timezone follows
browser timezone. Timezone changes rerender timestamp subscribers even if locale
is unchanged, and never alter query periods. Account changes do not write the
guest language key. Logout/401 reset preferences; cross-tab authentication
notifications clear caches/reload other workspace tabs. Same-account preference
notifications refetch the server instead of trusting stored preference values.
Browser storage contains notification markers and guest locale, not credentials.

The conversation rail follows the API's next_cursor and deduplicates session IDs,
with pending/error/retry/load-more states. Existing backend offset/updated-at
ordering remains unchanged; pagination is not a snapshot guarantee under concurrent
reordering. Real 45-session backend and browser fixtures reach every session and
open the oldest one. This is independent of message pagination within a session.

Executed on the existing disposable validation services/databases (generated
credentials remain in ignored runtime files; they were not printed or committed):

```bash
UV_CACHE_DIR=/tmp/aind-uv-cache uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# 517 passed, 2 dependency deprecation warnings, 14.89s
# With isolated PG/MySQL/TLS settings and AIND_TEST_SPARK_HOST=127.0.0.1,
# AIND_TEST_SPARK_PORT=11000:
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --tb=short
# 111 passed, 14 skipped, 25 deselected, 2 dependency warnings, 158.22s
uv run --project backend --frozen python backend/scripts/verify_schema.py
# schema verification OK: 31 tables, 11 named state checks, 25 indexes
cd frontend
npm run test:unit
# 12 passed
npm run build
# exit 0: OpenAPI types, TypeScript and production Vite build
# With E2E_BASE_URL and the disposable live admin/metric settings configured:
npx playwright test
# 8 passed, 44.4s (actual API/query worker/agent worker, deterministic model)
npx playwright test settings.spec.ts
# 1 passed, 8.7s after final preference/authentication changes
```

The new account integration module contributes 13 cases: durable preferences across
sessions, viewer/admin isolation, stale version rejection, null reset, preservation
of untouched fields, unknown/privileged field denial, invalid names/locales/timezones,
CSRF/version requirements, and 45-session history. The new four mocked browser
cases cover fresh-device restoration, account switching and failed-save drafts,
all session pages, and cross-tab conflict handling. The account-switch case also
checks that a second workspace tab clears its old identity on logout. A frontend
unit case verifies timezone formatting and guest/account isolation. Live E2E creates
a disposable viewer, saves preferences, reloads, signs in on a fresh context and
returns to the unchanged administrator. Desktop and 390px settings captures are
in `/tmp/aind-settings-desktop.png` and `/tmp/aind-settings-mobile.png`; the mobile
page has no horizontal document overflow.

Spark reproduction used pinned `apache/spark:3.5.3`, Impyla 0.24.0 and a standalone
NOSASL Thrift Server on loopback 11000, with an embedded metastore. A controlled
pause before reading a real GetOperationStatus response allows CancelOperation to
run concurrently. Before the fix the shared client consumed the wrong response:

```json
{"status_rpc":"corrupted","exception":"KeyError","detail":"None","cancel_detail":"Spark cancel requested"}
```

After isolating cancellation on a fresh connection, the same probe reports:

```json
{"status_rpc":"valid","executing":false,"cancel_detail":"Spark cancel requested"}
```

The checked-in live regression additionally requires server CANCELED_STATE and a
successful later SELECT. Focused Spark run: **10 passed** (9 unit, 1 real service).
Cancellation closes its control transport on success/failure, leaves the execution
cursor/client untouched, stops a pre-cancelled query before launch and resends a
cancel arriving during operation-handle publication. It still returns an
unconfirmed request; terminal confirmation belongs to the executor/monitor.
`infra/spark-preview/compose.cancel-test.yaml` and `cancel-test-hive-site.xml`
provide the isolated fixture; Compose config validation exits 0. The command-line
container used for the run has the equivalent image, XML and Spark settings.
This does not validate Spark read-only source authorization, secure authentication,
shared HMS, listener lineage or Spark browser end-to-end coverage.

Resource limits: Docker remains vfs on the 32 GB filesystem. Before this round
roughly 24 GB was available; adding the standalone Spark fixture left roughly
18 GB. The earlier measured Doris FE pull alone had increased filesystem use
from roughly 5 GB to 22 GB, without its BE or the DataHub dependencies. The complete
Doris/DataHub stack was not retried on this disk and its 14 tests remain skipped
(13 Doris, one deployed DataHub). Existing Docker storage settings, base services
and user data were preserved. No mocked metadata response or protocol-stall test
is counted as deployed Doris/DataHub acceptance.

During final browser runs, two existing test-order races surfaced: the mobile
scope test checked body overflow before React's resize cleanup ran, and the filter
editing test began its next turn after request capture but before the previous
mocked response/context completion. They now wait on their observable completion
conditions (overflow restored; confirmed scope/composer ready), with no sleeps,
removed assertions or production behavior changes for these tests.

Final mocked-browser run after those explicit completion waits:

```bash
PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium npm --prefix frontend run test:regression
# 58 passed, 2.1m
```

An earlier complete run also passed 58; a subsequent run surfaced the filter
fixture race (57 passed / 1 failed), which the final run above supersedes. The
production filter/send behavior was not modified to make the test pass. Profile
OpenAPI was finally tightened so an explicitly null display name is rejected by
the request schema as well as validation; the affected 13 integration cases and
static suite were rerun, and frontend types/build regenerated.

## 2026-10-16 Continued TODO — workspace management, passwords and contracts

Implemented after the user asked to continue the backlog:

- Conversation rename, archive/restore, include-archived browsing, preserved
  conflict drafts and read-only archived transcripts. Management actions require
  the existing analysis capability and send the current context version. Owner,
  busy and version checks remain server-authoritative.
- Saved SQL frontend save/list/load/delete and pagination. Loading fills SQL and
  datasource without executing, and unavailable sources cannot load. Tests assert
  that saving/loading creates no query job or run request.
- Self-service password change with current-password check, secret DTO fields,
  new-password length/reuse validation, CSRF and bounded failures, audit and
  atomic revocation of all old sessions. Cookies are actually deleted. Login now
  holds the user lock through verification/session creation, serializing with
  rotation. A real NOWAIT lock probe during controlled login verification and
  subsequent rotation verify that the issued session is revoked.
- Actual quota boundaries (50 skills, 20 versions) and idempotent hash replay at
  capacity, plus dedicated session SSE framing/replay/ownership/idle contracts.
  The SSE tests found and fixed an empty-idle stream waiting until its duration
  limit because it had never seen an event; it now sends stream.end immediately.

Executed verification so far:

```bash
UV_CACHE_DIR=/tmp/aind-uv-cache uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# 520 passed, 2 dependency deprecation warnings, 11.74s
uv run --project backend --frozen pytest backend/tests/integration/test_password_change.py backend/tests/integration/test_workspace_actions.py backend/tests/integration/test_skills_api.py -q --tb=short
# 15 passed, 2 dependency warnings, 20.14s (before adding the concurrent-login probe)
cd frontend
npm run build
# exit 0: OpenAPI-generated types, TypeScript, production Vite bundle
npm run test:unit
# 12 passed
# Disposable live API + query/agent workers + frontend; deterministic provider:
npx playwright test
# 9 passed, 1.3m
```

The live settings journey changes a disposable viewer's password, verifies a
second device receives 401, signs in with the replacement and returns to the
unchanged administrator. The new workspace journey persists rename/archive/restore
and saves/loads/deletes a real SQL template. Four new mocked browser cases cover
management/conflict retention, saved SQL pagination without execution, and password
confirmation/failure/success. The logout selector was narrowed to its exact label
after the new password form introduced a second button containing “退出”; the
previous 61-pass/one-failure mock and 8-pass/one-failure live runs are superseded
by the complete reruns, with all assertions retained.

Two quota tests initially expected HTTP 422; inspection confirmed SKILL_TOO_LARGE
maps to HTTP 413, and they now assert that existing protocol contract. One service
matrix and a focused password batch were inadvertently started on the same
disposable control database; clean_db truncation caused fixture collisions. Those
runs were stopped and are not acceptance evidence. The full matrix is rerun alone;
run database pytest batches serially for each AIND_DATABASE_URL. This affected only
the disposable integration database, not the separately provisioned live-service
history or pre-existing databases.

Doris/DataHub skips and the deferred DeepSeek/A09 remain unchanged. No multi-worker
load/soak, Spark read-only authorization/shared-HMS lineage, semantic memory,
team-scope memory or model-driven tool selection is claimed by this round.

Final browser and focused checks for this round:

```bash
PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium npm --prefix frontend run test:regression
# 62 passed, 3.3m
# After the last guard against loading a template before active-query detail arrives:
PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium npm --prefix frontend run test:regression -- workspace-management.spec.ts
# 4 passed, 15.2s
uv run --project backend --frozen pytest backend/tests/integration/test_password_change.py -q --tb=short
# 5 passed, 2 dependency warnings, 9.02s
```

The concurrent-login probe initially assumed the outer executor's thread name was
also the FastAPI handler thread name. FastAPI runs sync handlers in its own worker
thread, so its event never fired. The probe now gates the first verification call
instead; its real PostgreSQL NOWAIT check then confirms the held user lock. This
was a test synchronization correction, not a relaxation of the lock assertion.
The intermediate matrix's 119 passes / one failed probe is superseded by the
subsequent isolated full-matrix run recorded below. No database test batches ran
concurrently during that final matrix.

Final isolated service matrix (same pinned PG/MySQL/TLS and standalone Spark
fixtures as the preceding round):

```bash
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q --tb=short
# 120 passed, 14 skipped, 28 deselected, 2 dependency warnings, 210.54s
```

This supersedes the interrupted shared-database batches and the failed test-thread
probe. The 14 skips remain 13 Doris-dependent cases and one deployed DataHub case;
three newly added pure SSE contracts account for the deselection increase from
25 to 28 (they pass in the static suite). No test assertion or skip condition was
removed to obtain the final result.


## 2026-10-16 Remaining features: closed actions, explanations, semantic/team memory and Top-10 charts

Implemented scope:

- Explain checks session ownership, current source grants and report evidence
  expiry, then renders templates from the claim-bound calculation artifact.
  Baseline/current/change strings remain verbatim; no arithmetic is delegated to
  prose and no source query is submitted. Missing historical artifacts retain a
  nonnumeric method explanation instead of reconstructed values.
- PlanningSelection chooses compare/breakdown (and up to three allowed dimensions)
  or consideration of a declared driver. Explicit user dimensions remain binding.
  A subsequent ObservationSelection chooses finish or driver_decomposition.
  Materiality/target/metric declaration/depth/query/tool gates still control the
  only optional extension. Both calls use prepare/reserve, network without a DB
  session, then fenced apply. Cancellation during either stage discards late
  successes and errors. This is bounded adaptive analysis, not arbitrary tools.
- The OpenAI-compatible adapter permits one format-repair request against the
  original prompt and closed schema. Invalid model output is not replayed as
  instructions. Aggregate tokens and physical calls are counted, retry obeys
  token/time/call limits, and planning durably reserves both possible requests
  within remaining tokens. A crashed request retains its worst-case charge.
- Local domain semantic retrieval maps bilingual analytics synonyms into sparse
  concept vectors plus word/character features, ranks by cosine similarity and
  retains reuse/recency order as a tie-break. Up to 200 metric-scoped candidates
  are recalled; scope and source grants are filtered before ranking/top-8.
  This is a bounded-domain local encoder, NOT a general pretrained embedding
  service, and no external embedding effectiveness claim is made.
- Migration e4b8a1c79d20 adds teams, users.team_id and memory team/owner columns.
  Existing source-linked rows become private to their source author; orphans are
  invisible. Administrators create teams and assign/clear one current team per
  user in the permissions UI/API, with CSRF, capability checks and audits. Team
  changes do not move old memories. Lists, counts, curation, prompt recall and
  historical analysis-memory reads require both current scope and source grants.
  Extraction refuses team changes (including personal -> team), and serializes
  duplicate checks with an advisory transaction lock. Shared-memory provenance
  does not link another author's private analysis as if analysis access were shared.
- Charts default to ten largest absolute changes, expand/collapse loaded rows and
  load further bounded pages through the existing permission-checked endpoint.
  Tables and bars share the same visible rows; exact Decimal strings remain in
  titles and hidden/stored-dropped counts remain explicit. Chart identity resets
  expansion. Datasource mono-id, result wrapper/name styles and unused assistant
  message class tokens were cleaned up.

Executed validation (isolated control/source databases; no concurrent DB pytest
batches, workers do not consume the test queues):

```bash
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# 531 passed (final tree)
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q
# 127 passed / 15 skipped / 28 deselected (PG/MySQL/TLS)
# 13 Doris skips, one deployed DataHub skip, one unconfigured standalone Spark skip
AIND_TEST_SPARK_HOST=127.0.0.1 AIND_TEST_SPARK_PORT=11000 \
  uv run --project backend --frozen pytest backend/tests/integration/test_spark_cancel_transport.py -q
# 1 passed, real standalone Spark 3.5.3
uv run --project backend --frozen pytest backend/tests/integration/test_priority_workflows.py -q
# 15 passed, final affected checkpoint/budget/team cases
# Additional final C05 explain + memory visibility + extraction-fencing rerun: 3 passed
uv run --project backend --frozen python backend/scripts/verify_schema.py
# schema verification OK: 32 tables, 11 named state checks, 25 indexes
cd frontend
npm run build
# exit 0; generated OpenAPI types, tsc and Vite production bundle pass
npm run test:unit
# 12 passed
PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium npm run test:regression
# 63 passed / 1 failed: new team error test exposed missing role=alert
PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium npm run test:regression -- teams.spec.ts chart.spec.ts
# After role=alert fix: 8 passed, including narrow chart, Top-10/expand/paging,
# team create conflict preserving the draft, assignment and removal
```

The full mocked-browser attempt is not counted as 64 passed. Its one failure was
corrected and the affected cases rerun. The service matrix and standalone Spark
results are separate runs, not one combined 128-pass matrix. Final focused checks
cover later membership-change and explanatory evidence assertions. Test cache
was redirected to /tmp/uv-cache because the default home cache is read-only.

Not executed: current real DeepSeek endpoint/model identity/A09/large candidate-pool
model choice (explicitly deferred by the user); complete Doris/deployed DataHub;
Spark source read-only authorization/shared-HMS listener lineage/browser journey;
DataHub auth-enabled mode; multi-session/worker load and soak; broad paraphrase
contention; historical report reruns. Existing stored reports remain unchanged.
Apply the new migration before starting the updated application/workers. These
results do not claim a new production deployment or new real-model acceptance.

## 2026-10-16 Operational completion: defaults, reruns, shared auth, Spark TLS and sustained load

Delivered migration **f6a12c83d095** adds account analysis defaults and shared
PostgreSQL authentication failure windows. Settings UI/API save an active metric,
allowed dimensions and off/auto skills; new sessions snapshot preferences below
explicit input, without inventing dates or completeness. Settled analyses can be
rerun through the UI/API with owner/capability/source-grant/CSRF/idempotency checks,
creating a new child under current contracts with unknown completeness. Old reports
are unchanged. Invalid old scopes give validation errors.

Production login/password verification now locks a shared failure key before the
user row. Windows survive fresh processes, failed audits preserve counts, expired
slots are reclaimed and new-key capacity is bounded. A deployed test varied six
X-Forwarded-For headers against one unknown account and observed
**401,401,401,401,401,429** with `--no-proxy-headers`. SSE already reads persisted
events across API instances; no in-process fan-out replacement was necessary.
Query/analysis/session streams now check auth-session expiry/revocation and active
user state before another poll, plus current grants before publishing data/events.
A separate API instance replay/resume and real DB auth revocation test passed.

Spark now supports verified TLS and PLAIN-over-TLS configuration. CA/hostname
verification is mandatory; plaintext PLAIN is rejected. Scalar parameters become
AST literals and must preserve the exact Spark AST after serialization; missing,
extra, anonymous, nested and nonfinite bindings fail closed. There is no Impyla
string formatting. Real Spark 3.5.3 executed quote/backslash/newline/SQL-fragment
values unchanged, including through the Query Gateway and browser. Untrusted CA
rejection and the existing cancellation transport regression passed. PLAIN config
forwarding is unit-tested; no real identity authenticator was exercised.

Observed commands/results (UV_CACHE_DIR=/tmp/uv-cache, isolated test databases):

```bash
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# 550 passed, includes Spark attack corpus/AST scalar binding and SSE closure
# With existing PG/MySQL/TLS + standalone Spark + TLS fixture environment:
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q
# 136 passed / 14 skipped / 29 deselected
# 13 Doris + 1 deployed DataHub skips; not counted as supported
AIND_TEST_SOAK_SECONDS=120 uv run --project backend --frozen pytest backend/tests/integration/test_operational_todo.py -q -s
# Cross-author sustained run: 6 passed (before the additional capacity case)
# 360 sessions, 45 waves, 126.776 seconds, two authors sharing one team
# 3 analysis / 2 query workers; 4 extraction workers, 360 extractions in 4.228s
# exactly one deduplicated memory; no active queue or execution-lease leftovers
# per-wave p95 processing time 2.855s (fixture/deterministic-provider sample only)
uv run --project backend --frozen pytest backend/tests/integration/test_operational_todo.py -q
# Final affected operational suite: 7 passed (includes capacity/expiry reclamation)
uv run --project backend --frozen python backend/scripts/verify_schema.py
# Deployed schema verification OK: 33 tables / 11 named state checks / 26 indexes
cd frontend
npm run build
# generated types + tsc + production bundle exit 0
npm run test:unit
# 12 passed, after fixing a missing new UI translation
PLAYWRIGHT_EXECUTABLE_PATH=/usr/bin/chromium npm run test:regression
# Final full mocked-browser suite: 66 passed
npm run test:e2e
# Isolated live deployment: 10 passed, including Spark TLS/parameter browser test
npm run test:e2e -- operations.spec.ts
# Added live defaults -> inherited scope -> child rerun / unchanged original: 1 passed
```

The sustained-load test uses real PostgreSQL queries, queues, claims, calculations
and result files, with deterministic planning and scripted memory extraction.
Two different users share a team, exercising the scope lock rather than merely
serializing on one user's row. Five fresh Python processes observed the same
failure window (three allowed failures followed by two denials); twelve concurrent
new keys against capacity two admitted exactly two, then reclaimed expired slots.
`make test-load SOAK_SECONDS=120` and `scripts/dev.ps1 test-load` expose the harness;
PowerShell execution itself was not verified on this Linux host.

Live validation used **new isolated control/source databases**, API port 18009 with
two Uvicorn workers, query/agent workers, frontend port 3020 and the separate TLS
Spark fixture on 11001. Both migrations and source grants were applied there;
existing working databases and port 18008 were not replaced. Initial browser
attempts rejected unsafe requests because the new frontend origin was not yet
in AIND_CORS_ORIGINS; correcting the deployment setting yielded the 10-pass run.
The TLS fixture setup script is checked in; generated keys/passwords remain in
ignored runtime directories. It uses NOSASL and embedded HMS and is not a source
read-only authorizer or shared-HMS lineage deployment.

Remaining/unverified: complete Doris/DataHub and DataHub auth-enabled mode (the
32 GB vfs host lacks sufficient disk footprint for the full stack); actual Spark
PLAIN identity verification/read-only authorization/shared-HMS listener lineage;
16 GiB full-stack proof; overnight/high-scale endurance and real-model throughput;
user-deferred DeepSeek identity/A09/large-pool Skill evaluation. General pretrained
embeddings, multi-host HA/object storage and other roadmap integrations are not
silently claimed. Actual business report reruns still need owner-selected reports;
no blanket rewrite or replay of every historical artifact was performed.

## 2026-10-16 Spark PLAIN identity over verified TLS

The remaining identity-verification TODO is now exercised against actual Spark
3.5.3, Impyla 0.24.0 and Hive CUSTOM authentication (SASL PLAIN on the wire).
`infra/spark-preview/setup_tls_fixture.py --auth PLAIN` compiles the checked-in
single-identity Scala authenticator using the compiler/Hive API already bundled
in the pinned Spark image. No additional image or dependency download is needed.
The isolated loopback fixture used port 11002, container
`aind-spark-plain-acceptance` and ignored `runtime/validation/spark-plain-v2/`.
Existing Spark instances, APIs, workers and user databases were left in place.

Private credential/config/keystore files use mode 0600. Credentials are random
and never printed, passed as command-line literals or committed. The server
compares username and a salted password digest in constant time. Generation
refuses nonempty directories and paths outside ignored runtime/.

Observed commands/results:

- `uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q`:
  **550 passed**, 2 existing warnings, 7.97 seconds.
- With the existing dedicated control DB configuration and all three Spark
  fixture prefixes configured (`AIND_TEST_SPARK_*`, `AIND_TEST_SPARK_TLS_*`,
  `AIND_TEST_SPARK_PLAIN_*`), run
  `uv run --project backend --frozen pytest backend/tests/integration/test_spark_plain_auth.py backend/tests/integration/test_spark_cancel_transport.py backend/tests/integration/test_spark_secure_parameters.py -q`:
  **10 passed**, 11 pinned-Thrift hostname-verification deprecation warnings,
  5.73 seconds. PLAIN credentials are supplied only through
  `AIND_TEST_SPARK_PLAIN_CREDENTIALS_FILE`; see the fixture README for setup.

The five new identity cases prove valid login and exact hostile-string/scalar
parameter execution; wrong username, wrong password and empty password rejected
by SASL before a query session; and an untrusted server certificate rejected with
an actual `SSLCertVerificationError` retained as the Thrift exception's cause.
The cancellation race test now runs for NOSASL and PLAIN, with an independently
TLS/authenticated cancel transport, an intact concurrent status reply, server
CANCELED_STATE and a subsequent successful SELECT. Existing NOSASL/TLS scalar
and untrusted-certificate cases also pass. The fixture helper allows ten seconds
for a cold Spark session/metadata initialization; it does not relax TLS checks.

This is a custom **test** authenticator with one ephemeral identity, root server
process and embedded metastore, not an enterprise identity service or source
read-only authorizer. Full Doris/DataHub, auth-enabled GMS, shared-HMS listener
lineage, 16 GiB full-stack proof and user-deferred DeepSeek/A09 were not run in
this round. No product API, schema migration or frontend behavior changed, so
frontend/browser and the full service matrix were not rerun.

## 2026-10-16 External security acceptance gates and DataHub failure handling

Implemented executable acceptance for remaining externally supplied services,
without claiming that unavailable authorization infrastructure is delivered:

- `scripts/verify_spark_readonly.py`: private owner/reader JSON connection files,
  verified TLS/PLAIN, same endpoint and pre-created dedicated aind_authz_* namespace.
  The owner seeds a random Parquet table; seven reader mutations must be denied
  for authorization reasons, with original columns/data readable after every
  denial. First allowed write, syntax/unsupported/missing-table/transport error or
  cleanup failure fails. Only its generated tables are cleaned. DELETE/UPDATE
  unsupported by the fixture are not treated as authorization evidence.
- `scripts/datahub_token.py --verify-auth`: no writes; explicit HTTP 401/403 for
  anonymous and random-invalid bearer, followed by valid-token HTTP 200 and the
  requested dataset field. Private --token-file / DATAHUB_TOKEN inputs added;
  legacy --token retained. Token storage is private, atomic and preserves extra
  fields; failed replacement leaves the original secret intact.
- `DataHubAdapter` now fails with METADATA_UNAVAILABLE for unexpected HTTP status,
  malformed/missing GraphQL envelope/operation/search fields. Valid empty results
  and explicit dataset:null retain their meaning. Remote diagnostic text is no
  longer reflected into public errors. The helper had incorrectly accepted some
  non-200/empty JSON responses as a verified token; that false success is fixed.
- Opt-in actual-source tests for read-only policy and authenticated GMS. The GMS
  case also reads through the platform adapter and pinned search document. Unset
  external endpoints skip; explicit but partial/broken configuration fails.

Observed checks:

- Full static command `uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q`:
  **609 passed**, 2 existing warnings, 7.20 seconds. Includes successful mock
  denial logic, false-positive rejection, namespace guards, cleanup failure,
  malformed GMS responses, auth path sequencing and atomic-secret replacement.
- Real Spark PLAIN/TLS combined check before adding the optional source-policy
  entry point: **11 passed / 1 skipped** (GMS absent), 24.27 seconds. Ten existing
  identity/binding/cancellation cases plus the negative-control verifier case.
- Final changed real-service subset
  `pytest backend/tests/integration/test_spark_readonly_acceptance.py backend/tests/integration/test_datahub_authentication.py -q`:
  **1 passed / 2 skipped**, 3.05 seconds. The full standalone CLI ran against
  actual unprotected Spark and returned exit 1 with insert:write_allowed. Owner
  inspection confirmed no verifier tables remained. The two skips are the absent
  actual read-only owner/reader configs and absent auth-enabled GMS URL. They are
  not positive authorization/authentication evidence.
- `scripts/datahub_token.py --check --gms-url http://127.0.0.1:18080` correctly
  returned exit 1 and reported the GMS unreachable, without asserting that
  authentication was enabled. No token was read or written for that check.

No API schema, database migration or frontend change. Frontend/browser, the full
engine matrix, actual authorizer and GMS, shared-HMS listener lineage, 16 GiB
resource proof and deferred DeepSeek/A09 were not rerun. Original working services
and databases remain in place; only the isolated Spark fixture was restarted for
these tests. Setup/run commands are in the runbook and Spark fixture README.

## 2026-10-16 Effective resource preflight

Fixed the doctor's physical-RAM-only/core-budget-only checks. It now records
physical bytes and visible process cgroup v1/v2 capacity, honors tighter ancestor
limits (including zero), reads usage/peak and v2 OOM counters where available,
and considers Docker VM memory when applying the selected budget. Default core
and non-strict behavior are retained; --profile full --strict and matching
Make/PowerShell doctor-full entry points select the existing conservative full
budget without launching containers/services or changing resource limits.

Observed:

- `uv run --project backend --frozen pytest backend/tests/unit/test_doctor_resources.py -q`:
  **14 passed**, 0.03 seconds. Covers inherited/unlimited/zero/namespace v1/v2
  cases, process/Docker capacity, exact RAM/disk byte thresholds and strict full CLI.
- Full static suite: **623 passed**, 2 existing warnings, 7.45 seconds, using
  `uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q`.
- Actual full strict JSON preflight: **exit 1**. Physical 33.3 GiB, visible process
  limit 32 GiB, Docker memory 33.3 GiB, vfs storage, repository free disk 13.3 GiB.
  Reported insufficient full-profile repository disk (60 GiB estimate), and the
  already occupied control/source DB ports. It correctly separates physical and
  capped memory; the environment was not resized and no working service changed.

No engine/browser/model journeys rerun for this stdlib preflight change. PowerShell
entry point was added but not executed on this Linux host. Current/peak cgroup
usage covers the entire executor; neither this report nor mocked smaller-memory
cases establishes full-stack behavior on a 16 GiB host. Actual authorizer/GMS/
lineage/resource acceptance and user-deferred DeepSeek remain open.

## 2026-10-16 Larger authorized memory recall

Fixed a candidate-starvation problem: the planner previously selected 200
high-reuse metric memories before checking source grants. Inaccessible rows could
consume that window and hide a useful accessible memory. Recall now filters team/
private scope, active reader, nonempty provenance and current discover/query on
**every** query dependency in SQL before LIMIT, then ranks at most **1000**
authorized proposed/confirmed candidates. Grants can come from different roles;
expired grants do not count. Prompt injection stays capped at **eight**, including
an oversized internal limit request; zero requests do not touch the database.

Knowledge list and status-count reads use the same source authorization predicate
in SQL. Each performs one SELECT instead of loading rows and issuing per-row
  scope/source/grant checks. Missing identity or explicit empty roles fail closed without row access;
disabled users cannot read private memories through repository calls either.
Existing single-record read/curation checks remain in place. No API shape,
migration, dependency or model service was added.

Observed validation:

- Full static suite: **623 passed**, 2 existing warnings, 8.39 seconds:
  `uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q`.
- Isolated real PostgreSQL suite: **5 passed**, 2 existing warnings, 9.36 seconds:
  `uv run --project backend --frozen pytest backend/tests/integration/test_memory_recall_pool.py backend/tests/integration/test_priority_workflows.py::test_memory_team_scope_list_counts_curation_and_historical_reads -q -s`.
  Dedicated control/source test databases were configured; these fixtures truncate
  their configured control DB, so never use working databases or concurrent tests.
- The 1802-row synthetic case has 1051 eligible rows, 650 rows with an ungranted
  additional dependency, 100 rows lacking provenance and one rejected row. A
  relevant target below the original top-200 reuse window is retrieved. The ranker
  receives exactly 1000 authorized candidates, no denied/orphan/rejected rows,
  and emits eight hints. Retrieval issues exactly **one SQL statement**; list and
  aggregate counts each issue one additional statement. This is query-count and
  bounded-recall evidence, not a throughput SLO.
- Actual DB cases cover expired discover and query separately, both grants split
  across roles, grant deletion/restoration in the same session, no identity/roles, disabled private owner, another team's/private
  memory, team departure and historical read/curation isolation. Former mocks
  were updated to the authorized-repository contract; per-row fake filtering is
  replaced by actual PostgreSQL list/count-before-LIMIT regression coverage.

The encoder remains the local bilingual domain concept/word/character vector;
no general pretrained Embedding or real-model evaluation is claimed. Candidate
selection still has a bounded reuse/recency window, so it is not global semantic
search over unlimited history. Extraction's near-duplicate comparison retains its
separate 200-row window. No browser/full-engine/DeepSeek/overnight tests were run
for this backend retrieval change. Existing external deployment gaps remain open.

## 2026-10-17 Continuous learning and concurrent reuse accounting

A new cold -> learn -> warm loop reproduced a real counter race: all eight
analyses in the second wave quoted the shared memory, but reuse_count was only
**2**, instead of **8**. The former SELECT/read/increment/write sequence lost
updates between planning workers. The previous soak extracted all memory only
after analyses finished, so it could not reveal this warm-use race.

The repository now increments in SQL in the analysis transaction. Multi-row
updates acquire locks in ID order, duplicate IDs count once, last_used_at cannot
move backwards, and rollback removes the increment. A real PostgreSQL regression
preloads stale ORM instances in four sessions, then concurrently updates three
shared rows in opposing orders. Every row reaches exactly four uses; a rollback
adds no committed use and a newer stored timestamp is preserved.

The load fixture now uses stateless scripted model planning and four extractors
after **each** wave. The next wave must quote the learned ID; every wave checks
exact cumulative reuse, one deduplicated memory, terminal extraction status and
no active task queue/execution lease leftovers. Two team members exercise shared
scope with 3 analysis and 2 query workers. No real-model call is implied.

Observed short checks: the new four-session stale-instance regression **1 passed**
(2.86 seconds); the initial two-wave smoke **1 passed**, with 16 analyses, eight
warm uses, reuse_count eight and one memory. Full static validation **623 passed**,
2 existing warnings, 12.02 seconds. The longer run below uses dedicated disposable
control/source PostgreSQL databases; working app databases/workers are untouched.

Longer observed run (2026-10-17):

```bash
AIND_TEST_SOAK_SECONDS=600 uv run --project backend --frozen pytest \
  backend/tests/integration/test_operational_todo.py \
  backend/tests/integration/test_memory_recall_pool.py \
  backend/tests/integration/test_priority_workflows.py::test_memory_team_scope_list_counts_curation_and_historical_reads \
  -q -s
```

With isolated test database settings loaded, the final batch reported **13 passed**,
2 existing warnings, **626.73 seconds**. The continuous portion completed **1480
analyses / 185 waves in 600.498 seconds**. All **1472 warm analyses** quoted the
same memory and its reuse_count was **1472**; all 1480 extractions finished,
near-paraphrases left one memory, and no active task-queue/execution lease was
left after each wave. Cumulative extraction time across the waves was 25.41 seconds.
The remainder also reran the large-pool/grant-expiry/team-isolation and multi-row
counter rollback cases, plus the operational auth/rerun/defaults checks.

Linux quiescent observations after warmup -> final: RSS **139940 -> 141844 KiB**
(about +1.9 MiB), peak RSS **201604 -> 201604 KiB**, threads **4 -> 4**, open FDs
**14 -> 14**. These samples do not prove absence of all leaks or overnight
stability. There was no real model, full Doris/DataHub, source authorizer/GMS or
16 GiB deployment in this test. Evidence is saved under ignored
`runtime/validation/learning-soak-20261017.log` and `.json`. Existing external
acceptance/deferred-model items remain pending.
