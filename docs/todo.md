# TODO and stage acceptance record

Project: AI-Native Data Platform (spec `AI-Native-Data-Platform-Implementation-Spec.md`, v1.0)
Scope currently delivered: **M0-M4 plus the first M5 governed-analysis vertical
slice**. M5 real-model evaluation and adaptive analysis remain open.
Rule followed: a stage counts as done only when its gate ran on real services; tests
that were not executed are marked "not executed" and never counted as passed.

Legend: `[x]` done and verified · `[~]` partially verified (see note) · `[ ]` not started.

---

## M0 - version freeze, machine check, official compose, provider APIs

Deliverables: `infra/versions.env`, `backend/uv.lock`, `frontend/package-lock.json`,
`docs/compatibility.md`, `scripts/doctor.py`, `infra/datahub/compose.pinned.yaml`.

- [x] Repository skeleton created under `AI-Native_DP/ai-native-data-platform/`
      (existing user files preserved; the spec document was not modified).
- [x] Host/machine report captured (`scripts/doctor.py`): Windows 10 AMD64, 18 logical
      cores, 31.5 GiB RAM, Docker Desktop 28.3.2 + Compose 2.38.2-desktop.1, repo disk
      225 GiB free. Full report in `docs/compatibility.md`.
- [x] Python 3.11.15 and uv 0.11.16 pinned; `uv.lock` committed (48 packages resolved).
- [x] Frontend lockfile committed (`npm ci` reproducible, 65 packages, 0 vulnerabilities).
- [x] Runtime build images pulled and digests recorded: `postgres:16.15`,
      `mysql:8.4.11`, `python:3.11.15-slim-bookworm`, `node:24.21.0-alpine`,
      `nginx:1.29.8-alpine`.
- [x] Doris 3.x stable patch selected (`apache/doris:fe-3.1.4`, `be-3.1.4`); tags
      verified against the registry. Images not pulled yet (needed in M2) - marked
      pending, not "supported".
- [x] DataHub stable release selected (`v1.7.0.1`, published 2026-09-03); official
      quickstart compose stored at `infra/datahub/compose.pinned.yaml` (unmodified,
      pinned to `v1.7.0.1`). Images not pulled yet (needed in M3).
- [x] Provider key-API contract tests implemented and passing:
      SQLGlot 30.18.0 (dialect parsing, `%(name)s` placeholder rendering, scope
      analysis, star expansion, join attributes), psycopg 3.3.5 (`cancel`,
      `cancel_safe`, OID type registry, error classes).
- [x] `make doctor` equivalent (`scripts/dev.ps1 doctor`) executed successfully.

M0 findings recorded in `docs/compatibility.md` (no invented tags, all verified):

1. TypeScript 7.0.2 is current on npm, but `openapi-typescript@7.13.0` requires
   `typescript@^5.x`; the frontend is pinned to TypeScript 5.9.3 so that frontend
   types can be generated from OpenAPI as required. Upgrading TS is a later decision.
2. `psycopg.connect(..., read_only=True)` is invalid (not a libpq connection
   option); psycopg 3 exposes `Connection.read_only` as a property. Fixed in the
   provider and pinned by a contract test.
3. `SET statement_timeout = %s` fails on PostgreSQL (utility commands cannot take
   parameters); the provider inlines validated integers and never user SQL.
4. Host port `55432` was already occupied by an unrelated local PostgreSQL, so the
   control database is published on `55430` (dev override). Ports `5432`/`3306`
   on this host are used by local PostgreSQL 17 / MySQL 5.7 installations; the
   project runs its own containers and never touches the local servers.
5. Docker Desktop data lives on `C:` (32.8 GiB free at M0 time); the repository and
   results live on `D:`. Full-stack disk budget stays a documented risk.

