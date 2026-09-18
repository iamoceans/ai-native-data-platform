"""Deterministic demo data generator (spec section 26).

Generation is a pure function of ``(seed, as_of, days, scale, scenario,
generator_version)``; the same inputs must produce the same file hashes. All
money arithmetic uses ``Decimal`` so ground truth matches the analysis kernel
exactly. Scenario injections are explicit single-cell edits, never randomness.

Outputs per run directory:

- one CSV (and Parquet) per table;
- ``schema.json``   - column names and types;
- ``manifest.json`` - row counts, primary keys, date ranges, file hashes,
                      generator version and scenario;
- ``pipeline_lineage.json`` - the declared demo ETL (source tables -> derived
                      table) with the exact transform SQL and its hash;
- ``ground_truth.json`` - evaluation-only expected values (never mounted into
                      metadata or read by the agent).
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from demo.scenarios import (
    CAMPAIGN_CHANNELS,
    CAMPAIGN_IDS,
    INJECTIONS,
    PLATFORMS,
    PRIMARY_TARGET,
    TargetCell,
    scale_preset,
)
from demo.schemas import (
    DORIS_SOURCE_TABLES,
    DORIS_TABLES,
    MYSQL_APP_RELEASE_CONFIG,
    POSTGRES_CAMPAIGN_CONFIG,
    TRANSFORM_REVENUE_TOTAL_SQL,
)

GENERATOR_VERSION = "1.0.0"
QUANT = Decimal("0.000001")

COUNTRY_FACTORS = {"US": 1.6, "DE": 1.1, "JP": 1.2}
PLATFORM_FACTORS = {"android": 1.0, "ios": 0.6}
PLATFORM_ECPM = {"android": Decimal("8"), "ios": Decimal("14")}
VERSION_FACTORS = {"4.2.0": 1.0, "4.2.1": 0.85, "4.3.0": 0.6, "4.3.1": 0.5, "4.4.0": 0.35}
NETWORK_FACTORS = {"AppLovin": 1.4, "AdMob": 1.25, "Unity": 1.1}
# Synthetic concentration: the headline segment carries a realistic share of
# daily ad revenue (a long-tailed app: the US + Android + top network segment is
# material). Scenario targets must dominate day noise for the analysis
# conclusion to be driven by the injected cause.
CELL_WEIGHTS = {
    (PRIMARY_TARGET.country, PRIMARY_TARGET.platform, PRIMARY_TARGET.app_version, PRIMARY_TARGET.ad_network): 60.0,
    ("DE", "ios", "4.2.0", "AdMob"): 25.0,
}


@dataclass
class GenerationResult:
    run_dir: Path
    manifest: dict
    ground_truth: dict
    table_files: dict[str, dict[str, Path]] = field(default_factory=dict)


def _country_factor(country: str) -> float:
    if country in COUNTRY_FACTORS:
        return COUNTRY_FACTORS[country]
    digest = int(hashlib.sha256(country.encode()).hexdigest()[:8], 16)
    return 0.45 + (digest % 1000) / 1000 * 0.8


def _network_factor(network: str) -> float:
    if network in NETWORK_FACTORS:
        return NETWORK_FACTORS[network]
    digest = int(hashlib.sha256(network.encode()).hexdigest()[:8], 16)
    return 0.7 + (digest % 1000) / 1000 * 0.6


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class _TableWriter:
    """Streaming CSV writer that tracks row counts and date ranges."""

    def __init__(self, path: Path, columns: list[str], date_column: str | None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.columns = columns
        self.date_column = date_column
        self._handle = path.open("w", encoding="utf-8", newline="")
        self._writer = csv.writer(self._handle, lineterminator="\n")
        self._writer.writerow(columns)
        self.rows = 0
        self.date_min: str | None = None
        self.date_max: str | None = None

    def write(self, row: dict[str, Any]) -> None:
        self._writer.writerow([self._render(row.get(column)) for column in self.columns])
        self.rows += 1
        if self.date_column:
            value = row.get(self.date_column)
            if value is not None:
                text = str(value)
                self.date_min = text if self.date_min is None else min(self.date_min, text)
                self.date_max = text if self.date_max is None else max(self.date_max, text)

    @staticmethod
    def _render(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, Decimal):
            return format(value, "f")
        return value

    def close(self) -> None:
        self._handle.close()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _run_name(scenario: str, scale: str, seed: int, as_of: date) -> str:
    return f"{scenario}-{scale}-seed{seed}-asof{as_of.isoformat()}"


def generate(
    *,
    output_dir: Path,
    seed: int = 42,
    as_of: str = "2026-09-13",
    days: int = 90,
    scale: str = "small",
    scenario: str = "ecpm_drop",
    with_parquet: bool = True,
    force: bool = False,
) -> GenerationResult:
    preset = scale_preset(scale)
    as_of_date = _parse_date(as_of)
    run_dir = Path(output_dir) / _run_name(scenario, scale, seed, as_of_date)
    if run_dir.exists() and any(run_dir.iterdir()) and not force:
        raise SystemExit(
            f"run directory {run_dir} already exists; pass --force to regenerate"
        )
    if run_dir.exists() and force:
        shutil.rmtree(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    if scenario == "canonical_67":
        return _generate_canonical(
            run_dir=run_dir, seed=seed, as_of=as_of_date, scale=scale, with_parquet=with_parquet
        )

    rng = random.Random(seed)
    start_date = as_of_date - timedelta(days=days)
    current_date = as_of_date - timedelta(days=1)
    baseline_date = current_date - timedelta(days=1)
    injections = INJECTIONS.get(scenario, {})

    writers: dict[str, _TableWriter] = {}
    for table in DORIS_SOURCE_TABLES:
        spec = DORIS_TABLES[table]
        date_column = spec["primary_key"][0]
        writers[table] = _TableWriter(run_dir / f"{table}.csv", spec["columns"], date_column)
    config_writers = {
        "postgres.campaign_config": _TableWriter(
            run_dir / "campaign_config.csv", POSTGRES_CAMPAIGN_CONFIG["columns"], None
        ),
        "mysql.app_release_config": _TableWriter(
            run_dir / "app_release_config.csv", MYSQL_APP_RELEASE_CONFIG["columns"], None
        ),
    }

    day_totals: dict[str, Decimal] = {}
    tail_cells: dict[tuple[str, ...], list[Decimal]] = {}
    tail_impressions: dict[tuple[str, ...], list[Decimal]] = {}
    baseline_cell_values: dict[tuple[str, str, str, str], tuple[int, Decimal]] = {}
    complete_through = current_date

    ads = writers["ads_revenue_daily"]
    day = start_date
    while day < as_of_date:
        day_text = day.isoformat()
        is_current = day == current_date
        day_total = Decimal(0)
        for country in preset.countries:
            if scenario == "incomplete_day" and is_current and country == "US":
                continue  # missing partition on the current day
            country_factor = _country_factor(country)
            for platform in PLATFORMS:
                for version in preset.versions:
                    for network in preset.networks:
                        cell = TargetCell(country, platform, version, network)
                        cell_key = (country, platform, version, network)
                        impressions = _base_impressions(
                            rng, day, country_factor, platform, version, network
                        )
                        weight = CELL_WEIGHTS.get(cell_key, 1.0)
                        if weight != 1.0:
                            impressions = int(impressions * weight)
                        ecpm = _base_ecpm(rng, country_factor, platform, network)
                        if day == baseline_date:
                            baseline_cell_values[cell_key] = (impressions, ecpm)
                        if is_current and cell in injections:
                            injection = injections[cell]
                            baseline_values = baseline_cell_values.get(cell_key)
                            if injection.get("stable_impressions") and baseline_values:
                                impressions = baseline_values[0]
                            if injection.get("stable_ecpm") and baseline_values:
                                ecpm = baseline_values[1]
                            if "ecpm_factor" in injection:
                                ecpm = (ecpm * Decimal(str(injection["ecpm_factor"]))).quantize(
                                    Decimal("0.0001")
                                )
                            if "impressions_factor" in injection:
                                impressions = int(impressions * injection["impressions_factor"])
                        revenue = _revenue(impressions, ecpm)
                        clicks = int(impressions * (0.008 + rng.random() * 0.012))
                        ads.write(
                            {
                                "dt": day_text,
                                "country": country,
                                "platform": platform,
                                "app_version": version,
                                "ad_network": network,
                                "impressions": impressions,
                                "clicks": clicks,
                                "revenue_usd": revenue,
                            }
                        )
                        day_total += revenue
                        if day in (baseline_date, current_date):
                            key = (country, platform, version, network)
                            slot = tail_cells.setdefault(key, [Decimal(0), Decimal(0)])
                            slot[0 if day == baseline_date else 1] += revenue
                            imp_slot = tail_impressions.setdefault(key, [Decimal(0), Decimal(0)])
                            imp_slot[0 if day == baseline_date else 1] += Decimal(impressions)
        day_totals[day_text] = day_totals.get(day_text, Decimal(0)) + day_total

        _write_iap_rows(writers["iap_revenue_daily"], rng, day_text, preset)
        _write_user_rows(writers["user_daily"], rng, day_text, preset)
        _write_retention_rows(
            writers["retention_daily"], rng, day_text, preset, as_of_date
        )
        day += timedelta(days=1)

    _write_campaign_rows(writers["campaign_cohort_daily"], rng, preset, current_date)
    for row in _campaign_config_rows(scenario, current_date):
        config_writers["postgres.campaign_config"].write(row)
    for row in _app_release_rows(preset, current_date):
        config_writers["mysql.app_release_config"].write(row)
    if scenario == "schema_drift":
        drifted = run_dir / "schema_drift"
        drifted.mkdir(exist_ok=True)
        drifted_csv = drifted / "ads_revenue_daily.csv"
        with (run_dir / "ads_revenue_daily.csv").open("r", encoding="utf-8", newline="") as source:
            with drifted_csv.open("w", encoding="utf-8", newline="") as target:
                reader = csv.reader(source)
                writer = csv.writer(target, lineterminator="\n")
                header = next(reader)
                writer.writerow(["net_revenue_usd" if col == "revenue_usd" else col for col in header])
                for row in reader:
                    writer.writerow(row)

    for writer in writers.values():
        writer.close()
    for writer in config_writers.values():
        writer.close()

    baseline_total = day_totals.get(baseline_date.isoformat(), Decimal(0))
    current_total = day_totals.get(current_date.isoformat(), Decimal(0))
    ground_truth = _ground_truth(
        scenario=scenario,
        scale=scale,
        seed=seed,
        as_of=as_of_date,
        baseline_date=baseline_date,
        current_date=current_date,
        baseline_total=baseline_total,
        current_total=current_total,
        tail_cells=tail_cells,
        tail_impressions=tail_impressions,
        complete_through=(
            baseline_date.isoformat()
            if scenario == "incomplete_day"
            else current_date.isoformat()
        ),
    )

    manifest = _manifest(
        run_dir=run_dir,
        writers=writers,
        config_writers=config_writers,
        seed=seed,
        as_of=as_of_date,
        days=days,
        scale=scale,
        scenario=scenario,
        start_date=start_date,
        current_date=current_date,
        complete_through=(
            baseline_date if scenario == "incomplete_day" else current_date
        ),
        preset=preset,
        with_parquet=with_parquet,
    )
    _write_auxiliary(
        run_dir=run_dir,
        manifest=manifest,
        ground_truth=ground_truth,
        tables_in_manifest=set(writers) | {"revenue_daily_total"},
    )
    return GenerationResult(run_dir=run_dir, manifest=manifest, ground_truth=ground_truth)


# ---------------------------------------------------------------------------
# base value helpers
# ---------------------------------------------------------------------------
def _base_impressions(
    rng: random.Random,
    day: date,
    country_factor: float,
    platform: str,
    version: str,
    network: str,
) -> int:
    # Deliberately small day-over-day noise: the demo scenarios must dominate
    # the day-to-day wiggle so the analysis conclusion is driven by the injected
    # cause, not by weekend/seasonal effects.
    weekday_factor = 1.0
    noise = 0.97 + rng.random() * 0.06
    value = (
        3000
        * country_factor
        * PLATFORM_FACTORS[platform]
        * VERSION_FACTORS.get(version, 0.3)
        * _network_factor(network)
        * weekday_factor
        * noise
    )
    return max(1, int(value))


def _base_ecpm(
    rng: random.Random, country_factor: float, platform: str, network: str
) -> Decimal:
    noise = 0.98 + rng.random() * 0.04
    value = (
        PLATFORM_ECPM[platform]
        * Decimal(str(round(country_factor, 4)))
        * Decimal(str(round(_network_factor(network), 4)))
        * Decimal(str(round(noise, 4)))
    )
    return value.quantize(Decimal("0.0001"))


def _revenue(impressions: int, ecpm: Decimal) -> Decimal:
    return (Decimal(impressions) * ecpm / Decimal(1000)).quantize(QUANT, rounding=ROUND_HALF_UP)


def _write_iap_rows(writer: _TableWriter, rng: random.Random, day_text: str, preset) -> None:
    for country in preset.countries:
        for platform in PLATFORMS:
            for version in preset.versions:
                purchases = int(5 + rng.random() * 60)
                revenue = (Decimal(purchases) * Decimal(str(round(2 + rng.random() * 12, 4)))).quantize(QUANT)
                writer.write(
                    {
                        "dt": day_text,
                        "country": country,
                        "platform": platform,
                        "app_version": version,
                        "purchases": purchases,
                        "revenue_usd": revenue,
                    }
                )


def _write_user_rows(writer: _TableWriter, rng: random.Random, day_text: str, preset) -> None:
    for country in preset.countries:
        factor = _country_factor(country)
        for platform in PLATFORMS:
            dau = int(15000 * factor * PLATFORM_FACTORS[platform] * (0.9 + rng.random() * 0.2))
            new_users = int(dau * (0.04 + rng.random() * 0.03))
            writer.write(
                {
                    "dt": day_text,
                    "country": country,
                    "platform": platform,
                    "dau": dau,
                    "new_users": new_users,
                }
            )


def _write_retention_rows(
    writer: _TableWriter, rng: random.Random, day_text: str, preset, as_of: date
) -> None:
    cohort = _parse_date(day_text)
    age_days = (as_of - cohort).days
    for country in preset.countries:
        factor = _country_factor(country)
        for platform in PLATFORMS:
            cohort_size = int(2000 * factor * PLATFORM_FACTORS[platform] * (0.8 + rng.random() * 0.4))
            d1 = int(cohort_size * (0.35 + rng.random() * 0.15)) if age_days >= 2 else None
            d7 = int(cohort_size * (0.15 + rng.random() * 0.1)) if age_days >= 8 else None
            writer.write(
                {
                    "cohort_date": day_text,
                    "country": country,
                    "platform": platform,
                    "cohort_size": cohort_size,
                    "d1_users": d1,
                    "d7_users": d7,
                }
            )


def _write_campaign_rows(writer: _TableWriter, rng: random.Random, preset, current_date: date) -> None:
    first_cohort = current_date - timedelta(days=30)
    cohort = first_cohort
    while cohort <= current_date:
        for campaign in CAMPAIGN_IDS:
            for observation_day in range(preset.observations):
                cost = (Decimal(str(round(50 + rng.random() * 200, 4)))).quantize(QUANT)
                roas = Decimal(str(round(0.6 + rng.random() * 1.8, 4)))
                revenue = (cost * roas).quantize(QUANT)
                writer.write(
                    {
                        "cohort_date": cohort.isoformat(),
                        "observation_day": observation_day,
                        "campaign_id": campaign,
                        "revenue_usd": revenue,
                        "cost_usd": cost,
                    }
                )
        cohort += timedelta(days=1)


def _campaign_config_rows(scenario: str, current_date: date) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, campaign in enumerate(CAMPAIGN_IDS):
        rows.append(
            {
                "campaign_id": campaign,
                "valid_from": (current_date - timedelta(days=90)).isoformat(),
                "valid_to": None,
                "channel": CAMPAIGN_CHANNELS[index % len(CAMPAIGN_CHANNELS)],
                "target": f"install_{campaign}",
                "daily_budget_usd": Decimal("500.000000") + Decimal(index * 100),
            }
        )
    if scenario == "config_duplicate":
        # Overlapping validity for the first campaign: the as-of join must
        # refuse the duplicate instead of double counting.
        rows.append(
            {
                "campaign_id": CAMPAIGN_IDS[0],
                "valid_from": (current_date - timedelta(days=30)).isoformat(),
                "valid_to": None,
                "channel": "google_uac",
                "target": "install_cmp_001_v2",
                "daily_budget_usd": Decimal("750.000000"),
            }
        )
    return rows


def _app_release_rows(preset, current_date: date) -> list[dict[str, Any]]:
    rows = []
    for platform in PLATFORMS:
        for index, version in enumerate(preset.versions):
            release_at = current_date - timedelta(days=20 + index * 30)
            rows.append(
                {
                    "platform": platform,
                    "app_version": version,
                    "release_at": f"{release_at.isoformat()} 10:00:00",
                    "rollout_note": "ga" if index == 0 else f"rollout_{index}",
                }
            )
    return rows


# ---------------------------------------------------------------------------
# ground truth / manifest
# ---------------------------------------------------------------------------
def _ground_truth(
    *,
    scenario: str,
    scale: str,
    seed: int,
    as_of: date,
    baseline_date: date,
    current_date: date,
    baseline_total: Decimal,
    current_total: Decimal,
    tail_cells: dict[tuple[str, ...], list[Decimal]],
    tail_impressions: dict[tuple[str, ...], list[Decimal]],
    complete_through: str,
) -> dict:
    from app.analysis.drivers import decompose_revenue

    delta = current_total - baseline_total
    change_pct = None if baseline_total == 0 else delta / baseline_total

    target: dict[str, Any] | None = None
    expected: dict[str, Any] = {}
    has_target = scenario in {"ecpm_drop", "traffic_drop", "mixed_offset", "schema_drift"}
    if has_target:
        key = (
            PRIMARY_TARGET.country,
            PRIMARY_TARGET.platform,
            PRIMARY_TARGET.app_version,
            PRIMARY_TARGET.ad_network,
        )
        baseline_rev, current_rev = tail_cells.get(key, [Decimal(0), Decimal(0)])
        baseline_imp, current_imp = tail_impressions.get(key, [Decimal(0), Decimal(0)])
        target = PRIMARY_TARGET.as_dict()
        target_payload = {
            "baseline": str(baseline_rev),
            "current": str(current_rev),
            "delta": str(current_rev - baseline_rev),
            "impressions_baseline": str(baseline_imp),
            "impressions_current": str(current_imp),
        }
        if baseline_imp > 0 and current_imp > 0:
            driver = decompose_revenue(
                impressions_baseline=baseline_imp,
                impressions_current=current_imp,
                revenue_baseline=baseline_rev,
                revenue_current=current_rev,
            )
            target_payload["driver"] = {
                "status": driver.status,
                "impression_effect": str(driver.impression_effect),
                "ecpm_effect": str(driver.ecpm_effect),
                "ecpm_baseline": str(driver.ecpm_baseline),
                "ecpm_current": str(driver.ecpm_current),
            }
        expected["target"] = target_payload
        if scenario == "ecpm_drop":
            expected["dominant_factor"] = "ecpm"
        elif scenario == "traffic_drop":
            expected["dominant_factor"] = "impressions"
        elif scenario == "mixed_offset":
            expected["dominant_factor"] = "mixed"
        elif scenario == "schema_drift":
            expected["drift"] = {
                "table": "demo.ads_revenue_daily",
                "renamed_column": {"revenue_usd": "net_revenue_usd"},
                "expected_behavior": "refresh schema and regenerate SQL; never reuse the old column list",
            }
    elif scenario == "no_change":
        expected["no_material_change"] = True
    elif scenario == "incomplete_day":
        expected["incomplete_partition"] = {"country": "US", "date": current_date.isoformat()}
    elif scenario == "config_duplicate":
        expected["duplicate_config"] = {"campaign_id": CAMPAIGN_IDS[0]}
    elif scenario == "canonical_67":
        expected["canonical"] = True

    return {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "scenario": scenario,
        "scale": scale,
        "seed": seed,
        "as_of": as_of.isoformat(),
        "metric": "ads_revenue",
        "baseline_date": baseline_date.isoformat(),
        "current_date": current_date.isoformat(),
        "baseline_total": str(baseline_total),
        "current_total": str(current_total),
        "delta": str(delta),
        "change_pct": None if change_pct is None else str(change_pct),
        "data_complete_through": complete_through,
        "target": target,
        "expected": expected,
        "notes": [
            "ground truth is evaluation-only and must not be mounted into the agent",
            "all monetary values are Decimal strings in USD",
        ],
    }


def _manifest(
    *,
    run_dir: Path,
    writers: dict[str, _TableWriter],
    config_writers: dict[str, _TableWriter],
    seed: int,
    as_of: date,
    days: int,
    scale: str,
    scenario: str,
    start_date: date,
    current_date: date,
    complete_through: date,
    preset,
    with_parquet: bool,
) -> dict:
    tables: dict[str, Any] = {}
    for table, writer in writers.items():
        spec = DORIS_TABLES[table]
        hashes = {"csv": _sha256_file(writer.path)}
        if with_parquet:
            parquet_path = writer.path.with_suffix(".parquet")
            _write_parquet(writer.path, parquet_path)
            hashes["parquet"] = _sha256_file(parquet_path)
        tables[table] = {
            "rows": writer.rows,
            "columns": writer.columns,
            "primary_key": spec["primary_key"],
            "date_column": writer.date_column,
            "date_min": writer.date_min,
            "date_max": writer.date_max,
            "complete_through": complete_through.isoformat(),
            "hashes": hashes,
        }
    tables["revenue_daily_total"] = {
        "derived": True,
        "transform_id": "ads_iap_to_revenue_total_v1",
        "rows": None,
        "columns": DORIS_TABLES["revenue_daily_total"]["columns"],
        "primary_key": DORIS_TABLES["revenue_daily_total"]["primary_key"],
    }
    config_tables: dict[str, Any] = {}
    for key, writer in config_writers.items():
        database, name = key.split(".", 1)
        spec = POSTGRES_CAMPAIGN_CONFIG if database == "postgres" else MYSQL_APP_RELEASE_CONFIG
        config_tables.setdefault(database, {})[name] = {
            "rows": writer.rows,
            "columns": writer.columns,
            "primary_key": spec["primary_key"],
            "hashes": {"csv": _sha256_file(writer.path)},
        }
    return {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "as_of": as_of.isoformat(),
        "days": days,
        "scale": scale,
        "scenario": scenario,
        "currency": "USD",
        "business_timezone": "UTC",
        "date_range": {"start": start_date.isoformat(), "end": current_date.isoformat()},
        "dimensions": {
            "countries": list(preset.countries),
            "platforms": list(PLATFORMS),
            "app_versions": list(preset.versions),
            "ad_networks": list(preset.networks),
            "campaign_ids": list(CAMPAIGN_IDS),
        },
        "tables": tables,
        "complete_through": complete_through.isoformat(),
    }


def _write_auxiliary(
    *,
    run_dir: Path,
    manifest: dict,
    ground_truth: dict,
    tables_in_manifest: set[str],
) -> None:
    schema = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "tables": {
            table: {
                "columns": DORIS_TABLES[table]["columns"],
                "primary_key": DORIS_TABLES[table]["primary_key"],
            }
            for table in sorted(tables_in_manifest)
            if table in DORIS_TABLES
        },
    }
    lineage = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "run_id": run_dir.name,
        "declared": [],
    }
    if {"ads_revenue_daily", "iap_revenue_daily", "revenue_daily_total"} <= tables_in_manifest:
        transform_hash = hashlib.sha256(TRANSFORM_REVENUE_TOTAL_SQL.encode("utf-8")).hexdigest()
        for source in ("ads_revenue_daily", "iap_revenue_daily"):
            lineage["declared"].append(
                {
                    "transform_id": "ads_iap_to_revenue_total_v1",
                    "source_dataset": f"demo.{source}",
                    "target_dataset": "demo.revenue_daily_total",
                    "label": "declared_by_demo_pipeline",
                    "transform_sql": TRANSFORM_REVENUE_TOTAL_SQL,
                    "transform_sql_hash": transform_hash,
                }
            )
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / "schema.json").write_text(
        json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / "pipeline_lineage.json").write_text(
        json.dumps(lineage, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / "ground_truth.json").write_text(
        json.dumps(ground_truth, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_parquet(csv_path: Path, parquet_path: Path) -> None:
    try:
        import pyarrow.csv as pa_csv
        import pyarrow.parquet as pa_parquet
    except ImportError as exc:  # pragma: no cover - pyarrow is a backend dependency
        raise SystemExit(f"pyarrow is required for Parquet output: {exc}") from exc
    table = pa_csv.read_csv(csv_path)
    pa_parquet.write_table(table, parquet_path, compression="snappy")


# ---------------------------------------------------------------------------
# canonical_67 (spec 16.3 exact values)
# ---------------------------------------------------------------------------
CANONICAL_BASELINE_DATE = "2026-09-11"
CANONICAL_CURRENT_DATE = "2026-09-12"
CANONICAL_ROWS = (
    # country, ad_network, impressions (both days), baseline revenue, current revenue
    ("US", "AppLovin", 100000, "2500", "1830"),
    ("DE", "AdMob", 200000, "4000", "3700"),
    ("JP", "Unity", 200000, "3500", "3470"),
)


def _generate_canonical(
    *, run_dir: Path, seed: int, as_of: date, scale: str, with_parquet: bool
) -> GenerationResult:
    """Miniature golden set with the exact numbers from spec 16.3."""
    from app.analysis.drivers import decompose_revenue

    path = run_dir / "ads_revenue_daily.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(DORIS_TABLES["ads_revenue_daily"]["columns"])
        for country, network, impressions, baseline_rev, current_rev in CANONICAL_ROWS:
            for day, revenue in (
                (CANONICAL_BASELINE_DATE, baseline_rev),
                (CANONICAL_CURRENT_DATE, current_rev),
            ):
                writer.writerow(
                    [
                        day,
                        country,
                        "android",
                        "4.2.1",
                        network,
                        impressions,
                        int(impressions * 0.01),
                        format(Decimal(revenue).quantize(QUANT), "f"),
                    ]
                )
    hashes = {"csv": _sha256_file(path)}
    if with_parquet:
        _write_parquet(path, path.with_suffix(".parquet"))
        hashes["parquet"] = _sha256_file(path.with_suffix(".parquet"))

    baseline_total = sum(Decimal(row[3]) for row in CANONICAL_ROWS)
    current_total = sum(Decimal(row[4]) for row in CANONICAL_ROWS)
    target = CANONICAL_ROWS[0]
    target_baseline, target_current = Decimal(target[3]), Decimal(target[4])
    driver = decompose_revenue(
        impressions_baseline=target[2],
        impressions_current=target[2],
        revenue_baseline=target_baseline,
        revenue_current=target_current,
    )
    delta = current_total - baseline_total
    ground_truth = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "scenario": "canonical_67",
        "scale": scale,
        "seed": seed,
        "as_of": as_of.isoformat(),
        "metric": "ads_revenue",
        "baseline_date": CANONICAL_BASELINE_DATE,
        "current_date": CANONICAL_CURRENT_DATE,
        "baseline_total": str(baseline_total),
        "current_total": str(current_total),
        "delta": str(delta),
        "change_pct": str(delta / baseline_total),
        "data_complete_through": CANONICAL_CURRENT_DATE,
        "target": {
            "country": "US",
            "platform": "android",
            "app_version": "4.2.1",
            "ad_network": "AppLovin",
        },
        "expected": {
            "target": {
                "baseline": str(target_baseline),
                "current": str(target_current),
                "delta": str(target_current - target_baseline),
                "impressions_baseline": str(target[2]),
                "impressions_current": str(target[2]),
                "driver": {
                    "status": driver.status,
                    "impression_effect": str(driver.impression_effect),
                    "ecpm_effect": str(driver.ecpm_effect),
                    "ecpm_baseline": str(driver.ecpm_baseline),
                    "ecpm_current": str(driver.ecpm_current),
                },
            },
            "target_delta": str(target_current - target_baseline),
            "target_net_change_share": str((target_current - target_baseline) / delta),
            "target_contribution_pp": str((target_current - target_baseline) / baseline_total * 100),
            "impression_effect": str(driver.impression_effect),
            "ecpm_effect": str(driver.ecpm_effect),
            "other_groups_delta": str(delta - (target_current - target_baseline)),
        },
        "notes": [
            "exact acceptance sample from spec section 16.3 (not random)",
            "ground truth is evaluation-only and must not be mounted into the agent",
        ],
    }
    manifest = {
        "schema_version": 1,
        "generator_version": GENERATOR_VERSION,
        "seed": seed,
        "as_of": as_of.isoformat(),
        "days": 2,
        "scale": scale,
        "scenario": "canonical_67",
        "currency": "USD",
        "business_timezone": "UTC",
        "date_range": {"start": CANONICAL_BASELINE_DATE, "end": CANONICAL_CURRENT_DATE},
        "dimensions": {
            "countries": ["US", "DE", "JP"],
            "platforms": ["android"],
            "app_versions": ["4.2.1"],
            "ad_networks": ["AppLovin", "AdMob", "Unity"],
            "campaign_ids": [],
        },
        "tables": {
            "ads_revenue_daily": {
                "rows": len(CANONICAL_ROWS) * 2,
                "columns": DORIS_TABLES["ads_revenue_daily"]["columns"],
                "primary_key": DORIS_TABLES["ads_revenue_daily"]["primary_key"],
                "date_column": "dt",
                "date_min": CANONICAL_BASELINE_DATE,
                "date_max": CANONICAL_CURRENT_DATE,
                "complete_through": CANONICAL_CURRENT_DATE,
                "hashes": hashes,
            }
        },
        "complete_through": CANONICAL_CURRENT_DATE,
    }
    _write_auxiliary(
        run_dir=run_dir,
        manifest=manifest,
        ground_truth=ground_truth,
        tables_in_manifest={"ads_revenue_daily"},
    )
    return GenerationResult(run_dir=run_dir, manifest=manifest, ground_truth=ground_truth)
