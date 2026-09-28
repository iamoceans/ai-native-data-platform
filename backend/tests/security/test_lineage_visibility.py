"""DataHub lineage cannot reveal datasets without a platform grant."""

from __future__ import annotations

import uuid

from app.config import Settings
from app.metadata.datahub import DataHubLineageEdge, DataHubLineageNode, DataHubLineageResult
from app.metadata.service import dataset_lineage
from app.models.orm import Dataset


def test_external_and_ungranted_lineage_nodes_are_hidden(monkeypatch) -> None:
    root_urn = "urn:li:dataset:(urn:li:dataPlatform:hive,root.orders,PROD)"
    granted_urn = "urn:li:dataset:(urn:li:dataPlatform:hive,root.customers,PROD)"
    external_urn = "urn:li:dataset:(urn:li:dataPlatform:hive,other.payroll,PROD)"
    root = Dataset(id=uuid.uuid4(), datasource_id=uuid.uuid4(), catalog_name="spark_catalog",
                   schema_name="root", object_name="orders", active=True, datahub_urn=root_urn)
    granted = Dataset(id=uuid.uuid4(), datasource_id=root.datasource_id, catalog_name="spark_catalog",
                      schema_name="root", object_name="customers", active=True, datahub_urn=granted_urn)
    lineage = DataHubLineageResult(
        nodes=[DataHubLineageNode(granted_urn, "customers", "hive", 1),
               DataHubLineageNode(external_urn, "payroll", "hive", 1)],
        edges=[DataHubLineageEdge(granted_urn, root_urn, 1),
               DataHubLineageEdge(external_urn, root_urn, 1)],
    )
    monkeypatch.setattr("app.metadata.service.ensure_dataset_action", lambda *args, **kwargs: None)
    monkeypatch.setattr("app.metadata.service._analysis_evidence", lambda *args: [])
    monkeypatch.setattr("app.metadata.service._declared_upstreams", lambda *args: set())
    monkeypatch.setattr("app.metadata.service._map_urns", lambda *args: {granted_urn: granted})
    monkeypatch.setattr("app.metadata.service.datasets_repo.dataset_ids_with_action",
                        lambda *args: {granted.id})
    monkeypatch.setattr("app.metadata.service.DataHubAdapter.get_lineage",
                        lambda *args, **kwargs: lineage)
    result = dataset_lineage(None, role_ids=[], dataset=root, settings=Settings(datahub_enabled=True))
    assert [node["urn"] for node in result["nodes"]] == [granted_urn]
    assert [edge["source"] for edge in result["edges"]] == [granted_urn]
    assert result["filtered_nodes"] == 1
