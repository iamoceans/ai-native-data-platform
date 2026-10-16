"""Broken/error GMS responses must not masquerade as an empty catalog."""
import httpx
import pytest

from app.config import Settings
from app.metadata.datahub import DataHubAdapter, DataHubUnavailable


def adapter_response(monkeypatch, status, body):
    def post(client, url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer test-token"
        return httpx.Response(status, json=body)
    monkeypatch.setattr(httpx.Client, "post", post)
    return DataHubAdapter(Settings(datahub_token="test-token", datahub_gms_url="https://gms.invalid"))


@pytest.mark.parametrize("status,body", [
    (301, {}), (400, {}), (401, {}), (403, {}), (404, {}), (503, {}),
    (200, []), (200, {}), (200, {"data": []}), (200, {"data": None}),
    (200, {"errors": [{"message": "remote secret detail"}]}),
    (200, {"errors": "unexpected error shape"}),
])
def test_failed_http_or_envelope_returns_metadata_unavailable(monkeypatch, status, body):
    adapter = adapter_response(monkeypatch, status, body)
    with pytest.raises(DataHubUnavailable) as error:
        adapter.search("*")
    assert error.value.code == "METADATA_UNAVAILABLE"
    assert "remote secret detail" not in str(error.value) + str(error.value.details)


@pytest.mark.parametrize("operation", ["search", "dataset", "lineage"])
def test_missing_operation_object_is_not_an_empty_result(monkeypatch, operation):
    adapter = adapter_response(monkeypatch, 200, {"data": {}})
    with pytest.raises(DataHubUnavailable):
        {"search": lambda: adapter.search("*"), "dataset": lambda: adapter.get_dataset("urn:test"),
         "lineage": lambda: adapter.get_lineage("urn:test")}[operation]()


def test_explicit_dataset_null_remains_a_valid_not_found(monkeypatch):
    adapter = adapter_response(monkeypatch, 200, {"data": {"dataset": None}})
    assert adapter.get_dataset("urn:test") is None


def test_empty_valid_catalog_is_preserved(monkeypatch):
    adapter = adapter_response(monkeypatch, 200, {"data": {"searchAcrossEntities": {
        "total": 0, "start": 0, "count": 1, "searchResults": [],
    }}})
    assert adapter.search("*", count=1).entities == []


@pytest.mark.parametrize("operation", ["search", "lineage"])
@pytest.mark.parametrize("block", [{}, {"total": 0}, {"total": -1, "searchResults": []},
                                    {"total": 0, "searchResults": [None]}])
def test_malformed_search_block_never_looks_empty(monkeypatch, operation, block):
    key = "searchAcrossEntities" if operation == "search" else "searchAcrossLineage"
    adapter = adapter_response(monkeypatch, 200, {"data": {key: block}})
    with pytest.raises(DataHubUnavailable):
        if operation == "search":
            adapter.search("*")
        else:
            adapter.get_lineage("urn:test")
