"""Issue #1612 proof: the garbage collector never blocks on the pool.

AC-1 — a pooled connection the collector finalizes before its
``close()`` ran is given back to the pool, or closed, without the
finalizer ever waiting on a lock: proven the way a worst-case
collector runs it — on the thread that drops the handle, a thread
holding the pool's own lock, and another lock, at the very moment
the handle dies, twice over, on two different threads — where a
finalizer that returned the connection through the pool's own lock
would still be waiting on the lock it holds, a thread that never
finishes; and the connection not lost with it: the pool's count of
the connections it has open stays exact through the finalization
and the settle — sampled continuously, never once above the bound —
the connection ends idle in the pool, still usable by the next
user, and a checkout that blocked on the full bound the connection
filled finishes once the collector has settled it, instead of
waiting on it.

The pool is driven the way the app drives it: on threads named the
way anyio names its worker threads, the bound set to one
connection, so the pool is full the moment a connection is checked
out and a second checkout must wait.  A spy on the pool's finalize
handoff — the path the finalizer takes — records, on the
finalizer's own thread, whether the pool's lock sat with someone at
the moment of the handoff: a non-blocking probe, which waits on
nothing, so the spy proves the context without changing it.

And the lazy start of the reaper is proven to settle a finalized
connection exactly once: two finalizers race it the way the
collector can — both finding no reaper yet, the check waiting on no
lock — and the race is forced with a barrier in the thread
constructor, so the two reapers are running together for certain,
on a pool of their own, with connections that count how often the
pool closes them: settled twice, closed twice; settled never,
never; settled once, once — and with only one reaper left on the
pool when it is done, the twin gone.
"""

import gc
import threading
import time
from contextlib import suppress

import psycopg2

import app.db.connection as connection
from app.db.connection import _pool, get_connection

# The name anyio gives every worker thread it spawns to run the
# app's synchronous endpoints: a thread of that name is the request
# context, so the pool serves it.
WORKER_THREAD_NAME = "AnyIO worker thread"
# The bound for the proof: one connection, so the pool is full the
# moment a connection is checked out, and a second checkout waits.
BOUND = 1
# A thread that finished is a thread that did not wait on a lock: a
# finalizer that waited on the pool's own lock would never finish
# on the thread that holds it.
TIMEOUT = 15.0


class _FinalizedConnection:
    """A handed-off connection that counts how often it is closed.

    Closed from the start — a connection the pool cannot reuse — so
    settling it takes the plain close path, no reset, no database:
    settled once, its close runs once; settled twice, twice; settled
    never, never.
    """

    closed = 1

    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self.closed = 1


def _count() -> int:
    """The pool's count of the connections it has open."""
    with _pool._wake:
        return _pool._live + _pool._closing


def _drain_pool() -> None:
    """Close every idle pooled connection: the pool starts empty."""
    with _pool._wake:
        idle = list(_pool._idle)
        assert _pool._live == len(idle), "a pooled connection is checked out"
        _pool._idle.clear()
        _pool._live = 0
        _pool._closing = 0
        _pool._wake.notify_all()
    for conn, _factory in idle:
        with suppress(Exception):
            conn.close()


def _run_in_worker_thread(fn, timeout=TIMEOUT):
    """Run ``fn`` on a thread named the app's request worker threads.

    A thread that did not finish in time is a thread that waited on
    a lock: a finalizer that waited on the pool's own lock would
    never finish, on the thread that holds it.
    """
    box: dict = {}

    def target() -> None:
        try:
            box["result"] = fn()
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=target, name=WORKER_THREAD_NAME)
    thread.daemon = True
    thread.start()
    thread.join(timeout)
    assert not thread.is_alive(), "a worker thread did not finish in time"
    if "error" in box:
        raise box["error"]
    return box["result"]


