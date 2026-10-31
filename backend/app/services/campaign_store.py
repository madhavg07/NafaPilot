"""Campaign persistence: create, version, decide, read, audit.

Rules enforced here (and backed by database constraints):
- Versions are immutable; an edit creates version n+1 with its own input hash.
- Every version is evaluated by the M5 profit and policy engines when it is created.
- An approval is stored against one version id and its input hash (composite FK). It
  applies to that version only; a later edit leaves the new version pending.
- Only the current version can be decided. An approval is refused, and the refusal is
  audited, if the submitted hash differs from the version's or if any deterministic
  policy rule fails. Approval never overrides policy.
- Approval does not execute anything; execution_status stays "not_executed".
- Each state change runs in one transaction: version, evaluation and audit events are
  written together or not at all.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol, TypedDict

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    AuditEvent,
    Campaign,
    CampaignApproval,
    CampaignEvaluation,
    CampaignVersion,
    Execution,
    MerchantPreference,
    utcnow,
)
from app.schemas.campaigns import (
    NOTICE,
    SIMULATED_NOTICE,
    AuditEventOut,
    CampaignCreate,
    CampaignList,
    CampaignOut,
    CampaignSummary,
    DecisionCreate,
    DecisionOut,
    EvaluationOut,
    VersionCreate,
    VersionOut,
)
from app.schemas.decision import (
    ApprovalRecord,
    CampaignEconomics,
    CampaignInput,
    PolicyConfig,
    PolicyDecision,
)
from app.services.decision import evaluate_campaign, evaluate_policy, input_hash


class _AuditCtx(TypedDict):
    actor: str
    merchant_id: str
    campaign_id: uuid.UUID | None


class MerchantSource(Protocol):
    data_source: str
    is_synthetic: bool

    def merchant_policy(self, merchant_id: str) -> PolicyConfig | None: ...


# ------------------------------------------------------------------------- errors


class CampaignError(Exception):
    http_status = 400
    error_code = "campaign_error"

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class CampaignNotFound(CampaignError):
    http_status, error_code = 404, "campaign_not_found"


class VersionNotFound(CampaignError):
    http_status, error_code = 404, "version_not_found"


class PolicyMissing(CampaignError):
    http_status, error_code = 422, "policy_missing"


class NoChange(CampaignError):
    http_status, error_code = 409, "no_change"


class StaleVersion(CampaignError):
    http_status, error_code = 409, "stale_version"


class InputHashMismatch(CampaignError):
    http_status, error_code = 409, "input_hash_mismatch"


class AlreadyDecided(CampaignError):
    http_status, error_code = 409, "already_decided"


class PolicyBlocked(CampaignError):
    http_status, error_code = 409, "policy_blocked"


class AlreadyExecuted(CampaignError):
    http_status, error_code = 409, "already_executed"


class ConcurrentChange(CampaignError):
    http_status, error_code = 409, "concurrent_change"


_REFUSALS = (StaleVersion, InputHashMismatch, AlreadyDecided, PolicyBlocked)


# ----------------------------------------------------------------------- helpers


def _policy(repo: MerchantSource, merchant_id: str) -> PolicyConfig:
    policy = repo.merchant_policy(merchant_id)  # raises UnknownMerchant
    if policy is None:
        raise PolicyMissing(f"No policy is configured for merchant {merchant_id}.")
    return policy


def _audit(
    session: Session,
    event_type: str,
    *,
    actor: str,
    merchant_id: str,
    campaign_id: uuid.UUID | None,
    version_id: uuid.UUID | None = None,
    payload: dict[str, Any],
) -> None:
    """Append one event. Payloads are built from explicit fields only (no request bodies,
    headers or settings), so secrets cannot leak into the audit log."""
    session.add(
        AuditEvent(
            actor=actor,
            event_type=event_type,
            merchant_id=merchant_id,
            campaign_id=campaign_id,
            campaign_version_id=version_id,
            payload=payload,
        )
    )


def _evaluate(
    session: Session,
    version: CampaignVersion,
    inp: CampaignInput,
    policy: PolicyConfig,
    trigger: str,
    approval: ApprovalRecord | None = None,
) -> tuple[CampaignEconomics, PolicyDecision]:
    economics = evaluate_campaign(inp)
    if economics.input_hash != version.input_hash:
        raise RuntimeError("stored inputs no longer hash to the version's input_hash")
    decision = evaluate_policy(economics, inp, policy, approval)
    session.add(
        CampaignEvaluation(
            campaign_version_id=version.id,
            trigger=trigger,
            policy_version=policy.policy_version,
            economics_status=economics.status,
            permitted=decision.permitted,
            executable=decision.executable,
            result_kind=economics.result_kind,
            economics=economics.model_dump(mode="json"),
            policy_decision=decision.model_dump(mode="json"),
        )
    )
    return economics, decision


def _add_version(
    session: Session,
    campaign: Campaign,
    number: int,
    inp: CampaignInput,
    actor: str,
    note: str | None,
) -> CampaignVersion:
    version = CampaignVersion(
        id=uuid.uuid4(),
        campaign_id=campaign.id,
        version=number,
        input_hash=input_hash(inp),
        inputs=inp.model_dump(mode="json"),
        assumption_source=inp.assumption_source,
        change_note=note,
        created_by=actor,
    )
    session.add(version)
    return version


def _evaluation_payload(decision: PolicyDecision, economics: CampaignEconomics) -> dict[str, Any]:
    return {
        "economics_status": economics.status,
        "permitted": decision.permitted,
        "executable": decision.executable,
        "blocked_by": decision.blocked_by,
        "policy_version": decision.policy_version,
        "risk_scenario": decision.risk_scenario,
    }


def _lock_campaign(session: Session, campaign_id: uuid.UUID) -> Campaign:
    campaign = session.scalar(select(Campaign).where(Campaign.id == campaign_id).with_for_update())
    if campaign is None:
        raise CampaignNotFound(f"No campaign {campaign_id}.")
    return campaign


def _version(session: Session, campaign_id: uuid.UUID, number: int) -> CampaignVersion:
    version = session.scalar(
        select(CampaignVersion).where(
            CampaignVersion.campaign_id == campaign_id, CampaignVersion.version == number
        )
    )
    if version is None:
        raise VersionNotFound(f"Campaign {campaign_id} has no version {number}.")
    return version


# ------------------------------------------------------------------------ writes


def create_campaign(session: Session, repo: MerchantSource, body: CampaignCreate) -> uuid.UUID:
    with session.begin():
        return create_campaign_in_tx(session, repo, body)


def create_campaign_in_tx(
    session: Session,
    repo: MerchantSource,
    body: CampaignCreate,
    *,
    recommendation_run_id: uuid.UUID | None = None,
    campaign_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Create + evaluate + audit inside the caller's open transaction."""
    policy = _policy(repo, body.merchant_id)
    campaign = Campaign(
        id=campaign_id or uuid.uuid4(),
        merchant_id=body.merchant_id,
        campaign_type=body.campaign.campaign_type,
        current_version=1,
        execution_status="not_executed",
        data_source=repo.data_source,
        is_synthetic=repo.is_synthetic and body.campaign.is_synthetic,
        created_by=body.created_by,
        recommendation_run_id=recommendation_run_id,
    )
    session.add(campaign)
    version = _add_version(session, campaign, 1, body.campaign, body.created_by, body.change_note)
    session.flush()
    economics, decision = _evaluate(session, version, body.campaign, policy, "version_created")
    common: _AuditCtx = dict(
        actor=body.created_by, merchant_id=campaign.merchant_id, campaign_id=campaign.id
    )
    _audit(
        session,
        "campaign_created",
        **common,
        payload={
            "campaign_type": campaign.campaign_type,
            "data_source": campaign.data_source,
            "is_synthetic": campaign.is_synthetic,
            "recommendation_run_id": (
                None if recommendation_run_id is None else str(recommendation_run_id)
            ),
        },
    )
    _audit(
        session,
        "version_created",
        **common,
        version_id=version.id,
        payload={
            "version": 1,
            "input_hash": version.input_hash,
            "assumption_source": version.assumption_source,
        },
    )
    _audit(
        session,
        "evaluation_recorded",
        **common,
        version_id=version.id,
        payload={
            "version": 1,
            "trigger": "version_created",
            **_evaluation_payload(decision, economics),
        },
    )
    return campaign.id


