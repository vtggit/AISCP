"""Issue #1511 proof: the on-hand snapshot of the capture date.

``on_hand_snapshots`` holds, per capture date and per item-site, the
``on_hand`` row of the pair captured under that date: its
``quantity_on_hand``, its ``source_system`` (null when the ERP
reported none) and the row's own ``as_of``.  The maintenance entry
point ``app.supply.on_hand_snapshots.capture_on_hand_snapshot()``
(not a route) stores every ``on_hand`` row under one date — the UTC
date of the latest ``as_of`` among the ``on_hand`` rows — replacing
that date's rows wholesale: a second capture of the same date is
idempotent, a pair no longer in ``on_hand`` leaves the date's
snapshot, and rows of every other date are never touched.

Proved here, against the test database migrated to head:

  * the migration — the database has one head, equal to
    ``CURRENT_HEAD`` imported from ``tests/migration_head.py`` (the
    proof never writes the revision name as a literal), exactly one
    successor of the previous revision, and ``on_hand_snapshots``
    with exactly the documented columns (``snapshot_on`` date,
    ``item_code`` and ``site_code`` varchar(64), ``quantity_on_hand``
    double precision and ``as_of`` timestamptz, every one not null,
    ``source_system`` varchar(64) nullable) and the (``snapshot_on``,
    ``item_code``, ``site_code``) primary key;
  * the rows — three ``on_hand`` rows with item codes of this proof's
    own, every NOT NULL column filled and each ``as_of``
    2099-01-05T10:00:00Z: A (quantity 5, source "acumatica"), B
    (quantity 0, "acumatica") and C (quantity 2.5, "odoo");
  * the first capture — it answers ``snapshot_on`` "2099-01-05" and
    ``rows`` equal to the row count of ``on_hand``, and that date
    holds A, B and C with those quantities, sources and ``as_of``;
  * the second capture of the same date — the same answer, and the
    date still holds exactly one row per pair;
  * a change — A's quantity becomes 7 and B's ``on_hand`` row is
    deleted, and a capture leaves A at 7 and no row for B on that
    date;
  * a new date — after the remaining proof rows' ``as_of`` moves to
    2099-01-06T23:30:00-07:00, a capture answers ``snapshot_on``
    "2099-01-07", the UTC date, and the 2099-01-05 rows are
    unchanged;
  * the hostile date — a fourth proof row D whose ``as_of`` is the
    year 294276 (Postgres's timestamptz maximum, far outside
    Python's date range) is captured without the capture crashing:
    the answer is ``snapshot_on`` "294276-01-01" with the three
    rows, D holds its own far ``as_of`` on that date beside A and C
    with their 2099-01-07 ``as_of``, and the 2099-01-05 rows are
    unchanged;
  * the identity requirement — the proof also runs as a NON-ADMIN
    (role ``user``) in a second pass after a full reload: the page
    boots the production way (the token it would take from the URL
    hash as ``#access_token=...``, the bare development token, with
    ``/api/auth/config`` answering publicly and ``/api/auth/me``
    resolving the viewer for that user), the admin-only control is
    absent — the admin-only plan release audit is refused for the
    viewer and the capture job is not a route at all — while the page
    still renders: the settings the page loads answer 200 for the
    viewer and unauthenticated ``/api`` traffic is still 401.

Every ``on_hand`` row and every snapshot row (the dates 2099-01-05,
2099-01-07 and 294276-01-01) the proof wrote is deleted in a
``finally``.
"""

import os
import pathlib
from datetime import UTC, date, datetime, timedelta, timezone

from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api as api_package
from app.db.connection import get_connection, get_cursor
from app.main import app
from app.supply.on_hand_snapshots import capture_on_hand_snapshot
from tests.migration_head import CURRENT_HEAD

_MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations"
)

# The previous revision: this proof's migration is its one successor,
# and the head itself is only ever named through the shared pin.
PREV_REVISION = "0054_plan_release_readback"

# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

AUDIT = "/api/aggregate/release/audit"

