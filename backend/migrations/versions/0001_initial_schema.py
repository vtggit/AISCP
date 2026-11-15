"""Initial schema: inventory_items.

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-08-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "inventory_items",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("item_code", sa.String(), nullable=False),
        sa.Column("description", sa.String(), nullable=True),
        sa.Column("uom", sa.String(), nullable=True),
        sa.Column("source_system", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_inventory_items_item_code", "inventory_items", ["item_code"])


def downgrade() -> None:
    op.drop_index("ix_inventory_items_item_code", table_name="inventory_items")
    op.drop_table("inventory_items")
