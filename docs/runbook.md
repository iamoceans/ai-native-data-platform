# Runbook

Commands are given for both entry points:

- WSL2 / Linux: `make <target>`
- Windows PowerShell: `.\scripts\dev.ps1 <target>`

The Makefile and the PowerShell shim call the same scripts; exit codes are
preserved.

## 1. From a blank environment

```bash
cp .env.example .env          # or: make setup-secrets (generates random passwords)
make setup-secrets            # writes .env + infra/local-secrets/source-postgres.json
make doctor                   # host report; fix reported problems (Docker, ports, secrets)
make up-core                  # builds images and starts control/source PG + API + workers + UI
make migrate                  # apply control-DB migrations (idempotent)
make bootstrap                # roles, capacities, admin user (prints a generated password once)
```

Then open <http://127.0.0.1:3000>, sign in, and use the SQL workspace. To see a
fully populated example including a registered datasource, catalog refresh, role
grants and one real query:

```bash
AIND_SMOKE_IN_CLUSTER=1 AIND_SMOKE_PASSWORD=<admin password> \
  uv run --project backend --frozen python scripts/smoke_core.py --demo \
  --sql "SELECT country, COUNT(*) AS rows, SUM(revenue_usd) AS revenue FROM fixture_metrics GROUP BY country ORDER BY country"
```

The M4 demo dataset replaces the M1/M2 fixture seed for real work:

```bash
AIND_DATAHUB_ENABLED=1 make up-full     # core + MySQL + Doris (start the DataHub stack first)
make demo-generate                       # SEED=42 AS_OF=2026-09-13 SCALE=small SCENARIO=ecpm_drop
make demo-load                           # Stream Load into Doris + config tables + derived table
make demo-verify                         # offline checks against the generated ground truth
make seed-sources                        # no longer required; legacy M2 fixtures only
```

Then register the three sources and ingest metadata (see section 2.1).

## 2. Everyday operations

| Task | Command |
|---|---|
| Start / update the stack | `make up-core` |
| Stop containers, keep volumes | `make down` |
| Status | `make ps` |
| Logs | `make logs` |
| Apply migrations after an update | `make migrate` |
| Re-run bootstrap (idempotent) | `make bootstrap` |
| Export OpenAPI / regenerate frontend types | `make api-spec types` |
| Health | `curl http://127.0.0.1:8000/health/live`, `curl http://127.0.0.1:8000/api/v1/health/ready` |
| Regenerate the demo dataset | `make demo-generate` then `make demo-load --reset-demo` (or `make reset-demo CONFIRM=demo`) |
| Publish declared demo lineage | `make demo-lineage` (runs the DataHub SDK inside the ingestion image) |

Data is kept in named volumes (`control_pgdata`, `source_pgdata`, `results`,
`ingestion_work`, `source_mysqldata`, `doris_*`, `datahub_*`). `make down` never
deletes them.

### 2.1 DataHub stack and metadata pipeline (M3/M4)

Windows PowerShell needs `HOME` pointing at the user profile for the pinned
quickstart file to resolve its environment:

```powershell
$env:HOME=$env:USERPROFILE
docker compose --project-name datahub --env-file .env `
  -f infra/datahub/compose.pinned.yaml -f infra/datahub/compose.ainative.yaml `
  --profile quickstart up -d
```

Then, with `AIND_DATAHUB_ENABLED=1` set for the business stack:

```powershell
# register sources (idempotent), refresh catalogs, grant roles, queue ingestions
uv run --project backend --frozen python scripts/metadata_sync.py
uv run --project backend --frozen python scripts/verify_metadata.py
```

Facts to remember:

- GMS/UI/MySQL/OpenSearch/Kafka are loopback-published at 18080 / 9002 /
  13306 / 19200 / 19092; the quickstart runs with GMS authentication disabled
  (`scripts/datahub_token.py --check` documents this).
- The ingestion container mounts the same control DB and secret files as the
  backend; `make metadata-sync` only queues work.
- Verify the platform sees real lineage: `GET /datasets/{id}/lineage` should
  return `available` for `demo.ads_revenue_by_country` (view -> table, label
  `extracted`) and for `demo.revenue_daily_total` (declared demo pipeline).

### 2.2 Registering a datasource and granting access by hand

`scripts/metadata_sync.py` does this idempotently for the demo sources; the
manual path (spec sections 11 and 12) is, as an administrator:

1. put the source credentials in `infra/local-secrets/<name>.json`
   (`{"username": ..., "password": ...}`) - the file is mounted read-only and a
   password is never accepted as a request field;
