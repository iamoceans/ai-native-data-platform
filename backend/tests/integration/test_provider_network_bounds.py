"""Real sockets for stalled handshakes and an isolated PostgreSQL TLS service."""
from __future__ import annotations

import os
import socket
import threading
import time

import pymysql
import pytest

from app.config import Settings
from app.providers.base import ProviderCredentials
from app.providers.doris import DorisProvider
from app.providers.mysql import MySQLProvider
from app.providers.postgres import PostgresProvider
from tests.helpers import parse_url

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("provider_class", [MySQLProvider, DorisProvider])
def test_stalled_mysql_protocol_greeting_has_a_finite_socket_deadline(provider_class):
    accepted, release = threading.Event(), threading.Event()
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(10)
        def stall():
            with listener.accept()[0]:
                accepted.set()
                release.wait(15)
        server = threading.Thread(target=stall, daemon=True)
        server.start()
        provider = provider_class(connection_config={"host": "127.0.0.1", "port": listener.getsockname()[1], "database": "test", "connect_timeout_seconds": 1},
            credentials=ProviderCredentials("unused", "unused"), settings=Settings(default_timeout_seconds=1))
        started = time.monotonic()
        try:
            with pytest.raises(pymysql.OperationalError):
                provider._connect(timeout_seconds=1)
            elapsed = time.monotonic() - started
            assert accepted.is_set()
            assert 0.5 < elapsed < 12  # 1-second deadline + 5-second transport grace.
        finally:
            release.set()
            server.join(timeout=2)
        assert not server.is_alive()


def test_postgres_provider_negotiates_real_tls_when_required():
    url = os.environ.get("AIND_TEST_TLS_SOURCE_URL")
    if not url:
        pytest.skip("AIND_TEST_TLS_SOURCE_URL not configured")
    parts = parse_url(url)
    provider = PostgresProvider(connection_config={"host": parts["host"], "port": parts["port"], "database": parts["database"], "ssl_mode": "require", "connect_timeout_seconds": 3},
        credentials=ProviderCredentials(parts["user"], parts["password"]), settings=Settings())
    with provider._connect() as connection:
        assert connection.execute("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()").fetchone() == (True,)
        assert connection.execute("SHOW transaction_read_only").fetchone() == ("on",)
