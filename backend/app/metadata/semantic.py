"""Versioned semantic metadata (spec sections 10.2, 15).

`metadata/semantic/*.yaml` carries the semantics DataHub cannot infer reliably
from schema alone: grain, business timezone, currency, join keys and metric
keys. The platform validates these files at load time and merges them into
DatasetContext; the same values are published to DataHub as custom properties
during ingestion (so DataHub stays the searchable catalog while the platform
keeps the executable contract).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field


class SemanticEntryModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dataset: str = Field(min_length=3, description="<namespace>.<table> qualifier")
    description: str | None = None
    grain: list[str] = Field(default_factory=list)
    business_timezone: str = "UTC"
    currency: str | None = None
    metric_keys: list[str] = Field(default_factory=list)
    join_keys: list[str] = Field(default_factory=list)
    data_complete_through: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class SemanticRegistry:
    entries: dict[str, SemanticEntryModel] = field(default_factory=dict)

    def for_dataset(self, qualifier: str) -> SemanticEntryModel | None:
        return self.entries.get(qualifier)

    def as_custom_properties(self, entry: SemanticEntryModel) -> dict[str, str]:
        properties: dict[str, str] = {}
        if entry.grain:
            properties["ainative.grain"] = ",".join(entry.grain)
        properties["ainative.business_timezone"] = entry.business_timezone
        if entry.currency:
            properties["ainative.currency"] = entry.currency
        if entry.metric_keys:
            properties["ainative.metric_keys"] = ",".join(entry.metric_keys)
        if entry.join_keys:
            properties["ainative.join_keys"] = ",".join(entry.join_keys)
        if entry.data_complete_through:
            properties["ainative.data_complete_through"] = entry.data_complete_through
        return properties


def load_semantic_registry(metadata_dir: Path) -> SemanticRegistry:
    directory = Path(metadata_dir) / "semantic"
    entries: dict[str, SemanticEntryModel] = {}
    if not directory.is_dir():
        return SemanticRegistry(entries=entries)
    for path in sorted(directory.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"semantic file {path.name} must contain a mapping")
        entry = SemanticEntryModel.model_validate(payload)
        if entry.dataset in entries:
            raise ValueError(f"duplicate semantic entry for {entry.dataset}")
        entries[entry.dataset] = entry
    return SemanticRegistry(entries=entries)


def dataset_qualifier(catalog_name: str, schema_name: str, object_name: str) -> str:
    namespace = schema_name or catalog_name
    return f"{namespace}.{object_name}"
