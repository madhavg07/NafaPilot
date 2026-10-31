"""Schemas for everything that crosses the LLM boundary.

Inputs (contexts) are built by our code from validated data. Outputs are what the LLM
returns; they are parsed strictly (extra fields and unknown values are rejected) and are
only ever treated as *suggestions*. Profit and policy decisions are made elsewhere, by
deterministic Python.
"""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Language = Literal["en", "hinglish"]


class CampaignType(StrEnum):
    TARGETED_OFFER = "targeted_offer"
    OFF_PEAK = "off_peak"
    NEW_CUSTOMER = "new_customer"
    NO_ACTION = "no_action"


class TargetGroup(StrEnum):
    ELIGIBLE_EXISTING = "eligible_existing_customers"
    NEW_CUSTOMERS = "new_customers"
    ALL_WALK_INS = "all_walk_ins"
    NONE = "none"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- outputs


class ScenarioAssumption(_Strict):
    incremental_orders: int = Field(ge=0, le=100_000)
    existing_discounted_orders: int = Field(ge=0, le=100_000)


class ScenarioAssumptions(_Strict):
    conservative: ScenarioAssumption
    expected: ScenarioAssumption
    optimistic: ScenarioAssumption

    @model_validator(mode="after")
    def _ordered(self) -> ScenarioAssumptions:
        c = self.conservative.incremental_orders
        e = self.expected.incremental_orders
        o = self.optimistic.incremental_orders
        if not c <= e <= o:
            raise ValueError(
                "incremental_orders must satisfy conservative <= expected <= optimistic"
            )
        return self


class CampaignCandidate(_Strict):
    campaign_type: CampaignType
    discount_inr: int = Field(ge=0, le=100_000, description="Whole rupees off per order")
    target_group: TargetGroup
    assumptions: ScenarioAssumptions
    rationale: str = Field(min_length=1, max_length=500)
    cited_facts: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _no_action_is_empty(self) -> CampaignCandidate:
        if self.campaign_type is CampaignType.NO_ACTION:
            zero = ScenarioAssumption(incremental_orders=0, existing_discounted_orders=0)
            a = self.assumptions
            if self.discount_inr != 0 or not (a.conservative == a.expected == a.optimistic == zero):
                raise ValueError("no_action must have zero discount and zero assumptions")
        return self


class CandidateSet(_Strict):
    candidates: list[CampaignCandidate] = Field(min_length=1, max_length=5)


class ExtraOrders(_Strict):
    """The AI's estimate of extra orders the offer causes in 28 days, per scenario."""

    conservative: int = Field(ge=0, le=100_000)
    expected: int = Field(ge=0, le=100_000)
    optimistic: int = Field(ge=0, le=100_000)

    @model_validator(mode="after")
    def _ordered(self) -> ExtraOrders:
        if not self.conservative <= self.expected <= self.optimistic:
            raise ValueError("extra orders must satisfy conservative <= expected <= optimistic")
        return self


class PromotionCandidate(_Strict):
    """One product, bundle or cart offer proposed by the AI. Prices, costs and profit are
    never part of it: code looks those up and calculates them."""

    kind: Literal["product", "bundle", "cart"]
    skus: list[str] = Field(default_factory=list, max_length=3)
    discount_inr: int = Field(ge=1, le=100_000, description="Whole rupees off")
    min_cart_value_inr: int | None = Field(default=None, ge=1, le=1_000_000)
    hours: list[int] | None = Field(default=None, max_length=24)
    weekdays_only: bool = False
    extra_orders: ExtraOrders
    rationale: str = Field(min_length=1, max_length=500)
    cited_facts: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def _shape(self) -> PromotionCandidate:
        if self.kind == "product" and len(self.skus) != 1:
            raise ValueError("a product offer names exactly one sku")
        if self.kind == "bundle" and not (2 <= len(set(self.skus)) == len(self.skus) <= 3):
            raise ValueError("a bundle names 2 or 3 different skus")
        if self.kind == "cart" and (self.skus or self.min_cart_value_inr is None):
            raise ValueError("a cart offer names no skus and needs min_cart_value_inr")
        if self.kind != "cart" and self.min_cart_value_inr is not None:
            raise ValueError("only a cart offer has min_cart_value_inr")
        if self.hours is not None and (not self.hours or any(not 0 <= h <= 23 for h in self.hours)):
            raise ValueError("hours must be a non-empty list of hours 0-23")
        return self


