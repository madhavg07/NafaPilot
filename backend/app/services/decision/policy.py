"""Policy engine: deterministic rules over profit-engine results and merchant policy.

Implements exactly the rules in the project requirements (section 7):
  valid_inputs, discount_cap, min_post_discount_margin, max_loss (against the policy's
  risk scenario), campaign_budget, merchant_approval.
No other business constraints are added. The LLM's output reaches this engine only as
numbers inside CampaignInput, so a recommendation, rationale or claimed approval in LLM
text cannot change any result.

Interpretations where the requirements are not specific (see docs/decision-engine.md):
- Post-discount margin = (AOV − d − VC) / (AOV − d): contribution margin on the price
  the customer actually pays.
- Campaign budget applies to C (cash spent on the campaign besides the discount). The
  discount is forgone revenue already inside ΔR, and its downside is governed by the
  loss limit.
- Limits are inclusive: a discount exactly at the cap, a margin exactly at the floor, a
  loss exactly at the limit and a cost exactly at the budget all pass.
"""

from __future__ import annotations

from decimal import Decimal

from app.schemas.decision import (
    ApprovalRecord,
    CampaignEconomics,
    CampaignInput,
    PolicyConfig,
    PolicyDecision,
    RuleResult,
)
from app.services.decision.profit import _rupees, input_hash

_NOT_EVALUATED = "Not evaluated: campaign inputs are incomplete or invalid (see valid_inputs)."


def _pct(value: Decimal) -> str:
    return f"{value.quantize(Decimal('0.01')).normalize():f}%"


def evaluate_policy(
    economics: CampaignEconomics,
    inp: CampaignInput,
    policy: PolicyConfig,
    approval: ApprovalRecord | None = None,
) -> PolicyDecision:
    if economics.input_hash != input_hash(inp):
        raise ValueError("economics were not computed from these inputs")

    rules: list[RuleResult] = []
    ok = economics.status == "ok"
    rules.append(
        RuleResult(
            rule_id="valid_inputs",
            passed=ok,
            blocks="recommendation",
            reason=(
                "All required inputs are present and valid."
                if ok
                else "; ".join(i.message for i in economics.issues)
            ),
        )
    )

    if not ok:
        for rule_id in ("discount_cap", "min_post_discount_margin", "max_loss", "campaign_budget"):
            rules.append(
                RuleResult(
                    rule_id=rule_id,
                    passed=False,
                    blocks="recommendation",
                    reason=_NOT_EVALUATED,
                )
            )
    else:
        aov = inp.average_order_value_inr
        vc = inp.variable_cost_per_order_inr
        d = inp.discount_inr
        c = inp.campaign_cost_inr
        assert aov is not None and vc is not None and d is not None and c is not None

        promo = inp.promotion
        # Merchant-level campaigns compare the discount with the average order. Product and
        # bundle offers compare it with the discounted products' price and cost; a cart
        # offer with the smallest cart that qualifies (cap) and the average qualifying
        # basket (margin).
        if promo is None or promo.list_price_inr is None:
            base, base_text = aov, "the order value"
        elif promo.kind == "cart":
            base, base_text = promo.list_price_inr, "the minimum cart value"
        else:
            base, base_text = promo.list_price_inr, f"the price of {promo.label.split(':')[0]}"
        pct = d / base * 100
        cap_ok = pct <= policy.discount_cap_pct
        rules.append(
            RuleResult(
                rule_id="discount_cap",
                passed=cap_ok,
                blocks="recommendation",
                reason=(
                    f"Discount {_rupees(d)} is {_pct(pct)} of {base_text} ({_rupees(base)}), "
                    f"{'within' if cap_ok else 'above'} the {_pct(policy.discount_cap_pct)} cap."
                    if promo is not None
                    else f"Discount {_rupees(d)} is {_pct(pct)} of the order value, "
                    f"{'within' if cap_ok else 'above'} the {_pct(policy.discount_cap_pct)} cap."
                ),
                observed=_pct(pct),
                limit=_pct(policy.discount_cap_pct),
            )
        )

        if promo is not None and promo.kind != "cart" and promo.list_price_inr is not None:
            assert promo.item_cost_inr is not None  # checked by validate_input
            paid = promo.list_price_inr - d
            item_cost = promo.item_cost_inr
            what = "the offer's products"
        else:
            paid, item_cost, what = aov - d, vc, "the order"
        margin_pct = (paid - item_cost) / paid * 100
        margin_ok = margin_pct >= policy.min_margin_pct
        rules.append(
            RuleResult(
                rule_id="min_post_discount_margin",
                passed=margin_ok,
                blocks="recommendation",
                reason=(
                    f"After the discount the customer pays {_rupees(paid)} for {what}; margin "
                    f"over variable cost is {_pct(margin_pct)}, "
                    f"{'at or above' if margin_ok else 'below'} the "
                    f"{_pct(policy.min_margin_pct)} minimum."
                    if promo is not None
                    else f"After the discount the customer pays {_rupees(paid)}; margin over "
                    f"variable cost is {_pct(margin_pct)}, "
                    f"{'at or above' if margin_ok else 'below'} the "
                    f"{_pct(policy.min_margin_pct)} minimum."
                ),
                observed=_pct(margin_pct),
                limit=_pct(policy.min_margin_pct),
            )
        )

        risk = economics.scenario(policy.risk_scenario).incremental_contribution_profit_inr
        floor = -policy.max_loss_inr
        loss_ok = risk >= floor
        rules.append(
            RuleResult(
                rule_id="max_loss",
                passed=loss_ok,
                blocks="recommendation",
                reason=(
                    f"{policy.risk_scenario.capitalize()} scenario profit is {_rupees(risk)}; "
                    f"the permitted loss is {_rupees(floor)}. "
                    + (
                        "Within the limit."
                        if loss_ok
                        else "The possible loss exceeds the limit, so the campaign is blocked."
                    )
                ),
                observed=_rupees(risk),
                limit=_rupees(floor),
            )
        )

        budget_ok = c <= policy.budget_inr
        rules.append(
            RuleResult(
                rule_id="campaign_budget",
                passed=budget_ok,
                blocks="recommendation",
                reason=(
                    f"Campaign cost {_rupees(c)} is "
                    f"{'within' if budget_ok else 'above'} the {_rupees(policy.budget_inr)} "
                    "budget (discounts are counted in profit and the loss limit, not here)."
                ),
                observed=_rupees(c),
                limit=_rupees(policy.budget_inr),
            )
        )

    if approval is None:
        approval_ok, approval_reason = False, "Merchant approval is required before execution."
    elif approval.approved_input_hash != economics.input_hash:
        approval_ok, approval_reason = (
            False,
            "The recorded approval was for different campaign inputs; the campaign changed "
            "after approval and must be approved again.",
        )
    else:
        approval_ok, approval_reason = True, f"Approved by {approval.approved_by} for these inputs."
    rules.append(
        RuleResult(
            rule_id="merchant_approval",
            passed=approval_ok,
            blocks="execution",
            reason=approval_reason,
        )
    )

    blocked_by = [r.rule_id for r in rules if not r.passed and r.blocks == "recommendation"]
    permitted = not blocked_by
    executable = permitted and approval_ok
    if permitted and not approval_ok:
        blocked_execution = ["merchant_approval"]
    else:
        blocked_execution = []
    return PolicyDecision(
        campaign_ref=economics.campaign_ref,
        input_hash=economics.input_hash,
        policy_version=policy.policy_version,
        risk_scenario=policy.risk_scenario,
        rules=rules,
        permitted=permitted,
        executable=executable,
        blocked_by=blocked_by + blocked_execution,
    )
