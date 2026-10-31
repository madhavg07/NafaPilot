"""Deterministic promotion engine: who gets which discount in a basket, and what an offer
would have meant for the merchant's real (synthetic) orders.

Allocation rules (fixed, so the same basket always gets the same discounts):
  1. Offers apply in a fixed order: product offers, then bundles, then cart offers; within
     a kind, in the order given.
  2. Each product or bundle offer applies at most once per order (one unit / one set),
     always to the lowest-numbered free units.
  3. One unit never receives two discounts: a unit used by a product or bundle offer is
     not available to any later offer.
  4. A cart offer looks only at the *eligible subtotal*: units not used by any other offer
     and whose product the merchant allows in offers. If that subtotal reaches the
     threshold, the cart discount applies once (never more than the subtotal).
  5. The result reports the whole basket's contribution before and after discounts, so a
     discount that makes the order unprofitable is visible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from app.schemas.decision import AssumptionSource, CampaignInput, ScenarioAssumption
from app.schemas.promotions import (
    CAMPAIGN_TYPE_FOR,
    LEAKAGE_SHARE,
    AppliedOffer,
    BasketCheckOut,
    PromotionSpec,
    UnitAllocation,
)

KIND_ORDER = {"product": 0, "bundle": 1, "cart": 2}
WINDOW_DAYS = 28


def rupees(v: Decimal) -> str:
    q = v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"₹{q.normalize():,f}" if q == q.to_integral() else f"₹{q:,f}"


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price: Decimal
    cost: Decimal | None


@dataclass
class _Unit:
    sku: str
    n: int
    price: Decimal
    cost: Decimal | None
    used_by: str | None = None


@dataclass
class Allocation:
    units: list[_Unit]
    discounts: dict[str, Decimal] = field(default_factory=dict)  # offer key -> discount
    reasons: dict[str, str] = field(default_factory=dict)
    cart_eligible_subtotal: Decimal = Decimal(0)


def label_for(spec: PromotionSpec, products: dict[str, Product]) -> str:
    names = [products[s].name if s in products else s for s in spec.skus]

    def clock(h: int) -> str:
        return f"{(h + 11) % 12 + 1} {'am' if h < 12 or h == 24 else 'pm'}"

    slot = ""
    if spec.hours:
        slot = f", {clock(min(spec.hours))} to {clock(max(spec.hours) + 1)}"
        slot += " on weekdays" if spec.weekdays_only else ""
    elif spec.weekdays_only:
        slot = ", weekdays"
    d = rupees(spec.discount_inr)
    if spec.kind == "product":
        return f"{names[0]}: {d} off{slot}"
    if spec.kind == "bundle":
        return f"{' + '.join(names)}: {d} off the combo{slot}"
    assert spec.min_cart_value_inr is not None
    return f"Cart: {d} off on eligible items worth {rupees(spec.min_cart_value_inr)} or more{slot}"


def allocate(
    lines: list[tuple[str, int]],
    offers: list[tuple[str, PromotionSpec]],
    products: dict[str, Product],
) -> Allocation:
    """Apply offers to one basket. `lines` are (sku, quantity); `offers` are (key, spec)."""
    units: list[_Unit] = []
    for sku, qty in sorted(lines):
        p = products[sku]
        units.extend(_Unit(sku, i + 1, p.price, p.cost) for i in range(qty))
    alloc = Allocation(units=units)

    def free(sku: str) -> _Unit | None:
        return next((u for u in units if u.sku == sku and u.used_by is None), None)

    ordered = sorted(enumerate(offers), key=lambda t: (KIND_ORDER[t[1][1].kind], t[0]))
    for _, (key, spec) in ordered:
        if spec.kind in ("product", "bundle"):
            picks = [free(s) for s in spec.skus]
            if all(u is not None for u in picks):
                for u in picks:
                    assert u is not None
                    u.used_by = key
                alloc.discounts[key] = spec.discount_inr
                alloc.reasons[key] = "Applied to one " + (
                    "unit." if spec.kind == "product" else "set."
                )
            else:
                missing = [s for s, u in zip(spec.skus, picks, strict=True) if u is None]
                taken = [s for s in missing if any(u.sku == s for u in units)]
                alloc.discounts[key] = Decimal(0)
                alloc.reasons[key] = (
                    "Not applied: "
                    + ", ".join(products[s].name if s in products else s for s in missing)
                    + (
                        " already used by another offer."
                        if taken and len(taken) == len(missing)
                        else " not in the basket."
                    )
                )
        else:
            allowed = set(spec.eligible_skus)
            subtotal = sum(
                (u.price for u in units if u.used_by is None and u.sku in allowed), Decimal(0)
            )
            alloc.cart_eligible_subtotal = subtotal
            threshold = spec.min_cart_value_inr
            assert threshold is not None
            if subtotal >= threshold:
                for u in units:
                    if u.used_by is None and u.sku in allowed:
                        u.used_by = key
                alloc.discounts[key] = min(spec.discount_inr, subtotal)
                alloc.reasons[key] = (
                    f"Applied: eligible items come to {rupees(subtotal)}, "
                    f"at least {rupees(threshold)}."
                )
            else:
                alloc.discounts[key] = Decimal(0)
                alloc.reasons[key] = (
                    f"Not applied: eligible items come to {rupees(subtotal)}, below "
                    f"{rupees(threshold)}. Items already discounted by another offer don't count."
                )
    return alloc


def basket_check(
    lines: list[tuple[str, int]],
    offers: list[tuple[str | None, PromotionSpec]],
    products: dict[str, Product],
    cart_allowed: list[str],
) -> BasketCheckOut:
    keyed = [(f"offer-{i}", spec) for i, (_, spec) in enumerate(offers)]
    alloc = allocate(lines, keyed, products)
    total = sum((u.price for u in alloc.units), Decimal(0))
    discount = sum(alloc.discounts.values(), Decimal(0))
    costs = [u.cost for u in alloc.units]
    cost = None if any(c is None for c in costs) else sum((c for c in costs if c), Decimal(0))
    labels = {k: spec.label or label_for(spec, products) for k, spec in keyed}
    kind_of = {k: spec.kind for k, spec in keyed}
    allowed = set(cart_allowed)

    def cart_eligible(u: _Unit) -> bool:
        return u.sku in allowed and (u.used_by is None or kind_of[u.used_by] == "cart")

    return BasketCheckOut(
        units=[
            UnitAllocation(
                sku=u.sku,
                name=products[u.sku].name,
                unit=u.n,
                price_inr=u.price,
                used_by=None if u.used_by is None else labels[u.used_by],
                cart_eligible=cart_eligible(u),
            )
            for u in alloc.units
        ],
        offers=[
            AppliedOffer(
                campaign_id=cid,
                label=labels[k],
                kind=spec.kind,
                applied=alloc.discounts[k] > 0,
                discount_inr=alloc.discounts[k],
                reason=alloc.reasons[k],
            )
            for (k, spec), (cid, _) in zip(keyed, offers, strict=True)
        ],
        cart_total_inr=total,
        cart_eligible_subtotal_inr=sum(
            (u.price for u in alloc.units if cart_eligible(u)), Decimal(0)
        ),
        total_discount_inr=discount,
        customer_pays_inr=total - discount,
        variable_cost_inr=cost,
        contribution_before_inr=None if cost is None else total - cost,
        contribution_after_inr=None if cost is None else total - discount - cost,
        rules=[
            "Product offers apply first, then bundles, then cart offers.",
            "Each product or bundle offer applies once per order.",
            "A unit never gets two discounts.",
            "Items already discounted by a product or bundle offer don't count towards the "
            "cart offer's minimum value.",
            "Only products the merchant allows in offers count towards a cart offer.",
            "Time-limited offers are assumed to be running for this check.",
        ],
    )


# ------------------------------------------------------------- history-based inputs


def _window(orders: pd.DataFrame, as_of: pd.Timestamp, spec: PromotionSpec) -> pd.DataFrame:
    d = pd.to_datetime(orders["order_date"])
    start = as_of - pd.Timedelta(days=WINDOW_DAYS - 1)
    o = orders[(d >= start) & (d <= as_of)]
    if spec.hours:
        o = o[o["hour"].isin(spec.hours)]
    if spec.weekdays_only:
        o = o[pd.to_datetime(o["order_date"]).dt.day_of_week < 5]
    return o


def qualifying_orders(
    orders: pd.DataFrame,
    items: pd.DataFrame,
    spec: PromotionSpec,
    products: dict[str, Product],
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    """Past orders (last 28 days, in the offer's time slot) the offer would have applied to,
    using the same allocation rules as a live basket."""
    o = _window(orders, as_of, spec)
    if o.empty:
        return o
    lines = items[items["order_id"].isin(set(o["order_id"]))]
    if spec.kind in ("product", "bundle"):
        need = set(spec.skus)
        have = lines[lines["sku"].isin(need)].groupby("order_id")["sku"].nunique()
        ids = set(have[have == len(need)].index)
        return o[o["order_id"].isin(ids)]
    allowed = set(spec.eligible_skus)
    assert spec.min_cart_value_inr is not None
    eligible = lines[lines["sku"].isin(allowed)].groupby("order_id")["line_value_inr"].sum()
    ids = set(eligible[eligible >= float(spec.min_cart_value_inr)].index)
    return o[o["order_id"].isin(ids)]


def _dec(v: float) -> Decimal:
    return Decimal(str(round(float(v), 2)))


def build_input(
    spec: PromotionSpec,
    *,
    campaign_ref: str,
    products: dict[str, Product],
    orders: pd.DataFrame,
    items: pd.DataFrame,
    as_of: pd.Timestamp,
    incremental: dict[str, int],
    campaign_cost_inr: Decimal | None,
    merchant_cost_ratio: Decimal | None,
    assumption_source: AssumptionSource = "llm_suggestion",
    is_synthetic: bool = True,
) -> tuple[CampaignInput, list[str]]:
    """Turn an offer into profit-engine input, using the merchant's own order history.

    - Leakage S (orders that would have happened anyway but now get the discount) comes
      from the data: past qualifying orders × the scenario's redemption share.
    - AOV and VC are the averages of those qualifying baskets (the whole basket, not just
      the discounted product), so profit is judged on complete orders.
    - Incremental orders I are the only estimate from outside the data (the AI's
      suggestion, capped at twice the qualifying orders plus 10).
    Returns the input and plain-language notes about how it was built.
    """
    notes: list[str] = []
    prices = [products[s].price for s in spec.skus if s in products]
    costs = [products[s].cost for s in spec.skus if s in products]
    if spec.kind == "cart":
        list_price = spec.min_cart_value_inr
        item_cost = None
    else:
        list_price = sum(prices, Decimal(0)) if len(prices) == len(spec.skus) else None
        item_cost = (
            sum((c for c in costs if c is not None), Decimal(0))
            if len(costs) == len(spec.skus) and all(c is not None for c in costs)
            else None
        )
    q = qualifying_orders(orders, items, spec, products, as_of)
    n_q = len(q)
    if n_q:
        aov: Decimal | None = _dec(q["order_value_inr"].mean())
        vc_series = q["variable_cost_inr"]
        vc = None if vc_series.isna().any() else _dec(vc_series.mean())
        basis = (
            f"Average of the {n_q} past orders in the last 28 days that this offer would have "
            "applied to (whole baskets, not just the discounted items)."
        )
    else:
        aov = list_price
        if spec.kind == "cart":
            vc = (
                None
                if list_price is None or merchant_cost_ratio is None
                else (list_price * merchant_cost_ratio).quantize(Decimal("0.01"))
            )
        else:
            vc = item_cost
        basis = (
            "No past order in the last 28 days would have qualified, so the offer's own "
            "price and cost are used as the basket."
        )
        notes.append(basis)

    cap = 2 * n_q + 10
    capped = {k: min(int(v), cap) for k, v in incremental.items()}
    if capped != {k: int(v) for k, v in incremental.items()}:
        notes.append(
            f"The AI's estimate of extra orders was capped at {cap} "
            "(twice the qualifying orders plus 10)."
        )

    filled = spec.model_copy(
        update={
            "label": spec.label or label_for(spec, products),
            "eligible_skus": spec.eligible_skus if spec.kind == "cart" else list(spec.skus),
            "list_price_inr": list_price,
            "item_cost_inr": item_cost,
            "qualifying_orders_28d": n_q,
            "basket_basis": basis,
        }
    )
    inp = CampaignInput(
        campaign_ref=campaign_ref,
        campaign_type=CAMPAIGN_TYPE_FOR[spec.kind],
        average_order_value_inr=aov,
        variable_cost_per_order_inr=vc,
        discount_inr=spec.discount_inr,
        campaign_cost_inr=campaign_cost_inr,
        scenarios={
            name: ScenarioAssumption(
                incremental_orders=Decimal(capped.get(name, 0)),
                existing_discounted_orders=(Decimal(n_q) * LEAKAGE_SHARE[name]).quantize(
                    Decimal("0.1")
                ),
            )
            for name in ("conservative", "expected", "optimistic")
        },
        assumption_source=assumption_source,
        is_synthetic=is_synthetic,
        promotion=filled,
    )
    return inp, notes
