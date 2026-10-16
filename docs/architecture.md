# Architecture (M0-M6 implemented slice)

This document describes what is actually implemented. The authoritative long-term
design is the project specification; sections marked "planned" are not built yet.

## Processes and trust boundaries

The optional Spark path uses an existing Spark Thrift Server for SQL execution
and the same Hive Metastore's Thrift endpoint for DataHub ingestion. Both are
external services. One registered `spark` datasource owns each table and its
single DataHub `hive` URN. The API still only validates and enqueues user SQL;
the worker uses Impyla against Spark Thrift Server. Spark listener events must
use the same DataHub platform instance and environment to attach job lineage.
The current GraphQL lineage response exposes granted registered datasets only;
Spark DataJob nodes are not a separate UI entity yet. The connector path was
accepted live on 2026-09-28/29 (`docs/acceptance.md`); Spark job lineage,
read-only source authorization and browser E2E coverage are still open.

```
Browser (React SPA, 127.0.0.1:3000)
   |  cookies + CSRF header, same-origin via nginx
   v
API process (FastAPI, 127.0.0.1:8000)  -- reads/writes -->  Control PostgreSQL
   |  validation, auth, DTOs                                 (business state,
   |  metadata introspection via provider                     task queue, audit)
   |
   +-- enqueue query job (same transaction as validation/audit/event)
                     |
                     v
           query-worker (same image, separate process)
             |  claim (SKIP LOCKED) + capacity + lease + fencing token
             |  provider connection (read-only role + read-only session)
             v
           Source PostgreSQL (separate server/volume/role)
             |
             +--> result files (Arrow IPC + JSON) under the results volume
             +--> terminal status + result row + audit + event (one transaction)

agent-worker (same image): runs the reconciler
   (queue timeouts, suspect/lost leases, TTL cleanup) and checkpointed analyses.

ingestion runner (ainative-ingestion = pinned official DataHub ingestion image
   + platform runner): claims QUEUED ingestion_tasks (SKIP LOCKED), runs
   `datahub ingest` with the recipe payload written by the API, waits for the
   assets to become searchable, maps the observed URNs back onto `datasets`,
   emits semantic custom properties and finalizes the task.

DataHub stack (pinned quickstart, shared `datahub-net`): authoritative catalog
for search/context/lineage. Ingestion runs inside the ingestion image; the API
only reads through the GraphQL adapter (search / dataset / lineage).

Deterministic analysis kernel (in-process library, no SQL): compare / change
contribution / impression x eCPM drivers / whitelisted cross-source joins over
already-aggregated query results, all Decimal arithmetic.

SSE: `task_events` rows are the source of truth; the API streams them and
supports Last-Event-ID replay.
```

Key rules enforced by the code:

- The API never executes user SQL against a source database. It validates SQL,
  performs controlled metadata introspection through provider methods, and creates
  queued jobs. Only workers open execution connections.
- The worker re-checks permissions and re-validates the exact SQL it is about to
  execute (schema/permission drift fails the job with `SCHEMA_CHANGED` or
  `PERMISSION_DENIED` instead of running stale SQL).
- Results are published atomically: files first (temp -> fsync -> rename), then the
  database transaction that writes `query_results`, the terminal status, the audit
  record and the event.
- All worker claim/publish paths use the same lock order and the same transaction;
  capacity is enforced with row locks (global -> datasource -> user), not in-process
  semaphores.

## Repository layout (implemented parts)

