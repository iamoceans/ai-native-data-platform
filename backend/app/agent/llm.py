"""Structured LLM adapters with deterministic offline behavior."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel

from app.agent.budget import AnalysisBudget

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class LLMUsage:
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class StructuredGeneration:
    value: BaseModel
    usage: LLMUsage
    model_id: str
    finish_reason: str


def read_api_key(path: Path | str) -> str | None:
    """First usable key line from a mounted key file, or None.

    The file is documented to hold comments plus one key line, so comments and
    blank lines are skipped; a leftover placeholder counts as "no key yet". The
    returned value is what goes into the Authorization header, so nothing else
    from the file may leak into it.
    """
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    for raw in content.splitlines():
        line = raw.strip().strip('"').strip("'")
        if not line or line.startswith("#"):
            continue
        if "PASTE" in line.upper():
            return None
        return line
    return None


class LLMProvider(Protocol):
    def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        schema: type[T],
        budget: AnalysisBudget,
        max_output_tokens: int = 1000,
    ) -> StructuredGeneration: ...


class FakeLLMProvider:
    """Returns scripted objects and deterministic token accounting."""

    def __init__(self, outputs: list[dict[str, Any]], model_id: str = "fake-v1") -> None:
        self._outputs = list(outputs)
        self._model_id = model_id

    def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        schema: type[T],
        budget: AnalysisBudget,
        max_output_tokens: int = 1000,
    ) -> StructuredGeneration:
        estimated = sum(len(message.get("content", "")) for message in messages) // 4 + 1
        budget.reserve_model_call(estimated_input=estimated, max_output=max_output_tokens)
        if not self._outputs:
            raise RuntimeError("fake LLM has no scripted output")
        payload = self._outputs.pop(0)
        value = schema.model_validate(payload)
        output_tokens = len(json.dumps(payload, ensure_ascii=False)) // 4 + 1
        return StructuredGeneration(
            value=value,
            usage=LLMUsage(input_tokens=estimated, output_tokens=output_tokens),
            model_id=self._model_id,
            finish_reason="stop",
        )


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key_file: Path,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key_file = Path(api_key_file)
        self._timeout_seconds = timeout_seconds

    def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        schema: type[T],
        budget: AnalysisBudget,
        max_output_tokens: int = 1000,
    ) -> StructuredGeneration:
        estimated = sum(len(message.get("content", "")) for message in messages) // 4 + 1
        budget.reserve_model_call(estimated_input=estimated, max_output=max_output_tokens)
        api_key = read_api_key(self._api_key_file)
        if not api_key:
            raise RuntimeError(
                f"no API key in {self._api_key_file}; paste one (see infra/local-secrets/llm_api_key)"
            )
        response = httpx.post(
            f"{self._base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": self._model,
                "messages": messages,
                "max_tokens": max_output_tokens,
                "temperature": 0,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": schema.__name__,
                        "strict": True,
                        "schema": schema.model_json_schema(),
                    },
                },
            },
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        value = schema.model_validate_json(content)
        usage = payload.get("usage") or {}
        return StructuredGeneration(
            value=value,
            usage=LLMUsage(
                input_tokens=int(usage.get("prompt_tokens", estimated)),
                output_tokens=int(usage.get("completion_tokens", 0)),
            ),
            model_id=str(payload.get("model", self._model)),
            finish_reason=str(payload["choices"][0].get("finish_reason", "unknown")),
        )


@dataclass(frozen=True)
class ProviderResolution:
    """Outcome of resolving the configured provider.

    ``provider`` is None when the deterministic template path runs instead, and
    ``warning`` then says why - a half-configured endpoint (missing model, or a
    key file that is absent/empty) degrades to the deterministic path *loudly*
    rather than failing every analysis or silently pretending a model was called.
    """

    provider: LLMProvider | None
    warning: str | None = None


def resolve_provider(settings) -> ProviderResolution:
    if settings.llm_provider == "fake":
        return ProviderResolution(provider=None)
    if settings.llm_provider != "openai-compatible":
        # An unknown provider name is a configuration error, not a degradation.
        raise RuntimeError(f"unsupported LLM provider '{settings.llm_provider}'")
    if not settings.llm_model:
        return ProviderResolution(
            provider=None,
            warning=(
                "AIND_LLM_PROVIDER=openai-compatible but AIND_LLM_MODEL is empty; "
                "using the deterministic template"
            ),
        )
    key_file = settings.llm_api_key_file
    if key_file is None:
        return ProviderResolution(
            provider=None,
            warning=(
                "AIND_LLM_PROVIDER=openai-compatible but AIND_LLM_API_KEY_FILE is unset; "
                "using the deterministic template"
            ),
        )
    if read_api_key(key_file) is None:
        return ProviderResolution(
            provider=None,
            warning=(
                f"LLM key file {key_file} holds no key yet; using the deterministic "
                "template (paste the API key into infra/local-secrets/llm_api_key)"
            ),
        )
    return ProviderResolution(
        provider=OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key_file=key_file,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    )


def configured_provider(settings) -> LLMProvider | None:
    """Backwards-compatible strict accessor (provider or None)."""
    return resolve_provider(settings).provider
