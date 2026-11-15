"""Add ddmrp_buffers: one DDMRP buffer designation per item-site.

A DDMRP buffer designation records that a human designated one
item-site as a DDMRP buffer item.  The (item_code, site_code) pair
is the primary key, so a pair has at most one stored designation:
storing a designation for a pair that already has one replaces it,
and removing it deletes the row.

The table deliberately has no foreign key to item_sites (and no
ON DELETE CASCADE): the item-site's existence is validated at the
application level when a designation is stored (a pair that is not
in item_sites answers 404), and a stored designation remains visible
when its item-site row is absent — it is removed only by the admin
DELETE.  designated_by stores the token subject and
designated_by_name its display name (null when it has none), the
way planner_decisions records decided_by and decided_by_name.

Revision ID: 0027_ddmrp_buffers
Revises: 0026_parameter_writebacks
Create Date: 2026-10-29
"""

import sqlalchemy as sa
from alembic import op

revision = "0027_ddmrp_buffers"
down_revision = "0026_parameter_writebacks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ddmrp_buffers",
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("designated_by", sa.String(length=255), nullable=False),
        sa.Column("designated_by_name", sa.String(length=255), nullable=True),
        sa.Column(
            "designated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("item_code", "site_code"),
    )


def downgrade() -> None:
    op.drop_table("ddmrp_buffers")
