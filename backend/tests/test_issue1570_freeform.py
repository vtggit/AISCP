"""Issue #1570 proof: pooled connections, counting new connections
at ``psycopg2.connect``.

AC-1 — ``app.db.connection`` pools its connections where the app
serves requests: a request served a second time reuses the
connections the first one gave back, opening no new one; the
number of connections a process holds is bounded — by
``DB_POOL_SIZE`` connections when the instance has configured the
bound, 10 when it has not — so the pool blocks a request rather
than opening a connection past the bound.

AC-2 — nothing else changes: each ``get_cursor()`` still commits
when its body finishes and rolls back when it raises, a
``one_connection()`` block still shares one connection for the
whole block, no connection carries an open transaction, or any
other state, from one user to the next — a returned connection is
reset before the next user takes it — and every route answers
exactly as before, as the rest of the suite keeps proving.

AC-3 — this test, on the test database, counting new connections
at ``psycopg2.connect``, shows four things.  **Reuse**: a request
served a second time opens no new connection.  **Isolation**: after
a ``get_cursor()`` body raises, the next user of that connection
sees no open transaction and none of the failed statement's
effects.  **Concurrency**: several requests served at once from
separate threads all answer correctly, and the process never holds
more connections than its bound — counted from the database side,
in ``pg_stat_activity``, and in total, through the counting
factory.  **Recovery**: a connection the database has closed is
never handed out again — the next request still answers.
"""

import os
import threading
import time

import psycopg2
from fastapi.testclient import TestClient

import app.db.connection as connection
from app.db.connection import get_connection_params, get_cursor, one_connection
from app.main import app

URL = "/api/erp-sources"
EMPTY_BODY = {"sources": [], "item_sites": []}
FAILED_ID = "issue1570-failed"
SENTINEL_ID = "issue1570-sentinel"


def _run_in_worker_thread(fn):
    """Run ``fn`` on a thread named the app's request worker threads.

    The pool is active exactly where the app serves requests — a
    synchronous endpoint's body runs on a worker thread anyio
    names "AnyIO worker thread" — and a thread of that name is the
    request context, so the pool's behavior can be driven directly.
    """
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=target, name="AnyIO worker thread")
    thread.start()
    thread.join()
    if "error" in box:
        raise box["error"]
    return box["result"]


