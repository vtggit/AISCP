"""Add demand_events: observed demand occurrences per item and site.

Revision ID: 0003_demand_events
Revises: 0002_item_sites
Create Date: 2026-10-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_demand_events"
down_revision = "0002_item_sites"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demand_events",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("business_key", sa.String(length=128), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("quantity", sa.Double(), nullable=False),
        sa.Column("occurred_on", sa.Date(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.CheckConstraint("quantity > 0"),
        sa.CheckConstraint(
            "event_type IN ('shipment', 'production_issue', 'kit_issue')"
        ),
        sa.UniqueConstraint("business_key"),
    )
    op.create_index(
        "ix_demand_events_item_site_occurred",
        "demand_events",
        ["item_code", "site_code", "occurred_on"],
    )


def downgrade() -> None:
    op.drop_index("ix_demand_events_item_site_occurred", table_name="demand_events")
    op.drop_table("demand_events")
