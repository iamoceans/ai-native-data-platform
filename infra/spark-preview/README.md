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
