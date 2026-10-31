"""Campaign persistence: campaigns, immutable versions, evaluations, approvals, audit.

Creates new tables only; no existing table or data is touched. Downgrade drops these
tables (and their rows) and the append-only triggers.

Revision ID: 0001_campaign_persistence
Revises:
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_campaign_persistence"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPEND_ONLY_TABLES = (
    "campaign_versions",
    "campaign_evaluations",
    "campaign_approvals",
    "audit_events",
)

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGINT_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
NOW = sa.text("CURRENT_TIMESTAMP")


def _ts(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=False)


def upgrade() -> None:
    op.create_table(
        "campaigns",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.String(32), nullable=False),
        sa.Column("campaign_type", sa.String(40), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("execution_status", sa.String(20), nullable=False),
        sa.Column("data_source", sa.String(40), nullable=False),
        sa.Column("is_synthetic", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(80), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaigns")),
        sa.CheckConstraint(
            "current_version >= 1", name=op.f("ck_campaigns_current_version_positive")
        ),
        sa.CheckConstraint(
            "execution_status IN ('not_executed')",
            name=op.f("ck_campaigns_execution_status_valid"),
        ),
    )
    op.create_index(op.f("ix_campaigns_merchant_id"), "campaigns", ["merchant_id"])

    op.create_table(
        "campaign_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("inputs", JSON, nullable=False),
        sa.Column("assumption_source", sa.String(30), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_versions")),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_campaign_versions_campaign_id_campaigns"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "campaign_id", "version", name=op.f("uq_campaign_versions_campaign_id_version")
        ),
        sa.UniqueConstraint("id", "input_hash", name=op.f("uq_campaign_versions_id_input_hash")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_campaign_versions_version_positive")),
        sa.CheckConstraint(
            "length(input_hash) = 64", name=op.f("ck_campaign_versions_input_hash_length")
        ),
    )
    op.create_index(op.f("ix_campaign_versions_campaign_id"), "campaign_versions", ["campaign_id"])

    op.create_table(
        "campaign_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_version_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(30), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("economics_status", sa.String(20), nullable=False),
        sa.Column("permitted", sa.Boolean(), nullable=False),
        sa.Column("executable", sa.Boolean(), nullable=False),
        sa.Column("result_kind", sa.String(20), nullable=False),
        sa.Column("economics", JSON, nullable=False),
        sa.Column("policy_decision", JSON, nullable=False),
        _ts("evaluated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_evaluations")),
        sa.ForeignKeyConstraint(
            ["campaign_version_id"],
            ["campaign_versions.id"],
            name=op.f("fk_campaign_evaluations_campaign_version_id_campaign_versions"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "campaign_version_id",
            "trigger",
            name=op.f("uq_campaign_evaluations_campaign_version_id_trigger"),
        ),
        sa.CheckConstraint(
            "trigger IN ('version_created', 'decision_recorded')",
            name=op.f("ck_campaign_evaluations_trigger_valid"),
        ),
        sa.CheckConstraint(
            "result_kind = 'prediction'",
            name=op.f("ck_campaign_evaluations_result_kind_prediction"),
        ),
        sa.CheckConstraint(
            "NOT executable OR permitted",
            name=op.f("ck_campaign_evaluations_executable_requires_permitted"),
        ),
    )
    op.create_index(
        op.f("ix_campaign_evaluations_campaign_version_id"),
        "campaign_evaluations",
        ["campaign_version_id"],
    )

    op.create_table(
        "campaign_approvals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_version_id", sa.Uuid(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(10), nullable=False),
        sa.Column("decided_by", sa.String(80), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        _ts("decided_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_campaign_approvals")),
        sa.ForeignKeyConstraint(
            ["campaign_version_id", "input_hash"],
            ["campaign_versions.id", "campaign_versions.input_hash"],
            name=op.f("fk_campaign_approvals_campaign_version_id_input_hash_campaign_versions"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "campaign_version_id", name=op.f("uq_campaign_approvals_campaign_version_id")
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'rejected')",
            name=op.f("ck_campaign_approvals_decision_valid"),
        ),
    )

    op.create_table(
        "audit_events",
        sa.Column("id", BIGINT_ID, autoincrement=True, nullable=False),
        _ts("occurred_at"),
        sa.Column("actor", sa.String(80), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("merchant_id", sa.String(32), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=True),
        sa.Column("campaign_version_id", sa.Uuid(), nullable=True),
        sa.Column("payload", JSON, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_events")),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_audit_events_campaign_id_campaigns"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_version_id"],
            ["campaign_versions.id"],
            name=op.f("fk_audit_events_campaign_version_id_campaign_versions"),
            ondelete="RESTRICT",
        ),
    )
    op.create_index(op.f("ix_audit_events_event_type"), "audit_events", ["event_type"])
    op.create_index(op.f("ix_audit_events_merchant_id"), "audit_events", ["merchant_id"])
    op.create_index(op.f("ix_audit_events_campaign_id"), "audit_events", ["campaign_id"])

    _create_append_only_triggers()


def downgrade() -> None:
    _drop_append_only_triggers()
    op.drop_index(op.f("ix_audit_events_campaign_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_merchant_id"), table_name="audit_events")
    op.drop_index(op.f("ix_audit_events_event_type"), table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("campaign_approvals")
    op.drop_index(
        op.f("ix_campaign_evaluations_campaign_version_id"), table_name="campaign_evaluations"
    )
    op.drop_table("campaign_evaluations")
    op.drop_index(op.f("ix_campaign_versions_campaign_id"), table_name="campaign_versions")
    op.drop_table("campaign_versions")
    op.drop_index(op.f("ix_campaigns_merchant_id"), table_name="campaigns")
    op.drop_table("campaigns")


# ----------------------------------------------------------------- append-only triggers


def _create_append_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION nafapilot_forbid_mutation() RETURNS trigger
            LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only; % is not allowed', TG_TABLE_NAME, TG_OP;
            END;
            $$
            """
        )
        for table in APPEND_ONLY_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION nafapilot_forbid_mutation()"
            )
    elif dialect == "sqlite":
        for table in APPEND_ONLY_TABLES:
            for action in ("UPDATE", "DELETE"):
                op.execute(
                    f"CREATE TRIGGER trg_{table}_no_{action.lower()} BEFORE {action} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
                )


def _drop_append_only_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table in APPEND_ONLY_TABLES:
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS nafapilot_forbid_mutation()")
    elif dialect == "sqlite":
        for table in APPEND_ONLY_TABLES:
            for action in ("update", "delete"):
                op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_no_{action}")