# The proof's item codes: every one unique to this proof.
ITEM_A, SITE_A = "ITEM-1511A", "SITE-1511-1"
ITEM_B, SITE_B = "ITEM-1511B", "SITE-1511-2"
ITEM_C, SITE_C = "ITEM-1511C", "SITE-1511-3"
ITEM_D, SITE_D = "ITEM-1511D", "SITE-1511-4"

AS_OF_ONE = datetime(2099, 1, 5, 10, 0, 0, tzinfo=UTC)
# 2099-01-06T23:30:00-07:00, which is 2099-01-07T06:30:00 in UTC: the
# capture date must be the UTC date, not the wall-clock one.
AS_OF_TWO = datetime(2099, 1, 6, 23, 30, 0, tzinfo=timezone(timedelta(hours=-7)))
# The hostile as_of: the year 294276 is Postgres's timestamptz
# maximum, far outside Python's date range — the capture must not
# crash on it.  Compared in the database, as text, never as a
# Python date or datetime.
FAR_AS_OF = "294276-01-01 00:00:00+00"
FAR_SNAPSHOT_ON = "294276-01-01"
# AS_OF_TWO in UTC: the as_of A and C carry when the far date is
# captured.
OLD_AS_OF_UTC = "2099-01-07 06:30:00+00"

# column -> (data_type, is_nullable, character_maximum_length)
EXPECTED_COLUMNS = {
    "snapshot_on": ("date", "NO", None),
    "item_code": ("character varying", "NO", 64),
    "site_code": ("character varying", "NO", 64),
    "quantity_on_hand": ("double precision", "NO", None),
    "as_of": ("timestamp with time zone", "NO", None),
    "source_system": ("character varying", "YES", 64),
}

# The dates the proof writes snapshots under — kept as text so the
# hostile far date never reaches a Python date converter.
_SNAPSHOT_DATES = ("2099-01-05", "2099-01-07", FAR_SNAPSHOT_ON)


def _insert_on_hand(item, site, quantity, source, as_of):
    conn = get_connection()
    row_id = f"id-1511-{item}"
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO on_hand (id, item_code, site_code, "
                "quantity_on_hand, as_of, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (row_id, item, site, quantity, as_of, source),
            )
        conn.commit()
    finally:
        conn.close()
    return row_id


def _update_on_hand(item, **changes):
    sets = ", ".join(f"{column} = %s" for column in changes)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE on_hand SET {sets} WHERE item_code = %s",
                (*changes.values(), item),
            )
        conn.commit()
    finally:
        conn.close()


def _delete_on_hand(item):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM on_hand WHERE item_code = %s", (item,))
        conn.commit()
    finally:
        conn.close()


def _on_hand_count():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM on_hand")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _snapshot_rows(snapshot_on):
    """The stored rows of one date, in item/site order."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT item_code, site_code, quantity_on_hand, "
                "source_system, as_of FROM on_hand_snapshots "
                "WHERE snapshot_on = %s ORDER BY item_code, site_code",
                (snapshot_on,),
            )
            return [
                (item, site, float(quantity), source, as_of)
                for (item, site, quantity, source, as_of) in cur.fetchall()
            ]
    finally:
        conn.close()


def _table_columns():
    """The table's columns as (data_type, is_nullable, varchar length)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable, "
                "character_maximum_length "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'on_hand_snapshots' "
                "ORDER BY ordinal_position"
            )
            return {r[0]: (r[1], r[2], r[3]) for r in cur.fetchall()}
    finally:
        conn.close()


