"""Issue #1418 proof: one connection per block of the exceptions call.

AC-1 — ``app.db.connection`` gains the context manager
``one_connection()``, whose state is held in a
``contextvars.ContextVar``: inside the block, ``get_cursor()`` uses
one connection for the whole block — opened with
``get_connection()`` at the block's first use and reused for every
later ``get_cursor()`` there — while each ``get_cursor()`` still
commits when its body finishes and rolls back when it raises, as
today, and its cursor is closed at its end; the connection is closed
only when the outermost block exits, an exception included.  A nested
block shares the outer block's connection, a dropped connection — one
whose ``closed`` attribute is non-zero — is replaced by a new one at
the next ``get_cursor()``, and outside any block ``get_cursor()``
opens and closes its own connection, exactly as today.
``get_connection()`` is unchanged everywhere: code that calls it
directly always gets a new connection of its own.

AC-2 — the exceptions call: ``_collect()`` enters
``recommendations_memo()``, ``read_once()`` and ``one_connection()``
in one ``with`` statement, unconditionally, and the answer — the
as-of date, the rows and their order, and ``sources_failed`` — is the
same as before.

AC-3 — the cleanup: ``recommendations_for_all`` has no
``__wrapped__`` attribute (its ``@cached_read`` is gone; its own
``recommendations_memo()`` already answers repeated calls for one
as-of inside ``_collect()``), and ``app.api.exceptions`` has no
``_recommendations_memo`` name.

AC-4 — proved by ``tests/test_issue910_freeform.py``: its
memo-disabled pass also replaces ``app.api.exceptions.read_once``
with the same stand-in it gives ``recommendations_memo``.

AC-5 — proved in one test, on the test database: connections are
counted by replacing ``app.db.connection.get_connection`` with a
counting wrapper — ``get_cursor()`` calls that name at call time,
while the modules that imported it keep the original.  Three
``get_cursor()`` uses inside one ``one_connection()`` block open one
connection; outside any block they open three.  A nested block opens
no second connection, and after the outermost block exits that
connection's ``closed`` is non-zero.  A temporary table created in a
first ``get_cursor()`` of a block, and a row inserted into it in a
``get_cursor()`` body that raises, leave the table empty, and the
next ``get_cursor()`` in the same block sees it on the same
connection.  A block connection closed by hand in the middle of the
block is replaced: the next ``get_cursor()`` opens one new
connection and its query works.  ``get_connection()`` inside a block
returns a new connection, not the block's.  The exceptions call,
seeded the way ``tests/test_issue1410_freeform.py`` seeds it with one
failed release: ``_collect()`` opens exactly one connection through
``get_cursor()``, and ``_gather_sources()`` run without any block
opens more than one; the two answers are equal.
"""

import app.api.exceptions as exceptions
import app.db.connection as connection
import app.recommend.reorder as reorder
from app.db.connection import get_connection, get_cursor, one_connection
from tests.test_issue1410_freeform import _seed_failed_release, _truncate_all


