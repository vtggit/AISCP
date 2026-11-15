"""Add erp_source_currencies: the currencies each ERP source reports in.

Each ERP reports its quantities and values in the currency of its own
companies (in Odoo, ``res.company``'s ``currency_id``).  AIERP adds
values up as the ``erp.reporting_currency`` system setting (USD by
default) and does not convert currencies, so every sync stores the
distinct currency codes its source actually reports: one row per
(``source_system``, ``currency_code``) pair — ``source_system`` (text
not null) the source system that reported the code, ``currency_code``
(text not null) the uppercase ISO-4217 code — and ``seen_at``
(``TIMESTAMPTZ`` not null with a ``now()`` server default) the newest
sync that saw it.  A sync replaces the source's whole set: the codes
it no longer reports are dropped, the ones it still reports refresh
``seen_at``, and the new set is written in one transaction.

The primary key is the pair of text columns itself: one row per code
per source system.  The downgrade drops only that table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0040_erp_source_currencies
Revises: 0039_erp_code_clashes
Create Date: 2026-11-06
"""

import sqlalchemy as sa
from alembic import op

revision = "0040_erp_source_currencies"
down_revision = "0039_erp_code_clashes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erp_source_currencies",
        sa.Column("source_system", sa.Text(), primary_key=True),
        sa.Column("currency_code", sa.Text(), primary_key=True),
        sa.Column(
            "seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_table("erp_source_currencies")
