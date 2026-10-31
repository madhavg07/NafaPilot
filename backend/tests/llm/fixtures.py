"""Shared sample inputs and a fake Gemini transport (no network)."""

from __future__ import annotations

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from app.schemas.llm import (
    CampaignType,
    CandidateContext,
    ExplainRecord,
    ExplanationRequest,
    PolicyLimits,
    ScenarioProfits,
)
from app.services.llm.gemini import RawResponse

FAKE_KEY = "AIzaFAKE-test-key-0123456789abcdef"


def anand_context(**overrides: Any) -> CandidateContext:
    base: dict[str, Any] = dict(
        merchant_id="M001",
        merchant_name="Anand Cafe",
        average_order_value_inr=Decimal("200"),
        variable_cost_per_order_inr=Decimal("110"),
        observed_facts=["Weekday 2-5 pm orders fell from 38 to 30 per day (-21%)."],
        policy=PolicyLimits(discount_cap_pct=Decimal("15"), max_loss_inr=Decimal("1000")),
    )
    base.update(overrides)
    return CandidateContext(**base)


def explain_request(lang: str = "en") -> ExplanationRequest:
    return ExplanationRequest(
        merchant_name="Anand Cafe",
        lang=lang,  # type: ignore[arg-type]
        records=[
            ExplainRecord(
                record_id="A",
                campaign_type=CampaignType.TARGETED_OFFER,
                discount_inr=20,
                scenario_profits_inr=ScenarioProfits(
                    conservative=Decimal("100"),
                    expected=Decimal("2400"),
                    optimistic=Decimal("4700"),
                ),
                policy_passed=True,
            ),
            ExplainRecord(
                record_id="B",
                campaign_type=CampaignType.OFF_PEAK,
                discount_inr=30,
                scenario_profits_inr=ScenarioProfits(
                    conservative=Decimal("-3600"),
                    expected=Decimal("1200"),
                    optimistic=Decimal("5400"),
                ),
                policy_passed=False,
                policy_reasons=["Conservative loss exceeds limit"],
            ),
        ],
    )


VALID_CANDIDATES_JSON = json.dumps(
    {
        "candidates": [
            {
                "campaign_type": "targeted_offer",
                "discount_inr": 20,
                "target_group": "eligible_existing_customers",
                "assumptions": {
                    "conservative": {"incremental_orders": 30, "existing_discounted_orders": 50},
                    "expected": {"incremental_orders": 60, "existing_discounted_orders": 40},
                    "optimistic": {"incremental_orders": 90, "existing_discounted_orders": 30},
                },
                "rationale": "Focus on the declining afternoon window.",
                "cited_facts": [],
            },
            {
                "campaign_type": "no_action",
                "discount_inr": 0,
                "target_group": "none",
                "assumptions": {
                    "conservative": {"incremental_orders": 0, "existing_discounted_orders": 0},
                    "expected": {"incremental_orders": 0, "existing_discounted_orders": 0},
                    "optimistic": {"incremental_orders": 0, "existing_discounted_orders": 0},
                },
                "rationale": "Baseline.",
            },
        ]
    }
)


Step = RawResponse | BaseException | Callable[[], RawResponse]


class FakeTransport:
    """Returns (or raises) each scripted step in order, recording every call."""

    def __init__(self, *steps: Step) -> None:
        self._steps = list(steps)
        self.calls: list[dict[str, Any]] = []

    def generate(self, **kwargs: Any) -> RawResponse:
        self.calls.append(kwargs)
        step = self._steps.pop(0) if len(self._steps) > 1 else self._steps[0]
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, RawResponse):
            return step
        return step()
