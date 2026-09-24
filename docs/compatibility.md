# Compatibility and version freeze (M0-M3)

Status legend: **verified** = executed on the target machine and observed ·
**tag-verified** = the artifact exists upstream but was not pulled/run yet ·
**pending** = not yet checked.

## Host and toolchain (evidence: `scripts/doctor.py`, 2026-09-14)

| Item | Value | Status |
|---|---|---|
| Host OS | Windows 10 Pro (AMD64), Docker Desktop with WSL2 backend | verified |
| CPU | Intel Core Ultra 5 125H, 14 cores / 18 logical | verified |
| RAM | 31.5 GiB total (full-stack budget 32 GiB - borderline; core needs ~6-8 GiB) | verified |
| Disk | repo volume D: 225.6 GiB free; Docker Desktop data on C: (32.8 GiB free at M0) | verified |
| Docker | client 28.3.2, server 28.3.2, Compose v2.38.2-desktop.1 | verified |
| Python | 3.11.15 (uv-managed); system Python 3.13 is not used for the backend | verified |
| uv | 0.11.16 | verified |
| Node / npm | 25.1.0 / 11.6.2 (npm scripts must run via `cmd /c` on this host due to PowerShell execution policy) | verified |
| git | 2.51.2.windows.1 | verified |
| Local services present | PostgreSQL 17 on 5432, MySQL 5.7 on 3306, unrelated PostgreSQL on 55432 | verified (the project never uses them) |
| Full-stack feasibility | not attempted in M1; DataHub/Doris pulls pending | pending |

## Pinned artifacts

`infra/versions.env` is the manifest. No `latest`/`master`/`nightly` tags are used
anywhere in the project.

| Artifact | Pin | Digest / note | Status |
|---|---|---|---|
| PostgreSQL (control + source) | `postgres:16.15` | `sha256:f1c3376c26f2609ab9f29f71f824103fe2fcd8ee0346485cb6122a4f93df6f94` | verified (pulled, running) |
| MySQL source (M2) | `mysql:8.4.11` | `sha256:85b9bf2e29cf836ecb8c2a15a935d4ba0c606631dff1dd79531a11983c638f2a` | verified (pulled, running, seeded, queried, cancelled, timed out) |
| Doris FE (M2) | `apache/doris:fe-3.1.4` | `sha256:e86abeb405d5d56ee60da7c3e132f0dddfdbe195206284231e55805c7fc3cf86` | verified (pulled, running healthy, SQL protocol queried) |
| Doris BE (M2) | `apache/doris:be-3.1.4` | `sha256:21cd6cdf39dfab5c08f21cc695c2d7541325273ee61ec0a8ab108b553e26925e` | verified (pulled, running healthy, registered with FE, executes queries) |
| Python build image | `python:3.11.15-slim-bookworm` | `sha256:d29f48a31a8b408ed19272ca1e7b10ebae13b240a27e862d3d4217c528e2e0c3` | verified (pulled, image built) |
| Node build image | `node:24.21.0-alpine` | `sha256:be80f76cf40ec8e42b9bec49f60a55e0660f30af58d3e5a25530785b30ea67e2` | verified (pulled, image built) |
| nginx serve image | `nginx:1.29.8-alpine` | `sha256:5616878291a2eed594aee8db4dade5878cf7edcb475e59193904b198d9b830de` | verified (pulled, image built) |
| DataHub release (M3) | `v1.7.0.1` (released 2026-09-03) | official quickstart compose stored at `infra/datahub/compose.pinned.yaml`; internal pins from that file: `mysql:8.2`, `opensearchproject/opensearch:2.19.3`, `confluentinc/cp-kafka:8.2.2`; application images `acryldata/datahub-{gms,frontend-react,upgrade,actions}:v1.7.0.1` | verified (all images pulled, stack running; GMS + frontend healthy; three sources ingested; digests recorded below) |
| DataHub GMS | `acryldata/datahub-gms:v1.7.0.1` | `sha256:74e15e982b94d0e147de41d05133ef3df48f74a1004a9c997805e0aa7e010173` | verified (GraphQL search/read/lineage executed) |
| DataHub frontend | `acryldata/datahub-frontend-react:v1.7.0.1` | `sha256:99513cc1c45e4cc053ceb0f9aeb04a3263a8a747907bb0e85cac2eb03461310d` | verified (healthy; UI on 127.0.0.1:9002) |
| DataHub upgrade | `acryldata/datahub-upgrade:v1.7.0.1` | `sha256:c3db54d8fb94bc761be611c014c5ede3fd02daac1f15c8c177d69eceee79a24c` | verified (system-update job exited 0) |
| DataHub actions | `acryldata/datahub-actions:v1.7.0.1-slim` | `sha256:e55fbb8938b7b33791d554c3eb10c79fde0e88720d7a4d010f88bdd68b062bd3` | running (no actions configured) |
| DataHub internal MySQL | `mysql:8.2` | `sha256:212fe73edca5df6ff14826d5eb975c914bfb91f82a2e923f9050568f99525da1` | verified (healthy) |
| DataHub OpenSearch | `opensearchproject/opensearch:2.19.3` | `sha256:e96cc6ae1500a073d973c0906f30f7cf4d9c461f32f855f9242a2da933660cdd` | verified (healthy) |
| DataHub Kafka | `confluentinc/cp-kafka:8.2.2` | `sha256:8e01c0305844d6c05bfb8e86479f5f363bb6a53497625395943a9da780de67ce` | verified (healthy) |
| Platform ingestion image (M3) | `ainative-ingestion:0.1.0` = `acryldata/datahub-ingestion:v1.7.0.1` + repo runner | base `sha256:8845102fd495f2589e1ffc00dbd85cee3cbb700abc20d62fac1929ce95535620`, built image `sha256:062da4251e7be8f5b6cf2a3ff0f8f16680506d5d636b1f459a479fba636e8e33` | verified (contains SDK `1.7.0.1+docker`, psycopg2 2.9.12, and the postgres/mysql/doris sources in the CLI registry; executed real ingestions) |

