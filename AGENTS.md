# AGENTS.md - Oceanus

DeepSeek work resumed on 2026-10-17. `llm-check --list-models` discovers endpoint
IDs; `llm-check --expect-model ID` requires exact response identity, never a
configured fallback. The subsequent published-environment retry succeeded:
authenticated `/models` lists `deepseek-flash` and `deepseek-v4-pro`; a structured
completion with `--expect-model deepseek-flash` passed with that exact returned
ID. Local ignored .env now selects deepseek-flash. The endpoint does not establish
the user's requested "v4.1" version label. Full A09/large-pool Skill acceptance
remains pending; this restored environment has only the two PostgreSQL services,
and onboarding env.sh still overrides the running stack to the fake provider.
Do not confuse the successful host diagnostic with deployed-stack acceptance.

Orientation for agent sessions working in this repository. Read this first, then
`README.md` (usage) and `docs/architecture.md` (what is actually implemented).

## What this is

Oceanus is a local, single-machine data platform: people run SQL (later natural
language) against governed data sources with strict read-only safety, default-deny
dataset grants, cancellable asynchronous execution, audit evidence and replayable
results.
PostgreSQL, MySQL and Doris are first-class engines behind one Query Gateway;
DataHub is the authoritative catalog for search, context and lineage.

The design contract is the platform specification (v1.0); it is not part of this
repository and is not modified by work here. Delivered scope: **M0-M6**, with the
per-milestone notes and open items in `docs/todo.md` and the known limits in
`README.md` (section "Known limitations").
M5's real-model evaluation passed on 2026-09-27 (`docs/acceptance.md`). The runner's
driver decomposition is integration-tested. Model-backed planning now chooses
closed compare/breakdown actions and, after observations, finish or the declared
driver action. General arbitrary-tool iteration remains outside scope. The
`/datasets/:id` deep link is implemented (2026-10-16).

## Layout