```
backend/
  app/
    api/routes/        auth, datasources, datasets, queries (incl. SSE), saved
                       queries, sessions/conversations (incl. session SSE), skills,
                       admin, health - DTOs in api/dto.py
    auth/              Argon2id, sessions/CSRF, RBAC capabilities, rate limit
    datasource/        registry service, secret resolver, host policy
    providers/         base protocol, registry, postgres/mysql/doris/spark providers
    metadata/          DataHub adapter + GraphQL documents, ingestion tasks,
                       recipes, semantic registry, context/lineage service
    metrics/           versioned metric contracts (registry) + compiler
    analysis/          compare, contribution, drivers, bounded join, relations
    query/             gateway, validator, resolver, limits, scheduler, executor
    results/           value serialization, signed cursors, atomic file store
    events/, audit/    task_events and audit_logs helpers
    repositories/      SQLAlchemy persistence per aggregate
    workers/           query_worker, agent_worker (reconciler + analyses +
                       conversation turns + learning), shared runtime
    agent/conversation/  session scope merge, intent decision, skill loading,
                       turn budget, explanation, turn orchestrator
    skills/            SKILL.md/ZIP parsing, strict front matter, versions
    models/orm.py      control schema (31 tables: the spec 8 list + business_memory
                       + conversation_turns + skills/skill_versions)
  migrations/          Alembic (baseline 4f36847b8351 + permission state + business
                       memory + conversation turns + skills)
  tests/               unit, security, contract, integration, fixtures/datahub
frontend/              React SPA + generated OpenAPI types + Playwright journeys
demo/                  M4 generator, scenarios, schemas, loaders
ingestion/             runner.py (ingestion worker) + publish_lineage.py (SDK)
metadata/
  recipes/, semantic/, metrics/, relations/   versioned metadata contracts
  skills/              builtin skill packages (SKILL.md + references)
infra/                 versions.env, secrets example, DataHub pinned compose
scripts/               doctor, setup_secrets, smoke_core, verify_schema,
                       seed_sources, metadata_sync, verify_metadata,
                       demo_generate, demo_load, demo_verify,
                       capture_datahub_fixtures, datahub_token, dev.ps1
```

`runtime/` (gitignored) holds generated demo runs, ingestion payloads and the
evaluation ground truth; it is never mounted into the agent's metadata context.

## Query lifecycle (M1)

1. `POST /api/v1/queries` validates request size, resolves the datasource, computes
   effective limits and (optionally) resolves the idempotency key.
2. The gateway parses the SQL with SQLGlot: exactly one statement, SELECT/UNION
   only, node denylist (writes/DDL/session/locking/`INTO`), function allowlist,
   join policy, subquery depth and relation budget.
3. Before resolving names, the gateway filters active datasets to those with
   both current `query` and `discover` grants. Hidden/unknown names get one
   generic refusal. Table references are resolved through SQLGlot scope analysis (CTEs are not
   physical tables), rewritten to fully qualified registered names, then `qualify`
   expands `*` and validates every column against the latest schema snapshot.
4. The outer `LIMIT` is rewritten to `max_rows + 1` (the extra row exists only to
   detect truncation; it is dropped before publication).
5. `query` and `discover` permissions are re-checked for every referenced dataset.
6. `query_jobs` + `task_queue` + `query_dependencies` + audit + `query.queued`
   event are written in one transaction.
7. A worker locks the resource before its queue (`FOR UPDATE SKIP LOCKED`), locks capacity
   rows, creates an execution lease with a fencing token and commits.
8. The monitor thread heartbeats (5 s), polls the cancel flag, and cancels the
   source query at the deadline. The executor streams batches from a server-side
   cursor, counts rows and encoded bytes, detects truncation with the N+1 rule and
   closes the cursor.
9. Success: result files are published atomically; then a single transaction stores
   the result row, sets `SUCCEEDED`, releases the lease, marks the queue item done
   and appends the `query.finished` event and audit record.
10. Failure/cancel/timeout/lost: the same publish path with the corresponding
    terminal state; stale workers are rejected by the fencing token. File-publication
    errors also use this terminal path. A cancellation committed during publication
    is resolved under the job lock, with failure details preserved as a cause.

## State machines

Query: `QUEUED -> RUNNING -> {SUCCEEDED, FAILED, CANCEL_REQUESTED, TIMED_OUT, LOST}`;
`QUEUED -> {CANCELLED, FAILED}`; `CANCEL_REQUESTED -> {CANCELLED, TIMED_OUT, LOST}`.
Transitions are enforced by an explicit allow-table (`app/repositories/queries.py`),
never by ad-hoc status writes.

