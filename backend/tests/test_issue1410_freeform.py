"""Issue #1410 proof: the read cache of the exceptions call.

AC-1 — ``app.db.read_cache`` is the per-context read cache of the
exceptions call: ``read_once()`` is a context manager, and while its
block is active — held in a ``contextvars.ContextVar`` — a function
decorated with ``cached_read`` answers a repeated call with the same
arguments from the first call's result, a ``copy.deepcopy`` of it
(changing the first answer's nested dict changes nothing in the
second answer), while a call with any argument that is not hashable
calls through, uncached, and a call that raises stores nothing, so
the next call calls through again.  A nested block shares the outer
block's cache, and the cache is gone when the outermost block exits.
Outside any block a decorated function calls through, exactly as
before.  ``cached_read_method`` decorates a method the same way, but
leaves ``self`` out of the key, so the instances of one repository
class share the entries.

AC-2 — the decorated reads: with ``cached_read``
``app.recommend.reorder.recommendations_for_all``,
``app.stats.demand.demand_stats_for_all``,
``app.forecast.history.history_for_all``,
``app.recommend.stockout.stockout_ranked`` and
``app.recommend.supplier_performance.supplier_performance``; with
``cached_read_method``
``OpenPurchaseLinePostgresRepository.list_latest`` and
``LeadTimeObservationPostgresRepository.list_rows`` — each keeping
its signature, its docstring and its answer.

AC-3 — the exceptions call: ``_collect()`` runs its sources inside
one ``read_once()`` block, and its answer — the as-of date, the rows
and their order, and ``sources_failed`` — is the same as without the
block.

AC-4 — proved in one test: the cache itself (a function decorated
with ``cached_read`` and called twice with the same arguments inside
``read_once()`` runs once, and outside any block runs twice; a call
with a list argument runs every time; changing the first answer's
nested dict changes nothing in the second answer; a call that raised
is run again by the next call; nested blocks share one cache, and a
new block after the outermost one exits starts empty; two instances
of a class whose method is decorated with ``cached_read_method``
share one entry); the exceptions call, on the test database seeded so
that at least one source answers a row (one failed
``plan_releases`` row): ``_collect()`` run without a ``read_once()``
block, through a stand-in that calls the sources the way it does
today, and ``_collect()`` as the route runs it, answer the same
as-of date, rows and order and ``sources_failed``; the statements
executed, counted the way ``tests/test_issue381_freeform.py`` counts
them, by wrapping ``psycopg2.connect`` — with the block the count is
strictly lower, and the open purchase lines' select statement runs
exactly once; and the route: ``GET /api/exceptions`` answers 200
with the same keys as the ``user`` role, and 401 with no token.
"""

import os

import psycopg2
import psycopg2.sql
import pytest
from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
from app.db.connection import get_connection
from app.db.read_cache import cached_read, cached_read_method, read_once
from app.main import app

URL = "/api/exceptions"
SITE = "SITE-1410"
SUPPLIER = "SUP-1410"
VENDOR = "VEND-1410"


# ---------------------------------------------------------------------------
# The cache itself (AC-1 / AC-4): fakes that count their own calls.
# ---------------------------------------------------------------------------


def _fake_read():
    """A ``cached_read`` module function that counts its calls and
    answers a nested dict carrying its call number."""
    calls = {"n": 0}

    @cached_read
    def fake_read(value):
        calls["n"] += 1
        return {"value": value, "call": calls["n"], "nested": {"call": calls["n"]}}

    return fake_read, calls


def _raising_read():
    """A ``cached_read`` function that raises on its first call only:
    a call that raises stores nothing, so the next call calls
    through."""
    calls = {"n": 0}

    @cached_read
    def raising_read(value):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("first call is down")
        return {"value": value, "call": calls["n"]}

    return raising_read, calls


class _Repo:
    """A repository whose method is decorated with
    ``cached_read_method``: the instances share the entries."""

    def __init__(self):
        self.calls = []

    @cached_read_method
    def fetch(self, key):
        self.calls.append(key)
        return {"key": key, "instance": id(self), "nested": {"instance": id(self)}}


