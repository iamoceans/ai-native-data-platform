"""Real Spark scalar binding and verified TLS; optional standalone fixtures."""
import os
import uuid
import pytest
from app.providers.base import ProviderCredentials, ValidatedQuery
from app.providers.spark import SparkProvider

pytestmark=pytest.mark.integration


def _provider(tls=False,trusted=True):
    prefix='AIND_TEST_SPARK_TLS' if tls else 'AIND_TEST_SPARK'
    host=os.environ.get(prefix+'_HOST')
    if not host:pytest.skip(prefix+'_HOST not configured')
    config={'host':host,'port':int(os.environ.get(prefix+'_PORT','11001' if tls else '11000')),'database':'default','connect_timeout_seconds':3}
    if tls:config.update(use_ssl=True,ca_cert=os.environ.get(prefix+'_CA_CERT') if trusted else None)
    return SparkProvider(connection_config=config,credentials=ProviderCredentials('fixture-reader',''))


@pytest.mark.parametrize('tls',[False,True])
def test_scalar_binding_survives_real_spark_execution(tls):
    provider=_provider(tls)
    value="a\\b'; SELECT secret FROM forbidden; --\n值"
    query=ValidatedQuery(query_id=uuid.uuid4(),sql='SELECT :value AS result, :amount AS amount',
        parameters={'value':value,'amount':-2.5},dataset_ids=(),schema_hashes={},policy_revision=1,max_rows=10,max_bytes=10000,timeout_seconds=10)
    handle=provider.open_execution(query)
    try:
        rows=[row for batch in provider.execute(handle,query) for row in batch]
        assert rows==[(value,-2.5)]
    finally:provider.close(handle)


def test_untrusted_spark_server_certificate_is_rejected():
    provider=_provider(True,False)
    with pytest.raises(Exception):provider._connect()