def _primary_key_columns():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = 'on_hand_snapshots'::regclass "
                "AND i.indisprimary "
                "ORDER BY a.attname"
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def test_issue1511_freeform(client) -> None:
    inserted_ids: list[str] = []
    try:
        # -- The migration: one head (the shared pin), one successor of
        #    the previous revision, the table's documented shape and
        #    its primary key. ----------------------------------------------
        script_dir = ScriptDirectory(_MIGRATIONS_DIR)
        assert list(script_dir.get_heads()) == [CURRENT_HEAD]
        successors = [
            script
            for script in script_dir.walk_revisions()
            if script.down_revision == PREV_REVISION
        ]
        assert len(successors) == 1
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT version_num FROM alembic_version")
                assert cur.fetchone()[0] == CURRENT_HEAD
        finally:
            conn.close()
        assert _table_columns() == EXPECTED_COLUMNS
        assert _primary_key_columns() == ["item_code", "site_code", "snapshot_on"]

        # -- The rows: A, B and C, each as_of 2099-01-05T10:00:00Z. --------
        inserted_ids.append(
            _insert_on_hand(ITEM_A, SITE_A, 5.0, "acumatica", AS_OF_ONE)
        )
        inserted_ids.append(
            _insert_on_hand(ITEM_B, SITE_B, 0.0, "acumatica", AS_OF_ONE)
        )
        inserted_ids.append(_insert_on_hand(ITEM_C, SITE_C, 2.5, "odoo", AS_OF_ONE))

        expected_day_one = [
            (ITEM_A, SITE_A, 5.0, "acumatica", AS_OF_ONE),
            (ITEM_B, SITE_B, 0.0, "acumatica", AS_OF_ONE),
            (ITEM_C, SITE_C, 2.5, "odoo", AS_OF_ONE),
        ]

        # -- The first capture: under the UTC date of the latest as_of,
        #    every on_hand row, with its own values. ------------------------
        answer = capture_on_hand_snapshot()
        assert answer == {"snapshot_on": "2099-01-05", "rows": _on_hand_count()}
        assert answer == {"snapshot_on": "2099-01-05", "rows": 3}
        assert _snapshot_rows(date(2099, 1, 5)) == expected_day_one
        assert _snapshot_rows(date(2099, 1, 7)) == []  # the other dates are untouched

        # -- The second capture of the same date: the same answer, exactly
        #    one row per pair. ----------------------------------------------
        answer = capture_on_hand_snapshot()
        assert answer == {"snapshot_on": "2099-01-05", "rows": 3}
        rows = _snapshot_rows(date(2099, 1, 5))
        assert rows == expected_day_one
        assert {(item, site) for (item, site, _q, _s, _a) in rows} == {
            (ITEM_A, SITE_A),
            (ITEM_B, SITE_B),
            (ITEM_C, SITE_C),
        }

        # -- A change: A's quantity becomes 7 and B leaves on_hand; a
        #    capture leaves A at 7 and no row for B on the date. ------------
        _update_on_hand(ITEM_A, quantity_on_hand=7.0)
        _delete_on_hand(ITEM_B)
        answer = capture_on_hand_snapshot()
        assert answer == {"snapshot_on": "2099-01-05", "rows": 2}
        assert _snapshot_rows(date(2099, 1, 5)) == [
            (ITEM_A, SITE_A, 7.0, "acumatica", AS_OF_ONE),
            (ITEM_C, SITE_C, 2.5, "odoo", AS_OF_ONE),
        ]

        # -- A new date: the remaining proof rows' as_of moves to
        #    2099-01-06T23:30:00-07:00; the capture date is the UTC date,
        #    and the 2099-01-05 rows are unchanged. -------------------------
        _update_on_hand(ITEM_A, as_of=AS_OF_TWO)
        _update_on_hand(ITEM_C, as_of=AS_OF_TWO)
        answer = capture_on_hand_snapshot()
        assert answer == {"snapshot_on": "2099-01-07", "rows": 2}
        assert _snapshot_rows(date(2099, 1, 7)) == [
            (ITEM_A, SITE_A, 7.0, "acumatica", AS_OF_TWO),
            (ITEM_C, SITE_C, 2.5, "odoo", AS_OF_TWO),
        ]
        assert _snapshot_rows(date(2099, 1, 5)) == [
            (ITEM_A, SITE_A, 7.0, "acumatica", AS_OF_ONE),
            (ITEM_C, SITE_C, 2.5, "odoo", AS_OF_ONE),
        ]

        # -- The hostile date: D's as_of is the year 294276, far
        #    outside Python's date range.  The capture must not crash
        #    on it: it stores D under the UTC date of its as_of,
        #    beside A and C with their own as_of, and leaves the
        #    earlier dates untouched. ------------------------------------
        inserted_ids.append(_insert_on_hand(ITEM_D, SITE_D, 3.0, None, FAR_AS_OF))
        answer = capture_on_hand_snapshot()
        assert answer == {"snapshot_on": FAR_SNAPSHOT_ON, "rows": _on_hand_count()}
        assert answer == {"snapshot_on": FAR_SNAPSHOT_ON, "rows": 3}
        assert _snapshot_rows(date(2099, 1, 5)) == [
            (ITEM_A, SITE_A, 7.0, "acumatica", AS_OF_ONE),
            (ITEM_C, SITE_C, 2.5, "odoo", AS_OF_ONE),
        ]
        assert _snapshot_rows(date(2099, 1, 7)) == [
            (ITEM_A, SITE_A, 7.0, "acumatica", AS_OF_TWO),
            (ITEM_C, SITE_C, 2.5, "odoo", AS_OF_TWO),
        ]
        # The far date's own rows, compared in the database so the
        # out-of-range as_of never reaches a Python converter.
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT item_code, site_code, quantity_on_hand, "
                    "source_system, (as_of = %s::timestamptz) AS far_as_of, "
                    "(as_of = %s::timestamptz) AS old_as_of "
                    "FROM on_hand_snapshots WHERE snapshot_on = %s::date "
                    "ORDER BY item_code, site_code",
                    (FAR_AS_OF, OLD_AS_OF_UTC, FAR_SNAPSHOT_ON),
                )
                far_rows = cur.fetchall()
        finally:
            conn.close()
        assert far_rows == [
            (ITEM_A, SITE_A, 7.0, "acumatica", False, True),
            (ITEM_C, SITE_C, 2.5, "odoo", False, True),
            (ITEM_D, SITE_D, 3.0, None, True, False),
        ]

        # -- Identity: a second pass after a full reload, as the
        # NON-ADMIN (role "user").  The page is booted the production
        # way: the token it would take from the URL hash as
        # #access_token=... is the development token (role "user") sent
        # as its Bearer token; GET /api/auth/config answers publicly
        # and GET /api/auth/me resolves the viewer for that user.
        viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
        anon = TestClient(app)
        config = anon.get("/api/auth/config")
        assert config.status_code == 200
        me = viewer.get("/api/auth/me")
        assert me.status_code == 200, me.text
        user = me.json()["user"]
        assert user["roles"] == ["user"]
        assert "admin" not in user["roles"]
        assert "admin" not in user["groups"]
        # The admin-only control is absent: the plan release audit is
        # admin-only and refused for the viewer, and this issue's
        # capture job is not a route at all — no route module exposes
        # it.
        assert viewer.get(AUDIT).status_code == 403
        for path in pathlib.Path(api_package.__file__).parent.glob("*.py"):
            assert "capture_on_hand_snapshot" not in path.read_text()
        # The page still renders: the settings the page loads answer
        # 200 for the viewer, and the gate is intact — unauthenticated
        # /api traffic is still 401.
        assert viewer.get("/api/settings").status_code == 200
        assert anon.get("/api/settings").status_code == 401
    finally:
        # Every on_hand row and every snapshot row the proof wrote (the
        # dates 2099-01-05, 2099-01-07 and 294276-01-01) is deleted.
        with get_cursor() as cur:
            cur.execute("DELETE FROM on_hand WHERE id = ANY(%s)", (inserted_ids,))
            cur.execute(
                "DELETE FROM on_hand_snapshots " "WHERE snapshot_on = ANY(%s::date[])",
                (list(_SNAPSHOT_DATES),),
            )
    assert _on_hand_count() == 0
    assert _snapshot_rows(date(2099, 1, 5)) == []
    assert _snapshot_rows(date(2099, 1, 7)) == []
    # The hostile far date is cleaned up too, checked in the database
    # so its date never reaches a Python date converter.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM on_hand_snapshots "
                "WHERE snapshot_on = %s::date",
                (FAR_SNAPSHOT_ON,),
            )
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()
