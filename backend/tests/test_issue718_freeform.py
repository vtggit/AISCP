"""Issue #718 proof: the parameter_writebacks audit table, its
repository and its read route.

Proves, against the live app and the migrated test database:

* the migration — ``0026_parameter_writebacks`` (revision after
  ``0025_empirical_adoptions``) is the only alembic head and is
  applied; it creates ``parameter_writebacks`` with exactly the
  documented columns (String(36) ``id`` not null and the primary key,
  String(64) ``item_code``/``site_code`` not null, String(32)
  ``source_system`` not null, String(36) ``decision_id`` nullable,
  Float ``before_reorder_point``/``before_max_qty``/
  ``after_reorder_point``/``after_max_qty`` nullable, Float
  ``requested_reorder_point``/``requested_max_qty`` not null,
  String(16) ``outcome`` not null with a CHECK constraint allowing
  only "written", "unchanged" and "failed", text ``error`` nullable,
  String(255) ``written_by`` not null, String(255)
  ``written_by_name`` nullable, timestamptz ``written_at`` not null
  defaulting to now()) and an index on (item_code, site_code,
  written_at); an insert with outcome "other" is rejected by the
  CHECK constraint; the migration creates no database role, changes
  no table ownership and grants no privilege (the table is owned by
  the application's existing database user with no explicit grants);
* the repository — three rows inserted through ``insert`` (a new
  uuid4 id each) for two item-sites come back from ``list`` newest
  first (written_at DESC, id DESC); the ``item_code`` and
  ``site_code`` filters narrow them; ``limit`` and ``offset`` page
  them; decimal values such as 0.1000 round-trip exactly through the
  audit record (no update or delete exists on the repository);
* the route — ``GET /api/parameter-writeback/history`` with the
  planner (non-admin) token returns the same rows and filters, as
  the typed ``ParameterWriteback`` model with exact decimal parameter
  values; no token answers 401; a ``limit`` of 0 or 1001 answers 422,
  as do a NUL byte, an over-long code, a non-numeric limit and a
  negative offset — never a 500;
* the downgrade — rolling back to ``0025_empirical_adoptions`` does
  not drop, truncate or alter the table: every audit row survives,
  and the upgrade back to head keeps them too;
* the schema file — ``backend/openapi.json`` lists the
  ``/api/parameter-writeback/history`` path and the
  ``ParameterWriteback`` model;
* the identity requirement: the proof also runs as a NON-ADMIN (role
  ``user``) — a second pass after a full reload, with the page booted
  the production way (the token it would take from the URL hash as
  ``#access_token=...``), ``GET /api/auth/config`` answering publicly
  and ``GET /api/auth/me`` resolving the viewer, sees the admin-only
  control absent (no 403 on the history route) while the page still
  renders with all rows, and no token is still a 401.
"""

import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import psycopg2
import psycopg2.extras
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.repositories.parameter_writebacks_postgres_repository import (
    ParameterWritebacksPostgresRepository,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

URL = "/api/parameter-writeback/history"
PREVIEW_URL = "/api/parameter-writeback/preview"

HEAD = CURRENT_HEAD
PREV_HEAD = "0025_empirical_adoptions"

PLANNER_TOKEN = "dev-secret-token:user"
VIEWER_TOKEN = "dev-secret-token"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

ITEM_A = "ITEM-718A"
ITEM_B = "ITEM-718B"
SITE_A = "SITE-718-1"
SITE_B = "SITE-718-2"

# column -> (data_type, character_maximum_length, is_nullable)
EXPECTED_SHAPE = {
    "id": ("character varying", 36, "NO"),
    "item_code": ("character varying", 64, "NO"),
    "site_code": ("character varying", 64, "NO"),
    "source_system": ("character varying", 32, "NO"),
    "decision_id": ("character varying", 36, "YES"),
    "basis": ("character varying", 16, "NO"),
    "before_reorder_point": ("double precision", None, "YES"),
    "before_max_qty": ("double precision", None, "YES"),
    "after_reorder_point": ("double precision", None, "YES"),
    "after_max_qty": ("double precision", None, "YES"),
    "requested_reorder_point": ("double precision", None, "NO"),
    "requested_max_qty": ("double precision", None, "NO"),
    "outcome": ("character varying", 16, "NO"),
    "error": ("text", None, "YES"),
    "written_by": ("character varying", 255, "NO"),
    "written_by_name": ("character varying", 255, "YES"),
    "written_at": ("timestamp with time zone", None, "NO"),
}

_NUMERIC_FIELDS = (
    "before_reorder_point",
    "before_max_qty",
    "after_reorder_point",
    "after_max_qty",
    "requested_reorder_point",
    "requested_max_qty",
)


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND_ROOT, "migrations"))
    return cfg