DataHub source of truth for the compose file:
`https://raw.githubusercontent.com/datahub-project/datahub/v1.7.0.1/docker/quickstart/docker-compose.quickstart-profile.yml`
(stored verbatim; its `${DATAHUB_VERSION}` default is pinned by our environment).

## Python dependencies (uv.lock is the source of truth, 48 packages)

| Package | Pin | What was verified on this machine |
|---|---|---|
| fastapi | 0.141.1 | application runs, OpenAPI 3.1 document generated |
| uvicorn[standard] | 0.52.4 | API and worker processes run |
| pydantic / pydantic-settings | 2.13.5 / 2.15.0 | DTO validation, `extra=forbid` requests |
| SQLAlchemy / Alembic | 2.0.52 / 1.20.0 | migration generated, applied, schema verified (27 tables) |
| psycopg[binary] | 3.3.5 | live queries, server-side cursors, `cancel_safe`, read-only sessions, OID type registry (contract tests) |
| PyMySQL | 1.2.0 | live queries, server-side cursors, `KILL QUERY`, read-only sessions, temp-table-free writes rejected - all verified on MySQL 8.4.11 and Doris 3.1.4 (`pymysql.__version__` reports the client protocol string 2.2.8; the distribution version 1.2.0 is the pin) |
| sqlglot | 30.18.0 | postgres dialect parsing, `%(name)s` placeholder rendering, scope/CTE resolution, star expansion, join attributes (contract tests) |
| argon2-cffi | 25.1.0 | Argon2id hashes (`$argon2id$`) verified |
| pyarrow | 25.0.1 | Arrow IPC result files written in the acceptance runs |
| pytest / pytest-cov | 9.1.1 / 7.1.0 | static suites executed at every milestone; counts recorded in `docs/acceptance.md` |
| httpx | 0.28.1 | TestClient and smoke script |

