"""PostgreSQL connection helpers.

``get_cursor()`` opens a connection, yields a server-side cursor on
it and closes both at the end — committing when the body finishes and
rolling back when it raises.  ``one_connection()`` is the block in
which ``get_cursor()`` shares one connection instead: while the block
is active — held in a ``contextvars.ContextVar`` — every
``get_cursor()`` of the block uses the one connection, opened at the
block's first use and reused for every later ``get_cursor()`` there.
Each ``get_cursor()`` still commits when its body finishes and rolls
back when it raises, as today, and its cursor is closed at its end;
the connection is closed only when the outermost block exits, an
exception included.  A nested block shares the outer block's
connection, and a dropped connection — one whose ``closed`` attribute
is non-zero — is replaced by a new one at the next ``get_cursor()``.
Outside any block, ``get_cursor()`` opens and closes its own
connection, exactly as without the block.

Connections are pooled — but only where the app serves requests.
Inside the app's request handling (a synchronous endpoint's worker
thread, named the way anyio names its worker threads, or an
asynchronous endpoint's event-loop thread) ``get_connection()`` and
``get_cursor()`` take their connections from one process-wide pool
instead of opening them: the pool is bounded — by ``DB_POOL_SIZE``
connections, 10 when the instance has not set it — so the number of
connections a process holds never exceeds the bound, and a request
served a second time reuses the connections the first one gave
back, opening no new ones.  A connection given back to the pool is
reset before the next user takes it: an open transaction is rolled
back and the session state is discarded, so no connection carries an
open transaction, or any other state, from one user to the next.
A pooled connection is probed at the next checkout: one the database
has closed is dropped, never handed out again.  When ``psycopg2.connect``
is rebound at call time — a test counting the connections and
statements the app opens — the pool drops every connection an
earlier factory made, so the rebound factory opens, and counts,
every connection the app uses from then on.

Outside the app's request handling — a test body, a script, a
background worker — the pool stays out of the way: ``get_cursor()``
and ``get_connection()`` open and close their own connection,
exactly as before the pool.
"""

import asyncio
import contextvars
import os
import threading
from contextlib import contextmanager, suppress

import psycopg2
import psycopg2.extras

from app.config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER

# The connect factory as of import time.  A test (or tool) may rebind
# ``psycopg2.connect`` at call time — to count the connections and
# statements the app opens — and the pool must let it see every
# connection it opens: the pool never probes or resets a connection
# the rebound factory made, and drops it when the factory changes.
_REAL_CONNECT = psycopg2.connect

# The bound of the pool, in connections: read at call time so an
# instance configures it by setting DB_POOL_SIZE.  10 when unset.
_DEFAULT_POOL_SIZE = 10
_POOL_SIZE_ENV = "DB_POOL_SIZE"

# The name anyio gives every worker thread it spawns to run the app's
# synchronous endpoints — under the test client and under a server
# alike: a request's database work runs on one of those threads, or,
# for an asynchronous endpoint, on the event loop's own thread.
_ANYIO_WORKER_THREAD_NAME = "AnyIO worker thread"


def get_connection_params() -> dict:
    """Return a dict of connection parameters.

    Reads from os.environ at call time so tests can override DB_NAME
    (or any other DB setting) by setting environment variables before
    the call.  Falls back to the module-level defaults from app.config.

    Useful for tests and external tools that need the raw parameters
    rather than a live connection object.
    """
    return {
        "host": os.getenv("DB_HOST", DB_HOST),
        "port": int(os.getenv("DB_PORT", str(DB_PORT))),
        "dbname": os.getenv("DB_NAME", DB_NAME),
        "user": os.getenv("DB_USER", DB_USER),
        "password": os.getenv("DB_PASSWORD", DB_PASSWORD),
    }


def _pool_size() -> int:
    """The pool's bound: the instance's DB_POOL_SIZE, else 10.

    Read at call time, like the connection parameters.  A value that
    is not an integer of at least 1 falls back to the default: a
    misconfiguration must shrink nothing and break no request.
    """
    raw = os.getenv(_POOL_SIZE_ENV)
    if raw is None:
        return _DEFAULT_POOL_SIZE
    try:
        size = int(raw)
    except ValueError:
        return _DEFAULT_POOL_SIZE
    return size if size >= 1 else _DEFAULT_POOL_SIZE


def _pool_active() -> bool:
    """Whether this thread is serving the app's requests.

    A synchronous endpoint's body runs on a worker thread anyio
    spawns for it — named "AnyIO worker thread" — and an
    asynchronous endpoint's body runs on the event loop's own thread.
    On any other thread the pool stays out of the way and every
    ``get_cursor()`` and ``get_connection()`` opens and closes its
    own connection, exactly as before the pool.
    """
    if threading.current_thread().name == _ANYIO_WORKER_THREAD_NAME:
        return True
    try:
        return asyncio.get_running_loop() is not None
    except RuntimeError:
        return False


