"""Loading a generated demo run into the three sources (spec section 26.3).

- Doris facts load through the FE Stream Load HTTP API (bounded, fast);
- the tiny PostgreSQL/MySQL configuration tables load with parameterized
  inserts;
- the derived `demo.revenue_daily_total` is produced by the SQL recorded in
  ``pipeline_lineage.json`` (declared lineage), never by a Python reimplementation.

Reloading an already-loaded run is refused unless ``reset_demo`` is set; the
reset only ever drops tables in the ``demo`` database.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
import psycopg
import pymysql

from demo.schemas import (
    DORIS_SOURCE_TABLES,
    DORIS_TABLES,
    MYSQL_APP_RELEASE_CONFIG,
    MYSQL_APP_RELEASE_CONFIG_DDL,
    POSTGRES_CAMPAIGN_CONFIG,
    POSTGRES_CAMPAIGN_CONFIG_DDL,
    TRANSFORM_REVENUE_TOTAL_SQL,
)

REQUIRED_DATABASES = {"demo"}


@dataclass(frozen=True)
class LoadTargets:
    doris_host: str
    doris_sql_port: int
    # Stream Load is sent to a BE directly: the FE answers with a 307 redirect to
    # the BE's internal address, which is not routable from the host.
    doris_be_host: str
    doris_be_http_port: int
    doris_user: str
    doris_password: str
    mysql_host: str
    mysql_port: int
    mysql_user: str
    mysql_password: str
    mysql_database: str
    pg_host: str
    pg_port: int
    pg_user: str
    pg_password: str
    pg_database: str


def _log(message: str) -> None:
    print(message, flush=True)


def _doris_connection(targets: LoadTargets):
    return pymysql.connect(
        host=targets.doris_host,
        port=targets.doris_sql_port,
        user=targets.doris_user,
        password=targets.doris_password,
        charset="utf8mb4",
        autocommit=True,
    )


def _fetch_scalar(connection, sql: str):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchone()[0]


def _table_rows(connection, table: str) -> int:
    return int(_fetch_scalar(connection, f"SELECT COUNT(*) FROM demo.`{table}`"))


def load_run(
    *,
    run_dir: Path,
    targets: LoadTargets,
    reset_demo: bool = False,
    progress: Callable[[str], None] = _log,
) -> dict[str, Any]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    lineage = json.loads((run_dir / "pipeline_lineage.json").read_text(encoding="utf-8"))
    doris_targets = {name for name in DORIS_SOURCE_TABLES if name in manifest["tables"]}

    stats: dict[str, Any] = {"run_dir": str(run_dir), "tables": {}}
    connection = _doris_connection(targets)
    try:
        with connection.cursor() as cursor:
            cursor.execute("CREATE DATABASE IF NOT EXISTS demo")
        if reset_demo:
            progress("reset: dropping demo tables (explicit --reset-demo)")
            with connection.cursor() as cursor:
                for table in DORIS_TABLES:
                    cursor.execute(f"DROP TABLE IF EXISTS demo.`{table}`")
        for table in doris_targets:
            with connection.cursor() as cursor:
                cursor.execute(DORIS_TABLES[table]["ddl"])
        for table in doris_targets:
            rows = _table_rows(connection, table)
            if rows and not reset_demo:
                raise SystemExit(
                    f"demo.{table} already has {rows} rows; pass --reset-demo to rebuild the demo data"
                )
        # Recreating the derived table keeps the transform idempotent.
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS demo.revenue_daily_total")
            cursor.execute(DORIS_TABLES["revenue_daily_total"]["ddl"])

        for table in doris_targets:
            csv_path = run_dir / f"{table}.csv"
            result = _stream_load(
                targets=targets,
                table=table,
                csv_path=csv_path,
                columns=DORIS_TABLES[table]["columns"],
                label_seed=f"{run_dir.name}",
            )
            stats["tables"][table] = {"loaded": result["loaded"], "stream_load": result["status"]}
            progress(f"doris {table}: loaded {result['loaded']} rows ({result['status']})")

        progress("doris transform: building revenue_daily_total from the declared SQL")
        with connection.cursor() as cursor:
            cursor.execute(TRANSFORM_REVENUE_TOTAL_SQL)
        derived_rows = _table_rows(connection, "revenue_daily_total")
        expected_derived = manifest["tables"].get("iap_revenue_daily", {}).get("rows")
        stats["tables"]["revenue_daily_total"] = {"loaded": derived_rows, "stream_load": "derived"}
        progress(f"doris revenue_daily_total: derived {derived_rows} rows")
        if expected_derived is not None and derived_rows != expected_derived:
            raise SystemExit(
                f"derived table has {derived_rows} rows, expected {expected_derived} (one per iap combo)"
            )

        _verify_counts(connection, manifest=manifest, doris_targets=doris_targets)
    finally:
        connection.close()

    stats["config"] = _load_config_tables(run_dir=run_dir, targets=targets, reset_demo=reset_demo, progress=progress)
    stats["lineage"] = lineage
    return stats


def _verify_counts(connection, *, manifest: dict, doris_targets: set[str]) -> None:
    for table in doris_targets:
        expected = manifest["tables"][table]["rows"]
        actual = _table_rows(connection, table)
        if actual != expected:
            raise SystemExit(f"demo.{table}: loaded {actual} rows, manifest says {expected}")


def _stream_load(
    *,
    targets: LoadTargets,
    table: str,
    csv_path: Path,
    columns: list[str],
    label_seed: str,
) -> dict[str, Any]:
    # Doris keeps load labels for a retention window; a reset+reload reuses the
    # same table name, so the label must be unique per attempt.
    label = (
        "demo-"
        + hashlib.sha256(f"{label_seed}:{table}".encode()).hexdigest()[:16]
        + "-"
        + uuid.uuid4().hex[:8]
    )
    url = (
        f"http://{targets.doris_be_host}:{targets.doris_be_http_port}"
        f"/api/demo/{table}/_stream_load"
    )
    credentials = base64.b64encode(
        f"{targets.doris_user}:{targets.doris_password}".encode("utf-8")
    ).decode("ascii")
    headers = {
        "Authorization": f"Basic {credentials}",
        "label": label,
        "format": "csv",
        "column_separator": ",",
        "columns": ",".join(columns),
        "null_value": "\\N",
        # The Doris FE rejects Stream Load without this header.
        "Expect": "100-continue",
    }
    temp_path = _normalized_csv(csv_path)
    try:
        with temp_path.open("rb") as body:
            with httpx.Client(timeout=600) as client:
                response = client.put(url, content=body, headers=headers)
    finally:
        temp_path.unlink(missing_ok=True)
    if response.status_code != 200:
        raise SystemExit(f"stream load for demo.{table} failed: HTTP {response.status_code} {response.text[:500]}")
    payload = response.json()
    if payload.get("Status") != "Success":
        raise SystemExit(f"stream load for demo.{table} failed: {json.dumps(payload)[:500]}")
    return {
        "status": payload.get("Status"),
        "loaded": int(payload.get("NumberLoadedRows") or 0),
    }


def _normalized_csv(csv_path: Path) -> Path:
    """Re-encode the canonical CSV for Stream Load: empty cells become NULL.

    The header row is dropped (the ``columns`` header declares the order) and the
    result is written to a system temp file so large runs never sit in memory.
    """
    import tempfile

    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", newline="", suffix=".csv", delete=False
    )
    try:
        with csv_path.open("r", encoding="utf-8", newline="") as source:
            reader = csv.reader(source)
            next(reader, None)  # header
            for row in reader:
                handle.write(",".join("\\N" if cell == "" else cell for cell in row))
                handle.write("\n")
    finally:
        handle.close()
    return Path(handle.name)


def _load_config_tables(
    *, run_dir: Path, targets: LoadTargets, reset_demo: bool, progress
) -> dict[str, Any]:
    stats: dict[str, Any] = {}

    with psycopg.connect(
        host=targets.pg_host,
        port=targets.pg_port,
        dbname=targets.pg_database,
        user=targets.pg_user,
        password=targets.pg_password,
        connect_timeout=5,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(POSTGRES_CAMPAIGN_CONFIG_DDL)
        if reset_demo:
            with connection.cursor() as cursor:
                cursor.execute("TRUNCATE TABLE public.campaign_config")
        existing = _pg_scalar(connection, "SELECT COUNT(*) FROM public.campaign_config")
        if existing and not reset_demo:
            raise SystemExit("public.campaign_config already has rows; pass --reset-demo")
        rows = _read_csv(run_dir / "campaign_config.csv")
        with connection.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO public.campaign_config "
                "(campaign_id, valid_from, valid_to, channel, target, daily_budget_usd) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (
                        row["campaign_id"],
                        row["valid_from"] or None,
                        row["valid_to"] or None,
                        row["channel"],
                        row["target"],
                        row["daily_budget_usd"],
                    )
                    for row in rows
                ],
            )
        connection.commit()
        stats["postgres.campaign_config"] = {"loaded": len(rows)}
        progress(f"postgres campaign_config: loaded {len(rows)} rows")

    mysql = pymysql.connect(
        host=targets.mysql_host,
        port=targets.mysql_port,
        user=targets.mysql_user,
        password=targets.mysql_password,
        database=targets.mysql_database,
        charset="utf8mb4",
        autocommit=True,
    )
    try:
        with mysql.cursor() as cursor:
            cursor.execute(MYSQL_APP_RELEASE_CONFIG_DDL)
        if reset_demo:
            with mysql.cursor() as cursor:
                cursor.execute("TRUNCATE TABLE app_release_config")
        existing = int(_fetch_scalar(mysql, "SELECT COUNT(*) FROM app_release_config"))
        if existing and not reset_demo:
            raise SystemExit("app_release_config already has rows; pass --reset-demo")
        rows = _read_csv(run_dir / "app_release_config.csv")
        with mysql.cursor() as cursor:
            cursor.executemany(
                "INSERT INTO app_release_config (platform, app_version, release_at, rollout_note) "
                "VALUES (%s, %s, %s, %s)",
                [
                    (
                        row["platform"],
                        row["app_version"],
                        row["release_at"] or None,
                        row["rollout_note"] or None,
                    )
                    for row in rows
                ],
            )
        stats["mysql.app_release_config"] = {"loaded": len(rows)}
        progress(f"mysql app_release_config: loaded {len(rows)} rows")
    finally:
        mysql.close()
    return stats


def _pg_scalar(connection, sql: str):
    with connection.cursor() as cursor:
        cursor.execute(sql)
        return cursor.fetchone()[0]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))
