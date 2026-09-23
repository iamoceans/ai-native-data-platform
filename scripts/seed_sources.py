#!/usr/bin/env python3
"""M2 three-source seed (connector scope).

Creates small, deterministic tables on the MySQL and Doris sources so the
connector acceptance (schema, types, query, timeout, cancel) runs against real
engines:

- PostgreSQL ``public.fixture_metrics`` / ``public.fixture_heavy`` (the connector
  and UI fixtures the smoke test and the SQL workspace journey query; they are
  not the M4 demo dataset, which the generator owns)
- MySQL  ``app_release_config``   (spec 26.1 MySQL source)
- Doris  ``demo.ads_revenue_daily`` per spec 26.2 (DUPLICATE KEY, 4 buckets, rn=1)
- Doris  ``demo.iap_revenue_daily``, ``demo.user_daily`` (spec 26.1 shapes)
- Doris  ``demo.m2_types_probe``  (type-mapping fixture: LARGEINT, DECIMAL,
  DATETIME, STRING, BOOLEAN)

This is the M2 seed, not the M4 demo generator: it is small (hundreds of rows),
idempotent (only inserts when a table is empty) and uses no company data.
Values are generated from a fixed seed so files/rows are reproducible.
"""

from __future__ import annotations

import argparse
import os
import random
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pymysql

ROOT = Path(__file__).resolve().parents[1]

COUNTRIES = ["US", "DE", "JP"]
PLATFORMS = ["android", "ios"]
VERSIONS = ["4.1.0", "4.2.0", "4.2.1"]
NETWORKS = ["admob", "applovin", "unity"]
CHANNELS = ["paid_social", "search", "influencer"]

