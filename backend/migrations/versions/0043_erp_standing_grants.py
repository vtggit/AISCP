"""Add erp_standing_grants: the standing grants to write to the ERP.

A standing grant is the standing permission to run one release process
against the ERP without a person approving every write: one row per
grant — ``process`` (text not null, the process the grant covers,
CHECK that it is one of ``order_plan_release`` or
``transfer_release``), ``max_document_value`` (numeric not null,
``max_document_value > 0``) the largest document value the grant
covers, ``max_daily_value`` (numeric not null,
``max_daily_value >= max_document_value``) the largest total value the
grant covers per day, ``max_data_age_hours`` (integer not null,
``max_data_age_hours between 1 and 168``) how many hours the data the
write is based on may be old, ``created_at`` (TIMESTAMPTZ not null,
``now()`` server default), ``created_by`` (text not null, the username
of the admin who created the grant), ``revoked_at`` (TIMESTAMPTZ null)
and ``revoked_by`` (text null, the username of the admin who revoked
the grant).  A CHECK constraint ties the two revocation columns
together: ``revoked_by`` is null exactly when ``revoked_at`` is null.

``id`` is a bigint identity primary key, and a unique index over
``process`` where ``revoked_at`` is null allows one grant in force per
process: a second in-force grant for the same process is a unique
violation the API answers with 409, and revoking a grant frees the
process for a new one.  A grant is never deleted and its limits never
change: changing a limit means revoking the grant and creating a new
one.  The downgrade drops the table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0043_erp_standing_grants
Revises: 0042_guidance
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op

revision = "0043_erp_standing_grants"
down_revision = "0042_guidance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "erp_standing_grants",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("process", sa.Text(), nullable=False),
        sa.Column("max_document_value", sa.Numeric(), nullable=False),
        sa.Column("max_daily_value", sa.Numeric(), nullable=False),
        sa.Column("max_data_age_hours", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Text(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_by", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "process IN ('order_plan_release', 'transfer_release')",
            name="ck_erp_standing_grants_process",
        ),
        sa.CheckConstraint(
            "max_document_value > 0",
            name="ck_erp_standing_grants_max_document_value",
        ),
        sa.CheckConstraint(
            "max_daily_value >= max_document_value",
            name="ck_erp_standing_grants_max_daily_value",
        ),
        sa.CheckConstraint(
            "max_data_age_hours BETWEEN 1 AND 168",
            name="ck_erp_standing_grants_max_data_age_hours",
        ),
        sa.CheckConstraint(
            "(revoked_by IS NULL) = (revoked_at IS NULL)",
            name="ck_erp_standing_grants_revoked",
        ),
    )
    op.create_index(
        "ix_erp_standing_grants_process_in_force",
        "erp_standing_grants",
        ["process"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("erp_standing_grants")
