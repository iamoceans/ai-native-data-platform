"""Metadata service (spec sections 10, 11).

M3 catalog source: DataHub is the authoritative catalog for search, context and
lineage; the platform database keeps the asset mapping and the execution
contract. Live schema for SQL validation still comes from controlled
introspection of the registered source (spec 10.2: every execution validates the
real schema), so a stale catalog can never widen what a query may read.

Cache: context is cached for `datahub_metadata_cache_ttl_seconds` (default 5
minutes) keyed by dataset id + schema hash + policy revision. When DataHub is
unreachable, only within-TTL entries are served (marked `metadata_stale=true`);
expired entries are not fabricated.
"""

from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy.orm import Session

from app.auth.rbac import ensure_dataset_action
from app.config import Settings
from app.constants import DatasetAction, DatasetSyncStatus, ErrorCode, SCHEMA_VERSION
from app.datasource.secrets import SecretResolver
from app.datasource.service import build_provider
from app.errors import ApiError
from app.ids import utcnow
from app.metadata.datahub import DataHubAdapter, DataHubEntity, DataHubUnavailable
from app.metadata.semantic import SemanticRegistry, dataset_qualifier, load_semantic_registry
from app.models.orm import Dataset, Datasource
from app.providers.base import ProviderCredentials, TableRef, TableSchema
from app.repositories import datasets as datasets_repo
from app.repositories import policy as policy_repo

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchemaView:
    columns: list[dict]
    schema_hash: str
    fetched_at: object
    expires_at: object
    source: str
    snapshot_id: uuid.UUID | None


@dataclass(frozen=True)
class RefreshResult:
    registered: int
    updated: int
    deactivated: int
    datasets: list[Dataset]
    skipped: list[dict]


# --------------------------------------------------------------------------
# cache
# --------------------------------------------------------------------------
@dataclass
class _CacheEntry:
    payload: dict
    expires_at: float


_context_cache: dict[tuple[str, str, int], _CacheEntry] = {}
_cache_lock = threading.Lock()

_semantic_cache: SemanticRegistry | None = None
_semantic_lock = threading.Lock()


def semantic_registry(settings: Settings) -> SemanticRegistry:
    global _semantic_cache
    with _semantic_lock:
        if _semantic_cache is None:
            _semantic_cache = load_semantic_registry(settings.metadata_dir)
        return _semantic_cache


def reset_metadata_caches() -> None:
    """Test hook: clear context and semantic caches."""
    global _semantic_cache
    with _cache_lock:
        _context_cache.clear()
    with _semantic_lock:
        _semantic_cache = None


def _resolver(settings: Settings) -> SecretResolver:
    return SecretResolver(settings.secrets_dir)


def _provider(datasource: Datasource, settings: Settings):
    credentials = _resolver(settings).resolve(datasource.secret_ref)
    return build_provider(datasource, credentials, settings)


def _columns_payload(schema: TableSchema) -> list[dict]:
    return [
        {"name": column.name, "type": column.type, "nullable": column.nullable}
        for column in schema.columns
    ]


def _sensitive_columns(columns: list[dict], pattern: str) -> list[str]:
    compiled = re.compile(pattern, re.IGNORECASE)
    return [column["name"] for column in columns if compiled.search(column["name"])]


