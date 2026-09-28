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
