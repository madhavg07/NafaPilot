"""Persistence for campaigns, immutable versions, evaluations, decisions and audit events.

Mutability:
- campaigns: one mutable row per campaign (pointer to the current version, timestamps).
- campaign_versions, campaign_evaluations, campaign_approvals, audit_events: append-only.
  Enforced three ways: no update/delete code paths, an ORM guard (below), and database
  triggers created by the migration (PostgreSQL and SQLite).

Merchants still come from the dataset repository (CSV), so merchant_id is an indexed
column validated by the API layer, not a foreign key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from app.db.base import Base, BigIntId, JsonType

APPEND_ONLY_TABLES = (
    "campaign_versions",
    "campaign_evaluations",
    "campaign_approvals",
    "audit_events",
    "merchant_preferences",
    "recommendation_runs",
    "explanations",
    "executions",
    "assignments",
    "outcomes",
    "measurements",
    "merchant_settings",
    "merchant_product_selections",
)


def utcnow() -> datetime:
    return datetime.now(UTC)


def _ts(**kw: Any) -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, server_default=func.now(), **kw)


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(32), index=True)
    campaign_type: Mapped[str] = mapped_column(String(40))
    current_version: Mapped[int] = mapped_column(Integer)
    # 'not_executed' until a simulated execution completes (M8). Never a real execution.
    execution_status: Mapped[str] = mapped_column(String(20), default="not_executed")
    data_source: Mapped[str] = mapped_column(String(40))
    is_synthetic: Mapped[bool] = mapped_column(Boolean)
    created_by: Mapped[str] = mapped_column(String(80))
    # Set when the campaign came from an LLM recommendation run (M7); null if entered.
    recommendation_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        CheckConstraint("current_version >= 1", name="current_version_positive"),
        CheckConstraint(
            "execution_status IN ('not_executed', 'simulated')", name="execution_status_valid"
        ),
    )


class CampaignVersion(Base):
    __tablename__ = "campaign_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="RESTRICT"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    input_hash: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[dict[str, Any]] = mapped_column(JsonType)
    assumption_source: Mapped[str] = mapped_column(String(30))
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("campaign_id", "version"),
        # Target for the approvals composite FK: an approval's hash must be this version's.
        UniqueConstraint("id", "input_hash"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("length(input_hash) = 64", name="input_hash_length"),
    )


class CampaignEvaluation(Base):
    """Profit + policy results for a version. At most one per trigger per version."""

    __tablename__ = "campaign_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_versions.id", ondelete="RESTRICT"), index=True
    )
    trigger: Mapped[str] = mapped_column(String(30))
    policy_version: Mapped[int] = mapped_column(Integer)
    economics_status: Mapped[str] = mapped_column(String(20))
    permitted: Mapped[bool] = mapped_column(Boolean)
    executable: Mapped[bool] = mapped_column(Boolean)
    result_kind: Mapped[str] = mapped_column(String(20), default="prediction")
    economics: Mapped[dict[str, Any]] = mapped_column(JsonType)
    policy_decision: Mapped[dict[str, Any]] = mapped_column(JsonType)
    evaluated_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("campaign_version_id", "trigger"),
        CheckConstraint(
            "trigger IN ('version_created', 'decision_recorded')", name="trigger_valid"
        ),
        CheckConstraint("result_kind = 'prediction'", name="result_kind_prediction"),
        CheckConstraint("NOT executable OR permitted", name="executable_requires_permitted"),
    )


class CampaignApproval(Base):
    """A merchant's decision on one exact version. One decision per version."""

    __tablename__ = "campaign_approvals"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)  # unique below
    input_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(10))
    decided_by: Mapped[str] = mapped_column(String(80))
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    decided_at: Mapped[datetime] = _ts()

    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_version_id", "input_hash"],
            ["campaign_versions.id", "campaign_versions.input_hash"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("campaign_version_id"),
        CheckConstraint("decision IN ('approved', 'rejected')", name="decision_valid"),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    occurred_at: Mapped[datetime] = _ts()
    actor: Mapped[str] = mapped_column(String(80))
    event_type: Mapped[str] = mapped_column(String(40), index=True)
    merchant_id: Mapped[str] = mapped_column(String(32), index=True)
    campaign_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaigns.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    campaign_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_versions.id", ondelete="RESTRICT"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)


PREFERENCE_KEYS = ("target", "max_discount_inr", "avoid_campaign_type")


class MerchantPreference(Base):
    """A preference the merchant stated explicitly (M7). Latest row per key wins.

    Only recorded from an explicit, structured merchant choice; never inferred from
    free-text reasons or from LLM output.
    """

    __tablename__ = "merchant_preferences"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(String(32), index=True)
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[str] = mapped_column(String(80))
    source: Mapped[str] = mapped_column(String(30))
    campaign_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("campaign_versions.id", ondelete="RESTRICT"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        CheckConstraint(
            "key IN ('target', 'max_discount_inr', 'avoid_campaign_type')", name="key_valid"
        ),
        CheckConstraint(
            "source IN ('merchant_decision', 'merchant_settings')", name="source_valid"
        ),
    )


class RecommendationRun(Base):
    """One LLM candidate-generation run and the deterministic selection made from it."""

    __tablename__ = "recommendation_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    merchant_id: Mapped[str] = mapped_column(String(32), index=True)
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(20))
    context_hash: Mapped[str] = mapped_column(String(64))
    context: Mapped[dict[str, Any]] = mapped_column(JsonType)
    result: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = _ts()


