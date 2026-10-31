"""Uplift estimator, interval, verdicts and measured profit (pure, no DB)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import numpy as np
from scipy import stats

from app.services.experiments.measurement import METHOD, measure

D = Decimal
MONEY: dict[str, Any] = dict(aov=D(200), vc=D(110), discount=D(20), campaign_cost=D(1000))


def run(
    t: list[int], h: list[int], red: int = 0, nt: int | None = None, nh: int | None = None
) -> dict[str, Any]:
    return measure(
        treatment_orders=t,
        holdout_orders=h,
        treatment_redemptions=red,
        treatment_assigned=nt if nt is not None else len(t),
        holdout_assigned=nh if nh is not None else len(h),
        **MONEY,
    )


def rng_counts(rate: float, n: int, seed: int) -> list[int]:
    return np.random.default_rng(seed).poisson(rate, n).tolist()  # type: ignore[no-any-return]


def test_deck_example_reproduces_hand_computed_welch_interval() -> None:
    # 240 treated / 60 holdout; 0/1 outcomes with 60 vs 3 orders (deck slide 7 style).
    t, h = [1] * 60 + [0] * 180, [1] * 3 + [0] * 57
    r = run(t, h, red=100)
    e = r["effect"]
    assert r["method"] == METHOD and r["confidence_level"] == 0.95
    assert r["assignment_proportion_treatment"] == 0.8
    assert e["estimated_incremental_orders"] == 48.0  # (0.25 - 0.05) × 240
    ref = stats.ttest_ind(t, h, equal_var=False).confidence_interval(0.95)
    assert abs(e["ci_low"] - ref.low) < 1e-6 and abs(e["ci_high"] - ref.high) < 1e-6
    assert abs(e["incremental_orders_ci_low"] - ref.low * 240) < 1e-3
    assert abs(e["incremental_orders_ci_high"] - ref.high * 240) < 1e-3
    assert r["effect_verdict"] == "positive"
    # ΔP = 48×90 − 100×20 − 1000 = 1320 (≡ 48×70 − 52×20 − 1000)
    assert r["measured_profit"]["measured_incremental_profit_inr"] == "1320.00"
    assert r["profit_verdict"] == "inconclusive"  # profit interval spans zero


def test_clear_positive_effect() -> None:
    r = run(rng_counts(1.0, 800, 1), rng_counts(0.4, 200, 2), red=500)
    assert r["effect_verdict"] == "positive" and r["effect"]["ci_low"] > 0
    assert r["profit_verdict"] == "profitable"


def test_clear_negative_effect() -> None:
    r = run(rng_counts(0.2, 800, 3), rng_counts(0.8, 200, 4), red=100)
    assert r["effect_verdict"] == "negative" and r["effect"]["ci_high"] < 0
    assert r["profit_verdict"] == "loss"


def test_null_effect_is_inconclusive_not_forced() -> None:
    r = run(
        rng_counts(0.4, 800, 5),
        rng_counts(0.4, 200, 6),
        red=0,
    )
    assert r["effect_verdict"] == "inconclusive"
    assert any(x.startswith("interval_includes_zero") for x in r["effect_reasons"])


def test_null_effect_with_discount_leakage_is_a_measured_loss() -> None:
    """No extra orders but discounts paid on orders that happened anyway."""
    r = run(rng_counts(0.4, 800, 5), rng_counts(0.4, 200, 6), red=300)
    assert r["effect_verdict"] == "inconclusive"
    assert r["profit_verdict"] == "loss"


def test_small_sample_reports_interval_but_no_verdict() -> None:
    r = run([3, 4, 5, 3, 4, 5, 4, 4, 3, 5], [0, 1, 0, 1, 0, 0, 1, 0, 1, 0], red=10)
    assert r["effect"]["ci_low"] is not None and r["effect"]["ci_low"] > 0
    assert r["effect_verdict"] == "inconclusive" and r["profit_verdict"] == "inconclusive"
    assert any(x.startswith("small_sample") for x in r["effect_reasons"])


def test_degenerate_cases() -> None:
    flat = run([0] * 50, [0] * 50)
    assert flat["effect_verdict"] == "inconclusive"
    assert any(x.startswith("no_variation") for x in flat["effect_reasons"])
    assert flat["effect"]["ci_low"] is None
    tiny = run([1], [0, 1, 0])
    assert any(x.startswith("insufficient_measured") for x in tiny["effect_reasons"])
    assert tiny["measured_profit"]["measured_incremental_profit_inr"] is None
    empty = run([], [], nt=40, nh=10)
    assert empty["effect_verdict"] == "inconclusive"
    assert empty["treatment"]["missing_outcomes"] == 40


def test_missing_outcomes_scale_to_assigned_and_are_reported() -> None:
    t, h = [1] * 30 + [0] * 90, [0] * 40  # 120 of 240 treated measured
    r = run(t, h, red=40, nt=240, nh=60)
    assert r["treatment"] == {
        "assigned": 240,
        "measured": 120,
        "missing_outcomes": 120,
        "orders": 30,
        "orders_per_measured_customer": 0.25,
        "redemptions": 40,
    }
    assert r["holdout"]["missing_outcomes"] == 20
    assert r["effect"]["estimated_incremental_orders"] == 60.0  # 0.25 × 240 assigned
    assert r["measured_profit"]["inputs"]["treatment_redemptions_R"] == 80.0  # 40 × 240/120


def test_money_is_two_decimal_strings() -> None:
    r = run(rng_counts(1.0, 100, 7), rng_counts(0.4, 40, 8), red=33)
    p = r["measured_profit"]
    for k in ("measured_incremental_profit_inr", "ci_low_inr", "ci_high_inr"):
        assert isinstance(p[k], str) and len(p[k].split(".")[1]) == 2
    assert (
        float(p["ci_low_inr"])
        <= float(p["measured_incremental_profit_inr"])
        <= float(p["ci_high_inr"])
    )
