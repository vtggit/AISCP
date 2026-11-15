"""Make inventory_items.item_code unique: the synced item master.

The Acumatica sync upserts the item master (``inventory_items``) keyed
by ``item_code`` — the trimmed ``InventoryCD`` of every stock item
(``StkItem`` true) of the ``InventoryItem`` entity set.  The
repository's ``upsert_many`` resolves the insert-or-update decision
with ``ON CONFLICT (item_code)``, which needs a unique constraint, so
the non-unique ``ix_inventory_items_item_code`` index of
0001_initial_schema is replaced by a unique index on the same column
under the same name.  The downgrade restores the non-unique index.

Revision ID: 0024_inventory_items_unique_code
Revises: 0023_suppliers
Create Date: 2026-10-25
"""

from alembic import op

revision = "0024_inventory_items_unique_code"
down_revision = "0023_suppliers"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_inventory_items_item_code", table_name="inventory_items")
    op.create_index(
        "ix_inventory_items_item_code",
        "inventory_items",
        ["item_code"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("ix_inventory_items_item_code", table_name="inventory_items")
    op.create_index("ix_inventory_items_item_code", "inventory_items", ["item_code"])
