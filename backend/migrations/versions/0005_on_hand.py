"""Add on_hand: current on-hand inventory by item and site.

Revision ID: 0005_on_hand
Revises: 0004_lead_time_observations
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_on_hand"
down_revision = "0004_lead_time_observations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "on_hand",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("quantity_on_hand", postgresql.DOUBLE_PRECISION, nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("item_code", "site_code"),
    )


def downgrade() -> None:
    op.drop_table("on_hand")
