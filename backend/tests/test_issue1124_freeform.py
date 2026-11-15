r"""Issue #1124 proof: the guidance publication behind a Bearer-gated API.

Everything is exercised against the test database through the app's
test client with the two development stub users: the admin stub
(``dev-secret-token:admin``, sub ``dev-admin-1``, roles admin+user,
username ``admin``) publishes the guidance, and the non-admin stub
(role ``user``, sub ``dev-user-1``, username ``developer``) gets
refused the publication.

Proved here:

  * the migration — the revision that creates the tables (found by
    walking the Alembic history; the proof never names it and never
    asserts it is the head) creates ``guidance_publications`` (bigint
    identity ``id`` primary key, text ``source`` not null with a CHECK
    that it is not empty, timestamptz ``published_at`` not null
    defaulting to now(), text ``published_by`` not null, integer
    ``item_count`` not null with ``item_count > 0``) and
    ``guidance_versions`` (bigint identity ``id`` primary key; ``key``,
    ``hash``, ``type``, ``scope``, ``area``, ``area_title``, ``title``,
    ``statement``, ``status`` and ``decided_by`` each text not null;
    date ``decided_on`` not null; ``decided_under``, ``decided_ref``
    and ``authority`` each text nullable; ``first_publication_id`` and
    ``last_publication_id`` each bigint not null, foreign keys to
    ``guidance_publications.id``; CHECKs that ``type`` is one of
    direction/roadmap/design/boundary, that ``status`` is one of
    proposed/active/retired, and that ``hash`` is exactly 10 lowercase
    hex characters; an index on ``key``); the migration history has
    exactly one head and the database's ``alembic_version`` equals the
    shared ``CURRENT_HEAD`` pin; the database itself refuses a
    ``type`` of initiative, a ``status`` of draft, a ``hash`` of XYZ,
    a ``first_publication_id`` naming no publication, an empty
    ``source`` and an ``item_count`` of 0; both tables hold no row at
    first;
  * the first publication — the admin's PUT of ``commit-1`` with three
    items answers 200 with ``changed`` true, ``items`` 3,
    ``new_versions`` 3, ``withdrawn`` 0 and an integer
    ``publication_id``; ``guidance_publications`` holds one row with
    that id, the source as sent, ``item_count`` 3, ``published_by``
    the caller's username and a ``published_at`` carrying a UTC
    offset; ``guidance_versions`` holds three rows whose text columns
    are exactly the strings sent, whose optional columns are null and
    whose both publication ids are that id;
  * the same items again change nothing: ``changed`` false, the same
    ``publication_id``, ``new_versions`` 0, ``withdrawn`` 0, the
    tables still hold one row and three rows;
  * a reworded item is a new version: the reworded ``SHOP-D-1`` (new
    statement, the hash worked out from the item's own words) answers
    ``changed`` true, ``new_versions`` 1, ``withdrawn`` 0 and a new
    ``publication_id``; the table then holds four rows — the first
    ``SHOP-D-1`` row keeps its old statement and both publication ids
    of the first publication, the new one has both ids of the second,
    and the other two rows have the first as ``first_publication_id``
    and the second as ``last_publication_id``;
  * a changed status is a new version (the ``SHOP-R-1`` row doubles),
    a changed decision record is a new version (the new
    ``SHOP-N-1`` row carries ``decided_under``);
  * a withdrawn item stays: dropping ``SHOP-N-1`` from the body
    answers ``changed`` true, ``items`` 2, ``new_versions`` 0 and
    ``withdrawn`` 1, no row is deleted and both ``SHOP-N-1`` rows
    keep the ``last_publication_id`` they had; an item that returns
    gets a new row (``SHOP-N-1`` then has three rows, ``withdrawn``
    0);
  * whole or not at all: the first publication's three items with the
    last one's hash replaced by 0000000000 answer 422 and neither
    table has a new row;
  * the longest texts are accepted: one item with a 90-character
    title and a 1200-character statement, the hash the proof works
    out by the rule above using ``hashlib``, answers 200 and its row
    is stored exactly;
  * a signed-in non-admin gets 403 on a valid body and stores
    nothing — and gets 403, not 422, on a body that would be refused,
    while without a token a body that would be refused answers 401:
    the 401/403 come before any validation;
  * the refused input, each 422 for the admin with both tables
    unchanged afterwards: a body that is a list; a body without
    ``source`` or without ``items``; a ``source`` that is empty, 121
    characters long, with a leading space, with two spaces in a row,
    with a tab inside, containing U+0000, a lone surrogate sent as
    the JSON string "\ud800" or the number 5; ``items`` an empty
    list, an object, a list of 2001 items or a list holding a string;
    and, each in a body whose one item is otherwise valid, a broken
    ``key`` (lowercase, a letter the pattern does not name, a five
    digit number, the number 5, missing), a broken ``type``
    (initiative, roadmap while the key says D, missing), a ``scope``
    that is not the key's part before its first hyphen, a broken
    ``area`` (capitalized, 50 characters), broken ``area_title`` /
    ``title`` / ``statement`` lengths and plain text, a ``status`` of
    draft, a broken ``decided_by``, a broken ``decided_on`` (a
    non-date, a different shape, a number), a broken ``decided_under``
    or ``decided_ref`` length, a broken ``authority`` and a broken
    ``hash`` (uppercase, 9 characters, 11 characters, the number 5);
    the same key twice in one body; and three raw JSON bodies holding
    Infinity, NaN and 1e400 as the value of an extra key, each
    answering 422 and never 500 — and the same three tokens as the
    sole element of the required ``items`` list, refused by the
    guard before the body model's own 422 could echo one back,
    each 422 and never 500;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role ``user``, asserted via ``GET /api/auth/me``) in a second
    pass after a full reload (a fresh ``TestClient`` booted the
    production way — the token the page would take from the URL hash
    as ``#access_token=...``, with ``GET /api/auth/config`` answering
    publicly and ``GET /api/auth/me`` resolving the viewer): the
    admin-only publication is refused with 403 (nothing stored) while
    the app still serves the viewer — and a request without a token
    still gets 401.
"""

