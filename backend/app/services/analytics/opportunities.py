"""Weekly segment trends and opportunity screening.

This is a screening heuristic that flags changes worth a merchant's attention. It is
not a calibrated statistical model, and it never identifies causes.

Method (summarised in MerchantAnalysis.method_note):
- Segments: all days and hours, plus weekday and weekend time slots.
- Each full calendar week (Mon-Sun) gets orders per day *with recorded orders*: segment
  orders divided by the segment's days on which the merchant recorded any order. Days
  with no orders at all are excluded, because the data has no operating-day calendar to
  say whether they were closures, outages or zero sales. Such weeks are flagged
  `has_days_without_orders`.
- Reference = the first 4 weeks of the last 12; recent = the last 2 weeks.
- Each segment gets an exact binomial test: recent vs reference counts, conditioned on
  their total, with exposure = days with orders. Benjamini-Hochberg at a 2.5% false
  discovery rate is applied across the merchant's segments.
- An alert also needs a change of at least 10% in the same direction for both the latest
  week and the last two weeks (an effect-size gate chosen by judgement, not calibrated).
- Confidence is a rule-based label: "high" when an 8-week linear trend agrees (p < 0.05)
  and no week used has days without orders, else "medium". Not a probability.

Assumptions and limits (the p-values are nominal):
- The binomial test treats orders as independent events with a constant Poisson rate
  within each period. Real orders are usually over-dispersed (busy and quiet days,
  group orders), which makes p-values too small; an alert can therefore be more
  likely than the nominal rate suggests.
- Exposure counts days, not their mix: if reference and recent periods differ in
  weekday/weekend composition (possible in the all-days segment when days are missing),
  the comparison is biased.
- Segments overlap (all-days contains every slot), so tests are positively dependent.
  Benjamini-Hochberg is generally robust to this, but the 2.5% rate is not guaranteed.
- The synthetic generator produces Poisson counts, so performance on this dataset
  (no chance alerts across tested seeds) overstates reliability on real data.
- Cause is always "unknown": order counts alone cannot explain a change.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy import stats

from app.schemas.analytics import (
    OpportunityAlert,
    ProjectedSegmentBaseline,
    SegmentTrend,
    WeeklyPoint,
)
from app.services.analytics.baseline import MerchantInfo, contribution, money

MIN_WEEKS = 6
REFERENCE_WEEKS = 4
RECENT_WEEKS = 2
LOOKBACK_WEEKS = 12
MIN_CHANGE = 0.10
FDR_Q = 0.025  # Benjamini-Hochberg false discovery rate per merchant
MIN_REFERENCE_ORDERS = 40
TREND_WEEKS = 8
SUPPRESS_WITHIN = 0.05  # slot alerts this close to an overall alert add nothing new

WEEKDAYS = (0, 1, 2, 3, 4)
WEEKEND = (5, 6)


@dataclass(frozen=True)
class Segment:
    segment_id: str
    label: str
    days: tuple[int, ...]
    hours: tuple[int, int]  # [start, end)


_SLOTS = (
    ("morning", "6-11 am", (6, 11)),
    ("midday", "11 am-2 pm", (11, 14)),
    ("afternoon", "2-5 pm", (14, 17)),
    ("evening", "5-9 pm", (17, 21)),
    ("late", "9 pm-12 am", (21, 24)),
)

SEGMENTS: tuple[Segment, ...] = (
    Segment("all_days_all_hours", "All days, all hours", tuple(range(7)), (0, 24)),
    *(Segment(f"weekday_{k}", f"Weekdays {t}", WEEKDAYS, h) for k, t, h in _SLOTS),
    *(Segment(f"weekend_{k}", f"Weekends {t}", WEEKEND, h) for k, t, h in _SLOTS),
)


def full_weeks(first: date, as_of: date) -> list[date]:
    """Mondays of every full Mon-Sun week from the week containing `first` to as_of."""
    start = first - timedelta(days=first.weekday())
    weeks = []
    while start + timedelta(days=6) <= as_of:
        weeks.append(start)
        start += timedelta(days=7)
    return weeks


def segment_rows(rows: pd.DataFrame, seg: Segment) -> pd.DataFrame:
    lo, hi = seg.hours
    mask = rows["weekday"].isin(seg.days) & (rows["hour"] >= lo) & (rows["hour"] < hi)
    return rows.loc[mask]


def weekly_series(
    rows: pd.DataFrame, seg: Segment, weeks: list[date], order_days: set[date]
) -> list[WeeklyPoint]:
    """Per-week segment orders, divided by segment days on which any order was recorded.

    A segment day with merchant orders but none in this segment counts as zero for the
    segment; a day with no merchant orders at all is excluded (see module doc).
    """
    seg_rows = segment_rows(rows, seg)
    counts = seg_rows.groupby("date").size()
    points = []
    for monday in weeks:
        days = [monday + timedelta(days=d) for d in seg.days]
        with_orders = [d for d in days if d in order_days]
        orders = int(sum(int(counts.get(d, 0)) for d in with_orders))
        points.append(
            WeeklyPoint(
                week_start=monday,
                days_with_orders=len(with_orders),
                calendar_days=len(days),
                orders=orders,
                orders_per_day_with_orders=(
                    round(orders / len(with_orders), 2) if with_orders else None
                ),
                has_days_without_orders=len(with_orders) < len(days),
            )
        )
    return points


def _projected_baseline(
    rows: pd.DataFrame, seg: Segment, info: MerchantInfo, latest_per_day: float, as_of: date
) -> ProjectedSegmentBaseline | None:
    """Projection at the latest week's rate over 4 weeks of calendar days. Not observed."""
    recent = segment_rows(rows, seg)
    recent = recent[recent["date"] > as_of - timedelta(days=28)]
    if recent.empty:
        return None
    orders = round(latest_per_day * len(seg.days) * 4)
    aov = money(float(recent["order_value_inr"].mean()))
    per_order_profit, basis, _ = contribution(recent, info.vc_setting_inr)
    profit = None
    if per_order_profit is not None:
        profit = money(per_order_profit / len(recent) * orders)
    return ProjectedSegmentBaseline(
        orders=orders,
        average_order_value_inr=aov,
        revenue_inr=money(aov * orders),
        contribution_profit_inr=profit,
        contribution_basis=basis,
    )


