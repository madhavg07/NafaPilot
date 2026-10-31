"""Profit engine: deterministic, pure Python, exact Decimal arithmetic.

Formula (the project's documented accounting convention; see docs/decision-engine.md):

    ΔR = I × (AOV − d) − S × d          incremental net revenue
    ΔP = ΔR − I × VC − C                incremental contribution profit
    I* = ceil((S × d + C) / (AOV − d − VC))   break-even incremental orders

I = incremental orders, S = existing orders that receive the discount, d = discount per
redeemed order, AOV = list price per order, VC = variable cost per order, C = campaign
costs other than the discount.

The discount is counted exactly once, inside ΔR: incremental orders are booked at the
discounted price, and the discount on orders that would have happened anyway is lost
revenue. Variable cost applies only to incremental orders, because existing orders
incur it with or without the campaign. `discount_cost_inr` is reported for transparency
and is never subtracted again.

All results are predictions from assumed I and S. Nothing here observes or forecasts
real demand. No I/O, no LLM, no database.
"""

from __future__ import annotations

import hashlib
import json
import math
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

from app.schemas.decision import (
    SCENARIOS,
    CampaignEconomics,
    CampaignInput,
    InputIssue,
    ScenarioName,
    ScenarioResult,
)

ACCOUNTING_NOTE = (
    "ΔR = I×(AOV−d) − S×d; ΔP = ΔR − I×VC − C. Incremental orders are counted at the "
    "discounted price; the discount on orders that would have happened anyway (S) is lost "
    "revenue; variable cost applies only to incremental orders; C is campaign cost other than "
    "the discount. The discount is counted once, inside ΔR. Results are predictions from "
    "assumed I and S, not observed or forecast outcomes."
)
_PAISA = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    """Round a final money value to paise. Intermediate values are never rounded."""
    return value.quantize(_PAISA, rounding=ROUND_HALF_UP)


def _canon(value: Decimal | None) -> str | None:
    return None if value is None else format(value.normalize(), "f")


def input_hash(inp: CampaignInput) -> str:
    """Stable hash of every input that affects the result. 200 and 200.00 hash the same."""
    payload: dict[str, Any] = {
        "campaign_type": inp.campaign_type,
        "aov": _canon(inp.average_order_value_inr),
        "vc": _canon(inp.variable_cost_per_order_inr),
        "d": _canon(inp.discount_inr),
        "c": _canon(inp.campaign_cost_inr),
        "scenarios": {
            name: [
                _canon(a.incremental_orders),
                _canon(a.existing_discounted_orders),
            ]
            for name, a in sorted(inp.scenarios.items())
        },
    }
    if inp.promotion is not None:  # absent for merchant-level campaigns: their hashes are unchanged
        payload["promotion"] = inp.promotion.model_dump(mode="json")
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def validate_input(inp: CampaignInput) -> list[InputIssue]:
    """Every problem, never a guess. Missing values are reported, not defaulted."""
    issues: list[InputIssue] = []
    required = {
        "average_order_value_inr": inp.average_order_value_inr,
        "variable_cost_per_order_inr": inp.variable_cost_per_order_inr,
        "discount_inr": inp.discount_inr,
        "campaign_cost_inr": inp.campaign_cost_inr,
    }
    for name, value in required.items():
        if value is None:
            issues.append(InputIssue(field=name, code="missing", message=f"{name} is required."))
        elif value < 0:
            issues.append(
                InputIssue(field=name, code="negative", message=f"{name} cannot be negative.")
            )
    aov, d = inp.average_order_value_inr, inp.discount_inr
    if aov is not None and aov == 0:
        issues.append(
            InputIssue(
                field="average_order_value_inr",
                code="not_positive",
                message="Average order value must be greater than zero.",
            )
        )
    if aov is not None and d is not None and aov > 0 and d >= 0 and d >= aov:
        issues.append(
            InputIssue(
                field="discount_inr",
                code="not_less_than_aov",
                message="The discount must be smaller than the order value.",
            )
        )
    for name in SCENARIOS:
        a = inp.scenarios.get(name)
        for field in ("incremental_orders", "existing_discounted_orders"):
            value = None if a is None else getattr(a, field)
            path = f"scenarios.{name}.{field}"
            if value is None:
                issues.append(
                    InputIssue(field=path, code="missing", message=f"{path} is required.")
                )
            elif value < 0:
                issues.append(
                    InputIssue(field=path, code="negative", message=f"{path} cannot be negative.")
                )
    counts = [
        inp.scenarios[n].incremental_orders if n in inp.scenarios else None for n in SCENARIOS
    ]
    if all(c is not None and c >= 0 for c in counts):
        c, e, o = counts
        assert c is not None and e is not None and o is not None
        if not c <= e <= o:
            issues.append(
                InputIssue(
                    field="scenarios",
                    code="order",
                    message="Incremental orders must satisfy conservative <= expected <= "
                    "optimistic.",
                )
            )
    promo = inp.promotion
    if promo is not None:
        if promo.list_price_inr is None:
            issues.append(
                InputIssue(
                    field="promotion.list_price_inr",
                    code="missing",
                    message="The offer's product prices are unknown.",
                )
            )
        elif promo.discount_inr >= promo.list_price_inr:
            issues.append(
                InputIssue(
                    field="promotion.discount_inr",
                    code="not_less_than_aov",
                    message="The discount must be smaller than the discounted price.",
                )
            )
        if promo.kind != "cart" and promo.item_cost_inr is None:
            issues.append(
                InputIssue(
                    field="promotion.item_cost_inr",
                    code="missing",
                    message="The variable cost of the offer's products is unknown.",
                )
            )
    return issues


