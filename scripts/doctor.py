#!/usr/bin/env python3
"""Host preflight check (M0 deliverable).

Verifies the machine, Docker, ports and secrets needed by the core stack and
prints a report that can be pasted into docs/compatibility.md as evidence.

Stdlib only (runs before the backend environment exists).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PORTS = {
    "api (8000)": 8000,
    "frontend (3000)": 3000,
    "datahub ui (9002)": 9002,
    "control db (55432)": 55432,
    "source db (55433)": 55433,
}

RESOURCE_BUDGET = {
    "core": {"ram_gib": 6, "disk_gib": 20},
    "full": {"ram_gib": 32, "disk_gib": 60},
}


def run(cmd: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, shell=False
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return proc.returncode, out.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def human_gib(value: int) -> float:
    return round(value / (1024**3), 1)


def physical_ram() -> dict:
    if os.name == "nt":
        code, out = run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory",
            ]
        )
        if code == 0 and out.isdigit():
            return {"total_gib": human_gib(int(out)), "total_bytes": int(out)}
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return {"total_gib": human_gib(pages * size), "total_bytes": pages * size}
    except (ValueError, OSError, AttributeError):
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            match = re.search(r"MemTotal:\s+(\d+) kB", meminfo.read_text())
            if match:
                total = int(match.group(1)) * 1024
                return {"total_gib": human_gib(total), "total_bytes": total}
    return {"total_gib": None, "error": "could not determine physical memory"}


def check_cgroup(root: Path = Path("/sys/fs/cgroup"), process: Path = Path("/proc/self/cgroup")) -> dict:
    """Read the visible Linux process memory hierarchy; never modify limits."""
    def read(path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError:
            return None

    def number(path: Path) -> int | None:
        value = read(path)
        return int(value) if value and value.isdigit() else None

    for entry in (read(process) or "").splitlines():
        fields = entry.split(":", 2)
        if len(fields) != 3:
            continue
        _, controllers, group = fields
        if controllers == "":
            base, version = root, 2
            limit_name, usage_name, peak_name = "memory.max", "memory.current", "memory.peak"
        elif "memory" in controllers.split(","):
            base, version = root / "memory", 1
            limit_name = "memory.limit_in_bytes"
            usage_name, peak_name = "memory.usage_in_bytes", "memory.max_usage_in_bytes"
        else:
            continue
        base = base.resolve()
        leaf = (base / group.lstrip("/")).resolve()
        if not leaf.is_relative_to(base) or not leaf.is_dir():
            # A container may expose its own group as the mount root.
            leaf = base
        if not (leaf / limit_name).is_file():
            continue
        limits = []
        current = leaf
        while True:
            limit = number(current / limit_name)
            # v1 uses a near-LONG_MAX sentinel for an unlimited group.
            if limit is not None and 0 <= limit < 2**60:
                limits.append(limit)
            if current == base:
                break
            current = current.parent
        result = {
            "version": version, "scope": "visible process memory hierarchy",
            "limit_bytes": min(limits) if limits else None,
            "current_bytes": number(leaf / usage_name), "peak_bytes": number(leaf / peak_name),
        }
        if version == 2:
            result["events"] = {
                key: int(value) for line in (read(leaf / "memory.events") or "").splitlines()
                for key, _, value in [line.partition(" ")] if value.isdigit()
            }
        return result
    return {"version": None, "scope": "unavailable", "limit_bytes": None}


def check_ram() -> dict:
    report = physical_ram()
    report["cgroup"] = check_cgroup()
    capacities = [value for value in (report.get("total_bytes"), report["cgroup"]["limit_bytes"])
                  if isinstance(value, int) and value >= 0]
    report["effective_bytes"] = min(capacities) if capacities else None
    report["effective_gib"] = human_gib(min(capacities)) if capacities else None
    return report


def resource_problems(report: dict, profile: str) -> list[str]:
    budget = RESOURCE_BUDGET[profile]
    capacities = [report["ram"].get("effective_bytes"), report["docker"].get("memory_bytes")]
    capacities = [value for value in capacities if isinstance(value, int) and value >= 0]
    problems = []
    if report["ram"].get("effective_bytes") is None or not capacities or min(capacities) < budget["ram_gib"] * 1024**3:
        problems.append(f"effective RAM below {profile} budget ({budget['ram_gib']} GiB)")
    disk = report["disk"]["repo"]
    free_bytes = disk.get("free_bytes", disk["free_gib"] * 1024**3)
    if free_bytes < budget["disk_gib"] * 1024**3:
        problems.append(f"repo disk free below {profile} budget ({budget['disk_gib']} GiB)")
    return problems


def check_disk(path: Path) -> dict:
    usage = shutil.disk_usage(path)
    return {"path": str(path), "free_gib": human_gib(usage.free), "total_gib": human_gib(usage.total),
            "free_bytes": usage.free, "total_bytes": usage.total}


def check_port(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def check_secrets() -> dict:
    env_file = ROOT / ".env"
    local_secrets = ROOT / "infra" / "local-secrets"
    provider_secret = local_secrets / "source-postgres.json"
    return {
        "env_file": env_file.exists(),
        "provider_secret_source_postgres": provider_secret.exists(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="AI-Native Data Platform doctor")
    parser.add_argument("--json", action="store_true", help="print machine-readable report only")
    parser.add_argument("--strict", action="store_true", help="exit 1 when a required check fails")
    parser.add_argument("--profile", choices=tuple(RESOURCE_BUDGET), default="core", help="resource budget to check")
    args = parser.parse_args()

    report: dict = {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "cpu_count": os.cpu_count(),
        "ram": check_ram(),
        "disk": {
            "repo": check_disk(ROOT),
            "cwd": check_disk(Path.cwd()),
        },
        "docker": {},
        "ports": {},
        "secrets": check_secrets(),
        "budget": RESOURCE_BUDGET,
        "selected_profile": args.profile,
    }

    docker_path = shutil.which("docker")
    report["docker"]["cli"] = docker_path or "missing"
    if docker_path:
        code, out = run([docker_path, "version", "--format", "{{.Client.Version}}"])
        report["docker"]["client_version"] = out if code == 0 else f"error: {out}"
        code, out = run([docker_path, "info", "--format",
                         '{"server_version":"{{.ServerVersion}}","memory_bytes":{{.MemTotal}},"storage_driver":"{{.Driver}}"}'])
        if code == 0:
            try:
                report["docker"].update(json.loads(out))
            except (ValueError, TypeError):
                code = 1
        report["docker"]["daemon"] = code == 0
        code, out = run([docker_path, "compose", "version", "--short"])
        report["docker"]["compose_version"] = out if code == 0 else None

    for label, port in PORTS.items():
        report["ports"][label] = "in-use" if check_port(port) else "free"

    problems = resource_problems(report, args.profile)
    if not report["docker"].get("daemon"):
        problems.append("Docker daemon is not reachable")
    for label, state in report["ports"].items():
        if state == "in-use":
            problems.append(f"port {label} is already in use")
    if not report["secrets"]["env_file"]:
        problems.append("missing .env (run `make setup-secrets`)")
    if not report["secrets"]["provider_secret_source_postgres"]:
        problems.append("missing infra/local-secrets/source-postgres.json (run `make setup-secrets`)")

    if args.json:
        print(json.dumps({"report": report, "problems": problems}, indent=2))
    else:
        print("== AI-Native Data Platform doctor ==")
        print(f"host        : {report['platform']['system']} {report['platform']['release']} ({report['platform']['machine']})")
        print(f"cpu         : {report['cpu_count']} logical cores")
        print(f"ram         : {report['ram'].get('total_gib')} GiB physical, {report['ram'].get('effective_gib')} GiB process capacity")
        print(f"profile     : {args.profile} (RAM >= {RESOURCE_BUDGET[args.profile]['ram_gib']} GiB, disk >= {RESOURCE_BUDGET[args.profile]['disk_gib']} GiB)")
        print(f"disk repo   : {report['disk']['repo']['free_gib']} GiB free of {report['disk']['repo']['total_gib']} GiB")
        docker = report["docker"]
        print(f"docker      : client={docker.get('client_version')} server={docker.get('server_version')} compose={docker.get('compose_version')}")
        if isinstance(docker.get("memory_bytes"), int):
            print(f"docker ram  : {human_gib(docker['memory_bytes'])} GiB daemon capacity; driver={docker.get('storage_driver')}")
        print("ports       : " + ", ".join(f"{k}={v}" for k, v in report["ports"].items()))
        print(f"secrets     : {report['secrets']}")
        if problems:
            print("\nPROBLEMS:")
            for item in problems:
                print(f"  - {item}")
        else:
            print("\nAll checks passed.")
        print("\nNote: this report is evidence for docs/compatibility.md; paste it there when it changes.")

    if args.strict and problems:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
