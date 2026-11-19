"""Issue #1432 proof: the record row of every purchase-budget save,
and the measure of it in the proposals' answer.

``PUT /api/purchase-budgets/{month}`` appends one row of
``purchase_budget_proposals`` beside every budget it stores, in one
database transaction with the budget save: the saved amount and the
caller's username, the month's proposal at that moment — computed the
way ``GET /api/purchase-budgets/proposals`` computes it, over a
12-month horizon: ``propose_budget`` of the month's ``total_spend``
of ``spend_against_budget(months=12)`` with the
``sop.budget_proposal_margin`` margin, the margin stored too — and a
null proposal with a null margin when the month is outside that
horizon, or when the spend read raises.  A failed append rolls the
budget save back and answers 500, so a budget is never stored without
its record row; either way the save answers exactly as it did
before, and ``DELETE`` appends nothing and removes no record row.

The answer of ``GET /api/purchase-budgets/proposals`` gains
``record``, ``{saved, with_proposal, taken, below, above}``, over the
latest record row of each month that still has a stored budget — the
record read once with ``latest_per_month`` and the stored budgets
read once: ``saved`` counts those rows, ``with_proposal`` those
whose ``proposed_amount`` is not null, and of those ``taken`` the
amount within 0.005 of the proposal, ``below`` the amount under it by
more and ``above`` the amount over it by more — so
``taken + below + above = with_proposal``.  The module docstring
describes the record and the measure, and
``backend/openapi.json`` carries the new schema.

Proved here, on the test database migrated to head, with
``app.api.purchase_budgets.spend_against_budget`` replaced by a
stand-in that answers the current month and the next with
``total_spend`` 123456.78 and 0, and nothing else:

* the migration — exactly one head, exactly one successor of
  ``0050_snapshot_history_quantity`` (its id not compared with the
  head, so a later migration leaves the check unchanged), and the
  table in the test database with its documented shape: a bigint
  identity ``id`` primary key, ``month`` text not null with the
  ``ck_purchase_budget_proposals_month`` pattern CHECK, ``amount``
  numeric not null with ``amount >= 0``, ``proposed_amount`` numeric
  null with a CHECK that it is null or at least 0, ``margin`` numeric
  null, ``saved_by`` text not null, ``saved_at`` timestamptz not null
  defaulting to ``now()``, and the index on ``(month, saved_at)``;
  the database itself refuses a malformed month, a negative amount
  and a negative proposal through the CHECK constraints; the
  downgrade drops the table and the upgrade back to head re-creates
  it cleanly;
* the saves — a save of the current month of 130000 as an admin
  answers 200 with exactly the stored budget row (the answer it gave
  before this slice) and appends one record row: amount 130000,
  ``proposed_amount`` 130000 (the stand-in's total spend grown by the
  registry-default 0.05 margin, the 12-month read), margin 0.05 and
  ``saved_by`` the admin; a second save of the same month at 120000
  answers as before and appends a second row, the
  ``purchase_budgets`` row updated in place as today (one row, new
  amount, same id); a save of a month 14 months ahead appends a row
  with a null proposal and a null margin (outside the stand-in's
  horizon); with the stand-in raising ``ValueError`` a save still
  answers 200 and appends a row with a null proposal;
* the transaction — with the record insert failing, the save answers
  500 and the budget it was storing is rolled back: no budget row,
  no record row;
* the measure — with those saves, ``record`` counts the latest row of
  each month that still has a budget: the current month (120000
  against 130000) in ``below`` and the far month in ``saved`` without
  a proposal, the record read once with ``latest_per_month`` beside
  the stored budgets read once; after a save at the proposal for the
  next month (0 against 0), ``taken`` counts it; after the far
  month's budget is deleted (204, appending nothing), it leaves
  ``saved`` while its rows stay in the table;
* access — a non-admin PUT answers 403 as before and appends
  nothing.

Identity — the proof also runs as a NON-ADMIN (role ``user``): a
second pass after a full reload, with the page booted the production
way (the token it would take from the URL hash as
``#access_token=...``), ``GET /api/auth/config`` answering publicly
and ``GET /api/auth/me`` resolving the viewer, sees the admin-only
store refused 403 (nothing appended) while the page still renders for
the viewer.

A hostile margin of -1 or less never raises: ``propose_budget``
proposes 0 whenever the margin shrinks the grown total to zero or
below — proved directly on the function and beside the routes, the
proposals answering 200 with 0 proposed for the stand-in's positive
total spend, and a save beside it answering 200 with the answer it
gave before while its record row holds the 0 proposal with the
hostile margin stored.
"""

