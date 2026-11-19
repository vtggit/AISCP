"""PostgreSQL connection helpers.

``get_cursor()`` opens a connection, yields a server-side cursor on
it and closes both at the end — committing when the body finishes and
rolling back when it raises.  ``one_connection()`` is the block in
which ``get_cursor()`` shares one connection instead: while the block
is active — held in a ``contextvars.ContextVar`` — every
``get_cursor()`` of the block uses the one connection, opened with
``get_connection()`` at the block's first use and reused for every
later ``get_cursor()`` there.  Each ``get_cursor()`` still commits
when its body finishes and rolls back when it raises, as today, and
its cursor is closed at its end; the connection is closed only when
the outermost block exits, an exception included.  A nested block
shares the outer block's connection, and a dropped connection — one
whose ``closed`` attribute is non-zero — is replaced by a new one at
the next ``get_cursor()``.  Outside any block, ``get_cursor()`` opens
and closes its own connection, exactly as without the block.
``get_connection()`` is unchanged everywhere: code that calls it
directly always gets a new connection of its own.
"""

import contextvars
import os
from contextlib import contextmanager

import psycopg2
import psycopg2.extras

from app.config import DB_HOST, DB_NAME, DB_PASSWORD, DB_PORT, DB_USER


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


def get_connection():
    """Return a new psycopg2 connection to the PostgreSQL database."""
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
    one connection — opened with ``get_connection()`` at the block's
    first use and reused for every later ``get_cursor()`` there: each
    ``get_cursor()`` still commits when its body finishes and rolls
    back when it raises, and its cursor is closed at its end, but the
    connection itself is closed only when the outermost block exits,
    an exception included.  A nested block shares the outer block's
    connection.  A dropped connection — one whose ``closed``
    attribute is non-zero — is replaced by a new one at the next
    ``get_cursor()``.  Outside any block, ``get_cursor()`` opens and
    closes its own connection, exactly as without this module.
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

    Outside any ``one_connection()`` block it opens a connection of its
    own and closes it at the end, committing when the body finishes and
    rolling back when it raises.  Inside a block it uses the block's one
    connection for the whole block — opened with ``get_connection()`` at
    the block's first use, or replaced by a new one when the block's
    connection is dropped — committing when the body finishes, rolling
    back when it raises, and closing its cursor at its end; the
    connection is closed when the outermost block exits, an exception
    included.
    """
    conn = _block_connection.get()
    if conn is None:
        # Outside any block: its own connection, opened and closed here.
        conn = get_connection()
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
        conn = get_connection()
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