# --------------------------------------------------------------------------
# catalog registration (M1/M2 path, still the mapping source)
# --------------------------------------------------------------------------
def refresh_catalog(
    session: Session,
    *,
    datasource: Datasource,
    settings: Settings,
    schemas: list[str],
    secure_views: list[str] | None = None,
) -> RefreshResult:
    """Register/refresh platform datasets from live source introspection.

    Applies the V1 sensitive-data rule (spec 12.3): base tables whose columns
    match the sensitive pattern are not registered; views are registered only
    when the administrator explicitly confirms them as secure views, and the
    confirmation basis (plus a definition hash when the provider offers one) is
    recorded in the dataset metadata snapshot.
    """
    confirmed_views = set(secure_views or [])
    provider = _provider(datasource, settings)
    # The administrator lists the selectable unit per engine: schema for
    # PostgreSQL, database for MySQL/Doris.
    namespaces = [
        namespace
        for namespace in provider.list_namespaces()
        if (namespace.schema or namespace.catalog) in schemas
    ]
    if not namespaces:
        raise ApiError(
            ErrorCode.METADATA_UNAVAILABLE,
            "no matching schemas found on the datasource",
            details={"schemas": schemas},
        )
    registered = 0
    updated = 0
    kept: set[uuid.UUID] = set()
    datasets_out: list[Dataset] = []
    skipped: list[dict] = []
    for namespace in namespaces:
        for table in provider.list_tables(namespace):
            schema = provider.describe_table(table)
            columns = _columns_payload(schema)
            sensitive = _sensitive_columns(columns, settings.sensitive_column_patterns)
            if table.object_type == "table" and sensitive:
                skipped.append(
                    {
                        "schema": table.schema,
                        "name": table.name,
                        "reason": "SENSITIVE_COLUMNS",
                        "columns": sensitive,
                    }
                )
                continue
            if table.object_type == "view":
                if table.name not in confirmed_views:
                    skipped.append(
                        {
                            "schema": table.schema,
                            "name": table.name,
                            "reason": "VIEW_REQUIRES_CONFIRMATION",
                        }
                    )
                    continue
                definition = None
                get_definition = getattr(provider, "view_definition", None)
                if callable(get_definition):
                    try:
                        definition = get_definition(table)
                    except Exception:  # introspection failure must not abort the refresh
                        definition = None
                security = {
                    "basis": "admin_confirmed_secure_view",
                    "definition_hash": (
                        hashlib.sha256(definition.encode("utf-8")).hexdigest()
                        if definition
                        else None
                    ),
                    "confirmed_by": "admin",
                }
                row, created = datasets_repo.upsert_dataset(
                    session,
                    datasource_id=datasource.id,
                    catalog_name=table.catalog,
                    schema_name=table.schema,
                    object_name=table.name,
                    object_type=table.object_type,
                    schema_hash=schema.schema_hash,
                    sync_status=DatasetSyncStatus.SYNCED,
                )
                datasets_repo.create_snapshot(
                    session,
                    dataset_id=row.id,
                    schema_version=SCHEMA_VERSION,
                    content={
                        "schema_version": SCHEMA_VERSION,
                        "columns": columns,
                        "object_type": "view",
                        "security": security,
                    },
                    schema_hash=schema.schema_hash,
                    fetched_at=utcnow(),
                    expires_at=utcnow() + timedelta(seconds=settings.metadata_snapshot_ttl_seconds),
                )
                if created:
                    registered += 1
                else:
                    updated += 1
                kept.add(row.id)
                datasets_out.append(row)
                continue
            row, created = datasets_repo.upsert_dataset(
                session,
                datasource_id=datasource.id,
                catalog_name=table.catalog,
                schema_name=table.schema,
                object_name=table.name,
                object_type=table.object_type,
                schema_hash=schema.schema_hash,
                sync_status=DatasetSyncStatus.SYNCED,
            )
            if created:
                registered += 1
            else:
                updated += 1
            kept.add(row.id)
            datasets_out.append(row)
    deactivated = datasets_repo.deactivate_missing(session, datasource.id, kept)
    return RefreshResult(
        registered=registered,
        updated=updated,
        deactivated=deactivated,
        datasets=datasets_out,
        skipped=skipped,
    )


# --------------------------------------------------------------------------
# live schema (execution-time truth)
# --------------------------------------------------------------------------
def get_schema_view(
    session: Session,
    *,
    dataset: Dataset,
    datasource: Datasource,
    settings: Settings,
    force_refresh: bool = False,
) -> SchemaView:
    if not force_refresh:
        snapshot = datasets_repo.latest_snapshot(session, dataset.id)
        if snapshot is not None and snapshot.expires_at > utcnow():
            return SchemaView(
                columns=list(snapshot.content.get("columns", [])),
                schema_hash=snapshot.schema_hash,
                fetched_at=snapshot.fetched_at,
                expires_at=snapshot.expires_at,
                source="snapshot",
                snapshot_id=snapshot.id,
            )
    provider = _provider(datasource, settings)
    ref = TableRef(
        catalog=dataset.catalog_name,
        schema=dataset.schema_name,
        name=dataset.object_name,
        object_type=dataset.object_type,
    )
    schema = provider.describe_table(ref)
    now = utcnow()
    expires_at = now + timedelta(seconds=settings.metadata_snapshot_ttl_seconds)
    content = {
        "schema_version": SCHEMA_VERSION,
        "physical_ref": {
            "catalog": dataset.catalog_name,
            "schema": dataset.schema_name,
            "name": dataset.object_name,
        },
        "columns": _columns_payload(schema),
        "object_type": dataset.object_type,
    }
    snapshot = datasets_repo.create_snapshot(
        session,
        dataset_id=dataset.id,
        schema_version=SCHEMA_VERSION,
        content=content,
        schema_hash=schema.schema_hash,
        fetched_at=now,
        expires_at=expires_at,
    )
    dataset.schema_hash = schema.schema_hash
    session.flush()
    return SchemaView(
        columns=content["columns"],
        schema_hash=schema.schema_hash,
        fetched_at=now,
        expires_at=expires_at,
        source="introspection",
        snapshot_id=snapshot.id,
    )


