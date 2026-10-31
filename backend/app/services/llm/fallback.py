"""Ordered fallback across providers, e.g. Sarvam first, then Gemini.

Each call is tried on the first provider; if it raises any LLMError (not configured, bad
key, quota, timeout, server error, invalid output), the same call is tried on the next one.
`name` and `model` always report the provider that answered the most recent call, so runs
and explanations record who actually produced them. If every provider fails, the last
provider's error type is raised with a message listing each failure.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from app.schemas.llm import (
    CandidateContext,
    CandidateSet,
    Explanation,
    ExplanationRequest,
    PromotionCandidateSet,
    PromotionContext,
)
from app.services.llm.base import LLMProvider
from app.services.llm.errors import LLMError

T = TypeVar("T")


class FallbackProvider:
    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("at least one provider is required")
        self._providers = providers
        self._name = providers[0].name
        self._model = providers[0].model
        self.attempts: list[tuple[str, str]] = []  # (provider, outcome) for the last call

    @property
    def name(self) -> str:
        return self._name

    @property
    def model(self) -> str:
        return self._model

    @property
    def chain(self) -> list[str]:
        return [p.name for p in self._providers]

    def _run(self, call: Callable[[LLMProvider], T]) -> T:
        self.attempts = []
        last: LLMError | None = None
        for p in self._providers:
            try:
                result = call(p)
            except LLMError as err:
                self.attempts.append((p.name, err.error_code))
                last = err
                continue
            self.attempts.append((p.name, "ok"))
            self._name, self._model = p.name, p.model
            return result
        assert last is not None
        summary = "; ".join(f"{n}: {o}" for n, o in self.attempts)
        failure = type(last)(f"All AI providers failed ({summary}). {last.message}")
        self._name = self._providers[-1].name
        self._model = self._providers[-1].model
        raise failure

    def generate_candidates(self, ctx: CandidateContext) -> CandidateSet:
        return self._run(lambda p: p.generate_candidates(ctx))

    def generate_promotions(self, ctx: PromotionContext) -> PromotionCandidateSet:
        return self._run(lambda p: p.generate_promotions(ctx))

    def explain(self, request: ExplanationRequest) -> Explanation:
        return self._run(lambda p: p.explain(request))

    def ping(self) -> None:
        self._run(lambda p: p.ping())