## Frontend dependencies (package-lock.json is the source of truth, 65 packages)

| Package | Pin | Note |
|---|---|---|
| react / react-dom | 19.3.0 | verified by `tsc` + `vite build` + Playwright run |
| vite | 8.3.0 | production build verified (311 kB JS / 97 kB gzip) |
| typescript | **5.9.3** | pinned down from 7.0.2 because `openapi-typescript@7.13.0` declares a `typescript@^5.x` peer range; TS 5.9.3 satisfies both the generator and Vite |
| @tanstack/react-query | 5.102.8 | data layer of the UI |
| react-router-dom | 7.18.3 | routing verified in Playwright |
| openapi-typescript | 7.13.0 | generated `src/api/schema.d.ts` from the exported OpenAPI document |
| @playwright/test | 1.63.0 | chromium downloaded, 2 E2E tests passed against the Docker stack |
| npm audit | - | 0 vulnerabilities reported at install time |

## Engine-behaviour findings (observed, used by the implementation)

1. **psycopg connect option**: `read_only` is not a libpq option; use the
   `Connection.read_only` property. The provider sets it before any statement and a
   contract test pins the API.
2. **SET with parameters**: `SET statement_timeout = %s` is rejected by PostgreSQL
   (utility statements cannot be parameterized). The provider inlines validated
   integers.
3. **Percent literals**: psycopg and PyMySQL both scan the SQL text for placeholders
   whenever parameters are bound; a literal `%` inside a string literal must be
   written `%%` in that mode. The provider layer escapes literals only when the
   statement binds parameters (`app/providers/sql_prep.py`), and the canonical SQL
   keeps single `%` for evidence.
