"""Add purchase_budgets: per-month purchase budgets.

A purchase budget caps one calendar month of purchasing: the
``YYYY-MM`` ``month`` is the table's unique key — one budget per
month — so an upsert on ``month`` updates the stored ``amount`` and
bumps ``updated_at`` and ``updated_by`` instead of creating a
duplicate row.

``id`` is a bigint identity primary key, so equal-month rows (there is
only ever one) list deterministically.  ``month`` is ``TEXT`` not null
with a CHECK constraint mirroring the API's 422 rule — the pattern is
``YYYY-MM`` with a real month, 01 to 12.  ``amount`` is ``NUMERIC``
not null with ``amount >= 0``; the API additionally caps it at
1,000,000,000,000 (a model rule, not a database rule).
``updated_at`` is ``TIMESTAMPTZ`` not null with a ``now()`` server
default; the application sets it on every write.  ``updated_by`` is
``TEXT`` not null: the username of the admin who last stored the
row.  The downgrade drops the table.

Revision ID: 0020_purchase_budgets
Revises: 0019_draft_po_actions_targets
Create Date: 2026-10-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0020_purchase_budgets"
down_revision = "0019_draft_po_actions_targets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "purchase_budgets",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("month", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.UniqueConstraint("month", name="uq_purchase_budgets_month"),
        sa.CheckConstraint(
            "month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'",
            name="ck_purchase_budgets_month",
        ),
        sa.CheckConstraint(
            "amount >= 0",
            name="ck_purchase_budgets_amount",
        ),
    )


def downgrade() -> None:
    op.drop_table("purchase_budgets")
