"""API models for simulated execution and measurement (M8).

Four kinds of numbers are kept apart and labelled:
- prediction:   the version's stored M5 evaluation (forecast; never modified)
- observed:     simulated outcomes per group (synthetic observations, not real ones)
- incremental:  treatment-vs-holdout estimate with a confidence interval
- measured_profit: incremental profit computed from the simulated observations
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field

NOTICE = (
    "SIMULATED execution on synthetic data. No offer was sent and no real customer, "
    "merchant or payment system was contacted. Simulated results do not show that a real "
    "campaign would work."
)


class SimulationOverrides(BaseModel):
    """Optional simulation ground truth. Defaults are documented in docs/experiments.md."""

    true_lift_per_treated_customer: Decimal | None = Field(
        default=None,
        ge=-5,
        le=5,
        description="Default: the version's expected incremental orders / treated customers.",
    )
    base_rate_per_customer: Decimal | None = Field(
        default=None,
        ge=0,
        le=20,
        description="Default: mean orders per holdout customer in the merchant's past "
        "(synthetic) experiments; required if the merchant has none.",
    )
    base_redeem_share: Decimal | None = Field(
        default=None,
        ge=0,
        le=1,
        description="Share of treated base orders that use the discount. Default: from the "
        "version's expected scenario, capped to [0, 1].",
    )
    missing_outcome_share: Decimal = Field(default=Decimal(0), ge=0, le=Decimal("0.95"))
    max_customers: int | None = Field(
        default=None,
        ge=2,
        le=50_000,
        description="Cap the experiment population (chosen by hash). Default: all eligible.",
    )


class ExecuteRequest(BaseModel):
    requested_by: str = Field(default="merchant", min_length=1, max_length=80)
    simulation: SimulationOverrides = Field(default_factory=SimulationOverrides)


class AssignmentOut(BaseModel):
    customer_id: str
    group: Literal["treatment", "holdout"]
    orders: int | None
    redemptions: int | None


class AssignmentPage(BaseModel):
    experiment_id: uuid.UUID
    total: int
    items: list[AssignmentOut]
    is_simulated: Literal[True] = True


class ExperimentOut(BaseModel):
    experiment_id: uuid.UUID
    campaign_id: uuid.UUID
    campaign_version: int
    input_hash: str
    idempotency_key: str
    replayed: bool = Field(description="True when this response replays an earlier request.")
    status: Literal["completed"]
    mode: Literal["simulated"]
    is_simulated: Literal[True] = True
    data_source: str
    requested_by: str
    requested_at: datetime
    completed_at: datetime
    approval: dict[str, Any]
    assignment: dict[str, Any]
    simulation: dict[str, Any]
    prediction: dict[str, Any]
    observed: dict[str, Any]
    incremental: dict[str, Any]
    measured_profit: dict[str, Any]
    effect_verdict: Literal["positive", "negative", "inconclusive"]
    profit_verdict: Literal["profitable", "loss", "inconclusive"]
    caveats: list[str]
    notice: str = NOTICE
