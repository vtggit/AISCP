"""Add transfer_releases: the audit of released transfer orders.

Every ``confirm=true`` request to
``POST /api/supply-plan/transfers/release`` inserts one row per
(from_site, to_site) group of the chosen transfer suggestions, written
``pending`` before the Acumatica batch call and updated to the batch's
per-group outcome — ``created``, ``exists`` or ``error`` with the
reference number, the resolved warehouse codes and the error message —
so the audit always shows every release attempt: an auth or transport
failure that aborts the batch leaves the release's rows ``aborted``,
and an ``exists`` outcome whose current plan lines differ from the
stored snapshot of the same origin keeps the outcome but not the
existing order's reference number, with the stale-lines error in the
row instead.

``id`` is a bigint identity primary key, the way ``plan_releases``
gives it one, so ``created_at DESC, id DESC`` lists equal-timestamp
rows deterministically (insertion order reversed).  ``origin`` is the
transfer release's origin tag — "AIERP transfer <as_of> site
<from_site> to <to_site>" — the idempotency key the on-hold transfer
order is written under in Acumatica; ``from_site_code`` and
``to_site_code`` identify the group's sites.  ``from_warehouse_code``
and ``to_warehouse_code`` are the codes Acumatica resolved for the
group (null until the batch settles one), ``reference_nbr`` the
Acumatica transfer order number (null without one), ``outcome`` the
row's state and ``error`` its message (null without one).  Every
column the table shares with ``plan_releases`` — ``origin``,
``outcome``, ``error``, ``line_count``, ``lines``, ``actor`` and the
timestamps — carries the same type and default ``plan_releases``
gives it.  ``lines`` is the exact line snapshot sent to Acumatica
(``[{item_code, quantity}]``) as jsonb not null; ``actor`` is the
username of the admin who confirmed and ``actor_name`` the
admin's display name (null without one).  ``created_at`` is
``TIMESTAMPTZ`` not null with a ``now()`` server default and
``updated_at`` is bumped on every update.  The ``outcome`` CHECK
limits the row to ``pending``, ``created``, ``exists``, ``error`` or
``aborted``.  The downgrade drops the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0034_transfer_releases
Revises: 0033_supplier_terms
Create Date: 2026-11-01
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0034_transfer_releases"
down_revision = "0033_supplier_terms"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "transfer_releases",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.Column("from_site_code", sa.Text(), nullable=False),
        sa.Column("to_site_code", sa.Text(), nullable=False),
        sa.Column("from_warehouse_code", sa.Text(), nullable=True),
        sa.Column("to_warehouse_code", sa.Text(), nullable=True),
        sa.Column("reference_nbr", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("line_count", sa.Integer(), nullable=False),
        sa.Column(
            "lines",
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
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('pending', 'created', 'exists', 'error', 'aborted')",
            name="ck_transfer_releases_outcome",
        ),
    )


def downgrade() -> None:
    op.drop_table("transfer_releases")
