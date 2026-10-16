"""Optional real PLAIN-over-TLS fixture; credentials stay in a private file."""
import json
import os
from pathlib import Path

import pytest

from app.providers.base import ProviderCredentials
from app.providers.spark import SparkProvider


def plain_provider() -> SparkProvider:
    host = os.environ.get("AIND_TEST_SPARK_PLAIN_HOST")
    if not host:
        pytest.skip("AIND_TEST_SPARK_PLAIN_HOST not configured")
    path = os.environ.get("AIND_TEST_SPARK_PLAIN_CREDENTIALS_FILE")
    ca = os.environ.get("AIND_TEST_SPARK_PLAIN_CA_CERT")
    if not path or not ca:
        pytest.fail("PLAIN acceptance requires private credentials file and CA path", pytrace=False)
    identity = json.loads(Path(path).read_text(encoding="utf-8"))
    return SparkProvider(
        connection_config={
            "host": host,
            "port": int(os.environ.get("AIND_TEST_SPARK_PLAIN_PORT", "11002")),
            "database": "default", "connect_timeout_seconds": 10,
            "use_ssl": True, "ca_cert": ca, "auth_mechanism": "PLAIN",
        },
        credentials=ProviderCredentials(identity["username"], identity["password"]),
    )
