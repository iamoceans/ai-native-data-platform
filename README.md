# AI-Native Data Platform (M0-M6 delivered)

[简体中文](README.zh-CN.md) · English

A local, single-machine data platform where people ask questions in SQL (and later in
natural language) against governed data sources, with strict read-only safety, explicit
permissions, cancellable asynchronous execution, auditable evidence and replayable
results. The design contract is the project specification (v1.0); this repository
implements **M0 (foundations and version freeze), M1 (the PostgreSQL query closed
loop), M2 (MySQL/Doris providers and the three-source loop), M3 (DataHub catalog,
context, lineage and the metadata-first substrate), M4 (deterministic demo
generator and analysis kernel), M5 (the governed analysis loop, with the real-model
A09 evaluation passed on 2026-09-27) and M6 (charts, drilldown, administration
screens and the release acceptance)**. Known deviations and limits are listed in
section 7.

What "M1" delivered (still the core of the platform):

- a control database (PostgreSQL) holding users, roles, datasources, datasets,
  permissions, query jobs, leases, results, events and audits;
- local authentication (Argon2id + session cookie + CSRF) and three roles
  (`viewer`, `analyst`, `admin`) with **default-deny per-dataset grants**;
- a Query Gateway that accepts only validated single-statement SQL, enforces row/byte
  /time limits and resolves tables to registered datasets;
- an independent query worker that executes on a read-only account with server-side
  timeouts, live cancellation, leases and fencing;
- results stored as Arrow IPC + JSON with signed pagination cursors and a 7-day TTL;
- SSE status streaming with `Last-Event-ID` replay;
- a minimal SQL web UI (login, SQL workspace, catalog, history).

What "M2" added on top of M1:

- a MySQL provider (dedicated connection, read-only session, `MAX_EXECUTION_TIME`
  deadline, `KILL QUERY` cancellation);
- an independent Doris provider (`internal` catalog, session `query_timeout`,
  `KILL QUERY`, external catalogs rejected);
- canonical SQL vs driver SQL separation, so the same validated statement runs on
  all engines with bound named parameters;
- a full-profile Docker stack with MySQL 8.4.11 and Doris FE/BE 3.1.4 plus a
  deterministic three-source seed (`scripts/seed_sources.py`);
- the attack corpus executed on all three engines, plus per-engine cancel/timeout
  acceptance.

What "M3" added:

- the pinned DataHub v1.7.0.1 stack (GMS/frontend/OpenSearch/Kafka/MySQL) with a
  shared network and loopback-only ports;
- an ingestion worker image built from the official ingestion image plus a
  platform runner: recipes per engine, single-active-task semantics (409), URN
  verification before mapping, semantic custom-property round-trip;
- DataHub-backed catalog context (search/permission filtering, `metadata_stale`
  handling, admin-only deep links) and lineage with honest empty states, exact
  BFS edges and `declared_by_demo_pipeline` vs `extracted` labels;
- GraphQL contract tests replaying real captured responses and M3 integration
  tests (full stack + failed-ingestion-keeps-mapping).

What "M4" added:

- a deterministic demo generator (`make demo-generate`) with eight scenarios
  (ecpm_drop, traffic_drop, mixed_offset, no_change, incomplete_day,
  schema_drift, config_duplicate, canonical_67), manifest/lineage/ground-truth
  artifacts, CSV + Parquet output and reproducible file hashes;
- loaders (`make demo-load`) using Doris Stream Load plus a derived table built
  by the exact SQL recorded in the lineage manifest;
- the deterministic analysis kernel (`app/analysis/`): period comparison,
  change-contribution decomposition with additivity checks, symmetric
  impression x eCPM driver split, and a bounded whitelisted cross-source join;
- the metric compiler (`app/metrics/compiler.py`) that turns the versioned
  metric YAML into validated SQL with a closed formula grammar.

What the current "M5" slice adds:

- persisted agent sessions and checkpointed analysis tasks, with explicit state
  transitions, fencing-token recovery and cancellation of child queries;
- `/sessions` and `/analyses` APIs, SSE progress, reports and permission-checked
  evidence retrieval;
