"""Role capabilities and dataset authorization (spec sections 12, 22).

Data access is default-deny and independent from product capabilities:
administrators manage resources but still need an explicit dataset grant to
run a query against that dataset.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.constants import ROLE_CAPABILITIES, DatasetAction, ErrorCode
from app.errors import ApiError
from app.models.orm import Dataset
from app.repositories import datasets as datasets_repo


def capabilities_for_roles(role_names: list[str]) -> frozenset[str]:
    caps: set[str] = set()
    for name in role_names:
        caps.update(ROLE_CAPABILITIES.get(name, frozenset()))
    return frozenset(caps)


def require_capability(capabilities: frozenset[str], capability: str) -> None:
    if capability not in capabilities:
        raise ApiError(ErrorCode.FORBIDDEN, f"missing capability: {capability}")


def is_admin(role_names: list[str]) -> bool:
    return "admin" in role_names


def ensure_dataset_action(
    session: Session,
    *,
    role_ids: list[uuid.UUID],
    dataset: Dataset,
    action: DatasetAction | str,
) -> None:
    action_value = str(action)
    if not datasets_repo.has_dataset_action(session, role_ids, dataset.id, action_value):
        raise ApiError(
            ErrorCode.PERMISSION_DENIED,
            f"no {action_value} permission on dataset {dataset.object_name}",
            details={
                "dataset_id": str(dataset.id),
                "action": action_value,
                "hint": "request access with POST /api/v1/permission-requests",
            },
        )
