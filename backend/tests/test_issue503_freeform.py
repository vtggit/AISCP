"""Issue #503 proof: per-month purchase budgets behind a Bearer-gated API.

Everything is exercised against the test database through the app's
test client with the two development stub users: the admin stub
(``dev-secret-token:admin``, sub ``dev-admin-1``, roles admin+user,
username ``admin``) stores and deletes budgets, and the non-admin stub
(role ``user``, sub ``dev-user-1``, username ``developer``) reads them
and gets refused the mutations.

Proved here:

  * the migration — ``0020_purchase_budgets`` (revision after
    ``0019_draft_po_actions_targets``) creates ``purchase_budgets``
    with exactly the documented shape (bigint identity ``id`` primary
    key, text ``month`` not null with a CHECK that it is ``YYYY-MM``
    with a month of 01 to 12, numeric ``amount`` not null with
    ``amount >= 0``, timestamptz ``updated_at`` not null defaulting to
    now(), and text ``updated_by`` not null, UNIQUE on ``month``); the
    database itself refuses a negative amount and a malformed month
    through the CHECK constraints and a duplicate month through the
    unique constraint; its downgrade drops the table and the upgrade
    back to head re-creates it cleanly;
  * an unauthenticated ``GET /api/purchase-budgets`` is 401 with
    ``WWW-Authenticate: Bearer``;
  * the admin's ``PUT /api/purchase-budgets/2026-11`` of
    250000.50 answers the stored row with ``updated_by`` the caller's
    username, and a second PUT of the same month updates it in place —
    still one row, new amount, same id;
  * a ``PUT`` of ``2026-12`` then a ``GET`` lists both months in month
    order, and a signed-in non-admin can read them (200);
  * a non-admin's ``PUT`` and ``DELETE`` are 403 — the exact message
    the erp-mappings routes use — and change nothing;
  * the months ``2026-13``, ``26-11``, ``2026-1``, ``2026-11x`` and
    ``2026`` in Arabic-Indic digits (a Unicode-digit month: re's \\d
    would accept it, but the [0-9] pattern mirroring the database's
    CHECK answers 422, not a 500 from the CHECK violation), and the
    amounts −1, 1e13, a non-finite number, a string and a missing
    body, are each 422 with no row written and the stored row
    untouched;
  * for the admin-only routes authentication and authorization take
    precedence over validation: an unauthenticated request answers 401
    and a non-admin request answers 403 before any 422 for the month
    or the amount;
  * the admin's ``DELETE /api/purchase-budgets/2026-12`` is 204 with
    an empty body, a second DELETE is 404, and the ``GET`` no longer
    lists the month;
  * the identity requirement: the proof also runs as a NON-ADMIN (role
    ``user``, asserted via ``GET /api/auth/me``) in a second pass after
    a full reload (a fresh ``TestClient`` booted the production way —
    the token the page would take from the URL hash as
    ``#access_token=...``, with ``GET /api/auth/config`` answering
    publicly and ``GET /api/auth/me`` resolving the viewer): the
    admin-only controls are refused with 403 (nothing stored or
    deleted) while the page still renders — the budgets the page lists
    answer 200 — and a request without a token still gets 401.
"""

import os
import time
from datetime import UTC, datetime

