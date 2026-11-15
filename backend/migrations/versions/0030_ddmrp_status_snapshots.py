"""Add ddmrp_status_snapshots: the DDMRP plan status by capture date.

A DDMRP status snapshot stores, for one ``snapshot_on`` date and one
designated item-site, the plan row ``GET /api/ddmrp/plan`` built for
the pair on that date: its ``planning_zone`` ("red", "yellow",
"green" or "over"), its ``on_hand_status`` ("stocked_out",
"critical", "low" or "ok"), and the plan's ``net_flow``, ``on_hand``
and the three zone tops (``top_of_red``, ``top_of_yellow`` and
``top_of_green``), so ``GET /api/ddmrp/performance`` can answer how
often each buffer sat in each zone and on-hand status over the days
ending on the latest captured date, without re-running the plan.

One row per pair per date: the primary key is (``snapshot_on``,
``item_code``, ``site_code``), and the capture replaces that date's
rows wholesale (a second capture of a date never duplicates a pair,
and a buffer absent from the plan on a date is removed from it).

Revision ID: 0030_ddmrp_status_snapshots
Revises: 0029_ddmrp_adjustments
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_ddmrp_status_snapshots"
down_revision = "0029_ddmrp_adjustments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ddmrp_status_snapshots",
        sa.Column("snapshot_on", sa.Date(), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("planning_zone", sa.String(length=16), nullable=False),
        sa.Column("on_hand_status", sa.String(length=16), nullable=False),
        sa.Column("net_flow", sa.Float(), nullable=False),
        sa.Column("on_hand", sa.Float(), nullable=False),
        sa.Column("top_of_red", sa.Float(), nullable=False),
        sa.Column("top_of_yellow", sa.Float(), nullable=False),
        sa.Column("top_of_green", sa.Float(), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("snapshot_on", "item_code", "site_code"),
    )
    op.create_index(
        "ix_ddmrp_status_snapshots_item_site_snapshot_on",
        "ddmrp_status_snapshots",
        ["item_code", "site_code", "snapshot_on"],
    )


def downgrade() -> None:
    op.drop_table("ddmrp_status_snapshots")