def create_version(
    session: Session, repo: MerchantSource, campaign_id: uuid.UUID, body: VersionCreate
) -> uuid.UUID:
    try:
        with session.begin():
            campaign = _lock_campaign(session, campaign_id)
            if campaign.execution_status != "not_executed":
                raise AlreadyExecuted(
                    "This campaign has already been executed (in simulation); its versions "
                    "are closed. Create a new campaign to try different inputs."
                )
            policy = _policy(repo, campaign.merchant_id)
            current = _version(session, campaign.id, campaign.current_version)
            new_hash = input_hash(body.campaign)
            if new_hash == current.input_hash:
                raise NoChange("The inputs are identical to the current version.")
            prior = session.scalar(
                select(CampaignApproval).where(CampaignApproval.campaign_version_id == current.id)
            )
            number = campaign.current_version + 1
            version = _add_version(
                session, campaign, number, body.campaign, body.created_by, body.change_note
            )
            campaign.current_version = number
            campaign.campaign_type = body.campaign.campaign_type
            campaign.updated_at = utcnow()
            session.flush()
            economics, decision = _evaluate(
                session, version, body.campaign, policy, "version_created"
            )
            common: _AuditCtx = dict(
                actor=body.created_by, merchant_id=campaign.merchant_id, campaign_id=campaign.id
            )
            if prior is not None and prior.decision == "approved":
                _audit(
                    session,
                    "approval_superseded",
                    **common,
                    version_id=current.id,
                    payload={
                        "version": current.version,
                        "reason": "A new version was created; the approval applies "
                        "only to the version it was given for.",
                    },
                )
            _audit(
                session,
                "version_created",
                **common,
                version_id=version.id,
                payload={
                    "version": number,
                    "previous_version": current.version,
                    "input_hash": version.input_hash,
                    "assumption_source": version.assumption_source,
                    "change_note": body.change_note,
                },
            )
            _audit(
                session,
                "evaluation_recorded",
                **common,
                version_id=version.id,
                payload={
                    "version": number,
                    "trigger": "version_created",
                    **_evaluation_payload(decision, economics),
                },
            )
            return campaign.id
    except IntegrityError as exc:  # another request created the same version number
        raise ConcurrentChange("The campaign changed concurrently; reload and retry.") from exc


