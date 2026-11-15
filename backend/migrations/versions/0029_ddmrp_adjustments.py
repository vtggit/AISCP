"""Add ddmrp_adjustments: DDMRP adjustment factors by window.

A DDMRP adjustment factor scales the average daily usage of one
item-site (when item_code and site_code are both set) or of every
stored buffer (when both are null) between start_on and end_on
(inclusive).  The factor is stored per window: a factor of 0.5
halves the ADU, a factor of 1.5 raises it by half, and a pair (or
the all-buffer scope) never has two stored windows that share a
date — the API answers 409 for an overlapping window of the same
scope, so the zone rows read exactly one active factor per scope
per as-of date.

created_by stores the token subject and created_by_name its display
name (null when the caller has none), the way ddmrp_buffers records
designated_by and designated_by_name.

Revision ID: 0029_ddmrp_adjustments
Revises: 0028_open_sales_lines
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_ddmrp_adjustments"
down_revision = "0028_open_sales_lines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ddmrp_adjustments",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("item_code", sa.String(length=64), nullable=True),
        sa.Column("site_code", sa.String(length=64), nullable=True),
        sa.Column("start_on", sa.Date(), nullable=False),
        sa.Column("end_on", sa.Date(), nullable=False),
        sa.Column("factor", sa.Float(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_by_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(item_code IS NULL AND site_code IS NULL) "
            "OR (item_code IS NOT NULL AND site_code IS NOT NULL)",
            name="ck_ddmrp_adjustments_scope",
        ),
        sa.CheckConstraint("end_on >= start_on", name="ck_ddmrp_adjustments_window"),
        sa.CheckConstraint(
            "factor > 0 AND factor <= 10", name="ck_ddmrp_adjustments_factor"
        ),
    )


def downgrade() -> None:
    op.drop_table("ddmrp_adjustments")