Lease: `ACTIVE -> SUSPECT -> RELEASED`; the reconciler marks suspect after the lease
expires and reaps to `LOST` only after the source-side hard deadline
(`statement_timeout`) plus a margin has passed, because PostgreSQL self-terminates
the query in that window.

## Catalog substrate (M3)

The platform registry is filled by controlled introspection of the registered
source (`POST /admin/datasources/{id}/catalog-refresh`): `information_schema`
reads through the provider, schema hash per dataset, sensitive column policy
(base tables with matching columns are skipped), views only after an
administrator confirms them as secure (definition hash recorded). Dataset
metadata snapshots have a 15-minute TTL.

DataHub is the authoritative catalog for search, context and lineage:

1. `POST /datasources/{id}/sync` builds an ingestion recipe from the registry
   (exact-match table allowlist, `platform_instance`, fabric env, stateful
   ingestion, top-level `pipeline_name`) and writes a payload into the shared
   ingestion volume plus an `ingestion_tasks` row and queue item in one
   transaction. Only one active ingestion per datasource is allowed (409).
2. The ingestion runner claims the task, executes `datahub ingest`, then waits
   for the assets to be searchable. Candidate URNs are accepted only after a
   direct lookup verifies the entity exists and the name matches; the observed
   URNs are written back to `datasets` (failed syncs keep the previous mapping).
3. Semantic entries from `metadata/semantic/*.yaml` are published as
   `ainative.*` custom properties; the context service reads them back, so the
   searchable catalog and the executable contract stay aligned.
4. `GET /datasets/{id}` builds the context from the live schema snapshot plus
   DataHub (description, owners, custom properties) and falls back to the
   platform registry when DataHub is unavailable (serving only within-TTL
   snapshots, marked `metadata_stale`). The cache key includes the DataHub URN so
   a completed sync invalidates the pre-sync context. The DataHub UI deep link is
   returned to administrators only.
5. `GET /datasets/{id}/lineage` walks the DataHub graph (BFS with exact edges),
   filters every node by platform authorization, distinguishes
   `not_ingested` / `no_upstream` / `permission_filtered` / `unavailable`, and
   labels edges with the publisher's declared type (`declared_by_demo_pipeline`)
   or `extracted` from the connector. Platform-side execution history is kept
   separately as `analysis_evidence`.

The GraphQL documents live in `app/metadata/graphql/` and are pinned by contract
tests replaying real captured responses (`tests/fixtures/datahub/`, regenerate
with `scripts/capture_datahub_fixtures.py`).

## Deterministic analysis kernel (M4)

- `demo/` generates the synthetic dataset (spec 26): deterministic per
  (seed, as_of, days, scale, scenario, generator version), Decimal arithmetic,
  CSV + Parquet, manifest/schema/pipeline-lineage and an evaluation-only
  `ground_truth.json`. Scenario injections (ecpm_drop, traffic_drop,
  mixed_offset, no_change, incomplete_day, schema_drift, config_duplicate,
  canonical_67) are explicit single-cell edits.
- `demo/loaders.py` loads the run: Doris through the BE Stream Load HTTP API,
  the PostgreSQL/MySQL configuration tables with parameterized inserts, and the
  derived `demo.revenue_daily_total` from the SQL recorded in
  `pipeline_lineage.json` (whose hash the loader executes verbatim).
