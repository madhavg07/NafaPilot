"""Product-level promotions: catalogue, merchant product selection, offer specs, baskets.

Three kinds of offer, evaluated by one deterministic engine:
  product  ₹d off one unit of one product (one unit per order)
  bundle   ₹d off one set of 2-3 products bought together (one set per order)
  cart     ₹d off once when the *eligible* cart value reaches a threshold

Hard rule (enforced in code, not by the AI): a unit used by a product or bundle offer is
excluded from the cart offer's eligible value, and one unit never gets two discounts.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.decision import Money

PromotionKind = Literal["product", "bundle", "cart"]
PROMOTION_KINDS: tuple[PromotionKind, ...] = ("product", "bundle", "cart")
CAMPAIGN_TYPE_FOR: dict[PromotionKind, str] = {
    "product": "product_offer",
    "bundle": "bundle_offer",
    "cart": "cart_offer",
}
KIND_FOR_CAMPAIGN_TYPE: dict[str, PromotionKind] = {v: k for k, v in CAMPAIGN_TYPE_FOR.items()}

# Share of the orders that already qualify which would redeem the offer anyway
# (discount leakage), per scenario. Conservative assumes everyone who qualifies uses it.
LEAKAGE_SHARE: dict[str, Decimal] = {
    "conservative": Decimal("1.0"),
    "expected": Decimal("0.8"),
    "optimistic": Decimal("0.6"),
}


class PromotionSpec(BaseModel):
    """Stored inside CampaignInput.promotion. The first block is the offer itself; the
    second is derived by code from the catalogue and order history (never by the AI)."""

    kind: PromotionKind
    skus: list[str] = Field(default_factory=list, max_length=3)
    discount_inr: Money = Field(ge=0)
    min_cart_value_inr: Money | None = None
    hours: list[int] | None = Field(default=None, description="Hours of day the offer runs.")
    weekdays_only: bool = False

    label: str = ""
    eligible_skus: list[str] = Field(default_factory=list)
    list_price_inr: Money | None = Field(
        default=None, description="Product price, bundle list price, or the cart threshold."
    )
    item_cost_inr: Money | None = Field(
        default=None, description="Variable cost of the discounted product or bundle."
    )
    qualifying_orders_28d: int = Field(
        default=0, description="Past orders in the last 28 days that would have qualified."
    )
    basket_basis: str = ""

    @model_validator(mode="after")
    def _shape(self) -> PromotionSpec:
        if self.kind == "product" and len(self.skus) != 1:
            raise ValueError("a product offer names exactly one product")
        if self.kind == "bundle" and not (2 <= len(set(self.skus)) == len(self.skus) <= 3):
            raise ValueError("a bundle names 2 or 3 different products")
        if self.kind == "cart":
            if self.skus:
                raise ValueError("a cart offer does not name products")
            if self.min_cart_value_inr is None or self.min_cart_value_inr <= 0:
                raise ValueError("a cart offer needs a minimum cart value")
        elif self.min_cart_value_inr is not None:
            raise ValueError("only a cart offer has a minimum cart value")
        if self.hours is not None and (not self.hours or any(h < 0 or h > 23 for h in self.hours)):
            raise ValueError("hours must be a non-empty list of hours 0-23")
        return self


# ------------------------------------------------------------------ catalogue API


class ProductStats(BaseModel):
    units_28d: int
    orders_28d: int
    revenue_28d_inr: Money
    contribution_28d_inr: Money | None
    units_prev_28d: int
    trend_pct: float | None = Field(description="Units, last 28 days vs the 28 days before.")
    share_of_orders: float


class ProductOut(BaseModel):
    sku: str
    name: str
    category: str
    price_inr: Money
    unit_cost_inr: Money | None
    margin_inr: Money | None
    margin_pct: float | None
    selected: bool
    stats: ProductStats


class PairOut(BaseModel):
    sku_a: str
    sku_b: str
    name_a: str
    name_b: str
    orders_together_28d: int
    confidence_a_to_b: float = Field(description="Share of orders with A that also have B.")
    lift: float = Field(description="How much more often they appear together than by chance.")


class BasketStatsOut(BaseModel):
    orders_28d: int
    average_value_inr: Money
    median_value_inr: Money
    p75_value_inr: Money
    average_items: float


class CatalogueOut(BaseModel):
    merchant_id: str
    period_start: str
    period_end: str
    products: list[ProductOut]
    pairs: list[PairOut]
    basket: BasketStatsOut
    selection_source: Literal["default", "merchant"]
    selection_version: int
    selected_skus: list[str]
    is_synthetic: bool
    data_source: str
    notice: str


class SelectionIn(BaseModel):
    skus: list[str] = Field(max_length=200)
    note: str | None = Field(default=None, max_length=300)
    updated_by: str = Field(default="merchant", min_length=1, max_length=80)


# ------------------------------------------------------------------ basket check


class BasketLineIn(BaseModel):
    sku: str
    quantity: int = Field(ge=1, le=100)


class BasketCheckIn(BaseModel):
    lines: list[BasketLineIn] = Field(min_length=1, max_length=50)
    campaign_ids: list[str] = Field(default_factory=list, max_length=10)


class UnitAllocation(BaseModel):
    sku: str
    name: str
    unit: int
    price_inr: Money
    used_by: str | None = Field(description="Offer that used this unit, if any.")
    cart_eligible: bool


class AppliedOffer(BaseModel):
    campaign_id: str | None
    label: str
    kind: PromotionKind
    applied: bool
    discount_inr: Money
    reason: str


class BasketCheckOut(BaseModel):
    units: list[UnitAllocation]
    offers: list[AppliedOffer]
    cart_total_inr: Money
    cart_eligible_subtotal_inr: Money
    total_discount_inr: Money
    customer_pays_inr: Money
    variable_cost_inr: Money | None
    contribution_before_inr: Money | None
    contribution_after_inr: Money | None
    rules: list[str]
