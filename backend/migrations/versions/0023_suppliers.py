"""Add suppliers: the synced read model of Acumatica BAccount vendors.

The Acumatica sync optionally reads the ``BAccount`` entity set through
the same ``iter_entities`` feed reader as the six other entity sets.  It
keeps only the vendor rows (``Type`` ``VE``) and stores them here keyed
by ``supplier_code`` (the ``BAccountID`` as text); ``vendor_code`` is
the trimmed ``AcctCD``, ``name`` the trimmed ``AcctName`` and ``status``
the ``Status``.  A feed that does not expose ``BAccount`` skips the
whole read model instead of failing the sync.

``id`` is a UUID text primary key like the other synced read models
(item_sites, on_hand), ``source_system`` the system the row came from
and ``synced_at`` defaults to ``NOW()``.  The downgrade drops the table.

Revision ID: 0023_suppliers
Revises: 0022_lead_time_promised_on
Create Date: 2026-10-24
"""

import sqlalchemy as sa
from alembic import op

revision = "0023_suppliers"
down_revision = "0022_lead_time_promised_on"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "suppliers",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("supplier_code", sa.Text(), nullable=False),
        sa.Column("vendor_code", sa.Text(), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=True),
        sa.Column("source_system", sa.String(length=64), nullable=True),
        sa.Column(
            "synced_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.UniqueConstraint("supplier_code"),
    )


def downgrade() -> None:
    op.drop_table("suppliers")
