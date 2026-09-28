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
                       queries, admin, health - DTOs in api/dto.py
    auth/              Argon2id, sessions/CSRF, RBAC capabilities, rate limit
    datasource/        registry service, secret resolver, host policy
    providers/         base protocol, registry, postgres/mysql/doris providers
    metadata/          DataHub adapter + GraphQL documents, ingestion tasks,
                       recipes, semantic registry, context/lineage service
    metrics/           versioned metric contracts (registry) + compiler
    analysis/          compare, contribution, drivers, bounded join, relations
    query/             gateway, validator, resolver, limits, scheduler, executor
    results/           value serialization, signed cursors, atomic file store
    events/, audit/    task_events and audit_logs helpers
    repositories/      SQLAlchemy persistence per aggregate
    workers/           query_worker, agent_worker (reconciler + analyses), shared runtime
    models/orm.py      control schema (27 tables, spec section 8)
  migrations/          Alembic (single baseline revision)
  tests/               unit, security, contract, integration, fixtures/datahub
frontend/              React SPA + generated OpenAPI types + Playwright journeys
demo/                  M4 generator, scenarios, schemas, loaders
ingestion/             runner.py (ingestion worker) + publish_lineage.py (SDK)
metadata/
  recipes/, semantic/, metrics/, relations/   versioned metadata contracts
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
3. Table references are resolved through SQLGlot scope analysis (CTEs are not
   physical tables), rewritten to fully qualified registered names, then `qualify`
   expands `*` and validates every column against the latest schema snapshot.
4. The outer `LIMIT` is rewritten to `max_rows + 1` (the extra row exists only to
   detect truncation; it is dropped before publication).
5. `query` and `discover` permissions are checked for every referenced dataset.
6. `query_jobs` + `task_queue` + `query_dependencies` + audit + `query.queued`
   event are written in one transaction.
7. A worker claims the task (`SELECT ... FOR UPDATE SKIP LOCKED`), locks capacity
   rows, creates an execution lease with a fencing token and commits.
8. The monitor thread heartbeats (5 s), polls the cancel flag, and cancels the
   source query at the deadline. The executor streams batches from a server-side
   cursor, counts rows and encoded bytes, detects truncation with the N+1 rule and
   closes the cursor.
9. Success: result files are published atomically; then a single transaction stores
   the result row, sets `SUCCEEDED`, releases the lease, marks the queue item done
   and appends the `query.finished` event and audit record.
10. Failure/cancel/timeout/lost: the same publish path with the corresponding
    terminal state; stale workers are rejected by the fencing token.

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
  composition formula; nothing joins fine-grained facts.
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

## Not built

- Model-driven plan and tool selection. The planning call may only choose up to
  three dimensions from the metric's `allowed_dimensions`; the plan's steps come
  from `comparison_plan()` and the one adaptive extension is rule-driven
  (`_extend_with_driver_step`: materiality + a declared `driver_decomposition`
  metric + budget and depth gates). Nothing yet lets a model choose steps or
  iterate over tools.
- The specification's `/datasets/:id` deep link: the catalog detail panel serves
  that content instead of a dedicated route. The shared/dashboard screens are not
  part of the V1 specification and were not invented.
- The production phases of the specification (sections 31-32): Kyuubi/Spark batch
  provider, Flink streaming jobs, MCP egress and enterprise semantics. See the
  roadmap row in `docs/todo.md`.
