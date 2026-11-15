"""Add excess_decisions: append-only decisions on dead and excess stock rows.

An excess decision records what a human did about one item-site's current
dead-and-excess row (accepted it with a follow-up action, dismissed it
with a reason, or snoozed it until a date).  Rows are append-only: every
decision snapshots the ranked row it was made against (as_of, category,
dead_reason, on_hand_units, excess_units and the money tied up) together
with who decided and when, so earlier decisions are never changed or
deleted and the decision history of an item-site is the full sequence of
rows.

Revision ID: 0009_excess_decisions
Revises: 0008_planner_decisions
Create Date: 2026-10-08
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_excess_decisions"
down_revision = "0008_planner_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "excess_decisions",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("snooze_until", sa.Date(), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("dead_reason", sa.String(length=16), nullable=True),
        sa.Column("on_hand_units", postgresql.DOUBLE_PRECISION, nullable=False),
        sa.Column("excess_units", postgresql.DOUBLE_PRECISION, nullable=False),
        sa.Column("money_tied_up", postgresql.DOUBLE_PRECISION, nullable=True),
        sa.Column("decided_by", sa.String(length=255), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN ('accepted', 'dismissed', 'snoozed')",
            name="ck_excess_decisions_decision",
        ),
        sa.CheckConstraint(
            "action IS NULL OR action IN ('stop_replenishing', 'reduce', 'dispose')",
            name="ck_excess_decisions_action",
        ),
    )
    op.create_index(
        "ix_excess_decisions_item_site_decided_at",
        "excess_decisions",
        ["item_code", "site_code", "decided_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_excess_decisions_item_site_decided_at", table_name="excess_decisions"
    )
    op.drop_table("excess_decisions")
