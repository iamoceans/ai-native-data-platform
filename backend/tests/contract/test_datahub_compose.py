"""Security defaults for the pinned optional DataHub deployment."""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]


def test_datahub_has_no_implicit_host_aws_credentials():
    config = yaml.safe_load((ROOT / 'infra/datahub/compose.pinned.yaml').read_text())
    for service in config['services'].values():
        for mount in service.get('volumes', []):
            assert '.aws' not in str(mount), 'host AWS credentials must require explicit opt-in'


def test_all_datahub_published_ports_are_loopback_only():
    config = yaml.safe_load((ROOT / 'infra/datahub/compose.pinned.yaml').read_text())
    ports = [port for service in config['services'].values() for port in service.get('ports', [])]
    assert ports
    assert any(port['target'] == 4319 for port in ports)
    for port in ports:
        assert port.get('host_ip') == '127.0.0.1', port