def record_decision(
    session: Session,
    repo: MerchantSource,
    campaign_id: uuid.UUID,
    version_number: int,
    body: DecisionCreate,
) -> uuid.UUID:
    merchant_id: str | None = None
    version_id: uuid.UUID | None = None
    try:
        with session.begin():
            campaign = _lock_campaign(session, campaign_id)
            merchant_id = campaign.merchant_id
            version = _version(session, campaign.id, version_number)
            version_id = version.id
            if version.version != campaign.current_version:
                raise StaleVersion(
                    f"Version {version.version} is not current (current is "
                    f"{campaign.current_version}); only the current version can be decided."
                )
            if body.input_hash != version.input_hash:
                raise InputHashMismatch(
                    "The submitted input_hash does not match this version; the merchant may "
                    "have reviewed different inputs."
                )
            existing = session.scalar(
                select(CampaignApproval).where(CampaignApproval.campaign_version_id == version.id)
            )
            if existing is not None:
                raise AlreadyDecided(
                    f"Version {version.version} was already {existing.decision}. Create a new "
                    "version to change the campaign."
                )
            inp = CampaignInput.model_validate(version.inputs)
            common: _AuditCtx = dict(
                actor=body.decided_by, merchant_id=campaign.merchant_id, campaign_id=campaign.id
            )

            if body.decision == "approved":
                policy = _policy(repo, campaign.merchant_id)
                check = evaluate_policy(evaluate_campaign(inp), inp, policy)
                if not check.permitted:
                    failed = [
                        r for r in check.rules if not r.passed and r.blocks == "recommendation"
                    ]
                    raise PolicyBlocked(
                        "Approval refused: deterministic policy rules failed. Approval cannot "
                        "override policy.",
                        details={
                            "blocked_by": check.blocked_by,
                            "reasons": {r.rule_id: r.reason for r in failed},
                        },
                    )
                session.add(
                    CampaignApproval(
                        campaign_version_id=version.id,
                        input_hash=version.input_hash,
                        decision="approved",
                        decided_by=body.decided_by,
                        reason=body.reason,
                    )
                )
                session.flush()
                approval = ApprovalRecord(
                    approved_input_hash=version.input_hash, approved_by=body.decided_by
                )
                economics, decision = _evaluate(
                    session, version, inp, policy, "decision_recorded", approval
                )
                _audit(
                    session,
                    "approval_recorded",
                    **common,
                    version_id=version.id,
                    payload={
                        "version": version.version,
                        "input_hash": version.input_hash,
                        "reason": body.reason,
                        "executable": decision.executable,
                        "execution_status": campaign.execution_status,
                    },
                )
                _audit(
                    session,
                    "evaluation_recorded",
                    **common,
                    version_id=version.id,
                    payload={
                        "version": version.version,
                        "trigger": "decision_recorded",
                        **_evaluation_payload(decision, economics),
                    },
                )
            else:
                session.add(
                    CampaignApproval(
                        campaign_version_id=version.id,
                        input_hash=version.input_hash,
                        decision="rejected",
                        decided_by=body.decided_by,
                        reason=body.reason,
                    )
                )
                _audit(
                    session,
                    "rejection_recorded",
                    **common,
                    version_id=version.id,
                    payload={
                        "version": version.version,
                        "input_hash": version.input_hash,
                        "reason": body.reason,
                    },
                )
                if body.preference is not None:
                    session.add(
                        MerchantPreference(
                            merchant_id=campaign.merchant_id,
                            key=body.preference.key,
                            value=body.preference.value,
                            source="merchant_decision",
                            campaign_version_id=version.id,
                            created_by=body.decided_by,
                        )
                    )
                    _audit(
                        session,
                        "preference_recorded",
                        **common,
                        version_id=version.id,
                        payload={
                            "version": version.version,
                            "preference_key": body.preference.key,
                            "preference_value": body.preference.value,
                        },
                    )
            return campaign.id
    except _REFUSALS as err:
        # The decision transaction rolled back; record the refused attempt on its own.
        assert merchant_id is not None
        with session.begin():
            _audit(
                session,
                "decision_refused",
                actor=body.decided_by,
                merchant_id=merchant_id,
                campaign_id=campaign_id,
                version_id=version_id,
                payload={
                    "attempted_decision": body.decision,
                    "version": version_number,
                    "error_code": err.error_code,
                    **err.details,
                },
            )
        raise


