# AGENTS.md - AI-Native Data Platform

Orientation for agent sessions working in this repository. Read this first, then
`README.md` (usage) and `docs/architecture.md` (what is actually implemented).

## What this is

A local, single-machine data platform: people run SQL (later natural language)
against governed data sources with strict read-only safety, default-deny dataset
grants, cancellable asynchronous execution, audit evidence and replayable results.
PostgreSQL, MySQL and Doris are first-class engines behind one Query Gateway;
DataHub is the authoritative catalog for search, context and lineage.

The design contract is the platform specification (v1.0); it is not part of this
repository and is not modified by work here. Delivered scope: **M0-M4 plus the first
M5 governed-analysis vertical slice**.
M5 real-model evaluation and adaptive analysis are open - see `docs/todo.md`.

## Layout

```
backend/      FastAPI API + query/agent workers (one image), Alembic, tests
  app/api/routes/     HTTP surface; DTOs in app/api/dto.py
  app/query/          gateway, validator, resolver, limits, scheduler, executor
  app/providers/      base protocol + postgres/mysql/doris implementations
  app/agent/          M5 planner, adapters, budget, runner, report
  app/analysis/       deterministic kernel (compare/contribution/drivers/join)
  app/metrics/        versioned metric registry + closed-grammar SQL compiler
  app/metadata/       DataHub GraphQL adapter, ingestion tasks, recipes, context
  app/results/        Arrow IPC + JSON store, signed cursors
  app/workers/        query_worker, agent_worker (reconciler + analyses)
  app/models/orm.py   control schema (27 tables, spec section 8)
  migrations/         Alembic (single baseline revision 4f36847b8351)
  tests/              unit / security / contract / integration
frontend/     React 19 + Vite 8 SPA, OpenAPI-generated types, Playwright journeys
demo/         M4 deterministic generator, scenarios, schemas, loaders
ingestion/    pinned DataHub ingestion image + platform runner / lineage publisher
metadata/     versioned contracts: recipes/, semantic/, metrics/, relations/
infra/        versions.env, pinned DataHub compose, per-engine init scripts
scripts/      doctor, setup_secrets, smoke_core, verify_schema, demo_*, dev.ps1
docs/         architecture, security, compatibility, runbook, acceptance, todo
```

## Commands

Two equivalent entry points; both call the same scripts and preserve exit codes.

- WSL2 / Linux: `make <target>`
- Windows PowerShell: `.\scripts\dev.ps1 <target>` (there is no GNU make on the
  Windows host - do not try to run `make` there)

Python always goes through uv, always frozen:

```bash
uv run --project backend --frozen python <script>
uv run --project backend --frozen pytest backend/tests/<suite> -q
```

Bring-up from blank: `setup-secrets` -> `doctor` -> `up-core` -> `migrate` ->
`bootstrap` (core profile is the default; `up-full` adds MySQL/Doris, the DataHub
stack is a separate compose under `infra/datahub/`).

Test suites (markers: `integration`, `security`, `contract`):

| Suite | Command | Services |
|---|---|---|
| static | `pytest backend/tests/unit backend/tests/security backend/tests/contract -q` | none |
| integration | `make test-integration` | control + source PostgreSQL |
| full matrix | `make test-full` | PG + MySQL + Doris (+ DataHub if up) |
| schema contract | `scripts/verify_schema.py` | control DB |
| browser | `cd frontend && npx playwright test` | running stack + admin password |
| end-to-end | `scripts/smoke_core.py --demo [--kind mysql\|doris]` | running stack |

`make test-integration` stops/starts nothing by itself: stop the compose
`query-worker` and `agent-worker` first so the host-side test worker owns the
queue, otherwise you will fight the running workers over claims.

Baseline as of 2026-09-18: **235 static tests pass**; the PostgreSQL core profile
integration run reported **43 passed / 22 skipped** (skips are the absent MySQL,
Doris and DataHub services). The earlier M4 full-profile record is **63 passed**
and has not been rerun since the M5 changes.

## Invariants - do not break these

