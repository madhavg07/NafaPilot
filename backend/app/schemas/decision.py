"""Inputs and results for the profit engine and the policy engine.

Inputs are deliberately permissive (optional, unconstrained) so the engines can report
every problem as a structured issue or failed policy rule instead of raising. Money is
Decimal throughout and serialised as a JSON number.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer

Money = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]
Count = Annotated[Decimal, PlainSerializer(lambda v: float(v), return_type=float, when_used="json")]

ScenarioName = Literal["conservative", "expected", "optimistic"]
SCENARIOS: tuple[ScenarioName, ...] = ("conservative", "expected", "optimistic")
AssumptionSource = Literal[
    "llm_suggestion", "merchant_edit", "template", "illustrative", "measured"
]


# ------------------------------------------------------------------------ inputs


class ScenarioAssumption(BaseModel):
    """Assumed counts for one scenario. Counts may be fractional expectations."""

    incremental_orders: Decimal | None = Field(
        default=None, description="I: orders that would not have happened without the offer."
    )
    existing_discounted_orders: Decimal | None = Field(
        default=None,
        description="S: orders that would have happened anyway but now receive the discount.",
    )


class CampaignInput(BaseModel):
    """Everything the profit engine needs. Nothing is filled in if missing."""

    campaign_ref: str = Field(description="Caller's label, e.g. a candidate or version id.")
    campaign_type: str
    average_order_value_inr: Decimal | None = Field(default=None, description="AOV, list price.")
    variable_cost_per_order_inr: Decimal | None = Field(default=None, description="VC.")
    discount_inr: Decimal | None = Field(default=None, description="d, per redeemed order.")
    campaign_cost_inr: Decimal | None = Field(
        default=None, description="C: costs other than the discount (messages, printing)."
    )
    scenarios: dict[ScenarioName, ScenarioAssumption]
    assumption_source: AssumptionSource
    is_synthetic: bool = True
    promotion: PromotionSpec | None = Field(
        default=None,
        description="Product-level offer (product, bundle or cart). None = merchant-level "
        "campaign. When set, AOV and VC are the averages of the orders the offer applies to.",
    )


class PolicyConfig(BaseModel):
    """A merchant's policy version, as stored in merchant_policies."""

    policy_version: int
    discount_cap_pct: Decimal
    min_margin_pct: Decimal
    max_loss_inr: Decimal
    budget_inr: Decimal
    holdout_pct: Decimal  # used by the experiment engine (M8), not a policy rule
    risk_scenario: ScenarioName


class ApprovalRecord(BaseModel):
    """A merchant approval, bound to the exact inputs it was given for."""

    approved_input_hash: str
    approved_by: str


# ----------------------------------------------------------------------- results


class InputIssue(BaseModel):
    field: str
    code: Literal["missing", "negative", "not_less_than_aov", "not_positive", "order"]
    message: str


class ScenarioResult(BaseModel):
    scenario: ScenarioName
    incremental_orders: Count
    existing_discounted_orders: Count
    incremental_net_revenue_inr: Money = Field(description="ΔR = I×(AOV−d) − S×d")
    incremental_variable_cost_inr: Money = Field(description="I × VC")
    campaign_cost_inr: Money = Field(description="C")
    incremental_contribution_profit_inr: Money = Field(description="ΔP = ΔR − I×VC − C")
    discount_cost_inr: Money = Field(
        description="(I+S)×d, for transparency only. Already inside ΔR; never subtracted again."
    )
    break_even_incremental_orders: int | None = Field(
        description="ceil((S×d + C) / (AOV−d−VC)) at this scenario's S; None if unreachable."
    )
    explanation: list[str]


class CampaignEconomics(BaseModel):
    campaign_ref: str
    campaign_type: str
    status: Literal["ok", "insufficient_data", "invalid_input"]
    issues: list[InputIssue]
    input_hash: str = Field(description="SHA-256 of the canonical inputs; approvals bind to it.")
    result_kind: Literal["prediction"] = "prediction"
    assumption_source: AssumptionSource
    is_synthetic: bool
    unit_margin_after_discount_inr: Money | None = Field(
        default=None, description="AOV − d − VC: profit per incremental order before C."
    )
    discount_pct_of_aov: Decimal | None = None
    scenarios: list[ScenarioResult] = Field(default_factory=list)
    break_even_incremental_orders: int | None = Field(
        default=None, description="At the expected scenario's S."
    )
    break_even_status: Literal["reachable", "not_reachable", "unavailable"] = "unavailable"
    accounting_note: str

    def scenario(self, name: ScenarioName) -> ScenarioResult:
        return next(s for s in self.scenarios if s.scenario == name)


class RuleResult(BaseModel):
    rule_id: Literal[
        "valid_inputs",
        "discount_cap",
        "min_post_discount_margin",
        "max_loss",
        "campaign_budget",
        "merchant_approval",
    ]
    passed: bool
    blocks: Literal["recommendation", "execution"] = Field(
        description="'recommendation' rules block showing the campaign as permitted; "
        "'execution' rules block running it."
    )
    reason: str
    observed: str | None = None
    limit: str | None = None


class PolicyDecision(BaseModel):
    campaign_ref: str
    input_hash: str
    policy_version: int
    risk_scenario: ScenarioName
    rules: list[RuleResult]
    permitted: bool = Field(description="All recommendation-blocking rules passed.")
    executable: bool = Field(description="Permitted and approved for exactly these inputs.")
    blocked_by: list[str]
    decided_by: Literal["deterministic_policy_engine"] = "deterministic_policy_engine"


from app.schemas.promotions import PromotionSpec  # noqa: E402  (avoids an import cycle)

CampaignInput.model_rebuild()
