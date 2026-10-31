"""Sarvam AI provider (chat completions v2 over HTTPS).

It reuses GeminiProvider's retry, error mapping and strict Pydantic validation; only the
transport differs. SarvamTransport is the only code that talks to Sarvam, so tests replace
it with a fake and never make network calls.

API (docs.sarvam.ai, "Chat completions v2"):
  POST {base}/v2/chat/completions, header `api-subscription-key: <key>`
  body: model, messages[system,user], temperature, max_tokens, reasoning_effort,
        response_format {type: "json_schema", json_schema: {name, schema, strict}}
  200: choices[0].message.content, choices[0].finish_reason (stop|length|content_filter|...)
  errors: JSON {"error": {"message", ...}} with 400/403/404/429/502/503

The key is sent only in that header. It is never logged, returned or put in an error
message; upstream error text is redacted before use.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import Any

import httpx

from app.logging_config import redact
from app.services.llm.errors import LLMError, LLMInvalidResponse, LLMNotConfigured
from app.services.llm.gemini import GeminiProvider, GeminiTransport, RawResponse

_FINISH = {"length": "MAX_TOKENS", "content_filter": "SAFETY", "stop": "STOP"}
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class SarvamHTTPError(Exception):
    """A non-2xx response. `code`/`status` match what GeminiProvider's mapper reads."""

    def __init__(self, code: int, status: str | None, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


def _clean_json(text: str) -> str:
    """Strip markdown fences and any text around the outermost JSON object."""
    t = _FENCE.sub("", text.strip())
    start, end = t.find("{"), t.rfind("}")
    return t[start : end + 1] if start != -1 and end > start else t


class SarvamTransport:
    def __init__(
        self,
        api_key: str,
        timeout_seconds: float,
        base_url: str = "https://api.sarvam.ai",
        reasoning_effort: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self._key = api_key
        self._url = base_url.rstrip("/") + "/v2/chat/completions"
        self._reasoning = reasoning_effort or None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def generate(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
    ) -> RawResponse:
        body: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "nafapilot_output", "schema": json_schema, "strict": False},
            },
        }
        if self._reasoning:
            body["reasoning_effort"] = self._reasoning
        resp = self._client.post(
            self._url,
            json=body,
            headers={"api-subscription-key": self._key, "Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            status, message = None, ""
            try:
                err = resp.json().get("error") or {}
                status = err.get("code") or err.get("type")
                message = str(err.get("message") or "")
            except (ValueError, AttributeError):
                message = resp.text[:200]
            raise SarvamHTTPError(resp.status_code, status, message)
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return RawResponse(text=None)
        choice = choices[0]
        finish = choice.get("finish_reason")
        content = (choice.get("message") or {}).get("content")
        return RawResponse(
            text=_clean_json(content) if isinstance(content, str) else None,
            finish_reason=_FINISH.get(str(finish), str(finish).upper() if finish else None),
        )


class SarvamProvider(GeminiProvider):
    name = "sarvam"
    _label = "Sarvam"
    _key_var = "SARVAM_API_KEY"
    _model_var = "SARVAM_MODEL"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str | None,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
        base_url: str = "https://api.sarvam.ai",
        reasoning_effort: str | None = None,
        transport: GeminiTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise LLMNotConfigured(
                "SARVAM_API_KEY is not set. Add it to the .env file in the project root."
            )
        if not model:
            raise LLMNotConfigured(
                "SARVAM_MODEL is not set. Add it to the .env file in the project root."
            )
        self.model = model
        self._secrets = [api_key]
        self._max_retries = max_retries
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._sleep = sleep
        self._transport = transport or SarvamTransport(
            api_key, timeout_seconds, base_url, reasoning_effort
        )

    def _map_exception(self, exc: Exception) -> LLMError:
        if isinstance(exc, httpx.TimeoutException):
            return super()._map_exception(TimeoutError("timeout"))
        if isinstance(exc, httpx.TransportError):
            return super()._map_exception(ConnectionError("network"))
        if isinstance(exc, (json.JSONDecodeError, ValueError)):
            return LLMInvalidResponse("Sarvam response was not valid JSON.")
        if isinstance(exc, SarvamHTTPError):
            exc.message = redact(exc.message, self._secrets)
        return super()._map_exception(exc)
