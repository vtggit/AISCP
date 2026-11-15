"""Add the effective forecast to forecast_snapshots (issue #381).

Two nullable columns join the snapshot bucket rows:

* ``effective_quantity DOUBLE PRECISION`` — the bucket's effective
  forecast quantity [rule: OVR-4];
* ``override_days INTEGER`` — how many of the bucket's days an
  override covers (0 to the bucket's length).

A live capture (no ``as_of``) stores, for each bucket, the
``effective_quantity`` and ``override_days`` the forecast list
(``GET /api/forecast``) shows for that bucket at capture time — the
statistical ``quantity`` beside its effective forecast [rule: OVR-3]
[rule: OVR-4].  A backfill capture (an ``as_of`` given) stores
``effective_quantity`` equal to ``quantity`` and ``override_days`` 0:
a hindcast has no overrides.  The statistical columns of every stored
row — ``quantity``, ``method``, ``demand_class`` and
``history_buckets`` — are exactly the ones the capture stored before
this migration.

Existing rows are left untouched: both columns stay null for them,
and a stored row whose ``effective_quantity`` is null is read with
``effective_quantity`` equal to its ``quantity`` and ``override_days``
0 — the statistical forecast a hindcast stores, and what no override
covered.  The one-snapshot-per-date guard triggers are unchanged.

Revision ID: 0015_forecast_snapshot_effective
Revises: 0014_forecast_overrides
Create Date: 2026-10-15
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0015_forecast_snapshot_effective"
down_revision = "0014_forecast_overrides"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "forecast_snapshots",
        sa.Column("effective_quantity", postgresql.DOUBLE_PRECISION, nullable=True),
    )
    op.add_column(
        "forecast_snapshots",
        sa.Column("override_days", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("forecast_snapshots", "effective_quantity")
    op.drop_column("forecast_snapshots", "override_days")