M0 gate status: **passed for the core stack**. The spec's M0 gate also mentions
"three sources connect" and "Doris cancel / DataHub ingestion route feasible" -
those belong to M2/M3 and are explicitly **not executed** (no M2/M3 work was
started, per "do not pre-build later providers").

---

## M1 - control DB, local login, RBAC, PG gateway, tasks/results/audit, minimal SQL UI

Gate (spec section 31): permissions, write rejection, cancellation, idempotency and
pagination all exercised through the real stack.

- [x] Control schema implemented as Alembic migration `4f36847b8351`; verified
      against the spec DDL: 27 tables, 4 named state CHECK constraints, 17 indexes
      (`backend/scripts/verify_schema.py`).
- [x] Local auth: Argon2id passwords, hashed session/CSRF tokens, HttpOnly cookie,
      CSRF header validation, Origin allowlist, per-account/IP login rate limiting.
- [x] RBAC: `viewer` / `analyst` / `admin` product capabilities plus default-deny
      per-dataset `discover`/`query` grants; grants bump `policy_state.revision`
      in the same transaction; revoking a grant cancels associated live queries.
- [x] Datasource registry: host allowlist + metadata-endpoint rejection, mounted
      secret files only (`SecretResolver`), connection test with sanitized errors,
      optimistic-lock PATCH.
- [x] Query Gateway: SQLGlot AST validation (single statement, SELECT/UNION only,
      node denylist, function allowlist, join policy, subquery depth, relation
      budget), table resolution against registered datasets with CTE shadowing
      handled, star expansion and column validation, outer LIMIT rewrite
      (`max_rows + 1` probe), re-parse of the executed SQL, named parameter binding,
      `query` **and** `discover` permission checks.
- [x] Scheduler/worker: `FOR UPDATE SKIP LOCKED` claim in a short transaction,
      capacity rows locked in the fixed order global -> datasource -> user,
      execution leases with fencing token, heartbeat, cancel poll, deadline,
      terminal-state publication fenced against stale workers.
- [x] Results: Arrow IPC + JSON, atomic temp-write/fsync/rename, N+1 truncation
      detection, byte accounting, signed pagination cursors, 7-day TTL; missing
      files return `RESULT_UNAVAILABLE`, never an empty array.
- [x] Events/SSE: `task_events` as the source of truth, `Last-Event-ID` replay,
      heartbeat comments, `EVENT_CURSOR_EXPIRED`, permission revocation closes the
      stream.
- [x] HTTP API with OpenAPI DTOs; frontend types generated with
      `openapi-typescript` from the exported document.
- [x] Minimal SQL UI (login, SQL workspace with run/cancel/results/pagination/
      truncation badge/validated SQL, catalog, history); React 19 + Vite 8 +
      TanStack Query; session token never stored in `localStorage`.

### M1 test evidence (commands actually executed)

```powershell
# unit + security + contract (no services required)
uv run --frozen pytest tests/unit tests/security tests/contract -q
# -> 130 passed in 7.75s

# integration against the real control + source PostgreSQL
uv run --frozen pytest tests/integration -q
# -> 38 passed in 50.98s
# (requires the compose query/agent workers to be stopped so host-side tests own the queue)

# full stack (Docker core profile) end-to-end
uv run --project backend --frozen python scripts/smoke_core.py --demo --sql "SELECT country, COUNT(*) AS rows, SUM(revenue_usd) AS revenue FROM fixture_metrics GROUP BY country ORDER BY country"
# -> all checks passed; DE 1667 rows / 74719.000000 USD, US 833 rows / 37409.300000 USD

# browser E2E against the running stack
$env:E2E_BASE_URL="http://127.0.0.1:3000"; $env:E2E_ADMIN_PASSWORD="dev-admin-password-123"
cd frontend; npx playwright test
# -> 2 passed (3.2s): full journey + forbidden-write rejection
```

Numbers from the source database are recomputed independently in
`tests/integration/test_query_flow.py::test_full_query_loop_recomputes_numbers`
(second SQL path over the same source) - the acceptance numbers are not hardcoded.

