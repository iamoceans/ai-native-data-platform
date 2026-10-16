"""Negative control on the actual unprotected PLAIN fixture (not authz proof)."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

from tests.spark_helpers import plain_provider

pytestmark = pytest.mark.integration


def test_configured_source_denies_all_seven_writes():
    reader = os.environ.get("AIND_TEST_SPARK_READONLY_READER_FILE")
    owner = os.environ.get("AIND_TEST_SPARK_READONLY_OWNER_FILE")
    if not reader and not owner:
        pytest.skip("Dedicated source authorization reader/owner files not configured")
    if not reader or not owner:
        pytest.fail("Both private source authorization files are required", pytrace=False)
    script = Path(__file__).resolve().parents[3] / "scripts" / "verify_spark_readonly.py"
    result = subprocess.run(
        [sys.executable, str(script), "--reader-config", reader, "--owner-config", owner],
        capture_output=True, text=True, timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_unprotected_spark_cannot_pass_source_readonly_acceptance(workdir):
    provider = plain_provider()
    # Fixture DDL/first Parquet write is setup, not a user query deadline.
    provider._config["connect_timeout_seconds"] = 30
    database = "aind_authz_" + uuid.uuid4().hex
    owner = provider._connect()
    admin = owner.cursor()
    try:
        admin.execute(f"CREATE DATABASE {database}")
        identity = provider._credentials
        config = {key: value for key, value in provider._config.items() if key != "connect_timeout_seconds"}
        config.update(database=database, user=identity.username, password=identity.password)
        path = workdir / "connection.json"
        with open(path, "x", opener=lambda name, flags: os.open(name, flags, 0o600)) as stream:
            json.dump(config, stream)
        script = Path(__file__).resolve().parents[3] / "scripts" / "verify_spark_readonly.py"
        result = subprocess.run([sys.executable, str(script), "--reader-config", str(path),
                                 "--owner-config", str(path)], capture_output=True, text=True, timeout=90)
        assert result.returncode == 1, result.stdout + result.stderr
        report = json.loads(result.stdout)
        assert report["passed"] is False and report["reason"] == "insert:write_allowed"
        assert not report["checks"]
        # Only verifier-owned names were used and cleanup ran even after an
        # unexpectedly allowed write. The dedicated namespace is empty again.
        admin.execute(f"SHOW TABLES IN {database}")
        assert admin.fetchall() == []
    finally:
        try:
            admin.execute(f"DROP DATABASE IF EXISTS {database} CASCADE")
        finally:
            admin.close()
            owner.close()
