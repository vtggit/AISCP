"""Issue #1450 proof: per-input-table data versions, one data stamp.

On the test database migrated to head:

* the table — ``data_versions`` holds exactly one row per input table
  (the eleven of ``app.db.data_stamp.INPUT_TABLES``), each at version 0,
  and a TRUNCATE of the table re-seeds those same eleven rows at
  version 0, so a cleanup that empties the bookkeeping table and the
  input tables in any order never leaves an input trigger without its
  row;
* the triggers — ``pg_trigger`` shows the eleven
  ``<table>_data_version`` triggers, one per input table, all
  statement-level (not row-level, not before) and all firing on insert,
  update, delete and truncate, all executing
  ``aierp_bump_data_version()``;
* the stamp — ``data_stamp()`` answers a tuple of the eleven versions in
  ``INPUT_TABLES`` order, read in one query, with an input table that
  has no row reading as 0;
* each write counts — an INSERT, an UPDATE and a DELETE on
  ``demand_signals`` (the append-only guard stood down for the duration
  of the writes and restored afterwards), each committed, each raising
  the stamp's ``demand_signals`` position by one and no other position;
  the test inserts its own row and deletes it;
* a rolled-back write leaves the stamp as it was, and an uncommitted
  write made on one connection does not change the stamp read on
  another connection until it commits;
* a write to a table that is not an input, ``exception_acks``, leaves
  the stamp as it was;
* a missing row — with no ``data_versions`` row for ``demand_signals``
  the trigger aborts the writer's transaction (the write raises and the
  next statement on the same connection fails with an aborted
  transaction), the stamp reads the table as 0 and stays put, and the
  proof restores the row.

The proof leaves the database as it found it: the guard trigger is back,
no proof rows remain in ``demand_signals`` or ``exception_acks``, and
``data_versions`` holds the eleven rows again.
"""

import psycopg2
import pytest

import app.db.data_stamp as data_stamp_module
from app.db.connection import get_connection, get_cursor
from app.db.data_stamp import INPUT_TABLES, data_stamp
from tests.migration_head import CURRENT_HEAD

_SIGNAL_REASON = "issue 1450 proof signal"
_ROLLED_BACK_REASON = "issue 1450 proof rolled back"
_UNCOMMITTED_REASON = "issue 1450 proof uncommitted"
_AC5_REASON = "issue 1450 proof missing row"

# pg_trigger.tgtype bits.
_ROW = 1 << 0
_BEFORE = 1 << 1
_STATEMENT = 1 << 2
_INSERT = 1 << 3
_DELETE = 1 << 4
_UPDATE = 1 << 5


def _write_signal(cur, reason):
    """Insert one proof signal row (a promotion, passing every CHECK)."""
    cur.execute(
        "INSERT INTO demand_signals "
        "(kind, item_code, site_code, start_date, end_date, quantity, "
        "reason, created_by) "
        "VALUES ('promotion', 'IT-1450', '159', '2026-12-01', '2026-12-31', "
        "10, %s, 'issue1450')",
        (reason,),
    )


def _bumped_only(before, after, position):
    """Only ``position`` moved, and it moved by exactly one."""
    assert after[position] == before[position] + 1
    assert all(after[i] == before[i] for i in range(len(INPUT_TABLES)) if i != position)


