"""Campaign routes.

- POST /campaigns/evaluate: stateless profit + policy evaluation (unchanged since M5;
  nothing is stored).
- Persisted campaigns (M6): create, list, read, immutable versions, merchant decisions
  bound to a version's input hash, and the append-only audit trail.

Inputs must be complete; no route fills in costs or assumptions. Nothing is executed.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.deps import EffectiveRepo
from app.db import get_session
from app.schemas.campaigns import (
    AuditEventOut,
    CampaignCreate,
    CampaignList,
    CampaignOut,
    DecisionCreate,
    VersionCreate,
    VersionOut,
)
from app.schemas.decision import ApprovalRecord, CampaignEconomics, CampaignInput, PolicyDecision
from app.services import campaign_store as store
from app.services.decision import evaluate_campaign, evaluate_policy

router = APIRouter(prefix="/campaigns", tags=["campaigns"])

RepoDep = EffectiveRepo
SessionDep = Annotated[Session, Depends(get_session)]


class EvaluateRequest(BaseModel):
    merchant_id: str
    campaign: CampaignInput
    approval: ApprovalRecord | None = None


class EvaluateResponse(BaseModel):
    merchant_id: str
    economics: CampaignEconomics
    policy: PolicyDecision
    data_source: str
    is_synthetic: bool
    execution_mode: str = "not_executed"


@router.post("/evaluate", response_model=EvaluateResponse)
def evaluate(body: EvaluateRequest, repo: RepoDep) -> EvaluateResponse:
    """Predicted economics and the policy decision for one campaign. Read-only."""
    policy = repo.merchant_policy(body.merchant_id)
    if policy is None:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "policy_missing",
                "message": "No policy is configured for this merchant; cannot evaluate.",
            },
        )
    economics = evaluate_campaign(body.campaign)
    decision = evaluate_policy(economics, body.campaign, policy, body.approval)
    return EvaluateResponse(
        merchant_id=body.merchant_id,
        economics=economics,
        policy=decision,
        data_source=repo.data_source,
        is_synthetic=repo.is_synthetic and body.campaign.is_synthetic,
    )


# --------------------------------------------------------------- persisted campaigns


def _no_raw_offer(campaign: CampaignInput) -> None:
    """Product-level offers get their basket figures from the order history, so they are
    created by offer runs and edited through /offer-versions, never sent in raw."""
    if campaign.promotion is not None:
        raise HTTPException(
            status_code=422,
            detail={
                "error_code": "use_offer_endpoints",
                "message": "Product, bundle and cart offers are created by an offer run and "
                "edited with POST /campaigns/{id}/offer-versions.",
            },
        )


@router.post("", response_model=CampaignOut, status_code=201)
def create_campaign(body: CampaignCreate, repo: RepoDep, session: SessionDep) -> CampaignOut:
    """Store a campaign as version 1 and evaluate it. Nothing is executed."""
    _no_raw_offer(body.campaign)
    campaign_id = store.create_campaign(session, repo, body)
    return store.get_campaign(session, campaign_id)


@router.get("", response_model=CampaignList)
def list_campaigns(
    session: SessionDep, merchant_id: Annotated[str | None, Query(max_length=32)] = None
) -> CampaignList:
    return store.list_campaigns(session, merchant_id)


@router.get("/{campaign_id}", response_model=CampaignOut)
def get_campaign(campaign_id: uuid.UUID, session: SessionDep) -> CampaignOut:
    return store.get_campaign(session, campaign_id)


@router.get("/{campaign_id}/versions", response_model=list[VersionOut])
def list_versions(campaign_id: uuid.UUID, session: SessionDep) -> list[VersionOut]:
    return store.list_versions(session, campaign_id)


@router.post("/{campaign_id}/versions", response_model=CampaignOut, status_code=201)
def create_version(
    campaign_id: uuid.UUID, body: VersionCreate, repo: RepoDep, session: SessionDep
) -> CampaignOut:
    """Edit = new immutable version, re-evaluated. Any earlier approval does not carry over."""
    _no_raw_offer(body.campaign)
    store.create_version(session, repo, campaign_id, body)
    return store.get_campaign(session, campaign_id)


@router.get("/{campaign_id}/versions/{version}", response_model=VersionOut)
def get_version(campaign_id: uuid.UUID, version: int, session: SessionDep) -> VersionOut:
    return store.get_version(session, campaign_id, version)


@router.post("/{campaign_id}/versions/{version}/decision", response_model=CampaignOut)
def decide(
    campaign_id: uuid.UUID,
    version: int,
    body: DecisionCreate,
    repo: RepoDep,
    session: SessionDep,
) -> CampaignOut:
    """Record the merchant's approval or rejection of one exact version.

    Refused (409, and audited) if the version is not current, the input_hash differs, the
    version was already decided, or, for approvals, any policy rule fails.
    """
    store.record_decision(session, repo, campaign_id, version, body)
    return store.get_campaign(session, campaign_id)


@router.get("/{campaign_id}/audit", response_model=list[AuditEventOut])
def audit(campaign_id: uuid.UUID, session: SessionDep) -> list[AuditEventOut]:
    """Append-only history for this campaign, oldest first."""
    return store.audit_events(session, campaign_id)
