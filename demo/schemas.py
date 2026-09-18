"""Target DDL for the demo tables (spec section 26.2).

Doris DDL is the single-BE local template validated in M2 (DUPLICATE KEY with
``replication_num=1``); duplicate loading is refused by the loader, never
silently deduplicated. The PostgreSQL/MySQL config tables are tiny and live in
their own source databases.
"""

from __future__ import annotations

DORIS_DATABASE = "demo"

# table -> (create DDL, column order used by the CSV files)
DORIS_TABLES: dict[str, dict] = {
    "ads_revenue_daily": {
        "columns": ["dt", "country", "platform", "app_version", "ad_network",
                    "impressions", "clicks", "revenue_usd"],
        "ddl": """
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
DUPLICATE KEY(dt,country,platform,app_version,ad_network)
DISTRIBUTED BY HASH(country,platform) BUCKETS 4
PROPERTIES ("replication_num"="1")
""",
        "primary_key": ["dt", "country", "platform", "app_version", "ad_network"],
    },
    "iap_revenue_daily": {
        "columns": ["dt", "country", "platform", "app_version", "purchases", "revenue_usd"],
        "ddl": """
CREATE TABLE IF NOT EXISTS demo.iap_revenue_daily (
  dt DATE NOT NULL,
  country VARCHAR(8) NOT NULL,
  platform VARCHAR(16) NOT NULL,
  app_version VARCHAR(32) NOT NULL,
  purchases BIGINT NOT NULL,
  revenue_usd DECIMAL(20,6) NOT NULL
)
DUPLICATE KEY(dt,country,platform,app_version)
DISTRIBUTED BY HASH(country,platform) BUCKETS 4
PROPERTIES ("replication_num"="1")
""",
        "primary_key": ["dt", "country", "platform", "app_version"],
    },
    "user_daily": {
        "columns": ["dt", "country", "platform", "dau", "new_users"],
        "ddl": """
CREATE TABLE IF NOT EXISTS demo.user_daily (
  dt DATE NOT NULL,
  country VARCHAR(8) NOT NULL,
  platform VARCHAR(16) NOT NULL,
  dau BIGINT NOT NULL,
  new_users BIGINT NOT NULL
)
DUPLICATE KEY(dt,country,platform)
DISTRIBUTED BY HASH(country) BUCKETS 4
PROPERTIES ("replication_num"="1")
""",
        "primary_key": ["dt", "country", "platform"],
    },
    "retention_daily": {
        "columns": ["cohort_date", "country", "platform", "cohort_size", "d1_users", "d7_users"],
        "ddl": """
CREATE TABLE IF NOT EXISTS demo.retention_daily (
  cohort_date DATE NOT NULL,
  country VARCHAR(8) NOT NULL,
  platform VARCHAR(16) NOT NULL,
  cohort_size BIGINT NOT NULL,
  d1_users BIGINT NULL,
  d7_users BIGINT NULL
)
DUPLICATE KEY(cohort_date,country,platform)
DISTRIBUTED BY HASH(country) BUCKETS 4
PROPERTIES ("replication_num"="1")
""",
        "primary_key": ["cohort_date", "country", "platform"],
    },
    "campaign_cohort_daily": {
        "columns": ["cohort_date", "observation_day", "campaign_id", "revenue_usd", "cost_usd"],
        "ddl": """
CREATE TABLE IF NOT EXISTS demo.campaign_cohort_daily (
  cohort_date DATE NOT NULL,
  observation_day INT NOT NULL,
  campaign_id VARCHAR(32) NOT NULL,
  revenue_usd DECIMAL(20,6) NOT NULL,
  cost_usd DECIMAL(20,6) NOT NULL
)
DUPLICATE KEY(cohort_date,observation_day,campaign_id)
DISTRIBUTED BY HASH(campaign_id) BUCKETS 4
PROPERTIES ("replication_num"="1")
""",
        "primary_key": ["cohort_date", "observation_day", "campaign_id"],
    },
    # Derived table: genuinely produced by `demo-load` from the two revenue
    # tables with the SQL recorded in pipeline_lineage.json (declared lineage).
    "revenue_daily_total": {
        "columns": ["dt", "country", "platform", "app_version", "revenue_usd"],
        "ddl": """
CREATE TABLE IF NOT EXISTS demo.revenue_daily_total (
  dt DATE NOT NULL,
  country VARCHAR(8) NOT NULL,
  platform VARCHAR(16) NOT NULL,
  app_version VARCHAR(32) NOT NULL,
  revenue_usd DECIMAL(20,6) NOT NULL
)
DUPLICATE KEY(dt,country,platform,app_version)
DISTRIBUTED BY HASH(country,platform) BUCKETS 4
PROPERTIES ("replication_num"="1")
""",
        "primary_key": ["dt", "country", "platform", "app_version"],
    },
}

# Tables produced directly from CSV; `revenue_daily_total` is derived by the
# loader from the transform SQL below.
DORIS_SOURCE_TABLES = tuple(name for name in DORIS_TABLES if name != "revenue_daily_total")

POSTGRES_CAMPAIGN_CONFIG = {
    "name": "campaign_config",
    "columns": ["campaign_id", "valid_from", "valid_to", "channel", "target", "daily_budget_usd"],
    "primary_key": ["campaign_id", "valid_from"],
}

MYSQL_APP_RELEASE_CONFIG = {
    "name": "app_release_config",
    "columns": ["platform", "app_version", "release_at", "rollout_note"],
    "primary_key": ["platform", "app_version"],
}

POSTGRES_CAMPAIGN_CONFIG_DDL = """
CREATE TABLE IF NOT EXISTS public.campaign_config (
  campaign_id VARCHAR(32) NOT NULL,
  valid_from DATE NOT NULL,
  valid_to DATE NULL,
  channel VARCHAR(32) NOT NULL,
  target VARCHAR(64) NOT NULL,
  daily_budget_usd DECIMAL(20,6) NOT NULL,
  PRIMARY KEY (campaign_id, valid_from)
)
"""

MYSQL_APP_RELEASE_CONFIG_DDL = """
CREATE TABLE IF NOT EXISTS app_release_config (
  platform VARCHAR(16) NOT NULL,
  app_version VARCHAR(32) NOT NULL,
  release_at DATETIME NOT NULL,
  rollout_note VARCHAR(128) NULL,
  PRIMARY KEY (platform, app_version)
)
"""

TRANSFORM_REVENUE_TOTAL_SQL = (
    "INSERT INTO demo.revenue_daily_total "
    "SELECT dt, country, platform, app_version, SUM(revenue_usd) AS revenue_usd FROM ("
    "SELECT dt, country, platform, app_version, revenue_usd FROM demo.ads_revenue_daily "
    "UNION ALL "
    "SELECT dt, country, platform, app_version, revenue_usd FROM demo.iap_revenue_daily"
    ") combined GROUP BY dt, country, platform, app_version"
)