import hashlib
import json
import os
from datetime import date

import psycopg2
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_USERNAME = "admin"  # the admin stub user's username
ADMIN_DETAIL = "Role 'admin' is required to perform this action."

URL = "/api/guidance"
_PUBLICATIONS = "guidance_publications"
_VERSIONS = "guidance_versions"
_PUBLICATION_COLUMNS = {
    "id",
    "source",
    "published_at",
    "published_by",
    "item_count",
}
_VERSION_COLUMNS = {
    "id",
    "key",
    "hash",
    "type",
    "scope",
    "area",
    "area_title",
    "title",
    "statement",
    "status",
    "decided_by",
    "decided_on",
    "decided_under",
    "decided_ref",
    "authority",
    "first_publication_id",
    "last_publication_id",
}


def _hash(type: str, title: str, statement: str) -> str:
    """The hash the rule works out: first 10 lowercase hex of SHA-256.

    The SHA-256 of the UTF-8 bytes of ``type + "\n" + title + "\n" +
    statement + "\n"``, as the proof must work it out itself.
    """
    words = f"{type}\n{title}\n{statement}\n"
    return hashlib.sha256(words.encode("utf-8")).hexdigest()[:10]


def _item(
    key: str,
    type: str,
    title: str,
    statement: str,
    item_hash: str | None = None,
    **overrides,
) -> dict:
    """One of the items used below (scope SHOP, area stack, active,
    decided by github:ceo on 2026-11-01, no optional field)."""
    item = {
        "key": key,
        "type": type,
        "scope": key.split("-", 1)[0],
        "area": "stack",
        "area_title": "Software stack",
        "title": title,
        "statement": statement,
        "status": "active",
        "decided_by": "github:ceo",
        "decided_on": "2026-11-01",
        "hash": item_hash if item_hash is not None else _hash(type, title, statement),
    }
    item.update(overrides)
    return item


