"""Add lead_time_observations: observed purchase-order receipt lead times.

Revision ID: 0004_lead_time_observations
Revises: 0003_demand_events
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0004_lead_time_observations"
down_revision = "0003_demand_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "lead_time_observations",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("business_key", sa.String(length=128), nullable=False),
        sa.Column("supplier_code", sa.String(length=64), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=True),
        sa.Column("ordered_on", sa.Date(), nullable=False),
        sa.Column("received_on", sa.Date(), nullable=False),
        sa.Column(
            "lead_time_days",
            sa.Integer(),
            server_default=sa.Computed("received_on - ordered_on"),
            nullable=False,
        ),
        sa.Column("source_system", sa.String(length=64), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint("received_on >= ordered_on"),
        sa.UniqueConstraint("business_key"),
    )
    op.create_index(
        "ix_lead_time_observations_supplier_item",
        "lead_time_observations",
        ["supplier_code", "item_code"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_lead_time_observations_supplier_item", table_name="lead_time_observations"
    )
    op.drop_table("lead_time_observations")
