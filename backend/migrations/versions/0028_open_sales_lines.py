"""Add open_sales_lines: open sales-order lines by business key.

The column shapes and types mirror open_purchase_lines
(migration 0006), with supplier_code, promised_on and ordered_on
replaced by a single ship_on.

Revision ID: 0028_open_sales_lines
Revises: 0027_ddmrp_buffers
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0028_open_sales_lines"
down_revision = "0027_ddmrp_buffers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "open_sales_lines",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("business_key", sa.String(length=255), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("order_type", sa.String(length=16), nullable=False),
        sa.Column("open_units", postgresql.DOUBLE_PRECISION, nullable=False),
        sa.Column("ship_on", sa.Date(), nullable=True),
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
        "ix_open_sales_lines_item_site",
        "open_sales_lines",
        ["item_code", "site_code"],
    )


def downgrade() -> None:
    op.drop_index("ix_open_sales_lines_item_site", table_name="open_sales_lines")
    op.drop_table("open_sales_lines")
