"""Add promised_on to lead_time_observations: the supplier's promised date.

The Acumatica sync joins each POReceiptLine to its POLine, which carries
the supplier's promised delivery date (POLine.PromisedDate).  Recording it
alongside the observed order and receipt dates lets the planner see, per
supplier and item, whether the promised date beat or trailed the actual
lead time.  The column is ``DATE`` nullable: the Odoo sync (and any
Acumatica receipt whose POLine carries no promised date, or one that does
not parse) stores NULL, and existing rows keep NULL until re-synced.
The downgrade drops the column (with ``IF EXISTS``, so a database whose
table was re-created from a pre-0022 snapshot while staying at head
still downgrades cleanly).

Revision ID: 0022_lead_time_promised_on
Revises: 0021_plan_releases
Create Date: 2026-10-23
"""

import sqlalchemy as sa
from alembic import op

revision = "0022_lead_time_promised_on"
down_revision = "0021_plan_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "lead_time_observations",
        sa.Column("promised_on", sa.Date(), nullable=True),
    )


def downgrade() -> None:
    op.execute("ALTER TABLE lead_time_observations DROP COLUMN IF EXISTS promised_on")
