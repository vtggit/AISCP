"""Add empirical_adoptions: one stored adoption per item-site.

An empirical adoption records that a human adopted an empirical value
for one item-site.  The (item_code, site_code) pair is the primary
key, so a pair has at most one stored adoption: storing an adoption
for a pair that already has one replaces it, and withdrawing it
removes the row.

The table deliberately has no foreign key to item_sites (and no
ON DELETE CASCADE): the item-site's existence is validated at the
application level, and an adoption for a pair that is not in
item_sites answers 404.  adopted_by stores the token subject and
adopted_by_name its display name (null when it has none), the way
planner_decisions records decided_by and decided_by_name.

Revision ID: 0025_empirical_adoptions
Revises: 0024_inventory_items_unique_code
Create Date: 2026-10-28
"""

import sqlalchemy as sa
from alembic import op

revision = "0025_empirical_adoptions"
down_revision = "0024_inventory_items_unique_code"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "empirical_adoptions",
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("adopted_by", sa.String(length=255), nullable=False),
        sa.Column("adopted_by_name", sa.String(length=255), nullable=True),
        sa.Column(
            "adopted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("item_code", "site_code"),
    )


def downgrade() -> None:
    op.drop_table("empirical_adoptions")