ITEM_DESIGN = _item(
    "SHOP-D-1",
    "design",
    "One database product",
    "Every service stores its data in PostgreSQL.",
)
ITEM_ROADMAP = _item("SHOP-R-1", "roadmap", "T", "S.")
ITEM_DIRECTION = _item("SHOP-N-1", "direction", "Café", "Naïve ≤ 5")
FIRST_ITEMS = [ITEM_DESIGN, ITEM_ROADMAP, ITEM_DIRECTION]

# The reworded design item: new statement, hash worked out from the
# item's own words by the rule above.
REWORDED_DESIGN = _item(
    "SHOP-D-1",
    "design",
    "One database product",
    "Every service stores its data in PostgreSQL 15.",
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


def _table_columns(table: str) -> dict:
    """The table's columns as (data_type, max_length, nullability, default, identity)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable, column_default, is_identity "
                "FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (table,),
            )
            return {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in cur.fetchall()}
    finally:
        conn.close()


def _check_constraints(table: str) -> dict[str, str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT con.conname, pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "WHERE rel.relname = %s AND con.contype = 'c' "
                "ORDER BY con.conname",
                (table,),
            )
            return {name: definition for name, definition in cur.fetchall()}
    finally:
        conn.close()


def _foreign_keys(table: str) -> dict[str, str]:
    """The table's foreign keys as constraint name -> definition."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT con.conname, pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "WHERE rel.relname = %s AND con.contype = 'f' "
                "ORDER BY con.conname",
                (table,),
            )
            return {name: definition for name, definition in cur.fetchall()}
    finally:
        conn.close()