def test_issue1450_freeform(monkeypatch):
    # -- The test database is migrated to head (the shared pin). -------
    with get_cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        assert cur.fetchone()["version_num"] == str(CURRENT_HEAD)

    # -- Restore the post-migration state of the table: one row per
    #    input table, at version 0 (an earlier test may have truncated
    #    it, which the per-test cleanup does for every table). ---------
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO data_versions (table_name) VALUES "
            + ", ".join("(%s)" for _ in INPUT_TABLES)
            + " ON CONFLICT (table_name) DO UPDATE SET version = 0, "
            "changed_at = now()",
            INPUT_TABLES,
        )

    # -- The table: one row per input table, at version 0. --------------
    with get_cursor() as cur:
        cur.execute("SELECT table_name, version, changed_at FROM data_versions")
        rows = cur.fetchall()
    assert len(rows) == len(INPUT_TABLES) == 11
    assert sorted(row["table_name"] for row in rows) == sorted(INPUT_TABLES)
    assert all(row["version"] == 0 for row in rows)
    assert all(row["changed_at"] is not None for row in rows)

    # A TRUNCATE of the bookkeeping table re-seeds it: the same eleven
    # rows back, at version 0.
    with get_cursor() as cur:
        cur.execute("TRUNCATE data_versions")
        cur.execute("SELECT table_name, version FROM data_versions")
        rows = cur.fetchall()
    assert len(rows) == len(INPUT_TABLES) == 11
    assert sorted(row["table_name"] for row in rows) == sorted(INPUT_TABLES)
    assert all(row["version"] == 0 for row in rows)

    # -- The stamp: the eleven versions in INPUT_TABLES order, read in
    #    one query. ------------------------------------------------------
    stamp = data_stamp()
    assert isinstance(stamp, tuple)
    assert len(stamp) == len(INPUT_TABLES) == 11
    assert all(isinstance(version, int) for version in stamp)
    assert stamp == tuple(0 for _ in INPUT_TABLES)

    calls = []
    real_get_cursor = data_stamp_module.get_cursor

    def counting_get_cursor():
        calls.append(None)
        return real_get_cursor()

    monkeypatch.setattr(data_stamp_module, "get_cursor", counting_get_cursor)
    assert data_stamp() == stamp
    assert len(calls) == 1

    # -- The triggers: eleven, statement-level, firing on insert,
    #    update, delete and truncate. -----------------------------------
    with get_cursor() as cur:
        cur.execute("""
            SELECT t.tgname AS name,
                   c.relname AS table_name,
                   t.tgtype AS tgtype,
                   p.proname AS function_name,
                   pg_get_triggerdef(t.oid) AS definition
            FROM pg_trigger t
            JOIN pg_class c ON c.oid = t.tgrelid
            JOIN pg_proc p ON p.oid = t.tgfoid
            WHERE NOT t.tgisinternal
            ORDER BY t.tgname
            """)
        triggers = [
            row for row in cur.fetchall() if row["name"].endswith("_data_version")
        ]
    assert sorted(t["name"] for t in triggers) == sorted(
        f"{table}_data_version" for table in INPUT_TABLES
    )
    assert sorted(t["table_name"] for t in triggers) == sorted(INPUT_TABLES)
    assert all(t["function_name"] == "aierp_bump_data_version" for t in triggers)
    # The statement-level insert/update/delete bits live in tgtype; the
    # TRUNCATE event is stored in the trigger definition (the catalog
    # does not carry a truncate bit).
    assert all(
        (t["tgtype"] & _STATEMENT) == _STATEMENT
        and (t["tgtype"] & _INSERT) == _INSERT
        and (t["tgtype"] & _DELETE) == _DELETE
        and (t["tgtype"] & _UPDATE) == _UPDATE
        and (t["tgtype"] & (_ROW | _BEFORE)) == 0
        for t in triggers
    )
    assert all("TRUNCATE" in t["definition"] for t in triggers)
    assert all("FOR EACH STATEMENT" in t["definition"] for t in triggers)

    # -- Each write counts: an INSERT, an UPDATE and a DELETE on
    #    demand_signals, each committed, each raising the stamp's
    #    demand_signals position by one and no other position. ----------
    position = INPUT_TABLES.index("demand_signals")
    # The append-only guard refuses every UPDATE and DELETE, so the
    # proof stands it down for the writes below and restores it, exactly
    # as the migration created it, when they are done.
    with get_cursor() as cur:
        cur.execute("DROP TRIGGER IF EXISTS demand_signals_guard ON demand_signals")
    try:
        before = data_stamp()
        with get_cursor() as cur:
            _write_signal(cur, _SIGNAL_REASON)
            cur.execute(
                "SELECT id FROM demand_signals WHERE reason = %s",
                (_SIGNAL_REASON,),
            )
            signal_id = cur.fetchone()["id"]
        _bumped_only(before, data_stamp(), position)

        before = data_stamp()
        with get_cursor() as cur:
            cur.execute(
                "UPDATE demand_signals SET quantity = 12 WHERE id = %s",
                (signal_id,),
            )
        _bumped_only(before, data_stamp(), position)

        # The proof deletes the row it inserted itself.
        before = data_stamp()
        with get_cursor() as cur:
            cur.execute("DELETE FROM demand_signals WHERE id = %s", (signal_id,))
        _bumped_only(before, data_stamp(), position)

        # -- A rolled-back write leaves the stamp as it was. ------------
        before = data_stamp()
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _write_signal(cur, _ROLLED_BACK_REASON)
            conn.rollback()
        finally:
            conn.close()
        assert data_stamp() == before

        # -- An uncommitted write on one connection does not change the
        #    stamp read on another connection until it commits. ---------
        before = data_stamp()
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _write_signal(cur, _UNCOMMITTED_REASON)
            assert data_stamp() == before
            conn.commit()
        finally:
            conn.close()
        _bumped_only(before, data_stamp(), position)
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM demand_signals WHERE reason = %s",
                (_UNCOMMITTED_REASON,),
            )
    finally:
        with get_cursor() as cur:
            cur.execute(
                "CREATE TRIGGER demand_signals_guard "
                "BEFORE INSERT OR UPDATE OR DELETE ON demand_signals "
                "FOR EACH ROW EXECUTE FUNCTION aierp_demand_signals_guard()"
            )

    # -- A write to a table that is not an input leaves the stamp. ------
    before = data_stamp()
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO exception_acks (key, acknowledged_by) "
            "VALUES ('issue-1450-proof', 'issue1450')"
        )
        cur.execute("DELETE FROM exception_acks WHERE key = 'issue-1450-proof'")
    assert data_stamp() == before

    # -- A tracked table with no version row: the trigger raises and
    #    aborts the writer's transaction. --------------------------------
    with get_cursor() as cur:
        cur.execute("DELETE FROM data_versions WHERE table_name = 'demand_signals'")
    try:
        # A table that has no row reads as 0 in the stamp.
        before = data_stamp()
        assert before[position] == 0

        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _write_signal(cur, _AC5_REASON)
            pytest.fail("a write with no data_versions row did not abort")
        except psycopg2.Error as error:
            assert "data_versions has no row for demand_signals" in str(error)
            # The writer's transaction is aborted: the next statement on
            # the same connection fails too.
            with conn.cursor() as cur, pytest.raises(
                psycopg2.Error, match="current transaction is aborted"
            ):
                cur.execute("SELECT 1")
            conn.rollback()
        finally:
            conn.close()

        # The aborted write bumped nothing.
        assert data_stamp() == before
    finally:
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO data_versions (table_name) "
                "VALUES ('demand_signals') ON CONFLICT (table_name) "
                "DO UPDATE SET version = 0, changed_at = now()"
            )
