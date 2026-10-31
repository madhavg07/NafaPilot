"""Merchant analysis: validate -> baseline -> trends -> opportunity alerts.

No I/O: callers pass DataFrames in. Never infers why sales changed, and returns
insufficient_data (with reasons) instead of guessing when inputs are missing.
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.schemas.analytics import CoverageInfo, MerchantAnalysis
from app.services.analytics.baseline import (
    MerchantInfo,
    baseline_summary,
    find_gaps,
    order_dates,
)
from app.services.analytics.opportunities import MIN_WEEKS, find_opportunities
from app.services.analytics.validation import validate_orders

METHOD_NOTE = (
    "Screening heuristic, not a calibrated model. Rates are orders per day with recorded "
    "orders: the data has no operating-day calendar, so days with no orders are excluded "
    "and reported, not treated as zero sales or missing data. An alert needs a change of "
    "at least 10% in both the latest week and the last two weeks versus the first 4 of "
    "the last 12 weeks, and an exact binomial test (assuming independent, Poisson-like "
    "order counts) passing Benjamini-Hochberg control at 2.5% across the merchant's time "
    "segments. Real order counts are usually more variable than this assumes, so p-values "
    "are nominal and confidence labels are rules, not probabilities. A change in orders "
    "does not show its cause."
)
LOW_CUSTOMER_ID_COVERAGE = 0.5


def analyze_merchant(
    raw_orders: pd.DataFrame,
    info: MerchantInfo,
    as_of: date,
    *,
    data_source: str,
    is_synthetic: bool,
    known_merchants: set[str] | None = None,
) -> MerchantAnalysis:
    """Analyse one merchant's orders (raw rows for that merchant only)."""
    clean, report = validate_orders(raw_orders, known_merchants)
    clean = clean[clean["date"] <= as_of]
    days = order_dates(clean)
    gaps = find_gaps(days, as_of, start=info.history_start)
    baseline = baseline_summary(clean, info, as_of)
    trends, alerts, weeks = find_opportunities(info.merchant_id, clean, info, as_of, days)
    coverage = CoverageInfo(
        declared_history_start=info.history_start,
        first_order_date=min(days) if days else None,
        last_order_date=max(days) if days else None,
    )

    reasons: list[str] = []
    warnings: list[str] = []
    if clean.empty:
        reasons.append("no_valid_orders: no usable order records.")
    if weeks < MIN_WEEKS:
        reasons.append(
            f"history_too_short: {weeks} full week(s) with recorded orders; at least "
            f"{MIN_WEEKS} are needed to compare periods."
        )
    if baseline.contribution_basis == "unavailable" and not clean.empty:
        reasons.append(
            "variable_cost_missing: no order costs and no merchant cost setting, so "
            "contribution profit cannot be calculated. Please enter a variable cost per order."
        )
    if baseline.contribution_basis == "merchant_setting":
        warnings.append(
            f"Only {baseline.cost_coverage:.0%} of recent orders have a recorded cost; "
            "profit uses the merchant-entered cost per order instead."
        )
    if not clean.empty and baseline.customer_id_coverage < LOW_CUSTOMER_ID_COVERAGE:
        warnings.append(
            f"Customer ids are missing on {1 - baseline.customer_id_coverage:.0%} of recent "
            "orders; repeat-customer metrics are unreliable."
        )
    for gap in gaps:
        warnings.append(
            f"No orders recorded for {gap.days} days ({gap.start:%d %b}-{gap.end:%d %b}). "
            "The data has no operating-day calendar, so this could be a closure, a data "
            "outage or zero sales; these days are excluded from per-day rates."
        )
    for issue in report.issues:
        if issue.severity == "error":
            warnings.append(f"{issue.count} order row(s) rejected: {issue.code}.")

    if weeks < MIN_WEEKS or clean.empty:
        opportunity_status = "insufficient_data"
        alerts = []
    else:
        opportunity_status = "alerts" if alerts else "no_significant_change"

    return MerchantAnalysis(
        merchant_id=info.merchant_id,
        data_source=data_source,
        is_synthetic=is_synthetic,
        as_of=as_of,
        status="insufficient_data" if reasons else "ok",
        insufficient_reasons=reasons,
        opportunity_status=opportunity_status,
        data_quality=report,
        data_warnings=warnings,
        coverage=coverage,
        gaps=gaps,
        baseline=baseline,
        trends=trends,
        alerts=alerts,
        method_note=METHOD_NOTE,
    )