2. `POST /api/v1/datasources` with
   `{name, kind: "postgres", connection_config, secret_ref}`;
3. `POST /api/v1/datasources/{id}/test` -> expect `HEALTHY`;
4. `POST /api/v1/admin/datasources/{id}/catalog-refresh` with `{"schemas": ["public"]}`
   (base tables with sensitive-looking columns are skipped, views require explicit
   confirmation);
5. `POST /api/v1/admin/grants` for the roles that should see the data.

`scripts/smoke_core.py --demo` automates all of the above and runs one real query.

## 3. Running the test suites

Unit / security / contract tests need no services:

```bash
uv run --project backend --frozen pytest tests/unit tests/security tests/contract -q
```

Integration tests need the control and source PostgreSQL reachable from the host.
**Stop the compose workers first** so the host-side test worker owns the queue:

```bash
docker compose stop query-worker agent-worker
make test-integration        # starts DBs with loopback ports, runs tests/integration + tests/contract
docker compose start query-worker agent-worker
```

`make test-core` runs all three suites in order.

The full matrix (`make test-full`) needs MySQL and Doris up. The M3 DataHub
integration tests additionally need the DataHub stack reachable and the
deployed platform API; the M4 SQL acceptance tests need the demo data loaded
(`make demo-load`). When a dependency is missing those tests **skip**, never
pass silently — the recorded numbers are in `docs/acceptance.md`.

Browser E2E (Playwright) needs the full core stack plus admin credentials:

```powershell
$env:E2E_BASE_URL="http://127.0.0.1:3000"; $env:E2E_ADMIN_PASSWORD="dev-admin-password-123"
cd frontend; npx playwright test
```

## 4. Recovery

| Situation | What happens / what to do |
|---|---|
| Worker killed mid-query | Lease expires -> reconciler marks it `SUSPECT`, then `LOST` after the source deadline + margin. The next worker cannot double-publish: stale publishes fail the fencing check. The source query self-terminates via `statement_timeout`. |
| API restarted | Sessions live in PostgreSQL; clients keep working. SSE reconnects with `Last-Event-ID`; if the cursor is older than retention the client gets `EVENT_CURSOR_EXPIRED` and should re-fetch the resource. |
| Control DB restored from backup | Queries in `RUNNING` with expired leases are reconciled to `LOST`; results whose files exist stay readable until `expires_at`. |
| Result file missing/corrupt | `GET /queries/{id}/results` returns `RESULT_UNAVAILABLE` (410). The platform never fabricates an empty result. |
| Datasource unreachable | Connection tests return sanitized errors; queued jobs fail with `DATASOURCE_UNAVAILABLE` (retryable) without crashing the worker loop. |
| Disk full while writing results | The atomic publish fails, the job fails, and no partial result is recorded. |
| Need a clean slate | `docker compose run --rm --no-deps backend alembic downgrade base && make migrate && make bootstrap` (destructive: deletes control state), or remove the volumes deliberately. |
| Reset demo data | `make reset-demo CONFIRM=demo` (drops/recreates the `demo` tables and truncates the demo-owned config tables, then reloads the newest generated run). Reloading a non-empty run without the flag is refused. |
| DataHub stack down (all quickstart containers exited) | Usually the WSL2 VM restarted under memory pressure. `docker compose --project-name datahub --env-file .env -f infra/datahub/compose.pinned.yaml -f infra/datahub/compose.ainative.yaml --profile quickstart up -d`, wait for GMS `healthy`, then re-run `scripts/metadata_sync.py` if mappings look stale. |
| Declared demo lineage missing after a reload | The publisher resolves URNs from the control DB; run `make metadata-sync` (queues ingestion) and then `make demo-lineage`, and allow a minute for the search index to catch up. |

## 5. Troubleshooting

- `docker compose up` fails with "network datahub-net declared as external":
  run `make network-up` (or `docker network create datahub-net`). The network is
  shared with the DataHub stack in M3.
- Port already in use: `scripts/doctor.py` lists the ports it checks. The dev
  override publishes control DB on 55430 and source DB on 55433 because 55432 was
  occupied on the reference host; change `CONTROL_DB_PORT`/`SOURCE_DB_PORT` in
  `.env` if needed.
- Login fails after running integration tests on a shared DB: the test suite
  truncates the control DB. Re-run `make bootstrap` to recreate roles/users and the
  capacity rows.
- npm scripts fail in PowerShell with an execution-policy error: run them via
  `cmd /c "npx ..."` (the dev shim does this) or set the policy for the current user.
- `make` is not available on the Windows host: use `.\scripts\dev.ps1`. The Makefile
  is the entry point under WSL2/Linux.
