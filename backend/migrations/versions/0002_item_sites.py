"""Add item_sites: per-item, per-site planning parameters.

Revision ID: 0002_item_sites
Revises: 0001_initial_schema
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_item_sites"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "item_sites",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("unit_cost", sa.Double(), nullable=True),
        sa.Column("reorder_point", sa.Double(), nullable=True),
        sa.Column("safety_stock", sa.Double(), nullable=True),
        sa.Column("max_qty", sa.Double(), nullable=True),
        sa.Column("order_qty", sa.Double(), nullable=True),
        sa.Column("master_lead_time_days", sa.Double(), nullable=True),
        sa.Column("source_system", sa.String(length=64), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("item_code", "site_code"),
        sa.CheckConstraint("unit_cost >= 0"),
        sa.CheckConstraint("master_lead_time_days >= 0"),
    )


def downgrade() -> None:
    op.drop_table("item_sites")
