"""Gemini provider (google-genai SDK).

Structure:
- GeminiTransport: the only code that touches the SDK. Tests replace it with a fake,
  so automated tests never make network calls.
- GeminiProvider: retries, error mapping, safety handling and strict Pydantic
  validation of every response.

The API key is passed only to the SDK client. It is never logged, returned or put in an
error message; upstream error text is redacted before use.
"""

from __future__ import annotations

import hashlib
import json
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.logging_config import redact
from app.schemas.llm import (
    CandidateContext,
    CandidateSet,
    Explanation,
    ExplanationRequest,
    PingResult,
    PromotionCandidateSet,
    PromotionContext,
)
from app.services.llm import prompts
from app.services.llm.errors import (
    LLMAuthError,
    LLMError,
    LLMInvalidResponse,
    LLMNotConfigured,
    LLMQuotaExceeded,
    LLMSafetyBlocked,
    LLMTimeout,
    LLMUpstreamError,
)

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class RawResponse:
    text: str | None
    block_reason: str | None = None
    finish_reason: str | None = None


class GeminiTransport(Protocol):
    def generate(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        json_schema: dict[str, Any],
        temperature: float,
        max_output_tokens: int,
    ) -> RawResponse: ...


def _enum_name(value: object) -> str | None:
    if value is None:
        return None
    name = getattr(value, "name", None)
    return str(name if name is not None else value)


class SdkTransport:
    """Real transport using google-genai. Imported lazily so tests never need the network."""

    def __init__(self, api_key: str, timeout_seconds: float) -> None:
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=int(timeout_seconds * 1000)),
        )

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
        config = self._types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=json_schema,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
        )
        response = self._client.models.generate_content(model=model, contents=prompt, config=config)
        feedback = getattr(response, "prompt_feedback", None)
        block_reason = _enum_name(getattr(feedback, "block_reason", None))
        candidates = getattr(response, "candidates", None) or []
        finish_reason = (
            _enum_name(getattr(candidates[0], "finish_reason", None)) if candidates else None
        )
        text: str | None
        try:
            text = response.text
        except (ValueError, AttributeError):
            text = None
        return RawResponse(text=text, block_reason=block_reason, finish_reason=finish_reason)

    def list_models(self) -> list[str]:
        return [m.name for m in self._client.models.list() if m.name]


def _is_timeout(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    return any("Timeout" in cls.__name__ for cls in type(exc).__mro__)


_SAFETY_FINISH = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}


