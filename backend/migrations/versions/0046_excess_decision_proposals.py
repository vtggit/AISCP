"""Add excess_decision_proposals: the remedy proposed with an excess decision.

An excess decision proposal stores the remedy the excess screen
proposed for one item-site's ranked row at the moment an excess
decision on that row was recorded (issue #1336: ``POST
/api/excess/decisions`` stores the ``GET /api/excess`` answer's
``remedy`` with the decision it was proposed for): one row per
proposal, ``decision_id`` (text not null, the id of the excess
decision the remedy was proposed for, the primary key and a foreign
key to ``excess_decisions(id)`` with ON DELETE CASCADE — a removed
decision removes its proposal, and one decision has at most one
proposal), ``proposed_remedy`` (jsonb not null, the proposed remedy —
the ``ExcessRemedy`` object in JSON, with every key, a CHECK
constraint that it is a JSON object whose ``kind`` is one of
``dispose``, ``reduce`` or ``stop_replenishing`` — the excess
decision's own action values) and ``created_at`` (TIMESTAMPTZ not
null, ``now()`` server default, when the proposal was stored).

The downgrade drops the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0046_excess_decision_proposals
Revises: 0045_stockout_decision_proposals
Create Date: 2026-11-16
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0046_excess_decision_proposals"
down_revision = "0045_stockout_decision_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "excess_decision_proposals",
        sa.Column(
            "decision_id",
            sa.String(length=64),
            sa.ForeignKey("excess_decisions.id", ondelete="CASCADE"),
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
            "IN ('dispose', 'reduce', 'stop_replenishing')",
            name="ck_excess_decision_proposals_kind",
        ),
    )


def downgrade() -> None:
    op.drop_table("excess_decision_proposals")
