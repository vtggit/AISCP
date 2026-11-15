"""Add erp_code_mappings: cross-ERP code mappings.

A mapping binds one AIERP source code to the code of the same thing in
one target system, per (kind, target_system, source_code): ``kind`` is
the AIERP entity the source code names (``item``, ``site`` or
``vendor``) and ``target_system`` is the system whose code is stored
(``odoo`` today).  The (kind, target_system, source_code) triple is
unique — one source code maps to exactly one target code per target
system — so an upsert on the triple updates the stored ``target_code``
and bumps ``updated_at`` instead of creating a duplicate row.

``id`` is a bigint identity primary key, so equal-timestamp rows list
deterministically.  ``created_at`` and ``updated_at`` are
``TIMESTAMPTZ`` not null with a ``now()`` server default; the
application sets both on insert and bumps ``updated_at`` on every
update.  ``source_code`` and ``target_code`` are ``VARCHAR(255)`` not
null: the codes stay short enough to carry as codes, and the CHECK
constraints on ``kind`` and ``target_system`` mirror the API's 422
rules at the database level.

Revision ID: 0018_erp_code_mappings
Revises: 0017_draft_po_actions
Create Date: 2026-10-18
"""

import sqlalchemy as sa
from alembic import op

revision = "0018_erp_code_mappings"
down_revision = "0017_draft_po_actions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erp_code_mappings",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("target_system", sa.Text(), nullable=False),
        sa.Column("source_code", sa.String(length=255), nullable=False),
        sa.Column("target_code", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "kind",
            "target_system",
            "source_code",
            name="uq_erp_code_mappings_kind_target_system_source_code",
        ),
        sa.CheckConstraint(
            "kind IN ('item', 'site', 'vendor')",
            name="ck_erp_code_mappings_kind",
        ),
        sa.CheckConstraint(
            "target_system IN ('odoo')",
            name="ck_erp_code_mappings_target_system",
        ),
    )


def downgrade() -> None:
    op.drop_table("erp_code_mappings")
