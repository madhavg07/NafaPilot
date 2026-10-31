"""API models for recommendation runs, explanations and merchant preferences (M7)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.decision import CampaignEconomics, PolicyDecision
from app.schemas.llm import CampaignType, Language
from app.schemas.promotions import PROMOTION_KINDS, PromotionKind, PromotionSpec

NOTICE = (
    "Synthetic prototype. Candidates are suggestions from a language model; profit and "
    "policy results come from deterministic code; the recommendation is chosen by a fixed "
    "rule, not by the model. Nothing has been executed."
)


class RecommendationRequest(BaseModel):
    campaign_costs_inr: dict[CampaignType, Decimal] = Field(
        default_factory=dict,
        description="Campaign cost C (costs besides the discount) per campaign type. The "
        "language model does not supply costs; a type without one is evaluated as "
        "insufficient_data and cannot be recommended.",
    )
    lang: Language = "en"
    requested_by: str = Field(default="merchant", min_length=1, max_length=80)

    @field_validator("campaign_costs_inr")
    @classmethod
    def _costs_valid(cls, v: dict[CampaignType, Decimal]) -> dict[CampaignType, Decimal]:
        if CampaignType.NO_ACTION in v:
            raise ValueError("no_action has no campaign cost")
        if any(c < 0 for c in v.values()):
            raise ValueError("campaign costs cannot be negative")
        return v


class ExplainRequestIn(BaseModel):
    lang: Language = "en"
    question: str | None = Field(default=None, max_length=300)
    requested_by: str = Field(default="merchant", min_length=1, max_length=80)


class CandidateOut(BaseModel):
    record_id: str = Field(description="Letter used in explanations (A, B, ...).")
    status: Literal["evaluated", "filtered_by_preference"]
    campaign_type: str
    discount_inr: int
    target_group: str
    llm_rationale: str = Field(description="Model-written suggestion text; not verified.")
    filter_reasons: list[str]
    cost_source: Literal["request", "missing"]
    campaign_id: uuid.UUID | None = Field(description="Persisted campaign, if evaluated.")
    input_hash: str | None
    economics: CampaignEconomics | None = Field(description="Snapshot at generation time.")
    policy: PolicyDecision | None = Field(description="Snapshot at generation time.")
    promotion: PromotionSpec | None = Field(
        default=None, description="Product-level offer details (product, bundle or cart)."
    )
    notes: list[str] = Field(default_factory=list, description="How code built the inputs.")


class PromotionRunRequest(BaseModel):
    lang: Language = "en"
    kinds: list[PromotionKind] = Field(default_factory=lambda: list(PROMOTION_KINDS), min_length=1)
    campaign_costs_inr: dict[PromotionKind, Decimal | None] = Field(
        default_factory=dict,
        description="Running cost per offer kind for this run. Missing kinds use the "
        "merchant's saved usual costs.",
    )
    requested_by: str = Field(default="merchant", min_length=1, max_length=80)

    @field_validator("campaign_costs_inr")
    @classmethod
    def _costs_ok(cls, v: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
        if any(c is not None and c < 0 for c in v.values()):
            raise ValueError("campaign costs cannot be negative")
        return v


class PromotionEditIn(BaseModel):
    """A merchant's change to a product, bundle or cart offer. Code rebuilds the rest."""

    discount_inr: Decimal = Field(gt=0, le=100_000)
    min_cart_value_inr: Decimal | None = Field(default=None, gt=0, le=1_000_000)
    campaign_cost_inr: Decimal | None = Field(default=None, ge=0, le=1_000_000)
    created_by: str = Field(default="merchant", min_length=1, max_length=80)
    change_note: str | None = Field(default=None, max_length=500)


class SelectionOut(BaseModel):
    campaign_id: uuid.UUID | None
    record_id: str | None
    campaign_type: str
    rule: str
    reason: str


class ExplanationOut(BaseModel):
    explanation_id: uuid.UUID
    lang: Language
    question: str | None
    source: Literal["llm", "template"]
    provider: str
    model: str
    grounded: bool
    text: str
    referenced_records: list[str]
    fallback_reason: str | None
    created_at: datetime


class LearningOut(BaseModel):
    model_retrained: Literal[False] = False
    preference_learning: str
    performance_learning: str
    preferences_applied: dict[str, str]
    past_decisions_used: int


class RecommendationRunOut(BaseModel):
    run_id: uuid.UUID
    merchant_id: str
    created_at: datetime
    provider: str
    model: str
    prompt_version: str
    context: dict[str, Any]
    candidates: list[CandidateOut]
    selection: SelectionOut
    explanations: list[ExplanationOut]
    learning: LearningOut
    result_kind: Literal["prediction"] = "prediction"
    execution_status: Literal["not_executed"] = "not_executed"
    data_source: str
    is_synthetic: bool
    notice: str = NOTICE


class PreferenceOut(BaseModel):
    value: str
    set_by: str
    set_at: datetime
    campaign_version_id: uuid.UUID | None


class PreferencesOut(BaseModel):
    merchant_id: str
    preferences: dict[str, PreferenceOut]
    source_note: str = (
        "Only preferences the merchant set explicitly when rejecting a campaign. "
        "Free-text reasons are not converted into preferences."
    )