### M1 acceptance matrices

| Spec item | Status | Evidence |
|---|---|---|
| SQL safety corpus (spec 30, A03 subset) | passed | `tests/security/test_sql_attack_corpus.py` (26 cases) + `tests/unit/test_validator.py` (37) |
| Read-only account (A05, PostgreSQL part) | passed | `tests/integration/test_source_permissions.py` (role `42501`, session `25006`) |
| Cancellation (A11, PostgreSQL part) | passed | `tests/integration/test_cancel_timeout.py` |
| Idempotency + pagination + truncation | passed | `tests/integration/test_query_flow.py` |
| Lease loss / fencing / capacity / queue timeout (A12/A13 subset) | passed | `tests/integration/test_recovery.py` (7) |
| SSE replay + revocation (A15 subset) | passed | `tests/integration/test_sse.py` (4) |
| Auth/CSRF/RBAC boundaries (A04 subset) | passed | `tests/integration/test_auth_and_admin.py` (10) |
| Minimal UI journey (A16 subset) | passed | Playwright `tests/e2e/workspace.spec.ts` (2) |

### Explicitly **not executed** in M1 (superseded where M2 completed it)

- ~~MySQL and Doris providers, their cancel/timeout semantics and the three-engine
  acceptance (A01, A11 full, A13 full)~~ - **completed in M2**, see below.
- DataHub ingestion, URN mapping, lineage, metadata-first agent context (A02, A18
  full) - planned M3.
- Deterministic analysis kernels, cross-source joins, demo generator/evaluator
  (A06-A10, A14, A17) - planned M4 (M6 UI).
- LLM adapter, planner, tool runner, recovery of analyses (A09) - planned M5.
- Full product UI, chart drilldown, admin screens (A16 full) - planned M6.
- `make` targets were not run under WSL on this machine (GNU make is not installed
  on the Windows host); the equivalent `scripts/dev.ps1` targets and direct
  commands were executed instead. The Makefile is kept as the Linux/WSL entry point.

---

## M2 - MySQL/Doris providers, three-source seed, multi-source loop

Gate (spec section 31): A01 (three-source connectors), A03 (attack corpus on all
engines), A11 (cancellation on all engines), A13 (idempotency/concurrency).

- [x] MySQL provider (`app/providers/mysql.py`): dedicated connection per query,
      `SET SESSION TRANSACTION READ ONLY`, `MAX_EXECUTION_TIME` server deadline,
      server-side cursor, `KILL QUERY` from a second connection, information_schema
      introspection, engine-specific error classification (3024 timeout / 1317
      interrupted / 1142 permission / 1792 read-only).
- [x] Doris provider (`app/providers/doris.py`): independent implementation (never
      inherits MySQL), `internal` catalog + database namespace per spec section 7,
      external catalogs rejected, session `query_timeout`, `KILL QUERY
      <connection_id>`, `SHOW DATABASES`/`information_schema` introspection,
      message-based classification (Doris reports 1105 for both timeout and cancel).
- [x] Canonical SQL vs driver SQL split: the validator emits canonical SQL; the
      provider renders driver parameter style (`:name` -> `%(name)s` for
      PyMySQL) and escapes literal `%` only when parameters are bound. This fixed a
      real M1 defect where a literal `%` in an unparameterized query was escaped.
- [x] Registry/config/API: all three kinds registerable; per-kind connection config
      validation; datasource `connection_config` accepts the per-engine models;
      catalog refresh selects the engine's namespace unit (schema/database).
- [x] Full profile compose: MySQL 8.4.11 with a read-only account init script,
      Doris FE/BE 3.1.4 with the official `FE_SERVERS`/`BE_ADDR` pattern on a
      dedicated fixed-subnet network, one-shot `doris-init` that creates the demo
      database and the SELECT-only account over the MySQL protocol.
