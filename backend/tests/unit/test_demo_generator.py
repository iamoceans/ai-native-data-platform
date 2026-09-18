"""Demo generator tests (spec section 26): determinism and scenario shapes."""

from __future__ import annotations

import csv
import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from demo.generator import GENERATOR_VERSION, generate
from demo.scenarios import SCENARIOS

pytestmark = []


def _generate(workdir: Path, scenario: str, **overrides):
    return generate(
        output_dir=workdir,
        seed=42,
        as_of="2026-09-13",
        days=5,
        scale="small",
        scenario=scenario,
        with_parquet=False,
        **overrides,
    )


def _csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_generation_is_deterministic(workdir):
    first = _generate(workdir, "ecpm_drop")
    first_hash = hashlib.sha256(
        (first.run_dir / "ads_revenue_daily.csv").read_bytes()
    ).hexdigest()
    second = _generate(workdir, "ecpm_drop", force=True)
    second_hash = hashlib.sha256(
        (second.run_dir / "ads_revenue_daily.csv").read_bytes()
    ).hexdigest()
    assert first_hash == second_hash
    assert first.manifest["generator_version"] == GENERATOR_VERSION
    assert first.manifest["tables"]["ads_revenue_daily"]["rows"] == len(
        _csv_rows(first.run_dir / "ads_revenue_daily.csv")
    )


def test_manifest_row_counts_match_files(workdir):
    result = _generate(workdir, "ecpm_drop")
    for table, entry in result.manifest["tables"].items():
        if entry.get("derived"):
            continue
        assert entry["rows"] == len(_csv_rows(result.run_dir / f"{table}.csv")), table
    assert result.manifest["tables"]["revenue_daily_total"]["derived"] is True


def test_ecpm_drop_injects_the_target_cell(workdir):
    result = _generate(workdir, "ecpm_drop")
    rows = _csv_rows(result.run_dir / "ads_revenue_daily.csv")
    target = result.ground_truth["target"]
    current_date = result.ground_truth["current_date"]
    baseline_date = result.ground_truth["baseline_date"]
    cell = {
        (row["country"], row["platform"], row["app_version"], row["ad_network"])
        for row in rows
        if row["country"] == target["country"]
        and row["platform"] == target["platform"]
        and row["app_version"] == target["app_version"]
        and row["ad_network"] == target["ad_network"]
    }
    assert cell  # the target cell exists at every scale
    expected = result.ground_truth["expected"]["target"]
    assert Decimal(expected["current"]) < Decimal(expected["baseline"])
    assert result.ground_truth["data_complete_through"] == current_date
    assert baseline_date < current_date


def test_incomplete_day_removes_us_partitions(workdir):
    result = _generate(workdir, "incomplete_day")
    rows = _csv_rows(result.run_dir / "ads_revenue_daily.csv")
    current = result.ground_truth["current_date"]
    assert not [row for row in rows if row["dt"] == current and row["country"] == "US"]
    assert result.ground_truth["data_complete_through"] != current


def test_config_duplicate_writes_overlapping_config(workdir):
    result = _generate(workdir, "config_duplicate")
    rows = _csv_rows(result.run_dir / "campaign_config.csv")
    duplicates = [row for row in rows if row["campaign_id"] == "cmp_001"]
    assert len(duplicates) == 2


def test_schema_drift_renames_a_column(workdir):
    result = _generate(workdir, "schema_drift")
    drifted = result.run_dir / "schema_drift" / "ads_revenue_daily.csv"
    with drifted.open("r", encoding="utf-8", newline="") as handle:
        header = next(csv.reader(handle))
    assert "net_revenue_usd" in header
    assert "revenue_usd" not in header
    assert result.ground_truth["expected"]["drift"]["renamed_column"]


def test_canonical_67_matches_the_specification(workdir):
    result = _generate(workdir, "canonical_67")
    expected = result.ground_truth["expected"]
    assert result.ground_truth["baseline_total"] == "10000"
    assert result.ground_truth["current_total"] == "9000"
    assert Decimal(expected["target"]["delta"]) == Decimal("-670")
    assert Decimal(expected["target"]["driver"]["impression_effect"]) == Decimal("0")
    assert Decimal(expected["target"]["driver"]["ecpm_effect"]) == Decimal("-670")
    assert expected["other_groups_delta"] == "-330"
    assert Decimal(expected["target"]["driver"]["ecpm_baseline"]) == Decimal("25")
    assert Decimal(expected["target"]["driver"]["ecpm_current"]) == Decimal("18.3")


def test_lineage_manifest_declares_the_transform(workdir):
    result = _generate(workdir, "ecpm_drop")
    lineage = json.loads((result.run_dir / "pipeline_lineage.json").read_text(encoding="utf-8"))
    assert len(lineage["declared"]) == 2
    for edge in lineage["declared"]:
        assert edge["label"] == "declared_by_demo_pipeline"
        assert edge["transform_sql_hash"]
        assert edge["target_dataset"] == "demo.revenue_daily_total"


def test_ground_truth_is_not_part_of_metadata(workdir):
    result = _generate(workdir, "ecpm_drop")
    # The evaluation artifact must never be referenced by the semantic/metadata
    # contract; the loader only reads manifest.json.
    manifest_text = (result.run_dir / "manifest.json").read_text(encoding="utf-8")
    assert "ground_truth" not in manifest_text
    for scenario in SCENARIOS:
        assert scenario in SCENARIOS
