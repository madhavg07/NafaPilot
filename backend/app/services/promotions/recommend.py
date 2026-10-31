"""Product-level recommendation runs: the AI proposes product, bundle and cart offers on
the merchant's selected products; code builds the inputs from real baskets, calculates
profit, applies policy, and picks one by a fixed rule.

    AI proposes → code checks products → code evaluates baskets → policy → merchant decides

Runs, explanations and campaigns are stored exactly like merchant-level runs, so
approval, versioning, audit and simulated experiments work unchanged.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from app.data import DataRepository
from app.db.models import AuditEvent, RecommendationRun
from app.schemas.campaigns import CampaignCreate
from app.schemas.decision import CampaignEconomics, CampaignInput, PolicyDecision
from app.schemas.llm import (
    ExplainRecord,
    ExplanationRequest,
    PairFact,
    PolicyLimits,
    ProductFact,
    PromotionCandidate,
    PromotionContext,
    ScenarioProfits,
    SlotFact,
)
from app.schemas.promotions import CAMPAIGN_TYPE_FOR, PromotionSpec
from app.schemas.recommendations import (
    CandidateOut,
    LearningOut,
    PromotionRunRequest,
    SelectionOut,
)
from app.services.analytics import analyze_merchant
from app.services.analytics.opportunities import SEGMENTS
from app.services.campaign_store import CampaignError, PolicyMissing, create_campaign_in_tx
from app.services.decision.policy import evaluate_policy
from app.services.decision.profit import evaluate_campaign
from app.services.llm.base import LLMProvider
from app.services.llm.prompts import PROMPT_VERSION
from app.services.promotions.catalogue import (
    catalogue_out,
    merchant_cost_ratio,
    products_map,
)
from app.services.promotions.engine import build_input, label_for
from app.services.recommendations import (
    PERFORMANCE_LEARNING,
    _store_explanation,
    current_preferences,
    decision_history,
    explain_grounded,
    observed_facts,
)

SELECTION_RULE = (
    "Among offers that pass every policy rule, pick the highest expected profit; ties go "
    "to the higher conservative profit, then the smaller discount. If none has a positive "
    "expected profit, recommend no offer."
)
PREFERENCE_LEARNING = (
    "Only the products the merchant selected were offered to the AI, and offers on any "
    "other product were rejected by code. A saved maximum discount is enforced as a filter."
)


class NoProductsSelected(CampaignError):
    http_status, error_code = 422, "no_products_selected"


@dataclass
class _Offer:
    record_id: str
    candidate: PromotionCandidate
    filter_reasons: list[str]
    spec: PromotionSpec | None = None
    inp: CampaignInput | None = None
    economics: CampaignEconomics | None = None
    policy: PolicyDecision | None = None
    campaign_id: uuid.UUID | None = None
    notes: list[str] = field(default_factory=list)


def _select(offers: list[_Offer]) -> _Offer | None:
    ok = [
        o
        for o in offers
        if o.economics is not None
        and o.policy is not None
        and o.economics.status == "ok"
        and o.policy.permitted
    ]
    if not ok:
        return None

    def key(o: _Offer) -> tuple[Decimal, Decimal, int, str]:
        assert o.economics is not None
        return (
            o.economics.scenario("expected").incremental_contribution_profit_inr,
            o.economics.scenario("conservative").incremental_contribution_profit_inr,
            -o.candidate.discount_inr,
            o.record_id,
        )

    best = max(ok, key=key)
    assert best.economics is not None
    if best.economics.scenario("expected").incremental_contribution_profit_inr <= 0:
        return None
    return best


def _slots(analysis: Any) -> list[SlotFact]:
    by_id = {s.segment_id: s for s in SEGMENTS}
    out = []
    for a in analysis.alerts:
        seg = by_id.get(a.segment_id)
        if seg is None or seg.segment_id == "all_days_all_hours":
            continue
        out.append(
            SlotFact(
                label=a.label,
                hours=list(range(seg.hours[0], seg.hours[1])),
                weekdays_only=set(seg.days) == {0, 1, 2, 3, 4},
                change_pct=round(float(a.change_pct), 1),
            )
        )
    return out[:10]


def generate_promotion_run(
    session: Session,
    repo: DataRepository,
    provider: LLMProvider,
    merchant_id: str,
    req: PromotionRunRequest,
) -> uuid.UUID:
    profile = repo.merchant_profile(merchant_id)  # raises UnknownMerchant
    policy = repo.merchant_policy(merchant_id)
    if policy is None:
        raise PolicyMissing(f"No policy is configured for merchant {merchant_id}.")
    cat = catalogue_out(repo, merchant_id)  # raises NoProductData
    selected = set(cat.selected_skus)
    if not selected:
        raise NoProductsSelected(
            "No products are selected for offers. Select at least one product first."
        )
    info = repo.merchant_info(merchant_id)
    analysis = analyze_merchant(
        repo.orders_for(merchant_id),
        info,
        repo.as_of,
        data_source=repo.data_source,
        is_synthetic=repo.is_synthetic,
        known_merchants=repo.merchant_ids,
    )
    with session.begin():
        prefs = {k: p.value for k, p in current_preferences(session, merchant_id).items()}
        history = decision_history(session, merchant_id)

    ctx = PromotionContext(
        merchant_id=merchant_id,
        merchant_name=str(profile["name"]),
        products=[
            ProductFact(
                sku=p.sku,
                name=p.name,
                category=p.category,
                price_inr=p.price_inr,
                unit_cost_inr=p.unit_cost_inr,
                margin_pct=p.margin_pct,
                orders_28d=p.stats.orders_28d,
                units_28d=p.stats.units_28d,
                trend_pct=p.stats.trend_pct,
            )
            for p in cat.products
            if p.selected
        ],
        pairs=[
            PairFact(
                sku_a=x.sku_a,
                sku_b=x.sku_b,
                orders_together_28d=x.orders_together_28d,
                confidence_a_to_b=x.confidence_a_to_b,
                lift=x.lift,
            )
            for x in cat.pairs
            if x.sku_a in selected and x.sku_b in selected
        ],
        basket_orders_28d=cat.basket.orders_28d,
        basket_average_inr=cat.basket.average_value_inr,
        basket_p75_inr=cat.basket.p75_value_inr,
        demand_changes=_slots(analysis),
        observed_facts=observed_facts(analysis),
        policy=PolicyLimits(
            discount_cap_pct=policy.discount_cap_pct,
            max_loss_inr=policy.max_loss_inr,
            min_margin_pct=policy.min_margin_pct,
            budget_inr=policy.budget_inr,
        ),
        allowed_kinds=list(dict.fromkeys(req.kinds)),
        preferences={k: v for k, v in prefs.items() if k == "max_discount_inr"},
    )
    candidates = provider.generate_promotions(ctx).candidates  # LLMError propagates
    gen_provider, gen_model = provider.name, provider.model

    products = products_map(repo, merchant_id)
    orders, items = repo.orders_for(merchant_id), repo.items_for(merchant_id)
    as_of = pd.Timestamp(repo.as_of)
    ratio = merchant_cost_ratio(repo, merchant_id)
    saved_costs: dict[str, Any] = profile["campaign_costs_inr"]
    max_disc = prefs.get("max_discount_inr")
    allowed_kinds = set(ctx.allowed_kinds)

    offers: list[_Offer] = []
    letters = iter("ABCDEFGH")
    for cand in candidates:
        o = _Offer(next(letters), cand, [])
        outside = [s for s in cand.skus if s not in selected]
        if outside:
            names = [products[s].name if s in products else s for s in outside]
            o.filter_reasons.append(
                f"Uses product(s) you did not select for offers: {', '.join(names)}."
            )
        if cand.kind not in allowed_kinds:
            o.filter_reasons.append(f"{cand.kind.capitalize()} offers were not requested.")
        if max_disc and max_disc.isdigit() and cand.discount_inr > int(max_disc):
            o.filter_reasons.append(
                f"₹{cand.discount_inr} off is more than your preferred maximum of ₹{max_disc}."
            )
        kind = cand.kind
        o.spec = PromotionSpec(
            kind=kind,
            skus=list(cand.skus),
            discount_inr=Decimal(cand.discount_inr),
            min_cart_value_inr=(
                None if cand.min_cart_value_inr is None else Decimal(cand.min_cart_value_inr)
            ),
            hours=None if cand.hours is None else sorted(set(cand.hours)),
            weekdays_only=cand.weekdays_only,
            eligible_skus=sorted(selected) if kind == "cart" else [],
        )
        o.spec = o.spec.model_copy(update={"label": label_for(o.spec, products)})
        if not o.filter_reasons:
            cost_key = CAMPAIGN_TYPE_FOR[kind]
            cost = (
                req.campaign_costs_inr[kind]
                if kind in req.campaign_costs_inr
                else saved_costs.get(cost_key)
            )
            o.inp, o.notes = build_input(
                o.spec,
                campaign_ref=f"run-offer-{o.record_id}",
                products=products,
                orders=orders,
                items=items,
                as_of=as_of,
                incremental={
                    "conservative": cand.extra_orders.conservative,
                    "expected": cand.extra_orders.expected,
                    "optimistic": cand.extra_orders.optimistic,
                },
                campaign_cost_inr=None if cost is None else Decimal(str(cost)),
                merchant_cost_ratio=ratio,
                is_synthetic=repo.is_synthetic,
            )
            o.economics = evaluate_campaign(o.inp)
            o.policy = evaluate_policy(o.economics, o.inp, policy)
            o.campaign_id = uuid.uuid4()
        offers.append(o)

    chosen = _select(offers)
    records = []
    for o in offers:
        if o.economics is None or o.policy is None or o.economics.status != "ok":
            continue
        assert o.inp is not None and o.inp.promotion is not None
        s = {x.scenario: x.incremental_contribution_profit_inr for x in o.economics.scenarios}
        records.append(
            ExplainRecord(
                record_id=o.record_id,
                campaign_type=o.inp.campaign_type,
                offer=o.inp.promotion.label,
                discount_inr=o.candidate.discount_inr,
                scenario_profits_inr=ScenarioProfits(
                    conservative=s["conservative"],
                    expected=s["expected"],
                    optimistic=s["optimistic"],
                ),
                break_even_orders=o.economics.break_even_incremental_orders,
                policy_passed=o.policy.permitted,
                policy_reasons=[
                    r.reason
                    for r in o.policy.rules
                    if not r.passed and r.blocks == "recommendation"
                ],
                selected=o is chosen,
            )
        )
    explain_request = (
        ExplanationRequest(
            merchant_name=str(profile["name"]),
            lang=req.lang,
            records=records[:10],
            context_facts=ctx.observed_facts,
        )
        if records
        else None
    )
    no_records = (
        "Koi bhi offer poori tarah evaluate nahi ho saka."
        if req.lang == "hinglish"
        else "No offer could be fully evaluated, so there is no recommendation."
    )
    explanation = explain_grounded(provider, explain_request, no_records)

    if chosen is None:
        selection = SelectionOut(
            campaign_id=None,
            record_id=None,
            campaign_type="no_action",
            rule=SELECTION_RULE,
            reason="No offer passed every policy rule with a positive expected profit; "
            "running no offer is the recommendation.",
        )
    else:
        assert chosen.economics is not None and chosen.inp is not None
        exp = chosen.economics.scenario("expected").incremental_contribution_profit_inr
        selection = SelectionOut(
            campaign_id=chosen.campaign_id,
            record_id=chosen.record_id,
            campaign_type=chosen.inp.campaign_type,
            rule=SELECTION_RULE,
            reason=f"Option {chosen.record_id} has the highest expected profit (₹{exp}) "
            "among offers that pass every policy rule.",
        )

    run_id = uuid.uuid4()
    actor = f"nafapilot:{gen_provider}"
    result: dict[str, Any] = {
        "candidates": [_offer_out(o).model_dump(mode="json") for o in offers],
        "selection": selection.model_dump(mode="json"),
        "explain_request": None
        if explain_request is None
        else explain_request.model_dump(mode="json", exclude={"lang", "question"}),
        "learning": LearningOut(
            preference_learning=PREFERENCE_LEARNING,
            performance_learning=PERFORMANCE_LEARNING,
            preferences_applied={k: v for k, v in prefs.items() if k == "max_discount_inr"},
            past_decisions_used=len(history),
        ).model_dump(mode="json"),
        "data_source": repo.data_source,
        "is_synthetic": repo.is_synthetic,
    }
    with session.begin():
        session.add(
            RecommendationRun(
                id=run_id,
                merchant_id=merchant_id,
                provider=gen_provider,
                model=gen_model,
                prompt_version=PROMPT_VERSION,
                context_hash=hashlib.sha256(ctx.model_dump_json().encode()).hexdigest(),
                context={
                    "mode": "promotion",
                    "llm_context": ctx.model_dump(mode="json"),
                    "selection_version": cat.selection_version,
                    "lang": req.lang,
                },
                result=result,
                created_by=req.requested_by,
            )
        )
        session.flush()
        for o in offers:
            if o.inp is not None:
                create_campaign_in_tx(
                    session,
                    repo,
                    CampaignCreate(
                        merchant_id=merchant_id,
                        campaign=o.inp,
                        created_by=actor,
                        change_note=f"Generated as option {o.record_id} in an offer run.",
                    ),
                    recommendation_run_id=run_id,
                    campaign_id=o.campaign_id,
                )
        _store_explanation(
            session, run_id, merchant_id, req.lang, None, explanation, req.requested_by
        )
        session.add(
            AuditEvent(
                actor=actor,
                event_type="promotion_run_generated",
                merchant_id=merchant_id,
                campaign_id=None,
                payload={
                    "recommendation_run_id": str(run_id),
                    "provider": gen_provider,
                    "model": gen_model,
                    "prompt_version": PROMPT_VERSION,
                    "offers": len(offers),
                    "filtered": sum(1 for o in offers if o.filter_reasons),
                    "selected_record": selection.record_id,
                    "selection_version": cat.selection_version,
                },
            )
        )
    return run_id


def _offer_out(o: _Offer) -> CandidateOut:
    promo = o.spec if o.inp is None else o.inp.promotion
    return CandidateOut(
        record_id=o.record_id,
        status="filtered_by_preference" if o.filter_reasons else "evaluated",
        campaign_type=CAMPAIGN_TYPE_FOR[o.candidate.kind],
        discount_inr=o.candidate.discount_inr,
        target_group="all_customers",
        llm_rationale=o.candidate.rationale,
        filter_reasons=o.filter_reasons,
        cost_source="missing" if o.inp is None or o.inp.campaign_cost_inr is None else "request",
        campaign_id=o.campaign_id,
        input_hash=None if o.economics is None else o.economics.input_hash,
        economics=o.economics,
        policy=o.policy,
        promotion=promo,
        notes=o.notes,
    )
