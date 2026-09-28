"""Spark connector contract without requiring an external Thrift service."""

from __future__ import annotations

import uuid

import pytest

from app.datasource.config_models import validate_connection_config
from app.metadata.recipes import build_recipe, platform_instance_for
from app.models.orm import Dataset, Datasource
from app.providers.base import ProviderCredentials, ValidatedQuery
from app.providers.spark import SparkOperationCancelled, SparkOperationTimedOut, SparkProvider


def _datasource() -> Datasource:
    return Datasource(
        id=uuid.uuid4(), name="spark-demo", kind="spark",
        connection_config={
            "host": "spark-thrift", "port": 10000, "database": "demo",
            "metastore_host": "hive-metastore", "metastore_port": 9083,
            "connect_timeout_seconds": 5,
        },
        secret_ref="spark-reader", capabilities={}, enabled=True,
        created_by=uuid.uuid4(),
    )


def test_spark_config_requires_shared_metastore_endpoint() -> None:
    config = _datasource().connection_config
    assert validate_connection_config("spark", config)["metastore_port"] == 9083
    with pytest.raises(Exception):
        validate_connection_config("spark", {key: value for key, value in config.items() if key != "metastore_host"})


def test_spark_hms_recipe_uses_one_hive_asset_identity(monkeypatch, tmp_path) -> None:
    datasource = _datasource()
    dataset = Dataset(
        id=uuid.uuid4(), datasource_id=datasource.id, catalog_name="spark_catalog",
        schema_name="demo", object_name="orders", object_type="table", active=True,
    )
    monkeypatch.setattr(
        "app.metadata.recipes.datasets_repo.list_datasets_for_datasource",
        lambda session, datasource_id: [dataset],
    )
    monkeypatch.setattr("app.metadata.recipes._common_properties", lambda *args: ({}, ["demo.orders"]))
    from app.config import Settings

    rendered = build_recipe(None, datasource=datasource, settings=Settings(metadata_dir=tmp_path))
    source = rendered.recipe["source"]
    assert source["type"] == "hive-metastore"
    assert source["config"]["connection_type"] == "thrift"
    assert source["config"]["host_port"] == "hive-metastore:9083"
    assert source["config"]["platform_instance"] == platform_instance_for(datasource)
    assert source["config"]["database_pattern"]["allow"] == ["^demo$"]
    assert rendered.expected_datasets == ["demo.orders"]


def test_spark_provider_rejects_client_side_parameter_binding() -> None:
    provider = SparkProvider(
        connection_config=_datasource().connection_config,
        credentials=ProviderCredentials(username="reader", password=""),
    )
    query = ValidatedQuery(
        query_id=uuid.uuid4(), sql="SELECT * FROM demo.orders WHERE id = :id",
        parameters={"id": "1"}, dataset_ids=(), schema_hashes={},
        policy_revision=1, max_rows=10, max_bytes=1000, timeout_seconds=5,
    )
    with pytest.raises(ValueError, match="parameterized"):
        provider.open_execution(query)


def test_spark_provider_uses_thrift_metadata_and_streams_rows(monkeypatch) -> None:
    class Cursor:
        description = [("id", "BIGINT")]

        def __init__(self):
            self.sql = None
            self.cancelled = False
            self.metadata_kind = ""
            self.batches = [[(1,), (2,)], []]

        def get_databases(self):
            self.metadata_kind = "databases"

        def get_tables(self, database_name):
            assert database_name == "demo"
            self.metadata_kind = "tables"

        def get_table_schema(self, table_name, database_name):
            assert (table_name, database_name) == ("orders", "demo")
            return [("id", "BIGINT")]

        def fetchall(self):
            return [("demo",)] if self.metadata_kind == "databases" else [(None, "demo", "orders", "TABLE")]

        def execute_async(self, sql):
            self.sql = sql

        def is_executing(self):
            return False

        def fetchmany(self, size):
            assert size == 1_000
            return self.batches.pop(0)

        def cancel_operation(self, reset_state=True):
            self.cancelled = True
            assert reset_state is False

        def close(self):
            pass

    class Connection:
        def __init__(self):
            self.last_cursor = None

        def cursor(self):
            self.last_cursor = Cursor()
            return self.last_cursor

        def close(self):
            pass

    connection = Connection()
    monkeypatch.setattr("app.providers.spark.connect", lambda **kwargs: connection)
    provider = SparkProvider(connection_config=_datasource().connection_config,
                             credentials=ProviderCredentials(username="reader", password=""))
    assert provider.list_tables(provider.list_namespaces()[0])[0].name == "orders"
    assert provider.describe_table(provider.list_tables(provider.list_namespaces()[0])[0]).columns[0].type == "bigint"
    query = ValidatedQuery(
        query_id=uuid.uuid4(), sql="SELECT id FROM demo.orders LIMIT 3", parameters={},
        dataset_ids=(), schema_hashes={}, policy_revision=1,
        max_rows=3, max_bytes=1000, timeout_seconds=5,
    )
    handle = provider.open_execution(query)
    assert list(provider.execute(handle, query)) == [[(1,), (2,)]]
    assert handle.columns[0].type_name == "bigint"
    assert provider.cancel(handle).confirmed is False
    assert connection.last_cursor.cancelled is True
    provider.close(handle)


