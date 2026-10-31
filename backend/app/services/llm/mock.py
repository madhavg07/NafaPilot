"""Deterministic offline provider for tests, offline demos and development without a key.

Output depends only on the input, so tests are repeatable. It mirrors the deck's
illustrative candidates (a targeted offer, an off-peak offer and no action), scaled to
the merchant's order value and discount cap.
"""

from __future__ import annotations

import math
from decimal import ROUND_FLOOR, Decimal

from app.schemas.llm import (
    CampaignCandidate,
    CampaignType,
    CandidateContext,
    CandidateSet,
    Explanation,
    ExplanationRequest,
    ExtraOrders,
    PromotionCandidate,
    PromotionCandidateSet,
    PromotionContext,
    ScenarioAssumption,
    ScenarioAssumptions,
    TargetGroup,
)
from app.services.llm.errors import LLMError
from app.services.llm.templates import template_explanation


def _assume(c: tuple[int, int], e: tuple[int, int], o: tuple[int, int]) -> ScenarioAssumptions:
    return ScenarioAssumptions(
        conservative=ScenarioAssumption(incremental_orders=c[0], existing_discounted_orders=c[1]),
        expected=ScenarioAssumption(incremental_orders=e[0], existing_discounted_orders=e[1]),
        optimistic=ScenarioAssumption(incremental_orders=o[0], existing_discounted_orders=o[1]),
    )


_NONE = _assume((0, 0), (0, 0), (0, 0))


def _whole_rupees(value: Decimal) -> int:
    return int(value.quantize(Decimal("1"), rounding=ROUND_FLOOR))


