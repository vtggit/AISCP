"""Per-item-site PostgreSQL advisory lock for draft-PO confirms.

The confirm path of ``POST /api/replenishment/actions/draft-po``
serializes on this session-level advisory lock in addition to the
route's in-process per-pair lock, so the "one confirm at a time per
item-site" guarantee holds across several API worker processes, not
just one: every confirm takes the lock on its own connection for the
duration of the Odoo create call, and a confirm that cannot take the
lock within ``DRAFT_PO_LOCK_TIMEOUT_MS`` milliseconds is refused by
the route with a 409 — the Odoo create is never called and no
``draft_po_actions`` audit row is written.

The second key is derived from the translated (Odoo) item and site
codes — the pair the create call is made with — as the first four
bytes of the SHA-256 of their NUL-joined encoding, read as a big-endian
signed 32-bit integer: identical in every process and interpreter,
always within PostgreSQL's ``int4`` range, and different for
different pairs in practice.  The lock is held on a dedicated
connection for the duration of the ``with`` block and released (best
effort) with it; an unlock failure never replaces the caller's
exception, and the session end would release the lock with the
connection either way.
"""

import hashlib
import struct
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg2
import psycopg2.errors

from app.db.connection import get_connection

# The first of the two integer keys of the draft-PO advisory locks;
# the second is ``draft_po_lock_key(item_code, site_code)``.
DRAFT_PO_LOCK_CLASS = 446

# How long (in milliseconds) a confirm waits for the per-item-site
# lock before the route refuses it with a 409.
DRAFT_PO_LOCK_TIMEOUT_MS = 60000


def draft_po_lock_key(item_code: str, site_code: str) -> int:
    """The second advisory-lock key for one (item, site) pair.

    The first four bytes of ``sha256(item_code + "\\x00" + site_code)``,
    read as a big-endian signed 32-bit integer — the same value in
    every process and interpreter.
    """
    digest = hashlib.sha256((item_code + "\x00" + site_code).encode()).digest()
    return struct.unpack(">i", digest[:4])[0]


class DraftPoLockTimeout(Exception):
    """The per-item-site advisory lock was not obtained in time."""


@contextmanager
def hold_draft_po_lock(item_code: str, site_code: str) -> Iterator[None]:
    """Hold the per-item-site advisory lock for the duration of the block.

    On its own connection from :func:`app.db.connection.get_connection`
    sets the session's ``lock_timeout`` to
    ``DRAFT_PO_LOCK_TIMEOUT_MS`` milliseconds and runs the blocking
    ``SELECT pg_advisory_lock(DRAFT_PO_LOCK_CLASS, key)``.  When the
    lock is not obtained in time :class:`DraftPoLockTimeout` is
    raised; whenever the acquisition fails in any way — a rejected
    ``SET lock_timeout`` included — the connection is closed before
    the exception leaves the context manager, so no acquisition
    failure leaks a connection.  On exit — including on an
    exception — runs ``SELECT pg_advisory_unlock`` with the same key
    pair and closes the connection; an unlock failure never replaces
    the caller's exception.
    """
    key = draft_po_lock_key(item_code, site_code)
    conn = get_connection()
    acquired = False
    try:
        with conn.cursor() as cur:
            cur.execute("SET lock_timeout = %s", (DRAFT_PO_LOCK_TIMEOUT_MS,))
            cur.execute(
                "SELECT pg_advisory_lock(%s, %s)",
                (DRAFT_PO_LOCK_CLASS, key),
            )
        acquired = True
    except psycopg2.errors.LockNotAvailable:
        raise DraftPoLockTimeout(
            "could not take the draft-PO advisory lock for item-site "
            f"({item_code!r}, {site_code!r}) within "
            f"{DRAFT_PO_LOCK_TIMEOUT_MS} ms"
        ) from None
    finally:
        if not acquired:
            conn.close()
    try:
        yield
    finally:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_advisory_unlock(%s, %s)",
                    (DRAFT_PO_LOCK_CLASS, key),
                )
        except Exception:
            pass
        finally:
            conn.close()
