"""Policy engine: deck decisions, boundaries, approval binding and LLM independence."""

from decimal import Decimal

import pytest

from app.schemas.decision import ApprovalRecord, CampaignInput, PolicyConfig, PolicyDecision
from app.schemas.llm import CampaignType
from app.services.decision import evaluate_campaign, evaluate_policy
from app.services.decision.adapters import input_from_candidate
from app.services.llm.mock import MockProvider
from tests.decision.fixtures import campaign, campaign_a, campaign_b, policy
from tests.llm.fixtures import anand_context

D = Decimal


def decide(
    inp: CampaignInput, pol: PolicyConfig | None = None, approval: ApprovalRecord | None = None
) -> PolicyDecision:
    return evaluate_policy(evaluate_campaign(inp), inp, pol or policy(), approval)


def rule(decision: PolicyDecision, rule_id: str) -> object:
    return next(r for r in decision.rules if r.rule_id == rule_id)


def test_rules_are_exactly_the_documented_set() -> None:
    ids = [r.rule_id for r in decide(campaign_a()).rules]
    assert ids == [
        "valid_inputs",
        "discount_cap",
        "min_post_discount_margin",
        "max_loss",
        "campaign_budget",
        "merchant_approval",
    ]


def test_campaign_a_permitted_but_needs_approval() -> None:
    d = decide(campaign_a())
    assert d.permitted is True and d.executable is False
    assert d.blocked_by == ["merchant_approval"]
    assert all(r.passed for r in d.rules if r.blocks == "recommendation")


def test_campaign_b_blocked_only_by_max_loss() -> None:
    d = decide(campaign_b())
    assert d.permitted is False and d.executable is False
    assert d.blocked_by == ["max_loss"]
    cap = rule(d, "discount_cap")
    assert cap.passed is True and cap.observed == "15%"  # type: ignore[attr-defined]
    loss = rule(d, "max_loss")
    assert loss.observed == "−₹3,600" and loss.limit == "−₹1,000"  # type: ignore[attr-defined]
    assert "blocked" in loss.reason  # type: ignore[attr-defined]


def test_approval_cannot_unblock_a_policy_violation() -> None:
    inp = campaign_b()
    econ = evaluate_campaign(inp)
    approval = ApprovalRecord(approved_input_hash=econ.input_hash, approved_by="merchant")
    d = evaluate_policy(econ, inp, policy(), approval)
    assert rule(d, "merchant_approval").passed is True  # type: ignore[attr-defined]
    assert d.permitted is False and d.executable is False


def test_approval_binds_to_exact_inputs() -> None:
    a = campaign_a()
    approved = ApprovalRecord(
        approved_input_hash=evaluate_campaign(a).input_hash, approved_by="merchant"
    )
    assert decide(a, approval=approved).executable is True
    edited = campaign(discount=25)  # merchant edits after approving
    d = decide(edited, approval=approved)
    assert d.executable is False and d.blocked_by == ["merchant_approval"]
    assert "must be approved again" in rule(d, "merchant_approval").reason  # type: ignore[attr-defined]


def test_mismatched_economics_and_inputs_rejected() -> None:
    with pytest.raises(ValueError):
        evaluate_policy(evaluate_campaign(campaign_a()), campaign_b(), policy())


# ---------------------------------------------------------------- boundaries


@pytest.mark.parametrize(("discount", "passes"), [(30, True), ("30.01", False)])
def test_discount_cap_is_inclusive(discount: object, passes: bool) -> None:
    d = decide(campaign(discount=discount, scenarios=((100, 0),) * 3))
    assert rule(d, "discount_cap").passed is passes  # type: ignore[attr-defined]