# ------------------------------------------------------------------------- reads


def _version_out(session: Session, version: CampaignVersion, current: int) -> VersionOut:
    evaluations = {
        e.trigger: e
        for e in session.scalars(
            select(CampaignEvaluation).where(CampaignEvaluation.campaign_version_id == version.id)
        )
    }
    latest = evaluations.get("decision_recorded") or evaluations["version_created"]
    approval = session.scalar(
        select(CampaignApproval).where(CampaignApproval.campaign_version_id == version.id)
    )
    return VersionOut(
        version_id=version.id,
        version=version.version,
        is_current=version.version == current,
        input_hash=version.input_hash,
        inputs=CampaignInput.model_validate(version.inputs),
        assumption_source=version.assumption_source,
        change_note=version.change_note,
        created_by=version.created_by,
        created_at=version.created_at,
        evaluation=EvaluationOut(
            trigger=latest.trigger,
            evaluated_at=latest.evaluated_at,
            policy_version=latest.policy_version,
            result_kind=latest.result_kind,
            economics=CampaignEconomics.model_validate(latest.economics),
            policy=PolicyDecision.model_validate(latest.policy_decision),
        ),
        decision=(
            None
            if approval is None
            else DecisionOut(
                decision=approval.decision,
                input_hash=approval.input_hash,
                decided_by=approval.decided_by,
                reason=approval.reason,
                decided_at=approval.decided_at,
            )
        ),
    )


