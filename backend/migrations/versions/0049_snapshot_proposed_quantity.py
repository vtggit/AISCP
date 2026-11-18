"""Add proposed_quantity to forecast_snapshots (issue #1375).

One nullable column joins the snapshot bucket rows:

* ``proposed_quantity DOUBLE PRECISION`` — the bucket's correction
  proposed quantity at capture time.

A live capture (no ``as_of``) stores, for each bucket, the
``proposed_quantity`` the forecast list (``GET
/api/forecast/by-key?include_correction=true``) proposes for that
bucket at capture time — the ``proposed_quantity`` of the period of
the item-site's ``correction`` with the same ``period_start`` as the
bucket's ``bucket_start`` — and null when the item-site has no
correction, or the correction has no period for the bucket (a bucket
with an override day or a demand signal).  A backfill capture (an
``as_of`` given) stores null on every row: a hindcast has no
correction read, as it has no overrides and no signals.

The column has no default and the migration changes no stored row:
every existing row reads ``proposed_quantity`` null.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0049_snapshot_proposed_quantity
Revises: 0048_forecast_override_proposals
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0049_snapshot_proposed_quantity"
down_revision = "0048_forecast_override_proposals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "forecast_snapshots",
        sa.Column("proposed_quantity", postgresql.DOUBLE_PRECISION, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("forecast_snapshots", "proposed_quantity")
