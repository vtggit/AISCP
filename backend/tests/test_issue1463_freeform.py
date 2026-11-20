"""Issue #1463 proof: the data stamp of the reorder recommendations.

``data_versions`` holds one row per input table of the reorder
recommendations — ``table_name`` text primary key, ``version`` bigint
not null default 0, ``changed_at`` timestamptz not null default
now().  A write to an input table bumps exactly its own row, in the
writer's own transaction, through the plpgsql function
``aierp_bump_data_version()`` bound on each of the eleven tables as
an AFTER INSERT OR UPDATE OR DELETE OR TRUNCATE ... FOR EACH
STATEMENT trigger named ``<table>_data_version``: the new version is
visible exactly when the new rows are, a rolled-back write leaves the
counter unchanged, and two writers of different tables never touch
the same row.  When a write's table has no row the function raises
and the writer's transaction aborts.  A TRUNCATE of ``data_versions``
itself re-seeds the eleven rows at version 0 through the reseed
trigger ``data_versions_reseed`` and its function
``aierp_reseed_data_versions()`` — the test suite truncates every
table between tests, and without the rows the next write to an input
table would raise.  ``app.db.data_stamp.data_stamp()`` answers the
tuple of the eleven versions, in the order of ``INPUT_TABLES``, in
one query; a table with no row reads as 0.

Proved here, on the test database migrated to head:

* the list — ``INPUT_TABLES`` equals the eleven names of the stamp
  criterion, in that order;
* the table and the triggers — ``data_versions`` holds one row per
  input table (with the documented shape), and ``pg_trigger`` shows
  the eleven ``<table>_data_version`` triggers as statement-level
  triggers that fire on insert, update, delete and truncate (beside
  the statement-level AFTER TRUNCATE reseed trigger on
  ``data_versions``);
* the reseed — after a committed ``TRUNCATE data_versions``, the
  table holds the eleven rows again, each at version 0;
* each write counts — an INSERT, an UPDATE and a DELETE on
  ``demand_signals``, each committed, each raise the stamp's
  ``demand_signals`` position by one, and no other position moves;
  the test inserts its own row and deletes it.  For the duration of
  those writes the table's append-only guard trigger
  (``demand_signals_guard``) is dropped and recreated, exactly as the
  migration stores it, so the guard itself never moves or blocks a
  counter;
* a rolled-back write leaves the stamp as it was;
* an uncommitted write made on one connection does not change the
  stamp read on another connection until it commits;
* other tables — a write to a table that is not an input,
  ``exception_acks``, leaves the stamp as it was, and the test
  removes its row afterwards;
* a missing row — inside one transaction, the ``demand_signals`` row
  deleted from ``data_versions`` makes a write to ``demand_signals``
  in that same transaction raise; after the rollback the row is back
  with its version unchanged;
* the tables the recommendations read — with
  ``psycopg2.extras.RealDictCursor.execute`` wrapped to record the
  text of every query (a composed query through its ``as_string``),
  one call of ``app.recommend.reorder.recommendations_for_all()``
  reads only public tables named in ``INPUT_TABLES`` — the set is not
  empty and holds ``settings``, ``demand_events`` and ``item_sites``.
"""

import re

import psycopg2
import psycopg2.extras
import psycopg2.sql

from app.db.connection import get_connection, get_cursor
from app.db.data_stamp import INPUT_TABLES, data_stamp
from app.recommend import reorder

EXPECTED_INPUT_TABLES = (
    "settings",
    "demand_events",
    "lead_time_observations",
    "forecast_overrides",
    "demand_signals",
    "item_sites",
    "on_hand",
    "open_purchase_lines",
    "plan_releases",
    "transfer_releases",
    "empirical_adoptions",
)

DS = EXPECTED_INPUT_TABLES.index("demand_signals")

# pg_trigger.tgtype bits: the trigger level and the event bits, and
# BEFORE (a trigger that is not BEFORE is AFTER).
TG_ROW = 1
TG_BEFORE = 2
TG_INSERT = 4
TG_DELETE = 8
TG_UPDATE = 16
TG_TRUNCATE = 32

# A name following FROM or JOIN in a query's text.
_TABLE_REF = re.compile(
    r"\b(?:FROM|JOIN)\s+\"?([A-Za-z_][A-Za-z0-9_]*)\"?", re.IGNORECASE
)