- `app/metrics/compiler.py` compiles a versioned metric definition into
  canonical SQL (date range mandatory, named parameters, closed formula grammar
  - components, numbers, `+ - * /`, `nullif/coalesce/abs/round`; anything else
  is refused). Multi-dataset metrics compile one aggregate per dataset plus a
  composition formula; nothing joins fine-grained facts. The runner evaluates
  that closed formula in Decimal over named component aggregates (including
  SQL NULL semantics). Nonadditive breakdowns also compile a separate period
  aggregate with the same filters and windows but without breakdown dimensions.
  Nonadditive/nonlinear formulas do not produce additive contribution charts.
  Every compiled query also selects `COUNT(*)` under a collision-safe alias;
  the query descriptor persists that alias. The observer uses period-total
  queries where present, otherwise the primary grouped aggregates, to record
  per-period/per-source counts with query IDs. These are row-presence evidence
  (a sum over returned groups can be a lower bound when results are truncated),
  not an assertion that every day is loaded. No extra query or query budget is
  needed. If any required source has zero rows in either period, the comparison
  and missing period values are NULL, contribution/driver extension is skipped,
  and synthesis emits PARTIAL with dated limitations, no numeric claims,
  hypotheses or charts, even with completeness set to complete. Populated zeros
  retain their existing comparison behavior. Coverage is stored in the
  period-comparison artifact; calculation checkpoints are now version 3.
  Older incompatible in-flight calculation checkpoints require resubmission;
  previously completed reports remain historical artifacts.
- `app/analysis/` implements the specification's arithmetic exactly:
  `compare_totals` (`change_pct`, baseline-zero handling), `decompose_contribution`
  (net/gross shares, contribution_pp, NULL bucket, OTHER merge, additivity
  check), `decompose_revenue` (symmetric impression x eCPM split, tolerance
  1e-6), and `join_results` (whitelisted relations, cardinality refusal,
  `[valid_from, valid_to)` as-of matching, amount preservation).
- Relations are registered in `metadata/relations/*.yaml`; a model can only
  choose a `relation_id`, never a join expression.
