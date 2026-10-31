"""Result models for the analytics and baseline engine.

Money is computed as Decimal and serialised as a JSON number rounded to 2 decimals.

Coverage: the dataset has no record of merchant operating days or data-collection
coverage. The only daily signal is whether at least one order was recorded, so rates are
"per day with recorded orders" and days without orders are reported, not interpreted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer

Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]

Severity = Literal["error", "warning"]
CostBasis = Literal["order_costs", "merchant_setting", "unavailable"]

COVERAGE_LIMITATION = (
    "The dataset records orders only. It has no calendar of operating days or "
    "data-collection coverage, so a day with no recorded orders could be a closure, a data "
    "outage or genuinely zero sales. Such days are excluded from per-day rates and reported "
    "as no-order periods; they are not assumed to be zero sales or missing data."
)


class DataIssue(BaseModel):
    code: str
    severity: Severity
    count: int
    message: str
    examples: list[str] = Field(default_factory=list)


class ValidationReport(BaseModel):
    rows_received: int
    rows_valid: int
    rows_rejected: int
    issues: list[DataIssue]
    field_completeness: dict[str, float]


class CoverageInfo(BaseModel):
    """What the analysis knows about which days are covered by the data."""

    rate_basis: Literal["days_with_recorded_orders"] = "days_with_recorded_orders"
    operating_calendar_available: bool = False
    declared_history_start: date | None = Field(
        default=None, description="Merchant-level history start from the merchant record."
    )
    first_order_date: date | None
    last_order_date: date | None
    limitation: str = COVERAGE_LIMITATION


class DataGap(BaseModel):
    """At least 3 consecutive calendar days with no recorded orders. Cause not known."""

    start: date
    end: date
    days: int


class DailyPoint(BaseModel):
    date: date
    orders: int
    revenue_inr: Money


class HeatCell(BaseModel):
    weekday: int  # 0 = Monday
    hour: int
    orders_per_day: float = Field(description="Per day with recorded orders on that weekday.")


class BaselineSummary(BaseModel):
    """Observed results for the last 28 calendar days (period_start..period_end)."""

    period_start: date
    period_end: date
    calendar_days: int
    days_with_orders: int
    days_without_orders: int = Field(
        description="Calendar days in the period with no recorded orders; cause unknown."
    )
    orders: int
    orders_per_day_with_orders: float
    average_order_value_inr: Money | None
    revenue_inr: Money
    contribution_profit_inr: Money | None
    contribution_basis: CostBasis
    cost_coverage: float
    customer_id_coverage: float
    repeat_customer_share: float | None
    daily: list[DailyPoint] = Field(
        description="Days within the 28-day period that have recorded orders only."
    )
    weekday_hour: list[HeatCell]


class WeeklyPoint(BaseModel):
    week_start: date
    days_with_orders: int = Field(
        description="Segment days in this week on which the merchant recorded any order."
    )
    calendar_days: int
    orders: int
    orders_per_day_with_orders: float | None
    has_days_without_orders: bool


class SegmentTrend(BaseModel):
    segment_id: str
    label: str
    weekly: list[WeeklyPoint]


class ProjectedSegmentBaseline(BaseModel):
    """A projection, not an observed result and not a forecast.

    Orders = the latest week's rate per day with orders × the segment's calendar days in
    4 weeks (assumes every calendar day trades). Order value and cost per order are the
    observed averages for this segment over the last 28 days.
    """

    is_projection: Literal[True] = True
    basis: Literal["latest_week_rate_x_4_weeks"] = "latest_week_rate_x_4_weeks"
    orders: int
    average_order_value_inr: Money
    revenue_inr: Money
    contribution_profit_inr: Money | None
    contribution_basis: CostBasis
    caveat: str = (
        "Projection at the latest observed rate if nothing changes. Not observed, and not "
        "a forecast; the rate may continue to change."
    )


class OpportunityAlert(BaseModel):
    alert_id: str
    segment_id: str
    label: str
    direction: Literal["decline", "increase"]
    reference_period: tuple[date, date]
    reference_orders_per_day: float = Field(description="Per day with recorded orders.")
    latest_week: tuple[date, date]
    latest_orders_per_day: float = Field(description="Per day with recorded orders.")
    recent_two_week_orders_per_day: float = Field(description="Per day with recorded orders.")
    change_pct: float  # latest week vs reference
    recent_change_pct: float  # last two weeks vs reference
    p_value: float = Field(
        description=(
            "Nominal p-value of an exact binomial test that assumes independent "
            "(Poisson) order counts. A screening statistic, not a calibrated error rate."
        )
    )
    confidence: Literal["high", "medium"] = Field(
        description=(
            "Rule-based screening label: 'high' when an 8-week linear trend agrees and no "
            "week used has days without orders. Not a calibrated probability."
        )
    )
    cause: Literal["unknown"] = "unknown"
    message: str
    projected_baseline_4w: ProjectedSegmentBaseline | None
    data_warnings: list[str] = Field(default_factory=list)


class MerchantAnalysis(BaseModel):
    merchant_id: str
    data_source: str
    is_synthetic: bool
    as_of: date
    status: Literal["ok", "insufficient_data"]
    insufficient_reasons: list[str]
    opportunity_status: Literal["alerts", "no_significant_change", "insufficient_data"]
    data_quality: ValidationReport
    data_warnings: list[str]
    coverage: CoverageInfo
    gaps: list[DataGap] = Field(description="Periods of 3+ days with no recorded orders.")
    baseline: BaselineSummary
    trends: list[SegmentTrend]
    alerts: list[OpportunityAlert]
    method_note: str
