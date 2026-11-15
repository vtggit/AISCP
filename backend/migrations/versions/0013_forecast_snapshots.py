"""Add forecast_snapshots: one row per item-site per forecast bucket.

A forecast snapshot captures, for one as-of date, exactly the forecast
buckets ``GET /api/forecast`` returns for that date: one row per
item-site that has a forecast, one per each of its twelve 30-day
horizon buckets.  ``origin`` is ``live`` when the capture used the
shared default as-of date and ``backfill`` when the caller named the
date explicitly.  A date gets at most one snapshot: the first
snapshot of a date is never updated or deleted, whatever its origin,
and any later insert for the same as-of date is rejected by the
``forecast_snapshots_guard`` trigger (rows written by an earlier,
different transaction are the marker of the first snapshot — the
inserting transaction's own rows share its xid, so a snapshot's own
batch insert never collides with itself).

``forecast_snapshot_records`` stores the snapshot-level record: one
row per snapshot (per as-of date) carrying ``origin``,
``item_sites``, ``rows`` and ``created_at``.  The record is written
in the same transaction as the snapshot's bucket rows and is the
source of the snapshot-level fields the API answers — they are read
from the stored record, not derived from the bucket rows.  Its
``forecast_snapshot_records_guard`` trigger enforces the same
one-snapshot-per-date rule and immutability as the bucket table's.

Revision ID: 0013_forecast_snapshots
Revises: 0012_display_names
Create Date: 2026-10-13
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_forecast_snapshots"
down_revision = "0012_display_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "forecast_snapshots",
        sa.Column("id", sa.String(length=64), primary_key=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("item_code", sa.String(length=64), nullable=False),
        sa.Column("site_code", sa.String(length=64), nullable=False),
        sa.Column("bucket_index", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.Date(), nullable=False),
        sa.Column("bucket_end", sa.Date(), nullable=False),
        sa.Column("quantity", postgresql.DOUBLE_PRECISION, nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("demand_class", sa.String(length=16), nullable=False),
        sa.Column("history_buckets", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "origin IN ('live', 'backfill')",
            name="ck_forecast_snapshots_origin",
        ),
        sa.CheckConstraint(
            "bucket_index >= 1 AND bucket_index <= 12",
            name="ck_forecast_snapshots_bucket_index",
        ),
        sa.UniqueConstraint(
            "as_of",
            "item_code",
            "site_code",
            "bucket_index",
            name="uq_forecast_snapshots_as_of_item_site_bucket",
        ),
    )
    op.create_index("ix_forecast_snapshots_as_of", "forecast_snapshots", ["as_of"])
    op.execute("""
        CREATE FUNCTION aierp_forecast_snapshot_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF EXISTS (
                    SELECT 1 FROM forecast_snapshots fs
                    WHERE fs.as_of = NEW.as_of
                      AND fs.xmin::text <> txid_current()::text
                ) THEN
                    RAISE EXCEPTION 'a forecast snapshot already exists for as_of %', NEW.as_of
                        USING ERRCODE = '23505';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'forecast snapshot rows are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RAISE EXCEPTION 'forecast snapshot rows cannot be deleted'
                USING ERRCODE = '55000';
        END
        $$ LANGUAGE plpgsql
        """)
    op.execute("""
        CREATE TRIGGER forecast_snapshots_guard
        BEFORE INSERT OR UPDATE OR DELETE ON forecast_snapshots
        FOR EACH ROW EXECUTE FUNCTION aierp_forecast_snapshot_guard()
        """)
    op.create_table(
        "forecast_snapshot_records",
        sa.Column("as_of", sa.Date(), primary_key=True),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("item_sites", sa.Integer(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "origin IN ('live', 'backfill')",
            name="ck_forecast_snapshot_records_origin",
        ),
    )
    op.execute("""
        CREATE FUNCTION aierp_forecast_snapshot_record_guard() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF EXISTS (
                    SELECT 1 FROM forecast_snapshots fs
                    WHERE fs.as_of = NEW.as_of
                      AND fs.xmin::text <> txid_current()::text
                ) THEN
                    RAISE EXCEPTION 'a forecast snapshot already exists for as_of %', NEW.as_of
                        USING ERRCODE = '23505';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'UPDATE' THEN
                RAISE EXCEPTION 'forecast snapshot records are immutable'
                    USING ERRCODE = '55000';
            END IF;
            RAISE EXCEPTION 'forecast snapshot records cannot be deleted'
                USING ERRCODE = '55000';
        END
        $$ LANGUAGE plpgsql
        """)
    op.execute("""
        CREATE TRIGGER forecast_snapshot_records_guard
        BEFORE INSERT OR UPDATE OR DELETE ON forecast_snapshot_records
        FOR EACH ROW EXECUTE FUNCTION aierp_forecast_snapshot_record_guard()
        """)


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS forecast_snapshot_records_guard "
        "ON forecast_snapshot_records"
    )
    op.execute("DROP FUNCTION IF EXISTS aierp_forecast_snapshot_record_guard()")
    op.drop_table("forecast_snapshot_records")
    op.execute("DROP TRIGGER IF EXISTS forecast_snapshots_guard ON forecast_snapshots")
    op.execute("DROP FUNCTION IF EXISTS aierp_forecast_snapshot_guard()")
    op.drop_index("ix_forecast_snapshots_as_of", table_name="forecast_snapshots")
    op.drop_table("forecast_snapshots")
