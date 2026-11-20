"""Issue #1475 proof: the record row of every DDMRP adjustment save,
and the measure of it.

``POST /api/ddmrp/adjustments`` appends one row of
``ddmrp_adjustment_proposals`` beside every adjustment it stores, in
the same database transaction: the new adjustment's id, its codes,
its window and its factor, the ``proposed_factor`` the proposals read
proposed for exactly that window at that moment — for a body with both
codes, the ``factor`` of the proposal of
``adjustment_proposals(periods=12)`` whose ``item_code``,
``site_code``, ``start_on`` and ``end_on`` all equal the body's — with
the ``threshold`` of that read stored too.  Both are null for an
all-buffer body (both codes absent), when no proposal matched the
window, or when the proposals read raised; a matched proposal whose
``factor`` is null or below 0 stores a null ``proposed_factor``.  A
failed append rolls the save back (500), so an adjustment is never
stored without its record row; an overlapping window (the 409)
appends neither row, and ``DELETE /api/ddmrp/adjustments/{id}``
removes the adjustment and leaves every record row —
``adjustment_id`` has no foreign key to ``ddmrp_adjustments``, so the
row stays when its adjustment is deleted.

``GET /api/ddmrp/adjustments/record`` (any signed-in user) answers,
over every row of the record, ``{saved, with_proposal, taken, below,
above}``: ``saved`` counts the rows, ``with_proposal`` those whose
``proposed_factor`` is not null, and of those ``taken`` the rows whose
``factor`` is within 0.005 of ``proposed_factor``, ``below`` those
lower by more and ``above`` those higher by more — so
``taken + below + above = with_proposal``.  Nothing is written.

Proved here, on the test database migrated to head, with three
item-sites (A, B and C) seeded in ``item_sites`` and removed
afterwards, and ``app.api.ddmrp.adjustment_proposals`` replaced by a
stand-in that counts its calls and answers
``{"as_of": "2026-11-20", "threshold": 0.25, "periods": 12,
"proposals": [PA, PB, PC]}`` — PA A's proposal for 2026-12-01 to
2026-12-30 with ``factor`` 1.5, PB B's proposal for the same window
with ``factor`` 0.7, PC C's proposal for 2027-03-01 to 2027-03-30
with ``factor`` 0, each also carrying ``adu_base``,
``forecast_daily`` and ``existing_factor`` (null):

* the migration — exactly one head, exactly one successor of
  ``0052_data_versions`` (its id not compared with the head, so a
  later migration leaves the check unchanged), and the table in the
  test database with its documented shape: a bigint identity ``id``
  primary key, ``adjustment_id`` text not null with no foreign key,
  ``item_code`` and ``site_code`` text null, ``start_on`` and
  ``end_on`` date not null, ``factor`` numeric not null with the
  ``factor > 0 AND factor <= 10`` CHECK, ``proposed_factor`` numeric
  null with a CHECK that it is null or above 0, ``threshold`` numeric
  null, ``saved_by`` text not null and ``saved_at`` timestamptz not
  null defaulting to ``now()``; the database itself refuses a factor
  of 0, a factor above 10 and a ``proposed_factor`` of 0 (or below)
  through the CHECK constraints; the downgrade drops the table and
  the upgrade back to head re-creates it cleanly;
* the saves — each an admin POST answering 201 and appending exactly
  one record row: A for the December window at 1.5 (``proposed_factor``
  1.5, ``threshold`` 0.25, ``factor`` 1.5, ``saved_by`` the admin's
  subject), B for the December window at 0.9 (``proposed_factor``
  0.7), A for 2027-01-01 to 2027-01-30 at 1.2 (``proposed_factor``
  null — no proposal matched the window — with ``threshold`` 0.25),
  and an all-buffer adjustment for 2027-02-01 to 2027-02-27 at 1.1
  (both values null, and the stand-in not called for it);
* delete and re-save — B's December adjustment is deleted through
  ``DELETE /api/ddmrp/adjustments/{id}`` (204) and its record row
  stays; B is then saved again for the same window at 0.5, appending
  a row with ``proposed_factor`` 0.7;
* the conflict — a POST for A's December window again answers 409 and
  appends no row;
* the failed record insert — a POST for C at 1.3 for 2027-03-01 to
  2027-03-30 gets PC's ``factor`` 0 as its ``proposed_factor``; the
  record insert then breaks its CHECK, so the POST fails (500, on a
  client that does not raise server exceptions); no adjustment for C
  is stored and no record row is appended;
* the measure — ``GET /api/ddmrp/adjustments/record`` answers
  ``{"saved": 5, "with_proposal": 3, "taken": 1, "below": 1,
  "above": 1}``; it answers 401 without a token and 200 for the
  ``user`` role, and ``backend/openapi.json`` carries the route;
* clean-up — the proof removes its adjustments and its
  ``ddmrp_adjustment_proposals`` rows, and its item-sites,
  afterwards.

Identity — the proof also runs as a NON-ADMIN (role ``user``): a
second pass in the same test after a full reload, with the page
booted the production way (the token it would take from the URL hash
as ``#access_token=...`` — the development token, role "user" — sent
as its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer, sees the admin-only
controls (the ``POST``/``DELETE`` of ``/api/ddmrp/adjustments``)
absent for that caller (403, nothing appended) while the page still
renders for the viewer.
"""

