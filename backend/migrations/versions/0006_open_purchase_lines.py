"""Add open_purchase_lines: open purchase-order lines by business key.

Revision ID: 0006_open_purchase_lines
Revises: 0005_on_hand
Create Date: 2026-10-06
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_open_purchase_lines"
down_revision = "0005_on_hand"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "open_purchase_lines",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("business_key", sa.String(length=255), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("supplier_code", sa.String(length=64), nullable=True),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("open_units", postgresql.DOUBLE_PRECISION, nullable=False),
        sa.Column("promised_on", sa.Date(), nullable=True),
        sa.Column("ordered_on", sa.Date(), nullable=True),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("business_key"),
    )
    op.create_index(
        "ix_open_purchase_lines_item_site",
        "open_purchase_lines",
        ["item_code", "site_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_open_purchase_lines_item_site", table_name="open_purchase_lines")
    op.drop_table("open_purchase_lines")