# --------------------------------------------------------------------------
# search (DataHub + registry, permission filtered)
# --------------------------------------------------------------------------
def search_datasets(
    session: Session,
    *,
    role_ids: list[uuid.UUID],
    query: str | None,
    datasource_id: uuid.UUID | None,
    limit: int,
    offset: int,
    settings: Settings | None = None,
) -> tuple[list[Dataset], int, bool]:
    """Permission-filtered catalog search.

    When DataHub is enabled its search is merged with the platform registry
    (mapped datasets only), capped at the configured candidate budget; the
    reported total counts authorized datasets only.
    """
    registry_rows, _ = datasets_repo.list_datasets(
        session, datasource_id=datasource_id, query=query, limit=10_000, offset=0
    )
    candidates: dict[uuid.UUID, Dataset] = {row.id: row for row in registry_rows}

    if settings is not None and settings.datahub_enabled and query:
        for row in _datahub_mapped_datasets(session, settings, query):
            if datasource_id is not None and row.datasource_id != datasource_id:
                continue
            candidates.setdefault(row.id, row)

    allowed = datasets_repo.dataset_ids_with_action(session, role_ids, DatasetAction.DISCOVER)
    filtered = [row for row in candidates.values() if row.id in allowed and row.active]
    filtered.sort(key=lambda row: (row.object_name, str(row.id)))
    total = len(filtered)
    page = filtered[offset : offset + limit]
    return page, total, total > offset + limit


def _datahub_mapped_datasets(
    session: Session, settings: Settings, query: str
) -> list[Dataset]:
    adapter = DataHubAdapter(settings)
    try:
        page = adapter.search(query, start=0, count=settings.datahub_search_candidate_limit)
    except DataHubUnavailable as exc:
        logger.warning("DataHub search unavailable: %s", exc.message)
        return []
    urns = [entity.urn for entity in page.entities if entity.urn]
    if not urns:
        return []
    rows = (
        session.query(Dataset)
        .filter(Dataset.datahub_urn.in_(urns), Dataset.active.is_(True))
        .all()
    )
    return list(rows)


# --------------------------------------------------------------------------
# context (registry + semantic yaml + DataHub)
# --------------------------------------------------------------------------
def dataset_summary(row: Dataset) -> dict:
    return {
        "id": row.id,
        "datasource_id": row.datasource_id,
        "catalog": row.catalog_name,
        "schema_name": row.schema_name,
        "object_name": row.object_name,
        "object_type": row.object_type,
        "datahub_urn": row.datahub_urn,
        "sync_status": row.sync_status,
        "schema_hash": row.schema_hash,
        "active": row.active,
        "last_synced_at": row.last_synced_at,
    }


def dataset_context(
    session: Session,
    *,
    role_ids: list[uuid.UUID],
    dataset: Dataset,
    datasource: Datasource,
    settings: Settings,
    include_datahub_link: bool = False,
) -> dict:
    ensure_dataset_action(
        session, role_ids=role_ids, dataset=dataset, action=DatasetAction.DISCOVER
    )
    view = get_schema_view(session, dataset=dataset, datasource=datasource, settings=settings)
    revision = policy_repo.get_revision(session)
    # The DataHub URN is part of the key: a catalog sync must invalidate cached
    # contexts (otherwise a pre-sync "platform_registry" context would be served
    # for up to the TTL after mapping - observed on the reference stack).
    cache_key = (str(dataset.id), view.schema_hash, int(revision), str(dataset.datahub_urn or ""))

    now = time.monotonic()
    with _cache_lock:
        entry = _context_cache.get(cache_key)
        if entry is not None and entry.expires_at > now:
            cached = dict(entry.payload)
            cached["metadata_cached"] = True
            return _for_audience(cached, include_datahub_link=include_datahub_link)

    context = _build_context(
        session, dataset=dataset, settings=settings, view=view, revision=revision
    )
    with _cache_lock:
        _context_cache[cache_key] = _CacheEntry(
            payload=context,
            expires_at=now + settings.datahub_metadata_cache_ttl_seconds,
        )
    cached = dict(context)
    cached["metadata_cached"] = False
    return _for_audience(cached, include_datahub_link=include_datahub_link)