def _index_columns(table: str) -> list[list[str]]:
    """The table's indexes as the list of each index's columns."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT i.indexrelid, a.attnum, a.attname "
                "FROM pg_index i "
                "JOIN pg_class t ON t.oid = i.indrelid "
                "JOIN pg_attribute a ON a.attrelid = t.oid "
                "AND a.attnum = ANY(i.indkey) "
                "WHERE t.relname = %s "
                "ORDER BY i.indexrelid, a.attnum",
                (table,),
            )
            by_index: dict[int, list[str]] = {}
            for relid, _attnum, attname in cur.fetchall():
                by_index.setdefault(relid, []).append(attname)
            return [columns for columns in by_index.values()]
    finally:
        conn.close()


def _rows(table: str) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT * FROM {table} ORDER BY id")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _row_count(table: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table}")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _creating_revision():
    """The revision that creates the tables, found by walking the history.

    The proof never names the revision: the Alembic history is walked
    and the one revision whose module creates ``guidance_versions`` is
    the one this proof is about.  The proof never asserts that it is
    the head or the only head, so a later migration cannot make it
    fail.
    """
    script = ScriptDirectory(_MIGRATIONS_DIR)
    for rev in script.walk_revisions():
        path = getattr(rev.module, "__file__", None)
        if not path:
            continue
        with open(path, encoding="utf-8") as fh:
            source = fh.read()
        if "create_table" in source and _VERSIONS in source:
            return rev
    raise AssertionError(f"no revision creates {_VERSIONS}")


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
        cur.execute("SAVEPOINT issue1124")
        try:
            cur.execute(sql, params)
        except psycopg2.Error as exc:
            assert type(exc) is expected, f"{label}: {type(exc).__name__}"
            cur.execute("ROLLBACK TO SAVEPOINT issue1124")
        else:
            raise AssertionError(f"{label}: the database accepted the row")
        finally:
            cur.execute("RELEASE SAVEPOINT issue1124")


def test_issue1124_freeform(client):
    # -- The migration: one head, the database sits on the shared pin,
    #    and the revision that creates the tables is found by walking
    #    the history (never named here, never asserted to be the
    #    head). ---------------------------------------------------------
    assert _alembic_revision() == CURRENT_HEAD
    heads = ScriptDirectory(_MIGRATIONS_DIR).get_heads()
    assert list(heads) == [CURRENT_HEAD]
    creating = _creating_revision()
    # The creating revision is reached walking the history down from
    # the head (it need not be the head itself).
    walked = {rev.revision for rev in ScriptDirectory(_MIGRATIONS_DIR).walk_revisions()}
    assert creating.revision in walked
    # The creating revision builds on a real earlier revision.
    script = ScriptDirectory(_MIGRATIONS_DIR)
    assert creating.down_revision is not None
    assert script.get_revision(creating.down_revision) is not None
    # Both tables exist.
    assert {_PUBLICATIONS, _VERSIONS} <= _pg_tables()

    # -- The tables have exactly the documented shape. ------------------
    columns = _table_columns(_PUBLICATIONS)
    assert set(columns) == _PUBLICATION_COLUMNS
    assert columns["id"] == ("bigint", None, "NO", None, "YES")
    assert columns["source"] == ("text", None, "NO", None, "NO")
    assert columns["published_at"] == (
        "timestamp with time zone",
        None,
        "NO",
        "now()",
        "NO",
    )
    assert columns["published_by"] == ("text", None, "NO", None, "NO")
    assert columns["item_count"] == ("integer", None, "NO", None, "NO")

    columns = _table_columns(_VERSIONS)
    assert set(columns) == _VERSION_COLUMNS
    assert columns["id"] == ("bigint", None, "NO", None, "YES")
    for column in (
        "key",
        "hash",
        "type",
        "scope",
        "area",
        "area_title",
        "title",
        "statement",
        "status",
        "decided_by",
    ):
        assert columns[column] == ("text", None, "NO", None, "NO"), column
    assert columns["decided_on"] == ("date", None, "NO", None, "NO")
    for column in ("decided_under", "decided_ref", "authority"):
        assert columns[column] == ("text", None, "YES", None, "NO"), column
    for column in ("first_publication_id", "last_publication_id"):
        assert columns[column] == ("bigint", None, "NO", None, "NO"), column

    checks = _check_constraints(_PUBLICATIONS)
    assert set(checks) == {
        "ck_guidance_publications_source",
        "ck_guidance_publications_item_count",
    }
    assert "source <> ''" in checks["ck_guidance_publications_source"]
    assert "item_count > 0" in checks["ck_guidance_publications_item_count"]

    checks = _check_constraints(_VERSIONS)
    assert set(checks) == {
        "ck_guidance_versions_type",
        "ck_guidance_versions_status",
        "ck_guidance_versions_hash",
    }
    type_check = checks["ck_guidance_versions_type"]
    assert "direction" in type_check
    assert "roadmap" in type_check
    assert "design" in type_check
    assert "boundary" in type_check
    status_check = checks["ck_guidance_versions_status"]
    assert "proposed" in status_check
    assert "active" in status_check
    assert "retired" in status_check
    hash_check = checks["ck_guidance_versions_hash"]
    assert "[0-9a-f]" in hash_check and "10" in hash_check

    # The publication ids are foreign keys to guidance_publications.id,
    # and key is indexed.
    fkeys = _foreign_keys(_VERSIONS)
    assert set(fkeys) == {
        "fk_guidance_versions_first_publication",
        "fk_guidance_versions_last_publication",
    }
    assert "first_publication_id" in fkeys["fk_guidance_versions_first_publication"]
    assert "last_publication_id" in fkeys["fk_guidance_versions_last_publication"]
    for definition in fkeys.values():
        assert "guidance_publications(id)" in definition
    assert ["key"] in _index_columns(_VERSIONS)

    # -- The database's own rules: nothing below is ever committed. ----
    version_insert = (
        "INSERT INTO guidance_versions "
        "(key, hash, type, scope, area, area_title, title, statement, "
        "status, decided_by, decided_on, first_publication_id, "
        "last_publication_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    base_params = (
        "SHOP-D-1",
        "caddbcd58d",
        "design",
        "SHOP",
        "stack",
        "Software stack",
        "One database product",
        "Every service stores its data in PostgreSQL.",
        "active",
        "engine",
        date(2026, 11, 1),
        1,
        1,
    )
    conn = get_connection()
    try:
        _expect_db_rejection(
            conn,
            version_insert,
            base_params[:2] + ("initiative",) + base_params[3:],
            psycopg2.errors.CheckViolation,
            "type initiative",
        )
        _expect_db_rejection(
            conn,
            version_insert,
            base_params[:8] + ("draft",) + base_params[9:],
            psycopg2.errors.CheckViolation,
            "status draft",
        )
        _expect_db_rejection(
            conn,
            version_insert,
            ("SHOP-D-1", "XYZ") + base_params[2:],
            psycopg2.errors.CheckViolation,
            "hash XYZ",
        )
        _expect_db_rejection(
            conn,
            version_insert,
            base_params,
            psycopg2.errors.ForeignKeyViolation,
            "first_publication_id naming no publication",
        )
        publication_insert = (
            "INSERT INTO guidance_publications "
            "(source, published_by, item_count) VALUES (%s, %s, %s)"
        )
        _expect_db_rejection(
            conn,
            publication_insert,
            ("", "admin", 1),
            psycopg2.errors.CheckViolation,
            "empty source",
        )
        _expect_db_rejection(
            conn,
            publication_insert,
            ("commit-1", "admin", 0),
            psycopg2.errors.CheckViolation,
            "item_count 0",
        )
    finally:
        conn.rollback()
        conn.close()

    # -- Empty at first: both tables hold no row. -----------------------
    assert _row_count(_PUBLICATIONS) == 0
    assert _row_count(_VERSIONS) == 0

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    # -- The first publication: everything is new. ----------------------
    response = admin.put(URL, json={"source": "commit-1", "items": FIRST_ITEMS})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert isinstance(body["publication_id"], int)
    first_publication = body["publication_id"]
    assert body["items"] == 3
    assert body["new_versions"] == 3
    assert body["withdrawn"] == 0

    publications = _rows(_PUBLICATIONS)
    assert len(publications) == 1
    publication = publications[0]
    assert publication["id"] == first_publication
    assert publication["source"] == "commit-1"
    assert publication["item_count"] == 3
    assert publication["published_by"] == ADMIN_USERNAME
    assert publication["published_at"].utcoffset() is not None

    versions = _rows(_VERSIONS)
    assert len(versions) == 3
    by_key = {row["key"]: row for row in versions}
    assert set(by_key) == {"SHOP-D-1", "SHOP-R-1", "SHOP-N-1"}
    for item in FIRST_ITEMS:
        row = by_key[item["key"]]
        for column in (
            "hash",
            "type",
            "scope",
            "area",
            "area_title",
            "title",
            "statement",
            "status",
            "decided_by",
        ):
            assert row[column] == item[column], (item["key"], column)
        assert row["decided_on"] == date(2026, 11, 1)
        assert row["decided_under"] is None
        assert row["decided_ref"] is None
        assert row["authority"] is None
        assert row["first_publication_id"] == first_publication
        assert row["last_publication_id"] == first_publication

    # -- The same again changes nothing. ---------------------------------
    response = admin.put(URL, json={"source": "commit-2", "items": FIRST_ITEMS})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is False
    assert body["publication_id"] == first_publication
    assert body["items"] == 3
    assert body["new_versions"] == 0
    assert body["withdrawn"] == 0
    assert _row_count(_PUBLICATIONS) == 1
    assert _row_count(_VERSIONS) == 3

    # -- A reworded item is a new version. -------------------------------
    reworded_items = [REWORDED_DESIGN, ITEM_ROADMAP, ITEM_DIRECTION]
    response = admin.put(URL, json={"source": "commit-3", "items": reworded_items})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    second_publication = body["publication_id"]
    assert second_publication != first_publication
    assert body["new_versions"] == 1
    assert body["withdrawn"] == 0

    versions = _rows(_VERSIONS)
    assert len(versions) == 4
    design_rows = [row for row in versions if row["key"] == "SHOP-D-1"]
    assert len(design_rows) == 2
    old_design = next(
        row
        for row in design_rows
        if row["statement"] == "Every service stores its data in PostgreSQL."
    )
    new_design = next(
        row
        for row in design_rows
        if row["statement"] == "Every service stores its data in PostgreSQL 15."
    )
    assert old_design["first_publication_id"] == first_publication
    assert old_design["last_publication_id"] == first_publication
    assert new_design["first_publication_id"] == second_publication
    assert new_design["last_publication_id"] == second_publication
    for row in versions:
        if row["key"] in ("SHOP-R-1", "SHOP-N-1"):
            assert row["first_publication_id"] == first_publication
            assert row["last_publication_id"] == second_publication

    # -- A changed status is a new version. ------------------------------
    retired_roadmap = dict(ITEM_ROADMAP, status="retired")
    response = admin.put(
        URL,
        json={
            "source": "commit-4",
            "items": [REWORDED_DESIGN, retired_roadmap, ITEM_DIRECTION],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    third_publication = body["publication_id"]
    assert body["new_versions"] == 1
    roadmap_rows = [row for row in _rows(_VERSIONS) if row["key"] == "SHOP-R-1"]
    assert len(roadmap_rows) == 2
    assert {row["status"] for row in roadmap_rows} == {"active", "retired"}
    assert (
        next(row for row in roadmap_rows if row["status"] == "retired")[
            "last_publication_id"
        ]
        == third_publication
    )

    # -- A changed decision record is a new version. ---------------------
    delegated_direction = dict(
        ITEM_DIRECTION, decided_by="model:claude", decided_under="delegation"
    )
    response = admin.put(
        URL,
        json={
            "source": "commit-5",
            "items": [REWORDED_DESIGN, retired_roadmap, delegated_direction],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    fourth_publication = body["publication_id"]
    assert body["new_versions"] == 1
    direction_rows = [row for row in _rows(_VERSIONS) if row["key"] == "SHOP-N-1"]
    assert len(direction_rows) == 2
    new_direction = next(
        row for row in direction_rows if row["decided_by"] == "model:claude"
    )
    assert new_direction["decided_under"] == "delegation"
    assert new_direction["first_publication_id"] == fourth_publication
    assert new_direction["last_publication_id"] == fourth_publication
    versions_before_withdrawal = _rows(_VERSIONS)

    # -- A withdrawn item stays. ------------------------------------------
    response = admin.put(
        URL,
        json={
            "source": "commit-6",
            "items": [REWORDED_DESIGN, retired_roadmap],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["items"] == 2
    assert body["new_versions"] == 0
    assert body["withdrawn"] == 1
    # No row was deleted, and both SHOP-N-1 rows keep the
    # last_publication_id they had.
    versions_after_withdrawal = _rows(_VERSIONS)
    assert len(versions_after_withdrawal) == len(versions_before_withdrawal)
    # Both SHOP-N-1 rows keep the last_publication_id they had.
    old_directions = [
        row for row in versions_before_withdrawal if row["key"] == "SHOP-N-1"
    ]
    new_directions = [
        row for row in versions_after_withdrawal if row["key"] == "SHOP-N-1"
    ]
    assert old_directions == new_directions

    # -- An item that returns gets a new row. -----------------------------
    response = admin.put(
        URL,
        json={
            "source": "commit-7",
            "items": [REWORDED_DESIGN, retired_roadmap, delegated_direction],
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["new_versions"] == 1
    assert body["withdrawn"] == 0
    direction_rows = [row for row in _rows(_VERSIONS) if row["key"] == "SHOP-N-1"]
    assert len(direction_rows) == 3

    # -- Whole or not at all: one broken hash refuses the whole
    #    publication and neither table gains a row. -----------------------
    broken_items = [
        dict(ITEM_DESIGN),
        dict(ITEM_ROADMAP),
        dict(ITEM_DIRECTION, hash="0000000000"),
    ]
    before_counts = (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    )
    response = admin.put(URL, json={"source": "commit-8", "items": broken_items})
    assert response.status_code == 422, response.text
    assert isinstance(response.json()["detail"], list)
    assert (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    ) == before_counts

    # -- The longest texts are accepted. -----------------------------------
    long_title = "T" * 90
    long_statement = "S" * 1200
    long_item = _item(
        "SHOP-D-2",
        "design",
        long_title,
        long_statement,
        _hash("design", long_title, long_statement),
    )
    response = admin.put(URL, json={"source": "commit-9", "items": [long_item]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["new_versions"] == 1
    # The three keys current before the call are all absent from
    # this one-item body: all three are withdrawn.
    assert body["withdrawn"] == 3
    stored = next(row for row in _rows(_VERSIONS) if row["key"] == "SHOP-D-2")
    assert stored["title"] == long_title
    assert stored["statement"] == long_statement
    assert len(stored["title"]) == 90
    assert len(stored["statement"]) == 1200

    # -- A non-admin gets 403 on a valid body and stores nothing; without
    #    a token a body that would be refused answers 401 (the 401/403
    #    come before any validation). ---------------------------------------
    before_counts = (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    )
    response = viewer.put(URL, json={"source": "viewer-1", "items": FIRST_ITEMS})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    assert (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    ) == before_counts
    # The 403 comes before any validation: a body that would be
    # refused for the admin (items missing) is refused with 403, not
    # 422, for the non-admin, and stores nothing.
    response = viewer.put(URL, json={"source": "viewer-1b"})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    assert (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    ) == before_counts
    response = anon.put(URL, json={"source": "no-token"})
    assert response.status_code == 401, response.text

    # -- The refused input: each 422 for the admin, both tables unchanged
    #    afterwards. ---------------------------------------------------------
    before_counts = (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    )

    def refuse(body, label):
        response = admin.put(URL, json=body)
        assert response.status_code == 422, f"{label}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, f"{label}: {response.text}"
        assert (
            _row_count(_PUBLICATIONS),
            _row_count(_VERSIONS),
        ) == before_counts, label

    def refuse_item(label, overrides, missing=()):
        item = dict(ITEM_DESIGN)
        for field in missing:
            item.pop(field)
        item.update(overrides)
        refuse({"source": "refused", "items": [item]}, label)

    # A body that is not an object, and missing top-level keys.
    refuse([1, 2, 3], "body is a list")
    refuse({"items": [ITEM_DESIGN]}, "body without source")
    refuse({"source": "refused"}, "body without items")

    # A broken source.
    for label, source in (
        ("source empty", ""),
        ("source 121 characters", "a" * 121),
        ("source leading space", " a"),
        ("source two spaces in a row", "a  b"),
        ("source tab inside", "a\tb"),
        ("source NUL inside", "a\u0000b"),
        ("source the number 5", 5),
    ):
        refuse({"source": source, "items": [ITEM_DESIGN]}, label)

    # A lone surrogate, sent as the JSON string "\ud800" (the only
    # way a hostile client can send one): 422, never a 500, and the
    # unstoreable value refused rather than echoed back.
    raw = '{"source": "\\ud800", "items": [' + json.dumps(ITEM_DESIGN) + "]}"
    response = admin.put(URL, content=raw, headers={"Content-Type": "application/json"})
    assert response.status_code == 422, "lone surrogate: " + response.text
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail, response.text
    assert "\ud800" not in response.text, response.text
    assert (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    ) == before_counts

    # A broken items list.
    refuse({"source": "refused", "items": []}, "items empty list")
    refuse({"source": "refused", "items": {}}, "items an object")
    bulk_items = [_item(f"K{i:03d}-D-1", "design", "T", "S.") for i in range(2001)]
    refuse({"source": "refused", "items": bulk_items}, "items 2001 long")
    refuse({"source": "refused", "items": ["not an object"]}, "items holds a string")

    # Each broken item field, in a body whose one item is otherwise valid.
    refuse_item("key lowercase", {"key": "shop-D-1", "scope": "shop"})
    refuse_item("key letter not named", {"key": "SHOP-X-1"})
    refuse_item("key five digit number", {"key": "SHOP-D-12345"})
    refuse_item("key the number 5", {"key": 5})
    refuse_item("key missing", {}, missing=("key",))
    refuse_item("type initiative", {"type": "initiative"})
    refuse_item("type roadmap while the key says D", {"type": "roadmap"})
    refuse_item("type missing", {}, missing=("type",))
    refuse_item("scope not the key's prefix", {"scope": "SHOPS"})
    refuse_item("area capitalized", {"area": "Stack"})
    refuse_item("area 50 characters", {"area": "a" * 50})
    refuse_item("area_title empty", {"area_title": ""})
    refuse_item("area_title 91 characters", {"area_title": "a" * 91})
    refuse_item("title empty", {"title": ""})
    refuse_item("title 91 characters", {"title": "a" * 91})
    refuse_item("title trailing space", {"title": "a" * 89 + " "})
    refuse_item("title line feed inside", {"title": "a\nb"})
    refuse_item("statement empty", {"statement": ""})
    refuse_item("statement 1201 characters", {"statement": "a" * 1201})
    refuse_item("statement two spaces in a row", {"statement": "a  b"})
    refuse_item("status draft", {"status": "draft"})
    refuse_item("decided_by ceo", {"decided_by": "ceo"})
    refuse_item("decided_by github:", {"decided_by": "github:"})
    refuse_item("decided_by the number 5", {"decided_by": 5})
    refuse_item("decided_on 2026-02-30", {"decided_on": "2026-02-30"})
    refuse_item("decided_on 11/01/2026", {"decided_on": "11/01/2026"})
    refuse_item("decided_on the number 20261101", {"decided_on": 20261101})
    refuse_item("decided_under empty", {"decided_under": ""})
    refuse_item("decided_under 41 characters", {"decided_under": "a" * 41})
    refuse_item("decided_ref 81 characters", {"decided_ref": "a" * 81})
    refuse_item("authority capitalized", {"authority": "Executive"})
    refuse_item("hash uppercase", {"hash": "CADDBCD58D"})
    refuse_item("hash 9 characters", {"hash": "caddbcd58"})
    refuse_item("hash 11 characters", {"hash": "caddbcd58e"})
    refuse_item("hash the number 5", {"hash": 5})
    # The same key twice in one body.
    refuse(
        {"source": "refused", "items": [ITEM_DESIGN, dict(ITEM_DESIGN)]},
        "the same key twice",
    )

    # Three raw JSON bodies holding a non-finite number as the value of
    # an extra key: each answers 422 and never a 500.
    valid_raw = json.dumps({"source": "commit-1", "items": FIRST_ITEMS})
    for token in ("Infinity", "NaN", "1e400"):
        raw = valid_raw[:-1] + f', "extra": {token}}}'
        response = admin.put(
            URL, content=raw, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422, f"{token}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, f"{token}: {response.text}"
    # The same three tokens as the sole element of the required items
    # list: the guard refuses them before the body model's own 422
    # could echo one of them back (a 500).
    for token in ("Infinity", "NaN", "1e400"):
        raw = f'{{"source": "commit-1", "items": [{token}]}}'
        response = admin.put(
            URL, content=raw, headers={"Content-Type": "application/json"}
        )
        assert response.status_code == 422, f"{token}: {response.text}"
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, f"{token}: {response.text}"
    assert (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    ) == before_counts

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only control is
    #    refused 403 while the app still serves the viewer. --------------
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
    # The admin-only control is absent for the viewer: 403, nothing
    # stored.
    before_counts = (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    )
    response = viewer_reloaded.put(
        URL, json={"source": "viewer-2", "items": FIRST_ITEMS}
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    assert (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    ) == before_counts
    # And a request without a token still gets 401.
    assert anon.put(URL, json={"source": "no-token"}).status_code == 401
