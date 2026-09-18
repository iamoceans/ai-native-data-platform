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


def check_ram() -> dict:
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
            return {"total_gib": human_gib(int(out))}
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        size = os.sysconf("SC_PAGE_SIZE")
        return {"total_gib": human_gib(pages * size)}
    except (ValueError, OSError, AttributeError):
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            match = re.search(r"MemTotal:\s+(\d+) kB", meminfo.read_text())
            if match:
                return {"total_gib": round(int(match.group(1)) / (1024**2), 1)}
    return {"total_gib": None, "error": "could not determine physical memory"}


def check_disk(path: Path) -> dict:
    usage = shutil.disk_usage(path)
    return {"path": str(path), "free_gib": human_gib(usage.free), "total_gib": human_gib(usage.total)}


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
    }

    docker_path = shutil.which("docker")
    report["docker"]["cli"] = docker_path or "missing"
    if docker_path:
        code, out = run([docker_path, "version", "--format", "{{.Client.Version}}"])
        report["docker"]["client_version"] = out if code == 0 else f"error: {out}"
        code, out = run([docker_path, "info", "--format", "{{.ServerVersion}}"])
        report["docker"]["server_version"] = out if code == 0 else None
        report["docker"]["daemon"] = code == 0
        code, out = run([docker_path, "compose", "version", "--short"])
        report["docker"]["compose_version"] = out if code == 0 else None

    for label, port in PORTS.items():
        report["ports"][label] = "in-use" if check_port(port) else "free"

    problems: list[str] = []
    if report["ram"]["total_gib"] is None or report["ram"]["total_gib"] < RESOURCE_BUDGET["core"]["ram_gib"]:
        problems.append(f"RAM below core budget ({RESOURCE_BUDGET['core']['ram_gib']} GiB)")
    if report["disk"]["repo"]["free_gib"] < RESOURCE_BUDGET["core"]["disk_gib"]:
        problems.append(f"repo disk free below core budget ({RESOURCE_BUDGET['core']['disk_gib']} GiB)")
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
        print(f"ram         : {report['ram'].get('total_gib')} GiB total   (core budget >= 6, full >= 32)")
        print(f"disk repo   : {report['disk']['repo']['free_gib']} GiB free of {report['disk']['repo']['total_gib']} GiB")
        docker = report["docker"]
        print(f"docker      : client={docker.get('client_version')} server={docker.get('server_version')} compose={docker.get('compose_version')}")
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