def _for_audience(context: dict, *, include_datahub_link: bool) -> dict:
    """Spec 10.1: the DataHub UI deep link is admin-only until DataHub's own
    authorization is configured and verified; analysts use the platform's
    controlled detail view. The cached payload keeps the link so an admin call
    is not downgraded by a preceding analyst call."""
    if not include_datahub_link:
        context.pop("datahub_url", None)
        context["datahub_url"] = None
    return context


def _build_context(
    session: Session,
    *,
    dataset: Dataset,
    settings: Settings,
    view: SchemaView,
    revision: int,
) -> dict:
    qualifier = dataset_qualifier(dataset.catalog_name, dataset.schema_name, dataset.object_name)
    semantic = semantic_registry(settings).for_dataset(qualifier)
    context = dataset_summary(dataset)
    context.update(
        {
            "schema_version": SCHEMA_VERSION,
            "description": semantic.description if semantic else None,
            "columns": view.columns,
            "grain": semantic.grain if semantic else None,
            "business_timezone": semantic.business_timezone if semantic else None,
            "currency": semantic.currency if semantic else None,
            "metric_keys": semantic.metric_keys if semantic else [],
            "join_keys": semantic.join_keys if semantic else [],
            "lineage_status": "unknown",
            "metadata_source": "platform_registry",
            "metadata_stale": False,
            "metadata_cached": False,
            "datahub_url": None,
            "owners": [],
            "policy_revision": revision,
        }
    )
    if not settings.datahub_enabled or not dataset.datahub_urn:
        context["lineage_status"] = "not_ingested" if settings.datahub_enabled else "unknown"
        return context

    adapter = DataHubAdapter(settings)
    try:
        entity = adapter.get_dataset(dataset.datahub_urn)
    except DataHubUnavailable as exc:
        logger.warning("DataHub context unavailable: %s", exc.message)
        context["metadata_stale"] = True
        context["lineage_status"] = "unavailable"
        return context
    if entity is None:
        context["metadata_stale"] = True
        context["lineage_status"] = "not_ingested"
        return context

    context.update(
        {
            "metadata_source": "datahub",
            "datahub_url": f"{settings.datahub_frontend_url}/dataset/{dataset.datahub_urn}",
            "owners": [owner.username or owner.urn for owner in entity.owners],
        }
    )
    if entity.description:
        context["description"] = entity.description
    _apply_custom_properties(context, entity)
    if entity.schema_hash and entity.schema_hash != view.schema_hash:
        # The catalog schema differs from the live schema: keep the live schema
        # for execution and surface the drift instead of hiding it.
        context["schema_drift"] = True
        context["datahub_schema_hash"] = entity.schema_hash
    return context


def _apply_custom_properties(context: dict, entity: DataHubEntity) -> None:
    properties = entity.custom_properties
    if properties.get("ainative.grain"):
        context["grain"] = [part for part in properties["ainative.grain"].split(",") if part]
    if properties.get("ainative.business_timezone"):
        context["business_timezone"] = properties["ainative.business_timezone"]
    if properties.get("ainative.currency"):
        context["currency"] = properties["ainative.currency"]
    if properties.get("ainative.metric_keys"):
        context["metric_keys"] = [
            part for part in properties["ainative.metric_keys"].split(",") if part
        ]
    if properties.get("ainative.join_keys"):
        context["join_keys"] = [part for part in properties["ainative.join_keys"].split(",") if part]


