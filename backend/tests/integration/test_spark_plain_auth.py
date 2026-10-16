"""Real Spark CUSTOM authenticator via verified TLS and SASL PLAIN."""
import ssl
import uuid

import pytest
from thrift.transport.TTransport import TTransportException

from app.providers.base import ProviderCredentials, ValidatedQuery
from tests.spark_helpers import plain_provider

pytestmark = pytest.mark.integration


def test_authenticated_scalar_execution():
    provider = plain_provider()
    assert provider.test_connection().status == "HEALTHY"
    query = ValidatedQuery(
        query_id=uuid.uuid4(), sql="SELECT :value AS result, :amount AS amount",
        parameters={"value": "值'; SELECT secret FROM forbidden; --\\\n", "amount": -2.5},
        dataset_ids=(), schema_hashes={}, policy_revision=1,
        max_rows=10, max_bytes=10000, timeout_seconds=10,
    )
    handle = provider.open_execution(query)
    try:
        rows = [row for batch in provider.execute(handle, query) for row in batch]
        assert rows == [(query.parameters["value"], -2.5)]
    finally:
        provider.close(handle)


@pytest.mark.parametrize("invalid", ["username", "password", "empty-password"])
def test_invalid_identity_is_rejected_during_sasl(invalid):
    provider = plain_provider()
    original = provider._credentials
    provider._credentials = ProviderCredentials(
        "unregistered-fixture-user" if invalid == "username" else original.username,
        {"password": "incorrect-fixture-password", "empty-password": ""}.get(invalid, original.password),
    )
    # A rejected SASL handshake never creates an HS2 session or runs a query.
    with pytest.raises(TTransportException, match="Bad status: 3 .*Error validating the login"):
        connection = provider._connect()
        connection.close()
    assert provider.test_connection().status == "UNAVAILABLE"


def test_plain_requires_trusted_server_certificate():
    provider = plain_provider()
    provider._config["ca_cert"] = None
    # Thrift's outer message is generic; inspect the retained TLS cause so a
    # connection refusal/timeout cannot masquerade as certificate verification.
    with pytest.raises(TTransportException) as rejected:
        connection = provider._connect()
        connection.close()
    assert isinstance(rejected.value.inner, ssl.SSLCertVerificationError)
