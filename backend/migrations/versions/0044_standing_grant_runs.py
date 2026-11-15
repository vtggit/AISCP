"""Add standing_grant_runs: the audit of the standing-grant runs.

A standing-grant run is one execution of a release process under a
standing grant (issue #1198: the run of the order plan —
``app.supply.standing_release.run_order_plan_grant``): one row per
run, ``process`` (text not null, the process the run executed, CHECK
that it is one of ``order_plan_release`` or ``transfer_release`` —
the same two names the grants cover), ``grant_id`` (bigint not null,
the id of the grant in force the run ran under, a foreign key to
``erp_standing_grants(id)`` — the grant is never deleted, so the key
never dangles), ``started_at`` (TIMESTAMPTZ not null, the aware UTC
time the run was started, ``now()`` server default), ``outcome``
(text not null, one of ``released`` — the chosen groups went out,
``nothing_due`` — the plan had no groups, ``skipped`` — the data was
stale or every due document sat over a limit, or ``failed`` — the
send refused or raised), ``reason`` (text null, why the run did not
release — the skip's or the failure's reason, null for a released
run and a run with nothing due), ``documents`` (integer not null
default 0, ``documents >= 0``, the count of the groups sent — 0
while none were), ``value`` (numeric not null default 0,
``value >= 0``, the summed value of the groups sent — 0 while none
were) and ``left_out`` (jsonb not null default an empty JSON array,
the groups the run's limits left out, each ``{"site_code",
"supplier_code", "value", "reason"}`` with the value a decimal
string).

A non-unique index over ``(process, started_at)`` serves the day's
total: the summed ``value`` of one process's runs whose
``started_at`` falls on one UTC date.  The downgrade drops the
table.

The migration creates no database role, changes no table ownership
and grants no privilege: the application keeps using its existing
database user.

Revision ID: 0044_standing_grant_runs
Revises: 0043_erp_standing_grants
Create Date: 2026-11-14
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_standing_grant_runs"
down_revision = "0043_erp_standing_grants"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "standing_grant_runs",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True),
        sa.Column("process", sa.Text(), nullable=False),
        sa.Column(
            "grant_id",
            sa.BigInteger(),
            sa.ForeignKey("erp_standing_grants.id"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "documents", sa.Integer(), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("value", sa.Numeric(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "left_out",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "process IN ('order_plan_release', 'transfer_release')",
            name="ck_standing_grant_runs_process",
        ),
        sa.CheckConstraint(
            "outcome IN ('released', 'nothing_due', 'skipped', 'failed')",
            name="ck_standing_grant_runs_outcome",
        ),
        sa.CheckConstraint("documents >= 0", name="ck_standing_grant_runs_documents"),
        sa.CheckConstraint("value >= 0", name="ck_standing_grant_runs_value"),
    )
    op.create_index(
        "ix_standing_grant_runs_process_started_at",
        "standing_grant_runs",
        ["process", "started_at"],
    )


def downgrade() -> None:
    op.drop_table("standing_grant_runs")
