# TODO and stage acceptance record

Project: AI-Native Data Platform (spec v1.0)
Scope currently delivered: **M0-M6**, with closed model action selection and
team/private memory added on 2026-10-16. The current backlog below is authoritative;
dated milestone sections retain historical execution records and are superseded
by later evidence. The `/datasets/:id` route is implemented.
Rule followed: a stage counts as done only when its gate ran on real services; tests
that were not executed are marked "not executed" and never counted as passed.

Legend: `[x]` done and verified · `[~]` partially verified (see note) · `[ ]` not started.

## Current actionable backlog (2026-10-16, after operational completion)

High-priority implementation requested in this round:

- [x] Personal profile/settings page, display name and read-only identity/roles/
      capabilities. `/profile` redirects to `/settings`.
- [x] Persisted per-account language and timezone, cross-device restoration,
      account isolation, cross-tab synchronization and stale-save conflict handling.
- [x] Session-list pagination beyond the default 20, with loading/error/retry
      states; distinct from pagination of messages inside a conversation.
- [x] Reproduce and fix Spark shared-client cancellation response corruption.
- [~] Full Doris/DataHub acceptance: remains blocked by this 32 GB vfs host's
      image/storage footprint. Current PG/MySQL/TLS/Spark matrix: **136 passed,
      14 skipped, 29 deselected**. The 14 skips require Doris/deployed DataHub.
      Standalone Spark TLS, binding and cancellation are covered; this is not
      source authorization or shared-HMS lineage acceptance.

Product and verification items (current status):

- [x] Account analysis defaults (metric/dimensions/skill mode), applied only to
      new sessions and subordinate to explicit choices; dates/completeness stay unset.
- [x] Shared PostgreSQL login/password failure limits, bounded allocation and
      cross-process persistence; same-host dual API deployment acceptance.
- [x] Per-poll SSE auth-session/user/grant rechecks and revoked event suppression.

- [x] Conversation rename/archive/restore UI and archived-session browsing.
      Current archived transcripts remain readable and retain their composer draft.
- [x] Saved SQL frontend save/list/reuse/delete workflow with pagination; loading
      never executes the statement and unavailable source entries cannot load.
- [x] Self-service password change with current-password verification, bounded
      failed attempts, audit, cookie deletion and revocation of all prior sessions.
- [x] Explain templates copy bound calculation values, describe contributor/driver meaning, and refuse expired references.
- [x] Actual 50-skill / 20-version quota boundaries and idempotent replay tested;
      dedicated session SSE replay/framing/ownership/idle-closure contracts added.
- [x] Bounded sustained load: 360 conversations / 45 waves, two authors sharing
      a team, 3 analysis / 2 query / 4 extraction workers; 360 memory extractions
      converge to one row with no active queue/lease leftovers. Observed 126.776
      seconds, not overnight endurance or a production SLO claim.
- [~] Spark verified TLS, scalar parameters and browser E2E pass. Real PLAIN
      identity verification, read-only source authorization and shared-HMS listener
      lineage still require an authenticator/authorizer/HMS/DataHub stack.
- [x] Chat chart Top-10 by absolute change, expand/collapse, bounded load-more and class/style cleanup.
- [x] Audited owner/CSRF/idempotency-checked rerun API/UI create a new child with
      current contracts and unknown completeness. Actual business reports require
      an owner-selected scope, not an automatic blanket rewrite of history.
- [x] Closed initial/observed model action selection and one bounded structured-output repair; deterministic execution gates remain binding.
- [x] Local bilingual domain semantic vectors, team/private scope, audited membership management and read-time isolation. General pretrained embeddings remain an enhancement.
- [x] Current backlog and superseded feature notes consolidated. Historical
      per-stage not-executed statements remain dated records, not current defects.
- [ ] DataHub authentication-enabled acceptance and full-stack resource proof on
      a 16 GiB host remain unverified (see README/compatibility limits).

Deferred by the user: DeepSeek v4.1 flash connection, model identity verification,
real-model A09 and large-pool automatic Skill choice acceptance. Enterprise/HA,
Kyuubi/Flink/MCP, content translation and other explicitly future/out-of-V1 items
remain roadmap choices, not high-priority implementation claims.

## 2026-10-15 medium defects

- [x] Regressions verify the existing NULL eCPM fix and optional driver skipping;
      companion submissions are atomic and kernel errors become report limits.
- [x] Driver percent escaping, PostgreSQL TLS-mode forwarding and finite
      MySQL/Doris socket bounds verified by unit/attack regressions.
- [x] Resource-before-queue locking, explicit queue transitions and fenced
      publication failure (including a simultaneous cancellation) verified locally.
- [x] Results/query SSE recheck both grants; nonadmin datasource summaries omit
      connection configuration/secret refs; request bodies/maps and limiter bounded.
- [x] Terminal SSE and history polling stop; idle cross-tab context refresh retained.
      Frontend resource tests 7 passed; generated API types and production build pass.
- [x] PostgreSQL contention tests: both executed against an isolated live control
      database on 2026-10-16; see the priority-workflows acceptance record.
- [x] MySQL/Doris connection stalls, PostgreSQL TLS negotiation and live browser
      journeys verified on 2026-10-16. Full Doris/DataHub acceptance remains open.
- [x] Spark shared-socket cancellation race reproduced against real Spark 3.5.3
      and fixed with an independent CancelOperation transport. The live overlap
      regression checks intact status responses, CANCELED_STATE and a later SELECT.
- [x] Analysis planning and memory extraction call the model outside database
      sessions. Post-call writes validate ownership, expiry and fencing; late
      success/failure cannot change a cancelled or reclaimed analysis.

Acceptance: static **469 passed**, frontend resource tests **7 passed**.
See the medium-defects section in `docs/acceptance.md`.

## 2026-10-15 reported defects

- [x] Independent nonadditive period totals and Decimal composition, including
      NULL semantics and unique source-repair keys; static regressions pass.
