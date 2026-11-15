"""Add signal_quantity to forecast_snapshots (issue #979).

One nullable column joins the snapshot bucket rows:

* ``signal_quantity DOUBLE PRECISION`` — the bucket's counted
  demand-signal quantity at capture time.

A live capture (no ``as_of``) stores, for each bucket, the
``signal_quantity`` the forecast list (``GET /api/forecast``) shows
for that bucket at capture time — the same value the effective
forecast adds to the bucket's ``effective_quantity``; a backfill
capture (an ``as_of`` given) stores 0: a hindcast has no signals,
as it has no overrides.  The statistical columns of every stored row
— ``quantity``, ``method``, ``demand_class`` and ``history_buckets``
— and the effective columns are exactly the ones the capture stored
before this migration.

A signal withdrawn after a capture changes no stored row: the rows
record what was believed on the as-of date, so the stored rows keep
the ``signal_quantity`` the capture stored.

The column has no default and the migration changes no stored row:
every existing row reads ``signal_quantity`` null.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0038_snapshot_signal_quantity
Revises: 0037_demand_signals
Create Date: 2026-11-04
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0038_snapshot_signal_quantity"
down_revision = "0037_demand_signals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "forecast_snapshots",
        sa.Column("signal_quantity", postgresql.DOUBLE_PRECISION, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("forecast_snapshots", "signal_quantity")
