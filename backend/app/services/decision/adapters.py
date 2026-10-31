"""Turn an LLM candidate into profit-engine input.

Only numbers cross over: campaign type, discount and the scenario assumptions. The
candidate's rationale and cited facts are explanation text and are dropped, so nothing
the LLM writes can influence profit or policy. Order value, variable cost and campaign
cost come from the caller (merchant settings or an explicit campaign plan); the LLM
schema has no campaign cost, and none is invented here.
"""

from __future__ import annotations

from decimal import Decimal

from app.schemas.decision import CampaignInput, ScenarioAssumption
from app.schemas.llm import CampaignCandidate


def input_from_candidate(
    candidate: CampaignCandidate,
    *,
    campaign_ref: str,
    average_order_value_inr: Decimal | None,
    variable_cost_per_order_inr: Decimal | None,
    campaign_cost_inr: Decimal | None,
    is_synthetic: bool = True,
) -> CampaignInput:
    a = candidate.assumptions
    return CampaignInput(
        campaign_ref=campaign_ref,
        campaign_type=candidate.campaign_type.value,
        average_order_value_inr=average_order_value_inr,
        variable_cost_per_order_inr=variable_cost_per_order_inr,
        discount_inr=Decimal(candidate.discount_inr),
        campaign_cost_inr=campaign_cost_inr,
        scenarios={
            name: ScenarioAssumption(
                incremental_orders=Decimal(s.incremental_orders),
                existing_discounted_orders=Decimal(s.existing_discounted_orders),
            )
            for name, s in (
                ("conservative", a.conservative),
                ("expected", a.expected),
                ("optimistic", a.optimistic),
            )
        },
        assumption_source="llm_suggestion",
        is_synthetic=is_synthetic,
    )