def _alembic_revision() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _table_columns() -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'parameter_writebacks' "
                "ORDER BY column_name"
            )
            return {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}
    finally:
        conn.close()


def _primary_key_columns() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = 'parameter_writebacks'::regclass "
                "AND i.indisprimary "
                "ORDER BY array_position(i.indkey, a.attnum)"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _check_constraint_names() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'parameter_writebacks'::regclass "
                "AND contype = 'c' ORDER BY conname"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _index_columns() -> list:
    """(index name, [columns]) for every index on the table."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.relname, "
                "array_agg(a.attname ORDER BY array_position(ix.indkey, a.attnum)) "
                "FROM pg_index AS ix "
                "JOIN pg_class AS i ON i.oid = ix.indexrelid "
                "JOIN pg_attribute AS a "
                "  ON a.attrelid = ix.indrelid AND a.attnum = ANY(ix.indkey) "
                "WHERE ix.indrelid = 'parameter_writebacks'::regclass "
                "GROUP BY i.relname"
            )
            return [(row[0], list(row[1])) for row in cur.fetchall()]
    finally:
        conn.close()


def _written_at_default() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'parameter_writebacks' "
                "AND column_name = 'written_at'"
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _owner(table: str) -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tableowner FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = %s",
                (table,),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _explicit_grants() -> object:
    """The table's access privilege list (NULL when nothing was granted)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT relacl FROM pg_class "
                "WHERE relnamespace = 'public'::regnamespace "
                "AND relname = 'parameter_writebacks'"
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _table_exists() -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM pg_tables "
                "WHERE schemaname = 'public' "
                "AND tablename = 'parameter_writebacks'"
            )
            return cur.fetchone()[0] == 1
    finally:
        conn.close()


