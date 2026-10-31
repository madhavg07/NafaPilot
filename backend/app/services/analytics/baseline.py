"""Baseline metrics for one merchant: the last 28 days, no-order periods and cost basis.

Pure functions over validated order rows (see validation.validate_orders).

There is no operating-day or coverage calendar in the data, so the only daily signal is
"at least one order was recorded". Days without orders are counted and reported, never
interpreted as closures, outages or zero sales.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

import pandas as pd

from app.schemas.analytics import (
    BaselineSummary,
    CostBasis,
    DailyPoint,
    DataGap,
    HeatCell,
)

BASELINE_DAYS = 28
ORDER_COST_COVERAGE_MIN = 0.95  # below this, order-level costs are not trusted for profit
MIN_GAP_DAYS = 3


@dataclass(frozen=True)
class MerchantInfo:
    merchant_id: str
    aov_setting_inr: Decimal | None
    vc_setting_inr: Decimal | None  # merchant-entered variable cost per order
    history_start: date | None = None  # declared on the merchant record, if any


def money(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def contribution(
    rows: pd.DataFrame, vc_setting: Decimal | None
) -> tuple[Decimal | None, CostBasis, float]:
    """Contribution profit (revenue − variable cost) for these rows.

    Uses recorded order costs when at least 95% of orders have one (scaling up for the
    few missing). Otherwise falls back to the merchant-entered cost per order. Otherwise
    profit is unavailable: we never guess a cost.
    """
    n = len(rows)
    if n == 0:
        return None, "unavailable", 0.0
    revenue = money(float(rows["order_value_inr"].sum()))
    known = rows["variable_cost_inr"].notna()
    coverage = float(known.mean())
    if coverage >= ORDER_COST_COVERAGE_MIN:
        cost_total = float(rows.loc[known, "variable_cost_inr"].sum()) / coverage
        return revenue - money(cost_total), "order_costs", coverage
    if vc_setting is not None:
        return revenue - money(vc_setting * n), "merchant_setting", coverage
    return None, "unavailable", coverage


def order_dates(rows: pd.DataFrame) -> set[date]:
    """Dates with at least one recorded order. Not a coverage calendar (see module doc)."""
    return set(rows["date"])


def find_gaps(dates: set[date], as_of: date, start: date | None = None) -> list[DataGap]:
    """Runs of at least 3 calendar days with no recorded orders, from `start` (the declared
    history start when known, else the first order date) to as_of. Cause is not known."""
    if not dates:
        return []
    gaps: list[DataGap] = []
    day = min(start, min(dates)) if start else min(dates)
    run_start: date | None = None
    while day <= as_of:
        if day not in dates:
            run_start = run_start or day
        elif run_start is not None:
            length = (day - run_start).days
            if length >= MIN_GAP_DAYS:
                gaps.append(DataGap(start=run_start, end=day - timedelta(days=1), days=length))
            run_start = None
        day += timedelta(days=1)
    if run_start is not None:
        length = (as_of - run_start).days + 1
        if length >= MIN_GAP_DAYS:
            gaps.append(DataGap(start=run_start, end=as_of, days=length))
    return gaps


def baseline_summary(rows: pd.DataFrame, info: MerchantInfo, as_of: date) -> BaselineSummary:
    start = as_of - timedelta(days=BASELINE_DAYS - 1)
    period = rows[(rows["date"] >= start) & (rows["date"] <= as_of)]
    days = period["date"].nunique()  # days with at least one recorded order
    n = len(period)
    revenue = money(float(period["order_value_inr"].sum())) if n else Decimal("0.00")
    profit, basis, coverage = contribution(period, info.vc_setting_inr)

    known_customer = period["customer_id"].notna() if n else pd.Series(dtype=bool)
    repeat_share: float | None = None
    if n and "is_repeat" in period.columns and known_customer.any():
        rep = period.loc[known_customer, "is_repeat"].astype("boolean")
        repeat_share = round(float(rep.mean()), 4) if rep.notna().any() else None

    daily_grp = period.groupby("date").agg(
        orders=("order_id", "size"), revenue=("order_value_inr", "sum")
    )
    daily = [
        DailyPoint(date=d, orders=int(r.orders), revenue_inr=money(float(r.revenue)))
        for d, r in daily_grp.iterrows()
    ]

    heat: list[HeatCell] = []
    if n:
        days_per_weekday = period.groupby("weekday")["date"].nunique()
        counts = period.groupby(["weekday", "hour"]).size()
        for (wd, hr), c in counts.items():
            heat.append(
                HeatCell(
                    weekday=int(wd),
                    hour=int(hr),
                    orders_per_day=round(float(c) / int(days_per_weekday[wd]), 2),
                )
            )

    return BaselineSummary(
        period_start=start,
        period_end=as_of,
        calendar_days=BASELINE_DAYS,
        days_with_orders=int(days),
        days_without_orders=BASELINE_DAYS - int(days),
        orders=n,
        orders_per_day_with_orders=round(n / days, 2) if days else 0.0,
        average_order_value_inr=money(float(period["order_value_inr"].mean())) if n else None,
        revenue_inr=revenue,
        contribution_profit_inr=profit,
        contribution_basis=basis,
        cost_coverage=round(coverage, 4),
        customer_id_coverage=round(float(known_customer.mean()), 4) if n else 0.0,
        repeat_customer_share=repeat_share,
        daily=daily,
        weekday_hour=heat,
    )
