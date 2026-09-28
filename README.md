# AI-Native Data Platform

[简体中文](README.zh-CN.md) · English

A data platform for governed querying and analysis. Analysts want fast answers from
operational databases, but handing people - or models - direct SQL access is unsafe
and unauditable; this platform sits in between: ask questions in SQL (and in
natural language) against governed sources, with strict read-only safety,
default-deny per-dataset grants, cancellable asynchronous execution, an audit trail
for every step, and results whose numbers can be recomputed from stored evidence.
It runs on a single machine: PostgreSQL, MySQL and Doris sit behind one query
gateway, and DataHub supplies the catalog, context and lineage. A Spark Thrift
Server adapter for a shared Hive Metastore is available as an external-service
integration: its connector, cancellation and timeout paths were accepted against
a real Spark/Hive stack on 2026-09-28/29, with Spark job lineage, read-only
source authorization and browser E2E still open.

## Status

- **Delivered**: M0-M6. The real-model evaluation (A09) passed on 2026-09-27 -
  ten fixed cases against DeepSeek with the target cell in the top three
  contributors **7/7**, no forced attribution **3/3**, evidence-consistent **10/10**.
- **Open**: the specification's `/datasets/:id` deep link is served by the catalog
  detail panel instead of its own route, and analysis steps are extended by rule
  rather than by the model choosing steps and tools.
- Per-milestone checklists and executed evidence live in
  [docs/todo.md](docs/todo.md) and [docs/acceptance.md](docs/acceptance.md);
  anything not executed is marked `not executed` and never counted as passing.

## What it does

- **Querying**: single-statement SELECT/UNION only, checked against an AST
  allowlist, a function allowlist and a join policy before it reaches the gateway;
  one provider per engine, with read-only accounts, read-only sessions,
  server-side timeouts and live cancellation.
- **Governance**: datasets must be registered and granted explicitly
  (`discover`/`query`, default-deny per dataset, administrators included);
  permissions are re-checked at search, submit, execute, download and stream time,
  and revoking a grant cancels live queries.
- **Evidence and replay**: every submission, execution, cancellation and permission
  change is audited; results are stored as Arrow IPC + JSON with content hashes and
  signed cursors, so the numbers can be recomputed from the stored evidence.
  Results expire after 7 days.
- **Deterministic analysis**: period comparison, contribution decomposition with
  additivity checks, a symmetric impression x eCPM driver split and a bounded
  whitelisted cross-source join. Metrics are versioned YAML compiled into SQL by a
  closed-grammar compiler - the arithmetic is always done by the deterministic
  kernel in `Decimal`, never by a model.
- **Natural-language questions**: the agent loop (plan -> execute through the
  gateway -> observe -> synthesize) delegates exactly one decision to the model,
  namely which allowlisted dimensions to break down. A model never supplies tools,
  SQL, URLs or code. With no model configured the platform degrades to the
  deterministic template path and records a warning - it never pretends a model
  was called.
- **Catalog and lineage**: DataHub (off by default) provides search, context and
  lineage; real `extracted` edges and declared demo-pipeline edges are labelled
  apart.
- **Business memory**: every finished analysis can teach the platform something -
  the model distils at most three statements per run, and they may not contain a
  figure the analysis did not produce, so prose never becomes a second source of
  numbers. Later questions read those statements back as advisory context for
  choosing dimensions, and an administrator confirms or rejects each one (`业务
  记忆` on the Ask screen). Ask more, and it knows more.
- **UI**: a React SPA with the SQL workspace, catalog, history, Ask/Analysis
  (charts, table-equivalent view, drilldown) and the datasource and permission
  administration screens.

## Quick start (core profile)