# --------------------------------------------------------------------------
# lineage (spec section 11)
# --------------------------------------------------------------------------
def dataset_lineage(
    session: Session,
    *,
    role_ids: list[uuid.UUID],
    dataset: Dataset,
    settings: Settings,
    direction: str = "upstream",
    depth: int = 1,
) -> dict:
    ensure_dataset_action(
        session, role_ids=role_ids, dataset=dataset, action=DatasetAction.DISCOVER
    )
    direction_up = direction.lower() == "upstream"
    result: dict = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": str(dataset.id),
        "direction": "upstream" if direction_up else "downstream",
        "depth": max(1, min(int(depth), 2)),
        "status": "not_ingested",
        "nodes": [],
        "edges": [],
        "filtered_nodes": 0,
        "labels": [],
        "analysis_evidence": _analysis_evidence(session, dataset),
    }

    if not settings.datahub_enabled:
        result["status"] = "unavailable"
        result["message"] = "DataHub integration is disabled in this deployment"
        return result
    if not dataset.datahub_urn:
        result["message"] = "this dataset has no DataHub URN yet; run a catalog sync"
        return result

    adapter = DataHubAdapter(settings)
    try:
        lineage_result = adapter.get_lineage(
            dataset.datahub_urn,
            direction="UPSTREAM" if direction_up else "DOWNSTREAM",
            depth=result["depth"],
        )
    except DataHubUnavailable as exc:
        result["status"] = "unavailable"
        result["message"] = exc.message
        return result
    nodes = lineage_result.nodes

    allowed = datasets_repo.dataset_ids_with_action(session, role_ids, DatasetAction.DISCOVER)
    mapped = _map_urns(session, {node.urn for node in nodes})
    declared_sources = _declared_upstreams(adapter, dataset.datahub_urn)
    visible: list[dict] = []
    filtered = 0
    for node in nodes:
        row = mapped.get(node.urn)
        if row is None or row.id not in allowed:
            filtered += 1
            continue
        qualifier = dataset_qualifier(row.catalog_name, row.schema_name, row.object_name)
        visible.append(
            {
                "urn": node.urn,
                "dataset_id": str(row.id),
                "name": row.object_name,
                "namespace": row.schema_name or row.catalog_name,
                "platform": node.platform,
                "degree": node.degree,
                "label": (
                    "declared_by_demo_pipeline"
                    if qualifier in declared_sources
                    else "extracted"
                ),
                "mapped": True,
            }
        )
    for node in nodes:
        if mapped.get(node.urn) is None:
            # Unregistered upstream/downstream assets are reported without a
            # platform dataset id (they are visible in DataHub only).
            visible.append(
                {
                    "urn": node.urn,
                    "dataset_id": None,
                    "name": node.name,
                    "namespace": None,
                    "platform": node.platform,
                    "degree": node.degree,
                    "label": "external",
                    "mapped": False,
                }
            )
    visible_urns = {node["urn"] for node in visible} | {dataset.datahub_urn}
    labels_by_urn = {node["urn"]: node["label"] for node in visible}
    result["edges"] = [
        {
            "source": edge.source,
            "target": edge.target,
            "degree": edge.degree,
            "label": labels_by_urn.get(edge.source, "extracted"),
        }
        for edge in lineage_result.edges
        if edge.source in visible_urns and edge.target in visible_urns
    ]
    result["nodes"] = visible
    result["filtered_nodes"] = filtered
    result["labels"] = sorted({node["label"] for node in visible})
    if not visible and filtered == 0:
        result["status"] = "no_upstream" if direction_up else "no_downstream"
        result["message"] = "no lineage captured in DataHub for this asset"
    elif not visible and filtered:
        result["status"] = "permission_filtered"
        result["message"] = "all related assets are outside your permissions"
    else:
        result["status"] = "available"
    return result


def _declared_upstreams(adapter: DataHubAdapter, target_urn: str) -> set[str]:
    """Dataset qualifiers the demo pipeline declared as upstream (spec 11).

    The label is read back from the custom property the publisher wrote, never
    inferred from naming conventions.
    """
    try:
        entity = adapter.get_dataset(target_urn)
    except DataHubUnavailable:
        return set()
    if entity is None:
        return set()
    raw = entity.custom_properties.get("ainative.declared_upstreams") or ""
    return {part for part in raw.split(",") if part}


def _map_urns(session: Session, urns: set[str]) -> dict[str, Dataset]:
    if not urns:
        return {}
    rows = session.query(Dataset).filter(Dataset.datahub_urn.in_(urns)).all()
    return {row.datahub_urn: row for row in rows if row.datahub_urn}


def _analysis_evidence(session: Session, dataset: Dataset) -> list[dict]:
    """Platform-side evidence edges: datasets consumed by executed queries.

    Kept separate from data-processing lineage (spec section 11): this is the
    platform's own execution history, not a transformation declaration.
    """
    from app.models.orm import QueryDependency, QueryJob

    rows = (
        session.query(QueryJob, QueryDependency)
        .join(QueryDependency, QueryDependency.query_id == QueryJob.id)
        .filter(QueryDependency.dataset_id == dataset.id, QueryJob.status == "SUCCEEDED")
        .order_by(QueryJob.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "query_id": str(job.id),
            "label": "analysis_evidence",
            "status": job.status,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
        for job, _dependency in rows
    ]
