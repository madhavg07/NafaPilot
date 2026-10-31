"""The provider interface. The rest of the app depends only on this, never on Gemini."""

from __future__ import annotations

from typing import Protocol

from app.schemas.llm import (
    CandidateContext,
    CandidateSet,
    Explanation,
    ExplanationRequest,
    PromotionCandidateSet,
    PromotionContext,
)


class LLMProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    def generate_candidates(self, ctx: CandidateContext) -> CandidateSet:
        """Propose campaign candidates. Output is a suggestion, never a decision."""
        ...

    def generate_promotions(self, ctx: PromotionContext) -> PromotionCandidateSet:
        """Propose product, bundle and cart offers on the merchant's selected products."""
        ...

    def explain(self, request: ExplanationRequest) -> Explanation:
        """Explain stored engine results in plain English or Hinglish."""
        ...

    def ping(self) -> None:
        """Smallest possible round-trip. Raises an LLMError subclass on failure."""
        ...
