"""Product-level promotions: the merchant's product selection.

Adds the append-only merchant_product_selections table (one row per version of the
products a merchant allows in offers). Product, bundle and cart offers are stored as
ordinary campaign versions (CampaignInput.promotion inside the JSON inputs), so no other
table changes. Downgrade refuses if any selection has been saved.

Revision ID: 0005_product_promotions
Revises: 0004_merchant_settings
Create Date: 2026-10-31
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_product_promotions"
down_revision: str | None = "0004_merchant_settings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
NOW = sa.text("CURRENT_TIMESTAMP")
TABLE = "merchant_product_selections"


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
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("skus", JSON, nullable=False),
        sa.Column("note", sa.String(300), nullable=True),
        sa.Column("created_by", sa.String(80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=NOW, nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_merchant_product_selections")),
        sa.UniqueConstraint(
            "merchant_id",
            "version",
            name=op.f("uq_merchant_product_selections_merchant_id_version"),
        ),
        sa.CheckConstraint(
            "version >= 1", name=op.f("ck_merchant_product_selections_version_positive")
        ),
    )
    op.create_index(op.f("ix_merchant_product_selections_merchant_id"), TABLE, ["merchant_id"])
    _triggers(create=True)


def downgrade() -> None:
    saved = op.get_bind().execute(sa.text(f"SELECT count(*) FROM {TABLE}")).scalar()
    if saved:
        raise RuntimeError(
            f"{saved} saved product selection(s) exist; refusing to downgrade and lose them."
        )
    _triggers(create=False)
    op.drop_index(op.f("ix_merchant_product_selections_merchant_id"), table_name=TABLE)
    op.drop_table(TABLE)


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
