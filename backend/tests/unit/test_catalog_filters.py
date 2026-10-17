"""Catalog filtering by datasource, schema and declared business domain.

The domain filter reads what metadata/semantic declares; nothing is inferred
from table or schema names. These tests pin the filter semantics - including the
reserved value selecting datasets without a declaration - without a database.
"""

from __future__ import annotations

import types
import uuid
from pathlib import Path

import pytest

from app.config import Settings
from app.constants import DOMAIN_UNDECLARED
from app.metadata import service as metadata_service
from app.metadata.semantic import load_semantic_registry
from app.models.orm import Dataset

ROOT = Path(__file__).resolve().parents[3]


def _write_semantic(directory: Path) -> Path:
    semantic = directory / "semantic"
    semantic.mkdir(parents=True, exist_ok=True)
    (semantic / "ads_revenue_daily.yaml").write_text(
        "dataset: demo.ads_revenue_daily\n"
        "domain: 广告变现\n"
        "metric_keys: [ads_revenue]\n",
        encoding="utf-8",
    )
    (semantic / "user_daily.yaml").write_text(
        "dataset: demo.user_daily\ndomain: 用户增长\ngrain: [dt]\n",
        encoding="utf-8",
    )
    (semantic / "app_release_config.yaml").write_text(
        "dataset: ainative_source.app_release_config\ndomain: 版本发布\n",
        encoding="utf-8",
    )
    return directory


def _row(
    schema: str, name: str, datasource_id: uuid.UUID, catalog: str = "internal"
) -> Dataset:
    return Dataset(
        id=uuid.uuid4(),
        datasource_id=datasource_id,
        catalog_name=catalog,
        schema_name=schema,
        object_name=name,
        object_type="table",
        sync_status="SYNCED",
        active=True,
    )


@pytest.fixture()
def catalog(monkeypatch, workdir):
    metadata_service.reset_metadata_caches()
    settings = Settings(metadata_dir=_write_semantic(workdir))
    source_pg, source_mysql = uuid.uuid4(), uuid.uuid4()
    granted = [
        _row("demo", "ads_revenue_daily", source_pg),
        _row("demo", "user_daily", source_pg),
        _row("demo", "retention_daily", source_pg),
        _row("public", "fixture_metrics", source_pg),
        # MySQL has no schema level: the catalog is the namespace.
        _row("", "app_release_config", source_mysql, catalog="ainative_source"),
    ]
    hidden = _row("public", "payroll", source_pg)

    def fake_list(session, *, datasource_id, query, limit, offset, namespace=None):
        selected = [row for row in granted + [hidden] if row.active]
        if datasource_id is not None:
            selected = [row for row in selected if row.datasource_id == datasource_id]
        if namespace is not None:
            selected = [
                row
                for row in selected
                if (row.schema_name or row.catalog_name) == namespace
            ]
        if query:
            selected = [row for row in selected if query in row.object_name]
        selected.sort(key=lambda row: (row.object_name, str(row.id)))
        return selected[offset : offset + limit], len(selected)

    allowed = {row.id for row in granted}
    names = {source_pg: "源库 PG", source_mysql: "演示 MySQL"}
    monkeypatch.setattr(metadata_service.datasets_repo, "list_datasets", fake_list)
    monkeypatch.setattr(
        metadata_service.datasets_repo, "dataset_ids_with_action", lambda *args: allowed
    )
    monkeypatch.setattr(
        metadata_service.datasources_repo,
        "list_by_ids",
        lambda session, ids: sorted(
            (
                types.SimpleNamespace(id=item, name=names[item], kind="postgres")
                for item in ids
            ),
            key=lambda item: item.name,
        ),
    )
    yield types.SimpleNamespace(
        settings=settings,
        granted=granted,
        hidden=hidden,
        source_pg=source_pg,
        source_mysql=source_mysql,
    )
    metadata_service.reset_metadata_caches()


def _search(catalog, **overrides):
    kwargs = {
        "session": None,
        "role_ids": [],
        "query": None,
        "datasource_id": None,
        "limit": 20,
        "offset": 0,
        "settings": catalog.settings,
    }
    kwargs.update(overrides)
    return metadata_service.search_datasets(**kwargs)


def test_semantic_domain_is_loaded_and_published_to_datahub(workdir) -> None:
    registry = load_semantic_registry(_write_semantic(workdir))
    ads = registry.for_dataset("demo.ads_revenue_daily")
    users = registry.for_dataset("demo.user_daily")
    assert ads is not None and ads.domain == "广告变现"
    assert registry.as_custom_properties(ads)["ainative.domain"] == "广告变现"
    assert registry.as_custom_properties(users)["ainative.domain"] == "用户增长"


def test_shipped_semantic_contracts_all_declare_a_domain() -> None:
    """The reference dataset set is fully classified, so the filter is usable."""
    registry = load_semantic_registry(ROOT / "metadata")
    assert registry.entries
    undeclared = [
        qualifier for qualifier, entry in registry.entries.items() if not entry.domain
    ]
    assert undeclared == []