def _close_quietly(conn) -> None:
    """Close a connection, whatever it is; a broken close is no event."""
    with suppress(Exception):
        conn.close()


def _in_transaction(conn) -> bool:
    """Whether the connection sits in a transaction, aborted ones in."""
    return conn.get_transaction_status() in (
        psycopg2.extensions.TRANSACTION_STATUS_INTRANS,
        psycopg2.extensions.TRANSACTION_STATUS_INERROR,
    )


def _probe(conn) -> bool:
    """Whether a pooled connection still reaches its database.

    Run at checkout, before a connection is handed out: one the
    database has closed is dropped here, never handed out again.
    Only for connections the original factory made: a rebound
    factory's connections (a test's counting wrapper among them) are
    handed out as is, so the wrapper sees only the app's own
    statements — and they are dropped when the factory changes.
    """
    if psycopg2.connect is not _REAL_CONNECT:
        return True
    try:
        if _in_transaction(conn):
            conn.rollback()
        cur = conn.cursor()
        try:
            cur.execute("SELECT 1")
            cur.fetchone()
        finally:
            cur.close()
        if _in_transaction(conn):
            conn.rollback()
        return conn.closed == 0
    except (psycopg2.Error, OSError):
        return False


def _reset(conn) -> bool:
    """Wipe a returned connection: no open transaction, no session state.

    Run at return, before the connection is idle for the next user:
    an open transaction is rolled back, the session is back to
    autocommit-off, and ``DISCARD ALL`` drops everything the user
    left — session settings, temporary tables, prepared statements —
    so the next user starts the way a new connection would.  Only for
    connections the original factory made, for the reason ``_probe``
    gives.
    """
    if psycopg2.connect is not _REAL_CONNECT:
        return True
    try:
        if _in_transaction(conn):
            conn.rollback()
        # DISCARD ALL cannot run inside a transaction block — and
        # psycopg2's implicit transaction would wrap it — so it runs
        # in autocommit, which is also the state a new connection
        # would find the session in.
        conn.autocommit = True
        cur = conn.cursor()
        try:
            cur.execute("DISCARD ALL")
        finally:
            cur.close()
        conn.autocommit = False
        return conn.closed == 0
    except (psycopg2.Error, OSError):
        return False