1. **The API never executes user SQL against a source database.** It validates,
   introspects through provider methods and enqueues jobs. Only workers open
   execution connections.
2. **The worker re-checks permissions and re-validates the exact SQL** before
   executing; schema/permission drift fails the job (`SCHEMA_CHANGED` /
   `PERMISSION_DENIED`) instead of running stale SQL.
3. **Publication is atomic.** Result files first (temp -> fsync -> rename), then a
   single transaction writing the result row, terminal status, lease release,
   queue completion, audit and event.
4. **Claims use one lock order** (global -> datasource -> user) with row locks and
   fencing tokens; capacity is never an in-process semaphore.
5. **State transitions go through the explicit allow-tables** in
   `app/repositories/`; never write a status column ad hoc.
6. **SQL safety is layered**: AST allowlist (single statement, SELECT/UNION,
   node denylist, function allowlist, join policy), resolution against registered
   datasets, `query` + `discover` permission checks, read-only DB account and
   session, hard time/row/byte limits. Touching `app/query/validator.py` or the
   providers means running the attack corpus.
7. **Data access is default-deny.** `discover`/`query` grants per dataset are
   required even for administrators; grants are checked at search, submit,
   execute, download and stream time, and revocation cancels live queries.
8. **Model output is constrained**: it may only choose allowlisted dimensions and
   `relation_id`s. It never supplies tools, SQL, URLs or code, and never does the
   arithmetic - the deterministic kernel in `app/analysis/` does, in `Decimal`.
9. **`ground_truth.json` is evaluation-only.** It is never mounted into metadata or
   agent context.
10. **Secrets live in mounted files** (`infra/local-secrets/<ref>.json`); the
    database stores only a `secret_ref`. Never commit `.env` or secret files.

## Conventions

- **Docs must stay honest.** Every "supported" claim needs a command and observed
  output recorded in `docs/acceptance.md` / `docs/todo.md`. Unexecuted items stay
  marked `not executed` / `pending` and are never counted as passing. If you
  verify or change behavior, update those two files plus `README.md` and
  `docs/architecture.md` where they describe it.
- **No floating versions.** `latest`/`master`/`nightly`/invented tags are banned.
  Sources of truth: `infra/versions.env`, `backend/uv.lock`,
  `frontend/package-lock.json`, the pinned DataHub compose.
- **Metadata contracts are versioned YAML** under `metadata/`; the metric compiler
  grammar is closed (components, numbers, `+ - * /`, `nullif/coalesce/abs/round`).
  Widening it is a code + test + docs change, not a config edit.
- **Frontend API types are generated**: `make api-spec` (export OpenAPI) then
  `make types`. `frontend/src/api/schema.d.ts` is gitignored and regenerated.
- Python is pinned to 3.11 (`>=3.11,<3.12`); tests use `pytest` with the markers
  above, not ad-hoc scripts.
- Match the existing style: type hints, dataclasses/Pydantic models, explicit
  error codes in `app/errors.py`, repository-layer SQLAlchemy (no raw SQL in
  routes).

## Gotchas

- Host ports are remapped because 5432/3306/8080/9200/9092 are taken on the dev
  machine: control DB **55430**, source PG **55433**, MySQL **33060**, Doris FE
  **19030**, DataHub GMS **18080**, DataHub UI **9002**.
- `runtime/` (demo runs, ingestion payloads, ground truth) and `.env` are
  gitignored by design; results TTL is 7 days and are not repo content.
- The full profile (DataHub + Doris + app) can exceed a 16 GiB WSL2 VM ceiling;
  start stacks in layers, see finding 12 in `docs/compatibility.md`.
- DataHub tests **skip** (not fail) when the stack is absent - a green run does not
  prove DataHub behavior; check the skip count.
- Demo generation is deterministic per
  `(seed, as_of, days, scale, scenario, generator version)`; never "fix" a
  failing scenario by loosening hash or ground-truth checks.
- The login rate limiter and SSE polling are single-process; multiple API replicas
  need a shared store/fan-out before they are correct.
