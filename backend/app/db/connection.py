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
has closed is dropped, never handed out again, whichever factory
opened it: the request that would have taken it gets a working
connection instead.  A connection the rebound factory made is probed
without a statement — the socket it rides is watched, no query is
run on it — so a wrapper counting the app's connections and
statements sees none of the probe.  When ``psycopg2.connect``
is rebound at call time — a test counting the connections and
statements the app opens — the pool drops every connection an
earlier factory made, so the rebound factory opens, and counts,
every connection the app uses from then on.  A changed factory never
stalls a checkout: an idle connection an earlier factory made is
closed for good at the next checkout — before any connection the
current factory opens — so that checkout hands out one the current
factory made instead of waiting on the old factory's idle
connections, and the process never holds more connections open than
the bound while it closes them.

Outside the app's request handling — a test body, a script, a
background worker — the pool stays out of the way: ``get_cursor()``
and ``get_connection()`` open and close their own connection,
exactly as before the pool.

A pooled connection is never lost to the collector either: one whose
holder drops it before its ``close()`` runs is handed back by the
handle's finalizer — without the finalizer waiting on a lock, the
pool's own lock included, whatever thread it runs on and whatever
that thread holds — and the pool's reaper thread settles it off the
request paths, resetting it for the next user or closing it for
good, counted open until the close is done, so the bound never
breaks and a checkout that waits on it is woken when it settles.
"""

import asyncio
import contextvars
import os
import select
import socket
import threading
import time
from contextlib import contextmanager, suppress

import psycopg2
import psycopg2.extras

from app.config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER

# The connect factory as of import time.  A test (or tool) may rebind
# ``psycopg2.connect`` at call time — to count the connections and
# statements the app opens — and the pool must let it see every
# connection it opens: the pool probes a connection the rebound
# factory made without a statement, never resets it, and drops it
# when the factory changes.
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

# How long the pool's reaper thread sleeps between draining the
# handoff list: short enough that a checkout waiting on a bound
# the collector just freed does not notice the wait, light enough
# that a process that finalizes nothing pays for the reaper with
# one list check a hundred times a second.
_FINALIZE_POLL_SECONDS = 0.01


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


def _silent_probe_alive(data: bytes) -> bool:
    """Whether the data peeked from a pooled connection's socket
    keeps the connection usable.

    The end of the stream closes it.  Otherwise the data is the
    queue of server messages the connection has not read: an idle
    pooled connection has answered every query of its, so the queue
    holds only messages the database sent on its own — and an error
    message among them is the database ending the connection, which
    no live idle connection carries.  A message cut off mid-arrival
    is not decided: the connection is still delivering.
    """
    if not data:
        return False
    offset = 0
    while offset < len(data):
        message_type = data[offset : offset + 1]
        if message_type == b"Z":
            # ReadyForQuery is the one message without a length.
            offset += 1
            continue
        if len(data) < offset + 5:
            return message_type != b"E"
        end = offset + 1 + int.from_bytes(data[offset + 1 : offset + 5], "big")
        if message_type == b"E":
            return False
        if end > len(data):
            return True
        offset = end
    return True


def _probe_silently(conn) -> bool:
    """Whether a rebound factory's pooled connection still reaches
    its database, with no statement on it.

    The rebound factory — a test counting the connections and
    statements the app opens — would count a probe run on the
    connection as the app's, so the probe watches the connection's
    socket instead: the database closed the connection, the socket
    answers a peek with the end of the stream, or with the
    database's last word; a socket with no data at all has not been
    closed.  A handle that shows no socket to watch is handed out
    as is, as before the probe learned to be silent.
    """
    try:
        if conn.closed:
            return False
        fd = conn.fileno()
    except (psycopg2.Error, OSError, ValueError):
        return True
    try:
        readable, _, _ = select.select([fd], [], [], 0)
    except (OSError, ValueError):
        return True
    if not readable:
        return True
    try:
        sock = socket.fromfd(fd, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return True
    try:
        try:
            data = sock.recv(65536, socket.MSG_PEEK)
        except BlockingIOError:
            return True
        except OSError:
            return False
        return _silent_probe_alive(data)
    finally:
        sock.close()


def _probe(conn) -> bool:
    """Whether a pooled connection still reaches its database.

    Run at checkout, before a connection is handed out: one the
    database has closed is dropped here, never handed out again,
    whichever factory opened it.  A connection the original factory
    made is probed with a statement on it; a connection a rebound
    factory made is probed the way ``_probe_silently`` does — no
    statement, so a wrapper counting the app's connections and
    statements sees none of the probe.
    """
    if psycopg2.connect is not _REAL_CONNECT:
        return _probe_silently(conn)
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
    connections the original factory made: a reset on a rebound
    factory's connection would ride its wrapper as a statement the
    app did not run, so it is skipped, and the wrapper sees only the
    app's own statements.
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

    At most ``_pool_size()`` connections are open at once — idle in
    the pool, handed out, or in the middle of being closed — so the
    process never holds more connections open than the bound, each
    connection counted open until it is closed.  Every connection
    rides with the ``psycopg2.connect`` factory that made it: when
    the factory changes, the pool closes the old factory's idle
    connections at the next checkout — before any connection the new
    factory opens — so the new factory, and anything counting
    through it, sees every connection the app opens from then on,
    and no connection it did not make is ever handed out.  A
    connection whose handle the collector finalized before its
    ``close()`` ran rides with the handoff list instead: the
    finalizer appends it without waiting on any lock — the pool's
    own lock included, whatever thread it runs on and whatever that
    thread holds — and the reaper thread drains the list and settles
    each connection through ``return_``, counted open until the
    close is done.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._wake = threading.Condition(self._lock)
        self._idle: list[tuple[object, object]] = []  # (connection, factory)
        self._live = 0  # open, not in the middle of being closed
        self._closing = 0  # open, in the middle of being closed
        # (connection, factory) pairs whose handles the collector
        # finalized before close() ran: the finalizer appends here
        # without waiting on any lock, and the reaper thread drains
        # the list and settles each one through return_.
        self._finalized: list[tuple[object, object]] = []
        self._reaper: threading.Thread | None = None
        # The lazy start is racy by design — a finalizer must wait
        # on no lock, so two finalizers may both find no reaper and
        # each start one — and this claim, taken by the reapers on
        # their own thread, settles that race: the first reaper to
        # claim it drains the handoff list, every reaper after it
        # exits without settling anything, and a finalized
        # connection is settled exactly once.
        self._reaper_claim = threading.Lock()
        self._reaper_claimed = False

    def checkout(self):
        """Hand out one connection: an idle one, or a new one.

        Blocks only while every connection of the bound is still
        open — checked out, or in the middle of being closed — the
        way a caller of the database waits for the database: a
        request served while the pool is full waits for a
        connection, never opens one past the bound.  A changed
        factory never stalls it: an idle connection an earlier
        factory made is closed for good before any connection the
        current factory opens, so the checkout hands out one the
        current factory made instead of waiting on the old factory's
        idle connections.
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
                if stale:
                    # An earlier factory's idle connections are never
                    # handed out: counted as open until each is closed
                    # for good below — before any new connection is
                    # opened, so the bound holds and the changed
                    # factory never stalls this checkout.
                    self._live -= len(stale)
                    self._closing += len(stale)
                if kept:
                    entry = kept.pop()
            for dead, _factory in stale:
                self._settle(dead)
            if entry is not None:
                conn, factory = entry
                if _probe(conn):
                    return _PooledConnection(conn, factory)
                self._abandon(conn)
                continue
            with self._wake:
                if self._live + self._closing < _pool_size():
                    make_new = True
                    self._live += 1
                else:
                    self._wake.wait()
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
                self._mark_closing()
        if not reusable:
            self._settle(conn)
            return
        if _reset(conn):
            with self._wake:
                self._idle.append((conn, factory))
                self._wake.notify_all()
        else:
            with self._wake:
                self._mark_closing()
            self._settle(conn)

    def finalize(self, conn, factory) -> None:
        """Hand off a connection whose handle the collector finalized.

        Called from ``_PooledConnection.__del__``, which the
        collector may run on any thread — one holding this pool's
        own lock among them — so it waits on no lock, the pool's
        included: it appends the connection to the handoff list, and
        the reaper thread settles it through the ordinary
        ``return_`` path — reset and idle for the next user, or
        closed for good — where it keeps counting as open until the
        close is done, so the bound never breaks and a checkout that
        waits on it is woken when it settles.  Two finalizers may
        race this start — it waits on no lock, so the race is a fact,
        not a fear — and start two reapers; the reapers claim the
        settling among themselves, on their own thread, and the
        connection is settled exactly once.
        """
        self._finalized.append((conn, factory))
        if self._reaper is None:
            self._reaper = threading.Thread(
                target=self._reap,
                name="aierp-db-pool-reaper",
                daemon=True,
            )
            self._reaper.start()

    def _reap(self) -> None:
        """The reaper loop: settle every finalized connection.

        Runs on a dedicated daemon thread, off the request paths:
        the only thread that ever settles a collector-finalized
        connection, so it is the one that takes the pool's lock
        where the count must move — a finalizer never does — and it
        sleeps between drains, so a process that finalizes nothing
        pays for it with one list check a hundred times a second.
        The racing start may have spawned a twin reaper, and two
        reapers on one handoff list would settle a connection twice:
        the claim below is where they meet, on their own thread, a
        finalizer never taking it — the first to claim drains the
        list, the rest exit without settling anything.
        """
        with self._reaper_claim:
            if self._reaper_claimed:
                return
            self._reaper_claimed = True
        while True:
            pending, self._finalized = self._finalized, []
            for conn, factory in pending:
                with suppress(Exception):
                    self.return_(conn, factory)
            if not pending:
                time.sleep(_FINALIZE_POLL_SECONDS)

    def _mark_closing(self) -> None:
        """Move one open connection into the middle of being closed.

        Called with the pool's lock held: the connection stops
        counting as live, but keeps counting as open — as
        ``_closing`` — until ``_settle`` has closed it, so the bound
        holds while the close is in flight off the lock.
        """
        self._live -= 1
        self._closing += 1

    def _settle(self, conn) -> None:
        """Close a closing connection for good and count it down."""
        _close_quietly(conn)
        with self._wake:
            self._closing -= 1
            self._wake.notify_all()

    def _abandon(self, conn) -> None:
        """Close a connection for good, counted open until it is closed."""
        with self._wake:
            self._mark_closing()
        self._settle(conn)

    def _forget(self) -> None:
        """Count down a connection that was counted but never opened."""
        with self._wake:
            self._live -= 1
            self._wake.notify_all()