- a deterministic planner plus fake and OpenAI-compatible structured adapters;
  model output can only choose allowlisted dimensions and never supplies tools or SQL;
- period comparison and contribution queries compiled from metric definitions,
  executed only through the Query Gateway, then rendered into calculation-bound claims;
- `/ask` and `/analyses/:id` pages for launching and inspecting the workflow.

What the M5 closure adds (2026-09-19):

- the runner as explicit, re-enterable phases
  (`EXECUTING -> OBSERVING -> EXECUTING|SYNTHESIZING`) so `OBSERVING` is what
  decides whether more evidence is worth buying;
- an adaptive observation step: a material change plus a metric that declares an
  impressions metric (`ads_revenue -> impressions`) buys one driver
  decomposition (impressions x eCPM), bounded by the query/tool budget and the
  plan depth;
- bounded SQL repair for capability failures (a drifted column is dropped and the
  queries it broke are resubmitted, at most twice), while policy refusals -
  permission, forbidden function, resource limits - are never retried with a
  rewritten statement;
- a documented 0.5 % materiality band, so generator noise is reported as "no
  material change" instead of a finding;
- `scripts/eval_agent.py` / `make eval-agent`: the A09 harness that runs ten
  fixed (scenario, seed) cases through the deployed loop and scores them against
  the generator's ground truth.

What the M6 work so far adds:

- controlled charts (spec section 25): `GET /charts/{id}` returns a ChartSpec
  with at most 1000 points, `POST /charts/{id}/drilldown` creates a child
  analysis with the drilled value bound as a parameter, and `/analyses/:id`
  renders the bar chart with a table-equivalent view and signed labels;
- the administration screens (spec section 24): `/admin/datasources` for health,
  connection tests, catalog refresh and ingestion sync, and
  `/admin/permissions` for the access-request queue (mock marking kept visibly
  apart from real grants), grants, users/roles and the sanitized audit list.
  Both are gated by the `admin.manage` capability and every admin endpoint
  re-checks it server-side; datasource credentials are only ever referenced by
  `secret_ref`;
- the access-request loop: a user asks for a dataset from the catalog and sees
  their own request status, an administrator approves it into a real grant (or
  rejects it), and `APPROVED` is a distinct schema state from the mock approval
  that grants nothing.

Still open: a real-model A09 run, the live worker-kill and network-drop
acceptances, a literal blank-volume run and the remaining admin screens; see
[docs/todo.md](docs/todo.md).

---

## 1. What problem does it solve?

Analysts want quick answers from operational databases, but giving people (or models)
direct SQL access is unsafe and unauditable. This platform sits in between:

- **Safety**: a SQL AST allowlist (SELECT/UNION only, function allowlist, join policy),
  SELECT-only database roles plus read-only sessions (where the engine supports them),
  hard time/row/byte limits, and execution accounts that can never write.
- **Governance**: every dataset is explicitly registered and granted; permissions are
  checked at search, submit, execute, download and stream time; revocations cancel
  live queries.
- **Auditability**: every submission, execution, cancellation and permission change is
  recorded; results are content-hashed files whose numbers can be recomputed from the
  stored evidence.
- **Multiple engines**: PostgreSQL, MySQL and Doris are first-class provider
  implementations behind one gateway and one audit trail.
- **Future natural-language analysis**: the deterministic core (M4) and the LLM
  planner (M5) will build on this closed loop; the model will never run SQL that
  bypasses the gateway.

## 2. How to start from a blank environment

