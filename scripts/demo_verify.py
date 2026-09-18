#!/usr/bin/env python3
"""Verify a generated demo run against its ground truth (offline, spec 26.3).

Usage:
  python scripts/demo_verify.py [--run-dir runtime/demo/<run>] [--output-dir runtime/demo]

Checks (all deterministic, no services required):
  1. manifest file hashes still match the files on disk;
  2. totals/delta/change_pct recomputed by the kernel match ground truth;
  3. the scenario target group is found with the expected contribution;
  4. the impression x eCPM driver split matches ground truth;
  5. scenario-specific expectations (no_change, incomplete_day, schema_drift,
     config_duplicate, mixed_offset, canonical_67).

The same run against the live stack is exercised by the integration tests
(real SQL through the Query Gateway).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from app.analysis.compare import compare_totals  # noqa: E402
from app.analysis.contribution import decompose_contribution  # noqa: E402
from app.analysis.drivers import decompose_revenue  # noqa: E402
from app.analysis.join import join_results  # noqa: E402
from app.analysis.relations import get_relation  # noqa: E402
from app.analysis.types import AnalysisError  # noqa: E402

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}" + (f"  {detail}" if detail and not condition else ""))
    if not condition:
        FAILURES.append(label)


def dec_eq(left, right) -> bool:
    """Exact Decimal comparison across equivalent renderings ('-1000' vs '-1000.000000')."""
    if left is None or right is None:
        return False
    return Decimal(str(left)) == Decimal(str(right))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def latest_run(output_dir: Path) -> Path:
    candidates = [
        path
        for path in output_dir.iterdir()
        if path.is_dir() and (path / "manifest.json").is_file()
    ]
    if not candidates:
        raise SystemExit(f"no runs under {output_dir}; run make demo-generate first")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def aggregate_cells(run_dir: Path, dates: set[str]) -> dict:
    """Per-cell, per-day aggregates for the requested dates."""
    cells: dict[tuple[str, str, str, str], dict[str, dict[str, Decimal]]] = {}
    path = run_dir / "ads_revenue_daily.csv"
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row["dt"] not in dates:
                continue
            key = (row["country"], row["platform"], row["app_version"], row["ad_network"])
            day = cells.setdefault(key, {}).setdefault(
                row["dt"], {"revenue_usd": Decimal(0), "impressions": Decimal(0)}
            )
            day["revenue_usd"] += Decimal(row["revenue_usd"])
            day["impressions"] += Decimal(row["impressions"])
    return cells


def _cell_rows(cells, date: str) -> list[dict]:
    rows = []
    for key, slot in cells.items():
        day = slot.get(date)
        if day is None:
            continue
        rows.append(
            {
                "country": key[0],
                "platform": key[1],
                "app_version": key[2],
                "ad_network": key[3],
                "revenue_usd": str(day["revenue_usd"]),
                "impressions": str(day["impressions"]),
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="verify a generated demo run")
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--output-dir", default=str(ROOT / "runtime" / "demo"))
    args = parser.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else latest_run(Path(args.output_dir))
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    ground_truth = json.loads((run_dir / "ground_truth.json").read_text(encoding="utf-8"))
    scenario = ground_truth["scenario"]
    print(f"verifying {run_dir} (scenario={scenario})")

    # 1. manifest integrity
    hash_failures = 0
    for table, entry in manifest["tables"].items():
        for fmt, expected_hash in entry.get("hashes", {}).items():
            suffix = ".parquet" if fmt == "parquet" else ".csv"
            path = run_dir / f"{table}{suffix}"
            if not path.is_file() or sha256_file(path) != expected_hash:
                hash_failures += 1
    check(hash_failures == 0, "manifest file hashes match", f"{hash_failures} mismatch(es)")

    baseline_date = ground_truth["baseline_date"]
    current_date = ground_truth["current_date"]
    cells = aggregate_cells(run_dir, {baseline_date, current_date})
    baseline_rows = _cell_rows(cells, baseline_date)
    current_rows = _cell_rows(cells, current_date)

    # 2. totals through the kernel
    baseline_total = sum((Decimal(row["revenue_usd"]) for row in baseline_rows), Decimal(0))
    current_total = sum((Decimal(row["revenue_usd"]) for row in current_rows), Decimal(0))
    comparison = compare_totals(baseline_total, current_total)
    check(
        dec_eq(comparison.baseline, ground_truth["baseline_total"])
        and dec_eq(comparison.current, ground_truth["current_total"])
        and dec_eq(comparison.delta, ground_truth["delta"]),
        "totals match ground truth",
        f"kernel={comparison.delta} gt={ground_truth['delta']}",
    )

    # 3. contribution
    contribution = decompose_contribution(
        baseline_rows,
        current_rows,
        dimension=("country", "platform", "app_version", "ad_network"),
        value_column="revenue_usd",
        support_column="impressions",
        min_support=1000,
        merge_below_support=False,
    )
    check(contribution.checks["sum_matches_parent"], "group deltas add up to the parent delta")
    target = ground_truth.get("target")
    target_group = None
    if target:
        target_key = (target["country"], target["platform"], target["app_version"], target["ad_network"])
        target_group = next((group for group in contribution.groups if group.key == target_key), None)
        check(target_group is not None, "scenario target group exists in the partition")
    if target_group is not None and target:
        expected = ground_truth["expected"].get("target", {})
        check(
            dec_eq(target_group.delta, expected.get("delta")),
            "target delta matches ground truth",
            f"{target_group.delta} != {expected.get('delta')}",
        )
        driver_gt = expected.get("driver")
        if driver_gt:
            parsed = decompose_revenue(
                impressions_baseline=Decimal(expected["impressions_baseline"]),
                impressions_current=Decimal(expected["impressions_current"]),
                revenue_baseline=Decimal(expected["baseline"]),
                revenue_current=Decimal(expected["current"]),
            )
            check(
                dec_eq(parsed.impression_effect, driver_gt["impression_effect"])
                and dec_eq(parsed.ecpm_effect, driver_gt["ecpm_effect"]),
                "driver effects match ground truth",
                f"{parsed.as_dict()}",
            )

    # 4. scenario-specific expectations
    if scenario in {"ecpm_drop", "traffic_drop", "mixed_offset"} and target_group is not None:
        expected_target = ground_truth["expected"].get("target", {})
        parsed = decompose_revenue(
            impressions_baseline=Decimal(expected_target["impressions_baseline"]),
            impressions_current=Decimal(expected_target["impressions_current"]),
            revenue_baseline=Decimal(expected_target["baseline"]),
            revenue_current=Decimal(expected_target["current"]),
        )
        delta = abs(parsed.delta)
        if scenario == "ecpm_drop":
            check(
                abs(parsed.impression_effect) <= max(delta * Decimal("0.05"), Decimal(1)),
                "ecpm_drop: impressions stable (impression effect ~ 0)",
            )
            check(
                abs(parsed.ecpm_effect) >= delta * Decimal("0.9"),
                "ecpm_drop: eCPM effect dominates",
            )
        elif scenario == "traffic_drop":
            check(
                abs(parsed.ecpm_effect) <= max(delta * Decimal("0.05"), Decimal(1)),
                "traffic_drop: eCPM stable (ecpm effect ~ 0)",
            )
            check(
                abs(parsed.impression_effect) >= delta * Decimal("0.9"),
                "traffic_drop: impression effect dominates",
            )
        elif scenario == "mixed_offset":
            negatives = [group for group in contribution.groups if group.delta < 0]
            positives = [group for group in contribution.groups if group.delta > 0]
            check(bool(negatives) and bool(positives), "mixed_offset: both declines and offsets exist")
    elif scenario == "no_change":
        check(
            abs(Decimal(ground_truth["change_pct"] or "0")) < Decimal("0.02"),
            "no_change: total change is below the materiality threshold",
            ground_truth["change_pct"] or "0",
        )
    elif scenario == "incomplete_day":
        us_rows = [row for row in current_rows if row["country"] == "US"]
        check(us_rows == [], "incomplete_day: US partition missing on the current day")
        check(
            ground_truth["data_complete_through"] == baseline_date,
            "incomplete_day: data completeness stops at the baseline day",
        )
    elif scenario == "config_duplicate":
        config_rows = []
        with (run_dir / "campaign_config.csv").open("r", encoding="utf-8", newline="") as handle:
            config_rows = list(csv.DictReader(handle))
        duplicates = [row for row in config_rows if row["campaign_id"] == "cmp_001"]
        check(len(duplicates) >= 2, "config_duplicate: duplicate campaign configuration present")
        left = [{"campaign_id": "cmp_001", "cohort_date": current_date, "revenue_usd": "10"}]
        right = [
            {
                "campaign_id": row["campaign_id"],
                "valid_from": row["valid_from"],
                "valid_to": row["valid_to"] or None,
                "channel": row["channel"],
            }
            for row in config_rows
        ]
        relation = get_relation(ROOT / "metadata", "campaign_cohort_to_config")
        try:
            join_results(left, right, relation)
            check(False, "config_duplicate: join refuses duplicate keys")
        except AnalysisError as exc:
            check(
                exc.code == "JOIN_CARDINALITY_VIOLATION",
                "config_duplicate: join refuses duplicate keys",
                exc.code,
            )
    elif scenario == "schema_drift":
        drifted = run_dir / "schema_drift" / "ads_revenue_daily.csv"
        check(drifted.is_file(), "schema_drift: drifted CSV exists")
        if drifted.is_file():
            with drifted.open("r", encoding="utf-8", newline="") as handle:
                header = next(csv.reader(handle))
            check(
                "net_revenue_usd" in header and "revenue_usd" not in header,
                "schema_drift: column rename recorded",
            )
    elif scenario == "canonical_67":
        expected = ground_truth["expected"]
        check(ground_truth["baseline_total"] == "10000", "canonical_67: baseline total is 10,000")
        check(ground_truth["current_total"] == "9000", "canonical_67: current total is 9,000")
        check(ground_truth["delta"] == "-1000", "canonical_67: delta is -1,000")
        check(dec_eq(expected["target_delta"], "-670"), "canonical_67: target delta is -670")
        check(
            dec_eq(expected["target_net_change_share"], "0.67"),
            "canonical_67: net decline share is 0.67",
        )
        check(
            dec_eq(expected["target_contribution_pp"], "-6.7"),
            "canonical_67: contribution is -6.7 pp",
        )
        check(dec_eq(expected["impression_effect"], "0"), "canonical_67: impression effect is 0")
        check(dec_eq(expected["ecpm_effect"], "-670"), "canonical_67: eCPM effect is -670")
        check(
            dec_eq(expected["other_groups_delta"], "-330"),
            "canonical_67: other groups net -330",
        )
        check(str(comparison.change_pct) == "-0.1", "canonical_67: change_pct is -0.1")

    if FAILURES:
        print(f"\ndemo-verify: {len(FAILURES)} check(s) failed")
        return 1
    print("\ndemo-verify: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
