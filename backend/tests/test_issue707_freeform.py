"""Issue #707 proof: the empirical_adoptions table and its API.

Seeds two item-sites in ``item_sites`` and proves, against the live
app and the migrated test database:

* the migration — ``0025_empirical_adoptions`` (revision after
  ``0024_inventory_items_unique_code``) is the only alembic head and
  is applied; it creates ``empirical_adoptions`` with exactly the
  documented columns (String(64) ``item_code``/``site_code`` not null
  and together the primary key, String(255) ``adopted_by`` not null,
  String(255) ``adopted_by_name`` nullable, timestamptz ``adopted_at``
  not null defaulting to now(), text ``note`` nullable), with no
  foreign key to ``item_sites`` (no ON DELETE CASCADE); the downgrade
  drops the table and the upgrade back to head re-creates it cleanly;
* adopting — an admin ``PUT /api/empirical-adoptions`` with a note
  answers 200 with ``adopted_by`` set to the admin token's subject and
  ``adopted_by_name`` to its display name (the way planner decisions
  record ``decided_by`` and ``decided_by_name``), and a second ``PUT``
  for the same pair replaces the note without a second row;
* listing — ``GET /api/empirical-adoptions`` with the planner
  (non-admin) token lists the one adoption;
* refusals — a ``PUT`` for a pair not in ``item_sites`` answers 404
  (the item-site's existence is validated at the application level);
  a planner ``PUT`` or ``DELETE`` answers 403; no token answers 401;
  a NUL byte in any field answers 422, as do over-long codes — never
  a 500;
* withdrawing — an admin ``DELETE`` answers 204 and the list is
  empty, and a second ``DELETE`` answers 404;
* the schema file — ``backend/openapi.json`` lists the
  ``/api/empirical-adoptions`` path with its three operations and the
  ``EmpiricalAdoption`` / ``EmpiricalAdoptionBody`` models;
* the identity requirement: the proof also runs as a NON-ADMIN (role
  ``user``) — a second pass after a full reload, with the page booted
  the production way (the token it would take from the URL hash as
  ``#access_token=...``), ``GET /api/auth/config`` answering publicly
  and ``GET /api/auth/me`` resolving the viewer, sees the admin-only
  control refused 403 (nothing stored) while the page still renders,
  and no token is still a 401.
"""

import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import psycopg2.extras
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

URL = "/api/empirical-adoptions"

HEAD = CURRENT_HEAD
PREV_HEAD = "0024_inventory_items_unique_code"

ADMIN_TOKEN = "dev-secret-token:admin"
PLANNER_TOKEN = "dev-secret-token:user"
VIEWER_TOKEN = "dev-secret-token"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
ADMIN_NAME = "admin"  # display_name of the admin stub user
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

ITEM_A = "ITEM-707A"
SITE_A = "SITE-707-1"
ITEM_B = "ITEM-707B"
SITE_B = "SITE-707-2"

# column -> (data_type, character_maximum_length, is_nullable)
EXPECTED_SHAPE = {
    "item_code": ("character varying", 64, "NO"),
    "site_code": ("character varying", 64, "NO"),
    "adopted_by": ("character varying", 255, "NO"),
    "adopted_by_name": ("character varying", 255, "YES"),
    "adopted_at": ("timestamp with time zone", None, "NO"),
    "note": ("text", None, "YES"),
}


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
                "AND table_name = 'empirical_adoptions' "
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
                "WHERE i.indrelid = 'empirical_adoptions'::regclass "
                "AND i.indisprimary "
                "ORDER BY array_position(i.indkey, a.attnum)"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _foreign_keys() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'empirical_adoptions'::regclass "
                "AND contype = 'f' ORDER BY conname"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _adopted_at_default() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_default FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'empirical_adoptions' "
                "AND column_name = 'adopted_at'"
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
                "AND tablename = 'empirical_adoptions'"
            )
            return cur.fetchone()[0] == 1
    finally:
        conn.close()


def _seed_item_site(item_code: str, site_code: str) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO item_sites (id, item_code, site_code) "
                "VALUES (%s, %s, %s)",
                (str(uuid4()), item_code, site_code),
            )
        conn.commit()
    finally:
        conn.close()


