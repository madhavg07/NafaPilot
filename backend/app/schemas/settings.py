"""Merchant settings edited in the app: policy limits, usual campaign costs, preferences.

Defaults: the policy comes from the dataset; the campaign costs below are starting
estimates the merchant should replace with their own. They are labelled as defaults in the
API and the UI until the merchant saves settings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.schemas.decision import PolicyConfig, ScenarioName
from app.schemas.llm import CampaignType

# Merchant-level campaign types, then the product-level offer kinds.
COST_TYPES = (
    "targeted_offer",
    "off_peak",
    "new_customer",
    "product_offer",
    "bundle_offer",
    "cart_offer",
)

# Starting estimates (running cost besides the discount), shown as defaults to replace.
DEFAULT_CAMPAIGN_COSTS: dict[str, Decimal | None] = {
    "targeted_offer": Decimal("1000"),
    "off_peak": Decimal("1200"),
    "new_customer": Decimal("800"),
    "product_offer": Decimal("500"),
    "bundle_offer": Decimal("600"),
    "cart_offer": Decimal("800"),
}

Target = Literal["new_customers", "existing_customers", "all"]
AvoidType = Literal["targeted_offer", "off_peak", "new_customer"]


@dataclass(frozen=True)
class SavedSettings:
    """The latest saved settings version for one merchant (read model)."""

    policy: PolicyConfig
    campaign_costs_inr: dict[str, Decimal | None]
    note: str | None
    created_by: str
    created_at: datetime


Money = Decimal


class PreferencesIn(BaseModel):
    """Explicit preferences. None = no preference (clears a saved one)."""

    target: Target | None = None
    max_discount_inr: int | None = Field(default=None, ge=0, le=100_000)
    avoid_campaign_type: AvoidType | None = None


class SettingsIn(BaseModel):
    discount_cap_pct: Money = Field(gt=0, le=50, description="Largest discount, % of order value")
    min_margin_pct: Money = Field(ge=0, le=90, description="Minimum margin after the discount, %")
    max_loss_inr: Money = Field(ge=0, le=1_000_000, description="Largest acceptable loss, ₹")
    risk_scenario: ScenarioName = Field(
        default="conservative", description="Which case the loss limit is checked on"
    )
    budget_inr: Money = Field(ge=0, le=1_000_000, description="Most to spend running one campaign")
    holdout_pct: int = Field(ge=5, le=50, description="% of customers kept out of a test")
    campaign_costs_inr: dict[str, Money | None] = Field(
        default_factory=dict, description="Usual running cost per campaign type, ₹ (None = skip)"
    )
    preferences: PreferencesIn = Field(default_factory=PreferencesIn)
    note: str | None = Field(default=None, max_length=300)
    updated_by: str = Field(default="merchant", min_length=1, max_length=80)

    @field_validator("campaign_costs_inr")
    @classmethod
    def _costs_valid(cls, v: dict[str, Decimal | None]) -> dict[str, Decimal | None]:
        unknown = sorted(set(v) - set(COST_TYPES))
        if unknown:
            raise ValueError(f"unknown campaign type(s): {', '.join(unknown)}")
        for k, c in v.items():
            if c is not None and not (Decimal(0) <= c <= Decimal(100_000)):
                raise ValueError(f"{k}: cost must be between 0 and 100000")
        # Only the types sent; the others keep their current values when saving.
        return {t: v[t] for t in COST_TYPES if t in v}


class PolicyOut(BaseModel):
    policy_version: int
    discount_cap_pct: float
    min_margin_pct: float
    max_loss_inr: float
    budget_inr: float
    holdout_pct: float
    risk_scenario: ScenarioName


class SettingsOut(BaseModel):
    merchant_id: str
    source: Literal["default", "merchant"] = Field(
        description="'default' until the merchant saves; then 'merchant'."
    )
    policy: PolicyOut | None
    default_policy: PolicyOut | None
    campaign_costs_inr: dict[str, float | None]
    default_campaign_costs_inr: dict[str, float | None]
    preferences: dict[str, str]
    updated_at: datetime | None
    updated_by: str | None
    note: str | None
    changed: bool = Field(default=False, description="True if this save created a new version.")


def policy_out(p: PolicyConfig | None) -> PolicyOut | None:
    if p is None:
        return None
    return PolicyOut(
        policy_version=p.policy_version,
        discount_cap_pct=float(p.discount_cap_pct),
        min_margin_pct=float(p.min_margin_pct),
        max_loss_inr=float(p.max_loss_inr),
        budget_inr=float(p.budget_inr),
        holdout_pct=float(p.holdout_pct),
        risk_scenario=p.risk_scenario,
    )


__all__ = [
    "COST_TYPES",
    "DEFAULT_CAMPAIGN_COSTS",
    "CampaignType",
    "PreferencesIn",
    "SavedSettings",
    "SettingsIn",
    "SettingsOut",
    "policy_out",
]
