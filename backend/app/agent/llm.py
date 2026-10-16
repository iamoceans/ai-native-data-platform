"""Structured LLM adapters with deterministic offline behavior."""

from __future__ import annotations

import json
from copy import copy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

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
    model_calls: int = 1
    # Kept separate from the configured fallback: only endpoint evidence can
    # establish an exact model identity.
    reported_model_id: str | None = None


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


RESPONSE_FORMATS = ("json_object", "json_schema")


def schema_instruction(schema: type[BaseModel]) -> str:
    """The system message that carries the schema in json_object mode.

    ``response_format: json_schema`` is an OpenAI structured-outputs feature that
    DeepSeek and most OpenAI-compatible servers reject with HTTP 400, so the
    portable mode sends ``json_object`` instead. That mode requires the word
    "json" to appear in the prompt, and the model still has to be told which
    fields are allowed - the same schema the response_format used to carry.
    """
    return (
        "Reply with a single json object and nothing else - no prose, no markdown "
        "fence, no extra keys. It must validate against this JSON Schema: "
        + json.dumps(schema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    )


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
        api_key_file: Path | None = None,
        api_key: str | None = None,
        timeout_seconds: float = 30.0,
        response_format: str = "json_object",
    ) -> None:
        if response_format not in RESPONSE_FORMATS:
            # A misspelled mode is a configuration error, not a degradation.
            raise RuntimeError(
                f"unsupported LLM response format '{response_format}'; expected one of "
                f"{', '.join(RESPONSE_FORMATS)}"
            )
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key_file = Path(api_key_file) if api_key_file is not None else None
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._response_format = response_format

    def _credential(self) -> str:
        api_key = self._api_key or (
            read_api_key(self._api_key_file) if self._api_key_file is not None else None
        )
        if not api_key:
            raise RuntimeError(
                "no LLM credential configured; bind AIND_LLM_API_KEY or a mounted key file"
            )
        return api_key

    def list_models(self) -> list[str]:
        """Query the configured endpoint; never infer versions from aliases."""
        response = httpx.get(
            f"{self._base_url}/models",
            headers={"Authorization": f"Bearer {self._credential()}"},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(rows, list) or any(
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"].strip()
            for row in rows
        ):
            raise ValueError("model list violates the OpenAI-compatible response schema")
        return sorted({row["id"] for row in rows})

    def _request_messages(
        self, *, messages: list[dict[str, str]], schema: type[BaseModel]
    ) -> list[dict[str, str]]:
        if self._response_format == "json_schema":
            # The schema is enforced by the provider; no prompt addition needed.
            return messages
        return [{"role": "system", "content": schema_instruction(schema)}, *messages]

    def _response_format_body(self, schema: type[BaseModel]) -> dict[str, Any]:
        if self._response_format == "json_schema":
            return {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            }
        return {"type": "json_object"}

    def generate_structured(
        self,
        *,
        messages: list[dict[str, str]],
        schema: type[T],
        budget: AnalysisBudget,
        max_output_tokens: int = 1000,
    ) -> StructuredGeneration:
        request_messages = self._request_messages(messages=messages, schema=schema)
        estimated = sum(len(message.get("content", "")) for message in request_messages) // 4 + 1
        api_key = self._credential()
        total_input = total_output = 0
        for attempt in range(2):
            if hasattr(budget, "max_model_calls") and budget.model_calls + attempt + 1 > budget.max_model_calls:
                from app.agent.budget import BudgetExceeded
                raise BudgetExceeded("model_calls")
            budget.ensure_time()
            # Validate each attempt against the caller's original reservation.
            # TurnBudget is mutable: accumulating temporary checks on it would
            # charge the first estimate again when reserving a format repair.
            # The orchestrator's durable in-flight reservation stays intact;
            # the caller settles the returned aggregate usage once.
            copy(budget).reserve_model_call(
                estimated_input=total_input + estimated,
                max_output=total_output + max_output_tokens,
            )
            response = httpx.post(
                f"{self._base_url}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": self._model, "messages": request_messages,
                    "max_tokens": max_output_tokens, "temperature": 0,
                    "response_format": self._response_format_body(schema),
                },
                timeout=min(self._timeout_seconds, max(0.1, budget.remaining_seconds()))
                if hasattr(budget, "remaining_seconds") else self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            usage = payload.get("usage") or {}
            total_input += max(0, int(usage.get("prompt_tokens", estimated)))
            total_output += max(0, int(usage.get("completion_tokens", max_output_tokens)))
            try:
                value = schema.model_validate_json(content)
            except ValidationError:
                if attempt:
                    raise ValueError("model output still violates the closed schema after format repair") from None
                # Never replay arbitrary model output or validation values as instructions.
                request_messages = [*request_messages, {"role": "system", "content":
                    "The previous response was invalid JSON or violated the schema. "
                    "Generate a corrected object for the original request. Do not add keys. "
                    + schema_instruction(schema)}]
                estimated = sum(len(m.get("content", "")) for m in request_messages) // 4 + 1
                continue
            if isinstance(budget, AnalysisBudget):
                budget.record_model_usage(input_tokens=total_input, output_tokens=total_output)
            elif budget.input_tokens + total_input > budget.max_input_tokens or budget.output_tokens + total_output > budget.max_output_tokens:
                from app.agent.budget import BudgetExceeded
                raise BudgetExceeded("model_tokens")
            return StructuredGeneration(
                value=value, usage=LLMUsage(total_input, total_output),
                model_id=str(payload.get("model", self._model)),
                finish_reason=str(payload["choices"][0].get("finish_reason", "unknown")),
                model_calls=attempt + 1,
                reported_model_id=payload.get("model")
                if isinstance(payload.get("model"), str) and payload["model"].strip()
                else None,
            )
        raise AssertionError("unreachable")



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
    secret = getattr(settings, "llm_api_key", None)
    api_key = secret.get_secret_value() if secret is not None else None
    if key_file is None and not api_key:
        return ProviderResolution(
            provider=None,
            warning=(
                "AIND_LLM_PROVIDER=openai-compatible but AIND_LLM_API_KEY and "
                "AIND_LLM_API_KEY_FILE are unset; "
                "using the deterministic template"
            ),
        )
    if not api_key and read_api_key(key_file) is None:
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
            api_key=api_key,
            timeout_seconds=settings.llm_timeout_seconds,
            response_format=settings.llm_response_format,
        )
    )


def configured_provider(settings) -> LLMProvider | None:
    """Backwards-compatible strict accessor (provider or None)."""
    return resolve_provider(settings).provider
