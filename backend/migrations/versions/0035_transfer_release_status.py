"""Add the Acumatica status readback to transfer_releases.

``erp_status`` is the status the transfer order last carried in
Acumatica, as read back by the status refresh — ``On Hold``,
``Released``, ``Canceled`` or whatever the ERP answers — and
``erp_status_checked_at`` is the instant that status was read.
Both are nullable: a release row has no readback until one happens,
and a row whose order the ERP no longer returns keeps its stored
values (the refresh leaves a missing number unchanged).

The migration adds the two columns to ``transfer_releases`` and
drops them in the downgrade.  It creates no database role, changes
no table ownership and grants no privilege: the application keeps
using its existing database user.

Revision ID: 0035_transfer_release_status
Revises: 0034_transfer_releases
Create Date: 2026-11-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0035_transfer_release_status"
down_revision = "0034_transfer_releases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transfer_releases",
        sa.Column("erp_status", sa.Text(), nullable=True),
    )
    op.add_column(
        "transfer_releases",
        sa.Column("erp_status_checked_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transfer_releases", "erp_status_checked_at")
    op.drop_column("transfer_releases", "erp_status")