MYSQL_DDL = """
CREATE TABLE IF NOT EXISTS app_release_config (
  platform VARCHAR(16) NOT NULL,
  app_version VARCHAR(32) NOT NULL,
  release_at DATETIME NOT NULL,
  rollout_note VARCHAR(255) NULL,
  PRIMARY KEY (platform, app_version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

# Cancel/timeout fixture: the connector acceptance cancels and times out against a
# deliberately expensive 3-way self-join, which needs a table big enough that the
# engine is still working when the platform intervenes.
MYSQL_HEAVY_DDL = """
CREATE TABLE IF NOT EXISTS m2_heavy (
  id BIGINT NOT NULL,
  bucket INT NOT NULL,
  revenue_usd DECIMAL(20,6) NOT NULL,
  PRIMARY KEY (id),
  KEY ix_m2_heavy_bucket (bucket)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

DORIS_ADS_DDL = """
CREATE TABLE IF NOT EXISTS demo.ads_revenue_daily (
  dt DATE NOT NULL,
  country VARCHAR(8) NOT NULL,
  platform VARCHAR(16) NOT NULL,
  app_version VARCHAR(32) NOT NULL,
  ad_network VARCHAR(32) NOT NULL,
  impressions BIGINT NOT NULL,
  clicks BIGINT NOT NULL,
  revenue_usd DECIMAL(20,6) NOT NULL
)
DUPLICATE KEY(dt, country, platform, app_version, ad_network)
DISTRIBUTED BY HASH(country, platform) BUCKETS 4
PROPERTIES ("replication_num" = "1");
"""

DORIS_IAP_DDL = """
CREATE TABLE IF NOT EXISTS demo.iap_revenue_daily (
  dt DATE NOT NULL,
  country VARCHAR(8) NOT NULL,
  platform VARCHAR(16) NOT NULL,
  app_version VARCHAR(32) NOT NULL,
  revenue_usd DECIMAL(20,6) NOT NULL
)
DUPLICATE KEY(dt, country, platform, app_version)
DISTRIBUTED BY HASH(country, platform) BUCKETS 4
PROPERTIES ("replication_num" = "1");
"""

DORIS_USER_DDL = """
CREATE TABLE IF NOT EXISTS demo.user_daily (
  dt DATE NOT NULL,
  country VARCHAR(8) NOT NULL,
  platform VARCHAR(16) NOT NULL,
  dau BIGINT NOT NULL,
  new_users BIGINT NOT NULL
)
DUPLICATE KEY(dt, country, platform)
DISTRIBUTED BY HASH(country, platform) BUCKETS 4
PROPERTIES ("replication_num" = "1");
"""

DORIS_TYPES_DDL = """
CREATE TABLE IF NOT EXISTS demo.m2_types_probe (
  id LARGEINT NOT NULL,
  amount DECIMAL(20,6) NOT NULL,
  created DATETIME NOT NULL,
  label STRING NULL,
  flag BOOLEAN NOT NULL,
  dt DATE NOT NULL
)
DUPLICATE KEY(id)
DISTRIBUTED BY HASH(id) BUCKETS 4
PROPERTIES ("replication_num" = "1");
"""


def read_env(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            match = re.match(rf"^\s*{name}\s*=\s*(.*)$", line)
            if match:
                return match.group(1).strip()
    return default


def connect_mysql(host: str, port: int, user: str, password: str, database: str):
    return pymysql.connect(
        host=host, port=port, user=user, password=password, database=database,
        charset="utf8mb4", autocommit=True,
    )


def table_empty(cursor, schema: str, table: str) -> bool:
    cursor.execute(f"SELECT COUNT(*) FROM `{schema}`.`{table}`")
    return int(cursor.fetchone()[0]) == 0


M2_HEAVY_ROWS = 200_000


def seed_mysql_heavy(cur, database: str) -> int:
    """Seed the cancel/timeout fixture once; returns its row count."""
    cur.execute(MYSQL_HEAVY_DDL)
    if not table_empty(cur, database, "m2_heavy"):
        cur.execute("SELECT COUNT(*) FROM m2_heavy")
        return int(cur.fetchone()[0])
    cur.execute(
        f"""
        INSERT INTO m2_heavy (id, bucket, revenue_usd)
        SELECT gs, gs % 100, ((gs % 10000) / 7.0)
        FROM (
          SELECT a.n + b.n * 10 + c.n * 100 + d.n * 1000 + e.n * 10000 + f.n * 100000 + 1 AS gs
          FROM (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
                UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) a
          CROSS JOIN (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
                UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) b
          CROSS JOIN (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
                UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) c
          CROSS JOIN (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
                UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) d
          CROSS JOIN (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
                UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) e
          CROSS JOIN (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
                UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) f
        ) AS series
        WHERE gs <= {M2_HEAVY_ROWS}
        """
    )
    cur.execute("SELECT COUNT(*) FROM m2_heavy")
    return int(cur.fetchone()[0])


def seed_mysql(host: str, port: int, user: str, password: str, database: str) -> int:
    with connect_mysql(host, port, user, password, database) as conn:
        with conn.cursor() as cur:
            cur.execute(MYSQL_DDL)
            if not table_empty(cur, database, "app_release_config"):
                cur.execute("SELECT COUNT(*) FROM app_release_config")
                return int(cur.fetchone()[0])
            rows = []
            for platform in PLATFORMS:
                for index, version in enumerate(VERSIONS):
                    rows.append(
                        (
                            platform,
                            version,
                            datetime(2026, 8, 20 + index, 9, 30, 0),
                            f"staged rollout {index + 1}/3",
                        )
                    )
            cur.executemany(
                "INSERT INTO app_release_config (platform, app_version, release_at, rollout_note) "
                "VALUES (%s, %s, %s, %s)",
                rows,
            )
            return len(rows)


def seed_mysql_heavy_rows(
    host: str, port: int, user: str, password: str, database: str
) -> int:
    with connect_mysql(host, port, user, password, database) as conn:
        with conn.cursor() as cur:
            return seed_mysql_heavy(cur, database)


def seed_doris(host: str, port: int, user: str, password: str, database: str) -> dict[str, int]:
    rng = random.Random(42)
    counts: dict[str, int] = {}
    with connect_mysql(host, port, user, password, database) as conn:
        with conn.cursor() as cur:
            cur.execute(DORIS_ADS_DDL)
            cur.execute(DORIS_IAP_DDL)
            cur.execute(DORIS_USER_DDL)
            cur.execute(DORIS_TYPES_DDL)
            start = date(2026, 9, 1)
            if table_empty(cur, database, "ads_revenue_daily"):
                rows = []
                for day in range(12):
                    dt = start + timedelta(days=day)
                    for country in COUNTRIES:
                        for platform in PLATFORMS:
                            for version in VERSIONS:
                                for network in NETWORKS:
                                    impressions = rng.randint(50_000, 200_000)
                                    cpm = rng.uniform(4.0, 12.0)
                                    revenue = round(impressions * cpm / 1000.0, 6)
                                    clicks = int(impressions * rng.uniform(0.005, 0.02))
                                    rows.append(
                                        (dt.isoformat(), country, platform, version, network,
                                         impressions, clicks, revenue)
                                    )
                cur.executemany(
                    "INSERT INTO demo.ads_revenue_daily "
                    "(dt, country, platform, app_version, ad_network, impressions, clicks, revenue_usd) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    rows,
                )
                counts["ads_revenue_daily"] = len(rows)
            else:
                cur.execute("SELECT COUNT(*) FROM demo.ads_revenue_daily")
                counts["ads_revenue_daily"] = int(cur.fetchone()[0])

            if table_empty(cur, database, "iap_revenue_daily"):
                rows = []
                for day in range(12):
                    dt = start + timedelta(days=day)
                    for country in COUNTRIES:
                        for platform in PLATFORMS:
                            for version in VERSIONS:
                                revenue = round(rng.uniform(200.0, 4000.0), 6)
                                rows.append((dt.isoformat(), country, platform, version, revenue))
                cur.executemany(
                    "INSERT INTO demo.iap_revenue_daily "
                    "(dt, country, platform, app_version, revenue_usd) VALUES (%s, %s, %s, %s, %s)",
                    rows,
                )
                counts["iap_revenue_daily"] = len(rows)
            else:
                cur.execute("SELECT COUNT(*) FROM demo.iap_revenue_daily")
                counts["iap_revenue_daily"] = int(cur.fetchone()[0])

            if table_empty(cur, database, "user_daily"):
                rows = []
                for day in range(12):
                    dt = start + timedelta(days=day)
                    for country in COUNTRIES:
                        for platform in PLATFORMS:
                            dau = rng.randint(20_000, 80_000)
                            new_users = int(dau * rng.uniform(0.01, 0.05))
                            rows.append((dt.isoformat(), country, platform, dau, new_users))
                cur.executemany(
                    "INSERT INTO demo.user_daily (dt, country, platform, dau, new_users) "
                    "VALUES (%s, %s, %s, %s, %s)",
                    rows,
                )
                counts["user_daily"] = len(rows)
            else:
                cur.execute("SELECT COUNT(*) FROM demo.user_daily")
                counts["user_daily"] = int(cur.fetchone()[0])

            if table_empty(cur, database, "m2_types_probe"):
                rows = [
                    (2**63, "1234567890123.456789", "2026-09-12 08:30:00", "large", True, "2026-09-12"),
                    (1, "0.000001", "2026-09-12 00:00:00", None, False, "2026-09-11"),
                ]
                cur.executemany(
                    "INSERT INTO demo.m2_types_probe (id, amount, created, label, flag, dt) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    rows,
                )
                counts["m2_types_probe"] = len(rows)
            else:
                cur.execute("SELECT COUNT(*) FROM demo.m2_types_probe")
                counts["m2_types_probe"] = int(cur.fetchone()[0])
    return counts


POSTGRES_FIXTURE_DDL = """
CREATE TABLE IF NOT EXISTS public.fixture_metrics (
  dt date NOT NULL,
  country text NOT NULL,
  platform text NOT NULL,
  note text,
  revenue_usd numeric(20,6) NOT NULL,
  impressions bigint NOT NULL
);
CREATE TABLE IF NOT EXISTS public.fixture_heavy (
  id bigint NOT NULL,
  bucket integer NOT NULL,
  revenue_usd numeric(20,6) NOT NULL
);
"""

# Same shapes and the same deterministic series as the integration fixtures, so a
# blank-volume start and a test run see identical numbers.
POSTGRES_FIXTURE_DATA = """
INSERT INTO public.fixture_metrics (dt, country, platform, note, revenue_usd, impressions)
SELECT
  DATE '2026-09-01' + (gs % 10),
  CASE WHEN gs % 3 = 0 THEN 'US' ELSE 'DE' END,
  CASE WHEN gs % 2 = 0 THEN 'android' ELSE 'ios' END,
  CASE WHEN gs % 5 = 0 THEN NULL ELSE 'note-' || gs END,
  ((gs % 997)::numeric / 10)::numeric(20,6),
  (gs * 10)::bigint
FROM generate_series(1, 2500) AS gs;

INSERT INTO public.fixture_heavy (id, bucket, revenue_usd)
SELECT gs, gs % 100, ((gs % 10000)::numeric / 7)::numeric(20,6)
FROM generate_series(1, 100000) AS gs;
"""


def seed_postgres(host: str, port: int, user: str, password: str, database: str) -> dict[str, int]:
    """Create the connector/UI fixture tables when they are missing or empty."""
    import psycopg

    counts: dict[str, int] = {}
    with psycopg.connect(
        host=host, port=port, user=user, password=password, dbname=database, connect_timeout=5
    ) as conn:
        conn.execute(POSTGRES_FIXTURE_DDL)
        for table in ("fixture_metrics", "fixture_heavy"):
            existing = conn.execute(f"SELECT COUNT(*) FROM public.{table}").fetchone()[0]
            if existing:
                counts[table] = int(existing)
                continue
            conn.execute(POSTGRES_FIXTURE_DATA)
            counts[table] = int(
                conn.execute(f"SELECT COUNT(*) FROM public.{table}").fetchone()[0]
            )
        conn.commit()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="seed the PostgreSQL fixtures plus the M2 MySQL and Doris sources"
    )
    parser.add_argument("--mysql-host", default=read_env("MYSQL_SEED_HOST", "127.0.0.1"))
    parser.add_argument("--mysql-port", type=int, default=int(read_env("MYSQL_DB_PORT", "33060")))
    parser.add_argument("--doris-host", default=read_env("DORIS_SEED_HOST", "127.0.0.1"))
    parser.add_argument("--doris-port", type=int, default=int(read_env("DORIS_FE_SQL_PORT", "19030")))
    parser.add_argument("--pg-host", default=read_env("PG_SEED_HOST", "127.0.0.1"))
    parser.add_argument("--pg-port", type=int, default=int(read_env("SOURCE_DB_PORT", "55433")))
    args = parser.parse_args()

    mysql_user = read_env("MYSQL_ADMIN_USER", "source_admin")
    mysql_password = read_env("MYSQL_ADMIN_PASSWORD")
    mysql_db = read_env("MYSQL_SOURCE_DB", "ainative_source")
    doris_user = read_env("DORIS_ADMIN_USER", "doris_admin")
    doris_password = read_env("DORIS_ADMIN_PASSWORD")

    if not mysql_password or not doris_password:
        print("missing passwords in .env; run scripts/setup_secrets.py first", file=sys.stderr)
        return 2

    pg_user = read_env("SOURCE_BOOTSTRAP_USER", "source_admin")
    pg_password = read_env("SOURCE_BOOTSTRAP_PASSWORD")
    pg_db = read_env("SOURCE_DB_NAME", "ainative_source")
    if pg_password:
        pg_counts = seed_postgres(args.pg_host, args.pg_port, pg_user, pg_password, pg_db)
        for table, count in sorted(pg_counts.items()):
            print(f"postgres public.{table}: {count} rows")
    else:
        print("postgres fixture seed skipped: SOURCE_BOOTSTRAP_PASSWORD is not set", file=sys.stderr)

    mysql_rows = seed_mysql(args.mysql_host, args.mysql_port, mysql_user, mysql_password, mysql_db)
    print(f"mysql app_release_config: {mysql_rows} rows")
    heavy_rows = seed_mysql_heavy_rows(args.mysql_host, args.mysql_port, mysql_user, mysql_password, mysql_db)
    print(f"mysql m2_heavy: {heavy_rows} rows")

    doris_counts = seed_doris(args.doris_host, args.doris_port, doris_user, doris_password, "demo")
    for table, count in sorted(doris_counts.items()):
        print(f"doris demo.{table}: {count} rows")
    print("seed complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