- [x] Disabled Ask fallback submits once with a scope snapshot and idempotency
      key; typecheck and production build pass.
- [x] Login denial/rate-limit audits survive rollback in local HTTP + SQLite tests.
- [x] Memory HTTP list/counts/curation require source grants; unauthorized query
      resolution cannot reach schema introspection.
- [x] Model history selects the latest twelve messages in chronological order.
- [x] Browser regressions completed on 2026-10-16: 54 cases repeated twice,
      **108 passed**, including the Skills keyboard file chooser.
- [~] PostgreSQL/MySQL matrix and local live-service journeys rerun on 2026-10-16.
      Doris/DataHub and real-model A09 remain pending; see the latest record.
- [x] Superseded: explicit rerun workflow now preserves original historical artifacts.
      Obsolete in-flight calculation checkpoints require resubmission.

See `docs/acceptance.md`: static **425 passed**, frontend typecheck/build exit 0.

## 2026-10-15 UI control consistency (Skills page, shared label styles, charts)

- [x] The upload row no longer renders the browser's native file control: the
      input is hidden and a real button opens it, with the picked file name shown
      next to it. The per-skill 新版本 action is a real button (keyboard
      reachable, same computed style as 停用 / 归档) instead of an unstyled
      `<label class="ghost small">`.
- [x] 显示已归档 keeps its checkbox and text on one line; the `dl.kv` preview
      facts and `header.page-head` have rules instead of browser defaults; the
      page no longer nests two `.page` containers (it was 40px narrower than
      every other page); a wrapped column `.form-row` no longer sizes its
      children to max-content (at 390px the row hung past the card border).
- [x] The Ask page's 技能 chips (and the scope dimension chips) rendered as tall
      pills with a floating checkbox: `.chip` set `display` / `align-items` but
      not `flex-direction`, so the global `label { flex-direction: column }`
      applied. The same leak hit `.check` (admin role pickers) and
      `.inline-label` (admin status filter); all four classes are now listed in
      one rule that keeps inline labels on one row.
- [x] The contribution chart no longer paints its value over the next column: a
      bar is a length on a full-width track and the signed value is text beside
      it (live probe before: a 21px bar holding 159px of text inside a 15px
      column). Values display with two decimals and the exact Decimal on hover;
      shares display as a percentage instead of a 14-digit fraction; the table
      view carries the same formatting plus the exact values; negative bars use
      the declared `--bad` token instead of an undeclared `--danger`.
- [x] The chart reports the points its bounded read window leaves out: the live
      analysis holds 576 groups, the client fetched 200, and the panel used to
      show 200 rows under a 共 576 点 footer with no notice. It now warns
      (`另有 376 个未显示`) and the footer reads 图表 200 / 共 576 点.
- [x] Ten mocked browser cases added (4 `skills.spec.ts`, 1 `ask.spec.ts`,
      6 `chart.spec.ts`). On this host the suite now runs to completion -
      **29 passed**, against 18 passed before this work - which supersedes the
      earlier stalled attempts for this suite. The chip case was run against the
      unfixed stylesheet and fails there (`column` received), and the chart
      cases address markup that did not exist before the change, so they cannot
      pass unfixed by construction. Frontend typecheck and production build
      exit 0.
- [x] Deployed and re-checked live on 2026-10-15 (three rebuilds, `up -d
      --no-deps frontend` each time, backend and workers untouched): the Skills
      page renders with its four real skills and previews
      `metadata/skills/channel-contribution.md` as `SKILL.md` (a wrongly named
      single file still shows `SKILL_UNSUPPORTED`); the 技能 chip measured
      176x25 row after the fix against 136x69 column before it; on
      `/admin/permissions` the `.check` pickers (19px) and `.inline-label`
      filter (34px) are on one row; the 576-point chart renders 200 rows with no
      overflow and states the 376 it left out. Nothing was saved or submitted.
- [x] Superseded on 2026-10-16: Top-10 default, expansion and subsequent bounded
      pages are implemented; see the final feature acceptance record.
- [ ] Not verified: engine matrix, A09 and the deployed Playwright journeys were
      not run for this frontend-only change; the deployed image was built from
      built from the working copy before this change was committed.
- [x] Top navigation now wraps at narrow widths (delivered with localization);
      the 390px browser layout regression passes, including the settings page.
- [x] Superseded on 2026-10-16: datasource IDs use mono-id; result-wrap and
      col-name have styles; unused assistant class tokens are removed.

See `docs/acceptance.md`, "2026-10-15 Shared control styles: native file input
and leaking label default" and "2026-10-15 Chart panel: values, precision and
the hidden read window".

## 2026-10-14 code-review fixes

- [x] Analysis failure handling imports the error enum and publishes failure plus
      queue completion; both generic and model-unavailable paths have regressions.
- [x] Analysis detail/list/cancel responses hide revoked report and calculation
      data. Report/evidence/memory/chart reads share discover + query checks.
- [x] Explanations check all quoted analyses and fail closed for missing or foreign
      sources; revoked evidence is excluded from model history and context.
      Both conversation and legacy-planner memory retrieval check current source
      dataset grants and exclude missing provenance.
- [x] Explicit UI values and clears override model scope proposals.
- [x] Session row locking, identity refresh and a shared active-turn/analysis
      check cover legacy submission, turns, patch and chart drilldown. Unit tests
      cover both directions and stale versions; the live suites and the full
      matrix pass with the locks in place. No concurrent load test was run -
      the busy path is covered by tests, not by contention measurement.
- [x] Invalid numeric/complex YAML mapping keys return `SKILL_INVALID`; attack
      corpus cases cover top-level and nested mappings.
- [x] Manual personal skill selection and exact session pins are available;
      private/archived versions and unwanted implicit upgrades are covered.