# The proof's demand_signals rows, inserted and deleted by the test.
SIGNAL_ITEM = "ITEM-1463"
SIGNAL_SITE = "SITE-1463"
SIGNAL_START = "2026-11-01"
SIGNAL_END = "2026-11-30"
SIGNAL_INSERT = (
    "INSERT INTO demand_signals "
    "(kind, item_code, site_code, start_date, end_date, quantity, "
    "reason, created_by) VALUES "
    "('promotion', %s, %s, %s, %s, %s, 'issue 1463 proof', 'test-1463') "
    "RETURNING id"
)


def _only(position: int, before: tuple, after: tuple) -> None:
    """after equals before with exactly ``position`` raised by one."""
    assert after[position] == before[position] + 1
    for i in range(len(before)):
        if i != position:
            assert (
                after[i] == before[i]
            ), f"position {i} moved: {before[i]} -> {after[i]}"


def test_issue1463_freeform(monkeypatch, client):
    # -- The list ------------------------------------------------------
    assert INPUT_TABLES == EXPECTED_INPUT_TABLES

    # -- The table and the triggers ------------------------------------
    with get_cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = 'public' AND table_name = 'data_versions' "
            "ORDER BY ordinal_position"
        )
        shape = [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in cur.fetchall()
        ]
    assert shape == [
        ("table_name", "text", "NO"),
        ("version", "bigint", "NO"),
        ("changed_at", "timestamp with time zone", "NO"),
    ]
    with get_cursor() as cur:
        cur.execute("SELECT table_name FROM data_versions ORDER BY table_name")
        stored = [row["table_name"] for row in cur.fetchall()]
    assert stored == sorted(EXPECTED_INPUT_TABLES)
    with get_cursor() as cur:
        cur.execute(
            "SELECT tgrelid::regclass::text AS table_name, tgname, tgtype "
            "FROM pg_trigger WHERE NOT tgisinternal"
        )
        triggers = {
            row["tgname"]: (row["table_name"], row["tgtype"]) for row in cur.fetchall()
        }
    for table in EXPECTED_INPUT_TABLES:
        name = f"{table}_data_version"
        assert name in triggers, f"missing trigger {name}"
        rel, tgtype = triggers[name]
        assert rel == table
        assert tgtype & TG_ROW == 0, f"{name} is not statement-level"
        assert tgtype & TG_BEFORE == 0, f"{name} is not an AFTER trigger"
        expected = TG_INSERT | TG_UPDATE | TG_DELETE | TG_TRUNCATE
        assert tgtype & expected == expected, (
            f"{name} does not fire on insert, update, delete and " "truncate"
        )
    rel, tgtype = triggers["data_versions_reseed"]
    assert rel == "data_versions"
    assert tgtype & TG_ROW == 0
    assert tgtype & TG_BEFORE == 0
    assert tgtype & TG_TRUNCATE == TG_TRUNCATE

    # -- The reseed ------------------------------------------------------
    with get_cursor() as cur:
        cur.execute("TRUNCATE data_versions")
    assert data_stamp() == (0,) * len(EXPECTED_INPUT_TABLES)
    with get_cursor() as cur:
        cur.execute(
            "SELECT table_name, version FROM data_versions " "ORDER BY table_name"
        )
        rows = cur.fetchall()
    assert [row["table_name"] for row in rows] == sorted(EXPECTED_INPUT_TABLES)
    assert all(row["version"] == 0 for row in rows)

    # -- Each write counts; a rolled-back write; an uncommitted write --
    # The table's append-only guard refuses every UPDATE and DELETE, so
    # the proof lifts it for the duration of the writes below and
    # recreates it exactly as the migration stores it.
    with get_cursor() as cur:
        cur.execute("DROP TRIGGER demand_signals_guard ON demand_signals")
    try:
        baseline = data_stamp()
        # The INSERT, committed: the demand_signals position alone
        # moves.
        with get_cursor() as cur:
            cur.execute(
                SIGNAL_INSERT,
                (SIGNAL_ITEM, SIGNAL_SITE, SIGNAL_START, SIGNAL_END, 7.0),
            )
            signal_id = cur.fetchone()["id"]
        after_insert = data_stamp()
        _only(DS, baseline, after_insert)
        # The UPDATE, committed: the position moves again.
        with get_cursor() as cur:
            cur.execute(
                "UPDATE demand_signals SET quantity = 11 WHERE id = %s",
                (signal_id,),
            )
        after_update = data_stamp()
        _only(DS, after_insert, after_update)
        # The DELETE, committed: the position moves again, and the
        # test's own row is gone.
        with get_cursor() as cur:
            cur.execute("DELETE FROM demand_signals WHERE id = %s", (signal_id,))
        after_delete = data_stamp()
        _only(DS, after_update, after_delete)
        with get_cursor() as cur:
            cur.execute("SELECT id FROM demand_signals WHERE id = %s", (signal_id,))
            assert cur.fetchone() is None

        # A rolled-back write leaves the stamp as it was.
        rolled_back = data_stamp()
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    SIGNAL_INSERT,
                    (SIGNAL_ITEM, SIGNAL_SITE, SIGNAL_START, SIGNAL_END, 3.0),
                )
            conn.rollback()
        finally:
            conn.close()
        assert data_stamp() == rolled_back

        # An uncommitted write on one connection does not change the
        # stamp read on another connection until it commits.
        before_uncommitted = data_stamp()
        writer = get_connection()
        try:
            with writer.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    SIGNAL_INSERT,
                    (SIGNAL_ITEM, SIGNAL_SITE, SIGNAL_START, SIGNAL_END, 4.0),
                )
                uncommitted_id = cur.fetchone()["id"]
            assert data_stamp() == before_uncommitted
            writer.commit()
        finally:
            writer.close()
        _only(DS, before_uncommitted, data_stamp())
        # The test removes the row it inserted.
        with get_cursor() as cur:
            cur.execute("DELETE FROM demand_signals WHERE id = %s", (uncommitted_id,))
    finally:
        with get_cursor() as cur:
            cur.execute(
                "CREATE TRIGGER demand_signals_guard "
                "BEFORE INSERT OR UPDATE OR DELETE ON demand_signals "
                "FOR EACH ROW EXECUTE FUNCTION aierp_demand_signals_guard()"
            )

    # -- Other tables ----------------------------------------------------
    ack_key = "issue-1463-proof"
    ack_before = data_stamp()
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO exception_acks (key, acknowledged_by) " "VALUES (%s, %s)",
            (ack_key, "test-1463"),
        )
    assert data_stamp() == ack_before
    with get_cursor() as cur:
        cur.execute("DELETE FROM exception_acks WHERE key = %s", (ack_key,))

    # -- A missing row -----------------------------------------------------
    missing_before = data_stamp()
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM data_versions WHERE table_name = 'demand_signals'")
            try:
                cur.execute(
                    SIGNAL_INSERT,
                    (SIGNAL_ITEM, SIGNAL_SITE, SIGNAL_START, SIGNAL_END, 5.0),
                )
            except psycopg2.Error as exc:
                assert "no data_versions row for table demand_signals" in str(exc), str(
                    exc
                )
            else:
                raise AssertionError("a write to an untracked table must raise")
        conn.rollback()
    finally:
        conn.close()
    assert data_stamp() == missing_before
    with get_cursor() as cur:
        cur.execute(
            "SELECT version FROM data_versions " "WHERE table_name = 'demand_signals'"
        )
        row = cur.fetchone()
    assert row is not None and row["version"] == missing_before[DS]

    # -- The tables the recommendations read ------------------------------
    recorded: list[str] = []
    original_execute = psycopg2.extras.RealDictCursor.execute

    def recording_execute(self, query, args=None):
        if isinstance(query, psycopg2.sql.Composed):
            try:
                text = query.as_string(self.connection)
            except Exception:
                text = str(query)
        else:
            text = str(query)
        recorded.append(text)
        return original_execute(self, query, args)

    monkeypatch.setattr(psycopg2.extras.RealDictCursor, "execute", recording_execute)

    event_ids = ("issue-1463-event-1", "issue-1463-event-2")
    with get_cursor() as cur:
        for index, site in enumerate(("SITE-1463-A", "SITE-1463-B"), 1):
            cur.execute(
                "INSERT INTO demand_events (id, business_key, item_code, "
                "site_code, quantity, occurred_on, event_type) "
                "VALUES (%s, %s, %s, %s, %s, %s, 'shipment')",
                (
                    f"issue-1463-event-{index}",
                    f"issue-1463-bk-{index}",
                    "ITEM-1463-READ",
                    site,
                    5.0,
                    "2026-11-15",
                ),
            )
    try:
        recorded.clear()
        reorder.recommendations_for_all()
        with get_cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_type = 'BASE TABLE'"
            )
            public_tables = {row["table_name"] for row in cur.fetchall()}
        read_tables = set()
        for text in recorded:
            read_tables.update(_TABLE_REF.findall(text))
        read_tables &= public_tables
    finally:
        with get_cursor() as cur:
            cur.execute("DELETE FROM demand_events WHERE id IN (%s, %s)", event_ids)
    assert read_tables, "the recommendations read no tables at all"
    assert {"settings", "demand_events", "item_sites"} <= read_tables
    assert read_tables <= set(INPUT_TABLES)
