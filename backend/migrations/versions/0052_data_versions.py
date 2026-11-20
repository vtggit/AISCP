"""Add data_versions: one data-version row per input table (issue #1450).

``data_versions`` holds one row per input table, at version 0: the ten
tables the ERP syncs write — inventory_items, item_sites, demand_events,
lead_time_observations, on_hand, open_purchase_lines, suppliers,
open_sales_lines, supplier_terms and erp_source_currencies — and
demand_signals, the planner-entered demand lifts.  ``version`` is a
bigint, 0 by default; ``changed_at`` is a timestamptz, now() by default;
both move only when a statement writes the table.

Every input table carries one statement trigger,
``<table>_data_version``, AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE,
that executes ``aierp_bump_data_version()`` in the writer's own
transaction: the row for the table is updated by the very statement that
wrote the table.  That is why the per-table row is updated in the
writer's own transaction — the new version is visible exactly when the
new rows are (a reader that sees the bumped version sees the write, and
one that does not sees neither), a rolled-back write leaves the version
unchanged along with its rows, and two writers of different tables never
touch the same row, so their bumps cannot race or block one another.

A tracked table whose trigger fires with no row in ``data_versions`` is
a broken install, not a quiet no-op: the function raises and aborts the
writer's transaction instead of letting a write go uncounted.  To keep
that loud failure honest, the bookkeeping table keeps its own row per
input table even across a TRUNCATE of itself: ``data_versions_reseed``,
AFTER TRUNCATE on ``data_versions``, re-inserts the eleven rows at
version 0 in the same transaction, so a cleanup that empties the
bookkeeping table and the input tables in any order never leaves an
input trigger without the row to bump.

The downgrade drops the eleven triggers, the reseed trigger, the
functions and the table.  The migration creates no database role,
changes no table ownership and grants no privilege: the application
keeps using its existing database user.

Revision ID: 0052_data_versions
Revises: 0051_purchase_budget_proposals
Create Date: 2026-11-20
"""

import sqlalchemy as sa
from alembic import op

revision = "0052_data_versions"
down_revision = "0051_purchase_budget_proposals"
branch_labels = None
depends_on = None

# The eleven input tables, in the order the migration creates their
# version rows and triggers (the same order data_stamp reports them).
_INPUT_TABLES = (
    "inventory_items",
    "item_sites",
    "demand_events",
    "lead_time_observations",
    "on_hand",
    "open_purchase_lines",
    "suppliers",
    "open_sales_lines",
    "supplier_terms",
    "demand_signals",
    "erp_source_currencies",
)


def upgrade() -> None:
    op.create_table(
        "data_versions",
        sa.Column("table_name", sa.Text(), primary_key=True),
        sa.Column(
            "version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    # One row per input table, at version 0.
    op.execute(
        "INSERT INTO data_versions (table_name) VALUES "
        + ", ".join(f"('{table}')" for table in _INPUT_TABLES)
    )
    op.execute("""
        CREATE FUNCTION aierp_bump_data_version() RETURNS trigger AS $$
        DECLARE
            bumped BIGINT;
        BEGIN
            UPDATE data_versions
            SET version = version + 1, changed_at = now()
            WHERE table_name = TG_TABLE_NAME;
            GET DIAGNOSTICS bumped = ROW_COUNT;
            IF bumped = 0 THEN
                RAISE EXCEPTION 'data_versions has no row for %', TG_TABLE_NAME
                    USING ERRCODE = '55000';
            END IF;
            RETURN NULL;
        END
        $$ LANGUAGE plpgsql
        """)
    op.execute("""
        CREATE FUNCTION aierp_reseed_data_versions() RETURNS trigger AS $$
        BEGIN
            INSERT INTO data_versions (table_name) VALUES
                ('inventory_items'), ('item_sites'), ('demand_events'),
                ('lead_time_observations'), ('on_hand'), ('open_purchase_lines'),
                ('suppliers'), ('open_sales_lines'), ('supplier_terms'),
                ('demand_signals'), ('erp_source_currencies');
            RETURN NULL;
        END
        $$ LANGUAGE plpgsql
        """)
    for _table in _INPUT_TABLES:
        op.execute(
            f"CREATE TRIGGER {_table}_data_version "
            "AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON "
            f"{_table} FOR EACH STATEMENT EXECUTE FUNCTION "
            "aierp_bump_data_version()"
        )
    op.execute("""
        CREATE TRIGGER data_versions_reseed
        AFTER TRUNCATE ON data_versions
        FOR EACH STATEMENT EXECUTE FUNCTION aierp_reseed_data_versions()
        """)


def downgrade() -> None:
    for _table in reversed(_INPUT_TABLES):
        op.execute(f"DROP TRIGGER IF EXISTS {_table}_data_version ON {_table}")
    op.execute("DROP TRIGGER IF EXISTS data_versions_reseed ON data_versions")
    op.execute("DROP FUNCTION IF EXISTS aierp_bump_data_version()")
    op.execute("DROP FUNCTION IF EXISTS aierp_reseed_data_versions()")
    op.drop_table("data_versions")
