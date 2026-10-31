"""Analytics engine against the planted scenarios in the synthetic dataset."""

from datetime import date, timedelta

import pytest

from app.schemas.analytics import MerchantAnalysis
from app.services.analytics.baseline import find_gaps
from app.services.analytics.opportunities import significant

TRENDING = {"M001", "M003", "M004"}


def test_anand_afternoon_decline_is_the_only_alert(analyses: dict[str, MerchantAnalysis]) -> None:
    a = analyses["M001"]
    assert a.status == "ok" and a.opportunity_status == "alerts"
    assert len(a.alerts) == 1
    alert = a.alerts[0]
    assert alert.segment_id == "weekday_afternoon"
    assert alert.direction == "decline"
    assert alert.reference_orders_per_day == 38.0
    assert alert.latest_orders_per_day == 30.0
    assert alert.change_pct == pytest.approx(-21.05, abs=0.01)  # deck: 38 -> 30, -21%
    assert alert.confidence == "high"
    assert alert.cause == "unknown"
    assert alert.reference_period == (date(2026, 8, 3), date(2026, 8, 30))
    assert alert.latest_week == (date(2026, 10, 19), date(2026, 10, 25))


def test_anand_segment_baseline_matches_deck_order_of_magnitude(
    analyses: dict[str, MerchantAnalysis],
) -> None:
    b = analyses["M001"].alerts[0].projected_baseline_4w
    assert b is not None
    assert b.is_projection is True and b.basis == "latest_week_rate_x_4_weeks"
    assert "not a forecast" in b.caveat.lower()
    assert b.orders == 600  # 30 per day x 20 weekdays (deck slide 5)
    assert b.contribution_basis == "order_costs"
    assert b.contribution_profit_inr is not None
    assert float(b.contribution_profit_inr) == pytest.approx(54_000, rel=0.03)


def test_alert_text_never_claims_a_cause(analyses: dict[str, MerchantAnalysis]) -> None:
    banned = ("because", "due to", "caused by", "weather", "competitor", "competition")
    for a in analyses.values():
        for alert in a.alerts:
            text = alert.message.lower()
            assert "cause unknown" in text
            assert not any(word in text for word in banned), text


def test_trends_detected(analyses: dict[str, MerchantAnalysis]) -> None:
    growing = analyses["M003"].alerts[0]
    declining = analyses["M004"].alerts[0]
    assert growing.segment_id == "all_days_all_hours" and growing.direction == "increase"
    assert declining.segment_id == "all_days_all_hours" and declining.direction == "decline"


def test_no_chance_alerts_for_stable_merchants(analyses: dict[str, MerchantAnalysis]) -> None:
    noisy = {mid: a.alerts for mid, a in analyses.items() if mid not in TRENDING and a.alerts}
    assert noisy == {}


def test_missing_cost_is_insufficient_not_guessed(analyses: dict[str, MerchantAnalysis]) -> None:
    a = analyses["M007"]
    assert a.status == "insufficient_data"
    assert a.baseline.contribution_profit_inr is None
    assert a.baseline.contribution_basis == "unavailable"
    assert any(r.startswith("variable_cost_missing") for r in a.insufficient_reasons)
    assert a.baseline.orders > 0  # order analytics still work without costs


def test_partial_costs_fall_back_to_merchant_setting(analyses: dict[str, MerchantAnalysis]) -> None:
    a = analyses["M011"]
    assert a.baseline.contribution_basis == "merchant_setting"
    assert a.baseline.cost_coverage == pytest.approx(0.7, abs=0.05)
    assert any("merchant-entered cost" in w for w in a.data_warnings)
    assert any("Customer ids are missing" in w for w in a.data_warnings)


def test_short_history_is_insufficient(analyses: dict[str, MerchantAnalysis]) -> None:
    a = analyses["M015"]
    assert a.opportunity_status == "insufficient_data"
    assert a.alerts == []
    assert any(r.startswith("history_too_short") for r in a.insufficient_reasons)


