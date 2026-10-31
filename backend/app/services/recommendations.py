"""Recommendation runs: LLM candidates -> deterministic checks -> stored campaigns.

Flow (generate_run):
1. Read stored merchant preferences and recent decisions (short read transaction).
2. Build the LLM context from validated analytics (M4), merchant settings and policy.
3. Ask the provider for candidates (no transaction is held during the network call).
4. In code: drop candidates that break an explicit preference, evaluate every remaining
   candidate with the M5 engines, and select the best permitted one by a fixed rule.
5. Ask the provider to explain the stored results; keep the text only if every number
   in it appears in the records it was given, otherwise use the deterministic template.
6. Write the run, its campaigns (each version 1, evaluated), the explanation and the
   audit events in one transaction.

Learning here is preference learning only: stored, explicit merchant preferences and
past decisions are passed as context and enforced as filters. No model is retrained,
and campaign performance is not learned until outcomes are measured (M8).
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data import DataRepository
from app.db.models import (
    AuditEvent,
    Campaign,
    CampaignApproval,
    CampaignVersion,
    Explanation,
    MerchantPreference,
    RecommendationRun,
)
from app.schemas.analytics import MerchantAnalysis
from app.schemas.campaigns import CampaignCreate
from app.schemas.decision import CampaignEconomics, CampaignInput, PolicyDecision
from app.schemas.llm import (
    CampaignCandidate,
    CampaignType,
    CandidateContext,
    ExplainRecord,
    ExplanationRequest,
    FeedbackNote,
    Language,
    PolicyLimits,
    ScenarioProfits,
)
from app.schemas.recommendations import (
    CandidateOut,
    ExplanationOut,
    LearningOut,
    PreferenceOut,
    PreferencesOut,
    RecommendationRequest,
    RecommendationRunOut,
    SelectionOut,
)
from app.services.analytics import analyze_merchant
from app.services.campaign_store import (
    CampaignError,
    PolicyMissing,
    create_campaign_in_tx,
)
from app.services.decision import evaluate_campaign, evaluate_policy
from app.services.decision.adapters import input_from_candidate
from app.services.llm import LLMError, LLMProvider
from app.services.llm.grounding import ungrounded_numbers
from app.services.llm.prompts import PROMPT_VERSION
from app.services.llm.templates import template_explanation

HISTORY_LIMIT = 20
SELECTION_RULE = (
    "Highest expected incremental contribution profit among candidates that pass every "
    "policy rule; ties go to the higher conservative profit, then the smaller discount. "
    "If none has a positive expected profit, recommend no action."
)
PREFERENCE_LEARNING = (
    "Stored merchant preferences and past decisions were given to the model as context, "
    "and preferences were enforced as filters in code. The model was not retrained."
)
PERFORMANCE_LEARNING = (
    "Not available yet: campaign outcomes are measured only after simulated execution "
    "(milestone 8)."
)


class RunNotFound(CampaignError):
    http_status, error_code = 404, "recommendation_run_not_found"


# ------------------------------------------------------------------ preferences


def current_preferences(session: Session, merchant_id: str) -> dict[str, MerchantPreference]:
    rows = session.scalars(
        select(MerchantPreference)
        .where(MerchantPreference.merchant_id == merchant_id)
        .order_by(MerchantPreference.id)
    )
    latest: dict[str, MerchantPreference] = {}
    for row in rows:
        latest[row.key] = row  # later rows win
    # An empty value is an explicit "no preference" saved from the settings screen.
    return {k: row for k, row in latest.items() if row.value != ""}


def preferences_out(session: Session, merchant_id: str) -> PreferencesOut:
    with session.begin():
        prefs = current_preferences(session, merchant_id)
        return PreferencesOut(
            merchant_id=merchant_id,
            preferences={
                k: PreferenceOut(
                    value=p.value,
                    set_by=p.created_by,
                    set_at=p.created_at,
                    campaign_version_id=p.campaign_version_id,
                )
                for k, p in prefs.items()
            },
        )


def preference_violations(candidate: CampaignCandidate, prefs: dict[str, str]) -> list[str]:
    """Deterministic check of a candidate against explicit merchant preferences."""
    reasons: list[str] = []
    target = prefs.get("target")
    group = candidate.target_group.value
    if target == "new_customers" and group not in ("new_customers", "none"):
        reasons.append("Merchant preference: offers only for new customers.")
    if target == "existing_customers" and group not in ("eligible_existing_customers", "none"):
        reasons.append("Merchant preference: offers only for existing customers.")
    cap = prefs.get("max_discount_inr")
    if cap is not None and candidate.discount_inr > int(cap):
        reasons.append(f"Merchant preference: discount at most ₹{cap}.")
    if prefs.get("avoid_campaign_type") == candidate.campaign_type.value:
        reasons.append(f"Merchant preference: no {candidate.campaign_type.value} campaigns.")
    return reasons


def decision_history(session: Session, merchant_id: str) -> list[FeedbackNote]:
    rows = session.execute(
        select(Campaign.campaign_type, CampaignApproval.decision, CampaignApproval.reason)
        .join(CampaignVersion, CampaignVersion.id == CampaignApproval.campaign_version_id)
        .join(Campaign, Campaign.id == CampaignVersion.campaign_id)
        .where(Campaign.merchant_id == merchant_id)
        .order_by(CampaignApproval.decided_at.desc())
        .limit(HISTORY_LIMIT)
    ).all()
    notes = []
    for ctype, decision, reason in rows:
        try:
            kind = CampaignType(ctype)
        except ValueError:
            continue
        notes.append(
            FeedbackNote(
                campaign_type=kind,
                decision=decision,
                reason=None if reason is None else reason[:300],
            )
        )
    return notes


# ------------------------------------------------------------------- context


def observed_facts(analysis: MerchantAnalysis) -> list[str]:
    facts = [a.message for a in analysis.alerts]
    b = analysis.baseline
    if b.orders:
        facts.append(
            f"Last 28 days ({b.period_start:%d %b}-{b.period_end:%d %b}): {b.orders} orders "
            f"on {b.days_with_orders} days with orders."
        )
    facts.extend(analysis.insufficient_reasons)
    if analysis.opportunity_status == "no_significant_change":
        facts.append("No significant change in orders was detected.")
    return facts[:20]


@dataclass
class _Candidate:
    record_id: str
    candidate: CampaignCandidate
    filter_reasons: list[str]
    inp: CampaignInput | None = None
    economics: CampaignEconomics | None = None
    policy: PolicyDecision | None = None
    campaign_id: uuid.UUID | None = None


def select_best(cands: list[_Candidate]) -> _Candidate | None:
    """The fixed selection rule (see SELECTION_RULE). Pure; the LLM has no say."""
    eligible = [
        c
        for c in cands
        if c.economics is not None
        and c.policy is not None
        and c.economics.status == "ok"
        and c.policy.permitted
    ]

    def key(c: _Candidate) -> tuple[Decimal, Decimal, int, str]:
        assert c.economics is not None
        exp = c.economics.scenario("expected").incremental_contribution_profit_inr
        con = c.economics.scenario("conservative").incremental_contribution_profit_inr
        return (exp, con, -c.candidate.discount_inr, c.record_id)

    if not eligible:
        return None
    best = max(eligible, key=key)
    assert best.economics is not None
    if best.economics.scenario("expected").incremental_contribution_profit_inr <= 0:
        return None
    return best


def _explain_records(cands: list[_Candidate], chosen: _Candidate | None) -> list[ExplainRecord]:
    records = []
    for c in cands:
        if c.economics is None or c.policy is None or c.economics.status != "ok":
            continue
        s = {x.scenario: x.incremental_contribution_profit_inr for x in c.economics.scenarios}
        records.append(
            ExplainRecord(
                record_id=c.record_id,
                campaign_type=c.candidate.campaign_type,
                discount_inr=c.candidate.discount_inr,
                scenario_profits_inr=ScenarioProfits(
                    conservative=s["conservative"],
                    expected=s["expected"],
                    optimistic=s["optimistic"],
                ),
                break_even_orders=c.economics.break_even_incremental_orders,
                policy_passed=c.policy.permitted,
                policy_reasons=[
                    r.reason
                    for r in c.policy.rules
                    if not r.passed and r.blocks == "recommendation"
                ],
                selected=c is chosen,
            )
        )
    return records[:10]


# --------------------------------------------------------------- explanation


@dataclass
class _ExplanationResult:
    text: str
    referenced: list[str]
    source: str
    provider: str
    model: str
    grounded: bool
    fallback_reason: str | None


def explain_grounded(
    provider: LLMProvider, request: ExplanationRequest | None, no_records_text: str
) -> _ExplanationResult:
    """LLM explanation if it is grounded in the request's numbers; else the template."""
    if request is None:
        return _ExplanationResult(
            no_records_text, [], "template", "template", "template", True, "no_evaluable_candidates"
        )
    try:
        exp = provider.explain(request)
        bad = ungrounded_numbers(exp.text, request.model_dump_json())
        if not bad:
            return _ExplanationResult(
                exp.text,
                exp.referenced_record_ids,
                "llm",
                provider.name,
                provider.model,
                True,
                None,
            )
        reason = f"ungrounded_numbers: {', '.join(bad[:5])}"
    except LLMError as err:
        reason = err.error_code
    t = template_explanation(request, reason)
    return _ExplanationResult(
        t.text, t.referenced_record_ids, "template", provider.name, provider.model, True, reason
    )


