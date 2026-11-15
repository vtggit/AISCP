"""Add sop_signoffs: the sign-offs of the S&OP review months.

A sign-off records that the signer accepted the S&OP review of one
month: ``month`` is the signed review month ("YYYY-MM"), ``note`` the
signer's note (null when the request carried a blank one), ``figures``
the exact S&OP review answer the route computed on the server at
sign-off time (jsonb — the figures stand as they did before the
signing, a later budget or projection change never changes a stored
row), ``actor`` the caller's username (their subject when they have
none) and ``actor_name`` their display name.

``id`` is a bigint identity primary key and ``created_at`` is
``TIMESTAMPTZ`` not null with a ``now()`` server default, the way
``transfer_releases`` gives them, so ``created_at DESC, id DESC``
lists equal-timestamp rows deterministically (the higher id first);
the index on (``month``, ``created_at``) serves the per-month newest
reads.  Rows are append-only — a second sign-off of a month writes a
second row — and the downgrade drops the index and the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0036_sop_signoffs
Revises: 0035_transfer_release_status
Create Date: 2026-11-02
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0036_sop_signoffs"
down_revision = "0035_transfer_release_status"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sop_signoffs",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("month", sa.Text(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "figures",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("actor_name", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_sop_signoffs_month_created_at",
        "sop_signoffs",
        ["month", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_sop_signoffs_month_created_at", table_name="sop_signoffs")
    op.drop_table("sop_signoffs")
