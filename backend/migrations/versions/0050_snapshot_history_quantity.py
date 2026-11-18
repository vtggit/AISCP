"""Add history_quantity to forecast_snapshots (issue #1382).

One nullable column joins the snapshot bucket rows:

* ``history_quantity DOUBLE PRECISION`` — the item-site's history
  mean over the demand statistics window, scaled to the bucket's
  length: the statistics row's ``mean_daily`` (the 360-day window
  the forecast row is built from) times the bucket's days (30 for
  every horizon bucket), not rounded.

A live capture (no ``as_of``) and a backfill capture (an
``as_of`` given) both fill the column: the value of the very
demand statistics row — from the same ``demand_stats_for_all``
call — the forecast row was built from, read, never stored as a
column of its own, and null when that ``mean_daily`` is missing
or not a finite number.

The column has no default and the migration changes no stored
row: every existing row reads ``history_quantity`` null.

The migration creates no database role, changes no table
ownership and grants no privilege: the application keeps using
its existing database user.

Revision ID: 0050_snapshot_history_quantity
Revises: 0049_snapshot_proposed_quantity
Create Date: 2026-11-18
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0050_snapshot_history_quantity"
down_revision = "0049_snapshot_proposed_quantity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "forecast_snapshots",
        sa.Column("history_quantity", postgresql.DOUBLE_PRECISION, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("forecast_snapshots", "history_quantity")