def test_no_order_period_is_reported_not_interpreted(
    analyses: dict[str, MerchantAnalysis],
) -> None:
    a = analyses["M019"]
    assert len(a.gaps) == 1 and a.gaps[0].days == 10
    warning = next(w for w in a.data_warnings if w.startswith("No orders recorded for 10 days"))
    assert "closure, a data outage or zero sales" in warning
    assert "excluded from per-day rates" in warning
    overall = next(t for t in a.trends if t.segment_id == "all_days_all_hours")
    flagged = [w for w in overall.weekly if w.has_days_without_orders]
    assert flagged and all(w.days_with_orders < w.calendar_days for w in flagged)
    # The gap must not show up as a fake decline.
    assert a.opportunity_status == "no_significant_change"


def test_baseline_period_is_last_28_days(analyses: dict[str, MerchantAnalysis]) -> None:
    b = analyses["M001"].baseline
    assert b.period_end == date(2026, 10, 25)
    assert b.period_end - b.period_start == timedelta(days=27)
    assert b.calendar_days == 28 and b.days_with_orders == 28 and b.days_without_orders == 0
    assert float(b.average_order_value_inr or 0) == pytest.approx(
        200, abs=10
    )  # basket mix shifts a little week to week


def test_results_are_labelled_synthetic(analyses: dict[str, MerchantAnalysis]) -> None:
    assert all(a.is_synthetic for a in analyses.values())


def test_find_gaps_unit() -> None:
    d0 = date(2026, 1, 1)
    days = {d0 + timedelta(days=i) for i in range(20) if not 5 <= i < 9}
    gaps = find_gaps(days, d0 + timedelta(days=19))
    assert [(g.start, g.days) for g in gaps] == [(d0 + timedelta(days=5), 4)]
    assert find_gaps({d0, d0 + timedelta(days=2)}, d0 + timedelta(days=2)) == []  # 1-day hole


def test_benjamini_hochberg_unit() -> None:
    assert significant([0.001, 0.2, 0.5, 0.9]) == 0.001  # 0.001 <= 1/4 * 0.025
    assert significant([0.01, 0.2, 0.5, 0.9]) == 0.0  # 0.01 > 0.00625
    assert significant([0.001, 0.004, 0.9, 0.9], q=0.025) == 0.004  # 0.004 <= 2/4 * 0.025


def test_baseline_daily_covers_only_the_28_day_period(
    analyses: dict[str, MerchantAnalysis],
) -> None:
    """Regression: `daily` used to include the full 12-week history."""
    for mid in ("M001", "M019", "M015"):
        b = analyses[mid].baseline
        dates = [p.date for p in b.daily]
        assert dates, mid
        assert min(dates) >= b.period_start and max(dates) <= b.period_end, mid
        assert len(dates) == b.days_with_orders, mid
        assert sum(p.orders for p in b.daily) == b.orders, mid


def test_coverage_limitation_is_exposed(analyses: dict[str, MerchantAnalysis]) -> None:
    for a in analyses.values():
        c = a.coverage
        assert c.operating_calendar_available is False
        assert c.rate_basis == "days_with_recorded_orders"
        assert "no calendar of operating days" in c.limitation
    m15 = analyses["M015"].coverage
    assert m15.declared_history_start == date(2026, 10, 5)
    assert m15.first_order_date is not None and m15.first_order_date >= date(2026, 10, 5)


def test_method_is_described_as_screening(analyses: dict[str, MerchantAnalysis]) -> None:
    note = analyses["M001"].method_note.lower()
    assert "screening heuristic" in note
    assert "not probabilities" in note and "nominal" in note
    assert "does not show its cause" in note


def test_gaps_start_from_declared_history_start() -> None:
    d0 = date(2026, 1, 1)
    orders = {d0 + timedelta(days=i) for i in range(5, 20)}
    gaps = find_gaps(orders, d0 + timedelta(days=19), start=d0)
    assert [(g.start, g.days) for g in gaps] == [(d0, 5)]  # no orders in the first 5 days