def test_issue1570_freeform(client, monkeypatch) -> None:
    real_connect = psycopg2.connect
    dbname = get_connection_params()["dbname"]
    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

    # A deterministic start: no item-sites, so the route answers its
    # empty body for the whole test.
    with get_cursor() as cur:
        cur.execute("DELETE FROM item_sites")

    # ------------------------------------------------------------------
    # Bounds: the process-wide bound is instance-configurable, 10 when
    # the instance has not configured one.
    # ------------------------------------------------------------------
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    assert connection._pool_size() == 10
    monkeypatch.setenv("DB_POOL_SIZE", "3")
    assert connection._pool_size() == 3
    monkeypatch.setenv("DB_POOL_SIZE", "not-a-number")
    assert connection._pool_size() == 10
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)

    # ------------------------------------------------------------------
    # Reuse: a request served a second time opens no new connection.
    # New connections are counted at psycopg2.connect.
    # ------------------------------------------------------------------
    connects = [0]

    def counting_connect(*args, **kwargs):
        connects[0] += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(psycopg2, "connect", counting_connect)
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY
    assert connects[0] == 1

    connects[0] = 0
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY
    assert connects[0] == 0

    # The phases below run the pool's own probe and reset, which only
    # the original factory may see: put it back.
    monkeypatch.setattr(psycopg2, "connect", real_connect)

    # ------------------------------------------------------------------
    # Isolation: after a get_cursor() body raises, the next user of
    # that connection sees no open transaction and none of the failed
    # statement's effects.  A block served the way a request is served:
    # its first get_cursor() opens the block's pooled connection and
    # leaves a temporary table on it — session state the pool must not
    # carry to the next user; its second get_cursor() body inserts a
    # row and raises, and the rollback undoes it; the block's next
    # get_cursor() takes the same connection, sees none of the failed
    # statement's effects, and commits a sentinel row in it.
    # ------------------------------------------------------------------
    def failing_user() -> None:
        with one_connection():
            with get_cursor() as cur:
                cur.execute("CREATE TEMP TABLE issue1570_probe (id int)")
            raised = False
            try:
                with get_cursor() as cur:
                    cur.execute(
                        "INSERT INTO item_sites (id, item_code, site_code, "
                        "unit_cost, reorder_point, safety_stock, source_system) "
                        "VALUES (%s, %s, %s, 1.0, 25.0, 10.0, %s)",
                        (FAILED_ID, "ITEM-1570-F", "SITE-1570-F", "test"),
                    )
                    raise RuntimeError("the body raises")
            except RuntimeError:
                raised = True
            assert raised
            with get_cursor() as cur:
                cur.execute(
                    "SELECT count(*) AS n FROM item_sites WHERE id = %s",
                    (FAILED_ID,),
                )
                assert cur.fetchone()["n"] == 0
                cur.execute(
                    "INSERT INTO item_sites (id, item_code, site_code, "
                    "unit_cost, reorder_point, safety_stock, source_system) "
                    "VALUES (%s, %s, %s, 1.0, 25.0, 10.0, %s)",
                    (SENTINEL_ID, "ITEM-1570-S", "SITE-1570-S", "test"),
                )

    _run_in_worker_thread(failing_user)

    # From a second backend: the failed statement left no effects, and
    # the block's commit was real — the sentinel is visible, where a
    # row an open transaction still held would be invisible.  No
    # connection carried an open transaction to the next user.
    second = real_connect(**get_connection_params())
    try:
        with second.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM item_sites WHERE id = %s",
                (FAILED_ID,),
            )
            assert cur.fetchone()[0] == 0
            cur.execute(
                "SELECT count(*) AS n FROM item_sites WHERE id = %s",
                (SENTINEL_ID,),
            )
            assert cur.fetchone()[0] == 1
    finally:
        second.close()

    # And no other state either: the block gave its connection back,
    # the pool reset it before this different user took it, and the
    # temporary table is gone — a connection without the reset would
    # still carry it.
    def next_user():
        with get_cursor() as cur:
            cur.execute("SELECT to_regclass('issue1570_probe') AS t")
            return cur.fetchone()["t"]

    assert _run_in_worker_thread(next_user) is None

    # Back to the deterministic start for the phases that follow.
    with get_cursor() as cur:
        cur.execute(
            "DELETE FROM item_sites WHERE id IN (%s, %s)",
            (FAILED_ID, SENTINEL_ID),
        )

    # ------------------------------------------------------------------
    # Recovery: a connection the database has closed is never handed
    # out again: the next request still answers.
    # ------------------------------------------------------------------
    def terminate_idle_backends() -> list[int]:
        """The database closes every idle backend of the test database."""
        admin = real_connect(**get_connection_params())
        try:
            admin.autocommit = True
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

    def wait_backends_gone(pids: list[int]) -> None:
        """Wait until none of the terminated backends is connected."""
        admin = real_connect(**get_connection_params())
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

    # A warm request: its connection goes back to the pool, idle.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY

    # The database closes every idle connection the pool holds.
    pids = terminate_idle_backends()
    assert pids
    wait_backends_gone(pids)

    # The pool probes before it hands out: the dead connections are
    # dropped, never handed out again, and the request still answers —
    # as does the next one.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_BODY

    # The next phase counts from the database side: leave the pool
    # nothing but dead references, so the count sees nothing but what
    # the burst opens.
    wait_backends_gone(terminate_idle_backends())

    # ------------------------------------------------------------------
    # Concurrency: several requests served at once from separate
    # threads all answer correctly, and the process never holds more
    # connections than its bound.
    # ------------------------------------------------------------------
    bound = 4
    monkeypatch.setenv("DB_POOL_SIZE", str(bound))
    connects = [0]

    def counting_connect(*args, **kwargs):
        connects[0] += 1
        return real_connect(*args, **kwargs)

    # The sampler polls pg_stat_activity, so the connections the
    # process holds are counted from the database side.  Its own
    # connection is opened with the original factory, before the
    # counting wrapper is bound, so it is not one of the counted.
    sampler_conn = real_connect(**get_connection_params())
    sampler_conn.autocommit = True
    max_seen = [0]
    stop = threading.Event()

    def sampler() -> None:
        with sampler_conn.cursor() as cur:
            while not stop.is_set():
                cur.execute(
                    "SELECT count(*) AS n FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (dbname,),
                )
                seen = cur.fetchone()[0]
                if seen > max_seen[0]:
                    max_seen[0] = seen
                time.sleep(0.005)

    sampler_thread = threading.Thread(target=sampler)
    sampler_thread.start()
    monkeypatch.setattr(psycopg2, "connect", counting_connect)
    try:
        failures: list[str] = []
        failures_lock = threading.Lock()
        n_threads = 6
        n_requests = 4

        def worker() -> None:
            with TestClient(
                app, headers={"Authorization": f"Bearer {dev_token}"}
            ) as thread_client:
                for _ in range(n_requests):
                    response = thread_client.get(URL)
                    if response.status_code != 200 or response.json() != EMPTY_BODY:
                        with failures_lock:
                            failures.append(response.text)
                        return

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        stop.set()
        sampler_thread.join()
        sampler_conn.close()

    assert not failures
    # The requests answered with the bound's connections: the process
    # held at least one, and never more than the bound — from the
    # database side, and in total, through the counting factory.
    assert 1 <= max_seen[0] <= bound
    assert 1 <= connects[0] <= bound
