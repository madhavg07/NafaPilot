"""Uplift estimate, confidence interval and measured profit. Pure function.

Estimator (one pre-specified primary metric: orders per assigned customer):
  per-customer effect  = mean(treatment orders) − mean(holdout orders)
  95% interval         = Welch's t interval (unequal variances), scipy.stats.ttest_ind
  incremental orders Î = effect × treated customers (and its interval likewise)

Measured profit uses the project's accounting convention with measured quantities:
  R  = redemptions in treatment (all discounted treated orders)
  ΔP = Î × (AOV − d − VC) − (R − Î) × d − C  =  Î × (AOV − VC) − R × d − C
so incremental orders are booked net of discount, the discount on orders that would have
happened anyway (R − Î) is lost revenue, and C is the campaign cost. The profit interval
maps Î's interval through this linear formula; it treats R as fixed, so it slightly
understates uncertainty.

Verdicts: "positive"/"negative" only if the whole interval is above/below zero;
otherwise, or with too little data, "inconclusive". Nothing is forced either way.
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

import numpy as np
from scipy import stats

METHOD = "welch_difference_in_means"
CONFIDENCE = 0.95
MIN_MEASURED_PER_ARM = 30
_CENT = Decimal("0.01")


def _money(x: float | Decimal) -> str:
    return str(Decimal(str(x)).quantize(_CENT, rounding=ROUND_HALF_UP))


def _arm(orders: list[int], redemptions: int, assigned: int) -> dict[str, Any]:
    n = len(orders)
    return {
        "assigned": assigned,
        "measured": n,
        "missing_outcomes": assigned - n,
        "orders": int(sum(orders)),
        "orders_per_measured_customer": round(float(np.mean(orders)), 6) if n else None,
        "redemptions": redemptions,
    }


def measure(
    *,
    treatment_orders: list[int],
    holdout_orders: list[int],
    treatment_redemptions: int,
    treatment_assigned: int,
    holdout_assigned: int,
    aov: Decimal,
    vc: Decimal,
    discount: Decimal,
    campaign_cost: Decimal,
) -> dict[str, Any]:
    n_t, n_h = len(treatment_orders), len(holdout_orders)
    reasons: list[str] = []
    effect: dict[str, Any] = {
        "per_customer_difference": None,
        "ci_low": None,
        "ci_high": None,
        "standard_error": None,
        "degrees_of_freedom": None,
        "p_value": None,
        "estimated_incremental_orders": None,
        "incremental_orders_ci_low": None,
        "incremental_orders_ci_high": None,
    }
    verdict = "inconclusive"

    if n_t < 2 or n_h < 2:
        reasons.append("insufficient_measured_outcomes: fewer than 2 measured customers in an arm")
    else:
        t = np.asarray(treatment_orders, dtype=float)
        h = np.asarray(holdout_orders, dtype=float)
        diff = float(t.mean() - h.mean())
        se = math.sqrt(t.var(ddof=1) / n_t + h.var(ddof=1) / n_h)
        effect["per_customer_difference"] = round(diff, 6)
        effect["estimated_incremental_orders"] = round(diff * treatment_assigned, 4)
        if se == 0:
            reasons.append("no_variation: every outcome in both arms is identical")
        else:
            res = stats.ttest_ind(t, h, equal_var=False)
            ci = res.confidence_interval(CONFIDENCE)
            lo, hi = float(ci.low), float(ci.high)
            effect.update(
                ci_low=round(lo, 6),
                ci_high=round(hi, 6),
                standard_error=round(se, 6),
                degrees_of_freedom=round(float(res.df), 2),
                p_value=float(res.pvalue),
                incremental_orders_ci_low=round(lo * treatment_assigned, 4),
                incremental_orders_ci_high=round(hi * treatment_assigned, 4),
            )
            if min(n_t, n_h) < MIN_MEASURED_PER_ARM:
                reasons.append(
                    f"small_sample: fewer than {MIN_MEASURED_PER_ARM} measured customers in an "
                    "arm; the interval is unreliable, so no conclusion is drawn"
                )
            elif lo > 0:
                verdict = "positive"
            elif hi < 0:
                verdict = "negative"
            else:
                reasons.append("interval_includes_zero: the data cannot rule out no effect")

    # Measured profit (scaled to all treated customers if some outcomes are missing).
    scale = treatment_assigned / n_t if n_t else 0.0
    redemptions_scaled = treatment_redemptions * scale
    profit: dict[str, Any] = {
        "formula": "ΔP = Î×(AOV−VC) − R×d − C  (≡ Î×(AOV−d−VC) − (R−Î)×d − C)",
        "inputs": {
            "average_order_value_inr": _money(aov),
            "variable_cost_per_order_inr": _money(vc),
            "discount_inr": _money(discount),
            "campaign_cost_inr": _money(campaign_cost),
            "treatment_redemptions_R": round(redemptions_scaled, 4),
        },
        "measured_incremental_profit_inr": None,
        "ci_low_inr": None,
        "ci_high_inr": None,
    }
    profit_verdict = "inconclusive"
    unit = float(aov - vc)
    fixed = redemptions_scaled * float(discount) + float(campaign_cost)
    i_hat = effect["estimated_incremental_orders"]
    if i_hat is not None:
        profit["measured_incremental_profit_inr"] = _money(i_hat * unit - fixed)
        if effect["incremental_orders_ci_low"] is not None:
            ends = sorted(
                (
                    effect["incremental_orders_ci_low"] * unit - fixed,
                    effect["incremental_orders_ci_high"] * unit - fixed,
                )
            )
            profit["ci_low_inr"], profit["ci_high_inr"] = _money(ends[0]), _money(ends[1])
            # Decisive only with an interval and enough measured customers in both arms.
            # A "loss" can be certain even when the order effect is not (e.g. no effect
            # but discounts on orders that would have happened anyway).
            if min(n_t, n_h) >= MIN_MEASURED_PER_ARM:
                if ends[0] > 0:
                    profit_verdict = "profitable"
                elif ends[1] < 0:
                    profit_verdict = "loss"

    return {
        "method": METHOD,
        "confidence_level": CONFIDENCE,
        "primary_metric": "orders per assigned customer over the campaign window",
        "assignment_proportion_treatment": round(
            treatment_assigned / (treatment_assigned + holdout_assigned), 6
        ),
        "treatment": _arm(treatment_orders, treatment_redemptions, treatment_assigned),
        "holdout": _arm(holdout_orders, 0, holdout_assigned),
        "effect": effect,
        "effect_verdict": verdict,
        "effect_reasons": reasons,
        "measured_profit": profit,
        "profit_verdict": profit_verdict,
    }