_pool = _Pool()


class _PooledConnection:
    """A checked-out pooled connection.

    Delegates everything to the psycopg2 connection it holds — a
    read of an attribute of the handle, a call of one, or an
    assignment to it reaches the connection, the way it would on the
    connection itself — and its ``close()`` gives the connection
    back to the pool, reset, so it carries no open transaction or
    other state to the next user, instead of closing it.  For the
    holder the connection is gone all the same: once ``close()`` has
    given it back, no attribute of the handle, public or private,
    still holds the connection, every other use fails the way a
    closed connection fails, and leaving a ``with`` block on the
    handle commits nothing and rolls back nothing.  A cursor the
    holder opened before ``close()`` is the holder's to close, as
    ``get_cursor()`` closes its own.  Left without a ``close()`` —
    a holder that dies with the connection still checked out — the
    collector finalizes the handle, and the connection is still
    given back: the finalizer hands it to the pool without waiting
    on any lock, the pool's own lock included, whatever thread it
    runs on and whatever that thread holds, and the pool's reaper
    thread settles it off the request paths — reset and idle for
    the next user, or closed for good — counted open until the
    close is done.
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

    def __setattr__(self, name, value):
        if name.startswith("_"):
            # The handle's own state only, as on any object.
            object.__setattr__(self, name, value)
            return
        if object.__getattribute__(self, "_returned"):
            raise psycopg2.InterfaceError("connection already closed")
        setattr(object.__getattribute__(self, "_conn"), name, value)

    def close(self) -> None:
        if object.__getattribute__(self, "_returned"):
            return
        object.__setattr__(self, "_returned", True)
        _pool.return_(
            object.__getattribute__(self, "_conn"),
            object.__getattribute__(self, "_factory"),
        )
        object.__setattr__(self, "_conn", None)
        object.__setattr__(self, "_factory", None)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if object.__getattribute__(self, "_returned"):
            return False
        conn = object.__getattribute__(self, "_conn")
        if exc_type is None:
            conn.commit()
        else:
            conn.rollback()
        return False

    def __del__(self) -> None:
        # The collector may run this on any thread — one holding the
        # pool's own lock among them — so it waits on no lock: the
        # connection is handed to the pool's reaper, which settles
        # it off the request paths, instead of being returned here.
        try:
            if not object.__getattribute__(self, "_returned"):
                object.__setattr__(self, "_returned", True)
                conn = object.__getattribute__(self, "_conn")
                factory = object.__getattribute__(self, "_factory")
                if conn is not None:
                    object.__setattr__(self, "_conn", None)
                    object.__setattr__(self, "_factory", None)
                    _pool.finalize(conn, factory)
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

    When the cursor cannot be opened, the caller gets the error that
    stopped it — never one raised by the clean-up — and, outside a
    block, the connection is closed, or given back to the pool,
    before that error reaches the caller; inside a block the block's
    connection stays the block's, as above.

    Inside the app's request handling the connection it opens — its
    own, or the block's — comes from the process-wide pool and is
    given back at the end, reset, for the next user.
    """
    conn = _block_connection.get()
    if conn is None:
        # Outside any block: its own connection, opened and closed here.
        conn = _pool.checkout() if _pool_active() else get_connection()
        cur = None
        try:
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            yield cur
            conn.commit()
        except Exception:
            with suppress(Exception):
                conn.rollback()
            raise
        finally:
            if cur is not None:
                with suppress(Exception):
                    cur.close()
            _close_quietly(conn)
        return
    if conn is _UNOPENED or conn.closed:
        # The block's first use — or its connection was dropped: open
        # the one connection and keep it for the rest of the block.
        conn = _pool.checkout() if _pool_active() else get_connection()
        _block_connection.set(conn)
    cur = None
    try:
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        yield cur
        conn.commit()
    except Exception:
        with suppress(Exception):
            conn.rollback()
        raise
    finally:
        if cur is not None:
            with suppress(Exception):
                cur.close()
