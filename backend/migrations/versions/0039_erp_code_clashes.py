"""Add erp_code_clashes: the item-site pairs refused by the code-clash guard.

Two ERPs may both claim the same (item_code, site_code) pair.  The
first holder keeps its ``item_sites`` row exactly as it was — including
its ``source_system`` — and every sync that still sees the pair held by
another source system records one row here: ``item_code`` and
``site_code`` the held pair, ``held_by`` the source system that stores
the pair in ``item_sites``, ``refused_source`` the source system whose
sync was refused, ``first_seen_at`` the first sync that saw the clash
and ``last_seen_at`` the newest one (both ``TIMESTAMPTZ`` not null with
a ``now()`` server default), and ``resolved_at`` (null while the clash
is open) set when a sync no longer sees the pair held — so a clash
someone fixed in the ERP closes itself.

``id`` is a bigint identity primary key and the unique constraint on
(``item_code``, ``site_code``, ``refused_source``) makes the guard's
record-and-resolve upserts atomic: one row per held pair per refusing
source, refreshed by every sync that still sees the clash and never
deleted.  The downgrade drops only that table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0039_erp_code_clashes
Revises: 0038_snapshot_signal_quantity
Create Date: 2026-11-05
"""

import sqlalchemy as sa
from alembic import op

revision = "0039_erp_code_clashes"
down_revision = "0038_snapshot_signal_quantity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erp_code_clashes",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("item_code", sa.Text(), nullable=False),
        sa.Column("site_code", sa.Text(), nullable=False),
        sa.Column("held_by", sa.Text(), nullable=False),
        sa.Column("refused_source", sa.Text(), nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("item_code", "site_code", "refused_source"),
    )


def downgrade() -> None:
    op.drop_table("erp_code_clashes")
