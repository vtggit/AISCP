"""Add forecast_overrides: the planner's overrides and withdrawals.

A forecast override replaces the statistical forecast of one forecast
period (one 30-day horizon bucket of the current forecast) for one
item-site with the planner's own quantity (rules OVR-1, OVR-3).
Overrides are append-only [rule: OVR-2]: a new override for the same
item-site and period is a new row, never an update — the
``forecast_overrides_guard`` trigger refuses every UPDATE and DELETE on
the table. For one item-site and period the row with the highest
database-generated ``id`` is the current one [rule: OVR-13];
``created_at`` is never used to order rows, so two rows with the same
or out-of-order timestamps still have one definite winner, and both are
stored. A withdrawal [rule: OVR-9] is likewise a new row (kind
``withdrawal``, null quantity) that stores the stored period's dates;
the earlier rows stay in the history. A completed period — its
``period_end`` on or before the shared default as-of date — is closed:
its rows stay in the history and can no longer be withdrawn
[rule: OVR-12].

``kind`` is ``override`` or ``withdrawal`` (CHECK). ``quantity`` is not
null and not negative for an override and null for a withdrawal
(CHECK). ``reason`` is not null and not blank after trimming (CHECK) —
it is stored trimmed, at most 1,000 characters. ``created_by`` is the
caller's token subject; ``created_by_name`` is the caller's display
name as the decision tables store it (nullable: null when the token
carries no display name). An index covers
(item_code, site_code, period_start, id).

Revision ID: 0014_forecast_overrides
Revises: 0013_forecast_snapshots
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0014_forecast_overrides"
down_revision = "0013_forecast_snapshots"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forecast_overrides",
        sa.Column("id", sa.Integer(), sa.Identity(start=1), primary_key=True),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("quantity", postgresql.DOUBLE_PRECISION, nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_by_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('override', 'withdrawal')",
            name="ck_forecast_overrides_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'override' AND quantity IS NOT NULL AND quantity >= 0) "
            "OR (kind = 'withdrawal' AND quantity IS NULL)",
            name="ck_forecast_overrides_quantity",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_forecast_overrides_reason",
        ),
    )
    op.create_index(
        "ix_forecast_overrides_item_site_period",
        "forecast_overrides",
        ["item_code", "site_code", "period_start", "id"],
    )
    op.execute("""
        CREATE FUNCTION aierp_forecast_overrides_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'forecast override rows are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RAISE EXCEPTION 'forecast override rows cannot be deleted'
                USING ERRCODE = '55000';
        END
        $$ LANGUAGE plpgsql
        """)
    op.execute("""
        CREATE TRIGGER forecast_overrides_guard
        BEFORE UPDATE OR DELETE ON forecast_overrides
        FOR EACH ROW EXECUTE FUNCTION aierp_forecast_overrides_guard()
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS forecast_overrides_guard ON forecast_overrides")
    op.execute("DROP FUNCTION IF EXISTS aierp_forecast_overrides_guard()")
    op.drop_index(
        "ix_forecast_overrides_item_site_period", table_name="forecast_overrides"
    )
    op.drop_table("forecast_overrides")
