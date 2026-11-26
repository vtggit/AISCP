"""Issue #1613 proof: a dead pooled connection is never handed out.

AC-1 — a pooled connection the database closed while it sat idle in
the pool is never handed out, whichever factory opened it, a
factory rebound at call time by this test included: the request
that would have taken it gets a working connection instead — a new
one the current factory opened — and answers.  Proven for the
original factory, where the pool's probe runs a statement on the
connection, and for the rebound factory, where it must not.

AC-2 — the rebound factory is a counting wrapper of the kind the
tests of the app's routes use: it counts the connections it opens
and every statement that rides its connections.  It still counts
exactly the app's own: the route's three requests run three of its
one statement each on two of its connections — the warm one and
the working replacement of the one the database closed — and no
statement the pool ran is among them, the pool's probe of the
rebound factory's connections rising from the socket they ride, no
query on them.
"""

import time

import psycopg2

from app.db.connection import _pool, get_connection_params, get_cursor

URL = "/api/erp-sources"
EMPTY_BODY = {"sources": [], "item_sites": []}


class _CountingCursor:
    """A cursor that counts its execute calls, delegating the rest."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def execute(self, query, *args, **kwargs):
        self._counter[0] += 1
        self._queries.append(query)
        return self._real.execute(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._real.close()
        return False


class _CountingConnection:
    """A connection whose cursors count their execute calls."""

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


def test_issue1613_freeform(client, monkeypatch) -> None:
    real_connect = psycopg2.connect
    params = get_connection_params()
    dbname = params["dbname"]

    def idle_backend_pids() -> list[int]:
        """The pids of the idle backends of the test database."""
        admin = real_connect(**params)
        try:
            admin.autocommit = True
            with admin.cursor() as cur:
                cur.execute(
                    "SELECT pid FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid() "
                    "AND state = 'idle'",
                    (dbname,),
                )
                return [row[0] for row in cur.fetchall()]
        finally:
            admin.close()

    def terminate_pids(pids: list[int]) -> None:
        """The database closes the backends of the pids."""
        if not pids:
            return
        admin = real_connect(**params)
        try:
            admin.autocommit = True
            with admin.cursor() as cur:
                for pid in pids:
                    cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        finally:
            admin.close()

    def wait_backends_gone(pids: list[int]) -> None:
        """Wait until none of the closed backends is connected."""
        if not pids:
            return
        admin = real_connect(**params)
        try:
            admin.autocommit = True
            with admin.cursor() as cur:
                for _ in range(200):
                    cur.execute(
                        "SELECT count(*) AS n FROM pg_stat_activity "
                        "WHERE pid = ANY(%s)",
                        (pids,),
                    )
                    if cur.fetchone()[0] == 0:
                        return
                    time.sleep(0.05)
            raise AssertionError(f"backends {pids} still connected after 10 s")
        finally:
            admin.close()

    # A deterministic start: no item-sites, so the route answers its
    # empty body, one SQL statement per call.
    with get_cursor() as cur:
        cur.execute("DELETE FROM item_sites")

    # ------------------------------------------------------------------
    # AC-1, the original factory: a warm request leaves its
    # connection idle in the pool, the database closes it there, and
    # the next request still answers — the dead one is dropped, a
    # working connection handed out.
    # ------------------------------------------------------------------
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY

    pids = idle_backend_pids()
    assert pids
    terminate_pids(pids)
    wait_backends_gone(pids)

    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY

    # ------------------------------------------------------------------
    # AC-1 and AC-2, the factory rebound at call time: the counting
    # wrapper counts the connections it opens and every statement
    # that rides its connections, the way the tests of the app's
    # routes count.
    # ------------------------------------------------------------------
    connects = [0]
    statements = [0]
    queries: list[str] = []

    def counting_connect(*args, **kwargs):
        connects[0] += 1
        return _CountingConnection(real_connect(*args, **kwargs), statements, queries)

    monkeypatch.setattr(psycopg2, "connect", counting_connect)

    # A warm request under the rebound factory: its connection goes
    # back to the pool, idle — the factory still being the rebound
    # one, so the pool keeps it for the next user.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY
    assert connects[0] == 1
    warm_statements = statements[0]
    assert warm_statements == 1

    # The pool's idle connection is the one the wrapper opened: the
    # database closes it while it sits there.
    idle = [entry for entry in _pool._idle if entry[1] is counting_connect]
    assert len(idle) == 1
    idle_pid = idle[0][0].get_backend_pid()
    pids = idle_backend_pids()
    assert idle_pid in pids
    terminate_pids(pids)
    wait_backends_gone(pids)
    # The close reaches the pool's socket before the next checkout:
    # the end of the stream, or the database's last word, is on the
    # wire by the time the request probes.
    time.sleep(0.1)

    # The request that would have taken the dead connection gets a
    # working one instead: the dead reference is dropped and a new
    # connection opened through the rebound factory — the request
    # answers, and the wrapper counts exactly the app's own
    # connection and statements, no probe of the pool's among them.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY
    assert connects[0] == 2
    assert statements[0] == 2 * warm_statements
    assert queries[warm_statements:] == queries[:warm_statements]

    # The replacement is a different, working connection, now idle in
    # the pool: the next request reuses it, opening no new one.
    idle = [entry for entry in _pool._idle if entry[1] is counting_connect]
    assert len(idle) == 1
    assert idle[0][0].get_backend_pid() != idle_pid

    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY
    assert connects[0] == 2
    assert statements[0] == 3 * warm_statements

    # Leave the database no idle backend: the pool's idle connection
    # of the rebound factory dies with it, and the next checkout —
    # the factory restored by then — drops the dead reference.
    terminate_pids(idle_backend_pids())
