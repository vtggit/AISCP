"""Add exception_acks: the planner's acknowledgements of the exceptions list.

An exception acknowledgement stores, for one exceptions-list key (the
stable ``<kind>:<ref>`` identity ``GET /api/exceptions`` answers),
who acknowledged it and when: ``acknowledged_by`` holds the token
subject and ``acknowledged_by_name`` its display name (null when it
has none), ``acknowledged_at`` the acknowledgement time (``now()`` on
store or replace), ``snooze_until`` the date the exception is hidden
from the list until (null when not snoozed) and ``note`` the
acknowledging planner's note (null when it has none).

One row per key: ``key`` is the primary key, and a second
acknowledgement of a key replaces the row — the new acknowledging
planner's subject and name and a fresh ``acknowledged_at``, with the
snooze and note set to the request's values (null when the request
omits one).  The downgrade drops the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0031_exception_acks
Revises: 0030_ddmrp_status_snapshots
Create Date: 2026-10-30
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_exception_acks"
down_revision = "0030_ddmrp_status_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exception_acks",
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=False),
        sa.Column("acknowledged_by_name", sa.String(length=255), nullable=True),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("snooze_until", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("exception_acks")
