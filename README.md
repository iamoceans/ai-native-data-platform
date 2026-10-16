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
source authorization and shared-HMS listener lineage still open; a standalone
TLS browser journey is now verified.

## Status

- **Delivered**: M0-M6. The real-model evaluation (A09) passed on 2026-09-27 -
  ten fixed cases against DeepSeek with the target cell in the top three
  contributors **7/7**, no forced attribution **3/3**, evidence-consistent **10/10** -
  and was re-run field-identical on 2026-10-13 with the conversational layer
  deployed. The conversational Ask workspace and skill packages were accepted on
  2026-10-13 (a real-model multi-turn conversation plus the full regression; see
  `docs/acceptance.md`).
- **Latest features**: closed model action selection, bounded structured-output
  repair, evidence explanation templates, team/private business memory with local
  domain semantic retrieval, and expandable Top-10 charts. Current real-model
  acceptance remains deferred; see the latest acceptance record.
  Dataset details have a shareable `/datasets/:id` route with permission checks.
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
  gateway -> observe -> synthesize) lets a model choose compare/breakdown and
  allowlisted dimensions, then finish or request the declared driver decomposition
  from observed evidence. Arbitrary tools, SQL, URLs and code remain forbidden. With no model configured the platform degrades to the
  deterministic template path and records a warning - it never pretends a model
  was called.
- **Catalog and lineage**: DataHub (off by default) provides search, context and
  lineage; real `extracted` edges and declared demo-pipeline edges are labelled
  apart.
- **Conversations and skills**: Ask is a persistent conversation workspace. A
  session keeps its confirmed scope (metric, windows, dimensions, completeness),
  so a follow-up inherits it; the model may ask for exactly the fields that are
  missing instead of guessing defaults, and an explanation reads an existing
  analysis's evidence without running new queries. Skill packages - a `SKILL.md`
  plus reference files, uploaded as text or a ZIP, alongside the builtin library
  under `metadata/skills/` - are versioned, immutable and hash-recorded, load per
  turn in `off`/`manual`/`auto` mode, and are advisory text only: any structured
  proposal still passes the same gates as model output. The module sits behind
  `AIND_CONVERSATIONAL_ASK_ENABLED` (default off).
  Enabled personal skills can be selected manually or saved as fixed versions
  to a session for later automatic matching. Report-derived content is checked
  against current dataset grants when read and before it enters model context.
  The 2026-10-14 review fixes were re-verified against the live stack
  (integration 26 passed, matrix 98/1/25, browser journeys 7 passed, A09
  7/7 - 3/3 - 10/10, real-model multi-turn conversation; see
  `docs/acceptance.md`).
- **Business memory**: every finished analysis can teach the platform something -
  the model distils at most three statements per run, and they may not contain a
  figure the analysis did not produce, so prose never becomes a second source of
  numbers. Later questions read those statements back as advisory context for
  choosing dimensions, and an administrator confirms or rejects each one (`业务
  记忆` on the Ask screen). Ask more, and it knows more.
- **UI**: a React SPA with the SQL workspace, catalog, history, Ask/Analysis
  (charts, table-equivalent view, drilldown) and the datasource and permission
  administration screens.

## Interface language

The login page and workspace header have a **简体中文 / English** selector.
It switches the entire interface immediately, retaining form drafts and active
queries. The browser language supplies the initial choice (Chinese for `zh-*`,
English otherwise). Before sign-in an explicit choice is kept in browser storage.
After sign-in the account's saved language takes precedence; changes in the header
are saved to that account and synchronized across tabs. Dates use the account's
IANA timezone (browser timezone when unset); chart numbers follow the locale.