def _campaign(session: Session, campaign_id: uuid.UUID) -> Campaign:
    campaign = session.get(Campaign, campaign_id)
    if campaign is None:
        raise CampaignNotFound(f"No campaign {campaign_id}.")
    return campaign


def get_campaign(session: Session, campaign_id: uuid.UUID) -> CampaignOut:
    with session.begin():
        campaign = _campaign(session, campaign_id)
        current = _version_out(
            session,
            _version(session, campaign.id, campaign.current_version),
            campaign.current_version,
        )
        earlier_approved = session.scalar(
            select(CampaignApproval.id)
            .join(CampaignVersion, CampaignVersion.id == CampaignApproval.campaign_version_id)
            .where(
                CampaignVersion.campaign_id == campaign.id,
                CampaignVersion.version < campaign.current_version,
                CampaignApproval.decision == "approved",
            )
            .limit(1)
        )
        experiment_id = session.scalar(
            select(Execution.id).where(Execution.campaign_id == campaign.id)
        )
        return CampaignOut(
            notice=NOTICE if campaign.execution_status == "not_executed" else SIMULATED_NOTICE,
            campaign_id=campaign.id,
            merchant_id=campaign.merchant_id,
            campaign_type=campaign.campaign_type,
            current_version=campaign.current_version,
            approval_status=current.decision.decision if current.decision else "pending",
            earlier_version_was_approved=earlier_approved is not None,
            permitted=current.evaluation.policy.permitted,
            executable=current.evaluation.policy.executable,
            execution_status=campaign.execution_status,
            experiment_id=experiment_id,
            data_source=campaign.data_source,
            is_synthetic=campaign.is_synthetic,
            created_at=campaign.created_at,
            updated_at=campaign.updated_at,
            current=current,
        )


def list_campaigns(session: Session, merchant_id: str | None = None) -> CampaignList:
    with session.begin():
        query = select(Campaign).order_by(Campaign.created_at, Campaign.id)
        if merchant_id:
            query = query.where(Campaign.merchant_id == merchant_id)
        return CampaignList(
            campaigns=[
                CampaignSummary(
                    campaign_id=c.id,
                    merchant_id=c.merchant_id,
                    campaign_type=c.campaign_type,
                    current_version=c.current_version,
                    execution_status=c.execution_status,
                    is_synthetic=c.is_synthetic,
                    updated_at=c.updated_at,
                )
                for c in session.scalars(query)
            ]
        )


def list_versions(session: Session, campaign_id: uuid.UUID) -> list[VersionOut]:
    with session.begin():
        campaign = _campaign(session, campaign_id)
        versions = session.scalars(
            select(CampaignVersion)
            .where(CampaignVersion.campaign_id == campaign.id)
            .order_by(CampaignVersion.version)
        )
        return [_version_out(session, v, campaign.current_version) for v in versions]


def get_version(session: Session, campaign_id: uuid.UUID, number: int) -> VersionOut:
    with session.begin():
        campaign = _campaign(session, campaign_id)
        return _version_out(
            session, _version(session, campaign.id, number), campaign.current_version
        )


def audit_events(session: Session, campaign_id: uuid.UUID) -> list[AuditEventOut]:
    with session.begin():
        _campaign(session, campaign_id)
        rows = session.scalars(
            select(AuditEvent).where(AuditEvent.campaign_id == campaign_id).order_by(AuditEvent.id)
        )
        return [
            AuditEventOut(
                id=r.id,
                occurred_at=r.occurred_at,
                actor=r.actor,
                event_type=r.event_type,
                merchant_id=r.merchant_id,
                campaign_id=r.campaign_id,
                campaign_version_id=r.campaign_version_id,
                payload=r.payload,
            )
            for r in rows
        ]