def _no_records_text(cands: list[_Candidate], lang: Language) -> str:
    missing = sorted({i.field for c in cands if c.economics for i in c.economics.issues})
    if lang == "hinglish":
        base = "Koi bhi option poori tarah evaluate nahi ho saka, isliye abhi koi suggestion nahi."
        return base + (f" Missing: {', '.join(missing)}." if missing else "")
    base = "No candidate could be fully evaluated, so there is no recommendation."
    return base + (f" Missing or invalid: {', '.join(missing)}." if missing else "")


# --------------------------------------------------------------------- run


def generate_run(
    session: Session,
    repo: DataRepository,
    provider: LLMProvider,
    merchant_id: str,
    req: RecommendationRequest,
) -> uuid.UUID:
    profile = repo.merchant_profile(merchant_id)  # raises UnknownMerchant
    info = repo.merchant_info(merchant_id)
    policy = repo.merchant_policy(merchant_id)
    if policy is None:
        raise PolicyMissing(f"No policy is configured for merchant {merchant_id}.")
    if info.aov_setting_inr is None or info.aov_setting_inr <= 0:
        raise PolicyMissing(f"Merchant {merchant_id} has no average order value setting.")
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

    avoid = prefs.get("avoid_campaign_type")
    ctx = CandidateContext(
        merchant_id=merchant_id,
        merchant_name=str(profile["name"]),
        average_order_value_inr=info.aov_setting_inr,
        variable_cost_per_order_inr=info.vc_setting_inr,
        observed_facts=observed_facts(analysis),
        policy=PolicyLimits(
            discount_cap_pct=policy.discount_cap_pct,
            max_loss_inr=policy.max_loss_inr,
            min_margin_pct=policy.min_margin_pct,
            budget_inr=policy.budget_inr,
        ),
        allowed_campaign_types=[t for t in CampaignType if t.value != avoid],
        preferences=prefs,
        feedback_history=history,
    )
    candidates = provider.generate_candidates(ctx).candidates  # LLMError propagates
    # Who actually proposed the candidates (a fallback chain may have moved on).
    gen_provider, gen_model = provider.name, provider.model

    cands: list[_Candidate] = []
    letters = iter("ABCDEFGH")
    for cand in candidates:
        if cand.campaign_type is CampaignType.NO_ACTION:
            continue  # the baseline is implicit: selection falls back to no action
        c = _Candidate(next(letters), cand, preference_violations(cand, prefs))
        if not c.filter_reasons:
            c.inp = input_from_candidate(
                cand,
                campaign_ref=f"run-candidate-{c.record_id}",
                average_order_value_inr=info.aov_setting_inr,
                variable_cost_per_order_inr=info.vc_setting_inr,
                campaign_cost_inr=req.campaign_costs_inr.get(cand.campaign_type),
                is_synthetic=repo.is_synthetic,
            )
            c.economics = evaluate_campaign(c.inp)
            c.policy = evaluate_policy(c.economics, c.inp, policy)
            c.campaign_id = uuid.uuid4()
        cands.append(c)

    chosen = select_best(cands)
    records = _explain_records(cands, chosen)
    explain_request = (
        ExplanationRequest(
            merchant_name=str(profile["name"]),
            lang=req.lang,
            records=records,
            context_facts=ctx.observed_facts,
        )
        if records
        else None
    )
    explanation = explain_grounded(provider, explain_request, _no_records_text(cands, req.lang))

    if chosen is None:
        selection = SelectionOut(
            campaign_id=None,
            record_id=None,
            campaign_type="no_action",
            rule=SELECTION_RULE,
            reason="No candidate passed every policy rule with a positive expected profit; "
            "doing nothing is the recommendation.",
        )
    else:
        assert chosen.economics is not None
        exp = chosen.economics.scenario("expected").incremental_contribution_profit_inr
        selection = SelectionOut(
            campaign_id=chosen.campaign_id,
            record_id=chosen.record_id,
            campaign_type=chosen.candidate.campaign_type.value,
            rule=SELECTION_RULE,
            reason=f"Option {chosen.record_id} has the highest expected profit (₹{exp}) "
            "among options that pass every policy rule.",
        )

    ctx_json = ctx.model_dump(mode="json")
    run_id = uuid.uuid4()
    actor = f"nafapilot:{gen_provider}"
    result: dict[str, Any] = {
        "candidates": [_candidate_out(c).model_dump(mode="json") for c in cands],
        "selection": selection.model_dump(mode="json"),
        "explain_request": None
        if explain_request is None
        else explain_request.model_dump(mode="json", exclude={"lang", "question"}),
        "learning": LearningOut(
            preference_learning=PREFERENCE_LEARNING,
            performance_learning=PERFORMANCE_LEARNING,
            preferences_applied=prefs,
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
                    "llm_context": ctx_json,
                    "campaign_costs_inr": {
                        k.value: format(v, "f") for k, v in req.campaign_costs_inr.items()
                    },
                    "settings_source": "merchant_entered_settings",
                    "lang": req.lang,
                },
                result=result,
                created_by=req.requested_by,
            )
        )
        session.flush()
        for c in cands:
            if c.inp is not None:
                create_campaign_in_tx(
                    session,
                    repo,
                    CampaignCreate(
                        merchant_id=merchant_id,
                        campaign=c.inp,
                        created_by=actor,
                        change_note=f"Generated as option {c.record_id} in recommendation run.",
                    ),
                    recommendation_run_id=run_id,
                    campaign_id=c.campaign_id,
                )
        _store_explanation(
            session, run_id, merchant_id, req.lang, None, explanation, req.requested_by
        )
        session.add(
            AuditEvent(
                actor=actor,
                event_type="recommendation_generated",
                merchant_id=merchant_id,
                campaign_id=None,
                payload={
                    "recommendation_run_id": str(run_id),
                    "provider": gen_provider,
                    "model": gen_model,
                    "prompt_version": PROMPT_VERSION,
                    "candidates": len(cands),
                    "filtered_by_preference": sum(1 for c in cands if c.filter_reasons),
                    "selected_record": selection.record_id,
                    "selected_campaign_id": (
                        None if selection.campaign_id is None else str(selection.campaign_id)
                    ),
                    "preferences_applied": sorted(prefs),
                    "past_decisions_used": len(history),
                },
            )
        )
    return run_id


