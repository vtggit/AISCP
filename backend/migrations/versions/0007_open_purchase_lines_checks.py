"""Add CHECK constraints to open_purchase_lines.

open_units is the quantity still open on a purchase-order line, so it can
never be negative, and order_type is a required sync discriminator that a
row without it cannot explain.  Both invariants are enforced by the
database instead of by the sync code alone.

Revision ID: 0007_open_purchase_lines_checks
Revises: 0006_open_purchase_lines
Create Date: 2026-10-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0007_open_purchase_lines_checks"
down_revision = "0006_open_purchase_lines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Validate before constraining: a row that violates either new check
    # must be repaired by the caller, never silently rewritten here, so
    # the migration refuses to run over a table it would corrupt.
    violations = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT count(*) FROM open_purchase_lines "
                "WHERE open_units < 0 OR length(order_type) <= 0"
            )
        )
        .scalar()
    )
    if violations:
        raise RuntimeError(
            "open_purchase_lines contains "
            f"{violations} row(s) with open_units < 0 or an empty "
            "order_type; repair them before applying "
            "0007_open_purchase_lines_checks"
        )
    op.create_check_constraint(
        "ck_open_purchase_lines_open_units_nonnegative",
        "open_purchase_lines",
        "open_units >= 0",
    )
    op.create_check_constraint(
        "ck_open_purchase_lines_order_type_nonempty",
        "open_purchase_lines",
        "length(order_type) > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_open_purchase_lines_order_type_nonempty",
        "open_purchase_lines",
        type_="check",
    )
    op.drop_constraint(
        "ck_open_purchase_lines_open_units_nonnegative",
        "open_purchase_lines",
        type_="check",
    )
