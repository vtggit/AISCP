"""Add the Odoo target codes to draft_po_actions.

``POST /api/replenishment/actions/draft-po`` translates the
request's item/site/supplier codes to their Odoo target codes
through the erp_code_mappings table before calling the Odoo client
(a mapped code is sent as its ``target_code``, an unmapped code
passes through unchanged), so the origin tag, the idempotency, and
the audit all follow the codes Odoo actually saw.  Every
``confirm=true`` audit row therefore also records the three
translated codes — ``target_item_code``, ``target_site_code`` and
``target_supplier_code`` — alongside the request's
item/site/supplier codes, so a row shows both what was asked for and
what Odoo was told.

The columns are ``TEXT`` nullable: rows written before this revision
carry no target codes (NULL), and the application never writes NULL
for a new row (an unmapped code is recorded as itself).  The
downgrade drops the three columns.

Revision ID: 0019_draft_po_actions_targets
Revises: 0018_erp_code_mappings
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0019_draft_po_actions_targets"
down_revision = "0018_erp_code_mappings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "draft_po_actions",
        sa.Column("target_item_code", sa.Text(), nullable=True),
    )
    op.add_column(
        "draft_po_actions",
        sa.Column("target_site_code", sa.Text(), nullable=True),
    )
    op.add_column(
        "draft_po_actions",
        sa.Column("target_supplier_code", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("draft_po_actions", "target_supplier_code")
    op.drop_column("draft_po_actions", "target_site_code")
    op.drop_column("draft_po_actions", "target_item_code")