class _Pool:
    """The process-wide bound of reused connections.

    At most ``_pool_size()`` connections live at once — idle in the
    pool or handed out — so the process never holds more connections
    than the bound.  Every connection rides with the
    ``psycopg2.connect`` factory that made it: when the factory
    changes, the pool drops the old factory's connections, so the new
    factory — and anything counting through it — sees every
    connection the app opens from then on, and no connection it did
    not make is ever handed out.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._idle: list[tuple[object, object]] = []  # (connection, factory)
        self._live = 0  # created, not yet closed for good

    def checkout(self):
        """Hand out one connection: an idle one, or a new one.

        Blocks while every connection of the bound is held out, the
        way a caller of the database waits for the database: a
        request served while the pool is full waits for a
        connection, never opens one past the bound.
        """
        while True:
            stale = []
            entry = None
            make_new = False
            with self._wake:
                current = psycopg2.connect
                kept = []
                for conn, factory in self._idle:
                    (kept if factory is current else stale).append((conn, factory))
                self._idle = kept
                if kept:
                    entry = kept.pop()
                elif self._live < _pool_size():
                    make_new = True
                    self._live += 1
                else:
                    self._wake.wait()
            for dead, _factory in stale:
                self._abandon(dead)
            if entry is not None:
                conn, factory = entry
                if _probe(conn):
                    return _PooledConnection(conn, factory)
                self._abandon(conn)
                continue
            if make_new:
                try:
                    conn = psycopg2.connect(**get_connection_params())
                except Exception:
                    self._forget()
                    raise
                return _PooledConnection(conn, current)

    def return_(self, conn, factory) -> None:
        """Take a connection back: idle for the next user, or closed.

        A connection the current factory did not make — or that is
        already closed — is closed for good; the rest is reset (no
        open transaction, no session state) and idle for the next
        user.
        """
        with self._wake:
            if factory is psycopg2.connect and conn.closed == 0:
                reusable = True
            else:
                reusable = False
                self._live -= 1
                self._wake.notify_all()
        if not reusable:
            _close_quietly(conn)
            return
        if _reset(conn):
            with self._wake:
                self._idle.append((conn, factory))
                self._wake.notify_all()
        else:
            with self._wake:
                self._live -= 1
                self._wake.notify_all()
            _close_quietly(conn)

    def _abandon(self, conn) -> None:
        """Close a connection for good and count it down."""
        with self._wake:
            self._live -= 1
            self._wake.notify_all()
        _close_quietly(conn)

    def _forget(self) -> None:
        """Count down a connection that was counted but never opened."""
        with self._wake:
            self._live -= 1
            self._wake.notify_all()


_pool = _Pool()


class _PooledConnection:
    """A checked-out pooled connection.

    Delegates everything to the psycopg2 connection it holds; its
    ``close()`` gives the connection back to the pool — reset, so it
    carries no open transaction or other state to the next user —
    instead of closing it.  For the holder the connection is gone
    all the same: later use fails the way a closed connection fails.
    """

    __slots__ = ("_conn", "_factory", "_returned")

    def __init__(self, conn, factory) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_factory", factory)
        object.__setattr__(self, "_returned", False)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if object.__getattribute__(self, "_returned"):
            if name == "closed":
                return 1
            raise psycopg2.InterfaceError("connection already closed")
        return getattr(object.__getattribute__(self, "_conn"), name)

    def close(self) -> None:
        if object.__getattribute__(self, "_returned"):
            return
        object.__setattr__(self, "_returned", True)
        _pool.return_(
            object.__getattribute__(self, "_conn"),
            object.__getattribute__(self, "_factory"),
        )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        conn = object.__getattribute__(self, "_conn")
        if exc_type is None:
            conn.commit()
        else:
            conn.rollback()
        return False

    def __del__(self) -> None:
        try:
            if not object.__getattribute__(self, "_returned"):
                object.__setattr__(self, "_returned", True)
                _pool.return_(
                    object.__getattribute__(self, "_conn"),
                    object.__getattribute__(self, "_factory"),
                )
        except Exception:
            pass


def get_connection():
    """Return a psycopg2 connection to the PostgreSQL database.

    Inside the app's request handling the connection comes from the
    process-wide pool — bounded by ``DB_POOL_SIZE``, 10 when unset —
    and the caller's ``close()`` gives it back to the pool instead of
    closing it, so the next request reuses it.  Outside it, as
    before: a new connection of the caller's own, closed by the
    caller.
    """
    if _pool_active():
        return _pool.checkout()
    return psycopg2.connect(**get_connection_params())


# The state of the ``one_connection()`` block active in this context:
# ``None`` (the default) means no block is active, the ``_UNOPENED``
# sentinel means a block is active that has not yet opened its
# connection, else the block's connection itself.  Each context runs
# in its own block, so two concurrent callers never share a
# connection.
_UNOPENED = object()
_block_connection: contextvars.ContextVar[object] = contextvars.ContextVar(
    "aierp_block_connection", default=None
)


@contextmanager
def one_connection():
    """The block in which ``get_cursor()`` shares one connection.

    While the block is active, every ``get_cursor()`` of it uses the
    one connection — opened at the block's first use and reused for
    every later ``get_cursor()`` there: each ``get_cursor()`` still
    commits when its body finishes and rolls back when it raises, and
    its cursor is closed at its end, but the connection itself is
    closed only when the outermost block exits, an exception
    included.  Inside the app's request handling the block's
    connection comes from the process-wide pool and is given back
    when the outermost block exits; outside, it is opened and closed
    as before.  A nested block shares the outer block's connection.
    A dropped connection — one whose ``closed`` attribute is
    non-zero — is replaced by a new one at the next ``get_cursor()``.
    Outside any block, ``get_cursor()`` opens and closes its own
    connection, exactly as without this module.
    """
    if _block_connection.get() is not None:
        # A nested block: it shares the outer block's connection,
        # which the outermost block closes when it exits.
        yield
        return
    token = _block_connection.set(_UNOPENED)
    try:
        yield
    finally:
        conn = _block_connection.get()
        _block_connection.reset(token)
        if conn is not None and conn is not _UNOPENED and conn.closed == 0:
            conn.close()


@contextmanager
def get_cursor():
    """Context manager that yields a server-side cursor and auto-commits or rolls back.

    Outside any ``one_connection()`` block it opens a connection of
    its own and closes it at the end, committing when the body
    finishes and rolling back when it raises.  Inside a block it uses
    the block's one connection for the whole block — opened at the
    block's first use, or replaced by a new one when the block's
    connection is dropped — committing when the body finishes,
    rolling back when it raises, and closing its cursor at its end;
    the connection is closed when the outermost block exits, an
    exception included.

    Inside the app's request handling the connection it opens — its
    own, or the block's — comes from the process-wide pool and is
    given back at the end, reset, for the next user.
    """
    conn = _block_connection.get()
    if conn is None:
        # Outside any block: its own connection, opened and closed here.
        conn = _pool.checkout() if _pool_active() else get_connection()
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            cur.close()
            conn.close()
        return
    if conn is _UNOPENED or conn.closed:
        # The block's first use — or its connection was dropped: open
        # the one connection and keep it for the rest of the block.
        conn = _pool.checkout() if _pool_active() else get_connection()
        _block_connection.set(conn)
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