def _assert_cache_itself() -> None:
    # Twice with the same arguments inside one read_once() block:
    # the function runs once, and every answer is a deep copy of the
    # first result.
    fake_read, calls = _fake_read()
    with read_once():
        first = fake_read("a")
        second = fake_read("a")
    assert calls["n"] == 1
    assert first["nested"] == {"call": 1}
    assert second["nested"] == {"call": 1}
    # Changing the first answer's nested dict changes nothing in the
    # second answer (and nothing the next call would be given).
    first["nested"]["call"] = 999
    first["nested"]["poisoned"] = True
    assert second["nested"] == {"call": 1}
    # A new block after the outermost one exits starts empty: the
    # function runs again, and the changed first answer never leaks
    # into the answer of the next block.
    with read_once():
        third = fake_read("a")
    assert calls["n"] == 2
    assert third["nested"] == {"call": 2}
    assert "poisoned" not in third["nested"]

    # Outside any block the same call runs every time.
    calls["n"] = 0
    outside_first = fake_read("a")
    outside_second = fake_read("a")
    assert calls["n"] == 2
    assert outside_first["call"] == 1
    assert outside_second["call"] == 2

    # A call with a list argument is not hashable: it runs every
    # time, inside a block too.
    calls["n"] = 0
    with read_once():
        fake_read(["a"])
        fake_read(["a"])
    assert calls["n"] == 2

    # A call that raised stored nothing: the next call calls through
    # (and the successful result is then cached for the block).
    raising_read, rcalls = _raising_read()
    raised = False
    with read_once():
        try:
            raising_read("x")
        except RuntimeError:
            raised = True
        second = raising_read("x")
    assert raised
    assert rcalls["n"] == 2
    assert second == {"value": "x", "call": 2}
    # A new block after the outermost one exits starts empty, so the
    # function runs again in it.
    with read_once():
        third = raising_read("x")
    assert rcalls["n"] == 3
    assert third == {"value": "x", "call": 3}

    # Nested blocks share one cache; a new block after the
    # outermost one exits starts empty.
    calls["n"] = 0
    with read_once():
        with read_once():
            nested_first = fake_read("b")
        nested_second = fake_read("b")
    assert calls["n"] == 1
    assert nested_first["call"] == nested_second["call"] == 1
    later = fake_read("b")
    assert calls["n"] == 2
    assert later["call"] == 2

    # Two instances of one repository class share the entry: the
    # method runs once for both, and the answers are deep copies of
    # the first result.
    one, other = _Repo(), _Repo()
    with read_once():
        shared_first = one.fetch("k")
        shared_second = other.fetch("k")
        # A distinct key keeps its own entry, in the same shared cache.
        distinct = other.fetch("other")
    # The "k" call of the second instance was answered from the
    # first result: it appended nothing for it.
    assert one.calls == ["k"] and other.calls == ["other"]
    # The second instance's answer is a deep copy of the first
    # result: it still carries the first instance's id, and changing
    # the first answer changes nothing the second one sees.
    assert shared_second["nested"] == {"instance": id(one)}
    shared_first["nested"]["instance"] = id(other)
    assert shared_second["nested"] == {"instance": id(one)}
    assert distinct["nested"] == {"instance": id(other)}
    # Outside any block the method runs on every call, per instance.
    one.calls.clear()
    other.calls.clear()
    one.fetch("k")
    other.fetch("k")
    assert one.calls == ["k"] and other.calls == ["k"]


# ---------------------------------------------------------------------------
# The exceptions call (AC-3 / AC-4): the seed, the stand-in and the
# statement counting the way tests/test_issue381_freeform.py counts.
# ---------------------------------------------------------------------------


def _truncate_all() -> None:
    """Every public table (but alembic_version) empty, so the proof
    sees a database of its own rows, whatever ran before it."""
    conn = get_connection()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
            tables = [row[0] for row in cur.fetchall()]
            for table in tables:
                cur.execute(
                    psycopg2.sql.SQL("TRUNCATE TABLE {} CASCADE").format(
                        psycopg2.sql.Identifier(table)
                    )
                )
    finally:
        conn.close()


