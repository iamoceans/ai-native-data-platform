"""Fail-closed GMS authentication acceptance and private atomic token storage."""
import json
import os
import stat

import httpx
import pytest

from scripts import datahub_token as helper


def response_client(status=200, body=None):
    return httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(status, json=body)))


@pytest.mark.parametrize("status,body", [
    (302, {}), (400, {}), (404, {}), (500, {}),
    (200, []), (200, {}), (200, {"data": {}}), (200, {"data": []}),
    (200, {"errors": [{"message": "sensitive remote diagnostic"}]}),
    (200, {"data": {"dataset": []}}),
    (200, {"data": {"dataset": {"urn": "other"}}}),
])
def test_http_and_envelope_failures_are_not_success(status, body):
    with response_client(status, body) as client:
        result = helper.probe_details("https://gms.invalid", "test-token", client=client)
    assert not result.accepted
    assert "sensitive remote diagnostic" not in result.message


def test_valid_null_dataset_is_a_successful_graphql_response():
    with response_client(body={"data": {"dataset": None}}) as client:
        assert helper.probe_details("https://gms.invalid", "test-token", client=client).accepted


@pytest.mark.parametrize("failure", [None, "anonymous", "invalid", "valid"])
def test_auth_acceptance_requires_both_denials_and_valid_graphql(failure):
    seen = []
    def respond(request):
        authorization = request.headers.get("Authorization")
        kind = "anonymous" if authorization is None else "valid" if authorization == "Bearer test-token" else "invalid"
        seen.append(kind)
        if kind == "valid":
            return httpx.Response(401 if failure == kind else 200, json={"data": {"dataset": None}})
        return httpx.Response(200 if failure == kind else 401, json={"data": {"dataset": None}})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        ok, _ = helper.verify_auth("https://gms.invalid", "test-token", client=client)
    assert ok == (failure is None)
    if ok:
        assert seen == ["anonymous", "invalid", "valid"]


def test_connection_outage_does_not_establish_authentication():
    def disconnected(request):
        raise httpx.ConnectError("sensitive transport detail", request=request)
    with httpx.Client(transport=httpx.MockTransport(disconnected)) as client:
        result = helper.probe_details("https://gms.invalid", None, client=client)
        ok, message = helper.verify_auth("https://gms.invalid", "test-token", client=client)
    assert result.status is None and not ok
    assert "sensitive" not in result.message + message


def test_token_save_is_private_and_preserves_metadata(workdir):
    target = workdir / "datahub.json"
    target.write_text('{"description":"keep this","token":"old-test-token"}')
    helper.store_token(workdir, "new-test-token")
    assert json.loads(target.read_text()) == {"description": "keep this", "token": "new-test-token"}
    if os.name == "posix":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_failed_atomic_replace_preserves_old_token_and_removes_temp(monkeypatch, workdir):
    target = workdir / "datahub.json"
    target.write_text('{"token":"old-test-token"}')
    def denied(*args):
        raise OSError("replace denied")
    monkeypatch.setattr(helper.os, "replace", denied)
    with pytest.raises(OSError):
        helper.store_token(workdir, "new-test-token")
    assert json.loads(target.read_text())["token"] == "old-test-token"
    assert sorted(path.name for path in workdir.iterdir()) == ["datahub.json"]


@pytest.mark.parametrize("contents", ["test-token", '{"token":"test-token"}'])
def test_private_file_token_inputs(workdir, contents):
    path = workdir / "token"
    path.write_text(contents)
    assert helper.token_from_file(path) == "test-token"


@pytest.mark.parametrize("contents", ["", '{"token":null}', "line\nbreak"])
def test_invalid_file_token_inputs(workdir, contents):
    path = workdir / "token"
    path.write_text(contents)
    with pytest.raises(ValueError):
        helper.token_from_file(path)
