"""Ingestion recipe construction (spec section 10.1).

Recipes are materialized from the registered datasource + the platform's
dataset registry, with credentials passed as environment variables at run time
(``${SOURCE_USERNAME}``/``${SOURCE_PASSWORD}``/``${DATAHUB_TOKEN}``) so secrets
never land in recipe files on disk.

The recipe dict is validated against the locked DataHub SDK by running
``datahub ingest -c <recipe> --dry-run`` in the ingestion image (contract test),
so an unknown or renamed option fails loudly instead of being silently ignored.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from app.config import Settings
from app.constants import ErrorCode, SourceKind
from app.errors import ApiError
from app.metadata.semantic import SemanticRegistry, dataset_qualifier, load_semantic_registry
from app.models.orm import Dataset, Datasource
from app.repositories import datasets as datasets_repo

FABRIC_TYPES = {"DEV", "PROD", "TEST", "CORP"}


@dataclass(frozen=True)
class RenderedRecipe:
    recipe: dict[str, Any]
    recipe_hash: str
    platform_instance: str
    expected_datasets: list[str]  # "namespace.table" qualifiers


def platform_instance_for(datasource: Datasource) -> str:
    return f"ainative-{str(datasource.id)[:8]}"


def pipeline_name_for(datasource: Datasource) -> str:
    """Required by DataHub when stateful ingestion is enabled (verified against
    the locked SDK: without it the pipeline fails to configure)."""
    return f"ainative-{datasource.kind}-{str(datasource.id)[:8]}"


def _escape_regex(value: str) -> str:
    return re.escape(value)


def _patterns(names: list[str]) -> dict[str, list[str]]:
    return {"allow": [f"^{_escape_regex(name)}$" for name in sorted(set(names))], "deny": []}


def _table_patterns(datasets: list[Dataset]) -> dict[str, list[str]]:
    """Exact-match whitelist for both bare and qualified table names.

    DataHub matches `table_pattern` against the name as the source reports it;
    SQL sources with a schema/database prefix report `namespace.table`
    (verified on the reference stack: bare patterns matched nothing, so the
    whitelist covers both forms explicitly).
    """
    names: set[str] = set()
    for dataset in datasets:
        namespace = dataset.schema_name or dataset.catalog_name
        names.add(dataset.object_name)
        if namespace:
            names.add(f"{namespace}.{dataset.object_name}")
    return _patterns(sorted(names))


def _common_properties(
    session: Session, datasource: Datasource, settings: Settings
) -> tuple[dict[str, str], list[str]]:
    """Semantic custom properties keyed by the dataset qualifier.

    Published through the SDK after ingestion (the sink recipe config cannot
    express per-dataset properties), so the builder returns them for the runner.
    """
    registry: SemanticRegistry = load_semantic_registry(settings.metadata_dir)
    properties: dict[str, str] = {}
    expected: list[str] = []
    datasets = datasets_repo.list_datasets_for_datasource(session, datasource.id)
    for dataset in datasets:
        if not dataset.active:
            continue
        qualifier = dataset_qualifier(dataset.catalog_name, dataset.schema_name, dataset.object_name)
        expected.append(qualifier)
        entry = registry.for_dataset(qualifier)
        if entry is not None:
            properties[qualifier] = json.dumps(registry.as_custom_properties(entry), sort_keys=True)
    return properties, expected


def build_recipe(
    session: Session,
    *,
    datasource: Datasource,
    settings: Settings,
) -> RenderedRecipe:
    datasets: list[Dataset] = [
        row for row in datasets_repo.list_datasets_for_datasource(session, datasource.id) if row.active
    ]
    if not datasets:
        raise ApiError(
            ErrorCode.METADATA_UNAVAILABLE,
            "no registered datasets on this datasource; run a catalog refresh before syncing",
        )
    platform_instance = platform_instance_for(datasource)
    pipeline_name = pipeline_name_for(datasource)
    env = settings.env.upper()
    fabric = env if env in FABRIC_TYPES else "PROD"
    config = datasource.connection_config
    host_port = f"{config['host']}:{config['port']}"
    custom_properties, expected = _common_properties(session, datasource, settings)

    source: dict[str, Any]
    if datasource.kind == SourceKind.POSTGRES:
        namespaces = sorted({row.schema_name for row in datasets})
        source = {
            "type": "postgres",
            "config": {
                "host_port": host_port,
                "database": config["database"],
                "username": "${SOURCE_USERNAME}",
                "password": "${SOURCE_PASSWORD}",
                "include_tables": True,
                "include_views": True,
                "include_view_lineage": True,
                "profiling": {"enabled": False},
                "schema_pattern": _patterns(namespaces),
                "table_pattern": _table_patterns(datasets),
                "platform_instance": platform_instance,
                "env": fabric,
                "stateful_ingestion": {"enabled": True, "remove_stale_metadata": True},
            },
        }
    elif datasource.kind == SourceKind.MYSQL:
        namespaces = sorted({row.catalog_name for row in datasets})
        source = {
            "type": "mysql",
            "config": {
                "host_port": host_port,
                "database": config["database"],
                "username": "${SOURCE_USERNAME}",
                "password": "${SOURCE_PASSWORD}",
                "include_tables": True,
                "include_views": True,
                "profiling": {"enabled": False},
                "database_pattern": _patterns(namespaces),
                "table_pattern": _table_patterns(datasets),
                "platform_instance": platform_instance,
                "env": fabric,
                "stateful_ingestion": {"enabled": True, "remove_stale_metadata": True},
            },
        }
    elif datasource.kind == SourceKind.DORIS:
        namespaces = sorted({row.schema_name for row in datasets})
        source = {
            "type": "doris",
            "config": {
                "host_port": host_port,
                "database": config["database"],
                "username": "${SOURCE_USERNAME}",
                "password": "${SOURCE_PASSWORD}",
                "include_tables": True,
                "include_views": True,
                "include_view_lineage": True,
                "profiling": {"enabled": False},
                "schema_pattern": _patterns(namespaces),
                "table_pattern": _table_patterns(datasets),
                "platform_instance": platform_instance,
                "env": fabric,
                "stateful_ingestion": {"enabled": True, "remove_stale_metadata": True},
            },
        }
    else:  # pragma: no cover - guarded at registration
        raise ApiError(ErrorCode.DATASOURCE_UNSUPPORTED, f"no ingestion recipe for kind '{datasource.kind}'")

    recipe: dict[str, Any] = {
        # DataHub requires pipeline_name at the recipe level (not inside the
        # source config) when stateful ingestion is enabled - verified against
        # the locked SDK via `datahub ingest --dry-run`.
        "pipeline_name": pipeline_name,
        "source": source,
        "sink": {
            "type": "datahub-rest",
            "config": {
                "server": settings.datahub_gms_url,
                "token": "${DATAHUB_TOKEN}",
            },
        },
    }
    hash_payload = {
        "recipe": recipe,
        "custom_properties": custom_properties,
        "expected": expected,
        "datahub_version": settings.datahub_version,
    }
    recipe_hash = hashlib.sha256(
        json.dumps(hash_payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return RenderedRecipe(
        recipe=recipe,
        recipe_hash=recipe_hash,
        platform_instance=platform_instance,
        expected_datasets=expected,
    )


def dump_recipe_yaml(recipe: dict[str, Any]) -> str:
    return yaml.safe_dump(recipe, sort_keys=False, allow_unicode=True)


def write_recipe(path: Path, recipe: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_recipe_yaml(recipe), encoding="utf-8")
    return path
