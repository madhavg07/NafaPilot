"""Simulated execution and measurement of an approved campaign version (M8).

Eligibility (checked inside one locked transaction; refusals are audited):
  the version is the campaign's current version, it has an *approved* decision bound to
  its input hash, every deterministic policy rule passes now (re-checked against the
  merchant's current policy), and it has never been executed.

Idempotency:
  - The caller supplies an Idempotency-Key. A retry with the same key for the same
    version returns the original experiment (replayed=true); nothing is re-simulated.
  - A different key for an already executed version is refused (already_executed).
  - A key already used for another version is refused (idempotency_key_reused).
  - Database: UNIQUE(campaign_version_id) and UNIQUE(idempotency_key) on executions;
    concurrent requests that race past the checks hit these and are resolved the same
    way. On PostgreSQL the campaign row is also locked (SELECT ... FOR UPDATE).

Atomicity: execution, assignments, outcomes, measurement, the campaign status change and
the completion audit events are one transaction. A failure leaves none of them and is
recorded as an execution_failed audit event; the campaign stays not_executed.

Nothing here contacts Paytm or any real system; mode is always "simulated".
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data import DataRepository
from app.db.models import (
    Assignment,
    Campaign,
    CampaignApproval,
    CampaignEvaluation,
    CampaignVersion,
    Execution,
    Measurement,
    Outcome,
    utcnow,
)
from app.schemas.decision import ApprovalRecord, CampaignInput
from app.schemas.experiments import (
    AssignmentOut,
    AssignmentPage,
    ExecuteRequest,
    ExperimentOut,
)
from app.services.campaign_store import (
    AlreadyExecuted,
    CampaignError,
    CampaignNotFound,
    PolicyBlocked,
    StaleVersion,
    _audit,
    _lock_campaign,
    _policy,
    _version,
)
from app.services.decision import evaluate_campaign, evaluate_policy
from app.services.experiments.assignment import (
    assign,
    eligible_customers,
    experiment_seed,
)
from app.services.experiments.measurement import MIN_MEASURED_PER_ARM, measure
from app.services.experiments.simulation import SimulationParams, simulate

CAMPAIGN_DAYS = 28
MIN_ASSIGNED_PER_ARM = MIN_MEASURED_PER_ARM


class NotApproved(CampaignError):
    http_status, error_code = 409, "not_approved"


class IdempotencyKeyReused(CampaignError):
    http_status, error_code = 409, "idempotency_key_reused"


class InsufficientData(CampaignError):
    http_status, error_code = 422, "insufficient_data"


class ExperimentNotFound(CampaignError):
    http_status, error_code = 404, "experiment_not_found"


_REFUSALS = (
    StaleVersion,
    NotApproved,
    PolicyBlocked,
    AlreadyExecuted,
    IdempotencyKeyReused,
    InsufficientData,
)


# ---------------------------------------------------------------------- helpers


def _anonymous_recent_orders(repo: DataRepository, merchant_id: str) -> int:
    orders = repo.orders_for(merchant_id)
    if orders.empty:
        return 0
    dates = pd.to_datetime(orders["order_date"], errors="coerce").dt.date
    recent = dates > repo.as_of - timedelta(days=CAMPAIGN_DAYS)
    return int((recent & orders["customer_id"].isna()).sum())


def _prediction(session: Session, version: CampaignVersion) -> dict[str, Any]:
    """The stored evaluation snapshot, read-only. Prefers the approval-time evaluation."""
    evals = {
        e.trigger: e
        for e in session.scalars(
            select(CampaignEvaluation).where(CampaignEvaluation.campaign_version_id == version.id)
        )
    }
    ev = evals.get("decision_recorded") or evals["version_created"]
    econ = ev.economics
    return {
        "source": "stored_version_evaluation",
        "result_kind": econ.get("result_kind", "prediction"),
        "evaluation_trigger": ev.trigger,
        "evaluated_at": ev.evaluated_at.isoformat(),
        "policy_version": ev.policy_version,
        "scenarios": [
            {
                "scenario": s["scenario"],
                "incremental_orders": s["incremental_orders"],
                "existing_discounted_orders": s["existing_discounted_orders"],
                "incremental_contribution_profit_inr": s["incremental_contribution_profit_inr"],
            }
            for s in econ.get("scenarios", [])
        ],
        "break_even_incremental_orders": econ.get("break_even_incremental_orders"),
    }


def _existing(
    session: Session, version_id: uuid.UUID, key: str
) -> tuple[Execution | None, Execution | None]:
    by_key = session.scalar(select(Execution).where(Execution.idempotency_key == key))
    by_version = session.scalar(
        select(Execution).where(Execution.campaign_version_id == version_id)
    )
    return by_key, by_version


def _check_replay(
    by_key: Execution | None, by_version: Execution | None, version_id: uuid.UUID
) -> uuid.UUID | None:
    if by_key is not None:
        if by_key.campaign_version_id == version_id:
            return by_key.id
        raise IdempotencyKeyReused(
            "This Idempotency-Key was already used for a different campaign version."
        )
    if by_version is not None:
        raise AlreadyExecuted(
            "This version has already been executed (in simulation). Each version runs once.",
            details={"experiment_id": str(by_version.id)},
        )
    return None


# ---------------------------------------------------------------------- execute


def execute(
    session: Session,
    repo: DataRepository,
    campaign_id: uuid.UUID,
    version_number: int,
    idempotency_key: str,
    body: ExecuteRequest,
) -> tuple[uuid.UUID, bool]:
    """Returns (experiment_id, replayed)."""
    requested_at = utcnow()
    with session.begin():
        campaign = session.get(Campaign, campaign_id)
        if campaign is None:
            raise CampaignNotFound(f"No campaign {campaign_id}.")
        merchant_id = campaign.merchant_id
        _audit(
            session,
            "execution_requested",
            actor=body.requested_by,
            merchant_id=merchant_id,
            campaign_id=campaign_id,
            payload={
                "version": version_number,
                "idempotency_key": idempotency_key,
                "mode": "simulated",
            },
        )
    version_id: uuid.UUID | None = None
    try:
        with session.begin():
            campaign = _lock_campaign(session, campaign_id)
            version = _version(session, campaign.id, version_number)
            version_id = version.id
            by_key, by_version = _existing(session, version.id, idempotency_key)
            replay = _check_replay(by_key, by_version, version.id)
            if replay is not None:
                return_id: uuid.UUID = replay
            else:
                return_id = _run(
                    session, repo, campaign, version, idempotency_key, body, requested_at
                )
        replayed = replay is not None
    except IntegrityError:
        # A concurrent request committed first; resolve exactly like a sequential retry.
        with session.begin():
            assert version_id is not None
            by_key, by_version = _existing(session, version_id, idempotency_key)
        try:
            replay = _check_replay(by_key, by_version, version_id)
        except _REFUSALS as err:
            _refused(session, body, merchant_id, campaign_id, version_id, version_number, err)
            raise
        if replay is None:  # pragma: no cover - integrity error without a winner
            raise
        return_id, replayed = replay, True
    except _REFUSALS as err:
        _refused(session, body, merchant_id, campaign_id, version_id, version_number, err)
        raise
    except CampaignError:
        raise
    except Exception as exc:
        with session.begin():
            _audit(
                session,
                "execution_failed",
                actor=body.requested_by,
                merchant_id=merchant_id,
                campaign_id=campaign_id,
                version_id=version_id,
                payload={
                    "version": version_number,
                    "error_type": type(exc).__name__,
                    "execution_status": "not_executed",
                },
            )
        raise
    if replayed:
        with session.begin():
            _audit(
                session,
                "execution_replayed",
                actor=body.requested_by,
                merchant_id=merchant_id,
                campaign_id=campaign_id,
                version_id=version_id,
                payload={"version": version_number, "experiment_id": str(return_id)},
            )
    return return_id, replayed


def _refused(
    session: Session,
    body: ExecuteRequest,
    merchant_id: str,
    campaign_id: uuid.UUID,
    version_id: uuid.UUID | None,
    version_number: int,
    err: CampaignError,
) -> None:
    with session.begin():
        _audit(
            session,
            "execution_refused",
            actor=body.requested_by,
            merchant_id=merchant_id,
            campaign_id=campaign_id,
            version_id=version_id,
            payload={
                "version": version_number,
                "error_code": err.error_code,
                **{k: v for k, v in err.details.items() if k != "reasons"},
            },
        )


def _run(
    session: Session,
    repo: DataRepository,
    campaign: Campaign,
    version: CampaignVersion,
    key: str,
    body: ExecuteRequest,
    requested_at: Any,
) -> uuid.UUID:
    if version.version != campaign.current_version:
        raise StaleVersion(
            f"Version {version.version} is not current (current is {campaign.current_version})."
        )
    approval = session.scalar(
        select(CampaignApproval).where(CampaignApproval.campaign_version_id == version.id)
    )
    if approval is None or approval.decision != "approved":
        raise NotApproved(
            "This version has no merchant approval"
            + ("" if approval is None else f" (it was {approval.decision})")
            + "; it cannot be executed.",
            details={"decision": None if approval is None else approval.decision},
        )
    inp = CampaignInput.model_validate(version.inputs)
    policy = _policy(repo, campaign.merchant_id)
    economics = evaluate_campaign(inp)
    decision = evaluate_policy(
        economics,
        inp,
        policy,
        ApprovalRecord(approved_input_hash=approval.input_hash, approved_by=approval.decided_by),
    )
    if not decision.permitted:
        raise PolicyBlocked(
            "Execution refused: deterministic policy rules fail under the current policy.",
            details={"blocked_by": decision.blocked_by},
        )
    if not decision.executable:  # pragma: no cover - approval hash is FK-bound to the version
        raise NotApproved("The approval does not match this version's inputs.")
    if inp.promotion is not None:
        # The merchant may have removed a product from offers after approving.
        _, _, allowed = repo.product_selection(campaign.merchant_id)
        dropped = sorted(set(inp.promotion.skus) - set(allowed))
        if dropped:
            raise PolicyBlocked(
                "Execution refused: the offer uses product(s) no longer selected for offers.",
                details={"blocked_by": ["product_selection"], "skus": dropped},
            )

    # ----- eligibility and assignment
    elig = eligible_customers(
        repo.customers_for(campaign.merchant_id),
        inp.campaign_type,
        _anonymous_recent_orders(repo, campaign.merchant_id),
    )
    if elig is None:
        raise InsufficientData(f"No eligibility rule for campaign type {inp.campaign_type}.")
    seed = experiment_seed(str(version.id), version.input_hash)
    holdout_pct = int(policy.holdout_pct)
    sim = body.simulation
    assigned = assign(elig.customer_ids, seed, holdout_pct, sim.max_customers)
    n_t = sum(a.group == "treatment" for a in assigned)
    n_h = len(assigned) - n_t
    if min(n_t, n_h) < MIN_ASSIGNED_PER_ARM:
        raise InsufficientData(
            f"Too few eligible customers: {n_t} treatment / {n_h} holdout; at least "
            f"{MIN_ASSIGNED_PER_ARM} per group are needed.",
            details={
                "eligible": len(elig.customer_ids),
                "treatment": n_t,
                "holdout": n_h,
                "excluded": elig.excluded,
            },
        )

    # ----- simulation ground truth (documented, labelled)
    if sim.base_rate_per_customer is not None:
        base_rate, base_source = float(sim.base_rate_per_customer), "request"
    else:
        hist, hist_n = repo.historical_holdout(campaign.merchant_id)
        if hist is None:
            raise InsufficientData(
                "No past holdout data for this merchant to calibrate the base order rate; "
                "supply simulation.base_rate_per_customer explicitly."
            )
        base_rate, base_source = hist, f"past_synthetic_holdouts(n={hist_n})"
    expected = economics.scenario("expected")
    if sim.true_lift_per_treated_customer is not None:
        lift, lift_source = float(sim.true_lift_per_treated_customer), "request"
    else:
        lift, lift_source = float(expected.incremental_orders) / n_t, "version_expected_scenario"
    if sim.base_redeem_share is not None:
        redeem, redeem_source = float(sim.base_redeem_share), "request"
    else:
        denom = n_t * base_rate
        redeem = min(1.0, float(expected.existing_discounted_orders) / denom) if denom else 0.0
        redeem_source = "version_expected_scenario"
    params = SimulationParams(base_rate, lift, redeem, float(sim.missing_outcome_share))
    outcomes = simulate(assigned, params, seed)

    t_orders = [o.orders for o in outcomes if o.group == "treatment" and o.orders is not None]
    h_orders = [o.orders for o in outcomes if o.group == "holdout" and o.orders is not None]
    t_red = sum(o.redemptions or 0 for o in outcomes if o.group == "treatment")
    assert inp.average_order_value_inr is not None and inp.variable_cost_per_order_inr is not None
    assert inp.discount_inr is not None and inp.campaign_cost_inr is not None
    result = measure(
        treatment_orders=t_orders,
        holdout_orders=h_orders,
        treatment_redemptions=t_red,
        treatment_assigned=n_t,
        holdout_assigned=n_h,
        aov=inp.average_order_value_inr,
        vc=inp.variable_cost_per_order_inr,
        discount=inp.discount_inr,
        campaign_cost=inp.campaign_cost_inr,
    )

    # ----- persist (same transaction)
    execution = Execution(
        id=uuid.uuid4(),
        campaign_id=campaign.id,
        campaign_version_id=version.id,
        input_hash=version.input_hash,
        approval_id=approval.id,
        idempotency_key=key,
        mode="simulated",
        status="completed",
        seed=seed,
        holdout_pct=holdout_pct,
        eligible_count=len(assigned),
        treatment_count=n_t,
        holdout_count=n_h,
        eligibility={
            "rule": elig.rule,
            "eligible_before_cap": len(elig.customer_ids),
            "max_customers": sim.max_customers,
            "excluded": elig.excluded,
        },
        simulation={
            "model": "poisson_base_plus_lift (data/README.md campaign convention)",
            "campaign_days": CAMPAIGN_DAYS,
            "base_rate_per_customer": base_rate,
            "base_rate_source": base_source,
            "true_lift_per_treated_customer": lift,
            "lift_source": lift_source,
            "base_redeem_share": redeem,
            "redeem_share_source": redeem_source,
            "missing_outcome_share": float(sim.missing_outcome_share),
        },
        requested_by=body.requested_by,
        requested_at=requested_at,
    )
    session.add(execution)
    session.flush()
    session.add_all(
        Assignment(
            execution_id=execution.id,
            customer_id=a.customer_id,
            campaign_version_id=version.id,
            group=a.group,
            assignment_hash=a.assignment_hash,
        )
        for a in assigned
    )
    session.flush()
    session.add_all(
        Outcome(
            execution_id=execution.id,
            customer_id=o.customer_id,
            group=o.group,
            orders=o.orders,
            redemptions=o.redemptions,
            is_simulated=True,
        )
        for o in outcomes
    )
    session.add(
        Measurement(
            execution_id=execution.id,
            method=result["method"],
            effect_verdict=result["effect_verdict"],
            profit_verdict=result["profit_verdict"],
            is_simulated=True,
            result=result,
        )
    )
    campaign.execution_status = "simulated"
    campaign.updated_at = utcnow()
    common = dict(
        actor=body.requested_by,
        merchant_id=campaign.merchant_id,
        campaign_id=campaign.id,
        version_id=version.id,
    )
    _audit(
        session,
        "execution_completed",
        **common,  # type: ignore[arg-type]
        payload={
            "version": version.version,
            "experiment_id": str(execution.id),
            "mode": "simulated",
            "treatment": n_t,
            "holdout": n_h,
            "holdout_pct": holdout_pct,
            "execution_status": "simulated",
        },
    )
    _audit(
        session,
        "measurement_completed",
        **common,  # type: ignore[arg-type]
        payload={
            "experiment_id": str(execution.id),
            "method": result["method"],
            "effect_verdict": result["effect_verdict"],
            "profit_verdict": result["profit_verdict"],
            "is_simulated": True,
        },
    )
    session.flush()
    return execution.id


# ------------------------------------------------------------------------ reads


CAVEATS_ALWAYS = [
    "Outcomes are simulated from synthetic data and assumed parameters; they are not "
    "observations of real customers and do not show real merchant uplift.",
    "Welch's t interval is approximate for skewed order counts; it is reasonable with at "
    f"least {MIN_MEASURED_PER_ARM} measured customers per group, and verdicts are withheld "
    "below that.",
    "One pre-specified primary metric (orders per customer) is tested once; profit is "
    "derived from it. Repeated looks or extra metrics would need a multiple-comparison "
    "correction.",
    "Order value and variable cost are the merchant-entered settings used by the "
    "prediction; per-order values are not simulated.",
    "The profit interval reflects uncertainty in incremental orders only; redemptions are "
    "treated as fixed.",
]


def _experiment_out(
    session: Session, ex: Execution, repo_source: str, replayed: bool
) -> ExperimentOut:
    version = session.get(CampaignVersion, ex.campaign_version_id)
    approval = session.get(CampaignApproval, ex.approval_id)
    m = session.get(Measurement, ex.id)
    assert version is not None and approval is not None and m is not None
    r = m.result
    caveats = list(CAVEATS_ALWAYS)
    sim = ex.simulation
    if sim.get("lift_source") == "version_expected_scenario":
        caveats.insert(
            1,
            "The simulated true effect was taken from this version's own "
            "expected scenario, so agreement with the prediction is by "
            "construction: this tests the measurement pipeline, not the "
            "prediction.",
        )
    if r["treatment"]["missing_outcomes"] or r["holdout"]["missing_outcomes"]:
        caveats.append(
            "Some outcomes are missing; they are assumed missing at random and "
            "estimates are scaled to all treated customers."
        )
    return ExperimentOut(
        experiment_id=ex.id,
        campaign_id=ex.campaign_id,
        campaign_version=version.version,
        input_hash=ex.input_hash,
        idempotency_key=ex.idempotency_key,
        replayed=replayed,
        status="completed",
        mode="simulated",
        data_source=repo_source,
        requested_by=ex.requested_by,
        requested_at=ex.requested_at,
        completed_at=ex.completed_at,
        approval={
            "decision": approval.decision,
            "decided_by": approval.decided_by,
            "decided_at": approval.decided_at.isoformat(),
            "input_hash": approval.input_hash,
        },
        assignment={
            "method": "sha256_hash_order_exact_split",
            "seed": ex.seed,
            "holdout_pct": ex.holdout_pct,
            "eligible": ex.eligible_count,
            "treatment": ex.treatment_count,
            "holdout": ex.holdout_count,
            **ex.eligibility,
        },
        simulation={"label": "simulation ground truth (assumed, synthetic)", **sim},
        prediction=_prediction(session, version),
        observed={
            "label": "simulated observed outcomes",
            "treatment": r["treatment"],
            "holdout": r["holdout"],
        },
        incremental={
            "label": "estimated incremental effect (treatment vs holdout)",
            "method": r["method"],
            "confidence_level": r["confidence_level"],
            "primary_metric": r["primary_metric"],
            "assignment_proportion_treatment": r["assignment_proportion_treatment"],
            **r["effect"],
            "reasons": r["effect_reasons"],
        },
        measured_profit={
            "label": "measured incremental profit (from simulated outcomes)",
            **r["measured_profit"],
        },
        effect_verdict=r["effect_verdict"],
        profit_verdict=r["profit_verdict"],
        caveats=caveats,
    )


def get_experiment(
    session: Session, experiment_id: uuid.UUID, data_source: str, replayed: bool = False
) -> ExperimentOut:
    with session.begin():
        ex = session.get(Execution, experiment_id)
        if ex is None:
            raise ExperimentNotFound(f"No experiment {experiment_id}.")
        return _experiment_out(session, ex, data_source, replayed)


def experiments_for_campaign(
    session: Session, campaign_id: uuid.UUID, data_source: str
) -> list[ExperimentOut]:
    with session.begin():
        if session.get(Campaign, campaign_id) is None:
            raise CampaignNotFound(f"No campaign {campaign_id}.")
        rows = session.scalars(
            select(Execution)
            .where(Execution.campaign_id == campaign_id)
            .order_by(Execution.completed_at)
        ).all()
        return [_experiment_out(session, ex, data_source, False) for ex in rows]


def assignments_page(
    session: Session, experiment_id: uuid.UUID, group: str | None, limit: int, offset: int
) -> AssignmentPage:
    with session.begin():
        if session.get(Execution, experiment_id) is None:
            raise ExperimentNotFound(f"No experiment {experiment_id}.")
        q = (
            select(Assignment, Outcome)
            .join(
                Outcome,
                (Outcome.execution_id == Assignment.execution_id)
                & (Outcome.customer_id == Assignment.customer_id),
            )
            .where(Assignment.execution_id == experiment_id)
        )
        count_q = (
            select(func.count())
            .select_from(Assignment)
            .where(Assignment.execution_id == experiment_id)
        )
        if group:
            q = q.where(Assignment.group == group)
            count_q = count_q.where(Assignment.group == group)
        total = session.scalar(count_q) or 0
        rows = session.execute(q.order_by(Assignment.customer_id).limit(limit).offset(offset))
        return AssignmentPage(
            experiment_id=experiment_id,
            total=total,
            items=[
                AssignmentOut(
                    customer_id=a.customer_id,
                    group=a.group,
                    orders=o.orders,
                    redemptions=o.redemptions,
                )
                for a, o in rows
            ],
        )


def experiment_id_for_campaign(session: Session, campaign_id: uuid.UUID) -> uuid.UUID | None:
    return session.scalar(select(Execution.id).where(Execution.campaign_id == campaign_id))