def test_domain_filter_selects_declared_and_undeclared_rows(catalog) -> None:
    declared = _search(catalog, domain="广告变现")[0]
    assert [row.object_name for row in declared] == ["ads_revenue_daily"]

    grouped = _search(catalog, domain="用户增长")[0]
    assert [row.object_name for row in grouped] == ["user_daily"]

    undeclared = _search(catalog, domain=DOMAIN_UNDECLARED)[0]
    assert [row.object_name for row in undeclared] == ["fixture_metrics", "retention_daily"]

    every = _search(catalog)[0]
    assert [row.object_name for row in every] == [
        "ads_revenue_daily",
        "app_release_config",
        "fixture_metrics",
        "retention_daily",
        "user_daily",
    ]


def test_namespace_filter_reaches_engines_without_a_schema(catalog) -> None:
    """MySQL rows carry an empty schema_name; their namespace is the catalog."""
    assert [row.object_name for row in _search(catalog, namespace="public")[0]] == [
        "fixture_metrics"
    ]
    assert [
        row.object_name for row in _search(catalog, namespace="ainative_source")[0]
    ] == ["app_release_config"]
    # The catalog-level filter must not shadow the namespace in the same value.
    assert _search(catalog, namespace="internal")[0] == []


def test_schema_and_datasource_filters_combine_with_domain(catalog) -> None:
    combined = _search(
        catalog, datasource_id=catalog.source_pg, domain=DOMAIN_UNDECLARED
    )[0]
    assert [row.object_name for row in combined] == ["fixture_metrics", "retention_daily"]

    joined = _search(
        catalog, datasource_id=catalog.source_mysql, domain="版本发布"
    )[0]
    assert [row.object_name for row in joined] == ["app_release_config"]


def test_total_counts_the_filtered_rows_not_the_page(catalog) -> None:
    page, total, has_more = _search(catalog, domain=DOMAIN_UNDECLARED, limit=1)
    assert [row.object_name for row in page] == ["fixture_metrics"]
    assert total == 2
    assert has_more is True

    _, expected_total, _ = _search(catalog, domain=DOMAIN_UNDECLARED, limit=1, offset=1)
    assert expected_total == 2


def test_facets_count_only_granted_rows_and_ignore_the_page(catalog) -> None:
    facets = metadata_service.dataset_facets(
        session=None, role_ids=[], settings=catalog.settings
    )
    assert facets["schemas"] == [
        {"value": "ainative_source", "count": 1},
        {"value": "demo", "count": 3},
        {"value": "public", "count": 1},
    ]
    # Facets keep a deterministic order (code point), not a collation: the
    # dropdown must not shuffle between reloads on the same data.
    assert facets["domains"] == [
        {"value": "广告变现", "count": 1},
        {"value": "版本发布", "count": 1},
        {"value": "用户增长", "count": 1},
    ]
    assert facets["undeclared_domain"] == 2
    assert [item["name"] for item in facets["datasources"]] == ["源库 PG", "演示 MySQL"]
    assert [item["count"] for item in facets["datasources"]] == [4, 1]
    assert "payroll" not in str(facets)


def test_dataset_summary_carries_the_declared_domain(catalog) -> None:
    rows = _search(catalog)[0]
    domains = metadata_service.declared_domains(catalog.settings, rows)
    summary = {
        row.object_name: metadata_service.dataset_summary(
            row, domain=domains[row.id]
        )["domain"]
        for row in rows
    }
    assert summary["ads_revenue_daily"] == "广告变现"
    assert summary["retention_daily"] is None


def test_catalog_route_passes_filters_and_serves_facets(monkeypatch) -> None:
    """`schema`/`domain` reach the service; /datasets/facets is not a UUID route."""
    from fastapi.testclient import TestClient

    from app.api.deps import current_auth, get_db, get_settings_dep
    from app.main import create_app

    seen: dict[str, object] = {}
    monkeypatch.setattr(
        metadata_service,
        "search_datasets",
        lambda session, **kwargs: seen.update(kwargs) or ([], 0, False),
    )
    monkeypatch.setattr(metadata_service, "declared_domains", lambda settings, rows: {})
    monkeypatch.setattr(
        metadata_service,
        "dataset_facets",
        lambda session, **kwargs: {
            "datasources": [],
            "schemas": [{"value": "demo", "count": 2}],
            "domains": [{"value": "广告变现", "count": 1}],
            "undeclared_domain": 1,
        },
    )
    app = create_app()
    app.dependency_overrides[current_auth] = lambda: types.SimpleNamespace(
        user=types.SimpleNamespace(id=uuid.uuid4()), role_ids=[], role_names=[]
    )
    app.dependency_overrides[get_db] = lambda: None
    app.dependency_overrides[get_settings_dep] = lambda: Settings()

    with TestClient(app, raise_server_exceptions=False) as client:
        listing = client.get(
            "/api/v1/datasets",
            params={"schema": "demo", "domain": "广告变现", "q": "revenue"},
        )
        facets = client.get("/api/v1/datasets/facets")

    assert listing.status_code == 200
    assert seen["namespace"] == "demo"
    assert seen["domain"] == "广告变现"
    assert seen["query"] == "revenue"
    assert facets.status_code == 200
    assert facets.json()["domains"] == [{"value": "广告变现", "count": 1}]
    assert facets.json()["undeclared_domain"] == 1