- [x] Three-source seed (`scripts/seed_sources.py`): MySQL `app_release_config`
      (6 rows); Doris `demo.ads_revenue_daily` (648), `iap_revenue_daily` (216),
      `user_daily` (72), `m2_types_probe` (LARGEINT/DECIMAL/DATETIME/STRING/BOOLEAN),
      plus a 200k-row `m2_heavy` MySQL fixture for cancel/timeout tests. Idempotent,
      fixed seed, no company data. (The M4 generator will replace this with the full
      synthetic dataset.)
- [x] Multi-engine attack corpus through the API (14 statements x 3 engines) and
      engine-specific validator corpus (mysql/doris parametrized).

### M2 test evidence (executed)

```powershell
# full three-engine integration matrix (MySQL and Doris containers running)
uv run --frozen pytest tests/integration -q
# -> 58 passed (38 PostgreSQL + 7 MySQL + 10 Doris + 3 API attack corpus)

# static suites after the provider refactor
uv run --frozen pytest tests/unit tests/security tests/contract -q
# -> 171 passed
```

| Spec item | Status | Evidence |
|---|---|---|
| A01 three-source connectors | passed | `test_mysql_source.py` (7), `test_doris_source.py` (10): connection, schema, types, query, timeout, cancel |
| A03 attack corpus on three engines | passed | `test_attack_corpus_api.py` (14 statements x postgres/mysql/doris) + unit corpus |
| A05 read-only accounts | passed (all engines) | PG `42501`/`25006`; MySQL `1792`/`1142`; Doris "denied" (1105) |
| A11 cancellation | passed (all engines) | `test_cancel_timeout.py`, `test_mysql_source.py`, `test_doris_source.py` |
| A13 idempotency/concurrency | passed (M1 + multi-engine) | M1 recovery suite + per-engine single-claim via the shared scheduler |

### Explicitly **not executed** in M2

- DataHub ingestion/lineage (M3); analysis kernels and the M4 demo generator;
  LLM agent (M5); full UI/charts (M6).
- Doris workload groups, multi-BE topologies, external catalogs (out of V1 scope).
- `make test-full` as a single command was not run end to end on this host
  (no GNU make); the same commands were executed directly and via `dev.ps1`-style
  env setup, and the full three-engine matrix passed.

### Next steps

1. M3: DataHub ingestion images, recipes, URN mapping, metadata context with
   `metadata_stale` handling, lineage display, DataHub-backed catalog screens.
2. Keep the docs honest: every new "supported" claim needs a command + output here.

---

## M3 - DataHub ingestion, URN mapping, context, metrics, lineage

Gate (spec section 31): A02 (search/schema/description/at least one real table
lineage edge), A04 (permissions across catalog, context and lineage), A18 part
(DataHub stack reproducible from documented commands).

- [x] Pinned DataHub v1.7.0.1 quickstart stack running (GMS/Frontend/OpenSearch/
      Kafka/MySQL/Actions); every port bound to loopback with collisions remapped
      (GMS 18080, UI 9002, MySQL 13306, OpenSearch 19200, Kafka 19092). Digests in
      `docs/compatibility.md`.
- [x] Ingestion image `ainative-ingestion:0.1.0` = official
      `acryldata/datahub-ingestion:v1.7.0.1` + platform runner; connector
      dependencies never enter the API/query-worker images.
- [x] Ingestion task model: `POST /datasources/{id}/sync` (202; one active task
      per datasource -> 409), `GET /ingestions/{id}`; payload written to the
      shared volume, credentials stay in mounted secrets.
- [x] Recipes per engine with exact-match table allowlists (bare + qualified
      names), `platform_instance`, fabric env, stateful ingestion and the
      required top-level `pipeline_name`; shapes validated against the locked SDK
      via `datahub ingest --dry-run` (contract test).
