"""Add short_order_releases: the record of the released short orders.

A short-order release row records one short supplier a confirmed
order-plan release went out for (issue #1582: the record of
``app.supply.short_orders.record_short_orders``): one row per supplier
entry of the top-ups suggestions whose ``source_system`` is
``acumatica`` and whose ``supplier_code`` holds at least one group the
confirm created — ``as_of`` (DATE not null) the release's as-of, the
as-of ``order_plan_release`` built its plan at; ``supplier_code``
(TEXT not null) and ``source_system`` (TEXT not null) the supplier's;
``minimum`` (NUMERIC not null) and ``shortfall`` (NUMERIC not null)
the supplier's stored order minimum and its shortfall against the
release's plan, the top-ups entry's own; ``suggested`` (TEXT not null,
CHECK that it is one of ``wait``, ``add_top_ups`` or ``order_anyway``)
the top-ups entry's ``suggestion``, and ``suggestion_reason`` (TEXT
not null) its ``suggestion_reason``; ``chosen`` (TEXT not null, CHECK
that it is one of ``add_top_ups`` or ``order_anyway``) what the
release did with the top-ups — ``add_top_ups`` when one of the
supplier's created groups holds a line whose ``source`` is ``top_up``,
``order_anyway`` when it went out on its due lines alone; ``actor``
(TEXT not null) the actor the release's ``plan_releases`` rows record
(the confirmer's username or subject, or ``standing-grant:<id>`` for
the standing-grant run's confirm); and ``created_at`` (TIMESTAMPTZ not
null, ``now()`` default) when the row was written.

The migration creates the table and drops it in the downgrade.  It
creates no database role, changes no table ownership and grants no
privilege: the application keeps using its existing database user.

Revision ID: 0056_short_order_releases
Revises: 0055_on_hand_snapshots
Create Date: 2026-11-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0056_short_order_releases"
down_revision = "0055_on_hand_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "short_order_releases",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("supplier_code", sa.Text(), nullable=False),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("minimum", sa.Numeric(), nullable=False),
        sa.Column("shortfall", sa.Numeric(), nullable=False),
        sa.Column("suggested", sa.Text(), nullable=False),
        sa.Column("suggestion_reason", sa.Text(), nullable=False),
        sa.Column("chosen", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "suggested IN ('wait', 'add_top_ups', 'order_anyway')",
            name="ck_short_order_releases_suggested",
        ),
        sa.CheckConstraint(
            "chosen IN ('add_top_ups', 'order_anyway')",
            name="ck_short_order_releases_chosen",
        ),
    )


def downgrade() -> None:
    op.drop_table("short_order_releases")
