"""Add supplier_order_minimums: the minimum order amount per supplier.

A supplier order minimum is the smallest order amount a supplier will
accept: one row per (``source_system``, ``supplier_code``) pair —
``source_system`` (text not null) the ERP the supplier lives in,
``supplier_code`` (text not null) the supplier's code in that system,
``amount`` (numeric not null, ``amount > 0``) the minimum in the
supplier's currency.  A minimum can be stored before the supplier is
synced: nothing checks that the supplier exists.  Storing a pair that
is already stored replaces the amount and bumps ``updated_at``
(``TIMESTAMPTZ`` not null, ``now()`` server default; the application
sets it on every write) and ``updated_by`` (text not null, the
username of the admin who last stored the row) instead of creating a
duplicate.

``id`` is a bigint identity primary key, and (``source_system``,
``supplier_code``) is the table's unique constraint.  CHECK
constraints keep ``amount`` above 0 and both text keys non-empty.
The downgrade drops the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0041_supplier_order_minimums
Revises: 0040_erp_source_currencies
Create Date: 2026-11-11
"""

import sqlalchemy as sa
from alembic import op

revision = "0041_supplier_order_minimums"
down_revision = "0040_erp_source_currencies"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_order_minimums",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("source_system", sa.Text(), nullable=False),
        sa.Column("supplier_code", sa.Text(), nullable=False),
        sa.Column("amount", sa.Numeric(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_by", sa.Text(), nullable=False),
        sa.UniqueConstraint(
            "source_system",
            "supplier_code",
            name="uq_supplier_order_minimums_source_supplier",
        ),
        sa.CheckConstraint(
            "amount > 0",
            name="ck_supplier_order_minimums_amount",
        ),
        sa.CheckConstraint(
            "source_system <> ''",
            name="ck_supplier_order_minimums_source_system",
        ),
        sa.CheckConstraint(
            "supplier_code <> ''",
            name="ck_supplier_order_minimums_supplier_code",
        ),
    )


def downgrade() -> None:
    op.drop_table("supplier_order_minimums")
