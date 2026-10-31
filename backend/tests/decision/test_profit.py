"""Profit engine: deck regressions, crossover, precision and edge cases."""

from decimal import Decimal

import pytest

from app.services.decision.profit import (
    break_even_orders,
    evaluate_campaign,
    input_hash,
    orders_to_exceed,
)
from tests.decision.fixtures import B_SCENARIOS, campaign, campaign_a, campaign_b

D = Decimal


def profits(econ: object) -> list[Decimal]:
    return [s.incremental_contribution_profit_inr for s in econ.scenarios]  # type: ignore[attr-defined]


# ------------------------------------------------------------ deck regressions


def test_campaign_a_matches_deck() -> None:
    e = evaluate_campaign(campaign_a())
    assert e.status == "ok"
    assert profits(e) == [D("100.00"), D("2400.00"), D("4700.00")]
    assert e.break_even_incremental_orders == 26
    exp = e.scenario("expected")
    assert exp.incremental_net_revenue_inr == D("10000.00")
    assert exp.incremental_variable_cost_inr == D("6600.00")
    assert exp.campaign_cost_inr == D("1000.00")


def test_campaign_b_matches_deck() -> None:
    e = evaluate_campaign(campaign_b())
    assert profits(e) == [D("-3600.00"), D("1200.00"), D("5400.00")]
    assert e.break_even_incremental_orders == 100
    exp = e.scenario("expected")
    assert exp.incremental_net_revenue_inr == D("15600.00")
    assert exp.incremental_variable_cost_inr == D("13200.00")
    assert e.discount_pct_of_aov == D(15)


def test_campaign_a_conservative_formula_trace() -> None:
    """The documented formula, not a cannibalisation variant: 30×180 − 50×20 − 30×110 − 1000."""
    s = evaluate_campaign(campaign_a()).scenario("conservative")
    assert s.incremental_net_revenue_inr == D("4400.00")  # 30×180 − 50×20
    assert s.incremental_contribution_profit_inr == D("100.00")
    assert s.explanation[0] == "ΔR = 30 × (₹200 − ₹20) − 50 × ₹20 = ₹4,400"
    assert s.explanation[2] == "ΔP = ₹4,400 − ₹3,300 − ₹1,000 = ₹100"


def test_discount_cost_is_reported_but_counted_once() -> None:
    s = evaluate_campaign(campaign_a()).scenario("expected")
    assert s.discount_cost_inr == D("2000.00")  # (60 + 40) × 20
    # ΔP equals ΔR − I×VC − C exactly; the discount cost is not subtracted a second time.
    assert s.incremental_contribution_profit_inr == (
        s.incremental_net_revenue_inr - s.incremental_variable_cost_inr - s.campaign_cost_inr
    )


def test_b_beats_a_only_above_documented_crossover() -> None:
    a_expected = evaluate_campaign(campaign_a()).scenario("expected")
    threshold = orders_to_exceed(
        a_expected.incremental_contribution_profit_inr, D(160), D(30), D(1200), D(60)
    )
    assert threshold == D(140)  # deck slide 5: "> 140 orders"

    def b_expected(i: int) -> Decimal:
        sc = (B_SCENARIOS[0], (i, 160), (max(i, 180), 140))
        e = evaluate_campaign(campaign("B", discount=30, cost=1200, scenarios=sc))
        return e.scenario("expected").incremental_contribution_profit_inr

    a = a_expected.incremental_contribution_profit_inr
    assert b_expected(120) < a
    assert b_expected(140) == a  # equal, not better
    assert b_expected(141) > a


# ----------------------------------------------------------------- break-even


def test_break_even_is_exact_ceiling() -> None:
    assert break_even_orders(D(40), D(20), D(1000), D(70)) == 26  # 1800/70 = 25.71
    assert break_even_orders(D(160), D(30), D(1200), D(60)) == 100  # exactly 100, not 101
    assert break_even_orders(D(0), D(0), D(0), D(70)) == 0


def test_break_even_profit_flips_at_the_returned_order_count() -> None:
    for sc, d, c in ((((26, 40), (26, 40), (26, 40)), 20, 1000), (((100, 160),) * 3, 30, 1200)):
        at = evaluate_campaign(campaign(discount=d, cost=c, scenarios=sc)).scenario("expected")
        below_sc = tuple((i - 1, s) for i, s in sc)
        below = evaluate_campaign(campaign(discount=d, cost=c, scenarios=below_sc))
        assert at.incremental_contribution_profit_inr >= 0
        assert below.scenario("expected").incremental_contribution_profit_inr < 0


