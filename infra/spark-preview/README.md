# Spark/Hive preview fixture

Throwaway local stand-ins for the two services `docs/runbook.md` ("Spark / Hive
shared Metastore preview") expects a deployment to already run:

| Service | Image | Port | Role |
|---|---|---|---|
| `hive-metastore` | `apache/hive:3.1.3` | 9083 | shared Hive Metastore (derby metadata, local warehouse) |
| `spark-thrift` | `apache/spark:3.5.3` | 10000 | Spark Thrift Server (`NOSASL`) that executes SQL |

Both containers share one named volume mounted at `/opt/hive/data`, so a table
registered through the Hive CLI and a table created through Spark point at the
same warehouse files. They join the platform's `ainative-backend` network, which
means `spark-thrift` and `hive-metastore` must be listed in
`AIND_SOURCE_HOST_ALLOWLIST` before a datasource can be registered.

This is **not** a delivered deployment profile: it runs both services as root
(only because they share one docker volume), has no authorizer and no
authentication (NOSASL ignores the source identity), and keeps its metadata in
an embedded derby database. Use it to accept the connector, never to serve data.

```bash
# start (assumes the platform stack is already up)
docker compose -f infra/spark-preview/compose.yaml up -d

# create a Hive-side table through the shared metastore
docker exec -i ainative-spark-preview-hive-metastore-1 bash -c '
  /opt/hive/bin/hive --hiveconf hive.metastore.uris=thrift://localhost:9083 \
    -e "CREATE DATABASE IF NOT EXISTS demo"'

# the platform then: registers kind=spark (host spark-thrift:10000,
# metastore_host hive-metastore:9083), refreshes the catalog for `demo`,
# grants discover/query per table, and queries it like any other engine.

# stop (volumes preserved)
docker compose -f infra/spark-preview/compose.yaml down
```

## Acceptance data

`runtime/accept_spark.py` drives the platform path (registration, catalog
refresh, grants, SELECT, the stateless rejections, cancel, and the 120-second
ceiling) and creates nothing itself; the tables it needs are:

| Table | Created by | Why |
|---|---|---|
| `demo.hive_orders` | the Hive CLI above, over a CSV in the shared warehouse | proves a table registered outside Spark appears in Spark's catalog |
| `demo.spark_revenue` | `INSERT INTO ... VALUES` through the Thrift Server | a Spark-managed table with known rows |
| `demo.slow_events` | `CREATE TABLE AS SELECT ... FROM range(1, 2000001)` | a query long enough for the cancel path |
| `demo.slow_events_big` | `CREATE TABLE AS SELECT ... FROM range(1, 8000001)` | long enough to outlive the 120-second timers (the 2M-row join finishes in ~118s) |

`runtime/accept_spark_server_timeout.py` proves the server backstop on its own,
without the platform: it builds `demo.slow_events_big` and waits for
`spark.sql.thriftServer.queryTimeout` to abort the join at 120.0s. The server
reports `TIMEDOUT_STATE` (8), which impyla 0.24.0 cannot name - see the comment
in `backend/app/providers/spark.py`.

The MySQL/Doris services can compete with this fixture for memory on a small
VM; `docs/compatibility.md` finding 12 covers the ceiling this host hit.

### Standalone cancellation transport regression

For the RPC race test only, an embedded-metastore Spark fixture avoids starting
HMS or the full platform. It does not verify shared-HMS lineage or authorization.
It binds only loopback; it uses the same pinned Spark 3.5.3 image as the preview.

```bash
docker compose -f infra/spark-preview/compose.cancel-test.yaml up -d --wait
AIND_TEST_SPARK_HOST=127.0.0.1 AIND_TEST_SPARK_PORT=11000 \
  uv run --project backend --frozen pytest backend/tests/integration/test_spark_cancel_transport.py -q
# Normal integration environment/control database settings are also required.
docker compose -f infra/spark-preview/compose.cancel-test.yaml down
```

The test holds a real GetOperationStatus response before reading it, issues
CancelOperation concurrently and verifies both the uncorrupted response and
server CANCELED_STATE, then a successful SELECT. Shared-client cancellation
reproduced KeyError(None) before isolation; the new provider uses a separate
bounded transport and closes it on success/failure.

### Verified TLS and scalar parameter acceptance

A second fixture adds encrypted transport and a short-lived self-signed CA. It
uses NOSASL and an embedded metastore: it proves certificate verification and
query/browser execution, not source read-only authorization or shared HMS lineage.

```bash
uv run --project backend --frozen python infra/spark-preview/setup_tls_fixture.py
# Public CA: runtime/spark-tls-fixture/ca.pem. Private keystore/password stay ignored.
# Wait until HiveThriftServer2 reports startup; local port defaults to 11001.
AIND_TEST_SPARK_HOST=127.0.0.1 AIND_TEST_SPARK_PORT=11000 \
AIND_TEST_SPARK_TLS_HOST=127.0.0.1 AIND_TEST_SPARK_TLS_PORT=11001 \
AIND_TEST_SPARK_TLS_CA_CERT="$PWD/runtime/spark-tls-fixture/ca.pem" \
  uv run --project backend --frozen pytest backend/tests/integration/test_spark_secure_parameters.py -q
# Requires the normal dedicated test control DB settings.
docker --host=unix:///var/run/docker.sock stop aind-spark-tls-acceptance
docker --host=unix:///var/run/docker.sock rm aind-spark-tls-acceptance
```

Use --output-dir, --name and --port for a distinct fixture. Existing keystores
are not overwritten. Preserve Docker's configured proxy and CA for image access.
Certificates expire after two days; remove only the stopped fixture's ignored
output directory before regenerating. For a deployed browser acceptance register
and grant a Spark source, set E2E_SPARK_DATASOURCE/E2E_BASE_URL and the standard
E2E administrator credentials, then run `npm run test:e2e -- spark.spec.ts`.

### PLAIN identity acceptance over verified TLS

The same generator can start an isolated server with Hive CUSTOM authentication
and SASL PLAIN on the wire. Its one random test identity is checked against a
salted digest using constant-time comparison. It compiles the checked-in Scala
authenticator using the compiler and Hive API already in the pinned Spark image;
no additional image or Maven download is needed.

```bash
uv run --project backend --frozen python infra/spark-preview/setup_tls_fixture.py \
  --auth PLAIN --port 11002 --name aind-spark-plain-acceptance \
  --output-dir runtime/spark-plain-fixture
# Wait for HiveThriftServer2 startup. Never print credentials.json.
AIND_TEST_SPARK_PLAIN_HOST=127.0.0.1 AIND_TEST_SPARK_PLAIN_PORT=11002 \
AIND_TEST_SPARK_PLAIN_CA_CERT="$PWD/runtime/spark-plain-fixture/ca.pem" \
AIND_TEST_SPARK_PLAIN_CREDENTIALS_FILE="$PWD/runtime/spark-plain-fixture/credentials.json" \
  uv run --project backend --frozen pytest \
    backend/tests/integration/test_spark_plain_auth.py \
    backend/tests/integration/test_spark_cancel_transport.py -q
# Requires the normal dedicated test control DB settings. The NOSASL cancellation
# variant skips unless AIND_TEST_SPARK_HOST is configured separately.
docker --host=unix:///var/run/docker.sock stop aind-spark-plain-acceptance
docker --host=unix:///var/run/docker.sock rm aind-spark-plain-acceptance
```

Generated credentials, password configuration and keystore are private files;
output directories must be empty and below the repository's ignored runtime/.
The fixture uses a custom single-identity test authenticator, root process and
embedded metastore. It is not a production identity service, read-only authorizer
or shared-HMS lineage deployment. Production deployments still need their own
source authentication and authorization acceptance.

### Source read-only authorization gate

PLAIN authenticates a caller; this fixture has no source authorizer. Use the
following verifier against a deployment that actually enforces a read-only
identity. The included unprotected fixture is a negative control and must fail.

Pre-create a dedicated `aind_authz_acceptance` database on that source. Give the
owner identity create/insert/read/drop privileges there and the reader only
SELECT/metadata privileges. Put each connection in a private ignored JSON file
with `host`, `port`, `user`, `password`, `database`, `auth_mechanism: "PLAIN"`,
`use_ssl: true` and optional `ca_cert`. Both files must address the same endpoint
and the same `aind_authz_*` database. These are Impyla connection files, not the
platform's credential-reference format. Never pass passwords as arguments.

```bash
uv run --project backend --frozen python scripts/verify_spark_readonly.py \
  --reader-config infra/local-secrets/spark-authz-reader.json \
  --owner-config infra/local-secrets/spark-authz-owner.json
```

The owner seeds a uniquely named Parquet table. The reader must retrieve its
known row and receive recognizable authorization errors for INSERT, INSERT
OVERWRITE, TRUNCATE, ALTER ADD COLUMN, RENAME, CREATE AS SELECT and DROP. After
each denial both identities must still read the original columns/row. Syntax,
unsupported-operation, missing-table and transport errors fail the gate. The
verifier stops on the first allowed write and always attempts owner cleanup of
its three generated table names. Cleanup failure also fails the gate. No tables
outside this dedicated namespace are changed. DELETE/UPDATE on this Parquet
fixture are not authorization proof and are not counted as denial cases.

For the integration entry point set
`AIND_TEST_SPARK_READONLY_READER_FILE` / `AIND_TEST_SPARK_READONLY_OWNER_FILE`
and run `backend/tests/integration/test_spark_readonly_acceptance.py`. With neither
file configured that deployment case skips; partial configuration or a failed
explicit endpoint fails. A separate real PLAIN-fixture case proves the verifier
rejects the unprotected server and removes its test tables. Mocked successful
policy tests prove verifier logic only; actual read-only source acceptance
remains pending until an authorizer is available.