- Full stack restarts the WSL2 VM (Docker Desktop memory ceiling): raise the
  memory in `%UserProfile%\.wslconfig` or start DataHub and the business stack in
  layers, accepting `METADATA_UNAVAILABLE` degradation until GMS is back.
- `make demo-load` fails with `stream load ... failed: There is no 100-continue
  header` or an HTTP 307: the loader talks to the BE directly (host port 18040 in
  the dev override); check that `--doris-be-http-port` matches your `.env`.
- Result queries that decompose a full partition return fewer rows than expected:
  the platform paginates results (default page 100). Follow `next_cursor` or set a
  higher page `limit` (max 500 per request).

## Blank-volume start (A18, verified 2026-09-23)

The business stack reproduces from nothing with the documented commands:

```bash
docker compose -f compose.yaml --profile full down -v --remove-orphans
docker compose -f compose.yaml -f compose.dev.yaml --profile full up -d --build
docker compose -f compose.yaml run --rm --no-deps backend alembic upgrade head
docker compose -f compose.yaml run --rm --no-deps backend python -m app.cli bootstrap
make seed-sources          # PostgreSQL fixtures + MySQL (incl. m2_heavy) + Doris demo tables
AIND_SMOKE_IN_CLUSTER=1 make smoke-core ARGS="--demo"   # or scripts/smoke_core.py --demo
make demo-generate && make reset-demo CONFIRM=demo && make demo-verify
make verify-a12 && make verify-a15 && make eval-agent
```

`seed-sources` fills `demo.*` with the small M2 connector fixture; the M4 dataset
replaces it, which is why the first `demo-load` after seeding needs the explicit
reset. The DataHub stack is a separate compose project and is not part of this run.

### DataHub part of the flow (pinned v1.7.0.1)

```bash
# Git Bash on Windows: MSYS_NO_PATHCONV=1 for every command with a container path
export HOME=/c/Users/<you>            # the pinned compose file mounts $HOME/.datahub and $HOME/.aws
docker compose --project-name datahub --env-file .env   -f infra/datahub/compose.pinned.yaml -f infra/datahub/compose.ainative.yaml   --profile quickstart up -d
AIND_DATAHUB_ENABLED=1 docker compose --profile full up -d backend query-worker agent-worker ingestion
make metadata-sync                    # three-source ingestion + URN mapping
uv run --project backend --frozen python scripts/verify_metadata.py   # context/lineage/metrics
make demo-lineage DEMO_RUN=<run>      # declared lineage via the DataHub SDK
```

After `make demo-lineage`, give the DataHub search index a few minutes before
judging the lineage view (SDK-published edges took ~10 minutes here; the GraphQL
`searchAcrossLineage` query from the source side shows them earlier). Integration
tests re-point datasources at the host's loopback ports, so a live run needs
`ensure_datasource` (scripts/eval_agent.py) or the admin screen to put the
platform's own view back.

### Running the browser journeys repeatedly

`npx playwright test` signs in once per run and reuses the stored session
(`tests/e2e/.auth/admin.json`); the capability-gate journey additionally signs in
as its viewer. The local dev override raises the login rate limit to 50 attempts
per 5 minutes so back-to-back runs work; without the override the platform's
default of 5 applies and a third run inside the same window will be rate
limited. If the frontend answers 502 after rebuilding the backend, it is the
nginx upstream cache: the shipped config re-resolves via Docker DNS, so rebuild
the frontend image once to pick that up.

### After any integration run: `make align-sources`

The integration suite registers datasources with the host's loopback ports (it
runs the app in-process, where that is correct). The control database is shared
with the deployed stack, so the platform's own ingestion, analyses and the
browser journeys then cannot reach the sources until the connection config is put
back to the compose service names:

```bash
make align-sources          # re-points postgres/mysql/doris, refreshes catalogs, grants admin
```

This is also the state the E2E journeys and `make eval-agent` expect, so run it
after `make test-integration`/`make test-full` and before a demo or acceptance
session.

### Access requests: what the states mean

`REQUESTED` - filed by a user, nobody has decided yet; it grants nothing.
`APPROVED` - an administrator approved it *and* the real grant was created (the
policy revision is bumped, so live queries re-check); the requester can query.
`MOCK_APPROVED` - a demo label only: it never creates a grant, which is why the
Permissions screen prints "Mock 标记，未授权" next to it.
`REJECTED` - declined, no grant created.

Approving needs the role and the action (`discover`/`query`); granting `query`
implies `discover` (spec 12.1). The requester sees their own requests through
`GET /permission-requests`, never anybody else's and never the admin queue.