def _start_bound_checker(pool):
    """Sample the pool's open count continuously, the whole test.

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


def test_issue1612_freeform(monkeypatch) -> None:
    pool = connection._pool
    saved_name = threading.current_thread().name
    _drain_pool()
    monkeypatch.setenv("DB_POOL_SIZE", str(BOUND))
    assert connection._pool_size() == BOUND

    # The spy: the finalizer's handoff, recorded on the finalizer's
    # own thread, with a non-blocking probe of the pool's lock — it
    # waits on nothing, so it proves the context without changing it.
    finalize_records: list[dict] = []
    original_finalize = pool.finalize

    def spy_finalize(conn, factory) -> None:
        free = pool._lock.acquire(blocking=False)
        try:
            finalize_records.append(
                {
                    "thread": threading.current_thread().name,
                    "pool_lock_held": not free,
                }
            )
        finally:
            if free:
                pool._lock.release()
        original_finalize(conn, factory)

    monkeypatch.setattr(pool, "finalize", spy_finalize)

    checker_thread, checker_stop, bound_max = _start_bound_checker(pool)
    try:
        # The main thread is a request thread for the rest of the
        # test: the pool serves it.
        threading.current_thread().name = WORKER_THREAD_NAME

        # ------------------------------------------------------------------
        # AC-1: the finalizer never waits on a lock.  A worker thread
        # checks out the one connection of the bound, then drops it —
        # no close() — while it holds the pool's own lock and another
        # lock: the collector finalizes the handle there, on that
        # thread, and a finalizer that waited on the pool's lock
        # would wait on the very lock the thread holds and never
        # finish.
        # ------------------------------------------------------------------
        def finalize_holding_the_locks() -> None:
            handle = get_connection()
            assert _count() == BOUND
            other = threading.Lock()
            other.acquire()
            pool._wake.acquire()
            try:
                del handle
                gc.collect()
            finally:
                pool._wake.release()
                other.release()

        _run_in_worker_thread(finalize_holding_the_locks)

        # And the connection is not lost with the finalizer: the
        # pool's reaper gave it back — reset, idle for the next
        # user — the count still exact at the bound's one open
        # connection.
        deadline = time.monotonic() + TIMEOUT
        with pool._wake:
            idle = len(pool._idle)
        while idle < BOUND and time.monotonic() < deadline:
            time.sleep(0.01)
            with pool._wake:
                idle = len(pool._idle)
        assert (
            idle == BOUND
        ), "the finalized connection was never given back to the pool"
        assert _count() == BOUND

        # Given back, not lost: the next user takes it and it works.
        def reuse() -> None:
            again = get_connection()
            with again.cursor() as cur:
                cur.execute("SELECT 1")
                assert cur.fetchone()[0] == 1
            again.close()

        _run_in_worker_thread(reuse)
        assert _count() == BOUND

        # ------------------------------------------------------------------
        # And a checkout that blocked on the full bound the
        # finalized connection filled finishes once the collector has
        # settled it: it never waits on a connection the collector
        # finalized.  The same finalization, this time on the main
        # thread, holding the same locks.
        # ------------------------------------------------------------------
        held = get_connection()
        assert _count() == BOUND

        def waiter() -> None:
            wait_conn = get_connection()
            wait_conn.close()

        waiter_thread = threading.Thread(target=waiter, name=WORKER_THREAD_NAME)
        waiter_thread.daemon = True
        waiter_thread.start()
        time.sleep(0.3)
        assert waiter_thread.is_alive(), "the waiter never reached the bound"
        other = threading.Lock()
        other.acquire()
        pool._wake.acquire()
        try:
            del held
            gc.collect()
        finally:
            pool._wake.release()
            other.release()
        waiter_thread.join(TIMEOUT)
        assert (
            not waiter_thread.is_alive()
        ), "a checkout waited on a connection the collector finalized"
        assert _count() == BOUND
    finally:
        checker_stop.set()
        checker_thread.join(TIMEOUT)
        threading.current_thread().name = saved_name
        with suppress(Exception):
            _drain_pool()

    # The bound never broke, and the proof is not vacuous: a
    # finalization really did run on a thread holding the pool's own
    # lock, and did not wait on it.
    assert (
        bound_max[0] <= BOUND
    ), f"the pool held {bound_max[0]} connections open, above the bound of {BOUND}"
    assert any(
        record["pool_lock_held"] for record in finalize_records
    ), "no finalization ran on a thread holding the pool's own lock"

    # ------------------------------------------------------------------
    # And the lazy start of the reaper cannot settle a finalized
    # connection twice: two finalizers may both find no reaper yet —
    # the check waits on no lock, so the race is a fact, not a fear —
    # and each start one, and two reapers on one handoff list would
    # settle a connection twice and let its twin go unsettled.  The
    # race is forced, not hoped for: a barrier in the thread
    # constructor holds the first finalizer before it has named its
    # reaper, so the second is certain to pass the "no reaper yet"
    # check, and the two reapers are running together for certain.
    # ------------------------------------------------------------------
    fresh = connection._Pool()
    fakes = [_FinalizedConnection() for _ in range(2)]
    # Counted open, as a checkout counts them, so the pool's count
    # must land exactly on zero once the reaper has settled both.
    fresh._live = len(fakes)

    real_thread = connection.threading.Thread
    start_race = threading.Barrier(2)

    class racing_thread(real_thread):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            start_race.wait(timeout=TIMEOUT)

    finalizers = [
        real_thread(target=fresh.finalize, args=(fake, psycopg2.connect))
        for fake in fakes
    ]
    monkeypatch.setattr(connection.threading, "Thread", racing_thread)
    try:
        for finalizer in finalizers:
            finalizer.start()
        for finalizer in finalizers:
            finalizer.join(TIMEOUT)
    finally:
        monkeypatch.undo()
    assert all(not finalizer.is_alive() for finalizer in finalizers), (
        "a finalizer did not finish: the start of the reaper made it " "wait on a lock"
    )

    # And only one reaper is left to settle the pool's handoff list:
    # two reapers on one list would settle a connection twice.  The
    # racing start is certain to have spawned two — the barrier
    # proves it — so this is the twin exiting, deterministically.
    deadline = time.monotonic() + TIMEOUT
    while True:
        reapers = []
        for t in threading.enumerate():
            target = getattr(t, "_target", None)
            if (
                t.is_alive()
                and target is not None
                and getattr(target, "__self__", None) is fresh
                and getattr(target, "__name__", None) == "_reap"
            ):
                reapers.append(t)
        if len(reapers) <= 1 or time.monotonic() > deadline:
            break
        time.sleep(0.005)
    assert len(reapers) == 1, (
        f"{len(reapers)} reapers are settling the pool's handoff list: "
        "the racing start of the reaper left twins, and twins settle "
        "a connection twice"
    )

    deadline = time.monotonic() + TIMEOUT
    while any(fake.close_calls == 0 for fake in fakes) and time.monotonic() < deadline:
        time.sleep(0.005)
    for fake in fakes:
        assert fake.close_calls == 1, (
            f"a finalized connection was settled {fake.close_calls} "
            "times: the racing start of the reaper settled it twice, "
            "or let its twin go unsettled"
        )
    with fresh._wake:
        open_now = fresh._live + fresh._closing
    assert open_now == 0, (
        f"the pool counts {open_now} connections open after settling "
        f"{len(fakes)}: the count is no longer exact"
    )

    # And the reaper that won the race still settles what comes
    # next: the race costs the pool a thread, not the handoff.
    survivor = _FinalizedConnection()
    fresh._live += 1
    fresh.finalize(survivor, psycopg2.connect)
    deadline = time.monotonic() + TIMEOUT
    while survivor.close_calls == 0 and time.monotonic() < deadline:
        time.sleep(0.005)
    assert (
        survivor.close_calls == 1
    ), "the reaper stopped settling after the racing start"