Prerequisites: Docker Desktop (Windows: WSL2 backend) with ~6-8 GiB of memory
available, Python tooling via [`uv`](https://docs.astral.sh/uv/), Node.js 20+, and
GNU make (WSL2/Linux; on Windows PowerShell use `.\scripts\dev.ps1` with the same
targets).

```bash
# 1. secrets: creates .env with random passwords + infra/local-secrets/source-postgres.json
make setup-secrets

# 2. host check: Docker, CPU/RAM/disk, ports, secret files
make doctor

# 3. start the core stack (control + source PostgreSQL, API, query worker, agent
#    worker, frontend)
make up-core

# 4. prepare the control database and the first administrator
make migrate      # Alembic, idempotent
make bootstrap    # roles, capacities, admin user (prints a generated password once)

# 5. open the UI
#    http://127.0.0.1:3000   (frontend)
#    http://127.0.0.1:8000/api/v1/docs  (OpenAPI)
```

### MySQL/Doris, DataHub and the demo dataset (full profile)

```bash
# 1. DataHub stack (pinned v1.7.0.1; Windows PowerShell needs HOME set:
#    $env:HOME=$env:USERPROFILE)
docker compose --project-name datahub --env-file .env \
  -f infra/datahub/compose.pinned.yaml -f infra/datahub/compose.ainative.yaml \
  --profile quickstart up -d

# 2. business stack with DataHub integration enabled
AIND_DATAHUB_ENABLED=1 make up-full   # core + MySQL 8.4.11 + Doris FE/BE 3.1.4

# 3. generate, load and verify the demo dataset
make demo-generate                    # SEED=42 AS_OF=2026-09-13 SCALE=small SCENARIO=ecpm_drop
make demo-load                        # after `make seed-sources` the demo tables hold the M2
                                      # connector fixture, so the first M4 load needs
                                      # `make reset-demo CONFIRM=demo` to replace it
make demo-verify                      # offline checks against the ground truth

# 4. acceptance harnesses (deployed stack)
make eval-agent                       # A09: ten fixed scenarios, scored vs ground truth
make verify-a12                       # A12: pause the query worker mid-query -> SUSPECT -> LOST
make verify-a15                       # A15: reset an SSE client mid-stream and reconnect

# 4. catalog + context + lineage through DataHub
make metadata-sync                    # queue ingestion for all registered sources
make demo-lineage                     # publish declared demo-pipeline lineage (SDK)
uv run --project backend --frozen python scripts/verify_metadata.py

# 5. the whole integration matrix against all three engines (+DataHub, when up)
make test-full
```

The full profile adds roughly 12 GiB of images and 4-6 GiB of runtime memory; the
core profile remains the default for everyday work. On a 16 GiB host start the
two stacks in layers (see the WSL2 note in `docs/compatibility.md`).

The M1/M2 `make seed-sources` fixture seed still exists for the connector tests
(`app_release_config`, `m2_heavy`, `m2_types_probe`); the M4 generator owns the
`demo` database from now on.

Then, as an administrator, register a data source and expose tables to roles:

1. put the source credentials in `infra/local-secrets/<name>.json`
   (`{"username": ..., "password": ...}`);
2. `POST /api/v1/datasources` with `{name, kind: "postgres", connection_config, secret_ref}`;
3. `POST /api/v1/datasources/{id}/test` -> expect `HEALTHY`;
4. `POST /api/v1/admin/datasources/{id}/catalog-refresh` with `{"schemas": ["public"]}`
   (base tables with sensitive-looking columns are skipped, views require explicit
   confirmation);
5. `POST /api/v1/admin/grants` for the roles that should see the data.

`scripts/smoke_core.py --demo` automates all of the above and runs one real query; see
section 4.

## 3. How to configure models

The default `fake` mode is fully offline: planning follows the deterministic
metric template and all numbers come from real gateway query results plus Decimal
calculations. It does not pretend that a model was called.

The M5 configuration surface is administrator-only and environment-based:

- `AIND_LLM_PROVIDER` (e.g. `openai-compatible`), `AIND_LLM_BASE_URL`,
  `AIND_LLM_MODEL`, `AIND_LLM_API_KEY_FILE` (mounted secret, never a request field);
- `AIND_LLM_RESPONSE_FORMAT`: `json_object` (the default) carries the JSON schema in
  the prompt and validates the reply against it locally - the portable mode, because
  DeepSeek and most OpenAI-compatible servers answer HTTP 400 for `json_schema`
  (`This response_format type is unavailable now`). Use `json_schema` only for a
  provider that implements strict structured outputs;
- `AIND_LLM_PROVIDER=fake` for the offline path, or `openai-compatible` to let a
  configured model select up to three dimensions from the metric allowlist;
- budgets: 60,000 input / 12,000 output tokens per analysis, 20 tool calls,
  12 queries, 2 SQL repairs, 180 s wall clock; exhaustion produces `PARTIAL`, not
  fabricated answers.

The API key is read from the configured mounted file. User questions cannot
override the endpoint, model, key path, tool list, metric formulas or SQL policy.

### Using a real model (DeepSeek is pre-configured)

`.env` already points at DeepSeek's OpenAI-compatible endpoint; the only missing
piece is the key. This path was executed on 2026-09-27 and produced the A09 record
in section 5:

```bash
# 1. paste the key (one line, replacing PASTE_DEEPSEEK_API_KEY_HERE)
#    infra/local-secrets/llm_api_key        <- mounted read-only at /run/secrets
# 2. prove the endpoint works (one structured call, prints model id/latency/tokens)
make llm-check
# 3. run the A09 evaluation over the ten fixed scenarios
make eval-agent
```

Until a key is present the platform runs the deterministic template path and says
so: the analysis state carries `llm_warning`, the model id stays
`deterministic-template-v1`, and the A09 record reports
`not_executed_no_real_model`. Nothing pretends a model was called. To switch
models, change `AIND_LLM_MODEL` (for example `deepseek-reasoner`); endpoint,
model and key path are administrator configuration, never request fields.

## 4. How to generate demo data

The M4 generator creates a deterministic synthetic mobile-app dataset (no company
data, fixed seed, reproducible file hashes):

```bash
make demo-generate SEED=42 AS_OF=2026-09-13 SCALE=small SCENARIO=ecpm_drop
```

| Artifact | Content |
|---|---|
| `runtime/demo/<run>/<table>.csv` + `.parquet` | `ads_revenue_daily` (small: 155,520 rows; medium: 864,000), `iap_revenue_daily`, `user_daily`, `retention_daily`, `campaign_cohort_daily`, plus `campaign_config` (PostgreSQL) and `app_release_config` (MySQL) |
| `manifest.json` | row counts, primary keys, date ranges, complete-through date, currency, file hashes, generator version |
| `pipeline_lineage.json` | the declared demo ETL (`ads`/`iap` -> `revenue_daily_total`) with the exact transform SQL and its hash |
| `ground_truth.json` | evaluation-only expected values - never mounted into metadata or read by an agent |

Scenarios: `ecpm_drop`, `traffic_drop`, `mixed_offset`, `no_change`,
`incomplete_day`, `schema_drift`, `config_duplicate` and the exact `canonical_67`
sample from the specification (10,000 -> 9,000; target -670; share 0.67).

```bash
make demo-load                        # Stream Load into Doris + config tables + derived table
make demo-load --reset-demo           # explicit rebuild (drops/recreates demo tables only)
make demo-verify                      # offline: hashes, totals, contributions, drivers, scenario rules
make demo-lineage                     # publish declared lineage via the DataHub SDK
```

Reloading a non-empty run without `--reset-demo` is refused. The loader verifies
row counts against the manifest and refuses duplicate stream-load labels.

Smoke against any engine (from the repository root, stack running):

```bash
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<admin password> \
  uv run --project backend --frozen python scripts/smoke_core.py --demo            # PostgreSQL
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<admin password> \
  uv run --project backend --frozen python scripts/smoke_core.py --demo --kind mysql
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<admin password> \
  uv run --project backend --frozen python scripts/smoke_core.py --demo --kind doris
```

The output shows real values recomputed by the engines (verified against independent
SQL paths in the integration tests).

## 5. How to run acceptance

```bash
# static + security + contract suites (no services needed)
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q

# integration suite (stop the compose workers first so the host-side
# test worker owns the queue; DataHub/M4 tests skip when their stack is absent)
docker compose stop query-worker agent-worker
make test-integration
docker compose start query-worker agent-worker

# full three-engine matrix (requires the full profile up, and the demo loaded
# for the M4 SQL acceptance tests)
make test-full

# schema contract check
uv run --project backend --frozen python scripts/verify_schema.py

# demo pipeline acceptance
make demo-generate && make demo-load --reset-demo && make demo-verify
uv run --project backend --frozen python scripts/verify_metadata.py

# end-to-end smoke against the running stack (add --kind mysql|doris for others)
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<admin password> \
  uv run --project backend --frozen python scripts/smoke_core.py --demo

# browser journey (Chromium)
cd frontend
E2E_BASE_URL=http://127.0.0.1:3000 E2E_ADMIN_PASSWORD=<admin password> npx playwright test
```

Recorded results for the current revision (2026-09-19): **238 static tests passed**
and the PostgreSQL integration profile reported **45 passed / 22 skipped**. The
skips are the intentionally absent MySQL, Doris and DataHub services in the core
profile. The earlier M4 full-profile record remains **63 passed** and has not been
rerun after the M5 changes.

The M5 analysis loop additionally has an evaluation harness:

```bash
make eval-agent                      # ten fixed (scenario, seed) cases, full profile
```

Its 2026-09-27 record (`runtime/eval/a09-20260927-224114.json`) is the real-model
run against DeepSeek: `a09_status=passed`, target cell in the top three
contributors 7/7 target cases, no forced attribution 3/3 no-target cases,
evidence-consistent 10/10, 2,169 input / 140 output tokens over 34 gateway
queries, 2.19-5.98 s per case. The 2026-09-19 record is the deterministic baseline
(`not_executed_no_real_model`, no provider was configured then) with the same
7/7 - 3/3 - 10/10.

The M4 full-profile run also recorded
**three engine smokes passed**, `verify_metadata.py` all checks passed
(Doris 8/8, PostgreSQL 4/4, MySQL 2/2 mapped), `demo-verify` passed for all 8
generator scenarios, and **2 Playwright journeys passed**. The full matrix -
including items that were *not* executed (LLM agent, full UI) - is in
[docs/acceptance.md](docs/acceptance.md); the milestone tracker is
[docs/todo.md](docs/todo.md).

## 6. How to stop and recover

```bash
make down        # stop containers, keep volumes (control data, source data, results)
make up-core     # start again; state is where you left it
```

- **Worker killed mid-query**: the lease expires, the reconciler marks the query
  `SUSPECT` then `LOST` after the source-side deadline; fencing tokens make sure a
  stale worker can never publish. No duplicate results.
- **API restarted**: sessions live in PostgreSQL; SSE reconnects with `Last-Event-ID`
  (expired cursors return `EVENT_CURSOR_EXPIRED`, the client re-fetches the resource).
- **Results** expire after 7 days; files and rows are cleaned by the reconciler. A
  missing file returns `RESULT_UNAVAILABLE`, never an empty success.
- **Full reset** (destructive, control state only):
  `docker compose run --rm --no-deps backend alembic downgrade base && make migrate && make bootstrap`.
- Detailed recovery paths and troubleshooting: [docs/runbook.md](docs/runbook.md).

## 7. Known limitations

- The current analysis runner covers metric period comparison and allowlisted
  contribution dimensions, and extends its plan with a rule-driven
  `driver_decomposition` step when the metric declares one and the materiality,
  budget and depth gates allow (integration-tested). Model-driven step selection is
  not implemented: the model's only decision is which allowlisted dimensions to
  break down, and there is no iterative tool-selection loop.
- A09 now has real-model evidence (DeepSeek, 2026-09-27, `docs/acceptance.md`).
  Model replies are validated against the closed plan schema locally, and a reply
  that does not validate fails that analysis loudly - there is no format-repair
  loop, only the SQL repair budget.
- The UI covers the V1 routes: login, the SQL workspace, results and history,
  ask/analysis with charts, table view and drilldown, and the two administration
  screens. Two things worth stating plainly: the specification's `/datasets/:id`
  deep link is served by the catalog detail panel instead of its own route, and the
  shared/dashboard screens are not part of the V1 specification, so they were not
  invented.
- DataHub auth-enabled mode is untested: the pinned quickstart disables GMS
  authentication (`scripts/datahub_token.py --check` reports this).
- DataHub profiling is disabled in the recipes; column-level lineage is out of scope.
- The demo generator's scenario claims are synthetic; `ground_truth.json` is
  evaluation-only and is never mounted into metadata or agent context.
- Sensitive-data protection is column-name based at registration; there is no general
  column/row rewriting engine (by design).
- Doris supports the local single-BE profile only; multi-BE topologies, workload
  groups and external catalogs are out of V1 scope.
- The full stack (DataHub + Doris + app) can exceed the WSL2 VM memory ceiling on a
  16 GiB host; start the stacks in layers and see
  [docs/compatibility.md](docs/compatibility.md) finding 12.
- The login rate limiter and SSE polling are single-process concerns; a shared store /
  fan-out is required before running multiple API replicas.
- Local HTTP deployment only; loopback binding is the compensating control (set
  `AIND_COOKIE_SECURE=true` behind TLS).

## 8. Future production roadmap (spec sections 31-32)

| Phase | Adds | Preconditions recorded now |
|---|---|---|
| M2 | MySQL and Doris providers, three-source seed, engine-specific cancel/timeout | **done**: MySQL `MAX_EXECUTION_TIME`/`KILL QUERY`, Doris `query_timeout`/`KILL QUERY`, corpus on all engines |
| M3 | DataHub ingestion, URN mapping, metadata context, lineage | **done**: pinned `v1.7.0.1` stack, three-source ingestion, real view->table edge, admin-only deep links |
| M4 | Synthetic demo generator, deterministic compare/contribution/driver kernels, bounded cross-source joins | **done**: A06-A08/A14 acceptance through real SQL, A17 resource report recorded |
| M5 | LLM adapter, planner, tool runner, evidence protocol, budget enforcement | **done**: governed comparison loop, evidence-bound reports, budget enforcement and the deterministic fake path; the real-model A09 evaluation passed on 2026-09-27 (7/7 - 3/3 - 10/10 against DeepSeek) |
| M6 | Full UI (charts, drilldown, SSE everywhere, admin screens), release acceptance | **done for the verified routes**: charts, drilldown and the analysis journey (A16), administration screens, the A12/A15 live acceptances and the A18 blank-volume run; the spec's `/datasets/:id` route is served by the catalog detail panel |
| Production | Kyuubi/Spark batch provider, Flink streaming jobs, MCP egress, enterprise semantics | identity mapping, HA, object storage, SLOs |

## 9. Repository map and documents

```
README.md                  this file
Makefile / scripts/dev.ps1 entry points (Linux/WSL / Windows)
compose.yaml               core + full profiles (DataHub network shared with M3)
backend/                   FastAPI app, workers, migrations, tests (see docs/architecture.md)
demo/                      deterministic demo generator, scenarios, loaders
ingestion/                 ingestion runner + declared-lineage publisher (DataHub SDK)
metadata/                  versioned semantic/metric/relation contracts + recipes
frontend/                  React SPA, generated OpenAPI types, Playwright journeys
scripts/                   doctor, setup_secrets, smoke_core, verify_schema,
                           metadata_sync, verify_metadata, demo_generate/load/verify,
                           capture_datahub_fixtures, datahub_token
infra/                     versions.env, secrets example, DataHub pinned compose
docs/architecture.md       module boundaries, query lifecycle, M3/M4 flows
docs/security.md           security model and explicit non-goals
docs/compatibility.md      pinned versions, digests, verified vs pending, findings
docs/runbook.md            start/stop/recover/troubleshoot
docs/acceptance.md         acceptance matrix with executed evidence (M0-M6)
docs/todo.md               milestone tracker (M0-M6, with per-milestone open items)
```

Every "supported" claim in these documents must have been executed on this machine;
unverified combinations stay marked `pending`/`not executed`.

## 10. License

Apache License 2.0 - see [LICENSE](LICENSE). Copyright 2026 oceans.

Third-party components (PostgreSQL, MySQL, Doris, DataHub and the Python and Node
dependencies pinned in `backend/uv.lock` and `frontend/package-lock.json`) keep their
own licenses.