def _candidate_out(c: _Candidate) -> CandidateOut:
    return CandidateOut(
        record_id=c.record_id,
        status="filtered_by_preference" if c.filter_reasons else "evaluated",
        campaign_type=c.candidate.campaign_type.value,
        discount_inr=c.candidate.discount_inr,
        target_group=c.candidate.target_group.value,
        llm_rationale=c.candidate.rationale,
        filter_reasons=c.filter_reasons,
        cost_source="missing" if c.inp is None or c.inp.campaign_cost_inr is None else "request",
        campaign_id=c.campaign_id,
        input_hash=None if c.economics is None else c.economics.input_hash,
        economics=c.economics,
        policy=c.policy,
    )


def _store_explanation(
    session: Session,
    run_id: uuid.UUID,
    merchant_id: str,
    lang: Language,
    question: str | None,
    e: _ExplanationResult,
    actor: str,
) -> uuid.UUID:
    row = Explanation(
        id=uuid.uuid4(),
        recommendation_run_id=run_id,
        lang=lang,
        question=question,
        source=e.source,
        provider=e.provider,
        model=e.model,
        grounded=e.grounded,
        text=e.text,
        referenced_records=e.referenced,
        fallback_reason=e.fallback_reason,
    )
    session.add(row)
    session.add(
        AuditEvent(
            actor=actor,
            event_type="explanation_generated",
            merchant_id=merchant_id,
            campaign_id=None,
            payload={
                "recommendation_run_id": str(run_id),
                "lang": lang,
                "source": e.source,
                "grounded": e.grounded,
                "fallback_reason": e.fallback_reason,
            },
        )
    )
    return row.id