def _seed_failed_release() -> None:
    """One failed release within the 14-day window: the releases
    source answers one row, kind "release_failed"."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO plan_releases "
                "(month, site_code, supplier_code, origin, warehouse_code, "
                "vendor_code, order_nbr, outcome, error, line_count, value, "
                "lines, actor) "
                "VALUES (%s, %s, %s, %s, NULL, %s, NULL, %s, %s, %s, %s, %s, %s)",
                (
                    "2026-10",
                    SITE,
                    SUPPLIER,
                    "test-origin",
                    VENDOR,
                    "error",
                    "boom",
                    1,
                    10,
                    "[]",
                    "tester",
                ),
            )
        conn.commit()
    finally:
        conn.close()


class _CountingCursor:
    """A cursor that counts its statement calls, delegating the rest."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def execute(self, query, *args, **kwargs):
        self._counter[0] += 1
        self._queries.append(query)
        return self._real.execute(query, *args, **kwargs)

    def executemany(self, query, *args, **kwargs):
        self._counter[0] += 1
        self._queries.append(query)
        return self._real.executemany(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._real.close()
        return False


class _CountingConnection:
    """A connection whose cursors count their statement calls."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def cursor(self, *args, **kwargs):
        return _CountingCursor(
            self._real.cursor(*args, **kwargs), self._counter, self._queries
        )

    def __getattr__(self, name):
        return getattr(self._real, name)


def _measure(fn):
    """Run fn with psycopg2.connect wrapped; (result, statements, SQL)."""
    counter = [0]
    queries: list[str] = []
    real_connect = psycopg2.connect

    def counting_connect(*args, **params):
        return _CountingConnection(real_connect(*args, **params), counter, queries)

    psycopg2.connect = counting_connect
    try:
        result = fn()
    finally:
        psycopg2.connect = real_connect
    return result, counter[0], queries


# The open purchase lines' select of
# OpenPurchaseLinePostgresRepository.list_latest.
OPEN_LINES_SELECT = "FROM open_purchase_lines AS p"


def _assert_exceptions_call() -> None:
    _truncate_all()
    _seed_failed_release()

    # The stand-in: _collect's sources, called the way they are
    # today, without any read_once() block.  Then the way the route
    # runs it: _collect, inside its one block.  Both answers — the
    # as-of date, the rows and their order, and sources_failed — are
    # the same.
    standin = exceptions._gather_sources()
    route_answer = exceptions._collect()
    assert standin == route_answer
    as_of, rows, sources_failed = route_answer
    assert as_of is not None
    assert sources_failed == []
    # At least one source answers a row: the seeded failed release.
    release_rows = [row for row in rows if row.kind == "release_failed"]
    assert len(release_rows) == 1
    assert release_rows[0].detail == f"release to {VENDOR} failed"
    assert release_rows[0].severity == "high"
    assert rows == sorted(rows, key=exceptions._sort_key)

    # The statements executed, counted by wrapping psycopg2.connect
    # the way tests/test_issue381_freeform.py counts them: with the
    # block the count is strictly lower, and the open purchase
    # lines' select statement runs exactly once.
    standin_again, count_plain, queries_plain = _measure(exceptions._gather_sources)
    route_again, count_block, queries_block = _measure(exceptions._collect)
    assert standin_again == route_again == route_answer
    assert count_block < count_plain
    plain_opens = [q for q in queries_plain if OPEN_LINES_SELECT in q]
    block_opens = [q for q in queries_block if OPEN_LINES_SELECT in q]
    assert len(block_opens) == 1
    # Without the block every source that reads the newest
    # open-purchase snapshot reads it again: the select runs more
    # than once.
    assert len(plain_opens) >= 2


# ---------------------------------------------------------------------------
# The route (AC-4): the user role and no token.
# ---------------------------------------------------------------------------

# The row keys of the answer: the ExceptionRow fields while
# include_proposal is false (the default) — the proposal key stays
# out of the answer.
ROW_KEYS = {
    "kind",
    "severity",
    "key",
    "item_code",
    "site_code",
    "title",
    "detail",
    "view",
    "detected_on",
    "acknowledged",
    "acknowledged_by_name",
    "acknowledged_at",
    "snooze_until",
    "first_seen_on",
    "new",
}


def _assert_route(client, user_headers) -> None:
    # The user role: 200 with the same keys as before the cache.
    response = client.get(URL, headers=user_headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"as_of", "counts", "rows", "sources_failed"}
    assert set(body["counts"]) == {"high", "medium", "acknowledged", "new"}
    assert isinstance(body["as_of"], (str, type(None)))
    assert isinstance(body["rows"], list)
    assert isinstance(body["sources_failed"], list)
    for row in body["rows"]:
        assert set(row) == ROW_KEYS, set(row) ^ ROW_KEYS
    # The seeded failed release is in the list the user sees.
    assert any(row["kind"] == "release_failed" for row in body["rows"])

    # No token: 401, the app's authentication gate.
    anon = TestClient(app)
    assert anon.get(URL).status_code == 401


def test_issue1410_freeform(client, user_headers) -> None:
    _assert_cache_itself()
    _assert_exceptions_call()
    _assert_route(client, user_headers)
