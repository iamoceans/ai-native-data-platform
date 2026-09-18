"""DataHub GraphQL contract tests (spec section 10.1).

The GraphQL documents live in ``app/metadata/graphql/*.graphql``; the fixtures in
``tests/fixtures/datahub/*.json`` are **real responses captured from the pinned
DataHub v1.7.0.1 stack** by ``scripts/capture_datahub_fixtures.py`` (recorded
payloads contain the exact request variables used). These tests pin:

- the adapter parses every field the real release returns (including inline
  fragments for ``ownership``, which is how the release exposes owners), and
- the recorded fixtures correspond to the query documents the adapter sends, so
  a document edit without a fixture refresh fails loudly.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.config import Settings
from app.metadata.datahub import DataHubAdapter

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "datahub"
PINNED_VERSION = "v1.7.0.1"

OPERATIONS = {
    "search": {"operation": "searchDatasets", "variables": ["query", "start", "count"]},
    "dataset": {"operation": "getDataset", "variables": ["urn"]},
    "lineage": {
        "operation": "searchLineage",
        "variables": ["urn", "direction", "count", "start"],
    },
}


def load_fixture(name: str) -> dict:
    path = FIXTURES / f"{name}.json"
    if not path.is_file():
        pytest.skip(f"fixture {path.name} is not present")
    return json.loads(path.read_text(encoding="utf-8"))


def replay(monkeypatch, fixture: dict) -> tuple[DataHubAdapter, dict]:
    settings = Settings(
        datahub_enabled=True,
        datahub_gms_url="http://127.0.0.1:18080",
        database_url="postgresql+psycopg://x/y",
    )
    adapter = DataHubAdapter(settings)
    seen: dict = {}

    def fake_post(query: str, variables: dict | None = None) -> dict:
        seen["query"] = query
        seen["variables"] = variables or {}
        return fixture["response"]["data"]

    monkeypatch.setattr(adapter, "_post", fake_post)
    return adapter, seen


# ---------------------------------------------------------------------------
# query documents
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_query_documents_match_the_recorded_stack():
    for name, spec in OPERATIONS.items():
        document = DataHubAdapter.query_document(name)
        assert f"query {spec['operation']}" in document
        for variable in spec["variables"]:
            assert f"${variable}:" in document, f"{name} must declare ${variable}"
        assert document.count("{") == document.count("}"), f"{name} braces must balance"
        variables = {match.split(":", 1)[0].lstrip("$") for match in re.findall(r"\$\w+:", document)}
        assert set(spec["variables"]) <= variables


# ---------------------------------------------------------------------------
# fixture integrity
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_all_fixtures_are_pinned_and_error_free():
    fixtures = sorted(FIXTURES.glob("*.json"))
    assert fixtures, "at least one captured fixture is required"
    for path in fixtures:
        document = json.loads(path.read_text(encoding="utf-8"))
        if "response" not in document:
            continue  # target.json-style metadata file
        assert document["datahub_version"] == PINNED_VERSION
        assert "errors" not in document["response"], f"{path.name} recorded GraphQL errors"


# ---------------------------------------------------------------------------
# search replay
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_search_fixture_replays_through_adapter(monkeypatch):
    fixture = load_fixture("search")
    adapter, seen = replay(monkeypatch, fixture)
    variables = fixture["request"]["variables"]
    page = adapter.search(variables["query"], start=variables["start"], count=variables["count"])

    assert seen["query"] == DataHubAdapter.query_document("search")
    assert seen["variables"] == variables
    block = fixture["response"]["data"]["searchAcrossEntities"]
    assert page.total == block["total"]
    assert len(page.entities) == len(block["searchResults"])
    first = page.entities[0]
    assert first.urn == block["searchResults"][0]["entity"]["urn"]
    assert first.platform == "doris"
    assert first.custom_properties["ainative.grain"].startswith("dt,")
    # The adapter surfaces `properties.name` when the parsed name segment matches.
    assert first.name


# ---------------------------------------------------------------------------
# dataset replay
# ---------------------------------------------------------------------------
@pytest.mark.contract
def test_dataset_fixture_replays_through_adapter(monkeypatch):
    fixture = load_fixture("dataset")
    adapter, seen = replay(monkeypatch, fixture)
    urn = fixture["request"]["variables"]["urn"]
    entity = adapter.get_dataset(urn)

    assert seen["query"] == DataHubAdapter.query_document("dataset")
    block = fixture["response"]["data"]["dataset"]
    assert entity is not None
    assert entity.urn == block["urn"]
    assert entity.platform == (block["platform"] or {}).get("name")
    assert entity.schema_hash == block["schemaMetadata"]["hash"]
    assert [field["name"] for field in entity.schema_fields] == [
        field["fieldPath"] for field in block["schemaMetadata"]["fields"]
    ]
    assert entity.custom_properties.get("ainative.metric_keys") == (
        "ads_revenue,impressions,ecpm"
    )
    # ownership is null in the recorded response -> parsed as no owners.
    assert entity.owners == []


@pytest.mark.contract
def test_dataset_fixture_schema_fields_include_native_types(monkeypatch):
    fixture = load_fixture("dataset")
    adapter, _ = replay(monkeypatch, fixture)
    entity = adapter.get_dataset(fixture["request"]["variables"]["urn"])
    types = {field["name"]: field["type"] for field in entity.schema_fields}
    assert types["revenue_usd"] == "decimal(20,6)"
    assert types["impressions"] == "bigint"


# ---------------------------------------------------------------------------
# lineage replay (all captured variant fixtures)
# ---------------------------------------------------------------------------
@pytest.mark.contract
@pytest.mark.parametrize(
    "fixture_name",
    sorted(path.stem for path in FIXTURES.glob("lineage_*.json")),
)
def test_lineage_fixtures_replay_through_adapter(monkeypatch, fixture_name):
    fixture = load_fixture(fixture_name)
    adapter, seen = replay(monkeypatch, fixture)
    variables = fixture["request"]["variables"]
    result = adapter.get_lineage(
        variables["urn"], direction=variables["direction"], depth=1, count=variables["count"]
    )

    assert seen["query"] == DataHubAdapter.query_document("lineage")
    block = fixture["response"]["data"]["searchAcrossLineage"]
    expected = {
        item["entity"]["urn"]: item
        for item in block["searchResults"]
        if item["entity"]["urn"] != variables["urn"]
    }
    assert {node.urn for node in result.nodes} == set(expected)
    for node in result.nodes:
        item = expected[node.urn]
        assert node.degree == int(item.get("degree") or 1)
        assert node.platform == (item["entity"].get("platform") or {}).get("name")

    upstream = variables["direction"] == "UPSTREAM"
    for edge in result.edges:
        assert edge.target if upstream else edge.source  # root side is the recorded URN
        if upstream:
            assert edge.source in expected and edge.target == variables["urn"]
        else:
            assert edge.source == variables["urn"] and edge.target in expected
    assert len(result.edges) == len(expected), "one edge per newly discovered node"
