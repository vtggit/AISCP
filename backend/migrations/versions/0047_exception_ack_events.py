"""Add exception_ack_events: the planner's acknowledgement events.

An exception acknowledgement event stores, for one stored
acknowledgement of one exceptions-list key (the stable ``<kind>:<ref>``
identity ``GET /api/exceptions`` answers), what the acknowledgement
saw: ``key`` (text not null, the exceptions-list key), ``kind``
(text not null, the kind of the acknowledged row), ``item_code`` and
``site_code`` (text, null when the exception is not item-specific),
``acknowledged_by`` (String(255) not null, the token subject),
``acknowledged_by_name`` (String(255), null when the subject has no
display name), ``acknowledged_at`` (TIMESTAMPTZ not null, no default
— the acknowledgement's own time, the stored acknowledgement's),
``snooze_until`` (DATE, null when not snoozed) and ``proposal``
(jsonb, null when no proposal answered the row at acknowledgement
time — the ``ExceptionProposal`` object in JSON, with every key, a
CHECK constraint that it is a JSON object whose ``record_kind`` is
non-empty).

The table is append-only: one row per stored acknowledgement, never
updated or deleted — a second acknowledgement of a key appends a
second row, and an acknowledgement cleared by
``DELETE /api/exceptions/ack`` keeps its rows.  The migration creates
no database role, changes no table ownership and grants no privilege:
the application keeps using its existing database user.

The downgrade drops the table.

Revision ID: 0047_exception_ack_events
Revises: 0046_excess_decision_proposals
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0047_exception_ack_events"
down_revision = "0046_excess_decision_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "exception_ack_events",
        sa.Column(
            "id",
            sa.BigInteger(),
            sa.Identity(start=1),
            primary_key=True,
        ),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("item_code", sa.Text(), nullable=True),
        sa.Column("site_code", sa.Text(), nullable=True),
        sa.Column("acknowledged_by", sa.String(length=255), nullable=False),
        sa.Column("acknowledged_by_name", sa.String(length=255), nullable=True),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("snooze_until", sa.Date(), nullable=True),
        sa.Column(
            "proposal",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.CheckConstraint(
            "proposal IS NULL OR (jsonb_typeof(proposal) = 'object' "
            "AND COALESCE(proposal->>'record_kind', '') <> '')",
            name="ck_exception_ack_events_proposal",
        ),
    )


def downgrade() -> None:
    op.drop_table("exception_ack_events")