def _stored_rows() -> list:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM empirical_adoptions ORDER BY item_code, site_code"
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def test_issue707_freeform(client):
    _seed_item_site(ITEM_A, SITE_A)
    _seed_item_site(ITEM_B, SITE_B)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    planner = TestClient(app, headers={"Authorization": f"Bearer {PLANNER_TOKEN}"})
    anon = TestClient(app)

    # -- 1. The migration: 0025 is the only head, applied; the table has
    #    the documented shape, the pair is the primary key, and there is
    #    no foreign key to item_sites (no ON DELETE CASCADE).  The
    #    downgrade drops the table; the upgrade back re-creates it. ----
    cfg = _alembic_config()
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert heads[0] == HEAD
    assert _alembic_revision() == HEAD
    assert _table_columns() == EXPECTED_SHAPE
    assert _primary_key_columns() == ["item_code", "site_code"]
    assert _adopted_at_default() == "now()"
    assert _foreign_keys() == []

    try:
        alembic_command.downgrade(cfg, PREV_HEAD)
        assert _alembic_revision() == PREV_HEAD
        assert not _table_exists()
    finally:
        alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == HEAD
    assert _table_columns() == EXPECTED_SHAPE
    assert _primary_key_columns() == ["item_code", "site_code"]
    assert _foreign_keys() == []

    # -- 2. Adopting: an admin PUT with a note stores the adoption, with
    #    adopted_by the admin token's subject and adopted_by_name its
    #    display name, as planner decisions record decided_by /
    #    decided_by_name. -----------------------------------------------
    response = admin.put(
        URL,
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "note": "adopted on the floor",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["item_code"] == ITEM_A
    assert body["site_code"] == SITE_A
    assert body["adopted_by"] == ADMIN_SUB
    assert body["adopted_by_name"] == ADMIN_NAME
    assert body["note"] == "adopted on the floor"
    datetime.fromisoformat(body["adopted_at"])  # ISO-8601, parseable

    # A second PUT for the same pair replaces the note without a
    # second row.
    response = admin.put(
        URL,
        json={"item_code": ITEM_A, "site_code": SITE_A, "note": "re-adopted"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["note"] == "re-adopted"
    assert response.json()["adopted_by"] == ADMIN_SUB
    rows = _stored_rows()
    assert len(rows) == 1
    assert (rows[0]["item_code"], rows[0]["site_code"]) == (ITEM_A, SITE_A)
    assert rows[0]["note"] == "re-adopted"

    # -- 3. Listing: the planner (non-admin) token lists the one
    #    adoption. --------------------------------------------------------
    response = planner.get(URL)
    assert response.status_code == 200, response.text
    listed = response.json()
    assert len(listed) == 1
    assert listed[0]["item_code"] == ITEM_A
    assert listed[0]["site_code"] == SITE_A
    assert listed[0]["adopted_by"] == ADMIN_SUB
    assert listed[0]["adopted_by_name"] == ADMIN_NAME
    assert listed[0]["note"] == "re-adopted"

    # -- 4. Refusals ------------------------------------------------------
    # A PUT for a pair not in item_sites answers 404 — the item-site's
    # existence is validated at the application level, not by a
    # foreign key.
    response = admin.put(
        URL,
        json={
            "item_code": "GHOST-ITEM",
            "site_code": "GHOST-SITE",
            "note": "no such item site",
        },
    )
    assert response.status_code == 404, response.text
    assert len(_stored_rows()) == 1  # nothing stored

    # A planner (non-admin) PUT or DELETE answers 403.
    response = planner.put(
        URL,
        json={"item_code": ITEM_A, "site_code": SITE_A, "note": "planner write"},
    )
    assert response.status_code == 403, response.text
    assert response.json() == {
        "detail": "Role 'admin' is required to perform this action."
    }
    response = planner.delete(URL, params={"item_code": ITEM_A, "site_code": SITE_A})
    assert response.status_code == 403, response.text
    assert len(_stored_rows()) == 1  # nothing written or removed

    # No token answers 401.
    assert anon.get(URL).status_code == 401
    response = anon.put(URL, json={"item_code": ITEM_A, "site_code": SITE_A})
    assert response.status_code == 401
    response = anon.delete(URL, params={"item_code": ITEM_A, "site_code": SITE_A})
    assert response.status_code == 401

    # A NUL byte in any field answers 422, as in the planner decisions —
    # never a 500.
    for bad_body in (
        {"item_code": ITEM_A + "\x00", "site_code": SITE_A},
        {"item_code": ITEM_A, "site_code": SITE_A + "\x00"},
        {"item_code": ITEM_A, "site_code": SITE_A, "note": "a\x00b"},
    ):
        response = admin.put(URL, json=bad_body)
        assert response.status_code == 422, (bad_body, response.text)
    response = admin.delete(
        URL, params={"item_code": ITEM_A + "\x00", "site_code": SITE_A}
    )
    assert response.status_code == 422, response.text
    # Over-long codes answer 422 too.
    response = admin.put(URL, json={"item_code": "x" * 65, "site_code": SITE_A})
    assert response.status_code == 422, response.text
    assert len(_stored_rows()) == 1

    # -- 5. Withdrawing: an admin DELETE answers 204 and the list is
    #    empty; a second DELETE answers 404. ------------------------------
    response = admin.delete(URL, params={"item_code": ITEM_A, "site_code": SITE_A})
    assert response.status_code == 204, response.text
    assert _stored_rows() == []
    assert planner.get(URL).json() == []
    response = admin.delete(URL, params={"item_code": ITEM_A, "site_code": SITE_A})
    assert response.status_code == 404, response.text

    # -- 6. The schema file lists both paths (the one path with its
    #    three operations) and the new models. ----------------------------
    spec = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    assert set(spec["paths"][URL]) == {"get", "put", "delete"}
    assert (
        spec["paths"][URL]["put"]["requestBody"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        == "#/components/schemas/EmpiricalAdoptionBody"
    )
    assert (
        spec["paths"][URL]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["items"]["$ref"]
        == "#/components/schemas/EmpiricalAdoption"
    )
    assert "EmpiricalAdoption" in spec["components"]["schemas"]
    assert "EmpiricalAdoptionBody" in spec["components"]["schemas"]

    # -- 7. Identity: the proof also runs as a NON-ADMIN (role 'user').
    #    Second pass after a full reload: the page boots the production
    #    way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only control is
    #    refused 403 (nothing stored) while the page still renders, and
    #    no token is still a 401. ------------------------------------------
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["sub"] == VIEWER_SUB
    assert me.json()["user"]["roles"] == ["user"]
    assert "admin" not in me.json()["user"]["roles"]
    assert "admin" not in me.json()["user"]["groups"]

    response = viewer.put(
        URL,
        json={"item_code": ITEM_B, "site_code": SITE_B, "note": "viewer write"},
    )
    assert response.status_code == 403, response.text
    response = viewer.delete(URL, params={"item_code": ITEM_B, "site_code": SITE_B})
    assert response.status_code == 403, response.text
    # The page still renders for the viewer: the listing answers 200
    # (empty — the viewer wrote nothing).
    assert viewer.get(URL).status_code == 200
    assert viewer.get(URL).json() == []
    response = anon_reloaded.get(URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"
    assert _stored_rows() == []
