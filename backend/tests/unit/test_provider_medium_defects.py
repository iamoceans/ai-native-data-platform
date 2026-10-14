"""Regressions for driver formatting, TLS policy and socket deadlines."""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from app.config import Settings
from app.providers.base import ProviderCredentials, ValidatedQuery
from app.providers.postgres import PostgresProvider
from app.providers.mysql import MySQLProvider
from app.providers.doris import DorisProvider
from app.providers.sql_prep import prepare_driver_sql


@pytest.mark.parametrize("kind,quote", [("postgres", '"'), ("mysql", '`'), ("doris", '`')])
def test_percent_scanner_escapes_comments_identifiers_and_fake_parameters(kind, quote):
    source = f"SELECT {quote}pct%{quote}, 'fake %(name)s', :name /* 10% %(name)s */ FROM t"
    prepared = prepare_driver_sql(source, kind=kind)
    # Emulate PyMySQL's scanner: fake tokens must remain literal SQL text.
    bound = prepared % {"name": "7"}
    assert f"{quote}pct%{quote}" in bound
    assert "'fake %(name)s'" in bound
    assert "10% %(name)s" in bound
    assert "7" in bound
    assert prepared.count("%(name)s") == 3  # two escaped, one actual binding


@pytest.mark.parametrize("kind", ["postgres", "mysql", "doris"])
def test_unbound_percent_sql_does_not_double(kind):
    prepared = prepare_driver_sql("SELECT '10%' /* 20% */", kind=kind)
    assert "10%" in prepared and "20%" in prepared
    assert "%%" not in prepared


@pytest.mark.parametrize("mode", ["disable", "prefer", "require", "verify-ca", "verify-full"])
def test_postgres_forwards_exact_tls_policy(monkeypatch, mode):
    connector = MagicMock()
    monkeypatch.setattr("app.providers.postgres.psycopg.connect", connector)
    provider = PostgresProvider(connection_config={"host": "localhost", "database": "db", "ssl_mode": mode}, credentials=ProviderCredentials("user", "secret"), settings=Settings())
    provider._connect()
    assert connector.call_args.kwargs["sslmode"] == mode


@pytest.mark.parametrize("provider_class,module", [(MySQLProvider, "mysql"), (DorisProvider, "doris")])
@pytest.mark.parametrize("execution", [False, True])
def test_mysql_protocol_socket_deadlines_are_bounded(monkeypatch, provider_class, module, execution):
    connector = MagicMock()
    connector.return_value.cursor.return_value.__enter__.return_value.fetchone.return_value = (1,)
    monkeypatch.setattr(f"app.providers.{module}.pymysql.connect", connector)
    settings = Settings(default_timeout_seconds=30)
    provider = provider_class(connection_config={"host": "localhost", "database": "db"}, credentials=ProviderCredentials("user", "secret"), settings=settings)
    if execution:
        query = ValidatedQuery(uuid4(), "SELECT 1", {}, (), {}, 1, 100, 10000, 75)
        provider.open_execution(query)
    else:
        provider._connect()
    expected = 80 if execution else 35
    assert connector.call_args.kwargs["read_timeout"] == expected
    assert connector.call_args.kwargs["write_timeout"] == expected


@pytest.mark.parametrize("kind", ["postgres", "mysql", "doris"])
def test_placeholder_sentinel_collision_and_repeated_bindings(kind):
    source = "SELECT '%%(__ainative_parameter_0)s', :a, :a, :b, 10 % 3 FROM t"
    bound = prepare_driver_sql(source, kind=kind) % {"a": "7", "b": "9"}
    assert "'%%(__ainative_parameter_0)s'" in bound
    assert ", 7, 7, 9," in bound
    assert "10 % 3" in bound