class Explanation(Base):
    """A stored explanation of a run, with its grounding check outcome."""

    __tablename__ = "explanations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    recommendation_run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("recommendation_runs.id", ondelete="RESTRICT"), index=True
    )
    lang: Mapped[str] = mapped_column(String(10))
    question: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(10))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(80))
    grounded: Mapped[bool] = mapped_column(Boolean)
    text: Mapped[str] = mapped_column(Text)
    referenced_records: Mapped[list[str]] = mapped_column(JsonType)
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        CheckConstraint("lang IN ('en', 'hinglish')", name="lang_valid"),
        CheckConstraint("source IN ('llm', 'template')", name="source_valid"),
        CheckConstraint("source = 'template' OR grounded", name="llm_text_must_be_grounded"),
    )


class Execution(Base):
    """A completed simulated execution (= experiment) of one approved version.

    Rows exist only for completed executions: the whole execution, assignment, outcome
    and measurement write is one transaction, so a failure leaves no row (it is
    recorded as an execution_failed audit event instead).
    """

    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    campaign_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaigns.id", ondelete="RESTRICT"), index=True
    )
    campaign_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    input_hash: Mapped[str] = mapped_column(String(64))
    approval_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("campaign_approvals.id", ondelete="RESTRICT")
    )
    idempotency_key: Mapped[str] = mapped_column(String(100))
    mode: Mapped[str] = mapped_column(String(20), default="simulated")
    status: Mapped[str] = mapped_column(String(20), default="completed")
    seed: Mapped[str] = mapped_column(String(64))
    holdout_pct: Mapped[int] = mapped_column(Integer)
    eligible_count: Mapped[int] = mapped_column(Integer)
    treatment_count: Mapped[int] = mapped_column(Integer)
    holdout_count: Mapped[int] = mapped_column(Integer)
    eligibility: Mapped[dict[str, Any]] = mapped_column(JsonType)
    simulation: Mapped[dict[str, Any]] = mapped_column(JsonType)
    requested_by: Mapped[str] = mapped_column(String(80))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime] = _ts()

    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_version_id", "input_hash"],
            ["campaign_versions.id", "campaign_versions.input_hash"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("campaign_version_id"),  # one execution per version, ever
        UniqueConstraint("idempotency_key"),
        UniqueConstraint("id", "campaign_version_id"),  # target for assignment FK
        CheckConstraint("mode = 'simulated'", name="mode_simulated"),
        CheckConstraint("status = 'completed'", name="status_completed"),
        CheckConstraint("holdout_pct BETWEEN 1 AND 99", name="holdout_pct_range"),
        CheckConstraint(
            "treatment_count + holdout_count = eligible_count", name="counts_consistent"
        ),
    )


class Assignment(Base):
    """Group membership of one customer in one experiment. Never changes."""

    __tablename__ = "assignments"

    execution_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    campaign_version_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    group: Mapped[str] = mapped_column("group", String(10))
    assignment_hash: Mapped[str] = mapped_column(String(64))

    __table_args__ = (
        ForeignKeyConstraint(
            ["execution_id", "campaign_version_id"],
            ["executions.id", "executions.campaign_version_id"],
            ondelete="RESTRICT",
        ),
        UniqueConstraint("execution_id", "customer_id", "group"),  # target for outcome FK
        CheckConstraint("\"group\" IN ('treatment', 'holdout')", name="group_valid"),
    )


