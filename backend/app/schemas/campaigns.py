"""API models for persisted campaigns, versions, decisions and audit events."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.decision import CampaignEconomics, CampaignInput, PolicyDecision
from app.schemas.llm import CampaignType

ApprovalStatus = Literal["pending", "approved", "rejected"]

NOTICE = (
    "Synthetic prototype data. Profit figures are predictions. Approval records the "
    "merchant's decision only; nothing has been executed."
)
SIMULATED_NOTICE = (
    "Synthetic prototype data. The approved version was executed in SIMULATION only: no "
    "offer was sent and no real system was contacted. Predictions and simulated "
    "measurements are reported separately on the experiment."
)
ExecutionStatus = Literal["not_executed", "simulated"]


class CampaignCreate(BaseModel):
    merchant_id: str = Field(max_length=32)
    campaign: CampaignInput
    created_by: str = Field(default="merchant", min_length=1, max_length=80)
    change_note: str | None = Field(default=None, max_length=500)


class VersionCreate(BaseModel):
    """A full replacement of the campaign inputs. Creates a new immutable version."""

    campaign: CampaignInput
    created_by: str = Field(default="merchant", min_length=1, max_length=80)
    change_note: str | None = Field(default=None, max_length=500)


TARGET_VALUES = ("new_customers", "existing_customers", "all")


class PreferenceIn(BaseModel):
    """An explicit preference the merchant selects (never inferred from free text)."""

    key: Literal["target", "max_discount_inr", "avoid_campaign_type"]
    value: str = Field(max_length=40)

    @model_validator(mode="after")
    def _valid_value(self) -> PreferenceIn:
        if self.key == "target" and self.value not in TARGET_VALUES:
            raise ValueError(f"target must be one of {', '.join(TARGET_VALUES)}")
        if self.key == "max_discount_inr" and not (
            self.value.isdigit() and int(self.value) <= 100_000
        ):
            raise ValueError("max_discount_inr must be a whole number of rupees (0-100000)")
        if self.key == "avoid_campaign_type" and self.value not in {
            t.value for t in CampaignType if t is not CampaignType.NO_ACTION
        }:
            raise ValueError("avoid_campaign_type must be a campaign type other than no_action")
        return self


class DecisionCreate(BaseModel):
    decision: Literal["approved", "rejected"]
    input_hash: str = Field(
        pattern=r"^[0-9a-f]{64}$",
        description="Hash of the version the merchant reviewed; must match that version.",
    )
    decided_by: str = Field(default="merchant", min_length=1, max_length=80)
    reason: str | None = Field(default=None, max_length=500)
    preference: PreferenceIn | None = Field(
        default=None,
        description="Optional, rejections only: a preference the merchant explicitly sets. "
        "Free-text reasons are stored as history but never turned into preferences.",
    )

    @model_validator(mode="after")
    def _preference_only_on_rejection(self) -> DecisionCreate:
        if self.preference is not None and self.decision != "rejected":
            raise ValueError("a preference can only be recorded with a rejection")
        return self


class EvaluationOut(BaseModel):
    trigger: Literal["version_created", "decision_recorded"]
    evaluated_at: datetime
    policy_version: int
    result_kind: Literal["prediction"]
    economics: CampaignEconomics
    policy: PolicyDecision


class DecisionOut(BaseModel):
    decision: Literal["approved", "rejected"]
    input_hash: str
    decided_by: str
    reason: str | None
    decided_at: datetime


class VersionOut(BaseModel):
    version_id: uuid.UUID
    version: int
    is_current: bool
    input_hash: str
    inputs: CampaignInput
    assumption_source: str
    change_note: str | None
    created_by: str
    created_at: datetime
    evaluation: EvaluationOut = Field(description="Latest evaluation of this version.")
    decision: DecisionOut | None


class CampaignOut(BaseModel):
    campaign_id: uuid.UUID
    merchant_id: str
    campaign_type: str
    current_version: int
    approval_status: ApprovalStatus = Field(description="Decision on the current version.")
    earlier_version_was_approved: bool = Field(
        description="True if an older version was approved; that approval does not carry over."
    )
    permitted: bool
    executable: bool = Field(
        description="Permitted by policy and approved for the current version's exact inputs."
    )
    execution_status: ExecutionStatus
    experiment_id: uuid.UUID | None = Field(
        default=None, description="Simulated experiment, if this campaign was executed."
    )
    result_kind: Literal["prediction"] = "prediction"
    data_source: str
    is_synthetic: bool
    created_at: datetime
    updated_at: datetime
    current: VersionOut
    notice: str = NOTICE


class CampaignSummary(BaseModel):
    campaign_id: uuid.UUID
    merchant_id: str
    campaign_type: str
    current_version: int
    execution_status: ExecutionStatus
    is_synthetic: bool
    updated_at: datetime


class CampaignList(BaseModel):
    campaigns: list[CampaignSummary]
    notice: str = NOTICE


class AuditEventOut(BaseModel):
    id: int
    occurred_at: datetime
    actor: str
    event_type: str
    merchant_id: str
    campaign_id: uuid.UUID | None
    campaign_version_id: uuid.UUID | None
    payload: dict[str, Any]
