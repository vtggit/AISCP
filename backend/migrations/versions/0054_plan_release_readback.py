"""Add the Acumatica purchase-order readback to plan_releases.

``erp_status`` is the status the purchase order last carried in
Acumatica, as read back by the readback — ``Open``, ``Completed``,
``Closed``, ``Canceled`` or whatever the ERP answers — ``erp_lines``
the order's lines exactly as the ERP returned them (jsonb), and
``erp_checked_at`` the instant that status was read.  All three are
nullable: a release row has no readback until one happens, and a row
whose order the ERP no longer returns keeps its stored values (the
readback leaves a missing number unchanged).

The migration adds the three columns to ``plan_releases`` and drops
them in the downgrade.  It creates no database role, changes no table
ownership and grants no privilege: the application keeps using its
existing database user.

Revision ID: 0054_plan_release_readback
Revises: 0053_ddmrp_adjustment_proposals
Create Date: 2026-11-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0054_plan_release_readback"
down_revision = "0053_ddmrp_adjustment_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "plan_releases",
        sa.Column("erp_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "plan_releases",
        sa.Column(
            "erp_lines",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "plan_releases",
        sa.Column("erp_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("plan_releases", "erp_checked_at")
    op.drop_column("plan_releases", "erp_lines")
    op.drop_column("plan_releases", "erp_status")