class GeminiProvider:
    name = "gemini"
    _label = "Gemini"
    _key_var = "GEMINI_API_KEY"
    _model_var = "GEMINI_MODEL"

    def __init__(
        self,
        *,
        api_key: str | None,
        model: str | None,
        timeout_seconds: float = 20.0,
        max_retries: int = 2,
        temperature: float = 0.2,
        max_output_tokens: int = 2048,
        transport: GeminiTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise LLMNotConfigured(
                "GEMINI_API_KEY is not set. Add it to the .env file in the project root."
            )
        if not model:
            raise LLMNotConfigured(
                "GEMINI_MODEL is not set. Add it to the .env file in the project root."
            )
        self.model = model
        self._secrets = [api_key]
        self._max_retries = max_retries
        self._temperature = temperature
        self._max_output_tokens = max_output_tokens
        self._sleep = sleep
        self._transport: GeminiTransport = transport or SdkTransport(api_key, timeout_seconds)

    # ------------------------------------------------------------------ public API

    def generate_candidates(self, ctx: CandidateContext) -> CandidateSet:
        return self._call(
            "candidates", prompts.CANDIDATE_SYSTEM, prompts.candidate_prompt(ctx), CandidateSet
        )

    def generate_promotions(self, ctx: PromotionContext) -> PromotionCandidateSet:
        return self._call(
            "promotions",
            prompts.PROMOTION_SYSTEM,
            prompts.promotion_prompt(ctx),
            PromotionCandidateSet,
        )

    def explain(self, request: ExplanationRequest) -> Explanation:
        result = self._call(
            "explain", prompts.EXPLAIN_SYSTEM, prompts.explain_prompt(request), Explanation
        )
        known = {r.record_id for r in request.records}
        unknown = [rid for rid in result.referenced_record_ids if rid not in known]
        if unknown:
            raise LLMInvalidResponse(
                f"Explanation referenced {len(unknown)} record id(s) that were not provided."
            )
        if result.lang != request.lang:
            raise LLMInvalidResponse("Explanation language does not match the request.")
        return result

    def ping(self) -> None:
        result = self._call("ping", prompts.PING_SYSTEM, "ping", PingResult)
        if not result.ok:
            raise LLMInvalidResponse("Ping returned ok=false.")

    # ------------------------------------------------------------------ internals

    def _call(self, purpose: str, system: str, prompt: str, out_model: type[T]) -> T:
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        started = time.monotonic()
        attempt = 0
        while True:
            try:
                raw = self._transport.generate(
                    model=self.model,
                    system=system,
                    prompt=prompt,
                    json_schema=out_model.model_json_schema(),
                    temperature=self._temperature,
                    max_output_tokens=self._max_output_tokens,
                )
                result = self._parse(raw, out_model)
            except Exception as exc:  # our LLMErrors, or SDK / network errors to map
                failure = exc if isinstance(exc, LLMError) else self._map_exception(exc)
                retryable = isinstance(failure, LLMQuotaExceeded | LLMTimeout | LLMUpstreamError)
                if retryable and attempt < self._max_retries:
                    self._backoff(attempt)
                    attempt += 1
                    continue
                self._log(purpose, prompt_hash, started, failure.error_code)
                raise failure from None
            self._log(purpose, prompt_hash, started, "ok")
            return result

    def _parse(self, raw: RawResponse, out_model: type[T]) -> T:
        if raw.block_reason:
            raise LLMSafetyBlocked(
                f"Request blocked by {self._label} safety filters ({raw.block_reason})"
            )
        if raw.finish_reason in _SAFETY_FINISH:
            raise LLMSafetyBlocked(f"Response stopped by {self._label} ({raw.finish_reason}).")
        if raw.finish_reason == "MAX_TOKENS":
            raise LLMInvalidResponse("Response cut off (MAX_TOKENS); raise LLM_MAX_OUTPUT_TOKENS.")
        if not raw.text or not raw.text.strip():
            raise LLMInvalidResponse(f"{self._label} returned an empty response.")
        try:
            data = json.loads(raw.text)
        except json.JSONDecodeError:
            raise LLMInvalidResponse(f"{self._label} response was not valid JSON.") from None
        try:
            return out_model.model_validate(data)
        except ValidationError as ve:
            fields = sorted({".".join(str(p) for p in e["loc"]) or "<root>" for e in ve.errors()})
            raise LLMInvalidResponse(
                f"{self._label} response failed schema validation ({len(ve.errors())} error(s) in: "
                f"{', '.join(fields[:5])})."
            ) from None

    def _map_exception(self, exc: Exception) -> LLMError:
        if _is_timeout(exc):
            return LLMTimeout(f"{self._label} did not respond before the timeout.")
        code = getattr(exc, "code", None)
        status = getattr(exc, "status", None)
        detail = redact(str(getattr(exc, "message", "") or ""), self._secrets)[:200]
        if isinstance(code, int):
            if code == 429 or status == "RESOURCE_EXHAUSTED":
                return LLMQuotaExceeded(
                    f"{self._label} quota or rate limit reached. Try again later."
                )
            if code in (408, 504):
                return LLMTimeout(f"{self._label} timed out upstream.")
            if code >= 500:
                return LLMUpstreamError(f"{self._label} server error {code}.")
            if code in (401, 403):
                return LLMAuthError(
                    f"{self._label} rejected the API key ({code}). Check {self._key_var}."
                )
            if code == 404:
                return LLMAuthError(f"Model not found (404). Check {self._model_var}. {detail}")
            return LLMAuthError(f"{self._label} rejected the request ({code} {status}). {detail}")
        if any(n in type(exc).__name__ for n in ("Connect", "Network", "Proxy")):
            return LLMUpstreamError(f"Could not reach the {self._label} API (network error).")
        return LLMUpstreamError(f"Unexpected {self._label} client error ({type(exc).__name__}).")

    def _backoff(self, attempt: int) -> None:
        self._sleep(min(8.0, 0.5 * 2**attempt) + random.uniform(0, 0.25))

    def _log(self, purpose: str, prompt_hash: str, started: float, status: str) -> None:
        log.info(
            "llm_call provider=%s model=%s purpose=%s status=%s latency_ms=%d "
            "prompt_sha256=%s prompt_version=%s",
            self.name,
            self.model,
            purpose,
            status,
            int((time.monotonic() - started) * 1000),
            prompt_hash,
            prompts.PROMPT_VERSION,
        )
