"""Add forecast_override_proposals: the correction proposed with a
forecast override.

A forecast override proposal stores the correction ``GET
/api/forecast/by-key?include_correction=true`` proposed for the
item-site at the moment an override on one of the item-site's
forecast periods was recorded (issue #1365: ``POST
/api/forecast/overrides`` stores the ``GET /api/forecast/by-key``
answer's ``correction`` with the override it was proposed for): one
row per proposal, ``override_id`` (integer not null, the id of the
forecast override the correction was proposed for, the primary key
and a foreign key to ``forecast_overrides.id`` with ON DELETE
CASCADE — a removed override removes its proposal, and one override
has at most one proposal), ``proposed_correction`` (jsonb not null,
the proposed correction — the ``ForecastCorrection`` object in JSON,
with every key, a CHECK constraint that it is a JSON object whose
``periods`` is an array) and ``created_at`` (TIMESTAMPTZ not null,
``now()`` server default, when the proposal was stored).

The downgrade drops the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0048_forecast_override_proposals
Revises: 0047_exception_ack_events
Create Date: 2026-11-17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0048_forecast_override_proposals"
down_revision = "0047_exception_ack_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forecast_override_proposals",
        sa.Column(
            "override_id",
            sa.Integer(),
            sa.ForeignKey("forecast_overrides.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "proposed_correction",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "jsonb_typeof(proposed_correction) = 'object' "
            "AND jsonb_typeof(proposed_correction->'periods') = 'array'",
            name="ck_forecast_override_proposals_shape",
        ),
    )


def downgrade() -> None:
    op.drop_table("forecast_override_proposals")