Interface labels, statuses, validation and API error guidance are translated.
SQL, query results, catalog descriptions, user-authored content, model prose and
stored report/evidence quotations keep their original text. This is interface
localization; it does not translate or regenerate analysis evidence. Original API
diagnostics remain available through the error's **Original error** control.
See [the observed checks](docs/acceptance.md#2026-10-15--chineseenglish-interface-localization).

## Personal settings and conversation history

Open **Personal settings** in the account link (`/settings`; `/profile` redirects
there) to edit your display name, interface language and IANA timezone. Preferences
are stored per account and restored on other devices; concurrent stale saves
return a conflict and preserve the unsaved form. Roles and capabilities are read-only.
The timezone changes displayed timestamps, not metric periods or query semantics.
Login/logout clears account caches; other workspace tabs reload on authentication
changes. Personal settings also offers current-password-verified password rotation; it
signs out every existing device/session. Login verification and session issuance
serialize with rotation so an old-password login cannot escape revocation.

The Ask conversation rail loads 20 sessions at a time with **Load more conversations**;
this is separate from backward pagination of messages inside a conversation.
Conversations can be renamed, archived and restored; enable Show archived
conversations to browse them. Archived transcripts stay readable. The SQL
workspace's Saved queries panel saves private templates, pages through them and
loads/deletes them; loading fills the editor without executing a query.
Existing installations must apply migration `f6a12c83d095` (`alembic upgrade head`)
before starting the updated API/workers.

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
access to a role is written out in [docs/runbook.md](docs/runbook.md) (section 2.3).

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
key comes from a mounted file or the redacted `AIND_LLM_API_KEY` environment
secret (which takes precedence). Bind it through cloud environment secrets rather
than committing credentials. Endpoint, model and credentials are administrator
configuration that a user question cannot override. Cloud access requires
`api.deepseek.com`; verify the endpoint-reported model before claiming a version.

The second 2026-10-15 review also fixes driver-error skipping, driver percent
escaping, PostgreSQL TLS configuration, MySQL/Doris socket bounds, queue lock
order, failed result publication and current result grants. Nonadministrators
receive datasource summaries without connection configuration or secret refs.
HTTP bodies are capped at 4 MiB by default, with smaller structured-map bounds;
terminal streams and historical analysis polling stop. Spark concurrent
cancellation over its shared transport remains suspected and needs live
verification; see `docs/todo.md`.

## Verification

The 2026-10-15 defect fixes give nonadditive breakdowns an independent period
aggregate and evaluate multi-source composition formulas in Decimal. Nonadditive
metrics produce period comparisons without additive contribution attribution.
Ask's disabled-feature fallback sends once per user submission with an
idempotency key; login denials retain their audit despite request rollback;
memory reads/counts enforce source grants; model history uses the latest window;
query schema validation operates on the authorized catalog. See
`docs/acceptance.md` for executed regression checks. Previously completed reports
are not rewritten; rerun affected analyses. In-flight obsolete nonadditive or
composed calculation checkpoints fail explicitly and must be resubmitted.

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
  The adapter supports verified TLS, NOSASL and PLAIN-over-TLS configuration,
  and finite JSON scalar parameters bound as AST literals with an exact parse
  round trip. Real TLS, parameter execution and a browser journey are verified;
  real PLAIN identity verification remains unverified. Spark
  does not expose a transactional read-only session here; deploy a read-only
  source identity and enforce a server-side query timeout. The connector,
  cancellation and timeout paths were accepted live on 2026-09-28/29; Spark job
  lineage, read-only source authorization and shared-HMS listener lineage still need
  live acceptance; the standalone TLS browser journey does not prove either. See the
  [Spark/Hive runbook](docs/runbook.md#spark--hive-shared-metastore-preview).
- Analysis comparisons require matching rows in both periods for every required
  source. Empty input yields a PARTIAL report explaining the affected period;
  observed zero values remain valid. Row presence does not establish full window
  completeness. Older in-flight analyses without row-coverage evidence require
  resubmission; completed historical reports are unchanged.
- Model actions are a closed, bounded investigation: initial compare/breakdown,
  followed by finish or one declared driver decomposition. Deterministic materiality,
  permissions, query/tool budgets and depth gates still control execution; there
  is no general arbitrary-tool agent. With no model, the rule path remains available.
- Memory retrieval uses local bilingual analytics concept vectors plus word/character
  features, not a general pretrained embedding model. Candidate recall is bounded
  to 200 metric-scoped rows, filtered by team/private ownership and source grants
  before semantic ranking. Each user has at most one team; no team means private
  memory. Existing memory migrates to its source owner's private scope. Team
  changes do not move historical memory. Apply migration f6a12c83d095 before startup (includes e4b8a1c79d20).
  Extraction uses bounded claims, fenced writes and a per-scope transaction lock
  for duplicate checks. A bounded 120-second deterministic-provider soak completed
  360 conversations across two authors, 3 analysis/2 query workers and 4 memory
  extractors, with one deduplicated memory and no active lease/queue leftovers.
  This is a local acceptance sample, not an overnight or production SLO claim.
- Invalid structured model output gets at most one format-repair request, against
  the same closed schema and token/time budget. Both calls are counted; another
  invalid response fails visibly. Real DeepSeek/A09 validation remains deferred.
- The shared/dashboard screens are not part of the V1 specification and were
  not invented.
- Doris runs the local single-BE profile only; multi-BE topologies, workload groups
  and external catalogs are out of V1 scope.
- DataHub auth-enabled mode is untested (the pinned quickstart disables GMS
  authentication) and DataHub profiling is disabled in the recipes; column-level
  lineage is out of scope.
- Local HTTP deployment only, with loopback binding as the compensating control
  (set `AIND_COOKIE_SECURE=true` behind TLS). Login/password failure windows are
  shared in PostgreSQL with bounded key allocation and cross-process serialization.
  SSE polls persisted events, rechecks sessions/grants before replay, and works
  across same-host API workers; no in-memory event fan-out is required. Multi-host
  HA/shared object storage remain outside this local deployment.
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

Account analysis defaults (metric, up to three allowed dimensions and off/auto
skills) are saved in personal settings and seed new conversations only. Explicit
choices and existing conversations take priority; dates and completeness are never
assumed. Analysis pages can rerun a settled report into a new child analysis with
current permissions/metric definitions and idempotency; the old report stays
unchanged and completeness resets to unknown. Apply Alembic head before startup.

For repeatable bounded acceptance on dedicated disposable test databases:
`make test-load SOAK_SECONDS=120` (PowerShell: set AIND_TEST_SOAK_SECONDS and run
`scripts/dev.ps1 test-load`). These pytest fixtures truncate the configured test
control tables; never point them at a user's working database. Spark TLS fixture
setup and acceptance commands are in infra/spark-preview/README.md.