- [x] Three-source ingestion: source-postgres 2/2, source-mysql 2/2, source-doris
      5/5 mapped after the M3 closure (the secure view included). URNs are only
      stored after a direct DataHub lookup verifies the asset exists and matches
      the expected name.
- [x] Metadata context served from DataHub with semantic custom-property
      round-trip (`ainative.grain/currency/metric_keys/join_keys/business_timezone`),
      `metadata_stale` handling, and a cache key that includes the DataHub URN so
      a completed sync invalidates the pre-sync context immediately.
- [x] Admin-only DataHub deep link: `DatasetContext.datahub_url` is returned to
      administrators only; analysts use the platform detail view (spec 10.1).
- [x] Lineage endpoint with honest empty states (`not_ingested` / `no_upstream` /
      `no_downstream` / `permission_filtered` / `unavailable`), permission
      filtering, platform-side `analysis_evidence` edges, and BFS-accurate edges.
      A real `extracted` edge `demo.ads_revenue_by_country -> ads_revenue_daily`
      is captured from the Doris connector (requires `SHOW_VIEW_PRIV` for the
      ingest account and `include_view_lineage: true`).
- [x] Metric registry: 7 versioned definitions validated at load time and
      bootstrapped into `metric_definitions`; the compiler lands in M4.
- [x] GraphQL contract tests replay **real captured responses**
      (`backend/tests/fixtures/datahub/*.json`, regenerate with
      `scripts/capture_datahub_fixtures.py`); the recorded variables pin the
      query documents the adapter sends.
- [x] Integration tests (A02/A04): full-stack register -> refresh (secure view) ->
      real ingestion -> URN mapping -> permission-filtered search -> DataHub
      context -> admin-only link -> lineage edges -> permission-filtered lineage,
      plus a failed ingestion that must keep the previous URN mapping.
- [x] `scripts/datahub_token.py` (check/verify/store) documents that the pinned
      quickstart runs with GMS authentication disabled; `make datahub-token` is
      check-only.

### M3 test evidence (executed)

```powershell
# static suites (unit + security + contract), 2026-09-14
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# -> 182 passed (was 171 before the M3 tests)

# DataHub contract fixtures replay (no services required beyond the repo)
# -> included in the static run above (25 contract tests)

# full-stack M3 integration (deployed stack + DataHub up)
uv run --project backend --frozen pytest backend/tests/integration/test_datahub_metadata.py -q
# -> 2 passed (real ingestion mapped 5/5 for Doris; view->table lineage captured;
#    failed ingestion kept the previous URN mapping)
```

### Explicitly **not executed** in M3

- ~~Demo-pipeline declared lineage (`declared_by_demo_pipeline`)~~ - delivered in
  M4 (`make demo-lineage`, verified: two `declared_by_demo_pipeline` edges into
  `demo.revenue_daily_total`).
- Column-level lineage and profiling - out of V1 scope / disabled by policy.
- DataHub authentication-enabled mode - the pinned quickstart disables GMS auth;
  documented as unverified in `docs/compatibility.md`.
- DataHub Actions pipelines - the actions container runs with no configured
  pipeline.

---

## M4 - demo generator, deterministic analysis, cross-source joins

Gate (spec section 31): A06 (canonical_67), A07 (driver decomposition), A08
(additive groups), A10 (no-anomaly / incomplete-day, generator+kernel half),
A14 (cross-source join), A17 (medium resource report).

- [x] Deterministic generator `demo/` (`make demo-generate SEED=42 AS_OF=2026-09-13
      SCALE=small|medium SCENARIO=<scenario>`): Decimal arithmetic, CSV + Parquet,
      `manifest.json` (row counts/keys/ranges/hashes), `schema.json`,
      `pipeline_lineage.json` (declared transform + SQL hash) and an
      evaluation-only `ground_truth.json` that never enters metadata.