import json
import os
from datetime import date
from pathlib import Path

import psycopg2
import psycopg2.errors
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.api import ddmrp as ddmrp_module
from app.db.connection import get_connection
from app.main import app
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_DETAIL = "Role 'admin' is required to perform this action."

URL = "/api/ddmrp/adjustments"
RECORD_URL = f"{URL}/record"
_TABLE = "ddmrp_adjustment_proposals"
PREV_REVISION = "0052_data_versions"

ITEM_A, SITE_A = "ITEM-1475A", "SITE-1475-1"
ITEM_B, SITE_B = "ITEM-1475B", "SITE-1475-2"
ITEM_C, SITE_C = "ITEM-1475C", "SITE-1475-3"

DECEMBER = (date(2026, 12, 1), date(2026, 12, 30))
JANUARY = (date(2027, 1, 1), date(2027, 1, 30))
FEBRUARY = (date(2027, 2, 1), date(2027, 2, 27))
MARCH = (date(2027, 3, 1), date(2027, 3, 30))

# column -> (data_type, is_nullable, column_default, is_identity)
EXPECTED_COLUMNS = {
    "id": ("bigint", "NO", None, "YES"),
    "adjustment_id": ("text", "NO", None, "NO"),
    "item_code": ("text", "YES", None, "NO"),
    "site_code": ("text", "YES", None, "NO"),
    "start_on": ("date", "NO", None, "NO"),
    "end_on": ("date", "NO", None, "NO"),
    "factor": ("numeric", "NO", None, "NO"),
    "proposed_factor": ("numeric", "YES", None, "NO"),
    "threshold": ("numeric", "YES", None, "NO"),
    "saved_by": ("text", "NO", None, "NO"),
    "saved_at": ("timestamp with time zone", "NO", "now()", "NO"),
}

