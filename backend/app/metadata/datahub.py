"""DataHub adapter (spec section 10.1).

GraphQL search/read against the pinned DataHub release; query documents live in
``app/metadata/graphql/*.graphql`` and their shapes are pinned by contract tests
with recorded response fixtures (never unverified query strings inline).

The adapter never grants access: the caller filters by platform authorization
first, and search totals exposed to users only count authorized assets.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.constants import ErrorCode
from app.errors import ApiError

logger = logging.getLogger(__name__)

GRAPHQL_DIR = Path(__file__).resolve().parent / "graphql"


@dataclass(frozen=True)
class DataHubOwner:
    urn: str
    username: str | None = None


@dataclass
class DataHubEntity:
    urn: str
    name: str
    platform: str | None = None
    description: str | None = None
    custom_properties: dict[str, str] = field(default_factory=dict)
    schema_fields: list[dict[str, Any]] = field(default_factory=list)
    schema_hash: str | None = None
    owners: list[DataHubOwner] = field(default_factory=list)


@dataclass(frozen=True)
class DataHubSearchPage:
    total: int
    entities: list[DataHubEntity]
    start: int
    count: int


@dataclass(frozen=True)
class DataHubLineageNode:
    urn: str
    name: str
    platform: str | None
    degree: int


@dataclass(frozen=True)
class DataHubLineageEdge:
    """A relation observed while walking the lineage graph.

    ``degree`` is the degree of the child node relative to the root; edges are
    recorded from the BFS front, so multi-hop shapes are exact rather than
    inferred from a flat node list.
    """

    source: str
    target: str
    degree: int


@dataclass(frozen=True)
class DataHubLineageResult:
    nodes: list[DataHubLineageNode]
    edges: list[DataHubLineageEdge]


class DataHubUnavailable(ApiError):
    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(
            ErrorCode.METADATA_UNAVAILABLE, message, retryable=True, details=details or {}
        )


class DataHubAdapter:
    """Thin, dependency-light GraphQL client for DataHub GMS."""

    _queries: dict[str, str] = {}
    _queries_lock = threading.Lock()

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._token = settings.datahub_token or _read_token(settings)
        self._url = settings.datahub_gms_url.rstrip("/") + "/api/graphql"

    @classmethod
    def query_document(cls, name: str) -> str:
        with cls._queries_lock:
            if name not in cls._queries:
                path = GRAPHQL_DIR / f"{name}.graphql"
                if not path.is_file():
                    raise FileNotFoundError(f"missing GraphQL document: {path}")
                cls._queries[name] = path.read_text(encoding="utf-8")
            return cls._queries[name]

    # ------------------------------------------------------------------ http
    def _post(self, query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        payload = {"query": query, "variables": variables or {}}
        try:
            with httpx.Client(timeout=self._settings.datahub_timeout_seconds) as client:
                response = client.post(self._url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise DataHubUnavailable(f"DataHub GMS is unreachable: {exc}") from exc
        if response.status_code == 401 or response.status_code == 403:
            raise DataHubUnavailable(
                "DataHub GMS rejected the platform token",
                {"status": response.status_code},
            )
        if response.status_code >= 500:
            raise DataHubUnavailable(
                "DataHub GMS returned a server error", {"status": response.status_code}
            )
        try:
            body = response.json()
        except json.JSONDecodeError as exc:
            raise DataHubUnavailable("DataHub GMS returned a non-JSON response") from exc
        if body.get("errors"):
            raise DataHubUnavailable(
                "DataHub GraphQL returned errors",
                {"errors": [str(item.get("message", item)) for item in body["errors"]][:5]},
            )
        return body.get("data") or {}

    # ---------------------------------------------------------------- search
    def search(self, query: str, *, start: int = 0, count: int = 20) -> DataHubSearchPage:
        data = self._post(
            self.query_document("search"),
            {"query": query, "start": start, "count": count},
        )
        block = data.get("searchAcrossEntities") or {}
        results = block.get("searchResults") or []
        return DataHubSearchPage(
            total=int(block.get("total") or 0),
            start=int(block.get("start") or 0),
            count=int(block.get("count") or len(results)),
            entities=[self._parse_entity(item.get("entity") or {}) for item in results],
        )

    def get_dataset(self, urn: str) -> DataHubEntity | None:
        data = self._post(self.query_document("dataset"), {"urn": urn})
        entity = data.get("dataset")
        if entity is None:
            return None
        parsed = self._parse_entity(entity)
        schema = (entity.get("schemaMetadata") or {})
        parsed.schema_hash = schema.get("hash")
        parsed.schema_fields = [
            {
                "name": field_.get("fieldPath"),
                "type": field_.get("nativeDataType"),
                "nullable": field_.get("nullable"),
                "description": field_.get("description"),
            }
            for field_ in (schema.get("fields") or [])
        ]
        ownership = (entity.get("ownership") or {}).get("owners") or []
        parsed.owners = [
            DataHubOwner(
                urn=str((entry.get("owner") or {}).get("urn") or ""),
                username=(entry.get("owner") or {}).get("username"),
            )
            for entry in ownership
        ]
        return parsed

    def get_lineage(
        self, urn: str, *, direction: str = "UPSTREAM", depth: int = 1, count: int = 20
    ) -> DataHubLineageResult:
        upstream = direction.upper() == "UPSTREAM"
        nodes: dict[str, DataHubLineageNode] = {}
        edges: dict[tuple[str, str], DataHubLineageEdge] = {}
        frontier = [urn]
        seen = {urn}
        for _ in range(max(1, min(depth, 5))):
            next_frontier: list[str] = []
            for current in frontier:
                data = self._post(
                    self.query_document("lineage"),
                    {"urn": current, "direction": direction.upper(), "count": count, "start": 0},
                )
                block = data.get("searchAcrossLineage") or {}
                for item in block.get("searchResults") or []:
                    entity = item.get("entity") or {}
                    entity_urn = str(entity.get("urn") or "")
                    if not entity_urn or entity_urn in seen:
                        continue
                    seen.add(entity_urn)
                    parsed = self._parse_entity(entity)
                    degree = int(item.get("degree") or 1)
                    nodes[entity_urn] = DataHubLineageNode(
                        urn=entity_urn,
                        name=parsed.name,
                        platform=parsed.platform,
                        degree=degree,
                    )
                    source, target = (
                        (entity_urn, current) if upstream else (current, entity_urn)
                    )
                    edges[(source, target)] = DataHubLineageEdge(
                        source=source, target=target, degree=degree
                    )
                    next_frontier.append(entity_urn)
            frontier = next_frontier
            if not frontier:
                break
        return DataHubLineageResult(nodes=list(nodes.values()), edges=list(edges.values()))

    # --------------------------------------------------------------- parsing
    @staticmethod
    def _parse_entity(entity: dict[str, Any]) -> DataHubEntity:
        if not entity:
            return DataHubEntity(urn="", name="")
        properties = entity.get("properties") or {}
        custom: dict[str, str] = {}
        for entry in properties.get("customProperties") or []:
            key = entry.get("key")
            if key:
                custom[str(key)] = str(entry.get("value") or "")
        platform_block = entity.get("platform") or {}
        name = (
            entity.get("name")
            or properties.get("name")
            or entity.get("urn", "").rsplit(",", 1)[-1].rstrip(")")
        )
        description = entity.get("description") or properties.get("description")
        return DataHubEntity(
            urn=str(entity.get("urn") or ""),
            name=str(name),
            platform=platform_block.get("name"),
            description=str(description) if description else None,
            custom_properties=custom,
        )


def _read_token(settings: Settings) -> str | None:
    """Optional DataHub token from the mounted secrets directory."""
    path = settings.secrets_dir / "datahub.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    token = payload.get("token")
    return token if isinstance(token, str) and token else None
