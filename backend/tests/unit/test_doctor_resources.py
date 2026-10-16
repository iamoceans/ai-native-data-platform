"""Host RAM alone must not let a limited runtime pass deployment preflight."""
import json

import pytest

from scripts import doctor

GIB = 1024**3


def hierarchy(workdir, group, values):
    root = workdir / "cgroup"
    root.mkdir()
    process = workdir / "process"
    process.write_text(group)
    for name, value in values.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value))
    return doctor.check_cgroup(root, process)


def test_v2_unlimited_child_still_obeys_parent(workdir):
    result = hierarchy(workdir, "0::/parent/child\n", {
        "memory.max": "max", "parent/memory.max": 4 * GIB,
        "parent/child/memory.max": "max", "parent/child/memory.current": 2 * GIB,
        "parent/child/memory.peak": 3 * GIB, "parent/child/memory.events": "oom 2\noom_kill 1\n",
    })
    assert result["limit_bytes"] == 4 * GIB
    assert result["current_bytes"] == 2 * GIB and result["peak_bytes"] == 3 * GIB
    assert result["events"]["oom_kill"] == 1


def test_namespaced_mount_root_is_used_when_host_path_is_hidden(workdir):
    result = hierarchy(workdir, "0::/hidden/container\n", {"memory.max": 8 * GIB})
    assert result["limit_bytes"] == 8 * GIB


def test_v1_memory_controller_and_unlimited_sentinel(workdir):
    result = hierarchy(workdir, "5:cpu:/ignored\n7:memory:/worker\n", {
        "memory/memory.limit_in_bytes": 2**63 - 4096,
        "memory/worker/memory.limit_in_bytes": 4 * GIB,
        "memory/worker/memory.usage_in_bytes": GIB,
    })
    assert result["version"] == 1 and result["limit_bytes"] == 4 * GIB


def test_zero_limit_is_not_mistaken_for_unlimited(workdir):
    assert hierarchy(workdir, "0::/\n", {"memory.max": 0})["limit_bytes"] == 0


def test_unavailable_cgroup_is_reported(workdir):
    assert doctor.check_cgroup(workdir / "missing", workdir / "missing-process")["scope"] == "unavailable"


def test_effective_ram_respects_cgroup_limit(monkeypatch):
    monkeypatch.setattr(doctor, "physical_ram", lambda: {"total_gib": 64.0, "total_bytes": 64 * GIB})
    monkeypatch.setattr(doctor, "check_cgroup", lambda: {"limit_bytes": 4 * GIB})
    assert doctor.check_ram()["effective_bytes"] == 4 * GIB


def report(process=64 * GIB, daemon=64 * GIB, disk=100):
    return {"ram": {"effective_bytes": process}, "docker": {"memory_bytes": daemon},
            "disk": {"repo": {"free_gib": disk}}}


@pytest.mark.parametrize("process,daemon", [(5 * GIB, 64 * GIB), (64 * GIB, 5 * GIB),
                                           (6 * GIB - 1, 64 * GIB), (0, 64 * GIB), (None, 64 * GIB)])
def test_core_rejects_low_or_unknown_process_capacity(process, daemon):
    assert doctor.resource_problems(report(process, daemon), "core")


def test_full_uses_full_budget_and_docker_vm_capacity():
    value = report(daemon=16 * GIB)
    assert not doctor.resource_problems(value, "core")
    assert "RAM" in doctor.resource_problems(value, "full")[0]
    assert "disk" in doctor.resource_problems(report(disk=50), "full")[0]


def test_disk_rounding_cannot_turn_insufficient_space_into_a_pass():
    value = report(disk=60.0)
    value["disk"]["repo"]["free_bytes"] = 60 * GIB - 1
    assert doctor.resource_problems(value, "full") == ["repo disk free below full budget (60 GiB)"]


def test_cli_strict_reports_selected_budget_without_starting_services(monkeypatch, capsys):
    monkeypatch.setattr(doctor.sys, "argv", ["doctor", "--profile", "full", "--strict", "--json"])
    monkeypatch.setattr(doctor, "check_ram", lambda: {"total_gib": 64.0, "effective_bytes": 64 * GIB})
    monkeypatch.setattr(doctor, "check_disk", lambda path: {"free_gib": 100})
    monkeypatch.setattr(doctor, "check_port", lambda port: False)
    monkeypatch.setattr(doctor, "check_secrets", lambda: {"env_file": True, "provider_secret_source_postgres": True})
    monkeypatch.setattr(doctor.shutil, "which", lambda name: "/docker")
    def run(command):
        return (0, json.dumps({"server_version": "28", "memory_bytes": 16 * GIB, "storage_driver": "vfs"})) if "info" in command else (0, "28")
    monkeypatch.setattr(doctor, "run", run)
    assert doctor.main() == 1
    output = json.loads(capsys.readouterr().out)
    assert output["report"]["selected_profile"] == "full"
    assert output["problems"] == ["effective RAM below full budget (32 GiB)"]
