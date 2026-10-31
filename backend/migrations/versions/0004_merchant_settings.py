"""Merchant settings edited in the app: policy limits and usual campaign costs.

Adds the append-only merchant_settings table (one row per settings version) and lets
merchant_preferences rows come from the settings screen (source 'merchant_settings') as
well as from rejections. Existing rows are untouched; until a merchant saves settings,
the dataset's policy stays in force.

Downgrade refuses if any settings or settings-sourced preferences exist, instead of
silently dropping the merchant's choices.

Revision ID: 0004_merchant_settings
Revises: 0003_simulated_execution
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_merchant_settings"
down_revision: str | None = "0003_simulated_execution"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
NOW = sa.text("CURRENT_TIMESTAMP")
TABLE = "merchant_settings"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("merchant_id", sa.String(32), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("discount_cap_pct", sa.String(20), nullable=False),
        sa.Column("min_margin_pct", sa.String(20), nullable=False),
        sa.Column("max_loss_inr", sa.String(20), nullable=False),
        sa.Column("budget_inr", sa.String(20), nullable=False),
        sa.Column("holdout_pct", sa.Integer(), nullable=False),
        sa.Column("risk_scenario", sa.String(20), nullable=False),
        sa.Column("campaign_costs_inr", JSON, nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchant_settings")),
        sa.UniqueConstraint(
            "merchant_id",
            "policy_version",
            name=op.f("uq_merchant_settings_merchant_id_policy_version"),
        ),
        sa.CheckConstraint(
            "holdout_pct BETWEEN 5 AND 50", name=op.f("ck_merchant_settings_holdout_range")
        ),
        sa.CheckConstraint(
            "risk_scenario IN ('conservative', 'expected', 'optimistic')",
            name=op.f("ck_merchant_settings_risk_valid"),
        ),
        sa.CheckConstraint(
            "policy_version >= 1", name=op.f("ck_merchant_settings_version_positive")
        ),
    )
    op.create_index(op.f("ix_merchant_settings_merchant_id"), TABLE, ["merchant_id"])
    _source_check("'merchant_decision', 'merchant_settings'")
    _triggers(create=True)


def downgrade() -> None:
    bind = op.get_bind()
    saved = bind.execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar()
    prefs = bind.execute(
        sa.text("SELECT count(*) FROM merchant_preferences WHERE source = 'merchant_settings'")
    ).scalar()
    if saved or prefs:
        raise RuntimeError(
            f"{saved} saved setting version(s) and {prefs} settings preference(s) exist; "
            "refusing to downgrade and lose the merchant's choices."
        )
    _triggers(create=False)
    op.drop_index(op.f("ix_merchant_settings_merchant_id"), table_name=TABLE)
    op.drop_table(TABLE)
    _source_check("'merchant_decision'")


def _source_check(values: str) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "sqlite":
        # Rebuilding merchant_preferences would trip its append-only triggers' targets;
        # drop and recreate them around the batch operation.
        _pref_triggers(create=False)
    with op.batch_alter_table("merchant_preferences") as batch:
        batch.drop_constraint(op.f("ck_merchant_preferences_source_valid"), type_="check")
        batch.create_check_constraint(
            op.f("ck_merchant_preferences_source_valid"), f"source IN ({values})"
        )
    if dialect == "sqlite":
        _pref_triggers(create=True)


def _pref_triggers(*, create: bool) -> None:
    for action in ("UPDATE", "DELETE"):
        name = f"trg_merchant_preferences_no_{action.lower()}"
        op.execute(
            f"CREATE TRIGGER {name} BEFORE {action} ON merchant_preferences "
            "BEGIN SELECT RAISE(ABORT, 'merchant_preferences is append-only'); END"
            if create
            else f"DROP TRIGGER IF EXISTS {name}"
        )


def _triggers(*, create: bool) -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            f"CREATE TRIGGER trg_{TABLE}_append_only BEFORE UPDATE OR DELETE ON {TABLE} "
            "FOR EACH ROW EXECUTE FUNCTION nafapilot_forbid_mutation()"
            if create
            else f"DROP TRIGGER IF EXISTS trg_{TABLE}_append_only ON {TABLE}"
        )
    elif dialect == "sqlite":
        for action in ("UPDATE", "DELETE"):
            name = f"trg_{TABLE}_no_{action.lower()}"
            op.execute(
                f"CREATE TRIGGER {name} BEFORE {action} ON {TABLE} "
                f"BEGIN SELECT RAISE(ABORT, '{TABLE} is append-only'); END"
                if create
                else f"DROP TRIGGER IF EXISTS {name}"
            )
