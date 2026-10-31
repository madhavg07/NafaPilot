"""Builds the configured provider, or an ordered fallback chain.

LLM_PROVIDER is one name ("sarvam", "gemini", "mock") or a comma-separated chain such as
"sarvam,gemini": each request goes to the first provider and moves to the next only if it
fails. A single provider that is not configured raises LLMNotConfigured (no silent
fallback). In a chain, an unconfigured provider is skipped and recorded; if none is
configured, LLMNotConfigured is raised.
"""

from __future__ import annotations

from typing import NoReturn

from app.config import Settings
from app.schemas.llm import (
    CandidateContext,
    CandidateSet,
    Explanation,
    ExplanationRequest,
    PromotionCandidateSet,
    PromotionContext,
)
from app.services.llm.base import LLMProvider
from app.services.llm.errors import LLMError, LLMNotConfigured
from app.services.llm.fallback import FallbackProvider
from app.services.llm.gemini import GeminiProvider
from app.services.llm.mock import MockProvider
from app.services.llm.sarvam import SarvamProvider


class _Unavailable:
    """Stands in for a chain member that could not be built; every call raises its error."""

    def __init__(self, name: str, model: str | None, error: LLMError) -> None:
        self.name = name
        self.model = model or "not set"
        self._error = error

    def _fail(self) -> NoReturn:
        raise type(self._error)(self._error.message)

    def generate_candidates(self, ctx: CandidateContext) -> CandidateSet:
        self._fail()

    def generate_promotions(self, ctx: PromotionContext) -> PromotionCandidateSet:
        self._fail()

    def explain(self, request: ExplanationRequest) -> Explanation:
        self._fail()

    def ping(self) -> None:
        self._fail()


def _build(name: str, settings: Settings, max_retries: int | None = None) -> LLMProvider:
    if name == "mock":
        return MockProvider()
    common = dict(
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries if max_retries is None else max_retries,
        temperature=settings.llm_temperature,
        max_output_tokens=settings.llm_max_output_tokens,
    )
    if name == "sarvam":
        return SarvamProvider(
            api_key=settings.sarvam_key_value,
            model=settings.sarvam_model,
            base_url=settings.sarvam_base_url,
            reasoning_effort=settings.sarvam_reasoning_effort,
            **common,  # type: ignore[arg-type]
        )
    return GeminiProvider(
        api_key=settings.gemini_key_value,
        model=settings.gemini_model,
        **common,  # type: ignore[arg-type]
    )


def get_provider(settings: Settings) -> LLMProvider:
    chain = settings.llm_provider_chain
    if len(chain) == 1:
        return _build(chain[0], settings)
    members: list[LLMProvider] = []
    for i, name in enumerate(chain):
        # Earlier members retry at most once, so a struggling provider hands over quickly.
        retries = None if i == len(chain) - 1 else min(settings.llm_max_retries, 1)
        try:
            members.append(_build(name, settings, retries))
        except LLMNotConfigured as err:
            members.append(_Unavailable(name, _model_setting(name, settings), err))
    if all(isinstance(m, _Unavailable) for m in members):
        raise LLMNotConfigured(
            "No AI provider in LLM_PROVIDER is configured ("
            + ", ".join(chain)
            + "). Add the keys to the .env file in the project root."
        )
    return FallbackProvider(members)


def _model_setting(name: str, settings: Settings) -> str | None:
    if name == "mock":
        return MockProvider.model
    return settings.sarvam_model if name == "sarvam" else settings.gemini_model


def _member_status(name: str, settings: Settings) -> dict[str, object]:
    if name == "mock":
        return {
            "provider": "mock",
            "model": MockProvider.model,
            "configured": True,
            "key_present": False,
        }
    key = settings.sarvam_key_value if name == "sarvam" else settings.gemini_key_value
    model = _model_setting(name, settings)
    return {
        "provider": name,
        "model": model,
        "configured": key is not None and bool(model),
        "key_present": key is not None,
    }


def llm_status(settings: Settings) -> dict[str, object]:
    """Configuration status without any network call. Never includes a key itself.

    Top-level fields describe the first provider in the chain (backwards compatible);
    `chain` lists every provider in the order they are tried.
    """
    members = [_member_status(n, settings) for n in settings.llm_provider_chain]
    first = dict(members[0])
    if first["provider"] == "mock":
        first["key_present"] = settings.gemini_key_value is not None
    first["chain"] = members
    first["configured"] = any(bool(m["configured"]) for m in members)
    return first