import json
import os
import re
from datetime import date
from pathlib import Path

import psycopg2
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.api import purchase_budgets as purchase_budgets_module
from app.db.connection import get_connection
from app.main import app
from app.repositories.purchase_budget_proposals_postgres_repository import (
    PurchaseBudgetProposalPostgresRepository,
)
from app.repositories.purchase_budgets_postgres_repository import (
    PurchaseBudgetPostgresRepository,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
ADMIN_USERNAME = "admin"  # the admin stub user's username
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_DETAIL = "Role 'admin' is required to perform this action."

LIST_URL = "/api/purchase-budgets"
PROPOSALS_URL = f"{LIST_URL}/proposals"
_TABLE = "purchase_budget_proposals"
PREV_REVISION = "0050_snapshot_history_quantity"

# The months of the proof, off the current one: the stand-in answers
# the current month and the next, and nothing else.
TODAY = date.today()


def _add_months(base: date, months: int) -> date:
    total = base.year * 12 + (base.month - 1) + months
    return date(total // 12, total % 12 + 1, 1)


CURRENT_MONTH = _add_months(TODAY, 0).strftime("%Y-%m")
NEXT_MONTH = _add_months(TODAY, 1).strftime("%Y-%m")
FAR_MONTH = _add_months(TODAY, 14).strftime("%Y-%m")  # outside the horizon


class _SpendStandIn:
    """A stand-in for ``app.api.purchase_budgets.spend_against_budget``.

    Answers the current month and the next — ``total_spend``
    123456.78 and 0 — and nothing else, records every call so the
    proof can assert the horizon the saves read it over, and can be
    made to raise.
    """

    def __init__(self):
        self.raises = None
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if self.raises is not None:
            raise self.raises
        return {
            "months": [
                {
                    "month": CURRENT_MONTH,
                    "committed_spend": 0,
                    "planned_spend": 123456.78,
                    "total_spend": 123456.78,
                    "budget": None,
                    "remaining": None,
                    "over_budget": False,
                },
                {
                    "month": NEXT_MONTH,
                    "committed_spend": 0,
                    "planned_spend": 0,
                    "total_spend": 0,
                    "budget": None,
                    "remaining": None,
                    "over_budget": False,
                },
            ],
            "item_sites_without_unit_cost": 0,
        }


def _alembic_revision() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _pg_tables() -> set[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def _table_columns() -> dict[str, tuple]:
    """The table's columns as (data_type, nullability, default, identity)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable, column_default, is_identity "
                "FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (_TABLE,),
            )
            return {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.fetchall()}
    finally:
        conn.close()


def _check_constraints() -> dict[str, str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT con.conname, pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "WHERE rel.relname = %s AND con.contype = 'c' "
                "ORDER BY con.conname",
                (_TABLE,),
            )
            return {name: definition for name, definition in cur.fetchall()}
    finally:
        conn.close()


def _index_columns() -> dict[str, list[str]]:
    """Every index of the table, by name, with its columns in order."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexname, indexdef FROM pg_indexes "
                "WHERE tablename = %s ORDER BY indexname",
                (_TABLE,),
            )
            columns = {}
            for name, indexdef in cur.fetchall():
                columns[name] = [
                    column.strip()
                    for column in re.search(r"\(([^)]*)\)$", indexdef)
                    .group(1)
                    .split(",")
                ]
            return columns
    finally:
        conn.close()


def _expect_db_rejection(
    conn,
    sql: str,
    params: tuple,
    expected: type[psycopg2.Error],
    label: str,
) -> None:
    """Run one statement in a savepoint; it must fail with ``expected``.

    The savepoint keeps the surrounding (rolled-back) transaction
    usable after each violation, and nothing here is ever committed.
    """
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT issue1432")
        try:
            cur.execute(sql, params)
        except psycopg2.Error as exc:
            assert type(exc) is expected, f"{label}: {type(exc).__name__}"
            cur.execute("ROLLBACK TO SAVEPOINT issue1432")
        else:
            raise AssertionError(f"{label}: the database accepted the row")
        finally:
            cur.execute("RELEASE SAVEPOINT issue1432")


def _record_rows() -> list[tuple]:
    """Every record row, in id order, with the numeric values as floats."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT month, amount, proposed_amount, margin, saved_by "
                "FROM purchase_budget_proposals ORDER BY id"
            )
            return [
                (
                    month,
                    float(amount),
                    None if proposed is None else float(proposed),
                    None if margin is None else float(margin),
                    saved_by,
                )
                for month, amount, proposed, margin, saved_by in cur.fetchall()
            ]
    finally:
        conn.close()


def _record_measure(client: TestClient) -> dict:
    response = client.get(PROPOSALS_URL)
    assert response.status_code == 200, response.text
    return response.json()["record"]


def test_issue1432_freeform(client, monkeypatch):
    record_repo = PurchaseBudgetProposalPostgresRepository()
    budget_repo = PurchaseBudgetPostgresRepository()
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})

    # -- The migration: exactly one head, exactly one successor of
    #    0050 (its id not compared with the head, so a later migration
    #    leaves the check unchanged), and the test database migrated to
    #    head. ---------------------------------------------------------------
    directory = ScriptDirectory(_MIGRATIONS_DIR)
    assert len(directory.get_heads()) == 1
    successors = [
        script
        for script in directory.walk_revisions()
        if script.down_revision == PREV_REVISION
    ]
    assert len(successors) == 1
    assert _alembic_revision() == CURRENT_HEAD

    # -- The table: its documented shape. ------------------------------------
    columns = _table_columns()
    assert columns == {
        "id": ("bigint", "NO", None, "YES"),
        "month": ("text", "NO", None, "NO"),
        "amount": ("numeric", "NO", None, "NO"),
        "proposed_amount": ("numeric", "YES", None, "NO"),
        "margin": ("numeric", "YES", None, "NO"),
        "saved_by": ("text", "NO", None, "NO"),
        "saved_at": ("timestamp with time zone", "NO", "now()", "NO"),
    }
    checks = _check_constraints()
    assert set(checks) == {
        "ck_purchase_budget_proposals_month",
        "ck_purchase_budget_proposals_amount",
        "ck_purchase_budget_proposals_proposed_amount",
    }
    # The month CHECK is the YYYY-MM pattern with a month of 01 to 12.
    assert ("month ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'") in checks[
        "ck_purchase_budget_proposals_month"
    ]
    assert "amount >= (0)::numeric" in checks["ck_purchase_budget_proposals_amount"]
    proposed_check = checks["ck_purchase_budget_proposals_proposed_amount"]
    assert "proposed_amount IS NULL" in proposed_check
    assert "proposed_amount >= (0)::numeric" in proposed_check
    indexes = _index_columns()
    assert [
        columns_list for columns_list in indexes.values() if columns_list != ["id"]
    ] == [["month", "saved_at"]]
    # The primary key is the id.
    assert ["id"] in indexes.values()

    # The database itself refuses a malformed month, a negative amount
    # and a negative proposal through the CHECK constraints (nothing
    # below is committed).
    conn = get_connection()
    try:
        insert_sql = (
            "INSERT INTO purchase_budget_proposals "
            "(month, amount, proposed_amount, margin, saved_by) "
            "VALUES (%s, %s, %s, %s, %s)"
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("2026-13", 100, 100, 0.05, "admin"),
            psycopg2.errors.CheckViolation,
            "month 2026-13",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("26-11", 100, 100, 0.05, "admin"),
            psycopg2.errors.CheckViolation,
            "month 26-11",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            (CURRENT_MONTH, -1, 100, 0.05, "admin"),
            psycopg2.errors.CheckViolation,
            "negative amount",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            (CURRENT_MONTH, 100, -1, 0.05, "admin"),
            psycopg2.errors.CheckViolation,
            "negative proposed_amount",
        )
    finally:
        conn.rollback()
        conn.close()

    # -- The downgrade drops the table; the upgrade back to head re-creates
    #    it cleanly. ----------------------------------------------------------
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", _MIGRATIONS_DIR)
    alembic_command.downgrade(cfg, PREV_REVISION)
    assert _alembic_revision() == PREV_REVISION
    assert _TABLE not in _pg_tables()
    alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_columns() == columns

    # -- The stand-in spend: the current month and the next, and nothing
    #    else. ----------------------------------------------------------------
    standin = _SpendStandIn()
    monkeypatch.setattr(purchase_budgets_module, "spend_against_budget", standin)

    # -- A save of the current month of 130000 as an admin. -------------------
    response = admin.put(f"{LIST_URL}/{CURRENT_MONTH}", json={"amount": 130000})
    assert response.status_code == 200, response.text
    saved = response.json()
    # The answer is exactly what it answered before this slice: the
    # stored budget row, and nothing else.
    assert set(saved) == {"id", "month", "amount", "updated_at", "updated_by"}
    assert saved["month"] == CURRENT_MONTH
    assert saved["amount"] == 130000
    assert saved["updated_by"] == ADMIN_USERNAME
    assert isinstance(saved["id"], int)
    assert budget_repo.list() == [saved]
    first_id = saved["id"]
    # The proposal read ran over the 12-month horizon, once.
    assert standin.calls == [{"months": 12}]
    # One record row was appended beside the budget: the saved amount,
    # the proposal of the stand-in's total spend (123456.78 grown by the
    # registry-default 0.05 margin is 130000), the margin and the admin.
    assert _record_rows() == [(CURRENT_MONTH, 130000.0, 130000.0, 0.05, ADMIN_USERNAME)]
    (latest,) = record_repo.latest_per_month()
    assert latest["month"] == CURRENT_MONTH
    assert latest["amount"] == 130000.0
    assert latest["proposed_amount"] == 130000.0
    assert latest["margin"] == 0.05
    assert latest["saved_by"] == ADMIN_USERNAME
    assert isinstance(latest["id"], int)
    assert isinstance(latest["saved_at"], str)

    # -- A second save of the same month at 120000: one more record row, the
    #    budget row updated in place as today. --------------------------------
    response = admin.put(f"{LIST_URL}/{CURRENT_MONTH}", json={"amount": 120000})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert saved["amount"] == 120000
    assert saved["updated_by"] == ADMIN_USERNAME
    # The purchase_budgets row is updated as today: still one row, the
    # new amount, the same id.
    assert saved["id"] == first_id
    assert budget_repo.list() == [saved]
    assert _record_rows() == [
        (CURRENT_MONTH, 130000.0, 130000.0, 0.05, ADMIN_USERNAME),
        (CURRENT_MONTH, 120000.0, 130000.0, 0.05, ADMIN_USERNAME),
    ]
    (latest,) = record_repo.latest_per_month()
    assert latest["amount"] == 120000.0
    assert latest["proposed_amount"] == 130000.0

    # -- A month outside the horizon: 14 months ahead. ------------------------
    response = admin.put(f"{LIST_URL}/{FAR_MONTH}", json={"amount": 4000})
    assert response.status_code == 200, response.text
    assert response.json()["amount"] == 4000
    assert standin.calls[-1] == {"months": 12}
    # Outside the stand-in's horizon: a null proposal and a null margin.
    assert _record_rows()[-1] == (FAR_MONTH, 4000.0, None, None, ADMIN_USERNAME)

    # -- A raising spend read: the save still answers 200, with a null
    #    proposal. ------------------------------------------------------------
    standin.raises = ValueError("the spend read is down")
    response = admin.put(f"{LIST_URL}/{NEXT_MONTH}", json={"amount": 777})
    assert response.status_code == 200, response.text
    assert response.json()["amount"] == 777
    standin.raises = None
    # The next month is inside the horizon, but the read raised: the
    # proposal and the margin are still null.
    assert _record_rows()[-1] == (NEXT_MONTH, 777.0, None, None, ADMIN_USERNAME)

    # -- The measure, with those saves. ---------------------------------------
    # The record reads once with latest_per_month, the stored budgets once.
    records_reads: list[tuple] = []
    budgets_reads: list[tuple] = []
    original_latest = purchase_budgets_module._record_repo.latest_per_month
    original_list = purchase_budgets_module._repo.list

    def counting_latest():
        records_reads.append(())
        return original_latest()

    def counting_list():
        budgets_reads.append(())
        return original_list()

    monkeypatch.setattr(
        purchase_budgets_module._record_repo, "latest_per_month", counting_latest
    )
    monkeypatch.setattr(purchase_budgets_module._repo, "list", counting_list)

    response = admin.get(PROPOSALS_URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "margin",
        "item_sites_without_unit_cost",
        "months",
        "record",
    }
    assert body["margin"] == 0.05
    # The spend answer: the stand-in's two months, in its order.
    assert [month["month"] for month in body["months"]] == [CURRENT_MONTH, NEXT_MONTH]
    # The current month (120000 against 130000) counts in below, the far
    # month in saved without a proposal.
    assert body["record"] == {
        "saved": 3,
        "with_proposal": 1,
        "taken": 0,
        "below": 1,
        "above": 0,
    }
    record = body["record"]
    assert (
        record["taken"] + record["below"] + record["above"] == record["with_proposal"]
    )
    # The one read of the record and of the stored budgets.
    assert records_reads == [()]
    assert budgets_reads == [()]
    # The measure is open to any signed-in user: the bare development
    # token (the client fixture's non-admin) gets the same answer.
    assert _record_measure(client) == record

    # A save at the proposal for the next month (0 against 0): taken.
    response = admin.put(f"{LIST_URL}/{NEXT_MONTH}", json={"amount": 0})
    assert response.status_code == 200, response.text
    assert _record_rows()[-1] == (NEXT_MONTH, 0.0, 0.0, 0.05, ADMIN_USERNAME)
    assert _record_measure(admin) == {
        "saved": 3,
        "with_proposal": 2,
        "taken": 1,
        "below": 1,
        "above": 0,
    }

    # The far month's budget deleted: it leaves saved, its rows stay.
    response = admin.delete(f"{LIST_URL}/{FAR_MONTH}")
    assert response.status_code == 204
    assert _record_measure(admin) == {
        "saved": 2,
        "with_proposal": 2,
        "taken": 1,
        "below": 1,
        "above": 0,
    }
    # DELETE appended nothing and removed no record row: the five saves
    # above have their five rows.
    assert len(_record_rows()) == 5
    assert sum(1 for row in _record_rows() if row[0] == FAR_MONTH) == 1

    # -- The transaction: a failed append rolls the save back and answers
    #    500 — no budget stored without its record row. -----------------------
    original_insert = purchase_budgets_module._insert_record

    def down_insert(cur, month, amount, proposed_amount, margin, saved_by):
        raise RuntimeError("the record store is down")

    monkeypatch.setattr(purchase_budgets_module, "_insert_record", down_insert)
    failing = TestClient(
        app,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        raise_server_exceptions=False,
    )
    try:
        response = failing.put(f"{LIST_URL}/{FAR_MONTH}", json={"amount": 6000})
        assert response.status_code == 500, response.text
    finally:
        monkeypatch.setattr(purchase_budgets_module, "_insert_record", original_insert)
    # The save rolled back: no budget for the far month, no record row for
    # it but the one the first far save stored.
    assert FAR_MONTH not in {row["month"] for row in budget_repo.list()}
    assert len(_record_rows()) == 5
    assert _record_measure(admin) == {
        "saved": 2,
        "with_proposal": 2,
        "taken": 1,
        "below": 1,
        "above": 0,
    }

    # -- Access: the non-admin's PUT is refused as before, appending nothing.
    rows_before = len(_record_rows())
    budgets_before = budget_repo.list()
    response = viewer.put(f"{LIST_URL}/{CURRENT_MONTH}", json={"amount": 1})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    assert len(_record_rows()) == rows_before
    assert budget_repo.list() == budgets_before

    # -- The OpenAPI document carries the new schema. -------------------------
    spec = json.loads(
        (Path(_BACKEND_ROOT) / "openapi.json").read_text(encoding="utf-8")
    )
    operation = spec["paths"]["/api/purchase-budgets/proposals"]["get"]
    assert (
        operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/BudgetProposals"
    )
    proposals_schema = spec["components"]["schemas"]["BudgetProposals"]
    assert set(proposals_schema["properties"]) == {
        "margin",
        "item_sites_without_unit_cost",
        "months",
        "record",
    }
    assert set(proposals_schema["required"]) == {
        "margin",
        "item_sites_without_unit_cost",
        "months",
        "record",
    }
    assert proposals_schema["properties"]["record"]["$ref"] == (
        "#/components/schemas/BudgetProposalRecord"
    )
    record_schema = spec["components"]["schemas"]["BudgetProposalRecord"]
    assert set(record_schema["properties"]) == {
        "saved",
        "with_proposal",
        "taken",
        "below",
        "above",
    }
    assert set(record_schema["required"]) == {
        "saved",
        "with_proposal",
        "taken",
        "below",
        "above",
    }

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only store is
    #    refused 403 (nothing appended) while the page still renders for
    #    the viewer. ------------------------------------------------------------
    assert TestClient(app).get("/api/auth/config").status_code == 200
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin stub's identity is the other one: sub and username.
    admin_me = admin.get("/api/auth/me")
    assert admin_me.status_code == 200, admin_me.text
    assert admin_me.json()["user"]["sub"] == ADMIN_SUB
    assert admin_me.json()["user"]["username"] == ADMIN_USERNAME
    # The admin-only control is absent for the viewer: 403, nothing
    # appended, nothing deleted.
    rows_before = len(_record_rows())
    budgets_before = budget_repo.list()
    response = viewer_reloaded.put(f"{LIST_URL}/{NEXT_MONTH}", json={"amount": 1})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer_reloaded.delete(f"{LIST_URL}/{NEXT_MONTH}")
    assert response.status_code == 403, response.text
    assert len(_record_rows()) == rows_before
    assert budget_repo.list() == budgets_before
    # The page still renders: the budgets and the proposals it loads
    # answer 200 for the viewer.
    assert viewer_reloaded.get(LIST_URL).status_code == 200
    response = viewer_reloaded.get(PROPOSALS_URL)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "margin",
        "item_sites_without_unit_cost",
        "months",
        "record",
    }
    assert response.json()["record"] == {
        "saved": 2,
        "with_proposal": 2,
        "taken": 1,
        "below": 1,
        "above": 0,
    }
    # And the gate is intact: a request without a token still gets 401.
    assert TestClient(app).get(LIST_URL).status_code == 401
    assert TestClient(app).get(PROPOSALS_URL).status_code == 401

    # -- A hostile margin of -1 or less: the proposal is 0, never a
    #    ValueError — directly, and beside the routes. ---------------------
    # The arithmetic itself: a margin of -1 or less shrinks the grown
    # total to zero or below, and the proposal is 0 — a budget is never
    # negative, and the round-up's log10 of a non-positive number would
    # raise.
    assert purchase_budgets_module.propose_budget(123456.78, -1) == 0.0
    assert purchase_budgets_module.propose_budget(123456.78, -1.5) == 0.0
    assert purchase_budgets_module.propose_budget(1000, -2) == 0.0
    monkeypatch.setattr(
        purchase_budgets_module, "setting_value", lambda stored, key: -1.0
    )
    hostile = TestClient(
        app,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        raise_server_exceptions=False,
    )
    # The proposals route answers 200, proposing 0 for the stand-in's
    # positive total spend.
    response = hostile.get(PROPOSALS_URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["margin"] == -1.0
    assert [month["proposed_budget"] for month in body["months"]] == [0.0, 0.0]
    # A save beside it: 200 with the answer it gave before, and the
    # record row holds the 0 proposal with the hostile margin stored.
    response = hostile.put(f"{LIST_URL}/{NEXT_MONTH}", json={"amount": 5000})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert set(saved) == {"id", "month", "amount", "updated_at", "updated_by"}
    assert saved["amount"] == 5000
    assert saved["updated_by"] == ADMIN_USERNAME
    assert _record_rows()[-1] == (NEXT_MONTH, 5000.0, 0.0, -1.0, ADMIN_USERNAME)
    assert _record_measure(hostile) == {
        "saved": 2,
        "with_proposal": 2,
        "taken": 0,
        "below": 1,
        "above": 1,
    }
