#!/usr/bin/env python3
"""Generate the deterministic demo dataset (spec section 26).

Usage:
  python scripts/demo_generate.py [--seed 42] [--as-of 2026-09-13] [--days 90]
      [--scale small|medium] [--scenario ecpm_drop] [--output-dir runtime/demo]
      [--no-parquet] [--force]

Same (seed, as_of, days, scale, scenario, generator version) => same file
hashes. Ground truth stays in the run directory (evaluation only).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from demo.generator import generate  # noqa: E402
from demo.scenarios import SCENARIOS, scale_preset  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="generate the deterministic demo dataset")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--as-of", default="2026-09-13", help="UTC reference date (yesterday is the current period)")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--scale", choices=sorted({"small", "medium"}), default="small")
    parser.add_argument("--scenario", choices=SCENARIOS, default="ecpm_drop")
    parser.add_argument("--output-dir", default=str(ROOT / "runtime" / "demo"))
    parser.add_argument("--no-parquet", action="store_true", help="skip Parquet copies")
    parser.add_argument("--force", action="store_true", help="regenerate an existing run directory")
    args = parser.parse_args()

    preset = scale_preset(args.scale)
    print(
        f"generating scenario={args.scenario} scale={args.scale} seed={args.seed} "
        f"as_of={args.as_of} days={args.days} "
        f"({preset.combos_per_day()} ad cells/day)"
    )
    result = generate(
        output_dir=Path(args.output_dir),
        seed=args.seed,
        as_of=args.as_of,
        days=args.days,
        scale=args.scale,
        scenario=args.scenario,
        with_parquet=not args.no_parquet,
        force=args.force,
    )
    tables = result.manifest["tables"]
    total_rows = sum(
        entry["rows"] for entry in tables.values() if isinstance(entry.get("rows"), int)
    )
    for table, entry in sorted(tables.items()):
        if entry.get("derived"):
            print(f"  {table:<24} derived at load time ({entry['transform_id']})")
        else:
            print(f"  {table:<24} {entry['rows']:>9} rows  {entry['date_min']}..{entry['date_max']}")
    print(f"run directory: {result.run_dir}")
    print(f"fact rows: {total_rows}")
    print(
        "ground truth: "
        + json.dumps(
            {
                "baseline_total": result.ground_truth["baseline_total"],
                "current_total": result.ground_truth["current_total"],
                "delta": result.ground_truth["delta"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
