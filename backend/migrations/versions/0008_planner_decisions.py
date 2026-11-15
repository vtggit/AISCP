"""Add planner_decisions: append-only planner decisions on drift rows.

A planner decision records what a human did about one item-site's current
reorder point drift row (accepted it, dismissed it with a reason, or
snoozed it).  Rows are append-only: every decision snapshots the drift
row it was made against (as_of, the recommended reorder point in whole
units, the current reorder point and the money at stake) together with
who decided and when, so earlier decisions are never changed or deleted
and the decision history of an item-site is the full sequence of rows.

Revision ID: 0008_planner_decisions
Revises: 0007_open_purchase_lines_checks
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_planner_decisions"
down_revision = "0007_open_purchase_lines_checks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "planner_decisions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("snooze_until", sa.Date(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("recommended_reorder_point_units", sa.Integer(), nullable=False),
        sa.Column("current_reorder_point", postgresql.DOUBLE_PRECISION, nullable=True),
        sa.Column("money_at_stake", postgresql.DOUBLE_PRECISION, nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('accepted', 'dismissed', 'snoozed')",
            name="ck_planner_decisions_decision",
        ),
    )
    op.create_index(
        "ix_planner_decisions_item_site_decided_at",
        "planner_decisions",
        ["item_code", "site_code", "decided_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_planner_decisions_item_site_decided_at", table_name="planner_decisions"
    )
    op.drop_table("planner_decisions")
