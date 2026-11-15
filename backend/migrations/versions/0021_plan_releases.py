"""Add plan_releases: the audit of planned-order releases to Acumatica.

Every ``confirm=true`` request to
``POST /api/aggregate/release`` inserts one row per (site, supplier)
group of the month's release plan, written ``pending`` before the
Acumatica batch call and updated to the batch's per-group outcome —
``created``, ``exists`` or ``error`` with the order number, the
resolved warehouse and vendor codes and the error message — so the
audit always shows every release attempt: an auth or transport
failure that aborts the batch leaves the release's rows ``aborted``,
and a confirm whose planned lines no longer match the stored snapshot
of an existing origin fails with a conflict and leaves the group's
row ``error`` instead of reporting ``exists``.

``id`` is a bigint identity primary key, so ``created_at DESC, id
DESC`` lists equal-timestamp rows deterministically (insertion order
reversed).  ``month`` is the released ``YYYY-MM`` month;
``site_code``, ``supplier_code`` and ``origin`` identify the group and
the Acumatica origin tag the draft PO was written under.
``warehouse_code`` and ``vendor_code`` are the codes Acumatica
resolved for the group (null until the batch settles one), ``order_nbr``
the Acumatica order number (null without one), ``outcome`` the
row's state and ``error`` its message (null without one).
``line_count`` is the number of lines in the snapshot, ``value`` the
group's total value as ``NUMERIC`` not null — the same exact numeric
type the other monetary columns use, never a floating-point type — and
``lines`` the exact line snapshot sent to Acumatica (
``[{item_code, quantity, unit_cost, value}]``) as jsonb not null.
``actor`` is the username of the admin who confirmed.  ``created_at``
is ``TIMESTAMPTZ`` not null with a ``now()`` server default and
``updated_at`` is bumped on every update.  The ``outcome`` CHECK
limits the row to ``pending``, ``created``, ``exists``, ``error`` or
``aborted``.  The downgrade drops the table.

Revision ID: 0021_plan_releases
Revises: 0020_purchase_budgets
Create Date: 2026-10-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0021_plan_releases"
down_revision = "0020_purchase_budgets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "plan_releases",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("month", sa.Text(), nullable=False),
        sa.Column("site_code", sa.Text(), nullable=False),
        sa.Column("supplier_code", sa.Text(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("warehouse_code", sa.Text(), nullable=True),
        sa.Column("vendor_code", sa.Text(), nullable=True),
        sa.Column("order_nbr", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column("value", sa.Numeric(), nullable=False),
        sa.Column(
            "lines",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('pending', 'created', 'exists', 'error', 'aborted')",
            name="ck_plan_releases_outcome",
        ),
    )


def downgrade() -> None:
    op.drop_table("plan_releases")
