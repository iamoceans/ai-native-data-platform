"""Opt-in authentication-enabled actual GMS acceptance, independent of Doris.

Unset URL skips; explicitly configured but unhealthy/misconfigured GMS fails.
"""
import os
from pathlib import Path

import pytest

from app.config import Settings
from app.metadata.datahub import DataHubAdapter
from scripts.datahub_token import token_from_file, verify_auth

pytestmark = pytest.mark.integration


def test_gms_denies_anonymous_invalid_and_accepts_valid_token():
    url = os.environ.get("AIND_TEST_DATAHUB_AUTH_URL")
    if not url:
        pytest.skip("AIND_TEST_DATAHUB_AUTH_URL not configured")
    token_path = os.environ.get("AIND_TEST_DATAHUB_AUTH_TOKEN_FILE")
    if not token_path:
        pytest.fail("Explicit GMS auth acceptance requires its private token file", pytrace=False)
    token = token_from_file(Path(token_path))
    ok, message = verify_auth(url, token)
    assert ok, message
    # Also exercise the actual platform adapter and its pinned search document.
    DataHubAdapter(Settings(datahub_gms_url=url, datahub_token=token)).search("*", count=1)
