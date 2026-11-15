"""Issue #468 proof: cross-ERP code mappings with a Bearer-gated API.

Everything is exercised against the test database through the app's
test client with the two development stub users: the admin stub
(``dev-secret-token:admin``, sub ``dev-admin-1``, roles admin+user)
stores and deletes mappings, and the non-admin stub (role ``user``,
sub ``dev-user-1``) reads them and gets refused the mutations.  No
external system is touched: the "target system" is a string column.

Proved here:

  * the migration — ``0018_erp_code_mappings`` (revision after
    ``0017_draft_po_actions``) creates ``erp_code_mappings`` with
    exactly the documented shape (bigint
    identity ``id`` primary key, text ``kind`` not null with the
    item/site/vendor check, text ``target_system`` not null with the
    odoo check, String(255) ``source_code``/``target_code`` not null,
    timestamptz ``created_at``/``updated_at`` not null defaulting to
    now(), and the UNIQUE constraint on (kind, target_system,
    source_code)); the database itself rejects a bad kind, a bad
    target_system and a duplicate triple; its downgrade drops the
    table and the upgrade back to head re-creates it cleanly;
  * an admin ``PUT /api/erp-mappings`` stores a mapping and returns
    the stored row (id, the body's fields, both timestamps near now);
  * a second ``PUT`` on the same (kind, target_system, source_code)
    updates ``target_code`` and ``updated_at`` of the existing row —
    same id, same ``created_at`` — rather than creating a duplicate;
  * ``GET /api/erp-mappings`` returns the mappings ordered by kind
    then source code, and honours the ``kind`` and ``target_system``
    filters; a filter value outside the allowed ones answers 422 with
    the same rules as the PUT body, not an empty result;
  * a signed-in non-admin can ``GET`` (200) but gets 403 on ``PUT``
    and on ``DELETE`` — exactly the message the settings routes use
    when they refuse a SYSTEM setting — and nothing is stored or
    deleted;
  * an admin ``DELETE /api/erp-mappings/{id}`` of an existing id
    answers 204 with an empty body and the row is gone, and an unknown
    id answers 404;
  * a bad kind, a target_system other than odoo, a blank code, a
    NUL-containing code and a 256-character code (in either code
    field) each answer 422 and store nothing — never a 500;
  * the repository's ``get_target`` returns the stored target code for
    a mapped (kind, target_system, source_code) and None for an
    unmapped one (including a code mapped under a different kind);
  * the routes are Bearer-gated like the other routers: 401 with
    ``WWW-Authenticate: Bearer`` without a token (and with a malformed
    Authorization header) on GET, PUT and DELETE alike;
  * the identity requirement: the proof runs as a NON-ADMIN (role
    ``user``, asserted via ``GET /api/auth/me``), and a second pass
    after a full reload (a fresh ``TestClient`` booted the production
    way — the token the page would take from the URL hash as
    ``#access_token=...``, with ``GET /api/auth/config`` answering
    publicly and ``GET /api/auth/me`` resolving the viewer) sees the
    admin-only controls refused with 403 (nothing stored) while the
    page still renders: the mappings the page lists and the settings
    it loads answer 200, and a request without a token still gets 401.
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
from app.repositories.erp_code_mappings_postgres_repository import (
    ErpCodeMappingPostgresRepository,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREV_HEAD = "0017_draft_po_actions"
HEAD_REVISION = "0022_lead_time_promised_on"

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_DETAIL = "Role 'admin' is required to perform this action."

URL = "/api/erp-mappings"
_TABLE = "erp_code_mappings"
_UNIQ = ("kind", "target_system", "source_code")
_COLUMNS = {
    "id",
    "kind",
    "target_system",
    "source_code",
    "target_code",
    "created_at",
    "updated_at",
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
        cur.execute("SAVEPOINT issue468")
        try:
            cur.execute(sql, params)
        except psycopg2.Error as exc:
            assert type(exc) is expected, f"{label}: {type(exc).__name__}"
            cur.execute("ROLLBACK TO SAVEPOINT issue468")
        else:
            raise AssertionError(f"{label}: the database accepted the row")
        finally:
            cur.execute("RELEASE SAVEPOINT issue468")


def test_issue468_freeform(client):
    repo = ErpCodeMappingPostgresRepository()

    # -- AC-1: the migration is the head and creates the table with the
    #    documented shape. -------------------------------------------------
    assert _alembic_revision() == CURRENT_HEAD
    heads = ScriptDirectory(os.path.join(_BACKEND_ROOT, "migrations")).get_heads()
    assert list(heads) == [CURRENT_HEAD]
    columns = _table_columns()
    assert set(columns) == _COLUMNS
    assert columns["id"] == ("bigint", None, "NO", None, "YES")
    assert columns["kind"] == ("text", None, "NO", None, "NO")
    assert columns["target_system"] == ("text", None, "NO", None, "NO")
    assert columns["source_code"] == ("character varying", 255, "NO", None, "NO")
    assert columns["target_code"] == ("character varying", 255, "NO", None, "NO")
    for column in ("created_at", "updated_at"):
        assert columns[column] == (
            "timestamp with time zone",
            None,
            "NO",
            "now()",
            "NO",
        )
    assert _unique_column_sets() == [list(_UNIQ)]
    checks = _check_constraints()
    assert set(checks) == {
        "ck_erp_code_mappings_kind",
        "ck_erp_code_mappings_target_system",
    }
    for kind in ("item", "site", "vendor"):
        assert f"'{kind}'" in checks["ck_erp_code_mappings_kind"]
    assert "'odoo'" in checks["ck_erp_code_mappings_target_system"]
    assert "item" not in checks["ck_erp_code_mappings_target_system"]

    # The database itself rejects a bad kind, a bad target_system and a
    # duplicate triple (nothing below is committed).
    conn = get_connection()
    try:
        insert_sql = (
            "INSERT INTO erp_code_mappings "
            "(kind, target_system, source_code, target_code) "
            "VALUES (%s, %s, %s, %s)"
        )
        for bad_kind in ("warehouse", "ITEM", ""):
            _expect_db_rejection(
                conn,
                insert_sql,
                (bad_kind, "odoo", "X1", "Y1"),
                psycopg2.errors.CheckViolation,
                f"kind {bad_kind!r}",
            )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("item", "sap", "X1", "Y1"),
            psycopg2.errors.CheckViolation,
            "target_system 'sap'",
        )
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT issue468")
            cur.execute(insert_sql, ("item", "odoo", "X1", "Y1"))
            cur.execute("RELEASE SAVEPOINT issue468")
        _expect_db_rejection(
            conn,
            insert_sql,
            ("item", "odoo", "X1", "Y2"),
            psycopg2.errors.UniqueViolation,
            "duplicate triple",
        )
        _expect_db_rejection(
            conn,
            insert_sql,
            ("item", "odoo", "x" * 256, "Y1"),
            psycopg2.errors.StringDataRightTruncation,
            "256-char source_code",
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

    # -- AC-1/AC-2: an admin PUT stores a mapping and returns it. -----------
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    first = {
        "kind": "item",
        "target_system": "odoo",
        "source_code": "WIDGET 1",
        "target_code": "ODOO-1001",
    }
    response = admin.put(URL, json=first)
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["kind"] == "item"
    assert row["target_system"] == "odoo"
    assert row["source_code"] == "WIDGET 1"
    assert row["target_code"] == "ODOO-1001"
    assert isinstance(row["id"], int)
    for timestamp in ("created_at", "updated_at"):
        parsed = datetime.fromisoformat(row[timestamp])
        assert abs((datetime.now(UTC) - parsed).total_seconds()) < 120
    first_id = row["id"]
    assert repo.get_target("item", "odoo", "WIDGET 1") == "ODOO-1001"

    # -- A second PUT on the same triple updates target_code and updated_at
    #    rather than creating a duplicate. -----------------------------------
    time.sleep(0.02)
    response = admin.put(URL, json={**first, "target_code": "ODOO-1002"})
    assert response.status_code == 200, response.text
    row2 = response.json()
    assert row2["id"] == first_id
    assert row2["target_code"] == "ODOO-1002"
    assert row2["created_at"] == row["created_at"]
    assert datetime.fromisoformat(row2["updated_at"]) > datetime.fromisoformat(
        row["updated_at"]
    )
    assert repo.get_target("item", "odoo", "WIDGET 1") == "ODOO-1002"
    # One row for the triple: the update made no duplicate.
    assert [(r["kind"], r["source_code"]) for r in repo.list()] == [
        ("item", "WIDGET 1")
    ]

    # -- Seed the rest of the mappings for the GET ordering/filter tests. ---
    for body in (
        {
            "kind": "item",
            "target_system": "odoo",
            "source_code": "BOLT M6",
            "target_code": "ODOO-0007",
        },
        {
            "kind": "site",
            "target_system": "odoo",
            "source_code": "SITE B",
            "target_code": "ODOO-SITE-B",
        },
        {
            "kind": "site",
            "target_system": "odoo",
            "source_code": "SITE A",
            "target_code": "ODOO-SITE-A",
        },
        {
            "kind": "vendor",
            "target_system": "odoo",
            "source_code": "ACME SUPPLY",
            "target_code": "ODOO-V-9",
        },
    ):
        response = admin.put(URL, json=body)
        assert response.status_code == 200, response.text

    # -- GET lists every mapping ordered by kind, then source_code. ---------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [(r["kind"], r["source_code"]) for r in rows] == [
        ("item", "BOLT M6"),
        ("item", "WIDGET 1"),
        ("site", "SITE A"),
        ("site", "SITE B"),
        ("vendor", "ACME SUPPLY"),
    ]
    for r in rows:
        assert r["target_system"] == "odoo"
        assert isinstance(r["id"], int)
    by_key = {(r["kind"], r["source_code"]): r["target_code"] for r in rows}
    assert by_key[("item", "WIDGET 1")] == "ODOO-1002"
    assert by_key[("vendor", "ACME SUPPLY")] == "ODOO-V-9"

    # The kind filter narrows to that kind's rows (still ordered).
    response = admin.get(URL, params={"kind": "site"})
    assert response.status_code == 200, response.text
    assert [(r["kind"], r["source_code"]) for r in response.json()] == [
        ("site", "SITE A"),
        ("site", "SITE B"),
    ]
    # The target_system filter keeps odoo's rows (every row here is odoo).
    response = admin.get(URL, params={"target_system": "odoo"})
    assert response.status_code == 200, response.text
    assert len(response.json()) == 5
    # Both filters together.
    response = admin.get(URL, params={"kind": "item", "target_system": "odoo"})
    assert response.status_code == 200, response.text
    assert [r["source_code"] for r in response.json()] == ["BOLT M6", "WIDGET 1"]
    # A valid kind with no rows answers an empty list, not an error.
    response = admin.get(URL, params={"kind": "vendor", "target_system": "odoo"})
    assert response.status_code == 200, response.text
    assert [r["source_code"] for r in response.json()] == ["ACME SUPPLY"]

    # A filter value outside the allowed set answers 422, not an empty
    # result — the same rules as the PUT body.
    for bad_params in (
        {"kind": "warehouse"},
        {"kind": "ITEM"},
        {"target_system": "sap"},
        {"kind": "item\x00"},
        {"kind": "k" * 256},
    ):
        response = admin.get(URL, params=bad_params)
        assert response.status_code == 422, f"{bad_params}: {response.text}"

    # -- A signed-in non-admin can GET but gets 403 on PUT and DELETE. ------
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    assert len(response.json()) == 5
    response = viewer.put(
        URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": "NOPE 1",
            "target_code": "ODOO-NOPE",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer.delete(f"{URL}/{first_id}")
    assert response.status_code == 403, response.text
    # Nothing changed: the refusal stored no row and deleted nothing.
    assert repo.get_target("item", "odoo", "WIDGET 1") == "ODOO-1002"
    assert repo.get_target("item", "odoo", "NOPE 1") is None

    # -- DELETE: an existing id answers 204 and the row is gone; an unknown
    #    id answers 404. ------------------------------------------------------
    response = admin.delete(f"{URL}/{first_id}")
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert repo.get_target("item", "odoo", "WIDGET 1") is None
    response = admin.get(URL, params={"kind": "item"})
    assert response.status_code == 200, response.text
    assert [r["source_code"] for r in response.json()] == ["BOLT M6"]
    # The deleted id and a never-existing id are both 404.
    response = admin.delete(f"{URL}/{first_id}")
    assert response.status_code == 404, response.text
    response = admin.delete(f"{URL}/999999")
    assert response.status_code == 404, response.text
    # A non-numeric id is a 422, never a 500.
    assert admin.delete(f"{URL}/not-an-id").status_code == 422

    # -- Hostile bodies each answer 422 and store nothing. -------------------
    before_count = len(repo.list())
    assert before_count == 4
    bad_bodies = (
        (
            {
                "kind": "warehouse",
                "target_system": "odoo",
                "source_code": "X",
                "target_code": "Y",
            },
            "bad kind",
        ),
        (
            {
                "kind": "item",
                "target_system": "sap",
                "source_code": "X",
                "target_code": "Y",
            },
            "bad target_system",
        ),
        (
            {
                "kind": "item",
                "target_system": "odoo",
                "source_code": "",
                "target_code": "Y",
            },
            "blank source_code",
        ),
        (
            {
                "kind": "item",
                "target_system": "odoo",
                "source_code": "   ",
                "target_code": "Y",
            },
            "whitespace source_code",
        ),
        (
            {
                "kind": "item",
                "target_system": "odoo",
                "source_code": "X",
                "target_code": "",
            },
            "blank target_code",
        ),
        (
            {
                "kind": "item",
                "target_system": "odoo",
                "source_code": "X\x00",
                "target_code": "Y",
            },
            "NUL source_code",
        ),
        (
            {
                "kind": "item",
                "target_system": "odoo",
                "source_code": "X",
                "target_code": "\x00Y",
            },
            "NUL target_code",
        ),
        (
            {
                "kind": "item",
                "target_system": "odoo",
                "source_code": "x" * 256,
                "target_code": "Y",
            },
            "256-char source_code",
        ),
        (
            {
                "kind": "item",
                "target_system": "odoo",
                "source_code": "X",
                "target_code": "y" * 256,
            },
            "256-char target_code",
        ),
    )
    for body, label in bad_bodies:
        response = admin.put(URL, json=body)
        assert response.status_code == 422, f"{label}: {response.text}"
    # 255 characters is the bound: it stores fine.
    response = admin.put(
        URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": "x" * 255,
            "target_code": "Y",
        },
    )
    assert response.status_code == 200, response.text
    assert repo.get_target("item", "odoo", "x" * 255) == "Y"
    admin.delete(f"{URL}/{response.json()['id']}")
    # The 422s stored nothing; only the 255-char row (now deleted) did.
    assert len(repo.list()) == before_count

    # -- get_target: mapped codes answer, unmapped ones return None. ---------
    assert repo.get_target("item", "odoo", "BOLT M6") == "ODOO-0007"
    assert repo.get_target("site", "odoo", "SITE A") == "ODOO-SITE-A"
    assert repo.get_target("vendor", "odoo", "ACME SUPPLY") == "ODOO-V-9"
    assert repo.get_target("item", "odoo", "NEVER MAPPED") is None
    assert repo.get_target("vendor", "odoo", "BOLT M6") is None  # wrong kind
    assert repo.get_target("item", "sap", "BOLT M6") is None  # wrong system

    # -- The routes are Bearer-gated like the other routers. -----------------
    anon = TestClient(app)
    for response in (
        anon.get(URL),
        anon.put(URL, json=first),
        anon.delete(f"{URL}/{first_id}"),
    ):
        assert response.status_code == 401, response.text
        assert response.headers.get("www-authenticate") == "Bearer"
    # A malformed Authorization header is a 401 too.
    malformed = TestClient(
        app, headers={"Authorization": "Token dev-secret-token:admin"}
    )
    assert malformed.get(URL).status_code == 401

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
    # The admin-only controls are absent for the viewer: 403, nothing
    # stored or deleted.
    response = viewer_reloaded.put(
        URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": "RELOAD 1",
            "target_code": "ODOO-RELOAD",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    assert repo.get_target("item", "odoo", "RELOAD 1") is None
    response = viewer_reloaded.delete(f"{URL}/{first_id}")
    assert response.status_code == 403, response.text
    # The page still renders: the data it loads answers 200 for the
    # viewer, and a request without a token still gets 401.
    response = viewer_reloaded.get(URL)
    assert response.status_code == 200, response.text
    assert len(response.json()) == before_count
    assert viewer_reloaded.get("/api/settings").status_code == 200
    assert TestClient(app).get(URL).status_code == 401