- Metric coverage is declared per table in `metadata/metrics/*.yaml`, and a
  table without a declaration is deliberately not analysable: the Ask screen
  says so (with the table's grain and measure columns) instead of guessing an
  aggregation. `campaign_spend` and `campaign_attributed_revenue` over
  `demo.campaign_cohort_daily` and `cohort_size` over `demo.retention_daily` are
  declared; their `freshness.date_column` is `cohort_date`, not `dt`. The cohort
  *rates* (D1/D7 retention, ROI_D1) stay undeclared until cohort maturity and a
  day-0 cost rule are enforced in the compiler - `SUM(d1_users)/SUM(cohort_size)`
  would mix immature cohorts and read as a plausible wrong number, and the
  generator writes a cost row per observation day rather than one acquisition
  cost per cohort.

## Business memory (learned business knowledge)

The platform learns from its own analyses: ask more, and the planner knows more
about the business. `app/agent/memory.py` and `business_memory` (a table beyond
the specification's frozen DDL list) implement the loop in three steps.

1. **Learn.** When an analysis reaches COMPLETED or PARTIAL, the agent worker
   builds a digest of the *governed record only* - question, metric contract,
   the kernel's ranked segments with a direction and support flag, the kernel's
   own limitations - and asks the configured model for at most three durable
   statements. Validation is what makes prose safe: a statement may not contain
   a percentage, an amount, or any figure that does not appear verbatim in that
   record (a version id such as `4.2.1` is a value the analysis actually
   grouped by; "down 67%" is refused). Every stored row links to its analysis
   and its comparison artifact.
2. **Recall.** Planning reads the metric's memories back (most-reused first) and
   puts them in the planner prompt as a labelled, untrusted data block. They
   influence only which dimensions the model selects, and the existing allowlist
   validation still decides what is legal. Use is counted (`reuse_count`), so the
   store converges on the statements that keep earning their place.
3. **Curate.** A fresh row is `proposed`: already used as a hint, labelled as
   such in the UI, and confirmable or rejectable by an administrator
   (`POST /memory/{id}/confirm|reject`, audited). A rejected statement is never
   used again, and re-learning it later bumps the counter instead of resurrecting
   it.

Failures here can never change an analysis outcome: extraction runs after the
analysis is published, in its own transaction, and records a loud
`analysis.memory.skipped` event when the model endpoint is not configured. A
catch-up pass in the worker loop picks up analyses whose extraction never ran,
so learning is self-healing rather than best-effort. The visible surfaces are
the 业务记忆 panel on the Ask screen (scoped to the selected metric, with
curation) and `GET /analyses/{id}/memory` on the analysis screen (what this run
read and what it produced).

Deliberate limits: retrieval is metric-scoped text, not embeddings; statements
are number-free by construction, so figures still live only in the evidence
chain; and a second worker would need a claim/lease for extraction before the
loop could run at that scale.

## Conversational Ask and skills

Ask is a persistent conversation, not a form: `app/agent/conversation/` and the
`conversation_turns` table implement the design dated 2026-10-13.

- **Sessions keep a confirmed scope.** A session stores `resolved_context`
  (metric, baseline/current windows, dimensions, filters and a three-state
  completeness value) with a `context_version`. Each turn's model call may
  propose a scope patch (`set` and `clear`); the server merges it (explicit UI
  values beat model values, which beat inheritance), freezes relative dates at
  receive time and writes the result back only when the turn completes. A patch
  whose required fields are still missing becomes a `clarify` reply instead of an
  analysis, and the missing fields persist as a draft for the next turn.
  The UI stores only user field overrides over the latest confirmed scope;
  untouched fields follow asynchronous updates. Filters retain their raw input
  until validation rather than being parsed on every keystroke.
- **One decision per turn, on the same terms as the analysis loop.** The intent
  call (`clarify` / `analyze` / `explain` / `help` / `unsupported`) runs outside
  any transaction, may only choose allowlisted dimensions and skill version ids,
  and never supplies SQL, tools or URLs. `explain` resolves references against
  the session's own analyses and their current grants and quotes stored evidence
  - it starts no query.
- **Turn lifecycle**: prepare (short transaction: message + turn row + queue
  entry) -> decide (model call, no locks held) -> apply (short transaction with a
  late-reply guard). At most one active turn per session is enforced by a partial
  unique index; `WAITING_INPUT` is terminal for the turn. A crashed worker
  recovers by requeueing stale turn claims; budgets are persisted, so recovery
  neither resets nor doubles the allowance.
  New turns, legacy analysis submissions, chart drilldowns and session
  configuration/archive writes use the same session row lock and busy check.
  A nonterminal legacy analysis also makes the session busy. Idempotent replays
  are resolved before the busy check; a title-only rename remains allowed.
- **Evidence is checked again when read.** Details, lists and cancellation
  responses preserve lifecycle metadata but hide reports, state, plans, context
  and step results after a dataset's discover or query grant is revoked. Reports,
  evidence and chart reads use the same grant check. Transcript explanations
  check every quoted analysis, including ownership and missing references;
  model history and recent-analysis context use the same current grants.
  History is the latest twelve messages, presented in chronological order.
  The disabled-feature Ask fallback uses one submitted scope snapshot and one
  idempotency key; errors do not trigger a render-driven retry.
  Model memory retrieval checks the source analysis's registered dependency IDs
  against the requesting user's current grants; a memory without traceable
  dependencies is not injected. The shared knowledge list and curator workflow
  keep their existing authenticated-reader contract.
- **Skills are versioned advisory text.** `app/skills/` parses `SKILL.md` and ZIP
  packages under strict limits (closed front matter, safe YAML, path and size
  caps, deterministic content hash), stores immutable versions in `skills` /
  `skill_versions`, and never executes or imports anything from a package. A
  turn loads at most three versions in `off`/`manual`/`auto` mode, layered as
  candidate -> body -> declared references with a 24 KiB per-turn content cap,
  and records exactly what loaded or why it was skipped. The loaded text is
  injected as labelled untrusted data: any structured proposal it inspires still
  passes the same allowlist gates as model output.
  The picker includes enabled personal uploads. Manual selections may use an
  owned version for that turn without a prior session binding; an explicit empty
  selection loads nothing. Saving the selection pins exact version IDs with an
  expected context version. Auto mode considers explicitly bound personal
  versions before builtin candidates; a new upload never replaces a pinned
  version. Within those tiers, question/description lexical similarity, metric and
  dimension relevance rank candidates. Selection is restricted to offered IDs
  and revalidated against final scope. Required inputs are unioned; missing
  fields and incompatible metric constraints clarify rather than silently
  replacing explicit scope. Archived or foreign versions cannot load.
- **Budgets**: a turn has a UTC `deadline_at`, token and model-call allowances;
  the analysis it creates runs on the *remaining* sub-budget, never a fresh one.
- **Live updates**: `GET /sessions/{id}/events` streams persisted, monotonic
  events (message/turn/analysis state, skill loads, revocations) with
  `Last-Event-ID` resume; the UI also polls, so a dropped stream degrades to
  polling instead of a stuck composer.
- The whole module sits behind `AIND_CONVERSATIONAL_ASK_ENABLED` (default off);
  with it off the turn write API answers 503 `CONVERSATIONAL_DISABLED` and the
  UI falls back to the one-shot analysis path.

## Interface localization

`frontend/src/i18n/` owns the Chinese (`zh-CN`) and English (`en`) interface
catalog and the shared locale store. Components subscribe with `useLocale()` and
resolve labels with `t()`; stateful notices store message keys and parameters so
an already-visible notice changes language too. Interpolation is a single pass
and React renders its output as text. No HTML is injected by translations.

The language selector is available at login, at the unauthenticated gate and in
the workspace header. Signed-out preference order is a supported `ainative.locale`
value in localStorage, then the browser language. Authenticated preference comes
from the user row; null means the browser language, without inheriting the guest
storage choice. Authenticated changes persist to the account, and storage events
notify same-account tabs to refetch authoritative preferences. Storage failures do
not prevent server persistence. Locale changes update document language/title and
rerender subscribed components without remounting routes, clearing the query
cache, resubmitting work or resetting drafts. Date/time formatting uses the
selected locale and the account's timezone (browser timezone when unset); chart display formatting uses Intl,
with exact audited Decimal strings retained in table values and tooltips.

API requests and multipart uploads include `Accept-Language`. Existing backend
contracts, error codes and stored records are unchanged. `ErrorMessage` provides
localized guidance for the backend's stable error codes and exposes the original
diagnostic with a keyboard- and touch-accessible button. Unknown error codes
fall back to their original message. SQL, identifiers, result cells, catalog
metadata, user/model prose and stored report/evidence quotations retain their
source text; automatic content translation and model response-language control
are outside this interface change. No database migration is needed.

Observed verification is recorded in `docs/acceptance.md` under the 2026-10-15
Chinese/English interface entry.

## Not built

- Model-driven plan and tool selection. The planning call may only choose up to
  three dimensions from the metric's `allowed_dimensions`; the plan's steps come
  from `comparison_plan()` and the one adaptive extension is rule-driven
  (`_extend_with_driver_step`: materiality + a declared `driver_decomposition`
  metric + budget and depth gates). Nothing yet lets a model choose steps or
  iterate over tools.
- The shared/dashboard screens are not part of the V1 specification and were
  not invented.
- The production phases of the specification (sections 31-32): Kyuubi/Spark batch
  provider, Flink streaming jobs, MCP egress and enterprise semantics. See the
  roadmap row in `docs/todo.md`.

### 2026-10-15 concurrency and resource bounds

Query resource locking is job -> queue -> lease for claim, publication and
reconciliation; capacity locks retain global -> datasource -> user ordering.
Analysis/turn/ingestion claims lock the resource before its queue and skip locked
resources. Analysis planning now reserves its model budget and a bounded lease
in a short transaction, closes the session for the network call, then locks task
and queue again to validate owner, fencing token, lease, checkpoint and terminal
state before applying. Lost calls retain their reservation; replacement calls
reserve again. Late replies cannot publish queries or overwrite cancellation.
Memory extraction uses the same session-free network pattern with a bounded
per-analysis claim token/expiry, at most three attempts and fenced post-call writes.
Task-queue state changes use an explicit allow-table.

Optional driver plans compile both periods before enqueuing, submit inside one
savepoint and skip `AnalysisError` without leaving a half-plan. Deterministic
driver errors become report limitations; NULL eCPM breakdown cells remain NULL
and do not enter Decimal conversion.

The API buffers HTTP bodies only to its configured bound before routing; bounded
JSON mappings cannot be persisted without validation. Query result/SSE grants
include discover and query; datasource list topology is administrative only.
Frontend SSE closes on terminal stream control events; session streams restart
for a new active turn. Idle tabs poll session/transcript at 15 seconds to detect
cross-tab activity, and terminal historical analyses stop their own 3-second polls.

### Dataset deep links (2026-10-16)

The catalog links to `/datasets/:id`; the route renders the same governed detail
surface and uses the URL as its selected dataset. Reload and browser history
retain that selection. Dataset-specific form state is remounted on ID changes,
so request reasons, mutation errors and success notices do not cross datasets.
The detail GET uses the existing discover grant check; schema and lineage reads
start only after context is available. Missing or denied context renders an error
and hides the request form; secondary read failures render their own errors.
IDs are encoded as path segments in links and API reads. No API contract or
database migration changed.

### Conversation history and retry (2026-10-16)

`GET /sessions/{id}/messages?direction=backward&limit=40` returns the newest
window in chronological order. Its signed cursor binds session and direction
and pages by immutable sequence, so appending messages cannot shift old pages.
The existing forward reader remains supported. Message rows include turn status.
`POST /sessions/{id}/turns/{turn_id}/retry` requires an idempotency key and an
owned FAILED/CANCELLED origin. It freezes the original resolved scope, clears,
question, parent and loaded skill versions in a new turn; the original stays
unchanged. The normal feature/capability/busy checks still apply. The UI retains
unsent drafts, pages older history, offers retry and reads authorized analysis
diagnostics inline. On narrow screens the scope panel is a native modal dialog.

The model adapter accepts a redacted `AIND_LLM_API_KEY` injected by the cloud
secret proxy, taking precedence over the existing mounted key file. Datasource
credentials still resolve exclusively through their mounted secret references.

### Account preferences and session-list pagination (2026-10-16)

Migration `d2e6a9f18c40` adds `display_name`, nullable locale/timezone and
`settings_version` to users. GET `/auth/me` and login responses include these
fields. PATCH `/auth/me` is authenticated and CSRF-protected, addresses only the
current user, accepts a closed field set and requires `expected_settings_version`.
The repository locks/refreshes that user before comparing versions and applying
only provided fields. Names are bounded/trimmed; locale is en/zh-CN/null and
timezone is a ZoneInfo-validated IANA name/null. An audit records changed field
names. It cannot alter identity, roles, capabilities or passwords. Null resets
language/timezone to browser defaults; a stale save returns 409 with no write.

The settings form preserves dirty fields and their base version across background
refreshes; an explicit reload discards them. Locale and timezone notify subscribers
even when only timezone changes. Account language never overwrites guest browser
preference storage; logout and 401 reset it. Login/logout broadcasts cause other
workspace tabs to clear caches and reload. The conversation rail now follows
GET `/sessions` cursors with an infinite query, deduplicates by session ID and
exposes loading, error/retry and load-more states. Existing backend offset cursor
semantics and updated-at ordering remain unchanged.

### Spark cancellation transport isolation (2026-10-16)

A real Spark 3.5.3/Impyla 0.24.0 overlap reproduced a shared-client response swap:
CancelOperation consumed an in-flight GetOperationStatus response and the status
reader received a response with no operationState (`KeyError(None)`). Cancellation
now uses a fresh bounded connection and sends the pinned HS2 CancelOperation RPC
for the captured opaque handle; execution/status/fetch keep their own transport.
The control connection closes even on RPC failure. Cancellation before submission
stops launch; cancellation during handle publication is sent again once the handle
exists. It remains an asynchronous request, not confirmation that the server stopped.
The live regression checks CANCELED_STATE and a subsequent successful query. This
standalone NOSASL fixture does not verify shared HMS lineage or source authorization.
