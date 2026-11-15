"""Add settings and settings_changes: registered settings with an audit trail.

A setting is one row of the ``settings`` table, keyed by (scope, owner, key):
scope is 'system' or 'user', owner is the empty string for a system setting
and the token subject (sub) for a user setting, and key is a registered
setting key.  The value is stored as jsonb, and updated_by / updated_at
record who changed it and when.  Only keys declared in
``app.settings.registry`` are ever stored; the API refuses any other key.

``settings_changes`` is the append-only audit trail: one row per change
(every store and every reset), holding the scope, owner and key, the value
stored before the change (old_value, null when the default applied), the
new value (null on a reset), and who changed it and when.  Rows are never
changed or deleted.

Revision ID: 0011_settings
Revises: 0010_stockout_decisions
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011_settings"
down_revision = "0010_stockout_decisions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint(
            "scope", "owner", "key", name="pk_settings_scope_owner_key"
        ),
        sa.CheckConstraint("scope IN ('system', 'user')", name="ck_settings_scope"),
        sa.CheckConstraint(
            "(scope = 'system' AND owner = '') OR (scope = 'user' AND owner <> '')",
            name="ck_settings_scope_owner",
        ),
    )
    op.create_table(
        "settings_changes",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("owner", sa.String(length=255), nullable=False),
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column(
            "old_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "new_value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("changed_by", sa.String(length=255), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "scope IN ('system', 'user')", name="ck_settings_changes_scope"
        ),
        sa.CheckConstraint(
            "(scope = 'system' AND owner = '') OR (scope = 'user' AND owner <> '')",
            name="ck_settings_changes_scope_owner",
        ),
    )
    op.create_index(
        "ix_settings_changes_scope_owner_key",
        "settings_changes",
        ["scope", "owner", "key"],
    )


def downgrade() -> None:
    op.drop_index("ix_settings_changes_scope_owner_key", table_name="settings_changes")
    op.drop_table("settings_changes")
    op.drop_table("settings")
