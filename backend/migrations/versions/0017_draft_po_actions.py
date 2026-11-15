"""Add draft_po_actions: persistent audit of draft-PO confirms.

Every ``confirm=true`` request to
``POST /api/replenishment/actions/draft-po`` appends exactly one row
after the Odoo call settles: who confirmed (the token subject and the
resolved display name), what was confirmed (the request's
item/site/supplier/quantity/unit_price) and the outcome.  ``outcome``
is ``created`` or ``already_existed`` when the client returned
``already_existed`` false or true (with the Odoo order's id and name)
or ``error`` when the request answered 409 / 502 / 422 (with the
raised exception's class name).  A ``confirm=false`` dry run appends
no row.  No credential, environment value, or Odoo payload is stored.

``id`` is a bigint identity primary key, so ``created_at DESC, id
DESC`` lists equal-timestamp rows deterministically (insertion order
reversed).  ``created_at`` is ``TIMESTAMPTZ`` not null with a ``now()``
server default.  Rows are append-only: they are never changed or
deleted.

Revision ID: 0017_draft_po_actions
Revises: 0016_scenarios
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0017_draft_po_actions"
down_revision = "0016_scenarios"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "draft_po_actions",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_sub", sa.String(length=255), nullable=False),
        sa.Column("actor_name", sa.Text(), nullable=True),
        sa.Column("item_code", sa.Text(), nullable=False),
        sa.Column("site_code", sa.Text(), nullable=False),
        sa.Column("supplier_code", sa.Text(), nullable=False),
        sa.Column("quantity", postgresql.DOUBLE_PRECISION, nullable=False),
        sa.Column("unit_price", postgresql.DOUBLE_PRECISION, nullable=False),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("odoo_id", sa.Integer(), nullable=True),
        sa.Column("odoo_name", sa.Text(), nullable=True),
        sa.Column("error_class", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('created', 'already_existed', 'error')",
            name="ck_draft_po_actions_outcome",
        ),
    )


def downgrade() -> None:
    op.drop_table("draft_po_actions")
