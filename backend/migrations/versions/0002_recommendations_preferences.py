"""Recommendation runs, explanations, merchant preferences; link campaigns to runs.

Adds three append-only tables and one nullable column on campaigns. Existing campaign
rows are kept (the new column is NULL for them). Downgrade removes the new tables, their
rows and the column; campaign rows themselves are preserved.

Revision ID: 0002_recommendations_preferences
Revises: 0001_campaign_persistence
Create Date: 2026-10-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_recommendations_preferences"
down_revision: str | None = "0001_campaign_persistence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NEW_APPEND_ONLY = ("merchant_preferences", "recommendation_runs", "explanations")

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
BIGINT_ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
NOW = sa.text("CURRENT_TIMESTAMP")


def _ts(name: str) -> sa.Column:  # type: ignore[type-arg]
    return sa.Column(name, sa.DateTime(timezone=True), server_default=NOW, nullable=False)


def upgrade() -> None:
    op.create_table(
        "recommendation_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("prompt_version", sa.String(20), nullable=False),
        sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("context", JSON, nullable=False),
        sa.Column("result", JSON, nullable=False),
        sa.Column("created_by", sa.String(80), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendation_runs")),
    )
    op.create_index(
        op.f("ix_recommendation_runs_merchant_id"), "recommendation_runs", ["merchant_id"]
    )

    op.create_table(
        "explanations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("recommendation_run_id", sa.Uuid(), nullable=False),
        sa.Column("lang", sa.String(10), nullable=False),
        sa.Column("question", sa.Text(), nullable=True),
        sa.Column("source", sa.String(10), nullable=False),
        sa.Column("provider", sa.String(20), nullable=False),
        sa.Column("model", sa.String(80), nullable=False),
        sa.Column("grounded", sa.Boolean(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("referenced_records", JSON, nullable=False),
        sa.Column("fallback_reason", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_explanations")),
        sa.ForeignKeyConstraint(
            ["recommendation_run_id"],
            ["recommendation_runs.id"],
            name=op.f("fk_explanations_recommendation_run_id_recommendation_runs"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("lang IN ('en', 'hinglish')", name=op.f("ck_explanations_lang_valid")),
        sa.CheckConstraint(
            "source IN ('llm', 'template')", name=op.f("ck_explanations_source_valid")
        ),
        sa.CheckConstraint(
            "source = 'template' OR grounded",
            name=op.f("ck_explanations_llm_text_must_be_grounded"),
        ),
    )
    op.create_index(
        op.f("ix_explanations_recommendation_run_id"), "explanations", ["recommendation_run_id"]
    )

    op.create_table(
        "merchant_preferences",
        sa.Column("id", BIGINT_ID, autoincrement=True, nullable=False),
        sa.Column("merchant_id", sa.String(32), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("value", sa.String(80), nullable=False),
        sa.Column("source", sa.String(30), nullable=False),
        sa.Column("campaign_version_id", sa.Uuid(), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchant_preferences")),
        sa.ForeignKeyConstraint(
            ["campaign_version_id"],
            ["campaign_versions.id"],
            name=op.f("fk_merchant_preferences_campaign_version_id_campaign_versions"),
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "key IN ('target', 'max_discount_inr', 'avoid_campaign_type')",
            name=op.f("ck_merchant_preferences_key_valid"),
        ),
        sa.CheckConstraint(
            "source IN ('merchant_decision')", name=op.f("ck_merchant_preferences_source_valid")
        ),
    )
    op.create_index(
        op.f("ix_merchant_preferences_merchant_id"), "merchant_preferences", ["merchant_id"]
    )

    with op.batch_alter_table("campaigns") as batch:
        batch.add_column(sa.Column("recommendation_run_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_campaigns_recommendation_run_id_recommendation_runs"),
            "recommendation_runs",
            ["recommendation_run_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch.create_index(op.f("ix_campaigns_recommendation_run_id"), ["recommendation_run_id"])

    _triggers(create=True)


def downgrade() -> None:
    _triggers(create=False)
    with op.batch_alter_table("campaigns") as batch:
        batch.drop_index(op.f("ix_campaigns_recommendation_run_id"))
        batch.drop_constraint(
            op.f("fk_campaigns_recommendation_run_id_recommendation_runs"), type_="foreignkey"
        )
        batch.drop_column("recommendation_run_id")
    op.drop_index(op.f("ix_merchant_preferences_merchant_id"), table_name="merchant_preferences")
    op.drop_table("merchant_preferences")
    op.drop_index(op.f("ix_explanations_recommendation_run_id"), table_name="explanations")
    op.drop_table("explanations")
    op.drop_index(op.f("ix_recommendation_runs_merchant_id"), table_name="recommendation_runs")
    op.drop_table("recommendation_runs")


def _triggers(*, create: bool) -> None:
    """Append-only triggers for the new tables. Reuses 0001's PostgreSQL function."""
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