def _cancelled_cursor_provider(monkeypatch, cursor):
    class Connection:
        def cursor(self):
            return cursor

        def close(self):
            pass

    monkeypatch.setattr("app.providers.spark.connect", lambda **kwargs: Connection())
    provider = SparkProvider(connection_config=_datasource().connection_config,
                             credentials=ProviderCredentials(username="reader", password=""))
    query = ValidatedQuery(
        query_id=uuid.uuid4(), sql="SELECT COUNT(*) FROM demo.orders", parameters={},
        dataset_ids=(), schema_hashes={}, policy_revision=1,
        max_rows=3, max_bytes=1000, timeout_seconds=5,
    )
    return provider, query, provider.open_execution(query)


def test_spark_cancel_translates_impyla_unknown_state(monkeypatch):
    """A cancel must be classified as one, not as a generic engine error.

    Impyla has no typed cancellation error: once the cancelled operation stops
    reporting a state, `is_executing()` raises `KeyError: None` from its own
    lookup. Untranslated, the executor would fail the job - and FAILED is not a
    legal transition out of CANCEL_REQUESTED, so the job would be resolved as
    LOST minutes later (observed on the live fixture 2026-09-28).
    """

    class Cursor:
        description = [("count(1)", "BIGINT")]

        def execute_async(self, sql):
            pass

        def is_executing(self):
            if self.cancelled:
                raise KeyError(None)
            return False

        def fetchmany(self, size):
            return []

        def cancel_operation(self, reset_state=True):
            self.cancelled = True

        def close(self):
            pass

    cursor = Cursor()
    cursor.cancelled = False
    provider, query, handle = _cancelled_cursor_provider(monkeypatch, cursor)

    assert list(provider.execute(handle, query)) == []
    assert provider.cancel(handle).confirmed is False
    with pytest.raises(SparkOperationCancelled) as caught:
        list(provider.execute(handle, query))
    assert provider.classify_execution_error(caught.value) == "cancelled"


def test_spark_unknown_state_without_a_cancel_stays_an_error(monkeypatch):
    """The same failure with no cancel pending is a real engine error."""

    class Cursor:
        description = []

        def execute_async(self, sql):
            pass

        def is_executing(self):
            raise KeyError(None)

        def fetchmany(self, size):
            return []

        def cancel_operation(self, reset_state=True):
            pass

        def close(self):
            pass

    provider, query, handle = _cancelled_cursor_provider(monkeypatch, Cursor())
    with pytest.raises(KeyError):
        list(provider.execute(handle, query))
    assert provider.classify_execution_error(KeyError(None)) == "error"


def test_spark_server_timeout_state_is_a_timeout(monkeypatch):
    """The server's TIMEDOUT_STATE (8) must classify as a timeout, not an error.

    `spark.sql.thriftServer.queryTimeout` makes the server report a state that
    impyla 0.24.0 cannot name, so `get_status()` raises `KeyError(8)`; without
    this mapping the platform recorded FAILED/EXECUTION_ERROR for a query it had
    itself timed out (observed on the live fixture 2026-09-28).
    """

    class Cursor:
        description = []

        def execute_async(self, sql):
            pass

        def is_executing(self):
            raise KeyError(8)

        def fetchmany(self, size):
            return []

        def cancel_operation(self, reset_state=True):
            pass

        def close(self):
            pass

    provider, query, handle = _cancelled_cursor_provider(monkeypatch, Cursor())
    with pytest.raises(SparkOperationTimedOut) as caught:
        list(provider.execute(handle, query))
    assert provider.classify_execution_error(caught.value) == "timeout"
