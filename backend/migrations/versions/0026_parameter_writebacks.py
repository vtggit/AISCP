"""Add parameter_writebacks: the append-only ERP parameter writeback audit.

A parameter writeback row records one attempt to write the reorder
point and max quantity of one item-site back to its source ERP system:
the parameters before and after the write, the values the writeback
requested, and the outcome ("written", "unchanged" or "failed",
enforced by a CHECK constraint so no other word can ever be stored).
decision_id links the row to the planner decision it carried when it
is one; error holds the ERP's error text on a failure.  written_by
stores the token subject and written_by_name its display name (null
when it has none), the way planner decisions record decided_by and
decided_by_name.  The index on (item_code, site_code, written_at)
serves the newest-first history listing.

The table is append-only: the repository that serves it exposes no
update or delete, and the downgrade below is a no-op — it drops,
truncates and alters nothing, so the audit rows survive a rollback.
Because a rollback keeps the table, the upgrade creates the table and
its index only when they are missing, so upgrading back to head after
a downgrade is clean.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0026_parameter_writebacks
Revises: 0025_empirical_adoptions
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0026_parameter_writebacks"
down_revision = "0025_empirical_adoptions"
branch_labels = None
depends_on = None

_INDEX_NAME = "ix_parameter_writebacks_item_site_written_at"


def upgrade() -> None:
    # The downgrade is a no-op, so a table created by an earlier
    # upgrade can still be here after a rollback: create the table (and
    # its index) only when missing instead of failing on the re-upgrade.
    inspector = sa.inspect(op.get_bind())
    if "parameter_writebacks" not in inspector.get_table_names():
        op.create_table(
            "parameter_writebacks",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("item_code", sa.String(length=64), nullable=False),
            sa.Column("site_code", sa.String(length=64), nullable=False),
            sa.Column("source_system", sa.String(length=32), nullable=False),
            sa.Column("decision_id", sa.String(length=36), nullable=True),
            sa.Column("before_reorder_point", sa.Float(), nullable=True),
            sa.Column("before_max_qty", sa.Float(), nullable=True),
            sa.Column("after_reorder_point", sa.Float(), nullable=True),
            sa.Column("after_max_qty", sa.Float(), nullable=True),
            sa.Column("requested_reorder_point", sa.Float(), nullable=False),
            sa.Column("requested_max_qty", sa.Float(), nullable=False),
            sa.Column("outcome", sa.String(length=16), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("written_by", sa.String(length=255), nullable=False),
            sa.Column("written_by_name", sa.String(length=255), nullable=True),
            sa.Column(
                "written_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint(
                "outcome IN ('written', 'unchanged', 'failed')",
                name="ck_parameter_writebacks_outcome",
            ),
        )
        op.create_index(
            _INDEX_NAME,
            "parameter_writebacks",
            ["item_code", "site_code", "written_at"],
        )


def downgrade() -> None:
    """No-op on purpose.

    The audit rows are the point of the table, so the downgrade does
    not drop, truncate or alter it: the rows survive a rollback and a
    re-upgrade finds the table already in place.
    """