class MockProvider:
    name = "mock"
    model = "mock-deterministic-v1"

    def __init__(self, fail_with: LLMError | None = None) -> None:
        self._fail_with = fail_with

    def _maybe_fail(self) -> None:
        if self._fail_with is not None:
            raise self._fail_with

    def generate_candidates(self, ctx: CandidateContext) -> CandidateSet:
        self._maybe_fail()
        aov = ctx.average_order_value_inr
        cap_discount = _whole_rupees(aov * ctx.policy.discount_cap_pct / 100)
        targeted_discount = min(_whole_rupees(aov * Decimal("0.10")), cap_discount)
        allowed = set(ctx.allowed_campaign_types)
        wants_new = ctx.preferences.get("target") == "new_customers"

        out: list[CampaignCandidate] = []
        if wants_new and CampaignType.NEW_CUSTOMER in allowed:
            out.append(
                CampaignCandidate(
                    campaign_type=CampaignType.NEW_CUSTOMER,
                    discount_inr=targeted_discount,
                    target_group=TargetGroup.NEW_CUSTOMERS,
                    assumptions=_assume((20, 0), (40, 0), (60, 0)),
                    rationale="First-order offer for new customers only, per merchant preference.",
                    cited_facts=["Merchant preference: target new customers"],
                )
            )
        elif CampaignType.TARGETED_OFFER in allowed:
            out.append(
                CampaignCandidate(
                    campaign_type=CampaignType.TARGETED_OFFER,
                    discount_inr=targeted_discount,
                    target_group=TargetGroup.ELIGIBLE_EXISTING,
                    assumptions=_assume((30, 50), (60, 40), (90, 30)),
                    rationale="A modest offer to a selected group limits discounts on orders "
                    "that would have happened anyway.",
                    cited_facts=ctx.observed_facts[:2],
                )
            )
        if CampaignType.OFF_PEAK in allowed and not wants_new:
            out.append(
                CampaignCandidate(
                    campaign_type=CampaignType.OFF_PEAK,
                    discount_inr=cap_discount,
                    target_group=TargetGroup.ALL_WALK_INS,
                    assumptions=_assume((60, 200), (120, 160), (180, 140)),
                    rationale="A larger off-peak discount for all walk-ins may lift volume "
                    "but discounts many existing orders.",
                    cited_facts=ctx.observed_facts[:2],
                )
            )
        out.append(
            CampaignCandidate(
                campaign_type=CampaignType.NO_ACTION,
                discount_inr=0,
                target_group=TargetGroup.NONE,
                assumptions=_NONE,
                rationale="Baseline for comparison: change nothing.",
            )
        )
        return CandidateSet(candidates=out)

    def generate_promotions(self, ctx: PromotionContext) -> PromotionCandidateSet:
        """One product offer (aimed at a declining slot if there is one), the strongest
        bundle among the selected products, and a cart offer above the usual basket."""
        self._maybe_fail()
        cap = ctx.policy.discount_cap_pct / 100
        pref_max = ctx.preferences.get("max_discount_inr")
        limit = int(pref_max) if pref_max and pref_max.isdigit() else None

        def disc(base: Decimal, share: Decimal) -> int:
            d = min(base * share, base * cap)
            d = max(Decimal(5), (d / 5).quantize(Decimal(1), rounding=ROUND_FLOOR) * 5)
            return int(d)

        def extra(base: float) -> ExtraOrders:
            return ExtraOrders(
                conservative=math.ceil(0.04 * base) + 1,
                expected=math.ceil(0.09 * base) + 2,
                optimistic=math.ceil(0.15 * base) + 3,
            )

        out: list[PromotionCandidate] = []
        by_sku = {p.sku: p for p in ctx.products}
        slot = next((s for s in ctx.demand_changes if s.change_pct < 0), None)
        if "product" in ctx.allowed_kinds:
            costed = [p for p in ctx.products if p.margin_pct is not None and p.margin_pct >= 35]
            pool = costed or list(ctx.products)
            pick = min(pool, key=lambda p: (p.trend_pct or 0, -p.orders_28d, p.sku))
            if (pick.trend_pct or 0) >= 0:
                pick = max(pool, key=lambda p: ((p.margin_pct or 0) * p.orders_28d, p.sku))
            base = pick.orders_28d * (0.35 if slot else 1.0)
            out.append(
                PromotionCandidate(
                    kind="product",
                    skus=[pick.sku],
                    discount_inr=disc(pick.price_inr, Decimal("0.11")),
                    hours=None if slot is None else slot.hours,
                    weekdays_only=False if slot is None else slot.weekdays_only,
                    extra_orders=extra(base),
                    rationale=f"{pick.name} has a good margin"
                    + (f"; orders fell in {slot.label}." if slot else ".")
                    + " A small discount on one product avoids discounting everything.",
                    cited_facts=[],
                )
            )
        if "bundle" in ctx.allowed_kinds:
            pair = next((p for p in ctx.pairs if p.sku_a in by_sku and p.sku_b in by_sku), None)
            if pair is not None:
                listed = by_sku[pair.sku_a].price_inr + by_sku[pair.sku_b].price_inr
                out.append(
                    PromotionCandidate(
                        kind="bundle",
                        skus=[pair.sku_a, pair.sku_b],
                        discount_inr=disc(listed, Decimal("0.08")),
                        extra_orders=extra(pair.orders_together_28d * 0.8),
                        rationale=f"{by_sku[pair.sku_a].name} and {by_sku[pair.sku_b].name} are "
                        "often bought together, so a combo price can lift the second item.",
                        cited_facts=[],
                    )
                )
        if "cart" in ctx.allowed_kinds and ctx.basket_orders_28d:
            raw = max(ctx.basket_p75_inr, ctx.basket_average_inr * Decimal("1.3"))
            threshold = int(math.ceil(float(raw) / 50) * 50)
            out.append(
                PromotionCandidate(
                    kind="cart",
                    min_cart_value_inr=threshold,
                    discount_inr=disc(Decimal(threshold), Decimal("0.08")),
                    extra_orders=extra(ctx.basket_orders_28d * 0.12),
                    rationale="A cart offer above the usual basket can encourage one more item.",
                    cited_facts=[],
                )
            )
        if limit is not None:
            out = [c for c in out if c.discount_inr <= limit] or out
        if not out:
            first = ctx.products[0]
            out.append(
                PromotionCandidate(
                    kind="product",
                    skus=[first.sku],
                    discount_inr=disc(first.price_inr, Decimal("0.1")),
                    extra_orders=extra(first.orders_28d),
                    rationale="Only option available with the current selection.",
                )
            )
        return PromotionCandidateSet(candidates=out[:6])

    def explain(self, request: ExplanationRequest) -> Explanation:
        self._maybe_fail()
        return template_explanation(request)

    def ping(self) -> None:
        self._maybe_fail()