```
backend/      FastAPI API + query/agent workers (one image), Alembic, tests
  app/api/routes/     HTTP surface; DTOs in app/api/dto.py
  app/query/          gateway, validator, resolver, limits, scheduler, executor
  app/providers/      base protocol + postgres/mysql/doris/spark implementations
  app/agent/          M5 planner, adapters, budget, runner, report, business memory,
                      conversation/ (session scope, turn orchestration)
  app/skills/         SKILL.md/ZIP parsing, strict front matter, versions
  app/analysis/       deterministic kernel (compare/contribution/drivers/join)
  app/metrics/        versioned metric registry + closed-grammar SQL compiler
  app/metadata/       DataHub GraphQL adapter, ingestion tasks, recipes, context
  app/results/        Arrow IPC + JSON store, signed cursors
  app/workers/        query_worker, agent_worker (reconciler + analyses + learning)
  app/models/orm.py   control schema (33 tables: the spec 8 list + business_memory
                      + conversation_turns + skills/skill_versions + teams + auth_rate_limits)
  migrations/         Alembic (baseline 4f36847b8351 + permission state + business
                      memory + conversation turns + skills)
  tests/              unit / security / contract / integration
frontend/     React 19 + Vite 8 SPA, OpenAPI-generated types, Playwright journeys
demo/         M4 deterministic generator, scenarios, schemas, loaders
ingestion/    pinned DataHub ingestion image + platform runner / lineage publisher
metadata/     versioned contracts: recipes/, semantic/, metrics/, relations/
infra/        versions.env, pinned DataHub compose, per-engine init scripts,
              spark-preview/ (throwaway Spark Thrift + HMS fixture for acceptance)
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
| browser (mocked) | `cd frontend && npm run test:regression` | none - mocks the API, own Vite server on :3018 |
| end-to-end | `scripts/smoke_core.py --demo [--kind mysql\|doris]` | running stack |

`make test-integration` stops/starts nothing by itself: stop the compose
`query-worker` and `agent-worker` first so the host-side test worker owns the
queue, otherwise you will fight the running workers over claims. Run DB pytest
batches serially per AIND_DATABASE_URL: clean_db truncates tables and two batches
cannot safely share that database, even when both use disposable fixtures.

Baseline as of 2026-09-18: **235 static tests pass**; the PostgreSQL core profile
integration run reported **43 passed / 22 skipped** (skips are the absent MySQL,
Doris and DataHub services). The earlier M4 full-profile record is **63 passed**
and has not been rerun since the M5 changes. Re-observed 2026-09-27 after the
`json_object` adapter change: **248 static tests pass**
(`pytest backend/tests/unit backend/tests/security backend/tests/contract -q`) and
the A09 harness passed against a real model (DeepSeek). Re-observed 2026-09-29
after the business-memory, Spark-preview and Spark-cancellation-fix changes:
**281 static tests pass** and the **full matrix is 73 passed**
(`pytest backend/tests/integration backend/tests/contract -m integration -q`,
MySQL + Doris + DataHub live, workers stopped). The full matrix needs four things
this host does not default to: `-f compose.dev.yaml` for the DB ports, Doris
running (`test_datahub_metadata` asserts its health), the `ecpm_drop` demo
scenario loaded (`test_a07_driver_decomposition_on_loaded_demo`), and the
deployed containers started with `AIND_DATAHUB_ENABLED=1` for the
deployed-stack DataHub test; on this host also pass
`--basetemp=runtime/pytest-tmp` or pytest cannot create its tmpdir. Re-observed
2026-10-13 on the committed tree (static **282 passed** after the curation-audit
fix) with the A09 harness re-run end to end: `a09_status=passed`, target-in-top3
**7/7**, no forced attribution **3/3**, evidence-consistent **10/10**, every case
field-identical to the 2026-09-27 run - and, for the first time, with the
business-memory loop active alongside it (the ten cases exercised cold -> warm ->
capped recall without changing a single score; see `docs/acceptance.md`). The
Spark preview's live acceptance - including the cancellation fix and the server-side
timeout - is recorded in `docs/acceptance.md`; `infra/spark-preview/` is the
throwaway Spark/HMS fixture that makes it reproducible. Re-observed 2026-10-13
after the conversational Ask + skills work (P1-P5 of the 2026-10-13 design,
committed to the working tree only): static **364 passed**; the full matrix
**98 passed, 1 skipped, 25 deselected** (DataHub stack not started - that subset
is not re-verified); the A09 harness again `a09_status=passed` 7/7 / 3/3 / 10/10
with the conversational layer deployed; and a real-model multi-turn conversation
passed end to end (scoped analyze evidence-scored, follow-up inheritance,
explain with no new query, honest clarification with a persisted draft). Records:
`docs/acceptance.md` P5 section, `runtime/acceptance/conversation-20261013-220711.json`.
Re-observed 2026-10-14 after the code-review fixes (session locking, read-time
grant checks, UI-over-model scope precedence, filter typing, exact skill pins):
static **400 passed**, the affected integration suites **26 passed** live, the
matrix **98 passed / 1 skipped / 25 deselected**, the isolated browser
regressions **15 passed**, the live journeys **7 passed**, A09 **7/7 - 3/3 -
10/10** (with the harness now matching the target cell by dimension name), and
the multi-turn conversation re-run. Two defects were found and fixed during that
re-verification: drilldown validated its dimension after session state, and the
harness compared group keys as ordered tuples. See `docs/acceptance.md`,
"2026-10-14 review fixes — live re-verification".
Re-observed 2026-10-15 after the frontend control and layout fixes (native file
input replaced, leaking `label { flex-direction: column }` contained, chart
values moved out of the bar and the hidden read window reported): the mocked
browser regressions now run to completion on this host - **29 passed**, against
18 before that work, superseding the earlier stalled attempts - the frontend
typecheck and production build exit 0, and the changed screens were re-checked
against the deployed stack (Skills, the Ask 技能 panel, `/admin/permissions`,
the 576-point chart). The backend static suite, the service matrix, A09 and the
deployed journeys were **not** rerun for this frontend-only change. See
`docs/acceptance.md`, the three "2026-10-15" records.
Re-observed 2026-10-16 after the product rename to **Oceanus** (the name is now
one constant in `frontend/src/i18n/index.ts` and reads the same in both
locales; the FastAPI title is `Oceanus API`): static **469 passed** (pass
`--basetemp=runtime/pytest-tmp`), frontend unit **11 passed**, typecheck and
production build exit 0, the mocked browser suite **43 passed**, and the rebuilt
deployed stack was verified live (page title, top-bar brand in zh and en, live
OpenAPI title). The service matrix, A09 and the deployed journeys were **not**
rerun - strings only. See `docs/acceptance.md`, the 2026-10-16 rename record.
Re-observed 2026-10-16 after the **catalog filters** work (datasource / namespace
/ declared business domain, `GET /datasets/facets`, the optional `domain:` field
in `metadata/semantic/*.yaml`): static **487 passed**, mocked browser suite
**52 passed**, the live catalog journeys **3 passed** against the deployed stack,
and the filtered catalog was checked in the browser (zh-CN, 13 rows, no console
errors, no horizontal overflow at 1440/1024/768/390 px). The service matrix, A09
and the real-model conversation run were **not** rerun (no execution, permission,
validation or evidence contract changed), and `ainative.domain` was not observed
inside a running DataHub instance. See `docs/acceptance.md`, the 2026-10-16
catalog filters record.

Re-observed 2026-10-16 after high/medium priority work: static **515 passed**,
frontend units **11 passed**, production build exit 0, repeated mocked browser
regressions **108 passed**, focused real socket/TLS tests **3 passed**, live-service browser journeys
**7 passed** (deterministic provider); real PostgreSQL/MySQL integration
**97 passed / 14 skipped / 25 deselected** (Doris/DataHub absent). Analysis
planning and memory extraction now perform network calls outside DB sessions,
with fenced apply and durable reservations/claims. Conversation history pages
backward by sequence; retry freezes original inputs; mobile scope uses a native
dialog; diagnostics are available per message; Skills rank descriptions and
reconcile required inputs. See the latest acceptance record for the live
PostgreSQL/MySQL and browser results and explicit Doris/DataHub/Spark/A09 gaps.
The user explicitly deferred DeepSeek/A09 until later. Credentials may also be
bound as redacted `AIND_LLM_API_KEY`; the saved
cloud draft needs review/save and publish before the network/secret change applies.

Latest account/settings round (2026-10-16): migration **d2e6a9f18c40** adds
per-user display name, nullable locale/timezone and settings version (still 31
tables). Apply migrations before starting updated API/workers. `/settings` and
`/profile` redirect, closed/CSRF/version-checked PATCH `/auth/me`, account-owned
language/timezone and session-list pagination are implemented. Guest language
storage is separate; auth changes clear/reload other workspace tabs. Spark's
shared-client cancel race was reproduced on real Spark 3.5.3 and fixed with a
separate bounded CancelOperation connection; standalone fixture lives in
`infra/spark-preview/compose.cancel-test.yaml`. Static **517 passed**, integration
**111 passed / 14 skipped / 25 deselected** (PG/MySQL/TLS + actual Spark RPC test),
frontend units **12 passed**, live browser **8 passed** plus the final settings
journey **1 passed**, production build exit 0 and schema verification passes.
Final mocked-browser suite: **58 passed**. Doris/DataHub
full acceptance remains blocked by disk footprint; Spark secure auth, read-only
authorization, shared-HMS lineage/E2E and DeepSeek/A09 remain open/deferred.

Continued TODO round (2026-10-16): conversation rename/archive/restore and
archived browsing, lazy saved-SQL frontend management, self-service password
rotation with current-password/CSRF checks and all-session revocation are
implemented. Login and password rotation serialize on the user row. Quota tests
cover 50 skills / 20 versions; dedicated session SSE contracts include immediate
empty-idle termination (fixed). Static **520 passed**, frontend units **12 passed**,
production build exit 0, live browser **9 passed**, mocked browser **62 passed**
and the final affected browser cases **4 passed**. Final serial service matrix:
**120 passed / 14 skipped / 28 deselected** (Doris/DataHub absent); see acceptance. No new migration is
needed beyond d2e6a9f18c40. Remaining infrastructure/model limitations still apply.

Merged 2026-10-18: the catalog branch (`wip/oceanus-catalog-20261016` - the
Oceanus rename plus catalog filters, verified on 2026-10-16 but left uncommitted
in this checkout) was reconciled with main's 13 commits. The conflicts were the
catalog page (main's route-driven detail now carries the branch's filters),
the two same-named catalog regression specs (split into `catalog.spec.ts` for
the filters and `dataset-detail.spec.ts` for the deep links), the Administration
journey's language (kept main's zh-CN pin and translated assertions, with the
request-status assertion scoped to the badge) and `docs/todo.md` (main's backlog
plus the branch's open requests). Verified on the merged tree: static **689
passed**, frontend unit **17 passed**, typecheck and production build exit 0,
mocked browser suite **81 passed**, and `frontend/openapi.json` regenerated from
the merged backend is identical to the merge result. The stack was then started
from the merged tree (core plus the MySQL/Doris engines, migrations applied,
bootstrap complete) and re-verified live: `smoke_core` all checks passed, all
three datasources HEALTHY through the platform, `llm-check` ok
(`deepseek-flash`), and the catalog-filter, SQL-workspace, administration and
analysis journeys **8 passed**. The service integration matrix and A09 were
**not** run for the merge. See `docs/acceptance.md`, the 2026-10-18 merge record.

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
8. **Model output is constrained**: it may only choose closed platform actions,
   allowlisted dimensions and `relation_id`s. It never supplies arbitrary tools,
   SQL, URLs or code, and never does the
   arithmetic - the deterministic kernel in `app/analysis/` does, in `Decimal`.
9. **`ground_truth.json` is evaluation-only.** It is never mounted into metadata or
   agent context.
10. **Datasource secrets live in mounted files** (`infra/local-secrets/<ref>.json`);
    the database stores only a `secret_ref`. LLM credentials may alternatively use
    the redacted cloud process secret `AIND_LLM_API_KEY`. Never commit `.env`,
    credential values or secret files.

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
- Authentication failure limits are shared in PostgreSQL. Acquire limiter locks
  before user locks. SSE polls persisted events and checks auth sessions/grants
  before replay; same-host API workers share DB/results/secrets. Multi-host HA
  still requires shared object storage and separate operational acceptance.

Remaining feature round (2026-10-16): apply **e4b8a1c79d20** after d2e6a9f18c40.
Team/private memory has current-grant checks at every read, local bilingual domain
vectors, and per-scope extraction locks. Closed planning and observation actions
run outside transactions; structured format repair is bounded to one retry and
budgeted. Explain templates use bound artifacts; charts show Top-10 with expansion
and paging. Static **531 passed**, frontend units **12 passed**, build exit 0,
PG/MySQL/TLS matrix **127 passed / 15 skipped / 28 deselected**, separate live Spark
**1 passed**, final priority cases **15 passed**, schema verification **32 tables**.
Mocked browser full attempt **63 passed / 1 failed**; missing admin-error alert
role fixed and final affected charts/teams **8 passed**. Real DeepSeek/A09 remains
deferred; remaining infrastructure/load acceptance gaps are in docs/todo.md.

Operational completion (2026-10-16): migrate to **f6a12c83d095** (33 tables).
Account metric/dimension/skill defaults seed only new sessions. Owner/idempotency
reruns create new child reports and reset completeness. Production login/password
failure limits use PostgreSQL, with limiter-before-user lock order. SSE is DB
polling across replicas and continuously checks auth sessions/user/grants. Spark
verified TLS and PLAIN-over-TLS configuration plus exact-round-trip AST scalar
binding are implemented; actual PLAIN/read-only/shared-HMS lineage remain unverified.
Static **550 passed**, service matrix **136 passed / 14 skipped / 29 deselected**,
final operational subset **7 passed**. Two-minute cross-author load: **360 sessions /
45 waves**, one deduplicated memory, no active queue/lease leftovers. Frontend
units **12 passed**, build exit 0, mocked browsers **66 passed**; isolated dual-API
live journeys **10 passed** plus added defaults/rerun **1 passed**. Preserve actual
remaining infrastructure/model boundaries in docs/todo.md. Native Uvicorn launches
must use --no-proxy-headers. Test-load fixtures truncate their configured control
DB and reseed source fixtures: use dedicated disposable databases only.

Spark identity acceptance (2026-10-16): `setup_tls_fixture.py --auth PLAIN`
compiles a single-identity CUSTOM authenticator from the Scala source using the
pinned Spark image's own compiler/Hive API. Output must be below ignored runtime/
and empty; credentials/config/keystore are private and never printed. Focused
Spark suites **10 passed** (including authenticated cancellation), static **550
passed**. Source read-only authorization, production identity-service integration
and shared-HMS listener lineage remain unverified; this fixture grants no policy.

External security gates (2026-10-16): static **609 passed**; final changed live
subset **1 passed / 2 skipped**. `scripts/verify_spark_readonly.py` accepts private
owner/reader connection files for the same dedicated aind_authz_* namespace only;
its real unprotected-source negative control fails and cleans its own tables.
`datahub_token.py --verify-auth --token-file ...` writes nothing and checks
anonymous/invalid denials then valid GraphQL. Token storage is atomic/private;
DataHub adapter errors are fail-closed and omit raw remote diagnostics. Actual
source-policy and auth-enabled GMS tests skip when unconfigured and fail on an
explicit broken configuration. Neither skipped deployment is considered verified.

Resource preflight (2026-10-16): doctor reports physical/process-cgroup/Docker VM
memory separately; visible v1/v2 ancestors constrain the budget. --profile full
--strict (make / dev.ps1 doctor-full) selects existing conservative full estimates
without starting services. Static **623 passed**, resource units **14 passed**;
actual strict full preflight exited 1 for repository disk/busy DB ports. Process
limit was 32 GiB despite 33.3 GiB physical. This is not 16 GiB full-stack proof.

Authorized recall (2026-10-16): planner takes at most 1000 authorized candidates,
with source grants before LIMIT, and injects at most eight. Knowledge list/count
also filter in SQL; each operation is one query. Missing identity/roles and
inactive users fail closed. No migration/API change. Static **623 passed**;
real PostgreSQL recall + team/private regressions **5 passed**, including an
1802-row candidate-starvation case and grant expiry/split roles. Local semantic
vectors remain domain-specific; extraction dedupe still has a separate 200-row
window. General pretrained embeddings/global history search are not claimed.

Continuous learning (2026-10-17): per-wave extraction now precedes the next warm
analysis wave. This exposed lost reuse increments (8 warm uses recorded as 2).
Reuse uses atomic SQL updates, stable multi-row ID locks and monotonic last-use
time; rollback is covered by stale-instance concurrent tests. Static **623
passed**, final real PostgreSQL batch **13 passed** including 600.498 seconds /
1480 analyses / 1472 exact warm uses / one deduplicated memory. Quiescent Linux
threads/FDs stayed 4/14; RSS grew about 1.9 MiB from warmup. This is not overnight
or real-model proof. make test-load SOAK_SECONDS=600 runs this bounded loop.