- [x] All eight scenarios generated and verified (`ecpm_drop`, `traffic_drop`,
      `mixed_offset`, `no_change`, `incomplete_day`, `schema_drift`,
      `config_duplicate`, `canonical_67`); scenario injections pin the untouched
      factor for the target cell so the decomposition attributes the whole change
      to the injected cause.
- [x] Determinism pinned by hash: regenerating the same run reproduces the same
      file hashes; unit tests assert it.
- [x] Loaders (`make demo-load`): Doris BE Stream Load HTTP API, parameterized
      config inserts for PostgreSQL/MySQL, derived `demo.revenue_daily_total`
      produced by the exact SQL recorded in the lineage manifest (never a Python
      reimplementation). Reloads are refused without `--reset-demo`; the reset
      only touches `demo` tables and the demo-owned config tables.
- [x] Declared lineage publisher (`make demo-lineage`) runs the matched DataHub
      SDK inside the ingestion image, resolves URNs from the control DB and
      merges a `ainative.declared_upstreams` property; `GET /datasets/{id}/lineage`
      labels those edges `declared_by_demo_pipeline`.
- [x] Metric compiler (`app/metrics/compiler.py`): closed formula grammar,
      mandatory `[start, end)` period, bound parameters, per-dataset compilation
      for multi-source metrics (`total_revenue`), `daily_average` semantics for
      DAU; injection attempts refused (`METRIC_FORMULA_INVALID`).
- [x] Analysis kernel (`app/analysis/`): `compare_totals`,
      `decompose_contribution`, `decompose_revenue` (symmetric impression x eCPM,
      1e-6 tolerance), `join_results` (whitelisted relations, cardinality
      refusal, `[valid_from, valid_to)` as-of matching, amount preservation).
- [x] Relations registry `metadata/relations/*.yaml`
      (`campaign_cohort_to_config`, `ads_to_app_release`).
- [x] Acceptance executed: canonical_67 through the real Query Gateway (A06),
      drivers on the loaded demo (A07), additivity checks (A08), scenario
      expectations (A10 generator+kernel), cross-source join through two real
      query results plus the duplicate-refusal path (A14), medium resource report
      (A17: 927,456 rows, 53.9 MiB, 17.3 s generation, 4.6 s load, 97 MiB Doris
      storage, 0.50-1.30 s timed aggregates, 22 MiB peak tracked allocations).

### M6 test evidence (executed 2026-09-19)

```powershell
# static suites after the chart work
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# -> 242 passed (4 new ChartSpec tests)

# full three-engine integration matrix (PostgreSQL + MySQL 8.4.11 + Doris 3.1.4; DataHub down)
AIND_DATABASE_URL=... AIND_TEST_SOURCE_URL=... AIND_TEST_MYSQL_URL=... AIND_TEST_DORIS_URL=...   uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q
# -> 67 passed, 1 skipped (the DataHub full-stack test skips when the stack is down)

# browser journeys
cd frontend; E2E_BASE_URL=http://127.0.0.1:3000 E2E_ADMIN_PASSWORD=<admin pw> npx playwright test
# -> 3 passed: SQL workspace journey, forbidden-write rejection, analysis -> chart -> drilldown
```

### M5 test evidence (executed 2026-09-19)

```powershell
# static suites (unit + security + contract)
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# -> 238 passed

# PostgreSQL integration (core profile up, MySQL/Doris/DataHub absent)
uv run --project backend --frozen pytest backend/tests/integration backend/tests/contract -m integration -q
# -> 45 passed, 22 skipped (skips are the absent engines/services)

# A09 harness against the deployed stack (full profile: MySQL + Doris)
uv run --project backend --frozen python scripts/eval_agent.py --cases 10 --password <admin pw>
# -> a09_status=not_executed_no_real_model; target_top3=7/7; unforced=3/3; scored=10/10
#    runtime/eval/a09-20260919-175727.json
```

### M4 test evidence (executed 2026-09-16)

