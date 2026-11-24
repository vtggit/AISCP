"""Add parameter_writebacks.basis: what the writeback's values carried.

A parameter writeback audit row records one attempt to write the
reorder point and max quantity of one item-site back to its source
ERP system.  Until now, the row's ``decision_id`` was the only hint
at what the values came from — not null, the writeback carried a
planner decision (a reorder-point push); null, it carried a DDMRP
buffer (a buffer push).  The new ``basis`` (String(16), not null,
with the CHECK constraint ``ck_parameter_writebacks_basis`` allowing
only "decision", "ddmrp" and "excess") says it directly: the confirm
stores the preview row's basis on every audit row it writes, the
repository's default derives "decision" from a non-null decision_id
and "ddmrp" from a null one, and the record
(``app.recommend.push_record``) counts a counted push as a
reorder-point push when its basis is "decision" and as a buffer push
when its basis is "ddmrp" — a push of any other basis (such as a
future "excess") is counted in neither kind.

The upgrade adds the column (nullable while empty), backfills every
existing row — "decision" on every row whose ``decision_id`` is not
null, "ddmrp" on every other row — makes the column NOT NULL and
adds the check constraint.  The downgrade drops the constraint, then
the column.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0057_parameter_writeback_basis
Revises: 0056_short_order_releases
Create Date: 2026-11-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0057_parameter_writeback_basis"
down_revision = "0056_short_order_releases"
branch_labels = None
depends_on = None

_CONSTRAINT_NAME = "ck_parameter_writebacks_basis"


def upgrade() -> None:
    op.add_column(
        "parameter_writebacks",
        sa.Column("basis", sa.String(length=16), nullable=True),
    )
    op.execute(
        "UPDATE parameter_writebacks SET basis = 'decision' "
        "WHERE decision_id IS NOT NULL"
    )
    op.execute(
        "UPDATE parameter_writebacks SET basis = 'ddmrp' " "WHERE decision_id IS NULL"
    )
    op.alter_column("parameter_writebacks", "basis", nullable=False)
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "parameter_writebacks",
        "basis IN ('decision', 'ddmrp', 'excess')",
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "parameter_writebacks")
    op.drop_column("parameter_writebacks", "basis")