def explain_run(
    session: Session,
    provider: LLMProvider,
    run_id: uuid.UUID,
    lang: Language,
    question: str | None,
    actor: str,
) -> uuid.UUID:
    """A new explanation (e.g. Hinglish, or answering 'why not B?') of a stored run.

    Uses the run's stored records, so it explains what was decided at generation time.
    """
    with session.begin():
        run = session.get(RecommendationRun, run_id)
        if run is None:
            raise RunNotFound(f"No recommendation run {run_id}.")
        stored = run.result.get("explain_request")
        merchant_id = run.merchant_id
    request = (
        None
        if stored is None
        else ExplanationRequest.model_validate({**stored, "lang": lang, "question": question})
    )
    result = explain_grounded(
        provider,
        request,
        "No candidate in this run could be fully evaluated, so there is nothing to explain.",
    )
    with session.begin():
        return _store_explanation(session, run_id, merchant_id, lang, question, result, actor)


def get_run(session: Session, run_id: uuid.UUID) -> RecommendationRunOut:
    with session.begin():
        run = session.get(RecommendationRun, run_id)
        if run is None:
            raise RunNotFound(f"No recommendation run {run_id}.")
        explanations = session.scalars(
            select(Explanation)
            .where(Explanation.recommendation_run_id == run.id)
            .order_by(Explanation.created_at, Explanation.id)
        ).all()
        r = run.result
        return RecommendationRunOut(
            run_id=run.id,
            merchant_id=run.merchant_id,
            created_at=run.created_at,
            provider=run.provider,
            model=run.model,
            prompt_version=run.prompt_version,
            context=run.context,
            candidates=[CandidateOut.model_validate(c) for c in r["candidates"]],
            selection=SelectionOut.model_validate(r["selection"]),
            explanations=[
                ExplanationOut(
                    explanation_id=e.id,
                    lang=e.lang,
                    question=e.question,
                    source=e.source,
                    provider=e.provider,
                    model=e.model,
                    grounded=e.grounded,
                    text=e.text,
                    referenced_records=e.referenced_records,
                    fallback_reason=e.fallback_reason,
                    created_at=e.created_at,
                )
                for e in explanations
            ],
            learning=LearningOut.model_validate(r["learning"]),
            data_source=r["data_source"],
            is_synthetic=r["is_synthetic"],
        )