```powershell
# static suites (unit + security + contract)
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# -> 223 passed (41 new M4 unit tests: analysis kernel, metric compiler, generator)

# full integration matrix (PG + MySQL + Doris + DataHub)
uv run --project backend --frozen pytest backend/tests/integration -m integration -q
# -> 63 passed (incl. A06/A07/A14 through real SQL)

# scenario verification (offline, deterministic)
uv run --project backend --frozen python scripts/demo_verify.py --run-dir runtime/demo/<run>
# -> all checks passed for all 8 scenarios
```

### Explicitly **not executed** in M4

- The agent half of A10 (an answer must not force an explanation on no_change /
  incomplete data) - M5.
- Column-level lineage and profiling - out of V1 scope.
- Loading medium-scale data through the dashboard is not needed for A17; the
  resource report above uses the generator + loader commands.

### Next steps (M5)

1. LLM adapter (real + deterministic fake), planner, tool runner over the M4
   kernel and the Query Gateway.
2. Analysis state machine, checkpoint recovery and the evidence/report protocol
   (A09, A12 restart, A10 agent half).
3. Keep this file and `docs/acceptance.md` current: every "supported" claim needs
   a command + observed output.

---

## M5 - governed analysis loop (closed on 2026-09-19; A09 still needs a real model)

- [x] Explicit analysis state machine and immutable budget accounting.
- [x] Deterministic fake adapter and OpenAI-compatible structured adapter; API
      keys are file-mounted administrator configuration.
- [x] Strict plan schema/DAG validation and dimension allowlist; model output
      cannot introduce tools, SQL, URLs or code.
- [x] Persisted sessions, analyses, steps, artifacts, checkpoint versions and
      resumable queue claims with fencing tokens.
- [x] Metric compiler -> Query Gateway -> real source query -> deterministic
      comparison/contribution -> evidence-bound report closed loop.
- [x] Session/analysis/report/evidence/SSE APIs and initial Ask/Analysis UI.
- [x] A10 behavior: no-change emits no forced hypothesis; incomplete evidence
      produces PARTIAL with no numeric conclusion.
- [x] PostgreSQL integration acceptance (`test_analysis_flow.py`) and role
      revocation cancellation; core profile 43 passed on 2026-09-18.
- [x] Adaptive observation loop: the runner is split into explicit
      EXECUTING -> OBSERVING -> EXECUTING|SYNTHESIZING phases, and OBSERVING
      extends the plan with a `driver_decomposition` step (declared per metric in
      `metadata/metrics/*.yaml`, e.g. `ads_revenue -> impressions`) only when the
      change is material, the budget allows it and the plan depth has room.
      Integration test: `test_analysis_adapts_with_driver_decomposition`
      (4 queries, driver artifact, report claim bound to `/ecpm_effect`).
- [x] Bounded SQL repair: `app/agent/repair.py` classifies failures
      (repairable = syntax/known-column, policy = permission/forbidden/limits,
      resource = timeout/cancel/unavailable). One repair = one dropped column,
      applied to every broken query, never more than `agent_max_sql_repairs`, and
      policy refusals are never retried with a rewritten statement. The repaired
      queries are listed with `SKIPPED` steps and a report limitation.
      Integration test: `test_analysis_repairs_a_dropped_dimension_within_budget`
      (schema drifts between submission and execution).
- [x] Materiality band (`MATERIALITY_RELATIVE_BAND = 0.5%`): a change inside the
      band is reported as immaterial with no forced attribution, which is what
      makes the `no_change` / `config_duplicate` scenarios honest (A10).
- [x] A09 harness `scripts/eval_agent.py` (+ `make eval-agent`): ten fixed
      (scenario, seed) cases through the deployed stack, scored against the
      generator's ground truth with model id, prompt version, tokens, latency and
      per-case failure reasons recorded in `runtime/eval/a09-*.json`.