def significant(p_values: list[float], q: float = FDR_Q) -> float:
    """Benjamini-Hochberg: the largest p-value still significant (0 if none)."""
    m = len(p_values)
    cutoff = 0.0
    for rank, p in enumerate(sorted(p_values), start=1):
        if p <= rank / m * q:
            cutoff = p
    return cutoff


def detect(
    merchant_id: str,
    seg: Segment,
    series: list[WeeklyPoint],
    rows: pd.DataFrame,
    info: MerchantInfo,
    as_of: date,
) -> tuple[float | None, OpportunityAlert | None]:
    """(p-value if the segment could be tested, alert if the change is large enough).

    Significance is decided afterwards, across all of the merchant's segments.
    """
    weeks = [w for w in series[-LOOKBACK_WEEKS:] if w.days_with_orders > 0]
    if len(weeks) < MIN_WEEKS:
        return None, None
    ref, recent, latest = weeks[:REFERENCE_WEEKS], weeks[-RECENT_WEEKS:], weeks[-1]
    ref_orders = sum(w.orders for w in ref)
    ref_days = sum(w.days_with_orders for w in ref)
    rec_orders = sum(w.orders for w in recent)
    rec_days = sum(w.days_with_orders for w in recent)
    if ref_orders < MIN_REFERENCE_ORDERS or ref_days == 0 or rec_days == 0:
        return None, None

    ref_rate = ref_orders / ref_days
    rec_rate = rec_orders / rec_days
    latest_rate = latest.orders / latest.days_with_orders
    change = latest_rate / ref_rate - 1
    rec_change = rec_rate / ref_rate - 1
    p = float(
        stats.binomtest(
            rec_orders, rec_orders + ref_orders, rec_days / (rec_days + ref_days)
        ).pvalue
    )
    same_sign = np.sign(change) == np.sign(rec_change)
    if not (same_sign and abs(change) >= MIN_CHANGE and abs(rec_change) >= MIN_CHANGE):
        return p, None

    trend = weeks[-TREND_WEEKS:]
    fit = stats.linregress(range(len(trend)), [w.orders_per_day_with_orders or 0.0 for w in trend])
    used = [*ref, *recent]
    no_order_days = any(w.has_days_without_orders for w in used)
    trend_agrees = fit.pvalue < 0.05 and np.sign(fit.slope) == np.sign(change)
    confidence = "high" if trend_agrees and not no_order_days else "medium"

    direction = "decline" if change < 0 else "increase"
    verb = "fell" if change < 0 else "rose"
    ref_start, ref_end = ref[0].week_start, ref[-1].week_start + timedelta(days=6)
    latest_end = latest.week_start + timedelta(days=6)
    message = (
        f"{seg.label}: orders {verb} from {ref_rate:.1f} to {latest_rate:.1f} per day "
        f"({change * 100:+.0f}%), comparing {ref_start:%d %b}-{ref_end:%d %b} with the week "
        f"of {latest.week_start:%d %b}. Cause unknown: order data alone does not show why."
    )
    warnings = []
    if no_order_days:
        warnings.append(
            "Some weeks used include days with no recorded orders (cause unknown); those "
            "days are excluded from per-day rates."
        )
    return p, OpportunityAlert(
        alert_id=f"{merchant_id}-{seg.segment_id}-{latest.week_start:%Y%m%d}",
        segment_id=seg.segment_id,
        label=seg.label,
        direction=direction,
        reference_period=(ref_start, ref_end),
        reference_orders_per_day=round(ref_rate, 2),
        latest_week=(latest.week_start, latest_end),
        latest_orders_per_day=round(latest_rate, 2),
        recent_two_week_orders_per_day=round(rec_rate, 2),
        change_pct=round(change * 100, 2),
        recent_change_pct=round(rec_change * 100, 2),
        p_value=p,
        confidence=confidence,
        message=message,
        projected_baseline_4w=_projected_baseline(rows, seg, info, latest_rate, as_of),
        data_warnings=warnings,
    )