def test_issue1418_freeform(client, monkeypatch) -> None:
    # Count the connections by replacing
    # app.db.connection.get_connection: get_cursor() calls that name
    # at call time, while the modules that imported it (this file's
    # get_connection among them) keep the original.
    opened: list = []
    real_get_connection = connection.get_connection

    def counting_get_connection():
        conn = real_get_connection()
        opened.append(conn)
        return conn

    monkeypatch.setattr("app.db.connection.get_connection", counting_get_connection)

    # --- AC-1: three uses ----------------------------------------------------
    # Three get_cursor() uses inside one one_connection() block open
    # one connection; outside any block they open three.
    opened.clear()
    with one_connection():
        for _ in range(3):
            with get_cursor() as cur:
                cur.execute("SELECT 1 AS one")
                assert cur.fetchone()["one"] == 1
    assert len(opened) == 1

    opened.clear()
    for _ in range(3):
        with get_cursor() as cur:
            cur.execute("SELECT 1 AS one")
    assert len(opened) == 3
    assert all(conn.closed != 0 for conn in opened)

    # --- AC-1: nesting ---------------------------------------------------------
    # A nested block opens no second connection, and the connection is
    # closed when the outermost block exits.
    opened.clear()
    with one_connection():
        with get_cursor() as cur:
            cur.execute("SELECT 1 AS one")
        with one_connection(), get_cursor() as cur:
            cur.execute("SELECT 1 AS one")
    assert len(opened) == 1
    assert opened[0].closed != 0

    # --- AC-1: rollback --------------------------------------------------------
    # The temporary table is created in a first get_cursor() of the
    # block; the raising get_cursor() body inserts a row that the
    # rollback undoes, leaving the table empty; the next get_cursor()
    # in the same block sees the table on the same connection (a
    # replaced connection would not see it).
    opened.clear()
    with one_connection():
        with get_cursor() as cur:
            cur.execute("CREATE TEMP TABLE issue1418_probe (id int)")
        raised = False
        try:
            with get_cursor() as cur:
                cur.execute("INSERT INTO issue1418_probe (id) VALUES (1)")
                raise RuntimeError("the body raises")
        except RuntimeError:
            raised = True
        assert raised
        with get_cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM issue1418_probe")
            assert cur.fetchone()["n"] == 0
    assert len(opened) == 1

    # --- AC-1: a dropped connection ---------------------------------------------
    # The block's connection is closed by hand in the middle of the
    # block: the next get_cursor() opens one new connection, its query
    # works, and the block closes the new one when it exits.
    opened.clear()
    with one_connection():
        with get_cursor() as cur:
            cur.execute("SELECT 1 AS one")
        assert len(opened) == 1
        first = opened[0]
        first.close()
        assert first.closed != 0
        with get_cursor() as cur:
            cur.execute("SELECT 42 AS answer")
            assert cur.fetchone()["answer"] == 42
        assert len(opened) == 2
        second = opened[1]
        assert second is not first
    assert first.closed != 0
    assert second.closed != 0

    # --- AC-1: get_connection() inside the block ---------------------------------
    # It returns a new connection of its own — not the block's — and
    # it does not count among the block's connections.
    opened.clear()
    with one_connection():
        with get_cursor() as cur:
            cur.execute("SELECT 1 AS one")
        assert len(opened) == 1
        own = get_connection()
        try:
            assert own is not opened[0]
            assert own.closed == 0
            with own.cursor() as cur:
                cur.execute("SELECT 7 AS mine")
                assert cur.fetchone()[0] == 7
        finally:
            own.close()
    assert len(opened) == 1
    assert opened[0].closed != 0

    # --- AC-2: the exceptions call, seeded the way test_issue1410
    # seeds it (one failed release) -------------------------------------------
    _truncate_all()
    _seed_failed_release()

    opened.clear()
    collect_answer = exceptions._collect()
    # _collect() opens exactly one connection through get_cursor():
    # the one_connection() block's, at its first use.
    assert len(opened) == 1
    assert opened[0].closed != 0

    opened.clear()
    gather_answer = exceptions._gather_sources()
    # Run without any block, the same calls open a connection each:
    # more than one.
    assert len(opened) > 1
    assert all(conn.closed != 0 for conn in opened)

    # The answer is the same as before: the as-of date, the rows and
    # their order, and sources_failed.
    assert collect_answer == gather_answer
    as_of, rows, sources_failed = collect_answer
    assert as_of is not None
    assert sources_failed == []
    assert any(row.kind == "release_failed" for row in rows)
    assert rows == sorted(rows, key=exceptions._sort_key)

    # --- AC-3: the cleanup -----------------------------------------------------
    # recommendations_for_all lost its @cached_read decorator (no
    # __wrapped__), and app.api.exceptions no longer imports
    # _recommendations_memo.
    assert not hasattr(reorder.recommendations_for_all, "__wrapped__")
    assert not hasattr(exceptions, "_recommendations_memo")
