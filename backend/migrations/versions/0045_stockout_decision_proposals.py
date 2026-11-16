"""Add stockout_decision_proposals: the remedy proposed with a stockout decision.

A stockout decision proposal stores the remedy the stockout screen
proposed for one item-site's ranked row at the moment a stockout
decision on that row was recorded (issue #1321: ``POST
/api/stockout/decisions`` stores the ``GET /api/stockout`` answer's
``remedy`` with the decision it was proposed for): one row per
proposal, ``decision_id`` (text not null, the id of the stockout
decision the remedy was proposed for, the primary key and a foreign
key to ``stockout_decisions(id)`` with ON DELETE CASCADE — a removed
decision removes its proposal, and one decision has at most one
proposal), ``proposed_remedy`` (jsonb not null, the proposed remedy —
the ``StockoutRemedy`` object in JSON, with every key and the dates
as ``YYYY-MM-DD`` strings, a CHECK constraint that it is a JSON
object whose ``kind`` is one of ``expedite``, ``raise_order`` or
``transfer`` — the stockout decision's own action values) and
``created_at`` (TIMESTAMPTZ not null, ``now()`` server default, when
the proposal was stored).

The downgrade drops the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0045_stockout_decision_proposals
Revises: 0044_standing_grant_runs
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_stockout_decision_proposals"
down_revision = "0044_standing_grant_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stockout_decision_proposals",
        sa.Column(
            "decision_id",
            sa.String(length=64),
            sa.ForeignKey("stockout_decisions.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "proposed_remedy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(proposed_remedy) = 'object' "
            "AND COALESCE(proposed_remedy->>'kind', '') "
            "IN ('expedite', 'raise_order', 'transfer')",
            name="ck_stockout_decision_proposals_kind",
        ),
    )


def downgrade() -> None:
    op.drop_table("stockout_decision_proposals")