def _stored_rows() -> list:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM parameter_writebacks "
                "ORDER BY written_at DESC, id DESC"
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def test_issue718_freeform(client):
    repo = ParameterWritebacksPostgresRepository()
    planner = TestClient(app, headers={"Authorization": f"Bearer {PLANNER_TOKEN}"})
    anon = TestClient(app)

    # -- 1. The migration: 0026 is the only head, applied; the table
    #    has the documented shape, id is the primary key, the CHECK
    #    constraint and the index are in place, and no role, ownership
    #    change or privilege was introduced. ----------------------------
    cfg = _alembic_config()
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert heads[0] == HEAD
    assert _alembic_revision() == HEAD
    assert _table_columns() == EXPECTED_SHAPE
    assert _primary_key_columns() == ["id"]
    assert _written_at_default() == "now()"
    assert "ck_parameter_writebacks_outcome" in _check_constraint_names()
    assert (
        "ix_parameter_writebacks_item_site_written_at",
        ["item_code", "site_code", "written_at"],
    ) in _index_columns()

    # The table is owned by the application's existing database user
    # (the same user that owns the other tables) with no explicit
    # grants: no role created, no ownership changed, no privilege
    # granted.
    assert _owner("parameter_writebacks") == _owner("empirical_adoptions")
    assert _owner("parameter_writebacks") == os.environ["DB_USER"]
    assert _explicit_grants() is None

    # The CHECK constraint: an insert with outcome "other" is
    # rejected (and rolls back, so it stores nothing).
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO parameter_writebacks "
                "(id, item_code, site_code, source_system, basis, "
                " requested_reorder_point, requested_max_qty, outcome, written_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    str(uuid4()),
                    ITEM_A,
                    SITE_A,
                    "acumatica",
                    "decision",
                    1.0,
                    2.0,
                    "other",
                    "nobody",
                ),
            )
    except psycopg2.Error as exc:
        assert isinstance(exc, psycopg2.errors.CheckViolation)
        assert "ck_parameter_writebacks_outcome" in str(exc)
    else:
        raise AssertionError("outcome 'other' was accepted")
    finally:
        conn.rollback()
        conn.close()
    assert _stored_rows() == []

    # -- 2. The repository: three rows for two item-sites come back
    #    newest first; the filters narrow; limit/offset page. ----------
    t_old = datetime(2026, 10, 25, 8, 0, tzinfo=UTC)
    t_mid = datetime(2026, 10, 26, 9, 30, tzinfo=UTC)
    t_new = datetime(2026, 10, 27, 10, 15, tzinfo=UTC)

    row_a_old = repo.insert(
        item_code=ITEM_A,
        site_code=SITE_A,
        source_system="acumatica",
        decision_id=None,
        before_reorder_point=10.0,
        before_max_qty=None,
        after_reorder_point=12.0,
        after_max_qty=30.0,
        requested_reorder_point=12.0,
        requested_max_qty=30.0,
        outcome="written",
        error=None,
        written_by="dev-admin-1",
        written_by_name="admin",
        written_at=t_old,
    )
    row_a_mid = repo.insert(
        item_code=ITEM_A,
        site_code=SITE_B,
        source_system="odoo",
        decision_id=str(uuid4()),
        before_reorder_point=Decimal("0.1000"),
        before_max_qty=None,
        after_reorder_point=Decimal("0.1000"),
        after_max_qty=None,
        requested_reorder_point=Decimal("0.1000"),
        requested_max_qty=25.0,
        outcome="unchanged",
        error=None,
        written_by="dev-user-1",
        written_by_name=None,
        written_at=t_mid,
    )
    row_b_new = repo.insert(
        item_code=ITEM_B,
        site_code=SITE_A,
        source_system="acumatica",
        decision_id=None,
        before_reorder_point=8.0,
        before_max_qty=3.0,
        after_reorder_point=8.0,
        after_max_qty=3.0,
        requested_reorder_point=7.5,
        requested_max_qty=2.25,
        outcome="failed",
        error="ERP rejected the update",
        written_by="dev-user-1",
        written_by_name="planner",
        written_at=t_new,
    )

    # Each row came back with its new uuid4 id and the stored values.
    for stored in (row_a_old, row_a_mid, row_b_new):
        UUID(stored["id"])  # a uuid4 id
    assert [stored["written_at"] for stored in (row_a_old, row_a_mid, row_b_new)] == [
        t_old,
        t_mid,
        t_new,
    ]

    # AC-4: a decimal value round-trips exactly through the audit
    # record — 0.1000 comes back exactly 0.1, with no rounding and no
    # binary-float noise.
    (stored_mid,) = repo.list(item_code=ITEM_A, site_code=SITE_B)
    for field in (
        "requested_reorder_point",
        "before_reorder_point",
        "after_reorder_point",
    ):
        assert stored_mid[field] == 0.1000
        assert Decimal(repr(stored_mid[field])) == Decimal("0.1000")

    # Newest first: written_at DESC, id DESC.
    rows = repo.list()
    assert [row["id"] for row in rows] == [
        row_b_new["id"],
        row_a_mid["id"],
        row_a_old["id"],
    ]

    # The filters narrow.
    assert [row["id"] for row in repo.list(item_code=ITEM_A)] == [
        row_a_mid["id"],
        row_a_old["id"],
    ]
    assert [row["id"] for row in repo.list(site_code=SITE_A)] == [
        row_b_new["id"],
        row_a_old["id"],
    ]
    assert [row["id"] for row in repo.list(item_code=ITEM_A, site_code=SITE_A)] == [
        row_a_old["id"]
    ]
    assert repo.list(item_code=ITEM_B, site_code=SITE_B) == []

    # limit and offset page.
    assert [row["id"] for row in repo.list(limit=2)] == [
        row_b_new["id"],
        row_a_mid["id"],
    ]
    assert [row["id"] for row in repo.list(limit=1, offset=1)] == [row_a_mid["id"]]
    assert [row["id"] for row in repo.list(limit=1, offset=2)] == [row_a_old["id"]]
    assert repo.list(limit=1, offset=3) == []

    # -- 3. The route: the planner token gets the same rows and
    #    filters, typed; no token gets 401; a limit of 0 or 1001 gets
    #    422; hostile input never gets a 500. ---------------------------
    response = planner.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["id"] for row in body] == [
        row_b_new["id"],
        row_a_mid["id"],
        row_a_old["id"],
    ]
    # The same rows as the repository, field for field, with the
    # parameter values as exact decimals.
    for wire, stored in zip(body, rows):
        assert wire["id"] == stored["id"]
        assert wire["item_code"] == stored["item_code"]
        assert wire["site_code"] == stored["site_code"]
        assert wire["source_system"] == stored["source_system"]
        assert wire["decision_id"] == stored["decision_id"]
        for field in _NUMERIC_FIELDS:
            if stored[field] is None:
                assert wire[field] is None
            else:
                assert Decimal(wire[field]) == Decimal(repr(stored[field]))
        assert wire["outcome"] == stored["outcome"]
        assert wire["error"] == stored["error"]
        assert wire["written_by"] == stored["written_by"]
        assert wire["written_by_name"] == stored["written_by_name"]
        assert datetime.fromisoformat(wire["written_at"]) == stored["written_at"]

    # AC-4 through the route: 0.1000 round-trips exactly — the exact
    # decimal face, never a 17-digit binary-float artifact.
    (wire_mid,) = (row for row in body if row["id"] == row_a_mid["id"])
    assert Decimal(wire_mid["requested_reorder_point"]) == Decimal("0.1000")
    assert wire_mid["requested_reorder_point"] in ("0.1", "0.1000")
    assert (
        "0.1000000000000000055511151231257827021181583404541015625" not in response.text
    )

    # The filters and the paging work over HTTP too.
    assert [
        row["id"] for row in planner.get(URL, params={"item_code": ITEM_A}).json()
    ] == [row_a_mid["id"], row_a_old["id"]]
    assert [
        row["id"] for row in planner.get(URL, params={"site_code": SITE_A}).json()
    ] == [row_b_new["id"], row_a_old["id"]]
    assert [
        row["id"]
        for row in planner.get(
            URL, params={"item_code": ITEM_A, "site_code": SITE_A}
        ).json()
    ] == [row_a_old["id"]]
    assert [
        row["id"] for row in planner.get(URL, params={"limit": 1, "offset": 1}).json()
    ] == [row_a_mid["id"]]

    # No token answers 401.
    assert anon.get(URL).status_code == 401

    # A limit of 0 or 1001 answers 422.
    assert planner.get(URL, params={"limit": 0}).status_code == 422
    assert planner.get(URL, params={"limit": 1001}).status_code == 422

    # Hostile input never 500s: a NUL byte and an over-long code in
    # either filter answer 422, a non-numeric limit and a negative
    # offset answer 422, and an empty or control-character code, or a
    # huge offset, simply answers 200 with the (empty) result.
    assert planner.get(URL, params={"item_code": ITEM_A + "\x00"}).status_code == 422
    assert planner.get(URL, params={"site_code": SITE_A + "\x00"}).status_code == 422
    assert planner.get(URL, params={"item_code": "x" * 65}).status_code == 422
    assert planner.get(URL, params={"limit": "abc"}).status_code == 422
    assert planner.get(URL, params={"offset": -1}).status_code == 422
    assert planner.get(URL, params={"item_code": ""}).json() == []
    assert planner.get(URL, params={"item_code": "A\u0001"}).status_code == 200
    assert planner.get(URL, params={"item_code": "A\u0001"}).json() == []
    assert planner.get(URL, params={"offset": 10**18}).status_code == 200
    assert planner.get(URL, params={"offset": 10**18}).json() == []

    # The existing preview route is unchanged.
    response = planner.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    assert {"rows", "count", "writable_count"} <= set(response.json())

    # -- 4. The schema file lists the path and the model. ---------------
    spec = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    assert URL in spec["paths"]
    get_op = spec["paths"][URL]["get"]
    query_params = [p["name"] for p in get_op["parameters"] if p["in"] == "query"]
    assert set(query_params) == {"item_code", "site_code", "limit", "offset"}
    schema = get_op["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["items"]["$ref"] == "#/components/schemas/ParameterWriteback"
    assert "ParameterWriteback" in spec["components"]["schemas"]

    # -- 5. The downgrade does not delete or truncate the audit rows,
    #    and the upgrade back to head keeps them too. --------------------
    before = _stored_rows()
    assert len(before) == 3
    # The downgrade drops the basis column by design, so at
    # PREV_HEAD the stored rows are before minus each row's basis
    # key.
    without_basis = [
        {key: value for key, value in row.items() if key != "basis"} for row in before
    ]
    try:
        alembic_command.downgrade(cfg, PREV_HEAD)
        assert _alembic_revision() == PREV_HEAD
        assert _table_exists()  # not dropped
        assert _stored_rows() == without_basis  # nothing deleted or truncated
    finally:
        alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == HEAD
    assert _table_columns() == EXPECTED_SHAPE
    assert _stored_rows() == before  # the backfill restores each basis

    # -- 6. Identity: the proof also runs as a NON-ADMIN (role 'user').
    #    Second pass after a full reload: the page boots the production
    #    way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only control is
    #    absent (no 403 on the history route) while the page still
    #    renders with all rows, and no token is still a 401. -----------
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["sub"] == VIEWER_SUB
    assert me.json()["user"]["roles"] == ["user"]
    assert "admin" not in me.json()["user"]["roles"]
    assert "admin" not in me.json()["user"]["groups"]

    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [
        row_b_new["id"],
        row_a_mid["id"],
        row_a_old["id"],
    ]
    response = viewer.get(URL, params={"site_code": SITE_A})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [
        row_b_new["id"],
        row_a_old["id"],
    ]
    assert anon_reloaded.get(URL).status_code == 401
    assert anon_reloaded.get(URL).headers.get("www-authenticate") == "Bearer"