4. **Placeholders**: SQLGlot's postgres generator renders `:name` as `%(name)s`
   (psycopg's style); for MySQL/Doris the canonical form keeps `:name` and the
   provider renders pyformat `%(name)s` through a dialect subclass generator. The
   MySQL/Doris tokenizers cannot re-parse `%(name)s`, which is why the conversion
   happens after validation and the stored `validated_sql` stays canonical.
5. **Read-only enforcement on PostgreSQL** works at two layers: session/transaction
   read-only (`SQLSTATE 25006`) and SELECT-only role grants (`SQLSTATE 42501`).
6. **MySQL 8.4.11** (observed): `SET SESSION TRANSACTION READ ONLY` makes writes fail
   with **1792** (read-only transaction); with a SELECT-only grant, `INSERT`/`UPDATE`
   fail with **1142** (command denied). `SET SESSION MAX_EXECUTION_TIME=<ms>` stops a
   runaway SELECT with **3024** ("maximum statement execution time exceeded"), and
   `KILL QUERY <connection_id>` interrupts it with **1317** ("Query execution was
   interrupted"). All four paths are covered by integration tests.
7. **Doris 3.1.4** (observed): FE reports `SELECT VERSION()` as `5.7.99` (MySQL
   protocol version); `SHOW BACKENDS` shows the build `doris-3.1.4-rc02-7f5ba43de6`
   (image labelling by upstream). `query_timeout` exists with default 900 s and is set
   per execution; exceeding it returns errno **1105** with
   "get result timeout"; `KILL QUERY <connection_id>` returns errno **1105** with
   "cancel query by user", so Doris error classification is message-based. Doris
   declares `SELECT_PRIV`-only accounts; `INSERT`/`CREATE` fail with "denied".
   `LARGEINT` beyond int64 and `DECIMAL` arrive as Python `str`/`Decimal` and stay
   strings in API responses. Doris has no PostgreSQL-style read-only transaction
   (`transactional_read_only=false` in capabilities).
8. **Local port conflicts**: the control DB is published on **55430** and the source
   DB on **55433** in the dev override because 55432 was occupied on this host; MySQL
   is published on 33060, Doris FE SQL on 19030 (HTTP 18030), Doris BE HTTP 18040.
   The Doris network uses `172.29.0.0/24` because `172.28.0.0/16` was taken by an
   unrelated Docker network on this machine.
9. **Doris on Docker Desktop/WSL2** needed no extra host tuning for this local
   single-BE profile (no `vm.max_map_count` change was required in practice); the
   fixed-IP `doris-net` and the official `FE_SERVERS`/`BE_ADDR` pattern from the
   Apache Doris docker-compose demo were used. This remains a Linux-specific area -
   re-verify if the BE is given more tablets or runs on a different host kernel.
10. **TypeScript 7.0.2 is current on npm**, but `openapi-typescript@7.13.0` declares
    `typescript@^5.x`; the frontend pins TypeScript 5.9.3 so frontend types can be
    generated from OpenAPI.

## DataHub findings (M3, observed on the running v1.7.0.1 stack)

1. **DataHub quickstart ships with GMS authentication disabled**
   (`METADATA_SERVICE_AUTH_ENABLED=false`). The platform supports an optional
   bearer token via `infra/local-secrets/datahub.json`; with auth disabled the
   token is not needed, and the recipe's `${DATAHUB_TOKEN}` placeholder must be
   removed (unset variable + `expandvars` -> `UnboundVariable`). `make
   datahub-token` is therefore a check-only helper here.
2. **Lightweight custom compose patch is required for this host**: all ports
   bind to `127.0.0.1` and colliding ports are remapped (broker 9092->19092,
   MySQL 3306->13306, OpenSearch 9200->19200, GMS 8080->18080; UI stays 9002).
   Recorded as a commented patch header in `infra/datahub/compose.pinned.yaml`.
3. **`pipeline_name` must be at recipe top level** (not inside `source.config`):
   a nested value fails validation with `Extra inputs are not permitted`, and
   stateful ingestion fails to initialize without it.
4. **Telemetry delays**: with a restricted network, DataHub CLI telemetry retries
   for about 5 minutes before failing; `DATAHUB_TELEMETRY_ENABLED=false` is set
   by the runner.
5. **URN layout with `platform_instance`**: the URN name segment is
   `<platform_instance>.<namespace>.<table>` and the third component is the
   **fabric** (DEV/PROD/TEST/CORP), not the instance. Example:
   `urn:li:dataset:(urn:li:dataPlatform:doris,ainative-70dbedd4.demo.ads_revenue_daily,DEV)`.
6. **Search index lag for PostgreSQL entities**: entities exist in GMS (direct
   `dataset(urn:)` lookups succeed) while `searchAcrossEntities` still returns
   nothing. The runner therefore uses "search first, then verify candidate URNs
   with a direct lookup and an exact name match"; the platform only stores
   verified URNs.
7. **`table_pattern` must cover both bare and qualified names**: SQL sources
   report `namespace.table`; a bare-name-only allowlist matched zero tables.
8. **GraphQL `ownership` needs inline fragments** (`OwnerType` has no
   `urn`/`type` fields; use `... on CorpUser { urn username }` /
   `... on CorpGroup { urn name }`). Recorded fixtures replay this shape
   (`backend/tests/fixtures/datahub/*.json`).
9. **View lineage is captured by the Doris connector** when the ingest account
   holds `SHOW_VIEW_PRIV` and the recipe sets `include_view_lineage: true`
   (verified: `ads_revenue_by_country` -> `ads_revenue_daily` appears as an
   `extracted` upstream edge). The platform registers such views only after the
   administrator confirms them as secure views (`catalog-refresh`
   `secure_views`).
10. **The ingestion runner is not root**: the shared work volume is created by
    the backend image, so the runner only reads `payload.json` and keeps scratch
    files in the system temp directory. On Windows hosts that is not `/tmp`, so
    the runner uses the default `tempfile` directory.
11. **SQL source schema hash can be empty**: for the Doris source the captured
    `schemaMetadata.hash` is `""`; the platform treats an empty hash as
    "unknown" rather than as drift.
12. **Docker Desktop WSL2 memory is the full-stack bottleneck**: the VM was
    capped at 15.35 GiB (50% of the host) and one ingestion peak restarted the
    VM; the DataHub stack and the business stack should be started in layers on
    16 GiB hosts (see `docs/runbook.md`).

## Not yet verified (do not treat as supported)

- DataHub authentication-enabled mode (GMS auth is disabled in the pinned
  quickstart; `scripts/datahub_token.py --token` is best-effort and documented
  as such).
- DataHub Profiling ingestion (explicitly disabled in the recipes).
- Full-stack resource footprint on a 16 GiB host (the reference host has 31.5
  GiB; the WSL2 VM cap is documented above).
- Doris multi-BE topologies, workload groups and external catalogs (explicitly
  out of V1 scope).
- MySQL `KILL QUERY` behaviour for accounts without PROCESS privilege on other
  users' connections (the platform only kills its own dedicated connections).


## M6 environment finding: HTTP client behaviour on this host (2026-09-19)

A Python `httpx` client that keeps a connection alive across `POST /api/v1/auth/login`
is intermittently answered with `401 UNAUTHENTICATED "session expired or revoked"`
on the *next* request, even though:

- the same cookie succeeds via `curl` (single request per invocation),
- the same cookie succeeds over a raw socket that sends POST then GET with
  keep-alive on one connection,
- `validate_token()` inside the API container returns the session row for that
  cookie (`revoked_at=None`, not expired), and
- an A/B loop reproduces it deterministically in the failing direction only for
  the default client: `trust_env=True`, connection reuse.

Mitigation used by the reference scripts: `trust_env=False` (the target is
loopback) plus either `Connection: close` or one connection per request
(`scripts/eval_agent.py`). Browsers are not affected, which is why the Playwright
journeys and the UI are reliable. The platform's own request path is unaffected:
the API, its session validation and the DB state were verified independently.

## M6 environment findings on this host (2026-09-24)

### Git Bash mangles container paths and swallows docker output

Two traps showed up while running the acceptance scripts from Git Bash on Windows:

1. **MSYS path conversion** rewrites arguments that look like absolute POSIX paths,
   so `docker compose run ... /opt/ainative/publish_lineage.py` reaches the
   container as `D:/dev/Git/opt/ainative/publish_lineage.py` and fails (or, with a
   plain `--entrypoint sh`, fails silently from the caller's point of view).
   Prefix such commands with `MSYS_NO_PATHCONV=1`.
2. **`timeout <cmd>` around the Windows `docker` CLI loses the container's
   output**: `timeout 600 docker compose run ...` exited 0 with an empty log while
   the same command without `timeout` printed the full result. Do not wrap docker
   invocations with the coreutils `timeout` in Git Bash.

### `make demo-lineage` pointed at the wrong manifest

The target mounted `runtime/` at `/data/runtime` but asked for
`/data/runtime/$(DEMO_RUN)/pipeline_lineage.json`, while the runs live under
`runtime/demo/<run>/`; it also ran without the `full` profile and without
`AIND_DATAHUB_ENABLED=1`. Fixed: the target now requests
`/data/runtime/demo/$(DEMO_RUN)/pipeline_lineage.json`, runs with `--profile full`
and enables DataHub.

### DataHub search index lag for SDK-published lineage

Lineage published through the DataHub SDK (the declared demo pipeline) took about
**10 minutes** here before `searchAcrossLineage` returned it, while the connector's
`extracted` edge was searchable within the platform's 45 s visibility window. The
platform's lineage endpoint reads `searchAcrossLineage`, so during the lag it
reports the honest `no_upstream` state - that is not a platform bug, but an
operational expectation: after `make demo-lineage`, allow the index to catch up
before judging the lineage view. A useful probe is the same GraphQL query from the
source side (`direction: DOWNSTREAM`), which showed the edge earlier than the
target-side query.