class Outcome(Base):
    """Simulated observed outcome for one assigned customer. NULL counts = not measured."""

    __tablename__ = "outcomes"

    execution_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    group: Mapped[str] = mapped_column("group", String(10))
    orders: Mapped[int | None] = mapped_column(Integer, nullable=True)
    redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)

    __table_args__ = (
        ForeignKeyConstraint(
            ["execution_id", "customer_id", "group"],
            ["assignments.execution_id", "assignments.customer_id", "assignments.group"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("is_simulated", name="simulated_only"),
        CheckConstraint("(orders IS NULL) = (redemptions IS NULL)", name="measured_together"),
        CheckConstraint("orders IS NULL OR orders >= 0", name="orders_non_negative"),
        CheckConstraint(
            "redemptions IS NULL OR (redemptions >= 0 AND redemptions <= orders)",
            name="redemptions_valid",
        ),
        # Holdout customers never receive the offer, so they cannot redeem it.
        CheckConstraint(
            "\"group\" = 'treatment' OR redemptions IS NULL OR redemptions = 0",
            name="holdout_never_redeems",
        ),
    )


class Measurement(Base):
    """Uplift estimate, interval and measured profit for one execution."""

    __tablename__ = "measurements"

    execution_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("executions.id", ondelete="RESTRICT"), primary_key=True
    )
    method: Mapped[str] = mapped_column(String(40))
    effect_verdict: Mapped[str] = mapped_column(String(20))
    profit_verdict: Mapped[str] = mapped_column(String(20))
    is_simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    result: Mapped[dict[str, Any]] = mapped_column(JsonType)
    measured_at: Mapped[datetime] = _ts()

    __table_args__ = (
        CheckConstraint("is_simulated", name="simulated_only"),
        CheckConstraint(
            "effect_verdict IN ('positive', 'negative', 'inconclusive')", name="effect_valid"
        ),
        CheckConstraint(
            "profit_verdict IN ('profitable', 'loss', 'inconclusive')", name="profit_valid"
        ),
    )


class MerchantSetting(Base):
    """A version of a merchant's own settings, edited in the app. Latest version wins.

    Holds the policy limits (same meaning as the dataset's merchant_policies) and the
    merchant's usual campaign costs. Until the first row exists, the dataset's policy is
    the default. Append-only: every change is a new version, so older evaluations keep
    the policy_version they were checked against.
    """

    __tablename__ = "merchant_settings"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(String(32), index=True)
    policy_version: Mapped[int] = mapped_column(Integer)
    discount_cap_pct: Mapped[str] = mapped_column(String(20))
    min_margin_pct: Mapped[str] = mapped_column(String(20))
    max_loss_inr: Mapped[str] = mapped_column(String(20))
    budget_inr: Mapped[str] = mapped_column(String(20))
    holdout_pct: Mapped[int] = mapped_column(Integer)
    risk_scenario: Mapped[str] = mapped_column(String(20))
    campaign_costs_inr: Mapped[dict[str, Any]] = mapped_column(JsonType)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("merchant_id", "policy_version"),
        CheckConstraint("holdout_pct BETWEEN 5 AND 50", name="holdout_range"),
        CheckConstraint(
            "risk_scenario IN ('conservative', 'expected', 'optimistic')", name="risk_valid"
        ),
        CheckConstraint("policy_version >= 1", name="version_positive"),
    )


class MerchantProductSelection(Base):
    """A version of the products a merchant allows in offers. Latest version wins.

    Until the first row exists every product is allowed. The AI can only propose offers
    on products in the latest selection; this is enforced in code. Append-only.
    """

    __tablename__ = "merchant_product_selections"

    id: Mapped[int] = mapped_column(BigIntId, primary_key=True, autoincrement=True)
    merchant_id: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer)
    skus: Mapped[list[str]] = mapped_column(JsonType)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_by: Mapped[str] = mapped_column(String(80))
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("merchant_id", "version"),
        CheckConstraint("version >= 1", name="version_positive"),
    )


_IMMUTABLE = (
    MerchantProductSelection,
    CampaignVersion,
    CampaignEvaluation,
    CampaignApproval,
    AuditEvent,
    MerchantPreference,
    RecommendationRun,
    Explanation,
    Execution,
    Assignment,
    Outcome,
    Measurement,
    MerchantSetting,
)


class ImmutableRecordError(Exception):
    pass


@event.listens_for(Session, "before_flush")
def _forbid_mutation(session: Session, _ctx: object, _instances: object) -> None:
    """ORM-level guard; the database triggers are the backstop for raw SQL."""
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, _IMMUTABLE) and (
            obj in session.deleted or session.is_modified(obj, include_collections=False)
        ):
            raise ImmutableRecordError(f"{type(obj).__tablename__} rows are append-only")
