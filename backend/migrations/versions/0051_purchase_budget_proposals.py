"""Add purchase_budget_proposals: the record row of every
purchase-budget save.

Every store of ``PUT /api/purchase-budgets/{month}`` appends one row
of ``purchase_budget_proposals`` beside the budget it stores (issue
#1432): the saved ``amount`` and ``saved_by`` (the caller's
username), the ``proposed_amount`` the proposals route proposed for
the month at that moment — ``propose_budget`` of the month's
``total_spend`` of ``spend_against_budget(months=12)`` grown by the
margin of the system setting ``sop.budget_proposal_margin`` — with
the ``margin`` stored too; both are null when the month is outside
that 12-month horizon, or when the spend read raises.  The row is
written in the same database transaction as the budget save: a
failed append rolls the save back, so a budget is never stored
without its record row.

The rows are append-only — never updated and never deleted.
``DELETE /api/purchase-budgets/{month}`` removes the budget and
leaves every record row.  ``id`` is a bigint identity primary key,
so the saves of one month order deterministically.  ``month`` is
``TEXT`` not null with the CHECK the ``purchase_budgets`` table
mirrors — ``YYYY-MM`` with a real month, 01 to 12.  ``amount`` is
``NUMERIC`` not null with ``amount >= 0``.  ``proposed_amount`` is
``NUMERIC`` null with a CHECK that it is null or at least 0 (the
nulls are the outside-the-horizon and the failed-read saves).
``margin`` is ``NUMERIC`` null.  ``saved_by`` is ``TEXT`` not null.
``saved_at`` is ``TIMESTAMPTZ`` not null with a ``now()`` server
default: when the save was stored.  An index on ``(month, saved_at)``
serves the measure's read of the latest row of each month.  The
downgrade drops the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0051_purchase_budget_proposals
Revises: 0050_snapshot_history_quantity
Create Date: 2026-11-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_purchase_budget_proposals"
down_revision = "0050_snapshot_history_quantity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_budget_proposals",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("month", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column("proposed_amount", sa.Numeric(), nullable=True),
        sa.Column("margin", sa.Numeric(), nullable=True),
        sa.Column("saved_by", sa.Text(), nullable=False),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'",
            name="ck_purchase_budget_proposals_month",
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name="ck_purchase_budget_proposals_amount",
        ),
        sa.CheckConstraint(
            "proposed_amount IS NULL OR proposed_amount >= 0",
            name="ck_purchase_budget_proposals_proposed_amount",
        ),
    )
    op.create_index(
        "ix_purchase_budget_proposals_month_saved_at",
        "purchase_budget_proposals",
        ["month", "saved_at"],
    )


def downgrade() -> None:
    op.drop_table("purchase_budget_proposals")
