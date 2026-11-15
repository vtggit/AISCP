"""Add supplier_terms: the synced read model of Acumatica vendor terms.

The Acumatica sync optionally reads the ``POVendorInventory`` entity set
through the same ``iter_entities`` feed reader as the other entity sets.
It keeps only the active rows (``Active`` true) and stores them here
keyed by (``source_system``, ``business_key``), where ``business_key``
is ``POVendorInventory:<RecordID>``; ``item_code`` is the referenced
``InventoryItem.InventoryCD`` resolved with the shared inventory index,
``supplier_code`` the ``VendorID`` rendered as text the way the
suppliers read model renders a ``BAccountID``, ``min_order_qty``,
``max_order_qty`` and ``lot_size`` come from ``MinOrdQty``,
``MaxOrdQty`` and ``LotSize`` (0 or a missing value stored as null, no
constraint) and ``lead_time_days`` is ``VLeadTime`` plus
``AddLeadTimeDays`` (null when ``VLeadTime`` is missing).  A feed that
does not expose ``POVendorInventory`` skips the whole read model
instead of failing the sync.

``id`` is a UUID text primary key like the other synced read models
(item_sites, on_hand, suppliers) and ``synced_at`` defaults to
``NOW()``.  The downgrade drops the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0033_supplier_terms
Revises: 0032_exception_first_seen
Create Date: 2026-10-31
"""

import sqlalchemy as sa
from alembic import op

revision = "0033_supplier_terms"
down_revision = "0032_exception_first_seen"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "supplier_terms",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("business_key", sa.String(length=255), nullable=False),
        sa.Column("source_system", sa.String(length=32), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("supplier_code", sa.String(length=64), nullable=False),
        sa.Column("min_order_qty", sa.Numeric(), nullable=True),
        sa.Column("max_order_qty", sa.Numeric(), nullable=True),
        sa.Column("lot_size", sa.Numeric(), nullable=True),
        sa.Column("lead_time_days", sa.Numeric(), nullable=True),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("source_system", "business_key"),
    )


def downgrade() -> None:
    op.drop_table("supplier_terms")
