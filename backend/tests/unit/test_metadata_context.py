"""Unit tests: metadata context cache behaviour and audience filtering.

The M3 defect fixed here was a cache key that ignored the DataHub URN: after an
ingestion mapped a dataset, the pre-sync context kept being served for up to the
5-minute TTL. These tests pin the fixed behaviour without needing a database
(spec sections 10.1/10.2).
"""

from __future__ import annotations

import types
import uuid

import pytest

from app.config import Settings
from app.metadata import service as metadata_service


def _dataset(urn: str | None = None):
    return types.SimpleNamespace(
        id=uuid.uuid4(),
        datasource_id=uuid.uuid4(),
        catalog_name="internal",
        schema_name="demo",
        object_name="ads_revenue_daily",
        object_type="table",
        datahub_urn=urn,
        sync_status="SYNCED",
        schema_hash="hash-1",
        active=True,
        last_synced_at=None,
    )


@pytest.fixture()
def context_env(monkeypatch, workdir):
    """Wire dataset_context to fakes; no database access."""
    metadata_service.reset_metadata_caches()
    monkeypatch.setattr(metadata_service, "ensure_dataset_action", lambda *a, **k: None)
    monkeypatch.setattr(metadata_service, "policy_repo", types.SimpleNamespace(get_revision=lambda s: 1))
    monkeypatch.setattr(
        metadata_service,
        "get_schema_view",
        lambda session, *, dataset, datasource, settings: metadata_service.SchemaView(
            columns=[{"name": "dt", "type": "date", "nullable": False}],
            schema_hash="hash-1",
            fetched_at=None,
            expires_at=None,
            source="snapshot",
            snapshot_id=None,
        ),
    )

    calls: list[str | None] = []

    def fake_build(session, *, dataset, settings, view, revision):
        calls.append(dataset.datahub_urn)
        return {
            "schema_version": 1,
            "dataset_id": dataset.id,
            "object_name": dataset.object_name,
            "datahub_urn": dataset.datahub_urn,
            "metadata_source": "datahub" if dataset.datahub_urn else "platform_registry",
            "metadata_stale": False,
            "metadata_cached": False,
            "datahub_url": (
                f"http://127.0.0.1:9002/dataset/{dataset.datahub_urn}"
                if dataset.datahub_urn
                else None
            ),
            "policy_revision": revision,
        }

    monkeypatch.setattr(metadata_service, "_build_context", fake_build)
    yield types.SimpleNamespace(calls=calls, settings=Settings(datahub_enabled=True))
    metadata_service.reset_metadata_caches()


def _context(dataset, env, *, include_link: bool):
    return metadata_service.dataset_context(
        session=None,
        role_ids=[],
        dataset=dataset,
        datasource=None,
        settings=env.settings,
        include_datahub_link=include_link,
    )


def test_context_cache_key_includes_datahub_urn(context_env):
    dataset = _dataset(urn=None)

    first = _context(dataset, context_env, include_link=False)
    assert first["metadata_cached"] is False
    assert first["metadata_source"] == "platform_registry"

    second = _context(dataset, context_env, include_link=False)
    assert second["metadata_cached"] is True
    assert len(context_env.calls) == 1

    # Catalog sync fills the URN: the cached pre-sync context must not be served.
    dataset.datahub_urn = "urn:li:dataset:(urn:li:dataPlatform:doris,ainative-x.demo.ads_revenue_daily,DEV)"
    third = _context(dataset, context_env, include_link=True)
    assert third["metadata_cached"] is False
    assert third["metadata_source"] == "datahub"
    assert len(context_env.calls) == 2


def test_datahub_deep_link_is_hidden_from_non_admins(context_env):
    urn = "urn:li:dataset:(urn:li:dataPlatform:doris,ainative-x.demo.ads_revenue_daily,DEV)"
    dataset = _dataset(urn=urn)

    non_admin = _context(dataset, context_env, include_link=False)
    assert non_admin["datahub_url"] is None

    # The non-admin call cached the payload; an admin call must still see the link.
    admin = _context(dataset, context_env, include_link=True)
    assert admin["datahub_url"] == f"{context_env.settings.datahub_frontend_url}/dataset/{urn}"
    assert admin["metadata_cached"] is True

    # And the reverse order must not leak the link to the next non-admin call.
    again = _context(dataset, context_env, include_link=False)
    assert again["datahub_url"] is None
