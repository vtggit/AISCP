"""Issue #1611 proof: a changed factory never stalls a checkout.

AC-1 — inside the app's request handling, when every connection of
the bound sits idle but was opened through an earlier
``psycopg2.connect`` than the current one, the next checkout hands
out a connection opened through the current factory instead of
waiting, and the process never has more connections open than the
bound, each earlier factory's connection counted open until it is
closed.  Proven three ways: a checkout driven on the request's own
thread (a thread of the name anyio gives its worker threads) that
must finish — a stall is a thread that does not — right after the
bound was filled with the earlier factory's idle connections; the
same served the way the app serves it, a real request on a filled
bound; and a burst of requests across the change, from separate
threads.  The bound is counted two ways, and both stay at the
bound: on the process side, the pool's own count of open
connections — idle, handed out, or in the middle of being closed,
each earlier factory's connection counted until it is closed —
sampled continuously; and from the database side, in
``pg_stat_activity``, the test's own pooled connections only (the
counting factories mark them with an application name, so no
backend the test did not open is ever in the count).

AC-2 — nothing else changes: a factory rebound to count the
connections the app opens sees exactly the app's own — one per
checkout, one per request — with nothing the pool adds, and every
connection an earlier factory made is closed for good before the
pool opens one the new factory made; the routes answer exactly as
before, as the rest of the suite keeps proving.  And the pooled
connection wrapper delegates attribute assignment: a caller sets an
attribute of the checked-out handle the way it would on the
connection itself, and the connection the handle holds — not the
handle — receives it; once the handle has given the connection
back, an assignment fails the way it fails on a closed connection.
"""

import os
import threading
import time
from contextlib import suppress

import psycopg2
from fastapi.testclient import TestClient

import app.db.connection as connection
from app.db.connection import get_connection, get_connection_params, get_cursor
from app.main import app

URL = "/api/erp-sources"
EMPTY_BODY = {"sources": [], "item_sites": []}
BOUND = 4
# The name anyio gives every worker thread it spawns to run the
# app's synchronous endpoints: a thread of that name is the request
# context, so the pool's behavior can be driven directly on it.
WORKER_THREAD_NAME = "AnyIO worker thread"
# The application name the counting factories mark their
# connections with: it is how the database-side count picks out the
# test's own pooled connections, and nothing else.
APP_NAME = "issue1611-pool"
TIMEOUT = 15.0


class _CountedConnection:
    """A real connection whose closes are counted, the rest delegated.

    Attribute assignments reach the real connection, the way they
    would on the connection itself, so a test that looks at the real
    connection sees what the app set.
    """

    def __init__(self, real, stats):
        self._real = real
        self._stats = stats

    def close(self):
        self._stats["closed"] += 1
        self._real.close()

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        setattr(object.__getattribute__(self, "_real"), name, value)


def _counting_factory(real_connect, stats, earlier=None, record=None):
    """A ``psycopg2.connect`` substitute that counts what it opens.

    Its connections are marked with the test's application name, so
    the database-side count sees exactly them.  When ``earlier`` —
    the stats of a factory that opened before this one — is not
    None, every open through this factory records a violation when
    the earlier factory still had a connection open: the bound can
    afford this connection only once the earlier factory's are
    closed for good.  When ``record`` is not None, every real
    connection the factory opens is appended to it, so the test can
    look at what the connections it counts really are.
    """

    def factory(*args, **kwargs):
        stats["opened"] += 1
        if earlier is not None and earlier["opened"] - earlier["closed"] > 0:
            stats["violation"] += 1
        kwargs["application_name"] = APP_NAME
        real = real_connect(*args, **kwargs)
        if record is not None:
            record.append(real)
        return _CountedConnection(real, stats)

    return factory


def _run_in_worker_thread(fn, timeout=TIMEOUT):
    """Run ``fn`` on a thread named the app's request worker threads.

    A checkout that stalled on an earlier factory's idle connections
    shows up as a thread that did not finish in time.
    """
    box = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=target, name=WORKER_THREAD_NAME)
    thread.daemon = True
    thread.start()
    thread.join(timeout)
    assert (
        not thread.is_alive()
    ), "a checkout stalled on the earlier factory's idle connections"
    if "error" in box:
        raise box["error"]
    return box["result"]