- [x] Scope draft synchronization, filter typing and personal skill binding have
      isolated browser regression coverage (15 passed), and the live-stack browser
      journeys (7 passed), the A09 harness (7/7 - 3/3 - 10/10) and the real-model
      multi-turn conversation were re-run after these fixes.
- [x] The affected integration suites were re-run against live services:
      **26 passed**, and the full matrix is **98 passed / 1 skipped / 25
      deselected**. Two defects found during that re-run were fixed: chart
      drilldown now validates the dimension before session state (it answered 409
      instead of 422 when the session was busy), and the A09 harness now matches
      the target cell by dimension name instead of by key order (the platform
      never pins that order; four cases scored as misses although the target cell
      was ranked 1st). See `docs/acceptance.md`, live re-verification section.

Executed commands and final results are recorded in `docs/acceptance.md`.

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

### M5 real-model evidence (executed 2026-09-27)

```powershell
# static suites (unit + security + contract), after the json_object adapter change
uv run --project backend --frozen pytest backend/tests/unit backend/tests/security backend/tests/contract -q
# -> 248 passed

# A09 harness against the deployed full profile with a real model (DeepSeek)
uv run --project backend --frozen python scripts/eval_agent.py --cases 10 --scale small --password <admin pw>
# -> a09_status=passed; target_top3=7/7; unforced=3/3; scored=10/10
#    model deepseek-flash (requested deepseek-chat); 2,169 in / 140 out tokens;
#    34 gateway queries; 2.19-5.98 s per case; 9 COMPLETED + incomplete_day PARTIAL
#    runtime/eval/a09-20260927-224114.json
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

## M5 - governed analysis loop (loop closed on 2026-09-19; real-model A09 passed 2026-09-27)

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
- [x] A09 result: **passed on a real model** (2026-09-27). Ten fixed cases through
      the deployed full profile against DeepSeek (`deepseek-chat` requested, served
      as `deepseek-flash`): `a09_status=passed`, target cell in the top-3
      contributors 7/7 target cases, no forced attribution 3/3 no-target cases,
      evidence-consistent 10/10, 2,169 input / 140 output tokens, 34 gateway
      queries, per-case latency 2.19-5.98 s, 9 COMPLETED + `incomplete_day` PARTIAL
      (`runtime/eval/a09-20260927-224114.json`). Reaching it needed one adapter
      fix: the JSON-schema response format is an OpenAI structured-outputs feature
      that DeepSeek and most OpenAI-compatible servers reject with HTTP 400, so the
      portable `json_object` mode is now the default (`AIND_LLM_RESPONSE_FORMAT`,
      the schema travels in the prompt and the reply is validated against it
      locally) and `json_schema` remains opt-in.
## M6 - charts, drilldown, administration screens and release acceptance (closed 2026-09-25)

- [x] Charts: `app/charts/spec.py` implements the controlled ChartSpec
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
- [x] Access-request workflow closed (2026-09-25): `POST /admin/permission-requests/{id}/approve`
      creates the real grant (with `discover` implied by `query`, policy revision
      bumped, audited as `permission_request.approve` + `grant.create`),
      `POST .../reject` declines without granting, `GET /permission-requests` lets a
      requester see only their own requests, and the new `APPROVED` state is a
      schema-level distinction from `MOCK_APPROVED` (migration
      `7c1d5a9e4b02`). The Permissions screen gained per-request
      approve/reject with an explicit role+action choice, and the catalog shows
      "我的申请" with its status.
- [x] Business memory (2026-09-28): every finished analysis contributes
      number-free, evidence-linked statements; planning reads them back as
      advisory context and an administrator confirms or rejects each one
      (`business_memory`, `app/agent/memory.py`, `GET /memory`,
      `GET /analyses/{id}/memory`). Live two-round check and the two findings it
      produced are recorded in `docs/acceptance.md`.
- [x] Superseded on 2026-10-16: scoped candidate recall now ranks local domain
      semantic vectors before the top-8 cut, with team/private isolation.
- [x] Bounded cross-author throughput/paraphrase acceptance now verifies claims,
      fencing and per-scope locks. Larger scale and overnight endurance still
      need multi-worker acceptance.
- [x] Curation audit detail (fixed 2026-09-29): `memory.confirm` / `memory.reject`
      rows used to log `previous` as the *new* status, because `_curate` read
      `row.status` after `set_status` had mutated it in place; the route now
      captures the previous status first and a unit test pins it
      (`test_curation_audit_records_the_previous_status`). Decided there too:
      statements that name the analysis window are refused as
      `unverifiable_figure` because the date is not in `evidence_identifiers` -
      the window is deliberately not quotable.
- [x] The spec's `/datasets/:id` route now opens the governed catalog detail
      directly, including schema, semantic context, lineage and access requests.
      See the 2026-10-16 dataset deep-link record. Real-model A09 passed on
      2026-09-27 (see the M5 section).

---

## Future phases (specification sections 31-32)

Spark Thrift Server now has a preview provider and HMS recipe; its connector
path, cancellation and the 120-second timeout paths were accepted live on
2026-09-28/29 (`docs/acceptance.md`). What remains open is the Spark listener's
job lineage, read-only source authorization and browser E2E (checklist below).
Not implemented and never represented as delivered: a Kyuubi provider or
separate HiveServer2 query provider, Flink streaming jobs, MCP egress, and the
enterprise semantics layer (identity mapping, HA, object storage, SLOs). The
preconditions each phase will need were recorded while the delivered milestones
were built; this section exists so the front page does not have to carry a
roadmap table.

### Spark/Hive shared Metastore preview (2026-09-28)

- [x] Add Spark source registration, dialect/resolution, worker-only Thrift
      execution, HMS ingestion recipe, single Hive URN mapping and UI fields.
- [x] Initial parameter rejection superseded by exact-round-trip AST scalar
      binding in the operational round; apply
      existing dataset grants and SQL denylist to Spark statements.
- [x] Real Spark Thrift Server + Hive Metastore + pinned DataHub ingestion cycle
      (2026-09-28, fixture in `infra/spark-preview/`): connection, catalog
      refresh of a table created through the Hive CLI, grants, SELECT, the
      stateless rejections, `mapped 3/3` Hive URN mapping, DataHub search and
      the platform's lineage endpoint. Recorded in `docs/acceptance.md`.
- [x] Cancellation and the 120-second ceiling (fixed and re-verified 2026-09-29):
      cancel now ends `CANCELLED`/`QUERY_CANCELLED` (terminal 2.2 s after the
      cancel) and the ceiling ends `TIMED_OUT`/`QUERY_TIMEOUT` at 120.6 s. The
      provider translates the two operation states Impyla cannot name
      (`SparkOperationCancelled`, and `SparkOperationTimedOut` for the server's
      `TIMEDOUT_STATE` 8), the executor lets a monitor cancel decide the outcome
      after a generic engine error, and a terminal publish from
      `CANCEL_REQUESTED` resolves to `CANCELLED` instead of stranding the job to
      `LOST`. Regression tests in the unit and integration suites; evidence in
      `docs/acceptance.md`.
- [x] Server-enforced query timeout, measured without the platform
      (`runtime/accept_spark_server_timeout.py`): the Thrift Server aborted an
      8M-row self-join at 120.0 s. Note for operators: the 2M-row fixture query
      finishes in ~118 s, so only `demo.slow_events_big` proves anything.
- [ ] Read-only source authorization: the fixture is NOSASL with no authorizer,
      so it was not exercised. Deploy a read-only source identity before use.
- [ ] Verify the DataHub Spark listener produces visible table lineage with the
      exact HMS platform instance and environment, then add browser E2E coverage.

---

## Conversational Ask and skills (design 2026-10-13)

Source: `docs/superpowers/specs/2026-10-13-askdata-conversation-skills-design.md`.
P1-P5 are ordered; P3/P4/P5 unfinished means the requirement is **not delivered**,
however complete an earlier phase looks.

### P1 - persistent conversation foundation

- [x] Migration `c4d9a17e6f20`: `agent_sessions` scope/draft/skill binding/
      `context_version`/archival; `conversation_turns` with the partial unique
      index that enforces one active turn per session; `agent_messages`
      `sequence`/`turn_id`/`content_schema_version` with a `(created_at, id)`
      backfill; `analysis_tasks.conversation_turn_id`; `task_queue.kind` accepts
      `conversation`. `verify_schema.py` updated (29 tables, 8 checks, 22 indexes).
- [x] Turn state machine (`ConversationTurnStatus` + explicit transition table,
      `WAITING_INPUT` terminal for the turn) and the repository that owns it.
- [x] Session API: list (archived filter + cursor), create, detail, PATCH
      (rename/archive/skill config, `expected_context_version`), messages with
      stable `sequence` order and the legacy content reader.
- [x] Turn API: `POST /sessions/{id}/turns` (required `Idempotency-Key`, request
      hash, 409 `IDEMPOTENCY_CONFLICT`, 409 `SESSION_BUSY`, 409
      `CONTEXT_VERSION_CONFLICT`, parent must be same user *and* same session),
      turn detail, idempotent cancel that also cancels the linked analysis.
- [x] Legacy entries take part in the same busy protection (`POST /analyses`).
- [x] Rule-only orchestrator + worker wiring: claim -> RESOLVING -> ANALYZING
      (one analysis, linked) -> reconciler closes the turn when the analysis is
      terminal; clarify/help replies end `WAITING_INPUT`/`COMPLETED`; the runner
      no longer writes its own assistant pointer for a turn-owned analysis.
- [x] Feature flag `AIND_CONVERSATIONAL_ASK_ENABLED` (default off) gates the
      turn write API.
- [x] P2-P5 delivered in their own sections below (P2 skill packages and
      versions; P3 model-backed intent parsing, scope resolution, explanations,
      shared budget, session SSE; P4 the `/ask/:sessionId` + `/skills` UI; P5 the
      acceptance matrix and the real-model multi-turn run). The P1 slice itself
      covered C01/C02/C06/C08-turn and the legacy-reader half of R01.

### P2 - skill packages and versions

- [x] `backend/app/skills/`: `SKILL.md` and ZIP parsing with every limit from
      design 7.3 enforced while reading (2 MiB upload, 4 MiB expanded, 32 files,
      256 KiB per file, 64 KiB SKILL.md, path length/depth, 100:1 compression
      ratio); path guards (absolute/drive/UNC/`..`/NUL/ADS/reserved names/
      trailing dot/symlink/case-folded duplicates/nested archives); only
      `SKILL.md` plus `references/*.md|.txt` accepted, anything else rejects the
      whole package; deterministic content hash (BOM stripped, LF normalized,
      sorted paths).
- [x] Strict front matter: closed top-level fields, safe YAML (duplicate keys,
      aliases, custom tags and deep nesting rejected), `metadata.askdata` closed
      schema with a version check, `required_inputs` checked against what the
      platform resolves, references must exist in the package, non-empty body,
      no scriptable markup or unsafe URLs.
- [x] Tables `skills` / `skill_versions` (migration `b7e2f40c91a8`): immutable
      versions, `(skill_id, version)` and `(skill_id, content_hash)` unique,
      owner/source consistency CHECK, builtin slug partial unique index;
      `verify_schema.py` now expects 31 tables.
- [x] API: `GET /skills`, `POST /skills/import-preview`, `POST /skills`,
      `POST /skills/{id}/versions`, `GET /skills/{id}`,
      `GET /skills/{id}/versions/{id}`, `PATCH /skills/{id}` - capability
      `analysis.create`, owner-scoped, invisible ids answer 404, every action
      audited. Preview never persists; the raw archive is never written.
- [x] Applicability recorded per version: unknown metrics and non-allowlisted
      dimensions mark the version "not applicable in this environment" without
      extending the metric registry, and never block saving. The API returns
      the *current* verdict against the live registry (`compatibility`) next
      to the import-time one (`compatibility_at_import`), so a skill imported
      before its metric existed becomes applicable once the metric loads.
- [x] Builtins: `metadata/skills/*.md` (revenue-change-reading,
      channel-contribution, data-completeness-check) plus
      `python -m app.cli load-skills` and a bootstrap hook; a builtin slug cannot
      be shadowed by an upload.
- [x] UI: `/skills` page (file picker -> preview -> save, version list, enable /
      disable / archive, builtin read-only) behind `PermissionGate`, with the
      nav entry, regenerated `openapi.json`/types and `data-testid`s.
- [x] Session-side pool and layered loading are delivered in P3 below.
- [x] Per-user (50) and per-skill (20) quota boundaries have dedicated live-DB
      tests, including rejection and hash replay at the limit.

### P3 - conversation orchestration

- [x] Model-backed intent (`app/agent/conversation/intent.py`): a closed decision
      (`clarify / analyze / explain / help / unsupported`) plus a *patch*; the
      server validates every field (unknown intent/extra fields rejected, metric
      must be registered, dimensions filtered by the metric allowlist, dates ISO
      and in range) and merges it (`set`/`clear`, missing = inherit).
- [x] Relative periods frozen server-side: `comparison=previous_period` with only
      a current period derives the baseline at receive time and records the
      interpretation; explicit dates are never overwritten.
- [x] Free prose guard: explanation text and reference notes may not carry a
      figure the evidence does not already contain (the business-memory numeric
      guard, reused).
- [x] Explanations from stored evidence (`explain.py`): references resolved by
      analysis/claim id with ownership *and* current discover/query checks, quotes
      copied verbatim from the report, a clear degraded state when a reference is
      revoked - and no new query.
- [x] Skill loading (`skills.py`): session pool (builtins + explicitly added
      versions, cap 10), modes off/manual/auto (manual <= 3, auto selects from the
      candidate ids the platform offered), layered loading (candidates first, then
      body + declared references), 24 KiB per turn, and an honest record of
      candidate/selected/loaded/skipped.
- [x] Turn budget (`budget.py`): persisted counters plus an absolute UTC
      `deadline_at`; a worst-case reservation is written before the model call and
      released with the actuals, so a crash cannot grant a free retry; the
      analysis receives the *remainder* as its sub-budget (the runner now honours
      persisted limits).
- [x] Lifecycle split into three phases: prepare (short transaction) -> decide
      (**no transaction** - a model call must never hold the turn row lock, or the
      cancel route would block behind it) -> apply (short transaction, late-reply
      guard re-reads the turn and a cancelled turn publishes nothing).
- [x] Session SSE (`GET /sessions/{id}/events`) over persisted events with
      `Last-Event-ID` resume; the client merges by event id.
- [x] Worker wiring: `run_turn` claims a turn, and `reconcile_turn` closes it once
      its analysis settles; `requeue_stale_turns` recovers a lost lease.
- [x] Auto candidates rank by question/description lexical similarity (English
      words and Chinese bigrams), metric and dimension relevance, within the
      existing pinned-before-builtin tiers. Selected versions are revalidated
      against final scope; conflicting metric requirements or missing required
      inputs produce clarification without overwriting explicit UI values.
- [x] Superseded on 2026-10-16: explain templates copy bound artifact values;
      session SSE contracts and empty-idle closure were already delivered.

### P4 - the conversational UI

- [x] Routes `/ask` (new conversation) and `/ask/:sessionId` (persistent); a
      conversation is created on demand and the URL carries the session, so a
      refresh restores the same transcript and the active turn.
- [x] Three-column workspace: session rail (newest first, active marker), the
      message thread with the composer, and a collapsible scope + skill panel;
      narrow screens collapse to one column.
- [x] Message rendering by `content.kind`: user, clarify (with the missing
      fields), explanation (verbatim evidence quotes + an explicit unavailable
      state), analysis result (claims, limitations, chart, deep link, skills
      used), help/unsupported, failure; legacy `content_schema_version = 0`
      messages are labelled as history.
- [x] Live progress comes from the active turn and its analysis (status + steps),
      never as extra chat messages; one active turn per session disables the
      composer and offers "停止本轮".
- [x] Scope panel prefilled from `resolved_context` with per-field origin tags;
      only changed fields are submitted as a patch, an emptied filter sends
      `clear`, and the completeness selector states the three-state value.
- [x] Skill picker: session mode (auto/manual/off) plus a manual selection of at
      most three versions; per-message "技能：…" usage line.
- [x] SSE (`/sessions/{id}/events`) drives refetches; the session and transcript
      also poll (fast while a turn is active) so a dropped stream degrades to
      polling instead of a frozen composer.
- [x] Flag-off fallback: when the turn API answers 503 CONVERSATIONAL_DISABLED
      the same question goes through the legacy one-shot analysis path.
- [x] Playwright journey (`frontend/tests/e2e/conversation.spec.ts`) plus a
      rewritten analysis journey: the full suite is **7 passed** against the
      deployed stack.
- [x] Narrow-screen scope drawer uses a native modal dialog with focus/Escape
      handling; drafts survive closing/resizing. Failed/cancelled turns can be
      retried with frozen original scope/skills and an idempotent new turn.
- [x] Transcript loads the latest 40 messages and pages backward with signed
      sequence cursors; history beyond 100 messages is reachable. Per-message
      analysis diagnostics display tokens, queries, tools, repairs and deadline.

### P5 - regression and acceptance

- [x] C07 recovery: crash after prepare -> `requeue_stale_turns` -> drain ends
      COMPLETED with exactly one analysis, one assistant message, one model call
      and no reset budget; re-running the same turn is a no-op
      (`backend/tests/integration/test_conversation_recovery.py`).
- [x] C10 revocation: revoking every query grant the analysis read redacts the
      transcript message (`permission_revoked`, blanked claims/quotes) and the
      report/evidence endpoints answer 403; the SSE stream emits
      `analysis.permission.revoked` (the assertion caught a real defect: the
      revocation branch referenced an unbound name, so every session stream died
      with `NameError` on its first poll - fixed and re-verified). S03-S05 were
      already covered by
      `test_skills_api.py` (cross-user) and `tests/security/test_skill_attack_corpus.py`.
- [x] A09 regression with the conversational layer deployed: `a09_status=passed`,
      target-in-top3 7/7, no forced attribution 3/3, fully scored 10/10, model
      `deepseek-flash`, prompt `m5-v1` (`runtime/eval/a09-20261013-215859.json`).
- [x] Real-model multi-turn conversation (`runtime/accept_conversation.py`,
      record `runtime/acceptance/conversation-20261013-220711.json`): scoped
      analyze (target rank 1, evidence-scored), follow-up inheriting the windows,
      explain without a new query, honest two-step clarification whose draft
      persists in `pending_clarification`, and a draft-completion turn that merges
      the draft into a completed analysis. Skill hashes recorded per turn; four
      consecutive runs replayed the same uploaded skill id/version (S02).
- [x] Static suite **364 passed**; full matrix **98 passed, 1 skipped, 25
      deselected** (DataHub stack not started; that subset is not counted as
      verified here). Details in `docs/acceptance.md`, P5 section.
- [ ] Not done in P5: the DataHub-dependent integration subset was not re-run
      (no code in this change set touches `app/metadata`); a real-model run of the
      *auto* skill mode's model choice under a large candidate pool is not
      recorded (the pool is at most three builtins today); no load/soak run of
      concurrent sessions beyond the single-active-turn constraint.


## 2026-10-15 — Chinese/English interface localization

- [x] Fast-forward local work to `origin/main` at `00bee00` before implementing.
- [x] Central Chinese/English catalog and live language selection on login,
      unauthenticated gate and the workspace header; browser detection, persistent
      preference, cross-tab synchronization and storage-disabled fallback.
- [x] Login, Ask and conversation/scope/skill panels, SQL, catalog and lineage
      labels, query history, analysis controls, business memory, skills, datasource
      administration and permissions administration use localized interface text.
- [x] Status labels retain protocol values; date/chart formatting follows locale;
      errors offer localized guidance plus inspectable original diagnostics.
- [x] Browser regressions cover both languages on all routes, preference
      persistence, input retention, auth/query errors, cross-tab updates and
      the 390px layout. Observed: **42 passed** (29 existing + 13 localization).
- [x] Unit checks cover catalog/placeholder parity, missing UI keys, all backend
      error codes and interpolation preserving user values. Observed: **11
      passed**; typecheck and production build also passed.
- [ ] Automatic translation of metadata, user/model prose and existing report or
      evidence quotations is outside this interface change; they retain source text.
- [ ] Live-stack database/model journeys: not executed for this frontend change.
      Mocked-browser checks do not count as live backend or real-model acceptance.

## 2026-10-16 Ask 自然语言可用性（executed）

Reported from the running UI: the Ask surface was unusable in natural language.
Three defects, all fixed on the working copy; evidence in `docs/acceptance.md`,
"2026-10-16 Ask 自然语言可用性".

- [x] `/ask` with no session can send: 发送 opens the conversation the question
      belongs to (`POST /sessions` + `POST /sessions/{id}/turns`) instead of
      staying permanently disabled without explanation.
- [x] A conversational turn no longer answers with an empty bubble:
      `report.py` takes the three-state `completeness` (design 6.1) and still
      publishes the observed change when the window is merely unverified, only
      withholding attribution; `incomplete` keeps withholding the change.
- [x] "有哪些指标 / ADS 层都有哪些数据" is answered from the metric registry
      (help/unsupported replies carry key, name, unit, dimensions, datasets).
- [x] An empty or inverted period (the model reading the dates as inclusive,
      `end == start`) now asks with the half-open convention instead of running an
      empty query and publishing `变化 0`; the prompt states the convention and the
      scope panel shows it.
- [x] A result with no claims renders its limitations instead of nothing.
- [x] Pre-existing layout defect closed, found by the required width check on this
      page: the fixed 320px side column was widened to 416px by the 指标 `<select>`'s
      widest option, overflowing the document by 29px at 1280px and 46px at 390px.
      Fixed with `min-width: 0` on the column and its children plus
      `grid-template-columns: minmax(0, 1fr)` on the panel `.stack`; overflow is now
      0 at 1440 / 1280 / 1024 / 768 / 390px with no offending element.
- [x] Tests: static **488 passed**, `ask.spec.ts` **22 passed** (three new cases;
      the full mocked suite reports 51 passed / 1 failed, the failure being the
      in-flight `catalog.spec.ts` case for the peer change in this tree — it fails
      with and without this change's CSS and passes when run alone), `npm run
      typecheck` exit 0, two live status probes against the deployed stack
      (`runtime/verify_ask_nl.py`, `runtime/verify_empty_period.py`) and a live
      click-through of the reported path on the deployed UI (typed question → 发送
      enabled → conversation opened → registry answer rendered), at
      1440/1280/1024/768/390px with a clean console.
- [ ] Not verified: the service integration matrix, A09 and the deployed
      Playwright journeys were not re-run. The matrix was skipped on purpose —
      its `clean_db` fixture wipes the control DB, which currently holds the live
      conversation history — so `test_conversation_flow.py`,
      `test_conversation_intent_flow.py`, `test_conversation_recovery.py` and
      `test_analysis_flow.py` are read-but-not-executed against these changes;
      every one of them drives `data_complete(_state) = complete` (or asserts the
      claim-carrying shape those paths keep), so the completeness change does not
      reach them, but that is reasoning, not a run.
- [x] Empty-period reporting fixed on 2026-10-16: source-row evidence now
      distinguishes missing data from genuine zero. Either period lacking rows
      in any required source yields PARTIAL with a dated limitation and no change
      or attribution. See the source-coverage record below for verification limits.
- [x] The completeness trade is now stated in the UI: with `unknown` by default a
      conversational answer reports the change but never attributes it, so the
      scope panel says so next to the 完整性 control ("未声明数据完整时只报告观测到的
      变化，不做归因。" / "数据完整：可以给出变化归因。").

## 2026-10-16 Dataset deep links

- [x] `/datasets/:id` opens schema, semantic context, upstream lineage and the
      access-request workflow directly. Catalog entries are real links; reload,
      browser back/forward and returning to the catalog preserve URL semantics.
- [x] Dataset-specific request drafts and notices reset on ID changes; requests
      target the current dataset. Missing/denied context renders an error, hides
      the form and prevents secondary schema/lineage reads. Read failures are
      shown rather than leaving permanent loading text.
- [x] Five new mocked catalog browser cases passed. Frontend typecheck and
      production build exit 0; frontend unit checks: **11 passed**.
- [x] Full mocked browser suite now passes: **108 passed** (54 cases repeated
      twice). Keyboard activation targets the new-version button directly,
      atomically restoring focus after preceding browser interactions.
- [~] Local live-service journeys and PostgreSQL/MySQL matrix rerun in the
      priority-workflows record below; real-model A09 remains pending.

## 2026-10-16 Empty-period source coverage

- [x] Every compiled metric query carries a collision-safe `COUNT(*)` evidence
      column under the same period and filters; no additional query is submitted.
- [x] The observer records per-period/per-source row evidence and its query IDs.
      Missing required input suppresses comparison, contribution, driver extension
      and charts; an explicit completeness flag cannot override missing rows.
      Rows whose metric is truly zero still produce the observed zero change.
- [x] Empty nonadditive periods and missing composition inputs produce honest
      PARTIAL reports rather than invented zeros; populated undefined formulas
      retain their existing failure behavior.
- [x] Coverage is retained in the period-comparison artifact (calculation version
      3). Older in-flight queries/checkpoints without coverage require resubmission.
      Completed historical reports are not rewritten.
- [x] Backend unit/security/contract suite: **509 passed**, including **30** new
      coverage regressions with SQLite aggregate execution and runner/report
      checks. `git diff --check` exits 0.
- [~] PostgreSQL/MySQL matrix and local live-service Ask journeys rerun in the
      priority-workflows record below. Doris, Spark and real-model A09 were not
      rerun. Row presence does not verify date-by-date data completeness.

## 2026-10-16 High/medium priority workflows

- [x] Analysis planning: short prepare/reserve transaction, session-free model
      request and fenced apply. Cancellation remains responsive during a model
      stall; reclaimed workers retain prior budget charges and reject old replies.
- [x] Business-memory extraction: bounded claim/expiry and at most three attempts;
      extraction and catch-up no longer hold task locks across network requests.
- [x] Skills description ranking and required-input reconciliation, including
      explicit clears and incompatible metric requirements. Current applicability
      and offered IDs are checked again before applying model decisions.
- [x] Backward transcript pagination (latest 40, signed sequence cursors), retry
      of failed/cancelled turns, native mobile scope drawer and message diagnostics.
- [x] Skills file chooser keyboard regression stabilized without sleeps or reduced
      assertions. Mocked browser regression: **108 passed**, 54 cases repeated twice.
- [x] Frontend unit tests **11 passed**, production build exit 0; backend static
      suite **515 passed**. Real protocol stalls/TLS focused suite **3 passed**.
- [x] Model adapter accepts redacted `AIND_LLM_API_KEY` cloud proxy credentials
      while retaining mounted-file compatibility. Credential precedence, redaction,
      request isolation and endpoint-reported model identity are unit-tested.
- [x] Local live-service browser journeys: **7 passed**, deterministic provider.
- [~] Real PostgreSQL/MySQL integration matrix: **97 passed / 14 skipped /
      25 deselected**, including socket/TLS cases. Thirteen skips need Doris and
      one needs deployed DataHub; Spark was not rerun.
- [ ] Requested DeepSeek v4.1 flash integration and real-model A09 were explicitly
      deferred by the user until later. No usable credential is bound and current proxy policy excludes `api.deepseek.com`.
      A minimal environment draft now declares that destination and a
      `DEEPSEEK_API_KEY` -> `AIND_LLM_API_KEY` secret binding. Saving the draft did
      not apply/publish it. The configured `deepseek-chat` alias has not been
      verified as the requested model version. Historical A09 passes above do not
      count as this revision's acceptance.

## 2026-10-16 Account settings, session-list pagination and Spark RPC isolation

- [x] Migration `d2e6a9f18c40` adds per-user display name, nullable locale/timezone
      and settings version. Closed PATCH `/auth/me` validates the fields, requires
      CSRF and an expected version, locks the user, and audits changed field names.
- [x] `/settings` plus `/profile` redirect, account entry, draft/error/reload states,
      account-owned header language and formatted timestamp timezone. Read-only
      roles/capabilities. Password change was delivered in the following round;
      default analysis preferences remain an optional, unconfirmed extension.
- [x] Account preferences do not write the guest's browser language key; 401/logout
      reset locale/timezone. Other workspace tabs reload on login/logout so caches
      and preferences from an earlier identity are discarded.
- [x] Session rail follows next_cursor, deduplicates IDs and makes older sessions
      reachable. 45-session backend/browser cases pass; backend ordering and
      offset cursor contract are unchanged.
- [x] Real Spark cancellation race reproduced before the fix (status RPC
      `KeyError(None)`) and passes after it with a separate control transport.
      Server cancellation and subsequent query success are asserted.
- [x] Backend static **517 passed**; current service matrix **111 passed /
      14 skipped / 25 deselected**; frontend units **12 passed**; production build
      exit 0; live browser suite **8 passed**, followed by **1 passed** rerun of
      the settings journey after cross-tab authentication handling was added.
- [~] Full Doris/DataHub acceptance is still not executed. The new standalone
      Spark NOSASL service does not validate read-only authorization, shared HMS
      or listener lineage. Real-model validation remains deferred by the user.

Final mocked-browser suite: **58 passed** (2.1m). Earlier race failures are
superseded; commands and details are in the acceptance record.

## 2026-10-16 Continued TODO: workspace management and security

- [x] Conversation rename, archive/restore and archived-session browsing; errors
      preserve the edit, archived transcripts preserve the unsent question.
- [x] Saved SQL save/list/load/delete UI, pagination and unavailable-source handling.
      Loading never executes; query access still requires the gateway checks.
- [x] Self-service password form and closed CSRF-protected endpoint. Current
      password verified, bounded failures audited, all sessions revoked atomically.
      Login holds the same user lock so old-password races cannot escape revocation.
- [x] Skills limits tested at 50 owned skills / 20 versions, including rejection
      and idempotent hash replay at capacity (HTTP 413 SKILL_TOO_LARGE).
- [x] Dedicated session SSE framing/resume/ownership/idle contracts. Empty idle
      streams now close immediately even without any previous event.
- [x] Static **520 passed**, frontend units **12 passed**, production build exit 0
      and live browser journeys **9 passed**. Mocked browsers **62 passed**, followed
      by **4 passed** for the final template-loading guard. Final serial matrix
      service matrix: **120 passed / 14 skipped / 28 deselected**, serial run.
      Shared-DB runs and the failed test synchronization probe are not counted.

## 2026-10-16 Remaining feature implementation

- [x] Explain templates render bound period-comparison values and contributor/
      driver interpretation; references check owner/session/grants/expiry. No new
      query or model arithmetic. Missing artifacts cannot reconstruct numbers.
- [x] Planning chooses compare/breakdown and allowlisted dimensions. A separate,
      transaction-free observed-evidence call chooses finish or one declared driver
      extension. Explicit user scope and deterministic eligibility/budget/depth
      gates remain binding. Late successes/errors after cancellation are fenced.
- [x] OpenAI-compatible closed output gets at most one format repair. Both usage
      totals and physical calls are counted; invalid output is not replayed as
      instructions. Planning reserves both attempts (bounded by remaining tokens)
      durably, and crashed requests retain their charge.
- [x] Domain semantic recall uses local bilingual concept vectors and cosine
      similarity with word/character fallback; source permission filtering occurs
      before ranking/top-8 selection. It is not a general pretrained encoder.
- [x] Migration e4b8a1c79d20 adds teams, user membership, memory team/owner fields;
      existing source-linked memories become private to their owner. At most one
      team per user, audited administrator create/assign UI/API, private fallback.
      List/count/curation/prompt/historical reads recheck scope and source grants.
      Team changes do not move old memories; extraction refuses changed scope and
      serializes duplicate checks with a PostgreSQL transaction lock.
- [x] Charts default to Top-10 absolute change, expand/collapse and load additional
      bounded pages; table and bars share rows and preserve exact values/counts.
      Datasource/result/message classes cleaned up. Team/private memory labels
      avoid linking another author's private analysis as if it were shared.
- [x] Static **531 passed**; frontend units **12 passed**; production build exit 0.
      Full service matrix **127 passed / 15 skipped / 28 deselected**, followed by
      the real standalone Spark cancellation test **1 passed**. Final affected
      PostgreSQL priority cases **15 passed**; schema verification exit 0:
      **32 tables / 11 named state checks / 25 required indexes**.
- [x] Mocked browser full attempt: **63 passed / 1 failed**; the new team test
      exposed a missing alert role on admin errors. Fixed; final chart/team
      regressions **8 passed**. Do not describe the earlier run as 64 passed.
- [~] Not executed: current real-model DeepSeek/A09 (user-deferred), complete
      Doris/DataHub, Spark read-only/HMS listener/browser acceptance, load/soak,
      general pretrained semantic evaluation and historical report reruns.

## 2026-10-16 Operational completion

- [x] Migration f6a12c83d095: account analysis defaults and auth_rate_limits.
      Defaults seed new conversations only; explicit scope wins and no date or
      completeness is assumed. UI/profile versioning and active metric validation.
- [x] Historical rerun UI/API: owner/current grants/CSRF/idempotency checked,
      current contracts, parent link and unknown completeness; original immutable.
- [x] Shared login/password failure windows, bounded allocation/reclamation,
      lock order before user locks, fresh-process and forwarded-header checks.
- [x] Persisted-event SSE across API instances with ongoing authentication,
      account-state and dataset-grant checks before replay/publishing.
- [x] Spark verified TLS, PLAIN-over-TLS configuration and exact-round-trip AST
      scalar binding. Real TLS/untrusted CA/parameters/cancellation verified;
      isolated Spark Query Gateway/browser journey passes.
- [x] Cross-author team memory race and bounded sustained load: 360 conversations,
      45 waves, 126.776 seconds; 360 extractions converge to one memory. No live
      queue/lease leftovers. Overnight/high-scale endurance remains unverified.
- [x] Static 550 passed; full service matrix 136 passed / 14 skipped /
      29 deselected; final operational subset 7 passed. Frontend units 12 passed,
      build exit 0, final mocked browsers 66 passed. Live dual-API deployment:
      10 browser journeys passed, followed by 1 added defaults/rerun journey.
      Deployed schema: 33 tables / 11 named checks / 26 required indexes.
- [~] Still external/resource-bound: 13 Doris + deployed DataHub test; GMS auth;
      Spark actual identity/read-only/shared-HMS listener lineage; 16 GiB proof;
      real model explicitly deferred. Further general embeddings, multi-host HA,
      batch/stream/MCP and content-language features remain roadmap extensions.
