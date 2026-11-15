"""Add demand_signals: the stored demand signals and their withdrawals.

A demand signal records a promotion, deal or event that lifts demand for
one item-site over a date window (start_date through end_date,
inclusive) by a quantity of units.  Signals are append-only: the
``demand_signals_guard`` trigger refuses every UPDATE and DELETE on the
table, the way ``forecast_overrides`` guards its table.  A withdrawal
is a new row (kind ``withdrawal``, null quantity, ``withdraws_id`` the
withdrawn signal's id) carrying the signal's item-site and dates; the
signal's rows stay in the history.

``kind`` is one of ``promotion``, ``deal``, ``event`` or ``withdrawal``
(CHECK).  A withdrawal row has a null quantity and a non-null
``withdraws_id``; every other row has a quantity above 0 and a null
``withdraws_id`` (CHECK).  ``end_date`` is not before ``start_date``
(CHECK).  ``reason`` is not null and not blank after trimming (CHECK)
— it is stored trimmed, at most 1,000 characters.

The two cross-row rules a CHECK cannot express (a CHECK may not look
at another row) are enforced by constraints of equal force: a partial
unique index on ``withdraws_id`` (where not null) lets at most one
row per non-null ``withdraws_id`` exist, so a non-withdrawal signal
cannot be duplicated by two withdrawals; and the guard trigger's
INSERT arm refuses a withdrawal whose ``withdraws_id`` names another
withdrawal (ERRCODE 55000).  The ``withdraws_id`` foreign key to
``demand_signals.id`` keeps the reference live: no signal row is ever
deleted or changed, so a stored withdrawal always names the signal it
withdrew.  An index covers (item_code, site_code, start_date).

``created_by`` is the caller's token subject; ``created_by_name`` is
the caller's display name as the decision tables store it (nullable:
null when the token carries no display name).  ``id`` is an integer
identity primary key and ``created_at`` is ``TIMESTAMPTZ`` not null
with a ``now()`` server default, so ``id DESC`` lists rows
deterministically.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0037_demand_signals
Revises: 0036_sop_signoffs
Create Date: 2026-11-03
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0037_demand_signals"
down_revision = "0036_sop_signoffs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "demand_signals",
        sa.Column("id", sa.Integer(), sa.Identity(start=1), primary_key=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("quantity", postgresql.DOUBLE_PRECISION, nullable=True),
        sa.Column("withdraws_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("created_by_name", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["withdraws_id"],
            ["demand_signals.id"],
            name="fk_demand_signals_withdraws_id",
        ),
        sa.CheckConstraint(
            "kind IN ('promotion', 'deal', 'event', 'withdrawal')",
            name="ck_demand_signals_kind",
        ),
        sa.CheckConstraint(
            "(kind = 'withdrawal' AND quantity IS NULL AND withdraws_id IS NOT NULL) "
            "OR (kind <> 'withdrawal' AND quantity > 0 AND withdraws_id IS NULL)",
            name="ck_demand_signals_shape",
        ),
        sa.CheckConstraint(
            "end_date >= start_date",
            name="ck_demand_signals_dates",
        ),
        sa.CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_demand_signals_reason",
        ),
    )
    op.create_index(
        "ix_demand_signals_item_site_start",
        "demand_signals",
        ["item_code", "site_code", "start_date"],
    )
    # A non-withdrawal signal can be withdrawn at most once: at most
    # one row per non-null withdraws_id.
    op.execute(
        "CREATE UNIQUE INDEX uq_demand_signals_withdraws "
        "ON demand_signals (withdraws_id) WHERE withdraws_id IS NOT NULL"
    )
    op.execute("""
        CREATE FUNCTION aierp_demand_signals_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.kind = 'withdrawal' AND NEW.withdraws_id IS NOT NULL
                   AND EXISTS (
                       SELECT 1 FROM demand_signals w
                       WHERE w.id = NEW.withdraws_id AND w.kind = 'withdrawal'
                   ) THEN
                    RAISE EXCEPTION 'a demand signal withdrawal cannot '
                        'withdraw another withdrawal'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'demand signal rows are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RAISE EXCEPTION 'demand signal rows cannot be deleted'
                USING ERRCODE = '55000';
        END
        $$ LANGUAGE plpgsql
        """)
    op.execute("""
        CREATE TRIGGER demand_signals_guard
        BEFORE INSERT OR UPDATE OR DELETE ON demand_signals
        FOR EACH ROW EXECUTE FUNCTION aierp_demand_signals_guard()
        """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS demand_signals_guard ON demand_signals")
    op.execute("DROP FUNCTION IF EXISTS aierp_demand_signals_guard()")
    op.execute("DROP INDEX IF EXISTS uq_demand_signals_withdraws")
    op.drop_index("ix_demand_signals_item_site_start", table_name="demand_signals")
    op.drop_table("demand_signals")
