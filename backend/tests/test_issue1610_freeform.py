"""Issue #1610 proof: a given-back connection is out of its holder's
reach, and a cursor that cannot be opened leaves nothing behind.

AC-1 — a pooled connection whose ``close()`` has given it back to the
pool is unreachable through the holder's handle: leaving a ``with``
block on the handle commits nothing and rolls back nothing — on a
clean exit and on an exception alike, so the connection the pool
hands out again still sits in the transaction the holder's body
opened — every other use fails the way a closed psycopg2 connection
fails (same exception type, same message, ``closed`` nonzero), and no
attribute of the handle, public or private, still holds the
connection.  A cursor the holder opened before ``close()`` is the
holder's to close first, as ``get_cursor()`` does: the pool leaves it
open, and the holder closes it.

AC-2 — when ``get_cursor()`` cannot open its cursor, the caller gets
the error that stopped it, never one raised by ``get_cursor()``'s own
clean-up — not the broken ``rollback()`` it attempts, not a
``close()`` of a cursor that was never opened.  Outside a
``one_connection()`` block the connection ``get_cursor()`` took is
given back to the pool where the app serves requests, and closed
where it does not, before that error reaches the caller; inside a
block the block's connection stays the block's — the block's next
``get_cursor()`` still uses it — and is closed when the outermost
block exits, as today.

The pool is driven the way the app drives it: on a thread named the
way anyio names its worker threads, with the connect factory rebound
to a recording wrapper, so every connection the app opens from then
on is one the wrapper made and recorded.
"""

import threading

import psycopg2
import psycopg2.extensions
import pytest

import app.db.connection as connection
from app.db.connection import (
    get_connection,
    get_connection_params,
    get_cursor,
    one_connection,
)

# The connect factory as of import time, before the test rebinds it:
# every spy wraps a real connection, never another spy.
_ORIGINAL_CONNECT = psycopg2.connect


class _SpyConnection:
    """A real connection with a recording cover.

    ``cursor()`` fails on the first calls the spec marks to fail, and
    ``commit()`` and ``rollback()`` fail when the spec marks them
    broken — the broken clean-up the proof shows never reaching the
    caller.  Every call of interest is recorded; everything else
    delegates to the real connection.
    """

    def __init__(self, real, spec):
        self._real = real
        self._spec = dict(spec)
        self._cursor_calls = 0
        self.calls = []
        self.closed = 0

    def cursor(self, *args, **kwargs):
        self._cursor_calls += 1
        self.calls.append("cursor")
        if self._cursor_calls <= self._spec["fail_cursor"]:
            raise psycopg2.OperationalError("cursor boom")
        return self._real.cursor(*args, **kwargs)

    def commit(self):
        self.calls.append("commit")
        if self._spec["broken_commit"]:
            raise psycopg2.OperationalError("commit boom")
        self._real.commit()

    def rollback(self):
        self.calls.append("rollback")
        if self._spec["broken_rollback"]:
            raise psycopg2.OperationalError("rollback boom")
        self._real.rollback()

    def close(self):
        self.calls.append("close")
        if self.closed == 0:
            self._real.close()
            self.closed = 1

    def __getattr__(self, name):
        return getattr(self._real, name)


def _install_spy_factory(monkeypatch):
    """Bind a recording factory to ``psycopg2.connect``.

    Every connection the app opens from then on is a
    ``_SpyConnection`` wrapped around a real one, recorded in the
    list that is returned.  The spec dict, read at each connection's
    creation, marks which of its calls fail.
    """
    spies = []
    spec = {"fail_cursor": 0, "broken_commit": False, "broken_rollback": False}

    def factory(*args, **kwargs):
        spy = _SpyConnection(_ORIGINAL_CONNECT(*args, **kwargs), spec)
        spies.append(spy)
        return spy

    monkeypatch.setattr(psycopg2, "connect", factory)
    return spies, spec


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