@pytest.mark.parametrize("vc", [180, 190])  # AOV − d − VC = 0 and = −10
def test_zero_or_negative_unit_margin_has_no_break_even(vc: int) -> None:
    e = evaluate_campaign(campaign(vc=vc))
    assert e.status == "ok"
    assert e.break_even_incremental_orders is None
    assert e.break_even_status == "not_reachable"
    assert all(s.break_even_incremental_orders is None for s in e.scenarios)
    assert "cannot recover the cost" in e.scenario("expected").explanation[3]
    assert orders_to_exceed(D(0), D(40), D(20), D(1000), D(200 - 20 - vc)) is None


# ---------------------------------------------------------- precision & rounding


def test_exact_decimal_arithmetic() -> None:
    # 0.1 + 0.2 style values that drift in binary floating point.
    e = evaluate_campaign(
        campaign(
            aov="199.99",
            vc="110.10",
            discount="19.99",
            cost="0.30",
            scenarios=(("0.1", "0.2"), ("0.1", "0.2"), ("0.1", "0.2")),
        )
    )
    s = e.scenario("expected")
    # ΔR = 0.1×180 − 0.2×19.99 = 18 − 3.998 = 14.002 -> 14.00
    assert s.incremental_net_revenue_inr == D("14.00")
    # ΔP = 14.002 − 11.01 − 0.30 = 2.692 -> 2.69 (rounded once, at the end)
    assert s.incremental_contribution_profit_inr == D("2.69")


def test_rounding_is_half_up_on_final_value_only() -> None:
    # ΔP = 1×(100−0−99.995) − 0 − 0 = 0.005 -> 0.01 with ROUND_HALF_UP.
    e = evaluate_campaign(
        campaign(aov=100, vc="99.995", discount=0, cost=0, scenarios=((1, 0),) * 3)
    )
    assert e.scenario("expected").incremental_contribution_profit_inr == D("0.01")


def test_input_hash_is_stable_and_sensitive() -> None:
    assert input_hash(campaign_a()) == input_hash(campaign(aov="200.00", vc="110.0"))
    assert input_hash(campaign_a()) != input_hash(campaign(discount=21))
    assert input_hash(campaign_a()) != input_hash(
        campaign(scenarios=((30, 50), (61, 40), (90, 30)))
    )


# ------------------------------------------------------------ missing / invalid


@pytest.mark.parametrize("field", ["vc", "aov", "discount", "cost"])
def test_missing_inputs_are_reported_never_guessed(field: str) -> None:
    e = evaluate_campaign(campaign(**{field: None}))
    assert e.status == "insufficient_data"
    assert e.scenarios == [] and e.break_even_incremental_orders is None
    assert [i.code for i in e.issues] == ["missing"]


def test_missing_scenario_assumption() -> None:
    e = evaluate_campaign(campaign(scenarios=((30, 50), (None, 40), (90, 30))))
    assert e.status == "insufficient_data"
    assert e.issues[0].field == "scenarios.expected.incremental_orders"


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"discount": -5}, "negative"),
        ({"cost": -1}, "negative"),
        ({"vc": -110}, "negative"),
        ({"aov": 0}, "not_positive"),
        ({"discount": 200}, "not_less_than_aov"),
        ({"scenarios": ((30, -1), (60, 40), (90, 30))}, "negative"),
        ({"scenarios": ((70, 50), (60, 40), (90, 30))}, "order"),
    ],
)
def test_invalid_inputs(kwargs: dict[str, object], code: str) -> None:
    e = evaluate_campaign(campaign(**kwargs))  # type: ignore[arg-type]
    assert e.status == "invalid_input"
    assert code in {i.code for i in e.issues}
    assert e.scenarios == []


def test_results_are_labelled_predictions() -> None:
    e = evaluate_campaign(campaign_a())
    assert e.result_kind == "prediction" and e.is_synthetic is True
    assert e.assumption_source == "illustrative"
    assert "counted once" in e.accounting_note
