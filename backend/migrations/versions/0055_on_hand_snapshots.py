"""Add on_hand_snapshots: the on-hand inventory by capture date.

An on-hand snapshot stores, for one ``snapshot_on`` date and one
item-site, the ``on_hand`` row of the pair captured under that date:
its ``quantity_on_hand``, its ``source_system`` (null when the ERP
reported none) and the row's own ``as_of``.  The capture stores every
``on_hand`` row under one date — the UTC date of the latest ``as_of``
among the ``on_hand`` rows — and replaces that date's rows wholesale,
so a second capture of the same date is idempotent and a pair no
longer in ``on_hand`` leaves the date's snapshot.

One row per pair per date: the primary key is (``snapshot_on``,
``item_code``, ``site_code``).  The migration drops the table in the
downgrade.  It creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0055_on_hand_snapshots
Revises: 0054_plan_release_readback
Create Date: 2026-11-21
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0055_on_hand_snapshots"
down_revision = "0054_plan_release_readback"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "on_hand_snapshots",
        sa.Column("snapshot_on", sa.Date(), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column(
            "quantity_on_hand",
            postgresql.DOUBLE_PRECISION,
            nullable=False,
        ),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("snapshot_on", "item_code", "site_code"),
    )


def downgrade() -> None:
    op.drop_table("on_hand_snapshots")