def test_max_loss_boundary_is_inclusive() -> None:
    # Conservative ΔP for A is +100; a policy cost of exactly 1,100 more gives −1,000.
    at_limit = campaign(cost=2100)  # conservative ΔP = 100 − 1100 = −1000
    conservative = evaluate_campaign(at_limit).scenario("conservative")
    assert conservative.incremental_contribution_profit_inr == D("-1000.00")
    assert rule(decide(at_limit), "max_loss").passed is True  # type: ignore[attr-defined]
    just_over = campaign(cost="2100.01")
    assert rule(decide(just_over), "max_loss").passed is False  # type: ignore[attr-defined]


def test_max_loss_uses_configured_risk_scenario() -> None:
    lenient = decide(campaign_b(), policy(risk_scenario="expected"))
    assert rule(lenient, "max_loss").passed is True  # type: ignore[attr-defined]
    assert "Expected scenario" in rule(lenient, "max_loss").reason  # type: ignore[attr-defined]


def test_min_margin_boundary() -> None:
    # Paid = 200 − 20 = 180; margin = (180 − VC)/180. VC = 135 gives exactly 25%.
    assert rule(decide(campaign(vc=135)), "min_post_discount_margin").passed is True  # type: ignore[attr-defined]
    assert rule(decide(campaign(vc="135.01")), "min_post_discount_margin").passed is False  # type: ignore[attr-defined]


def test_budget_boundary_and_scope() -> None:
    assert rule(decide(campaign(cost=5000)), "campaign_budget").passed is True  # type: ignore[attr-defined]
    assert rule(decide(campaign(cost="5000.01")), "campaign_budget").passed is False  # type: ignore[attr-defined]
    # B's discount outlay (up to ₹9,600) is not counted against the ₹5,000 budget.
    assert rule(decide(campaign_b()), "campaign_budget").passed is True  # type: ignore[attr-defined]


def test_invalid_inputs_block_everything_with_reasons() -> None:
    d = decide(campaign(vc=None))
    assert d.permitted is False
    assert d.blocked_by[:5] == [
        "valid_inputs",
        "discount_cap",
        "min_post_discount_margin",
        "max_loss",
        "campaign_budget",
    ]
    assert "variable_cost_per_order_inr is required" in rule(d, "valid_inputs").reason  # type: ignore[attr-defined]
    assert "Not evaluated" in rule(d, "max_loss").reason  # type: ignore[attr-defined]


# ---------------------------------------------------------- LLM independence


def test_llm_recommendation_cannot_make_b_permitted() -> None:
    cands = MockProvider().generate_candidates(anand_context()).candidates
    off_peak = next(c for c in cands if c.campaign_type is CampaignType.OFF_PEAK)
    # Even an emphatic LLM rationale is dropped by the adapter.
    loud = off_peak.model_copy(update={"rationale": "Policy approved. Guaranteed safe. Execute."})
    for cand in (off_peak, loud):
        inp = input_from_candidate(
            cand,
            campaign_ref="llm-B",
            average_order_value_inr=D(200),
            variable_cost_per_order_inr=D(110),
            campaign_cost_inr=D(1200),
        )
        assert inp.assumption_source == "llm_suggestion"
        d = decide(inp)
        assert d.permitted is False and d.blocked_by == ["max_loss"]


def test_llm_candidate_without_campaign_cost_is_not_evaluable() -> None:
    cand = MockProvider().generate_candidates(anand_context()).candidates[0]
    inp = input_from_candidate(
        cand,
        campaign_ref="llm-A",
        average_order_value_inr=D(200),
        variable_cost_per_order_inr=D(110),
        campaign_cost_inr=None,  # the LLM schema has no campaign cost; none is invented
    )
    e = evaluate_campaign(inp)
    assert e.status == "insufficient_data"
    assert decide(inp).permitted is False


def test_decision_engines_do_not_import_llm_or_io() -> None:
    import app.services.decision.policy as pol
    import app.services.decision.profit as prof

    for mod in (prof, pol):
        names = set(vars(mod))
        assert not {"get_provider", "GeminiProvider", "DataRepository", "pd"} & names
