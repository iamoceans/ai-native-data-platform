"""Minimal semantic metric contract (spec section 15).

`metadata/metrics/*.yaml` is the human-maintained, versioned authority. The
registry validates the files at load time; `sync_metric_definitions` writes them
into `metric_definitions` (idempotent by metric_key+version). The compiler that
turns these definitions into SQL lands with the analysis kernel (M4); nothing
here lets a model invent formulas.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.orm import MetricDefinition


class MetricComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aggregation: str
    column: str
    filter: str | None = None


class MetricFreshness(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date_column: str
    completeness_source: str


class MetricDriverDecomposition(BaseModel):
    """Declares the companion metric that makes a factor split possible.

    Only a definition can declare this; a model or a user question cannot, so
    the revenue = impressions x eCPM split stays a property of the metric
    contract rather than a runtime guess.
    """

    model_config = ConfigDict(extra="forbid")
    impressions_metric: str = Field(min_length=2)


class MetricDefinitionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    metric_key: str = Field(min_length=2)
    version: int = Field(ge=1)
    name: str
    dataset: str | None = None
    datasets: list[str] = Field(default_factory=list)
    components: dict[str, MetricComponent] = Field(default_factory=dict)
    formula: str
    unit: str
    currency: str | None = None
    timezone: str = "UTC"
    grain: list[str] = Field(default_factory=list)
    allowed_dimensions: list[str] = Field(default_factory=list)
    aggregation_kind: str
    freshness: MetricFreshness | None = None
    driver_decomposition: MetricDriverDecomposition | None = None
    notes: str | None = None

    def model_post_init(self, __context) -> None:  # noqa: D105
        if not self.dataset and not self.datasets:
            raise ValueError(f"metric {self.metric_key} needs dataset or datasets")
        if self.dataset and self.datasets:
            raise ValueError(f"metric {self.metric_key} must use either dataset or datasets, not both")

    @property
    def all_datasets(self) -> list[str]:
        return list(self.datasets) if self.datasets else [self.dataset or ""]


def load_metric_definitions(metadata_dir: Path) -> list[MetricDefinitionModel]:
    directory = Path(metadata_dir) / "metrics"
    if not directory.is_dir():
        return []
    definitions: list[MetricDefinitionModel] = []
    seen: set[tuple[str, int]] = set()
    for path in sorted(directory.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"metric file {path.name} must contain a mapping")
        definition = MetricDefinitionModel.model_validate(payload)
        key = (definition.metric_key, definition.version)
        if key in seen:
            raise ValueError(f"duplicate metric definition {key}")
        seen.add(key)
        definitions.append(definition)
    return definitions


def sync_metric_definitions(
    session: Session, definitions: list[MetricDefinitionModel]
) -> dict[str, int]:
    """Upsert definitions; returns counts for the bootstrap report."""
    created = 0
    updated = 0
    for definition in definitions:
        row = session.execute(
            select(MetricDefinition).where(
                MetricDefinition.metric_key == definition.metric_key,
                MetricDefinition.version == definition.version,
            )
        ).scalar_one_or_none()
        payload: dict[str, Any] = definition.model_dump(mode="json")
        if row is None:
            session.add(
                MetricDefinition(
                    id=uuid.uuid4(),
                    metric_key=definition.metric_key,
                    version=definition.version,
                    definition=payload,
                    active=True,
                )
            )
            created += 1
        elif row.definition != payload:
            row.definition = payload
            updated += 1
    session.flush()
    return {"created": created, "updated": updated}


def get_metric_definition(
    session: Session, metric_key: str, version: int | None = None
) -> MetricDefinition | None:
    statement = select(MetricDefinition).where(
        MetricDefinition.metric_key == metric_key, MetricDefinition.active.is_(True)
    )
    if version is not None:
        statement = statement.where(MetricDefinition.version == version)
    statement = statement.order_by(MetricDefinition.version.desc()).limit(1)
    return session.execute(statement).scalar_one_or_none()
