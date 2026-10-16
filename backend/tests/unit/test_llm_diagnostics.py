"""Endpoint discovery and exact identity checks, without live credentials."""
import httpx
import pytest

from app import cli
from app.agent.llm import OpenAICompatibleProvider
from app.config import Settings


def configure(monkeypatch):
    settings = Settings(
        _env_file=None, llm_provider="openai-compatible",
        llm_base_url="https://fixture.invalid/v1", llm_model="configured-alias",
        llm_api_key="fixture-secret-only", llm_api_key_file=None,
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return settings


def test_discovery_uses_configured_endpoint_and_secret_without_completion(monkeypatch, capsys):
    configure(monkeypatch)

    def get(url, *, headers, timeout):
        assert url == "https://fixture.invalid/v1/models"
        assert headers == {"Authorization": "Bearer fixture-secret-only"}
        assert timeout == 30
        return httpx.Response(200, request=httpx.Request("GET", url), json={
            "data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-a"}],
        })

    monkeypatch.setattr("app.agent.llm.httpx.get", get)
    monkeypatch.setattr("app.agent.llm.httpx.post", lambda *a, **kw: pytest.fail("unexpected completion"))
    assert cli.main(["llm-check", "--list-models"]) == 0
    output = capsys.readouterr().out
    assert "['model-a', 'model-b']" in output
    assert "fixture-secret-only" not in output


@pytest.mark.parametrize("payload", [{}, {"data": {}}, {"data": [{}]}, {"data": [{"id": 1}]}, {"data": [{"id": " "}]}])
def test_discovery_rejects_malformed_catalog(monkeypatch, payload):
    monkeypatch.setattr("app.agent.llm.httpx.get", lambda url, **kw: httpx.Response(
        200, request=httpx.Request("GET", url), json=payload,
    ))
    provider = OpenAICompatibleProvider(base_url="https://fixture.invalid/v1", model="alias", api_key="fixture")
    with pytest.raises(ValueError, match="response schema"):
        provider.list_models()


@pytest.mark.parametrize("reported, expected, code", [
    ("actual-model", "actual-model", 0),
    ("actual-model", "configured-alias", 1),
    (None, "configured-alias", 1),
    ("", "configured-alias", 1),
])
def test_exact_identity_uses_reported_model_not_configured_alias(monkeypatch, capsys, reported, expected, code):
    configure(monkeypatch)

    def post(url, **kw):
        payload = {
            "choices": [{"message": {"content": '{"dimensions":["country"]}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }
        if reported is not None:
            payload["model"] = reported
        return httpx.Response(200, request=httpx.Request("POST", url), json=payload)

    monkeypatch.setattr("app.agent.llm.httpx.post", post)
    assert cli.main(["llm-check", "--expect-model", expected]) == code
    output = capsys.readouterr().out
    assert "fixture-secret-only" not in output
    if code:
        assert "FAILED: expected endpoint" in output


@pytest.mark.parametrize("option, method", [("--list-models", "get"), (None, "post")])
def test_diagnostics_do_not_echo_provider_exception_secrets(monkeypatch, capsys, option, method):
    configure(monkeypatch)

    def fail(*args, **kwargs):
        raise RuntimeError("Authorization: Bearer fixture-secret-only")

    monkeypatch.setattr(f"app.agent.llm.httpx.{method}", fail)
    assert cli.main(["llm-check", *([option] if option else [])]) == 1
    output = capsys.readouterr().out
    assert "RuntimeError" in output
    assert "fixture-secret-only" not in output


@pytest.mark.parametrize("arguments", [["--list-models"], ["--expect-model", "actual-model"]])
def test_offline_template_cannot_pass_real_model_diagnostics(monkeypatch, arguments):
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, llm_provider="fake"))
    assert cli.main(["llm-check", *arguments]) == 1