def _connect_maintenance(real_connect):
    """A connection on the maintenance database.

    Off the test database, so this test's own counting eyes — the
    sampler and the backend counts — are invisible to the counts
    they take of the test database.
    """
    params = get_connection_params()
    conn = real_connect(
        host=params["host"],
        port=params["port"],
        user=params["user"],
        password=params["password"],
        dbname="postgres",
    )
    conn.autocommit = True
    return conn


def _terminate_idle_backends(real_connect, dbname) -> list[int]:
    """The database closes every idle backend of the test database."""
    admin = _connect_maintenance(real_connect)
    try:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT pid FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid() "
                "AND state = 'idle'",
                (dbname,),
            )
            pids = [row[0] for row in cur.fetchall()]
            for pid in pids:
                cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
        return pids
    finally:
        admin.close()


def _wait_backends_gone(real_connect, pids) -> None:
    """Wait until none of the terminated backends is connected."""
    if not pids:
        return
    admin = _connect_maintenance(real_connect)
    try:
        with admin.cursor() as cur:
            for _ in range(200):
                cur.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity " "WHERE pid = ANY(%s)",
                    (pids,),
                )
                if cur.fetchone()[0] == 0:
                    return
                time.sleep(0.05)
        raise AssertionError(f"backends {pids} still connected after 10 s")
    finally:
        admin.close()


def _pool_backends_on(real_connect, dbname) -> int:
    """How many of this test's pooled connections sit on the test
    database, counted from the database side.

    Only backends the counting factories marked with the test's
    application name are counted: the count is the pool's own
    connections, nothing the test did not open.
    """
    admin = _connect_maintenance(real_connect)
    try:
        with admin.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM pg_stat_activity "
                "WHERE datname = %s AND application_name = %s",
                (dbname, APP_NAME),
            )
            return cur.fetchone()[0]
    finally:
        admin.close()


def _drain_pool() -> None:
    """Close every idle pooled connection: the pool starts empty."""
    pool = connection._pool
    with pool._wake:
        idle = list(pool._idle)
        assert pool._live == len(idle), "a pooled connection is checked out"
        pool._idle.clear()
        pool._live = 0
        pool._closing = 0
        pool._wake.notify_all()
    for conn, _factory in idle:
        with suppress(Exception):
            conn.close()


def _start_sampler(real_connect, dbname, phase, pool):
    """Count this test's pooled backends from the database side.

    Runs until stopped and records the most it ever saw.  A count
    above the bound is recorded with the running phase, the pool's
    own view, and the backends that made it, so a failure shows its
    own cause.
    """
    conn = _connect_maintenance(real_connect)
    max_seen = [0]
    over = []
    stop = threading.Event()

    def sample() -> None:
        with conn.cursor() as cur:
            while not stop.is_set():
                cur.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity "
                    "WHERE datname = %s AND application_name = %s",
                    (dbname, APP_NAME),
                )
                seen = cur.fetchone()[0]
                if seen > max_seen[0]:
                    max_seen[0] = seen
                if seen > BOUND:
                    cur.execute(
                        "SELECT pid, state, now() - backend_start "
                        "FROM pg_stat_activity "
                        "WHERE datname = %s AND application_name = %s",
                        (dbname, APP_NAME),
                    )
                    with pool._wake:
                        pool_state = (
                            pool._live,
                            pool._closing,
                            len(pool._idle),
                        )
                    over.append((seen, list(phase), pool_state, cur.fetchall()))
                time.sleep(0.005)

    thread = threading.Thread(target=sample)
    thread.daemon = True
    thread.start()
    return thread, stop, max_seen, conn, over


def _start_bound_checker(pool):
    """Sample the pool's own count of open connections continuously.

    The pool counts a connection as open from the moment it is
    created until the moment it is closed for good — idle, handed
    out, or in the middle of being closed — so the most this
    checker ever saw is the most connections open the process ever
    held, the way the bound is defined.
    """
    max_seen = [0]
    stop = threading.Event()

    def check() -> None:
        while not stop.is_set():
            with pool._wake:
                open_now = pool._live + pool._closing
            if open_now > max_seen[0]:
                max_seen[0] = open_now
            time.sleep(0.001)

    thread = threading.Thread(target=check)
    thread.daemon = True
    thread.start()
    return thread, stop, max_seen