import psycopg2
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.repositories.purchase_budgets_postgres_repository import (
    PurchaseBudgetPostgresRepository,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREV_HEAD = "0019_draft_po_actions_targets"
HEAD_REVISION = "0022_lead_time_promised_on"

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_USERNAME = "admin"  # the admin stub user's username
ADMIN_DETAIL = "Role 'admin' is required to perform this action."

URL = "/api/purchase-budgets"
_TABLE = "purchase_budgets"
_UNIQ = ("month",)
_COLUMNS = {
    "id",
    "month",
    "amount",
    "updated_at",
    "updated_by",
}
# "2026" in Arabic-Indic digits (U+0662 U+0660 U+0662 U+0666): a
# Unicode-digit month.  re's \d would accept it, but the API's [0-9]
# pattern — the database's CHECK mirrored — must answer 422, not let
# it reach the table and 500 on the CHECK violation.
_BAD_MONTHS = (
    "2026-13",
    "26-11",
    "2026-1",
    "2026-11x",
    "\u0662\u0660\u0662\u0666-11",
)


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


def _table_columns() -> dict:
    """The table's columns as (data_type, max_length, nullability, default, identity)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable, column_default, is_identity "
                "FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (_TABLE,),
            )
            return {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in cur.fetchall()}
    finally:
        conn.close()


def _unique_column_sets() -> list[list[str]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT con.conname, a.attname "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_attribute a ON a.attrelid = con.conrelid "
                "AND a.attnum = ANY(con.conkey) "
                "WHERE rel.relname = %s AND con.contype = 'u' "
                "ORDER BY con.conname, a.attnum",
                (_TABLE,),
            )
            by_name: dict[str, list[str]] = {}
            for name, att in cur.fetchall():
                by_name.setdefault(name, []).append(att)
            return [by_name[name] for name in sorted(by_name)]
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
        cur.execute("SAVEPOINT issue503")
        try:
            cur.execute(sql, params)
        except psycopg2.Error as exc:
            assert type(exc) is expected, f"{label}: {type(exc).__name__}"
            cur.execute("ROLLBACK TO SAVEPOINT issue503")
        else:
            raise AssertionError(f"{label}: the database accepted the row")
        finally:
            cur.execute("RELEASE SAVEPOINT issue503")


def test_issue503_freeform(client):
    repo = PurchaseBudgetPostgresRepository()

    # -- AC-1: the migration is the head and creates the table with the
    #    documented shape. -------------------------------------------------
    assert _alembic_revision() == CURRENT_HEAD
    heads = ScriptDirectory(os.path.join(_BACKEND_ROOT, "migrations")).get_heads()
    assert list(heads) == [CURRENT_HEAD]
    columns = _table_columns()
    assert set(columns) == _COLUMNS
    assert columns["id"] == ("bigint", None, "NO", None, "YES")
    assert columns["month"] == ("text", None, "NO", None, "NO")
    assert columns["amount"] == ("numeric", None, "NO", None, "NO")
    assert columns["updated_at"] == (
        "timestamp with time zone",
        None,
        "NO",
        "now()",
        "NO",
    )
    assert columns["updated_by"] == ("text", None, "NO", None, "NO")
    assert _unique_column_sets() == [list(_UNIQ)]
    checks = _check_constraints()
    assert set(checks) == {
        "ck_purchase_budgets_month",
        "ck_purchase_budgets_amount",
    }
    # The month CHECK is a YYYY-MM pattern with a month of 01 to 12.
    assert "(0[1-9]|1[0-2])" in checks["ck_purchase_budgets_month"]
    assert "amount >= (0)::numeric" in checks["ck_purchase_budgets_amount"]

    # The database itself refuses a negative amount and a malformed
    # month through the CHECK constraints, and a duplicate month
    # through the unique constraint (nothing below is committed).
    conn = get_connection()
    try:
        insert_sql = (
            "INSERT INTO purchase_budgets (month, amount, updated_by) "
            "VALUES (%s, %s, %s)"
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("2026-13", 100, "admin"),
            psycopg2.errors.CheckViolation,
            "month 2026-13",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("2026-1", 100, "admin"),
            psycopg2.errors.CheckViolation,
            "month 2026-1",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("2026-01", -1, "admin"),
            psycopg2.errors.CheckViolation,
            "negative amount",
        )
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT issue503")
            cur.execute(insert_sql, ("2026-02", 100, "admin"))
            cur.execute("RELEASE SAVEPOINT issue503")
        _expect_db_rejection(
            conn,
            insert_sql,
            ("2026-02", 999, "admin"),
            psycopg2.errors.UniqueViolation,
            "duplicate month",
        )
    finally:
        conn.rollback()
        conn.close()

    # -- AC-1: the downgrade drops the table; the upgrade back to head
    #    re-creates it cleanly. ---------------------------------------------
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND_ROOT, "migrations"))
    alembic_command.downgrade(cfg, PREV_HEAD)
    assert _alembic_revision() == PREV_HEAD
    assert _TABLE not in _pg_tables()
    alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_columns() == columns

    # -- AC-2: an unauthenticated GET is 401. --------------------------------
    anon = TestClient(app)
    response = anon.get(URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"

    # -- AC-2: the admin's PUT of 2026-11 answers the row, updated_by the
    #    caller's username. --------------------------------------------------
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    response = admin.put(f"{URL}/2026-11", json={"amount": 250000.50})
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["month"] == "2026-11"
    assert row["amount"] == 250000.5
    assert row["updated_by"] == ADMIN_USERNAME
    assert isinstance(row["id"], int)
    parsed = datetime.fromisoformat(row["updated_at"])
    assert abs((datetime.now(UTC) - parsed).total_seconds()) < 120
    first_id = row["id"]
    assert repo.list() == [row]

    # -- A second PUT of the same month updates it in place: still one row,
    #    new amount. ----------------------------------------------------------
    time.sleep(0.02)
    response = admin.put(f"{URL}/2026-11", json={"amount": 260000.25})
    assert response.status_code == 200, response.text
    row2 = response.json()
    assert row2["id"] == first_id
    assert row2["month"] == "2026-11"
    assert row2["amount"] == 260000.25
    assert row2["updated_by"] == ADMIN_USERNAME
    assert datetime.fromisoformat(row2["updated_at"]) > datetime.fromisoformat(
        row["updated_at"]
    )
    rows = repo.list()
    assert len(rows) == 1
    assert rows[0]["amount"] == 260000.25

    # -- A PUT of 2026-12, then a GET lists both months in month order; a
    #    non-admin can read them. ----------------------------------------------
    response = admin.put(f"{URL}/2026-12", json={"amount": 150000})
    assert response.status_code == 200, response.text
    assert response.json()["month"] == "2026-12"
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    for who in (admin, viewer):
        response = who.get(URL)
        assert response.status_code == 200, response.text
        listed = response.json()
        assert [r["month"] for r in listed] == ["2026-11", "2026-12"]
        by_month = {r["month"]: r for r in listed}
        assert by_month["2026-11"]["amount"] == 260000.25
        assert by_month["2026-11"]["updated_by"] == ADMIN_USERNAME
        assert by_month["2026-12"]["amount"] == 150000.0
        for r in listed:
            assert isinstance(r["id"], int)
            assert r["updated_by"] == ADMIN_USERNAME

    # -- A signed-in non-admin's PUT and DELETE are 403 and change nothing. ---
    response = viewer.put(f"{URL}/2026-11", json={"amount": 1})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer.delete(f"{URL}/2026-11")
    assert response.status_code == 403, response.text
    response = viewer.delete(f"{URL}/2026-12")
    assert response.status_code == 403, response.text
    rows = repo.list()
    assert len(rows) == 2
    assert {r["month"]: r["amount"] for r in rows} == {
        "2026-11": 260000.25,
        "2026-12": 150000.0,
    }

    # -- Bad months are 422 before anything is written. -----------------------
    for bad_month in _BAD_MONTHS:
        response = admin.put(f"{URL}/{bad_month}", json={"amount": 100})
        assert response.status_code == 422, f"{bad_month}: {response.text}"
    assert len(repo.list()) == 2
    # A bad month on the DELETE route is a 422 too, and deletes nothing.
    for bad_month in _BAD_MONTHS:
        response = admin.delete(f"{URL}/{bad_month}")
        assert response.status_code == 422, f"{bad_month}: {response.text}"
    assert len(repo.list()) == 2

    # -- Bad amounts are 422 before anything is written: negative, above the
    #    cap, non-finite, a string, and a missing body. -----------------------
    before = repo.list()
    bad_amount_bodies = (
        ({"amount": -1}, "negative"),
        ({"amount": 1e13}, "above the cap"),
        ({"amount": "100"}, "a string"),
        ({"amount": True}, "a boolean"),
        ({"amount": None}, "null"),
        ({"amount": [100]}, "a list"),
        ({}, "missing amount"),
    )
    for body, label in bad_amount_bodies:
        response = admin.put(f"{URL}/2026-11", json=body)
        assert response.status_code == 422, f"{label}: {response.text}"
    # Non-finite amounts cannot ride in a serialized dict (the
    # serializer refuses them), so they go as the raw JSON text a
    # hostile client can send: the Infinity/NaN tokens and a number so
    # large it overflows to one on parse.
    raw_non_finite = (
        '{"amount": Infinity}',
        '{"amount": -Infinity}',
        '{"amount": NaN}',
        '{"amount": 1e400}',
    )
    for raw in raw_non_finite:
        response = admin.put(
            f"{URL}/2026-11",
            content=raw,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422, f"{raw}: {response.text}"
    # A missing body (no JSON at all) is a 422 as well.
    response = admin.put(f"{URL}/2026-11")
    assert response.status_code == 422, response.text
    # Nothing was written and the stored rows are untouched.
    assert repo.list() == before
    # The cap itself is inclusive: 1e12 stores fine.
    response = admin.put(f"{URL}/2026-01", json={"amount": 1e12})
    assert response.status_code == 200, response.text
    assert response.json()["amount"] == 1e12
    admin.delete(f"{URL}/2026-01")
    assert len(repo.list()) == 2

    # -- AC-3: for the admin-only routes, 401 and 403 come before any 422. ----
    # Unauthenticated: 401 even when the month and the amount are both bad.
    for response in (
        anon.put(f"{URL}/2026-13", json={"amount": -1}),
        anon.put(f"{URL}/2026-11", json={"amount": "nope"}),
        anon.delete(f"{URL}/2026-13"),
        anon.delete(f"{URL}/2026-11"),
    ):
        assert response.status_code == 401, response.text
        assert response.headers.get("www-authenticate") == "Bearer"
    # A malformed Authorization header is a 401 too, ahead of any 422.
    malformed = TestClient(
        app, headers={"Authorization": "Token dev-secret-token:admin"}
    )
    assert malformed.put(f"{URL}/2026-13", json={"amount": -1}).status_code == 401
    # A signed-in non-admin: 403 even when the month and the amount are
    # both bad — the refusal precedes the validation error.
    for response in (
        viewer.put(f"{URL}/2026-13", json={"amount": -1}),
        viewer.put(f"{URL}/2026-11", json={"amount": "nope"}),
        viewer.put(f"{URL}/2026-13", json={}),
        viewer.delete(f"{URL}/2026-13"),
    ):
        assert response.status_code == 403, response.text
        assert response.json() == {"detail": ADMIN_DETAIL}
    assert len(repo.list()) == 2

    # -- DELETE: an existing month is 204 and gone; a second DELETE is 404. ---
    response = admin.delete(f"{URL}/2026-12")
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert [r["month"] for r in repo.list()] == ["2026-11"]
    response = admin.delete(f"{URL}/2026-12")
    assert response.status_code == 404, response.text
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert [r["month"] for r in response.json()] == ["2026-11"]

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only controls are
    #    refused 403 while the page still renders for the viewer. ---------
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    assert TestClient(app).get("/api/auth/config").status_code == 200
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
    # The admin-only controls are absent for the viewer: 403, nothing
    # stored or deleted.
    response = viewer_reloaded.put(f"{URL}/2026-11", json={"amount": 1})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer_reloaded.delete(f"{URL}/2026-11")
    assert response.status_code == 403, response.text
    assert [r["month"] for r in repo.list()] == ["2026-11"]
    # The page still renders: the budgets it lists answer 200 for the
    # viewer, and a request without a token still gets 401.
    response = viewer_reloaded.get(URL)
    assert response.status_code == 200, response.text
    assert [r["month"] for r in response.json()] == ["2026-11"]
    assert TestClient(app).get(URL).status_code == 401
