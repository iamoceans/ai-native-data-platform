#!/usr/bin/env python3
"""Load a generated demo run into Doris/MySQL/PostgreSQL (spec section 26.3).

Usage:
  python scripts/demo_load.py [--run-dir runtime/demo/<run>] [--reset-demo]
      [--doris-host 127.0.0.1] [--doris-sql-port 19030] [--doris-http-port 18030]
      [--mysql-host 127.0.0.1] [--mysql-port 33060]
      [--pg-host 127.0.0.1] [--pg-port 55433]

Reloading a non-empty demo run is refused unless --reset-demo is passed; the
reset only drops tables inside the `demo` database (plus the demo-owned config
tables campaign_config/app_release_config, which are truncated explicitly).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / "backend"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from demo.loaders import LoadTargets, load_run  # noqa: E402


def read_env(name: str, default: str | None = None) -> str | None:
    if os.environ.get(name):
        return os.environ[name]
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{name}="):
                return line.split("=", 1)[1].strip()
    return default


def latest_run(output_dir: Path) -> Path:
    if not output_dir.is_dir():
        raise SystemExit(f"no runs under {output_dir}; run make demo-generate first")
    candidates = [
        path
        for path in output_dir.iterdir()
        if path.is_dir() and (path / "manifest.json").is_file()
    ]
    if not candidates:
        raise SystemExit(f"no runs under {output_dir}; run make demo-generate first")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def main() -> int:
    parser = argparse.ArgumentParser(description="load a generated demo run")
    parser.add_argument("--run-dir", default=None, help="run directory (default: newest under runtime/demo)")
    parser.add_argument("--output-dir", default=str(ROOT / "runtime" / "demo"))
    parser.add_argument("--reset-demo", action="store_true", help="drop/recreate demo tables (explicit)")
    parser.add_argument("--doris-host", default=read_env("AIND_DEMO_DORIS_HOST", "127.0.0.1"))
    parser.add_argument("--doris-sql-port", type=int, default=int(read_env("DORIS_FE_SQL_PORT", "19030") or 19030))
    parser.add_argument("--doris-be-host", default=read_env("AIND_DEMO_DORIS_BE_HOST", "127.0.0.1"))
    parser.add_argument(
        "--doris-be-http-port",
        type=int,
        default=int(read_env("DORIS_BE_HTTP_PORT", "18040") or 18040),
    )
    parser.add_argument("--mysql-host", default=read_env("AIND_DEMO_MYSQL_HOST", "127.0.0.1"))
    parser.add_argument("--mysql-port", type=int, default=int(read_env("MYSQL_DB_PORT", "33060") or 33060))
    parser.add_argument("--pg-host", default=read_env("AIND_DEMO_PG_HOST", "127.0.0.1"))
    parser.add_argument("--pg-port", type=int, default=int(read_env("SOURCE_DB_PORT", "55433") or 55433))
    args = parser.parse_args()

    run_dir = Path(args.run_dir) if args.run_dir else latest_run(Path(args.output_dir))
    targets = LoadTargets(
        doris_host=args.doris_host,
        doris_sql_port=args.doris_sql_port,
        doris_be_host=args.doris_be_host,
        doris_be_http_port=args.doris_be_http_port,
        doris_user=read_env("DORIS_ADMIN_USER", "doris_admin") or "doris_admin",
        doris_password=read_env("DORIS_ADMIN_PASSWORD", "") or "",
        mysql_host=args.mysql_host,
        mysql_port=args.mysql_port,
        mysql_user=read_env("MYSQL_ADMIN_USER", "source_admin") or "source_admin",
        mysql_password=read_env("MYSQL_ADMIN_PASSWORD", "") or "",
        mysql_database=read_env("MYSQL_SOURCE_DB", "ainative_source") or "ainative_source",
        pg_host=args.pg_host,
        pg_port=args.pg_port,
        pg_user=read_env("SOURCE_BOOTSTRAP_USER", "source_admin") or "source_admin",
        pg_password=read_env("SOURCE_BOOTSTRAP_PASSWORD", "") or "",
        pg_database=read_env("SOURCE_DB_NAME", "ainative_source") or "ainative_source",
    )
    stats = load_run(run_dir=run_dir, targets=targets, reset_demo=args.reset_demo)
    print(json.dumps(stats, indent=2, ensure_ascii=False, default=str))
    print("demo-load complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