def test_issue1611_freeform(monkeypatch) -> None:
    real_connect = psycopg2.connect
    dbname = get_connection_params()["dbname"]
    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    pool = connection._pool

    # A deterministic start: no item-sites, so the route answers its
    # empty body for the whole test; an empty pool; and no backend of
    # this test's on the test database.
    with get_cursor() as cur:
        cur.execute("DELETE FROM item_sites")
    _drain_pool()
    _wait_backends_gone(real_connect, _terminate_idle_backends(real_connect, dbname))
    assert _pool_backends_on(real_connect, dbname) == 0

    monkeypatch.setenv("DB_POOL_SIZE", str(BOUND))
    assert connection._pool_size() == BOUND

    stats1 = {"opened": 0, "closed": 0, "violation": 0}
    stats2 = {"opened": 0, "closed": 0, "violation": 0}
    stats3 = {"opened": 0, "closed": 0, "violation": 0}
    stats4 = {"opened": 0, "closed": 0, "violation": 0}
    earlier1 = _counting_factory(real_connect, stats1)
    earlier2 = _counting_factory(real_connect, stats2, earlier=stats1)
    current3 = _counting_factory(real_connect, stats3, earlier=stats2)
    current4 = _counting_factory(real_connect, stats4)

    # The bound is watched from both sides for the whole test: the
    # pool's own open count, sampled continuously, and the test's own
    # pooled backends, counted from the database side.
    phase = ["setup"]
    checker_thread, checker_stop, bound_max = _start_bound_checker(pool)
    sampler_thread, stop, db_max, sampler_conn, over = _start_sampler(
        real_connect, dbname, phase, pool
    )
    try:
        # ------------------------------------------------------------------
        # Fill the bound with the earlier factory: BOUND connections,
        # all checked out at once, then all given back — the bound
        # sits idle in the pool, every one of it the earlier
        # factory's.
        # ------------------------------------------------------------------
        phase[0] = "fill"
        monkeypatch.setattr(psycopg2, "connect", earlier1)

        def fill() -> None:
            held = [get_connection() for _ in range(BOUND)]
            for conn in held:
                conn.close()

        _run_in_worker_thread(fill)
        assert stats1 == {"opened": BOUND, "closed": 0, "violation": 0}
        with pool._wake:
            assert (pool._live, pool._closing, len(pool._idle)) == (BOUND, 0, BOUND)
        assert _pool_backends_on(real_connect, dbname) == BOUND

        # ------------------------------------------------------------------
        # AC-1: every connection of the bound sits idle, every one of
        # it an earlier factory's.  The factory changes; the next
        # checkout — on the request's own thread — must hand out one
        # the current factory opened, not wait on the old factory's
        # idle connections.
        # ------------------------------------------------------------------
        phase[0] = "checkout"
        monkeypatch.setattr(psycopg2, "connect", earlier2)
        conn = _run_in_worker_thread(get_connection)
        # The current factory opened exactly one — the app's own —
        # and opened it only after the earlier factory's were closed
        # for good: the bound held, each earlier connection counted
        # open until it was closed.
        assert stats2["opened"] == 1
        assert stats2["violation"] == 0
        assert stats1["closed"] == BOUND
        # The handed-out connection is the current factory's and works.
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
        conn.close()
        # And from the database side: the earlier factory's
        # connections are closed for good, only the current
        # factory's one is on the test database.
        assert _pool_backends_on(real_connect, dbname) == 1

        # ------------------------------------------------------------------
        # The same, served the way the app serves it: a real request,
        # on the bound the earlier factory filled.
        # ------------------------------------------------------------------
        phase[0] = "refill"
        _run_in_worker_thread(fill)
        assert stats2["opened"] == BOUND
        with pool._wake:
            assert (pool._live, pool._closing, len(pool._idle)) == (BOUND, 0, BOUND)

        phase[0] = "request"
        monkeypatch.setattr(psycopg2, "connect", current3)
        box = {}

        def request() -> None:
            try:
                with TestClient(
                    app, headers={"Authorization": f"Bearer {dev_token}"}
                ) as client:
                    box["response"] = client.get(URL)
            except BaseException as exc:
                box["error"] = exc

        request_thread = threading.Thread(target=request)
        request_thread.daemon = True
        request_thread.start()
        request_thread.join(TIMEOUT)
        assert (
            not request_thread.is_alive()
        ), "a request stalled on the earlier factory's idle connections"
        if "error" in box:
            raise box["error"]
        response = box["response"]
        assert response.status_code == 200, response.text
        assert response.json() == EMPTY_BODY
        # The request's one connection is the current factory's: one
        # open through it, nothing the pool adds, and the earlier
        # factory's closed for good before it opened.
        assert stats3["opened"] == 1
        assert stats3["violation"] == 0
        assert stats2["closed"] == BOUND
        assert _pool_backends_on(real_connect, dbname) == 1

        # ------------------------------------------------------------------
        # Under load across the change: several requests served at
        # once from separate threads all answer correctly, and the
        # process never holds more connections than the bound — the
        # earlier factory's connections counted open until they are
        # closed.
        # ------------------------------------------------------------------
        phase[0] = "refill"
        _run_in_worker_thread(fill)
        assert stats3["opened"] == BOUND
        with pool._wake:
            assert (pool._live, pool._closing, len(pool._idle)) == (BOUND, 0, BOUND)

        phase[0] = "burst"
        monkeypatch.setattr(psycopg2, "connect", current4)
        failures: list[BaseException] = []
        failures_lock = threading.Lock()
        n_threads = 6
        n_requests = 4

        def worker() -> None:
            try:
                with TestClient(
                    app, headers={"Authorization": f"Bearer {dev_token}"}
                ) as client:
                    for _ in range(n_requests):
                        response = client.get(URL)
                        if response.status_code != 200 or response.json() != EMPTY_BODY:
                            raise AssertionError(response.text)
            except BaseException as exc:
                with failures_lock:
                    failures.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(60)
        stalled = [t for t in threads if t.is_alive()]
        assert not stalled, "requests stalled on the earlier factory's idle connections"
        assert not failures
        # The current factory's connections are the app's own, never
        # more than the bound, none given back as a close; and every
        # connection the earlier factory made is closed for good.
        assert 1 <= stats4["opened"] <= BOUND
        assert stats4["closed"] == 0
        assert stats3["closed"] == BOUND
        with pool._wake:
            assert pool._live + pool._closing <= BOUND
            assert len(pool._idle) == pool._live + pool._closing

        # ------------------------------------------------------------------
        # The pooled connection wrapper delegates attribute
        # assignment: a caller sets an attribute of the checked-out
        # handle the way it would on the connection itself, and the
        # real connection the handle holds receives it; once the
        # handle has given the connection back, an assignment fails
        # the way it fails on a closed connection.
        # ------------------------------------------------------------------
        phase[0] = "setattr"
        stats5 = {"opened": 0, "closed": 0, "violation": 0}
        reals5: list = []
        current5 = _counting_factory(
            real_connect, stats5, earlier=stats4, record=reals5
        )
        monkeypatch.setattr(psycopg2, "connect", current5)

        def assign() -> None:
            handle = get_connection()
            try:
                handle.autocommit = True
                assert len(reals5) == 1
                assert reals5[0].autocommit is True
                handle.autocommit = False
                assert reals5[0].autocommit is False
            finally:
                handle.close()
            try:
                handle.autocommit = True
            except psycopg2.InterfaceError:
                pass
            else:
                raise AssertionError("a given-back handle took an assignment")

        _run_in_worker_thread(assign)
        # The changed factory opened the app's one connection only
        # after the earlier factory's were closed for good, and the
        # one it opened is back in the pool, idle.
        assert stats5 == {"opened": 1, "closed": 0, "violation": 0}
        assert stats4["closed"] == BOUND
        with pool._wake:
            assert (pool._live, pool._closing, len(pool._idle)) == (1, 0, 1)
    finally:
        stop.set()
        sampler_thread.join(10)
        assert not sampler_thread.is_alive()
        sampler_conn.close()
        checker_stop.set()
        checker_thread.join(10)
        assert not checker_thread.is_alive()

    # The bound, both ways: the pool's own count of open connections
    # never rose above it, and neither did the test's pooled
    # backends, counted from the database side.
    assert bound_max[0] <= BOUND, over
    assert db_max[0] <= BOUND, over
    assert db_max[0] >= 1

    # From the database side, what the pool idles is what the test
    # database holds — and nothing else.
    with pool._wake:
        idle_now = len(pool._idle)
    assert _pool_backends_on(real_connect, dbname) == idle_now

    # Leave nothing behind: an empty pool, no backend of this test's
    # on the test database, the factory as the app found it.
    _drain_pool()
    _wait_backends_gone(real_connect, _terminate_idle_backends(real_connect, dbname))