class PromotionCandidateSet(_Strict):
    candidates: list[PromotionCandidate] = Field(min_length=1, max_length=6)


class Explanation(_Strict):
    text: str = Field(min_length=1, max_length=1500)
    lang: Language
    referenced_record_ids: list[str] = Field(default_factory=list, max_length=20)


class PingResult(_Strict):
    ok: bool


# --------------------------------------------------------------------------- inputs


class PolicyLimits(BaseModel):
    discount_cap_pct: Decimal = Field(ge=0, le=100)
    max_loss_inr: Decimal = Field(ge=0)
    min_margin_pct: Decimal | None = Field(default=None, ge=0, le=100)
    budget_inr: Decimal | None = Field(default=None, ge=0)


class FeedbackNote(BaseModel):
    campaign_type: CampaignType
    decision: Literal["approved", "rejected", "edited"]
    reason: str | None = Field(default=None, max_length=300)


class CandidateContext(BaseModel):
    """Validated merchant summary handed to the LLM. Only facts our analytics produced."""

    merchant_id: str
    merchant_name: str
    average_order_value_inr: Decimal = Field(gt=0)
    variable_cost_per_order_inr: Decimal | None = Field(default=None, ge=0)
    observed_facts: list[str] = Field(default_factory=list, max_length=20)
    policy: PolicyLimits
    allowed_campaign_types: list[CampaignType] = Field(
        default_factory=lambda: list(CampaignType), min_length=1
    )
    preferences: dict[str, str] = Field(default_factory=dict)
    feedback_history: list[FeedbackNote] = Field(default_factory=list, max_length=20)


class ProductFact(BaseModel):
    sku: str
    name: str
    category: str
    price_inr: Decimal
    unit_cost_inr: Decimal | None
    margin_pct: float | None
    orders_28d: int
    units_28d: int
    trend_pct: float | None = Field(description="Units sold, last 28 days vs the 28 before.")


class PairFact(BaseModel):
    sku_a: str
    sku_b: str
    orders_together_28d: int
    confidence_a_to_b: float
    lift: float


class SlotFact(BaseModel):
    label: str
    hours: list[int]
    weekdays_only: bool
    change_pct: float


class PromotionContext(BaseModel):
    """Validated product-level summary handed to the LLM. Only products the merchant
    selected appear here, and every number comes from our analytics."""

    merchant_id: str
    merchant_name: str
    products: list[ProductFact] = Field(min_length=1, max_length=60)
    pairs: list[PairFact] = Field(default_factory=list, max_length=20)
    basket_orders_28d: int
    basket_average_inr: Decimal
    basket_p75_inr: Decimal
    demand_changes: list[SlotFact] = Field(default_factory=list, max_length=10)
    observed_facts: list[str] = Field(default_factory=list, max_length=20)
    policy: PolicyLimits
    allowed_kinds: list[Literal["product", "bundle", "cart"]] = Field(min_length=1)
    preferences: dict[str, str] = Field(default_factory=dict)


class ScenarioProfits(BaseModel):
    conservative: Decimal
    expected: Decimal
    optimistic: Decimal


class ExplainRecord(BaseModel):
    """One stored engine result. The LLM may only restate what is in these records."""

    record_id: str
    campaign_type: str
    offer: str | None = Field(default=None, description="Plain description of the offer.")
    discount_inr: int = Field(ge=0)
    scenario_profits_inr: ScenarioProfits
    break_even_orders: int | None = None
    policy_passed: bool
    policy_reasons: list[str] = Field(default_factory=list)
    selected: bool = False


class ExplanationRequest(BaseModel):
    merchant_name: str
    lang: Language = "en"
    question: str | None = Field(default=None, max_length=300)
    records: list[ExplainRecord] = Field(min_length=1, max_length=10)
    context_facts: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Observed facts from analytics that the explanation may cite.",
    )
