"""Let parameter_writebacks carry a pending outcome: the writeback's
audit row is stored before the ERP call.

A parameter writeback audit row records one attempt to write the
reorder point and max quantity of one item-site back to its source
ERP system.  Until now the confirm stored the row only after the
writer answered, so the outcome was "written", "unchanged" or
"failed" from the first insert.  The confirm now stores the row
before it calls the ERP, so the row exists while the write is
in flight: its outcome is "pending", its before and after values
are null, and its error is null, until the writer's answer or
error sets the outcome, the values and the error on the same row.
A row whose outcome is still "pending" therefore means the result
is unknown — the write may have happened, not happened, or be
happening.

The upgrade replaces the check constraint
``ck_parameter_writebacks_outcome`` with one of the same name
allowing ``outcome IN ('pending', 'written', 'unchanged',
'failed')``.  The downgrade first rewrites every row whose outcome
is "pending" — outcome "failed" and error "pending: result
unknown", the row's result never having been answered — and then
restores the constraint to
``outcome IN ('written', 'unchanged', 'failed')``.  The downgrade
deletes no row: the audit rows survive a rollback.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0058_parameter_writeback_pending
Revises: 0057_parameter_writeback_basis
Create Date: 2026-11-25
"""

from alembic import op

revision = "0058_parameter_writeback_pending"
down_revision = "0057_parameter_writeback_basis"
branch_labels = None
depends_on = None

_CONSTRAINT_NAME = "ck_parameter_writebacks_outcome"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "parameter_writebacks")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "parameter_writebacks",
        "outcome IN ('pending', 'written', 'unchanged', 'failed')",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE parameter_writebacks "
        "SET outcome = 'failed', error = 'pending: result unknown' "
        "WHERE outcome = 'pending'"
    )
    op.drop_constraint(_CONSTRAINT_NAME, "parameter_writebacks")
    op.create_check_constraint(
        _CONSTRAINT_NAME,
        "parameter_writebacks",
        "outcome IN ('written', 'unchanged', 'failed')",
    )
