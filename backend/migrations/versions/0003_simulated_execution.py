"""Simulated execution: executions, assignments, outcomes, measurements.

Widens campaigns.execution_status to allow 'simulated'. Adds four append-only tables.
Existing rows in all earlier tables are untouched.

Downgrade drops the four tables (and their rows) and narrows the check back to
'not_executed'. It refuses if any campaign is marked 'simulated', instead of silently
rewriting that status.

Revision ID: 0003_simulated_execution
Revises: 0002_recommendations_preferences
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_simulated_execution"
down_revision: str | None = "0002_recommendations_preferences"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_APPEND_ONLY = ("executions", "assignments", "outcomes", "measurements")
JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
NOW = sa.text("CURRENT_TIMESTAMP")


def upgrade() -> None:
    with op.batch_alter_table("campaigns") as batch:
        batch.drop_constraint(op.f("ck_campaigns_execution_status_valid"), type_="check")
        batch.create_check_constraint(
            op.f("ck_campaigns_execution_status_valid"),
            "execution_status IN ('not_executed', 'simulated')",
        )

    op.create_table(
        "executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("campaign_id", sa.Uuid(), nullable=False),
        sa.Column("campaign_version_id", sa.Uuid(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(100), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("seed", sa.String(64), nullable=False),
        sa.Column("holdout_pct", sa.Integer(), nullable=False),
        sa.Column("eligible_count", sa.Integer(), nullable=False),
        sa.Column("treatment_count", sa.Integer(), nullable=False),
        sa.Column("holdout_count", sa.Integer(), nullable=False),
        sa.Column("eligibility", JSON, nullable=False),
        sa.Column("simulation", JSON, nullable=False),
        sa.Column("requested_by", sa.String(80), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_executions")),
        sa.ForeignKeyConstraint(
            ["campaign_id"],
            ["campaigns.id"],
            name=op.f("fk_executions_campaign_id_campaigns"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["campaign_version_id", "input_hash"],
            ["campaign_versions.id", "campaign_versions.input_hash"],
            name=op.f("fk_executions_campaign_version_id_input_hash_campaign_versions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["campaign_approvals.id"],
            name=op.f("fk_executions_approval_id_campaign_approvals"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("campaign_version_id", name=op.f("uq_executions_campaign_version_id")),
        sa.UniqueConstraint("idempotency_key", name=op.f("uq_executions_idempotency_key")),
        sa.UniqueConstraint(
            "id", "campaign_version_id", name=op.f("uq_executions_id_campaign_version_id")
        ),
        sa.CheckConstraint("mode = 'simulated'", name=op.f("ck_executions_mode_simulated")),
        sa.CheckConstraint("status = 'completed'", name=op.f("ck_executions_status_completed")),
        sa.CheckConstraint(
            "holdout_pct BETWEEN 1 AND 99", name=op.f("ck_executions_holdout_pct_range")
        ),
        sa.CheckConstraint(
            "treatment_count + holdout_count = eligible_count",
            name=op.f("ck_executions_counts_consistent"),
        ),
    )
    op.create_index(op.f("ix_executions_campaign_id"), "executions", ["campaign_id"])

    op.create_table(
        "assignments",
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.String(40), nullable=False),
        sa.Column("campaign_version_id", sa.Uuid(), nullable=False),
        sa.Column("group", sa.String(10), nullable=False),
        sa.Column("assignment_hash", sa.String(64), nullable=False),
        sa.PrimaryKeyConstraint("execution_id", "customer_id", name=op.f("pk_assignments")),
        sa.ForeignKeyConstraint(
            ["execution_id", "campaign_version_id"],
            ["executions.id", "executions.campaign_version_id"],
            name=op.f("fk_assignments_execution_id_campaign_version_id_executions"),
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "execution_id",
            "customer_id",
            "group",
            name=op.f("uq_assignments_execution_id_customer_id_group"),
        ),
        sa.CheckConstraint(
            "\"group\" IN ('treatment', 'holdout')", name=op.f("ck_assignments_group_valid")
        ),
    )

    op.create_table(
        "outcomes",
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("customer_id", sa.String(40), nullable=False),
        sa.Column("group", sa.String(10), nullable=False),
        sa.Column("orders", sa.Integer(), nullable=True),
        sa.Column("redemptions", sa.Integer(), nullable=True),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("execution_id", "customer_id", name=op.f("pk_outcomes")),
        sa.ForeignKeyConstraint(
            ["execution_id", "customer_id", "group"],
            ["assignments.execution_id", "assignments.customer_id", "assignments.group"],
            name=op.f("fk_outcomes_execution_id_customer_id_group_assignments"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("is_simulated", name=op.f("ck_outcomes_simulated_only")),
        sa.CheckConstraint(
            "(orders IS NULL) = (redemptions IS NULL)", name=op.f("ck_outcomes_measured_together")
        ),
        sa.CheckConstraint(
            "orders IS NULL OR orders >= 0", name=op.f("ck_outcomes_orders_non_negative")
        ),
        sa.CheckConstraint(
            "redemptions IS NULL OR (redemptions >= 0 AND redemptions <= orders)",
            name=op.f("ck_outcomes_redemptions_valid"),
        ),
        sa.CheckConstraint(
            "\"group\" = 'treatment' OR redemptions IS NULL OR redemptions = 0",
            name=op.f("ck_outcomes_holdout_never_redeems"),
        ),
    )

    op.create_table(
        "measurements",
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("method", sa.String(40), nullable=False),
        sa.Column("effect_verdict", sa.String(20), nullable=False),
        sa.Column("profit_verdict", sa.String(20), nullable=False),
        sa.Column("is_simulated", sa.Boolean(), nullable=False),
        sa.Column("result", JSON, nullable=False),
        sa.Column("measured_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("execution_id", name=op.f("pk_measurements")),
        sa.ForeignKeyConstraint(
            ["execution_id"],
            ["executions.id"],
            name=op.f("fk_measurements_execution_id_executions"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("is_simulated", name=op.f("ck_measurements_simulated_only")),
        sa.CheckConstraint(
            "effect_verdict IN ('positive', 'negative', 'inconclusive')",
            name=op.f("ck_measurements_effect_valid"),
        ),
        sa.CheckConstraint(
            "profit_verdict IN ('profitable', 'loss', 'inconclusive')",
            name=op.f("ck_measurements_profit_valid"),
        ),
    )
    _triggers(create=True)


def downgrade() -> None:
    bind = op.get_bind()
    executed = bind.execute(
        sa.text("SELECT count(*) FROM campaigns WHERE execution_status = 'simulated'")
    ).scalar()
    if executed:
        raise RuntimeError(
            f"{executed} campaign(s) have simulated executions; refusing to downgrade and "
            "lose their status. Export or remove that data deliberately first."
        )
    _triggers(create=False)
    op.drop_table("measurements")
    op.drop_table("outcomes")
    op.drop_table("assignments")
    op.drop_index(op.f("ix_executions_campaign_id"), table_name="executions")
    op.drop_table("executions")
    with op.batch_alter_table("campaigns") as batch:
        batch.drop_constraint(op.f("ck_campaigns_execution_status_valid"), type_="check")
        batch.create_check_constraint(
            op.f("ck_campaigns_execution_status_valid"), "execution_status IN ('not_executed')"
        )


def _triggers(*, create: bool) -> None:
    dialect = op.get_bind().dialect.name
    for table in NEW_APPEND_ONLY:
        if dialect == "postgresql":
            op.execute(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION nafapilot_forbid_mutation()"
                if create
                else f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}"
            )
        elif dialect == "sqlite":
            for action in ("UPDATE", "DELETE"):
                name = f"trg_{table}_no_{action.lower()}"
                op.execute(
                    f"CREATE TRIGGER {name} BEFORE {action} ON {table} "
                    f"BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END"
                    if create
                    else f"DROP TRIGGER IF EXISTS {name}"
                )