def break_even_orders(s: Decimal, d: Decimal, c: Decimal, unit_margin: Decimal) -> int | None:
    """Smallest whole I with ΔP >= 0, holding S fixed. None if each incremental order
    earns nothing or loses money (AOV − d − VC <= 0), so no I can recover the cost."""
    if unit_margin <= 0:
        return None
    fixed = Fraction(s * d + c)
    if fixed <= 0:
        return 0
    return math.ceil(fixed / Fraction(unit_margin))


def orders_to_exceed(
    target_profit: Decimal, s: Decimal, d: Decimal, c: Decimal, unit_margin: Decimal
) -> Decimal | None:
    """Exact I at which ΔP equals target_profit (S fixed). ΔP exceeds the target only
    when I is strictly greater. None if the unit margin is not positive."""
    if unit_margin <= 0:
        return None
    threshold = (Fraction(target_profit) + Fraction(s * d + c)) / Fraction(unit_margin)
    return Decimal(threshold.numerator) / Decimal(threshold.denominator)


def _rupees(value: Decimal) -> str:
    q = money(value)
    sign = "−" if q < 0 else ""
    text = f"{abs(q):,.2f}".removesuffix(".00")
    return f"{sign}₹{text}"


def _count(value: Decimal) -> str:
    return format(value.normalize(), "f")


def scenario_result(
    name: ScenarioName,
    aov: Decimal,
    vc: Decimal,
    d: Decimal,
    c: Decimal,
    i: Decimal,
    s: Decimal,
) -> ScenarioResult:
    delta_r = i * (aov - d) - s * d
    var_cost = i * vc
    delta_p = delta_r - var_cost - c
    unit = aov - d - vc
    be = break_even_orders(s, d, c, unit)
    explanation = [
        f"ΔR = {_count(i)} × ({_rupees(aov)} − {_rupees(d)}) − {_count(s)} × {_rupees(d)} "
        f"= {_rupees(delta_r)}",
        f"Variable cost of incremental orders = {_count(i)} × {_rupees(vc)} = {_rupees(var_cost)}",
        f"ΔP = {_rupees(delta_r)} − {_rupees(var_cost)} − {_rupees(c)} = {_rupees(delta_p)}",
        (
            f"Break-even: ceil(({_count(s)} × {_rupees(d)} + {_rupees(c)}) / "
            f"{_rupees(unit)}) = {be} incremental orders"
            if be is not None
            else f"No break-even: each incremental order earns {_rupees(unit)} after "
            "discount and variable cost, so more orders cannot recover the cost."
        ),
    ]
    return ScenarioResult(
        scenario=name,
        incremental_orders=i,
        existing_discounted_orders=s,
        incremental_net_revenue_inr=money(delta_r),
        incremental_variable_cost_inr=money(var_cost),
        campaign_cost_inr=money(c),
        incremental_contribution_profit_inr=money(delta_p),
        discount_cost_inr=money((i + s) * d),
        break_even_incremental_orders=be,
        explanation=explanation,
    )


def evaluate_campaign(inp: CampaignInput) -> CampaignEconomics:
    """Three scenarios plus break-even, or a status explaining why not."""
    issues = validate_input(inp)
    base = dict(
        campaign_ref=inp.campaign_ref,
        campaign_type=inp.campaign_type,
        input_hash=input_hash(inp),
        assumption_source=inp.assumption_source,
        is_synthetic=inp.is_synthetic,
        accounting_note=ACCOUNTING_NOTE,
        issues=issues,
    )
    if issues:
        missing_only = all(i.code == "missing" for i in issues)
        return CampaignEconomics(
            status="insufficient_data" if missing_only else "invalid_input", **base
        )

    aov = inp.average_order_value_inr
    vc = inp.variable_cost_per_order_inr
    d = inp.discount_inr
    c = inp.campaign_cost_inr
    assert aov is not None and vc is not None and d is not None and c is not None
    results = []
    for name in SCENARIOS:
        a = inp.scenarios[name]
        assert a.incremental_orders is not None and a.existing_discounted_orders is not None
        results.append(
            scenario_result(name, aov, vc, d, c, a.incremental_orders, a.existing_discounted_orders)
        )
    expected = next(r for r in results if r.scenario == "expected")
    unit = aov - d - vc
    return CampaignEconomics(
        status="ok",
        unit_margin_after_discount_inr=money(unit),
        discount_pct_of_aov=d / aov * 100,
        scenarios=results,
        break_even_incremental_orders=expected.break_even_incremental_orders,
        break_even_status="reachable" if unit > 0 else "not_reachable",
        **base,
    )