_RECORD_INSERT = (
    "INSERT INTO ddmrp_adjustment_proposals "
    "(adjustment_id, item_code, site_code, start_on, end_on, factor, "
    "proposed_factor, threshold, saved_by) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


class _ProposalsStandIn:
    """A stand-in for ``app.api.ddmrp.adjustment_proposals``.

    Counts its calls and answers the one fixed answer of the proof:
    A's December proposal (``factor`` 1.5), B's December proposal
    (``factor`` 0.7) and C's March proposal (``factor`` 0), each also
    carrying ``adu_base``, ``forecast_daily`` and
    ``existing_factor`` (null).
    """

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "as_of": "2026-11-20",
            "threshold": 0.25,
            "periods": 12,
            "proposals": [
                self._proposal(ITEM_A, SITE_A, *DECEMBER, 1.5),
                self._proposal(ITEM_B, SITE_B, *DECEMBER, 0.7),
                self._proposal(ITEM_C, SITE_C, *MARCH, 0),
            ],
        }

    @staticmethod
    def _proposal(item, site, start_on, end_on, factor):
        return {
            "item_code": item,
            "site_code": site,
            "start_on": start_on.isoformat(),
            "end_on": end_on.isoformat(),
            "adu_base": 10.0,
            "forecast_daily": 10.0 * factor,
            "factor": factor,
            "existing_factor": None,
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
                "SELECT column_name, data_type, is_nullable, column_default, "
                "is_identity "
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


def _primary_key_columns() -> list[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = %s::regclass AND i.indisprimary "
                "ORDER BY a.attname",
                (_TABLE,),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _foreign_keys() -> list[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT con.conname FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "WHERE rel.relname = %s AND con.contype = 'f' "
                "ORDER BY con.conname",
                (_TABLE,),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _expect_db_rejection(conn, params: tuple, label: str) -> None:
    """Run one record insert in a savepoint; it must fail a CHECK.

    The savepoint keeps the surrounding (rolled-back) transaction
    usable after each violation, and nothing here is ever committed.
    """
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT issue1475")
        try:
            cur.execute(_RECORD_INSERT, params)
        except psycopg2.Error as exc:
            assert (
                type(exc) is psycopg2.errors.CheckViolation
            ), f"{label}: {type(exc).__name__}"
            cur.execute("ROLLBACK TO SAVEPOINT issue1475")
        else:
            raise AssertionError(f"{label}: the database accepted the row")
        finally:
            cur.execute("RELEASE SAVEPOINT issue1475")


def _record_rows() -> list[tuple]:
    """Every record row, in id order, with the numerics as floats."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT adjustment_id, item_code, site_code, start_on, "
                "end_on, factor, proposed_factor, threshold, saved_by "
                f"FROM {_TABLE} ORDER BY id"
            )
            return [
                (
                    adjustment_id,
                    item_code,
                    site_code,
                    start_on,
                    end_on,
                    float(factor),
                    None if proposed is None else float(proposed),
                    None if threshold is None else float(threshold),
                    saved_by,
                )
                for (
                    adjustment_id,
                    item_code,
                    site_code,
                    start_on,
                    end_on,
                    factor,
                    proposed,
                    threshold,
                    saved_by,
                ) in cur.fetchall()
            ]
    finally:
        conn.close()


def _adjustment_rows() -> list[tuple]:
    """Every stored adjustment of the proof's pairs, in id order."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, item_code, site_code, start_on, end_on, factor "
                "FROM ddmrp_adjustments ORDER BY id"
            )
            return [
                (id_, item_code, site_code, start_on, end_on, float(factor))
                for (
                    id_,
                    item_code,
                    site_code,
                    start_on,
                    end_on,
                    factor,
                ) in cur.fetchall()
            ]
    finally:
        conn.close()


def _seed_item_sites() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item, site in (
                (ITEM_A, SITE_A),
                (ITEM_B, SITE_B),
                (ITEM_C, SITE_C),
            ):
                cur.execute(
                    "INSERT INTO item_sites (id, item_code, site_code, "
                    "unit_cost, reorder_point, safety_stock, "
                    "master_lead_time_days, source_system) "
                    "VALUES (%s, %s, %s, 1.0, 25.0, 10.0, NULL, 'test')",
                    (f"id-is-1475-{item}", item, site),
                )
        conn.commit()
    finally:
        conn.close()


def _remove_item_sites() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item in (ITEM_A, ITEM_B, ITEM_C):
                cur.execute(
                    "DELETE FROM item_sites WHERE id = %s",
                    (f"id-is-1475-{item}",),
                )
        conn.commit()
    finally:
        conn.close()


def _record_measure(client: TestClient) -> dict:
    response = client.get(RECORD_URL)
    assert response.status_code == 200, response.text
    return response.json()


def test_issue1475_freeform(client, monkeypatch):
    stand_in = _ProposalsStandIn()
    monkeypatch.setattr(ddmrp_module, "adjustment_proposals", stand_in)
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    planner = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    # The client of the failed save: a server exception answers 500
    # instead of escaping the test.
    no_raise = TestClient(
        app,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        raise_server_exceptions=False,
    )

    # -- The migration: exactly one head, exactly one successor of
    #    0052 (its id not compared with the head, so a later migration
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

    # -- The table: its documented shape, its CHECKs, no foreign key. ---
    assert _table_columns() == EXPECTED_COLUMNS
    assert _primary_key_columns() == ["id"]
    # adjustment_id has no foreign key: the record must stay when its
    # adjustment is deleted.
    assert _foreign_keys() == []
    checks = _check_constraints()
    assert set(checks) == {
        "ck_ddmrp_adjustment_proposals_factor",
        "ck_ddmrp_adjustment_proposals_proposed_factor",
    }
    factor_check = checks["ck_ddmrp_adjustment_proposals_factor"]
    assert "factor > (0)::numeric" in factor_check
    assert "factor <= (10)::numeric" in factor_check
    proposed_check = checks["ck_ddmrp_adjustment_proposals_proposed_factor"]
    assert "proposed_factor IS NULL" in proposed_check
    assert "proposed_factor > (0)::numeric" in proposed_check

    # The database itself decides the CHECKs: a factor of 0, a factor
    # above 10 and a proposed_factor of 0 (or below) are refused; a
    # well-formed row — including a null proposed_factor and a null
    # threshold — is accepted, with saved_at from its now() default.
    conn = get_connection()
    try:
        for params, label in (
            (
                ("id-1475-1", ITEM_A, SITE_A, *DECEMBER, 0, None, None, ADMIN_SUB),
                "a factor of 0",
            ),
            (
                ("id-1475-2", ITEM_A, SITE_A, *DECEMBER, 10.5, 1.0, 0.25, ADMIN_SUB),
                "a factor above 10",
            ),
            (
                ("id-1475-3", ITEM_B, SITE_B, *DECEMBER, 0.9, 0, 0.25, ADMIN_SUB),
                "a proposed_factor of 0",
            ),
            (
                ("id-1475-4", ITEM_B, SITE_B, *DECEMBER, 0.9, -1, 0.25, ADMIN_SUB),
                "a negative proposed_factor",
            ),
        ):
            _expect_db_rejection(conn, params, label)
        with conn.cursor() as cur:
            cur.execute("SAVEPOINT issue1475")
            cur.execute(
                _RECORD_INSERT + " RETURNING saved_at",
                ("id-1475-ok", ITEM_A, SITE_A, *DECEMBER, 1.5, 0.7, 0.25, ADMIN_SUB),
            )
            assert cur.fetchone()[0] is not None, "saved_at has a now() default"
            cur.execute(
                _RECORD_INSERT + " RETURNING proposed_factor, threshold",
                ("id-1475-null", None, None, *FEBRUARY, 1.1, None, None, ADMIN_SUB),
            )
            assert cur.fetchone() == (None, None), "nulls are allowed"
            cur.execute("ROLLBACK TO SAVEPOINT issue1475")
    finally:
        conn.rollback()
        conn.close()

    # -- The downgrade drops the table; the upgrade back to head
    #    re-creates it cleanly. -----------------------------------------------
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", _MIGRATIONS_DIR)
    alembic_command.downgrade(cfg, PREV_REVISION)
    assert _alembic_revision() == PREV_REVISION
    assert _TABLE not in _pg_tables()
    alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_columns() == EXPECTED_COLUMNS

    # -- The setup: the three item-sites of the proof. -------------------
    _seed_item_sites()

    # -- The saves: each answers 201 and appends exactly one record row.
    # Save 1: A for the December window at 1.5 — the stand-in's PA
    # matches the window, so the record holds its factor 1.5 and the
    # read's threshold 0.25.
    response = admin.post(
        URL,
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "start_on": DECEMBER[0].isoformat(),
            "end_on": DECEMBER[1].isoformat(),
            "factor": 1.5,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    a_dec_id = body["id"]
    assert body["item_code"] == ITEM_A
    assert body["site_code"] == SITE_A
    assert body["factor"] == 1.5
    assert body["created_by"] == ADMIN_SUB
    # The proposals read ran once, over 12 periods.
    assert stand_in.calls == [{"periods": 12}]
    assert _record_rows() == [
        (a_dec_id, ITEM_A, SITE_A, *DECEMBER, 1.5, 1.5, 0.25, ADMIN_SUB)
    ]

    # Save 2: B for the December window at 0.9 — PB's factor 0.7.
    response = admin.post(
        URL,
        json={
            "item_code": ITEM_B,
            "site_code": SITE_B,
            "start_on": DECEMBER[0].isoformat(),
            "end_on": DECEMBER[1].isoformat(),
            "factor": 0.9,
        },
    )
    assert response.status_code == 201, response.text
    b_dec_id = response.json()["id"]
    assert _record_rows() == [
        (a_dec_id, ITEM_A, SITE_A, *DECEMBER, 1.5, 1.5, 0.25, ADMIN_SUB),
        (b_dec_id, ITEM_B, SITE_B, *DECEMBER, 0.9, 0.7, 0.25, ADMIN_SUB),
    ]

    # Save 3: A for January at 1.2 — no proposal matches the window, so
    # the record holds a null proposed_factor with the read's threshold.
    response = admin.post(
        URL,
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "start_on": JANUARY[0].isoformat(),
            "end_on": JANUARY[1].isoformat(),
            "factor": 1.2,
        },
    )
    assert response.status_code == 201, response.text
    a_jan_id = response.json()["id"]
    assert len(stand_in.calls) == 3
    assert _record_rows()[-1] == (
        a_jan_id,
        ITEM_A,
        SITE_A,
        *JANUARY,
        1.2,
        None,
        0.25,
        ADMIN_SUB,
    )

    # Save 4: the all-buffer adjustment for February at 1.1 — both
    # values null, and the stand-in not called for it.
    response = admin.post(
        URL,
        json={
            "start_on": FEBRUARY[0].isoformat(),
            "end_on": FEBRUARY[1].isoformat(),
            "factor": 1.1,
        },
    )
    assert response.status_code == 201, response.text
    allbuf_id = response.json()["id"]
    assert response.json()["created_by"] == ADMIN_SUB
    assert len(stand_in.calls) == 3, "the all-buffer save calls no proposals read"
    assert _record_rows()[-1] == (
        allbuf_id,
        None,
        None,
        *FEBRUARY,
        1.1,
        None,
        None,
        ADMIN_SUB,
    )
    assert len(_record_rows()) == 4

    # -- Delete and re-save: B's December adjustment is deleted and its
    #    record row stays; the re-save appends a new row. -----------------
    response = admin.delete(f"{URL}/{b_dec_id}")
    assert response.status_code == 204, response.text
    assert _record_rows() == [
        (a_dec_id, ITEM_A, SITE_A, *DECEMBER, 1.5, 1.5, 0.25, ADMIN_SUB),
        (b_dec_id, ITEM_B, SITE_B, *DECEMBER, 0.9, 0.7, 0.25, ADMIN_SUB),
        (a_jan_id, ITEM_A, SITE_A, *JANUARY, 1.2, None, 0.25, ADMIN_SUB),
        (allbuf_id, None, None, *FEBRUARY, 1.1, None, None, ADMIN_SUB),
    ], "the record row of the deleted adjustment stays"
    response = admin.post(
        URL,
        json={
            "item_code": ITEM_B,
            "site_code": SITE_B,
            "start_on": DECEMBER[0].isoformat(),
            "end_on": DECEMBER[1].isoformat(),
            "factor": 0.5,
        },
    )
    assert response.status_code == 201, response.text
    b_dec_id2 = response.json()["id"]
    assert _record_rows()[-1] == (
        b_dec_id2,
        ITEM_B,
        SITE_B,
        *DECEMBER,
        0.5,
        0.7,
        0.25,
        ADMIN_SUB,
    )
    assert len(_record_rows()) == 5

    # -- The conflict: A's December window is stored already, so the
    #    POST answers 409 and appends no row. --------------------------------
    response = admin.post(
        URL,
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "start_on": DECEMBER[0].isoformat(),
            "end_on": DECEMBER[1].isoformat(),
            "factor": 1.5,
        },
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "ddmrp_adjustment_overlap"
    assert detail["overlapping_id"] == a_dec_id
    assert len(_record_rows()) == 5, "an overlapping window appends no row"

    # -- The failed record insert: C's March save gets PC's factor 0 as
    #    its proposed_factor; the record insert breaks its CHECK, so the
    #    whole save rolls back — 500, no adjustment, no record row. --------
    adjustments_before = _adjustment_rows()
    response = no_raise.post(
        URL,
        json={
            "item_code": ITEM_C,
            "site_code": SITE_C,
            "start_on": MARCH[0].isoformat(),
            "end_on": MARCH[1].isoformat(),
            "factor": 1.3,
        },
    )
    assert response.status_code == 500, response.text
    assert _adjustment_rows() == adjustments_before, "no adjustment for C is stored"
    assert not any(row[1] == ITEM_C for row in _adjustment_rows())
    assert len(_record_rows()) == 5, "no record row is appended"

    # -- The measure: over the five record rows. ----------------------------
    assert _record_measure(admin) == {
        "saved": 5,
        "with_proposal": 3,
        "taken": 1,
        "below": 1,
        "above": 1,
    }
    # 401 without a token, 200 for the user role.
    assert TestClient(app).get(RECORD_URL).status_code == 401
    assert _record_measure(planner) == {
        "saved": 5,
        "with_proposal": 3,
        "taken": 1,
        "below": 1,
        "above": 1,
    }

    # -- The schema file carries the route. ----------------------------------
    spec = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    record_path = spec["paths"][RECORD_URL]
    assert set(record_path) == {"get"}
    response_schema = record_path["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema["$ref"] == "#/components/schemas/DdmrpAdjustmentRecord"
    record_model = spec["components"]["schemas"]["DdmrpAdjustmentRecord"]
    assert set(record_model["properties"]) == {
        "saved",
        "with_proposal",
        "taken",
        "below",
        "above",
    }
    assert set(record_model["required"]) == {
        "saved",
        "with_proposal",
        "taken",
        "below",
        "above",
    }

    # -- Identity, second pass in the same test after a full reload: the
    #    page boots the production way (the token it would take from the
    #    URL hash as #access_token=... — the development token, role
    #    "user" — sent as its Bearer token), /api/auth/config answers
    #    publicly and /api/auth/me resolves the viewer; the admin-only
    #    controls are absent for that caller (403, nothing appended)
    #    while the page still renders for the viewer. ------------------------
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
    # The admin's identity is the other one.
    admin_me = admin.get("/api/auth/me")
    assert admin_me.status_code == 200, admin_me.text
    assert admin_me.json()["user"]["sub"] == ADMIN_SUB
    # The admin-only controls are absent for the viewer: 403, nothing
    # appended, nothing deleted, no proposals read.
    calls_before = len(stand_in.calls)
    rows_before = _record_rows()
    adjustments_before = _adjustment_rows()
    response = viewer_reloaded.post(
        URL,
        json={
            "start_on": JANUARY[0].isoformat(),
            "end_on": JANUARY[1].isoformat(),
            "factor": 1.25,
        },
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer_reloaded.delete(f"{URL}/{b_dec_id2}")
    assert response.status_code == 403, response.text
    assert len(stand_in.calls) == calls_before
    assert _record_rows() == rows_before
    assert _adjustment_rows() == adjustments_before
    # The page still renders: the adjustments and the record it loads
    # answer 200 for the viewer.
    assert viewer_reloaded.get(URL).status_code == 200
    assert _record_measure(viewer_reloaded) == {
        "saved": 5,
        "with_proposal": 3,
        "taken": 1,
        "below": 1,
        "above": 1,
    }
    # And the gate is intact: a request without a token still gets 401.
    assert TestClient(app).get(RECORD_URL).status_code == 401

    # -- Clean-up: the proof removes its adjustments, its record rows and
    #    its item-sites afterwards. -------------------------------------------
    for adjustment_id in (a_dec_id, a_jan_id, allbuf_id, b_dec_id2):
        response = admin.delete(f"{URL}/{adjustment_id}")
        assert response.status_code == 204, response.text
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"DELETE FROM {_TABLE}")
        conn.commit()
    finally:
        conn.close()
    _remove_item_sites()
    assert _record_rows() == []
    assert _adjustment_rows() == []
