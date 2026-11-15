"""Add exception_first_seen: the first-seen date of every exception.

An exception first-seen row stores, for one exceptions-list key (the
stable ``<kind>:<ref>`` identity ``GET /api/exceptions`` answers),
the date the key was first captured: ``first_seen_on`` is the as-of
date of the list the capture saw, or the capture's current UTC date
when that list reported none.

One row per key: ``key`` is the primary key, and a capture only ever
inserts the keys that have no stored date yet — a key that already
has a ``first_seen_on`` is left untouched, so the first-seen date is
never updated or deleted.  The downgrade drops the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0032_exception_first_seen
Revises: 0031_exception_acks
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_exception_first_seen"
down_revision = "0031_exception_acks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exception_first_seen",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("first_seen_on", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("exception_first_seen")
