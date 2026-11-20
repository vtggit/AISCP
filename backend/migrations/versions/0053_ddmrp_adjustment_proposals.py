"""Add ddmrp_adjustment_proposals: the record row of every DDMRP
adjustment save.

Every store of ``POST /api/ddmrp/adjustments`` appends one row of
``ddmrp_adjustment_proposals`` beside the adjustment it stores
(issue #1475), in the same database transaction: the new adjustment's
``id`` (``adjustment_id``), its codes, its window and its ``factor``,
the ``proposed_factor`` the proposals read proposed for exactly that
window at that moment — for a body with both codes, the ``factor`` of
the proposal of ``adjustment_proposals(periods=12)`` whose
``item_code``, ``site_code``, ``start_on`` and ``end_on`` all equal
the body's — with the ``threshold`` of that read stored too.  Both
are null for an all-buffer save (both codes absent), when no proposal
matched the window, or when the proposals read raised (a matched
proposal whose ``factor`` is null or below 0 stores a null
``proposed_factor``).  A failed append rolls the save back, so an
adjustment is never stored without its record row, and an overlapping
window (the 409) appends neither row.

The rows are append-only — never updated and never deleted.
``DELETE /api/ddmrp/adjustments/{id}`` removes the adjustment and
leaves every record row.  ``adjustment_id`` is ``TEXT`` not null
with no foreign key to ``ddmrp_adjustments`` on purpose: the record
must stay when its adjustment is deleted, so no constraint could
remove the row — or even see the delete — when the adjustment goes
away.  ``item_code`` and ``site_code`` are ``TEXT`` null (null for an
all-buffer save), ``start_on`` and ``end_on`` are ``DATE`` not null,
and ``factor`` is ``NUMERIC`` not null with the ``factor > 0 AND
factor <= 10`` CHECK the ``ddmrp_adjustments`` table mirrors.
``proposed_factor`` is ``NUMERIC`` null with a CHECK that it is null
or above 0 (the nulls are the all-buffer, the no-match and the
failed-read saves; a factor of exactly 0 is not a proposal the record
may carry).  ``threshold`` is ``NUMERIC`` null.  ``saved_by`` is
``TEXT`` not null — the adjustment's ``created_by`` — and ``saved_at``
is ``TIMESTAMPTZ`` not null with a ``now()`` server default: when the
save was stored.  ``id`` is a bigint identity primary key, so the
saves of one adjustment order deterministically.  The downgrade drops
the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0053_ddmrp_adjustment_proposals
Revises: 0052_data_versions
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0053_ddmrp_adjustment_proposals"
down_revision = "0052_data_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ddmrp_adjustment_proposals",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("adjustment_id", sa.Text(), nullable=False),
        sa.Column("item_code", sa.Text(), nullable=True),
        sa.Column("site_code", sa.Text(), nullable=True),
        sa.Column("start_on", sa.Date(), nullable=False),
        sa.Column("end_on", sa.Date(), nullable=False),
        sa.Column("factor", sa.Numeric(), nullable=False),
        sa.Column("proposed_factor", sa.Numeric(), nullable=True),
        sa.Column("threshold", sa.Numeric(), nullable=True),
        sa.Column("saved_by", sa.Text(), nullable=False),
        sa.Column(
            "saved_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "factor > 0 AND factor <= 10",
            name="ck_ddmrp_adjustment_proposals_factor",
        ),
        sa.CheckConstraint(
            "proposed_factor IS NULL OR proposed_factor > 0",
            name="ck_ddmrp_adjustment_proposals_proposed_factor",
        ),
    )


def downgrade() -> None:
    op.drop_table("ddmrp_adjustment_proposals")
