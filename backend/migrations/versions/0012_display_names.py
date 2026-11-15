"""Add display-name columns beside the subject columns.

The audit columns ``planner_decisions.decided_by``,
``excess_decisions.decided_by``, ``stockout_decisions.decided_by``,
``settings.updated_by`` and ``settings_changes.changed_by`` store the
token subject (sub), which is a machine identifier a planner cannot
recognise.  This revision adds one nullable display-name column beside
each of them:

* ``planner_decisions.decided_by_name``
* ``excess_decisions.decided_by_name``
* ``stockout_decisions.decided_by_name``
* ``settings.updated_by_name``
* ``settings_changes.changed_by_name``

Every new write fills the column with the caller's display name — the
token's username when it is non-empty, else its email when non-empty,
else its sub, truncated to the column's length.  The subject columns
keep storing the sub exactly as before.  Rows written before this
change keep NULL in the new column: a null name is the marker of a row
written before display names were captured.

Revision ID: 0012_display_names
Revises: 0011_settings
Create Date: 2026-10-10
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_display_names"
down_revision = "0011_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "planner_decisions",
        sa.Column("decided_by_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "excess_decisions",
        sa.Column("decided_by_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "stockout_decisions",
        sa.Column("decided_by_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "settings",
        sa.Column("updated_by_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "settings_changes",
        sa.Column("changed_by_name", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("settings_changes", "changed_by_name")
    op.drop_column("settings", "updated_by_name")
    op.drop_column("stockout_decisions", "decided_by_name")
    op.drop_column("excess_decisions", "decided_by_name")
    op.drop_column("planner_decisions", "decided_by_name")
