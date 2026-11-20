"""Add data_versions: one version counter per input table.

``data_versions`` holds one row per input table of the reorder
recommendations — the eleven tables they read: settings, demand_events,
lead_time_observations, forecast_overrides, demand_signals, item_sites,
on_hand, open_purchase_lines, plan_releases, transfer_releases and
empirical_adoptions.  A row names its table (``table_name`` text
primary key), carries a ``version`` (bigint not null default 0) that
counts the writes to the table since its last reseed, and a
``changed_at`` (timestamptz not null default now()) that records when
the version last moved.

The counters move through one plpgsql function,
``aierp_bump_data_version()``, bound on each of the eleven tables as
an ``AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ... FOR EACH
STATEMENT`` trigger named ``<table>_data_version``.  The statement
trigger updates exactly the one row whose ``table_name`` is
``TG_TABLE_NAME`` — version + 1, changed_at now — in the writer's own
transaction, and it raises when no row answers (so the writer's
transaction aborts, the way the append-only guards abort theirs) and
returns NULL otherwise.  Updating the row in the writer's own
transaction is the point of the one-row-per-table shape: the new
version is visible exactly when the new rows are, a rolled-back write
leaves it unchanged, and two writers of different tables never touch
the same row, so their bumps never contend with each other.

The test suite truncates every table between tests, and without the
rows the next write to an input table would raise.  The reseed trigger
``data_versions_reseed`` (``AFTER TRUNCATE ON data_versions FOR EACH
STATEMENT``, executing ``aierp_reseed_data_versions()``) inserts the
eleven rows again at version 0, so a truncated database still holds a
row for every input table and the next write to one of them bumps
instead of raising.

The downgrade drops the twelve triggers, the two functions and the
table.

The migration creates no database role, changes no table ownership and
grants no privilege: the application keeps using its existing database
user.

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

# The eleven input tables the reorder recommendations read, in the
# order app.db.data_stamp.INPUT_TABLES names them.
INPUT_TABLES = [
    "settings",
    "demand_events",
    "lead_time_observations",
    "forecast_overrides",
    "demand_signals",
    "item_sites",
    "on_hand",
    "open_purchase_lines",
    "plan_releases",
    "transfer_releases",
    "empirical_adoptions",
]

# The VALUES list of every row the table holds: one row per input
# table, at version 0.
_VALUES = ", ".join(f"('{table}')" for table in INPUT_TABLES)


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
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.execute(f"INSERT INTO data_versions (table_name) VALUES {_VALUES}")
    op.execute("""
        CREATE FUNCTION aierp_bump_data_version() RETURNS trigger AS $$
        BEGIN
            UPDATE data_versions
               SET version = version + 1,
                   changed_at = now()
             WHERE table_name = TG_TABLE_NAME;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'no data_versions row for table %',
                    TG_TABLE_NAME USING ERRCODE = '55000';
            END IF;
            RETURN NULL;
        END
        $$ LANGUAGE plpgsql
        """)
    for table in INPUT_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_data_version "
            f"AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ON {table} "
            f"FOR EACH STATEMENT EXECUTE FUNCTION aierp_bump_data_version()"
        )
    op.execute(f"""
        CREATE FUNCTION aierp_reseed_data_versions() RETURNS trigger AS $$
        BEGIN
            INSERT INTO data_versions (table_name)
                VALUES {_VALUES};
            RETURN NULL;
        END
        $$ LANGUAGE plpgsql
        """)
    op.execute(
        "CREATE TRIGGER data_versions_reseed "
        "AFTER TRUNCATE ON data_versions "
        "FOR EACH STATEMENT EXECUTE FUNCTION aierp_reseed_data_versions()"
    )


def downgrade() -> None:
    for table in INPUT_TABLES:
        op.execute(f"DROP TRIGGER IF EXISTS {table}_data_version ON {table}")
    op.execute("DROP TRIGGER IF EXISTS data_versions_reseed ON data_versions")
    op.execute("DROP FUNCTION IF EXISTS aierp_bump_data_version()")
    op.execute("DROP FUNCTION IF EXISTS aierp_reseed_data_versions()")
    op.drop_table("data_versions")
