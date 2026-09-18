"""Whitelisted cross-source relations (spec section 17).

Cross-source analysis only combines bounded, already-aggregated results through
relations registered in ``metadata/relations/*.yaml`` and validated at load
time. The model (M5) will only ever *select* a ``relation_id``; it can never
submit a join expression.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.analysis.types import AnalysisError


class JoinRelationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_id: str = Field(min_length=3)
    left_dataset: str
    right_dataset: str
    left_keys: list[str] = Field(min_length=1)
    right_keys: list[str] = Field(min_length=1)
    relationship: Literal["many_to_one", "one_to_one"]
    left_time_column: str | None = None
    right_valid_from: str | None = None
    right_valid_to: str | None = None
    amount_columns: list[str] = Field(default_factory=list)
    description: str | None = None

    def model_post_init(self, __context) -> None:  # noqa: D105
        if len(self.left_keys) != len(self.right_keys):
            raise ValueError(
                f"relation {self.relation_id}: left_keys and right_keys must have the same length"
            )
        interval = [self.left_time_column, self.right_valid_from]
        if any(interval) and not all(interval):
            raise ValueError(
                f"relation {self.relation_id}: as-of matching needs left_time_column and right_valid_from"
            )
        if not any(interval) and self.right_valid_to:
            raise ValueError(
                f"relation {self.relation_id}: right_valid_to requires the as-of interval columns"
            )


@dataclass(frozen=True)
class JoinRelation:
    relation_id: str
    left_dataset: str
    right_dataset: str
    left_keys: tuple[str, ...]
    right_keys: tuple[str, ...]
    relationship: str
    left_time_column: str | None
    right_valid_from: str | None
    right_valid_to: str | None
    amount_columns: tuple[str, ...]
    description: str | None = None

    @property
    def as_of(self) -> bool:
        return bool(self.left_time_column and self.right_valid_from)


def load_relations(metadata_dir: Path) -> dict[str, JoinRelation]:
    directory = Path(metadata_dir) / "relations"
    relations: dict[str, JoinRelation] = {}
    if not directory.is_dir():
        return relations
    for path in sorted(directory.glob("*.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"relation file {path.name} must contain a mapping")
        model = JoinRelationModel.model_validate(payload)
        if model.relation_id in relations:
            raise ValueError(f"duplicate relation_id {model.relation_id}")
        relations[model.relation_id] = JoinRelation(
            relation_id=model.relation_id,
            left_dataset=model.left_dataset,
            right_dataset=model.right_dataset,
            left_keys=tuple(model.left_keys),
            right_keys=tuple(model.right_keys),
            relationship=model.relationship,
            left_time_column=model.left_time_column,
            right_valid_from=model.right_valid_from,
            right_valid_to=model.right_valid_to,
            amount_columns=tuple(model.amount_columns),
            description=model.description,
        )
    return relations


def get_relation(metadata_dir: Path, relation_id: str) -> JoinRelation:
    relation = load_relations(metadata_dir).get(relation_id)
    if relation is None:
        raise AnalysisError(
            "RELATION_UNKNOWN",
            f"relation '{relation_id}' is not registered",
            {"relation_id": relation_id},
        )
    return relation