Requirements: Docker Desktop (WSL2 backend on Windows) with roughly 6-8 GiB of
memory available, Python tooling through [uv](https://docs.astral.sh/uv/), and
Node.js 20+. Use `make` on WSL2/Linux, or `.\scripts\dev.ps1` on Windows PowerShell
(same targets).

```bash
make setup-secrets   # .env with random passwords, plus source credentials under infra/local-secrets/
make doctor          # host check: Docker, CPU/memory/disk, ports, secret files
make up-core         # control DB + source DB + API + query worker + agent worker + frontend
make migrate         # Alembic migrations (idempotent)
make bootstrap       # roles, capacities and the first administrator (password printed once)
```

Then open <http://127.0.0.1:3000>; the OpenAPI documentation is at
<http://127.0.0.1:8000/api/v1/docs>. Registering a datasource by hand and granting
access to a role is written out in [docs/runbook.md](docs/runbook.md) (section 2.2).

## Full profile: MySQL / Doris / DataHub / the demo dataset

```bash
# 1. the pinned DataHub stack (v1.7.0.1; Windows PowerShell needs $env:HOME=$env:USERPROFILE)
docker compose --project-name datahub --env-file .env \
  -f infra/datahub/compose.pinned.yaml -f infra/datahub/compose.ainative.yaml \
  --profile quickstart up -d

# 2. the business stack (core + MySQL 8.4.11 + Doris FE/BE 3.1.4) with DataHub enabled
AIND_DATAHUB_ENABLED=1 make up-full

# 3. seed data and the deterministic demo dataset
make seed-sources      # three-source connector fixtures
make demo-generate     # SEED / AS_OF / SCALE / SCENARIO
make demo-load         # Doris Stream Load, plus the derived table from the recorded transform SQL
make demo-verify       # offline: file hashes, totals, contributions, drivers, scenario rules
```

The generator writes CSV + Parquet tables, a manifest with row counts and file
hashes, the declared pipeline lineage with the exact transform SQL, and an
evaluation-only `ground_truth.json`. Its eight scenarios are `ecpm_drop`,
`traffic_drop`, `mixed_offset`, `no_change`, `incomplete_day`, `schema_drift`,
`config_duplicate` and the specification's exact `canonical_67` sample
(10,000 -> 9,000; target -670; share 0.67). Details are in
[docs/architecture.md](docs/architecture.md) and
[docs/acceptance.md](docs/acceptance.md).

## Using a real model (optional)

The default `AIND_LLM_PROVIDER=fake` runs fully offline on the deterministic
template. To use a real model:

```bash
# 1. paste the key into infra/local-secrets/llm_api_key (one line, replacing the placeholder)
# 2. verify the endpoint with one structured call
make llm-check
# 3. run the ten fixed cases of the A09 evaluation
make eval-agent
```

`.env` already points at DeepSeek (`https://api.deepseek.com/v1`, `deepseek-chat`).
`AIND_LLM_RESPONSE_FORMAT` defaults to `json_object`: the portable mode, in which
the JSON schema travels in the prompt and the reply is validated against the same
schema locally. Use `json_schema` only for a provider that implements strict
structured outputs - DeepSeek answers HTTP 400 for it. Per analysis the budget is
60,000 input / 12,000 output tokens, 20 tool calls, 12 queries, 2 SQL repairs and
180 s of wall clock; exhausting it produces `PARTIAL`, not a fabricated answer. The
key is read from the mounted file only - never from `.env`, a request field or the
browser - and endpoint, model and key path are administrator configuration that a
user question cannot override.

## Verification

```bash
# static suites (no services needed): unit + security + contract
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q

# integration suite (stop the compose workers first, so the host-side test worker owns the queue)
docker compose stop query-worker agent-worker && make test-integration && docker compose start query-worker agent-worker

# full three-engine matrix (needs the full profile up)
make test-full

# end-to-end smoke against the running stack: one real query
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<admin password> \
  uv run --project backend --frozen python scripts/smoke_core.py --demo

# browser journeys
cd frontend && E2E_BASE_URL=http://127.0.0.1:3000 E2E_ADMIN_PASSWORD=<admin password> npx playwright test
```

The rule this repository follows: **every "supported" claim comes with a command
and its observed output**, recorded in [docs/acceptance.md](docs/acceptance.md);
items that were not executed are marked `not executed` and never counted as
passing.

## Ports and resources

5432 / 3306 / 8080 / 9200 / 9092 are taken on the development host, so the ports
are remapped: control DB **55430**, source DB **55433**, MySQL **33060**, Doris FE
**19030**, DataHub GMS **18080**, DataHub UI **9002**. The stack keeps its state in
named volumes; `make down` stops the containers without deleting them.

The core profile is the everyday default. The full profile adds roughly 12 GiB of
images and 4-6 GiB of runtime memory; on a 16 GiB host start the stacks in layers
(see the WSL2 note in [docs/compatibility.md](docs/compatibility.md)).

## Repository layout

```
backend/      FastAPI API + query/agent workers (one image), Alembic, tests
frontend/     React SPA, generated OpenAPI types, Playwright journeys
demo/         deterministic generator, scenarios, loaders
metadata/     versioned contracts: recipes/, semantic/, metrics/, relations/
ingestion/    DataHub ingestion runner and declared-lineage publisher
infra/        versions.env, pinned DataHub compose, per-engine init scripts
scripts/      doctor, setup_secrets, smoke_core, verify_schema, demo_*, dev.ps1
docs/         architecture, security, compatibility, runbook, acceptance, todo
compose.yaml  core and full profiles; compose.dev.yaml publishes the DB ports
```

## Known limitations

- Spark/Hive requires an existing shared Hive Metastore and Spark Thrift Server.
  The adapter accepts only NOSASL connections and non-parameterized SQL. Spark
  does not expose a transactional read-only session here; deploy a read-only
  source identity and enforce a server-side query timeout. The connector,
  cancellation and timeout paths were accepted live on 2026-09-28/29; Spark job
  lineage, read-only source authorization and browser E2E coverage still need
  live acceptance. See the
  [Spark/Hive runbook](docs/runbook.md#spark--hive-shared-metastore-preview).
- The analysis runner extends its plan by rule; letting the model choose steps or
  iterate over tools is not implemented.
- Business memory is ranked text, not embeddings, and is platform-wide rather
  than per-team; extraction runs inline in the agent worker, so several workers
  would need a claim before the loop scales past one.
- Plan replies are validated against a closed schema locally, and a reply that does
  not validate fails that analysis loudly - there is no format-repair loop, only
  the SQL repair budget.
- The specification's `/datasets/:id` deep link is served by the catalog detail
  panel; the shared/dashboard screens are not part of the V1 specification and were
  not invented.
- Doris runs the local single-BE profile only; multi-BE topologies, workload groups
  and external catalogs are out of V1 scope.
- DataHub auth-enabled mode is untested (the pinned quickstart disables GMS
  authentication) and DataHub profiling is disabled in the recipes; column-level
  lineage is out of scope.
- Local HTTP deployment only, with loopback binding as the compensating control
  (set `AIND_COOKIE_SECURE=true` behind TLS); the login rate limiter and SSE
  polling are single-process, so multiple API replicas need a shared store and
  fan-out first.
- Sensitive-data protection is column-name based at registration; there is no
  general column/row rewriting engine, by design.
- The full stack (DataHub + Doris + app) can exceed the memory ceiling of a 16 GiB
  WSL2 VM, so start the stacks in layers.

The complete list of what was not executed or not verified is in
[docs/acceptance.md](docs/acceptance.md) ("Skipped or unverified"), and the
security model's explicit non-goals are in [docs/security.md](docs/security.md).

## Documentation

| Document | Content |
|---|---|
| [docs/architecture.md](docs/architecture.md) | module boundaries, query lifecycle, M3/M4 flows, what is not built |
| [docs/security.md](docs/security.md) | security model and explicit non-goals |
| [docs/compatibility.md](docs/compatibility.md) | pinned versions and digests, verified vs pending, host findings |
| [docs/runbook.md](docs/runbook.md) | start/stop/recover/troubleshoot |
| [docs/acceptance.md](docs/acceptance.md) | acceptance matrix (A01-A18) with commands and observed output |
| [docs/todo.md](docs/todo.md) | milestone tracker: what is done and what is still open |

## License

Apache License 2.0 - see [LICENSE](LICENSE). Third-party components (PostgreSQL,
MySQL, Doris, DataHub, and the dependencies pinned in `backend/uv.lock` and
`frontend/package-lock.json`) keep their own licenses.