def test_issue1610_freeform(monkeypatch) -> None:
    # The pool is process-wide, and earlier tests may leave
    # connections idle in it: a bound no checkout can block on.
    monkeypatch.setenv("DB_POOL_SIZE", "100")

    # ------------------------------------------------------------------
    # AC-1: a given-back connection is out of its holder's reach.
    # ------------------------------------------------------------------
    spies, _spec = _install_spy_factory(monkeypatch)

    def given_back_checks() -> None:
        handle = get_connection()
        assert isinstance(handle, connection._PooledConnection)
        spy = spies[-1]
        assert handle.closed == 0
        # A cursor the holder opens before close is the holder's to
        # close first: it works while the handle is the holder's...
        cursor = handle.cursor()
        cursor.execute("SELECT 1")
        assert cursor.fetchone() == (1,)
        # ...and giving the connection back to the pool leaves it
        # open — the pool does not close it for the holder.
        handle.close()
        assert handle.closed == 1
        assert "commit" not in spy.calls
        assert "rollback" not in spy.calls
        # How a closed psycopg2 connection fails — the signature every
        # use of the given-back handle must match.
        victim = _ORIGINAL_CONNECT(**get_connection_params())
        victim.close()
        for name in ("cursor", "commit", "rollback"):
            with pytest.raises(psycopg2.InterfaceError) as victim_exc:
                getattr(victim, name)()
            victim_signature = (
                type(victim_exc.value),
                str(victim_exc.value),
                victim.closed,
            )
            with pytest.raises(psycopg2.InterfaceError) as excinfo:
                getattr(handle, name)()
            assert (
                type(excinfo.value),
                str(excinfo.value),
                handle.closed,
            ) == victim_signature
        # No attribute of the handle, public or private, still holds
        # the connection.
        assert handle._conn is None
        assert all(
            value is not spy
            for value in (getattr(handle, slot) for slot in type(handle).__slots__)
        )
        # The holder's pre-close cursor is still the holder's: open,
        # and closeable by the holder.
        assert cursor.closed == 0
        cursor.close()
        assert cursor.closed == 1
        # Leaving a with block on the handle commits nothing and rolls
        # back nothing — on a clean exit...
        second = get_connection()
        assert second._conn is spy
        with second:
            second.close()
        assert second._conn is None
        assert second.closed == 1
        assert "commit" not in spy.calls
        assert "rollback" not in spy.calls
        # ...and on an exception, alike.
        third = get_connection()
        assert third._conn is spy
        try:
            with third:
                third.close()
                raise RuntimeError("the body raises")
        except RuntimeError:
            pass
        assert third._conn is None
        assert "commit" not in spy.calls
        assert "rollback" not in spy.calls
        # Both exits left the connection exactly where the pool put
        # it: still sitting in the transaction the holder's body
        # opened.
        fourth = get_connection()
        assert fourth._conn is spy
        assert (
            fourth.get_transaction_status()
            == psycopg2.extensions.TRANSACTION_STATUS_INTRANS
        )
        fourth.close()

    _run_in_worker_thread(given_back_checks)

    # ------------------------------------------------------------------
    # AC-2, where the app serves requests: the connection
    # get_cursor() took is given back to the pool before the
    # cursor's error reaches the caller.
    # ------------------------------------------------------------------
    spies, spec = _install_spy_factory(monkeypatch)
    spec.update(fail_cursor=1, broken_rollback=True)

    def failing_checkout() -> None:
        with pytest.raises(psycopg2.OperationalError) as excinfo, get_cursor():
            pass
        assert str(excinfo.value) == "cursor boom"
        spy = spies[-1]
        assert "rollback" in spy.calls
        assert spy.closed == 0
        again = get_connection()
        assert again._conn is spy
        again.close()

    _run_in_worker_thread(failing_checkout)

    # ------------------------------------------------------------------
    # AC-2, where the app does not serve requests: the connection
    # get_cursor() took is closed before the cursor's error reaches
    # the caller.
    # ------------------------------------------------------------------
    spies, spec = _install_spy_factory(monkeypatch)
    spec.update(fail_cursor=1, broken_rollback=True)

    with pytest.raises(psycopg2.OperationalError) as excinfo, get_cursor():
        pass
    assert str(excinfo.value) == "cursor boom"
    spy = spies[-1]
    assert "rollback" in spy.calls
    assert spy.closed == 1

    # ------------------------------------------------------------------
    # AC-2, inside a one_connection() block: the block's connection
    # stays the block's across a get_cursor() that cannot open its
    # cursor, and is closed when the outermost block exits, as today.
    # ------------------------------------------------------------------
    spies, spec = _install_spy_factory(monkeypatch)
    spec.update(fail_cursor=1, broken_rollback=True)

    with one_connection():
        with pytest.raises(psycopg2.OperationalError) as excinfo, get_cursor():
            pass
        assert str(excinfo.value) == "cursor boom"
        spy = spies[-1]
        assert "rollback" in spy.calls
        assert spy.closed == 0
        with get_cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()["?column?"] == 1
    assert spy.closed == 1
