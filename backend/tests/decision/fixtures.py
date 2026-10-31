"""Deck (slide 5) campaigns and the M001 policy, as engine inputs."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.schemas.decision import CampaignInput, PolicyConfig, ScenarioAssumption

D = Decimal
NAMES = ("conservative", "expected", "optimistic")

# Scenario inputs (I, S) from the deck, slide 5.
A_SCENARIOS = ((30, 50), (60, 40), (90, 30))
B_SCENARIOS = ((60, 200), (120, 160), (180, 140))


def campaign(
    ref: str = "A",
    *,
    discount: object = 20,
    cost: object = 1000,
    scenarios: tuple[tuple[object, object], ...] = A_SCENARIOS,
    aov: object = 200,
    vc: object = 110,
    **extra: Any,
) -> CampaignInput:
    def dec(v: object) -> Decimal | None:
        return None if v is None else Decimal(str(v))

    return CampaignInput(
        campaign_ref=ref,
        campaign_type=extra.pop("campaign_type", "targeted_offer"),
        average_order_value_inr=dec(aov),
        variable_cost_per_order_inr=dec(vc),
        discount_inr=dec(discount),
        campaign_cost_inr=dec(cost),
        scenarios={
            n: ScenarioAssumption(incremental_orders=dec(i), existing_discounted_orders=dec(s))
            for n, (i, s) in zip(NAMES, scenarios, strict=True)
        },
        assumption_source="illustrative",
        **extra,
    )


def campaign_a() -> CampaignInput:
    return campaign("A")


def campaign_b() -> CampaignInput:
    return campaign("B", discount=30, cost=1200, scenarios=B_SCENARIOS, campaign_type="off_peak")


def policy(**overrides: object) -> PolicyConfig:
    base: dict[str, object] = dict(
        policy_version=1,
        discount_cap_pct=D(15),
        min_margin_pct=D(25),
        max_loss_inr=D(1000),
        budget_inr=D(5000),
        holdout_pct=D(20),
        risk_scenario="conservative",
    )
    base.update(overrides)
    return PolicyConfig.model_validate(base)