def find_opportunities(
    merchant_id: str,
    rows: pd.DataFrame,
    info: MerchantInfo,
    as_of: date,
    order_days: set[date],
) -> tuple[list[SegmentTrend], list[OpportunityAlert], int]:
    """Returns (trends, alerts, number of full weeks containing recorded orders)."""
    if rows.empty:
        return [], [], 0
    weeks = full_weeks(min(order_days), as_of)
    trends: list[SegmentTrend] = []
    candidates: list[OpportunityAlert] = []
    tested: list[float] = []
    for seg in SEGMENTS:
        series = weekly_series(rows, seg, weeks, order_days)
        if not any(w.orders for w in series):
            continue
        trends.append(SegmentTrend(segment_id=seg.segment_id, label=seg.label, weekly=series))
        p, alert = detect(merchant_id, seg, series, rows, info, as_of)
        if p is not None:
            tested.append(p)
        if alert:
            candidates.append(alert)
    cutoff = significant(tested)
    alerts = [a for a in candidates if a.p_value <= cutoff]

    overall = next((a for a in alerts if a.segment_id == "all_days_all_hours"), None)
    if overall:
        alerts = [
            a
            for a in alerts
            if a is overall
            or a.direction != overall.direction
            or abs(a.change_pct - overall.change_pct) >= SUPPRESS_WITHIN * 100
        ]
    alerts.sort(key=lambda a: (a.p_value, -abs(a.change_pct)))
    weeks_with_orders = sum(
        1 for m in weeks if any(m + timedelta(days=d) in order_days for d in range(7))
    )
    return trends, alerts, weeks_with_orders
