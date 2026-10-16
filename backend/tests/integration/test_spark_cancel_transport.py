"""Real Spark/Impyla RPC overlap; optional standalone Spark 3.5.3 fixture."""
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.providers.base import ProviderCredentials, ValidatedQuery
from app.providers.spark import SparkProvider
from tests.spark_helpers import plain_provider

pytestmark = pytest.mark.integration


@pytest.fixture(params=["NOSASL", "PLAIN"])
def provider(request):
    if request.param == "PLAIN":
        return plain_provider()
    host = os.environ.get('AIND_TEST_SPARK_HOST')
    if not host:
        pytest.skip('AIND_TEST_SPARK_HOST not configured')
    return SparkProvider(connection_config={'host': host, 'port': int(os.environ.get('AIND_TEST_SPARK_PORT', '10000')),
        'database': 'default', 'connect_timeout_seconds': 3}, credentials=ProviderCredentials('priority-check', ''))


def query(sql):
    return ValidatedQuery(query_id=uuid.uuid4(), sql=sql, parameters={}, dataset_ids=(), schema_hashes={},
        policy_revision=1, max_rows=10, max_bytes=10000, timeout_seconds=10)


def test_cancel_does_not_steal_an_inflight_status_response(provider):
    statement = query('SELECT sum(sqrt(id)) FROM range(1000000000)')
    handle = provider.open_execution(statement)
    cursor = handle.native['cursor']
    cursor.execute_async(statement.sql)
    protocol = cursor.session.client._iprot
    original = protocol.readMessageBegin
    entered, release = threading.Event(), threading.Event()
    paused = False
    def read():
        nonlocal paused
        if threading.current_thread().name.startswith('spark-status') and not paused:
            paused = True; entered.set(); assert release.wait(8)
        return original()
    protocol.readMessageBegin = read
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix='spark-status') as pool:
            pending = pool.submit(cursor.is_executing)
            try:
                assert entered.wait(5)
                outcome = provider.cancel(handle)
                assert outcome.detail == 'Spark cancel requested'
            finally:
                release.set()
            assert isinstance(pending.result(timeout=5), bool)
        protocol.readMessageBegin = original
        deadline = time.monotonic() + 5
        while cursor._last_operation.get_status() != 'CANCELED_STATE':
            assert time.monotonic() < deadline
            threading.Event().wait(0.05)
        # The execution transport still parses subsequent RPCs correctly.
        assert cursor._last_operation.get_status() == 'CANCELED_STATE'
    finally:
        release.set(); protocol.readMessageBegin = original
        provider.close(handle)
    healthy = query('SELECT 1 AS ok')
    handle = provider.open_execution(healthy)
    try:
        assert list(provider.execute(handle, healthy)) == [[(1,)]]
    finally:
        provider.close(handle)