- [ ] A09 result: **not executed (no real model configured)**. The recorded run
      is a deterministic-path baseline: status `not_executed_no_real_model`,
      target cell in the top-3 contributors 7/7 target cases, no forced
      attribution 3/3 no-target cases, evidence-consistent 10/10. Configure
      `AIND_LLM_PROVIDER=openai-compatible` with a model and key file and re-run
      `make eval-agent` to produce the real A09 record.
```
- [x] M6 charts: `app/charts/spec.py` implements the controlled ChartSpec
      (kinds line/bar/table, unknown fields rejected, <=1000 points, ordered line
      x without silent zero fill, units from the metric contract). The runner
      writes a bar chart artifact for every contribution calculation, sets
      `report.chart_ids`, and one id serves both the row and the payload.
- [x] `GET /charts/{id}` (bounded window) and `POST /charts/{id}/drilldown`
      (parent_id + parameter-bound filter, 202) with owner and data-permission
      checks on every read.
- [x] Frontend: `ChartPanel` renders the bar chart plus a table-equivalent view
      with signed labels (colour never carries meaning alone, no formatter and no
      HTML from the payload), and a drilldown button that navigates to the child
      analysis.
- [x] Playwright journey `Analysis and charts`: Ask -> analysis -> chart ->
      table view -> drilldown child analysis (3 journeys passed 2026-09-19).
- [x] A12 live acceptance: `scripts/verify_a12_worker_kill.py` (+ `make verify-a12`)
      pauses the real query worker mid-execution and observes ACTIVE -> SUSPECT ->
      LOST, `QUERY_LOST`, no publication, and fencing holding after the worker
      resumes.
- [x] A15 live acceptance: `scripts/verify_a15_sse_drop.py` (+ `make verify-a15`)
      resets the client connection mid-stream and reconnects with Last-Event-ID:
      no gap, no duplicate, terminal event delivered.
- [x] A18 literal blank-volume run (business stack): `down -v` -> up -> migrate ->
      bootstrap -> schema check -> seed -> smoke -> demo load/verify -> analysis
      with driver decomposition and chart. The run exposed two reproducibility
      gaps, both fixed: `seed_sources.py` now creates the PostgreSQL fixture
      tables and the MySQL `m2_heavy` cancel/timeout fixture.
- [x] Administration screens (spec 24): `/admin/datasources` (health,
      connection test, catalog refresh with secure views, ingestion sync,
      registration by `secret_ref` - no password ever reaches the browser) and
      `/admin/permissions` (access-request queue, real grants create/revoke,
      users and roles, sanitized audit list). Navigation and pages are gated by
      the `admin.manage` capability through `PermissionGate`, while every
      endpoint re-checks it server-side.
- [x] `GET /admin/permission-requests` (status filter, cursor pagination): the
      queue the spec's Permissions screen needs. Marking a request
      `MOCK_APPROVED` is labelled as a mock and provably creates no grant
      (`test_permission_request_queue_is_admin_only_and_stays_mock`).
- [x] Catalog additions: an upstream lineage panel driven by the real endpoint
      (the M1-era "lineage lands in M3" text was stale) and an access-request
      form, so the request -> queue -> mock loop is walkable in the UI.
- [x] DataHub part of the full flow, rerun 2026-09-24 against the pinned
      v1.7.0.1 stack: three-source ingestion mapped 13/13 datasets (PG 4/4,
      MySQL 2/2, Doris 8/8 incl. the confirmed secure view), `verify_metadata.py`
      all checks passed, a real `extracted` view->table edge, and two
      `declared_by_demo_pipeline` edges published via the SDK and read back. Two
      operational findings recorded in compatibility.md (Git Bash path/locale
      traps, `demo-lineage` manifest path fixed, ~10 min search-index lag for
      SDK-published lineage).
- [ ] Remaining: the spec's `/datasets/:id` route is served by the catalog detail
      panel rather than its own page; real-model A09 needs a configured provider.
```
