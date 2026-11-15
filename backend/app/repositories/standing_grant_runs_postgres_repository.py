"""PostgreSQL repository for standing_grant_runs — the run audit.

Every standing-grant run that finds a grant in force writes exactly
one row — the run's outcome (``released``, ``nothing_due``,
``skipped`` or ``failed``), its reason, the count and the summed
value of the groups it sent and the groups its limits left out —
stamped with the aware UTC time the run was started.  Rows are never
changed or deleted: the audit shows every run, the day's total is
the summed ``value`` of one process's runs on one UTC date, and a
failed run's attempted value still counts toward it.

``list`` is the route's read: the stored runs ordered by
``started_at`` descending (the greater ``id`` breaking a tie of two
runs stamped for the same instant), at most ``limit`` of them,
narrowed to one process when ``process`` is given — read-only, it
writes nothing.

The runs additionally serialize on a session-level PostgreSQL
advisory lock (``pg_try_advisory_lock`` on the fixed key
``GRANT_RUN_LOCK_KEY``) taken on its own connection and held for the
whole run — so only one run of a process runs at a time across
several API worker processes, and a run that cannot take the lock
answers ``{"outcome": "busy"}`` and records nothing.  The key is the
run's own, never the release confirm's ``RELEASE_LOCK_KEY``: a
person's confirm and the grant's run never wait on each other's
lock.
"""

import json
from datetime import datetime
from decimal import Decimal

from app.db.connection import get_connection, get_cursor

# The fixed advisory-lock key the standing-grant runs serialize on —
# the run's own key, other than the release confirm's
# ``RELEASE_LOCK_KEY``.
GRANT_RUN_LOCK_KEY = 540


def _json_default(obj: object) -> str:
    """The exact decimal spelling of a Decimal in the left-out list.

    The left-out entries' values are decimal strings — never
    floating-point numbers — and any other non-serializable shape
    fails the insert instead of being silently re-shaped.
    """
    if isinstance(obj, Decimal):
        return str(obj)
    raise TypeError(f"not JSON serializable: {type(obj).__name__}")


def acquire_grant_run_lock():
    """Try to take the run advisory lock on its own connection.

    Runs ``pg_try_advisory_lock(GRANT_RUN_LOCK_KEY)`` on a fresh
    connection and returns that connection — the lock is held by the
    session until the connection is closed or unlocked, so the caller
    must hold it for the whole run and pass it back to
    :func:`release_grant_run_lock` in a ``finally``.  Returns
    ``None`` when another session already holds the lock; nothing is
    acquired.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (GRANT_RUN_LOCK_KEY,))
            acquired = bool(cur.fetchone()[0])
        if not acquired:
            conn.close()
            return None
        return conn
    except Exception:
        conn.close()
        raise


def release_grant_run_lock(conn) -> None:
    """Unlock the run advisory lock and close its connection.

    Best effort: an unlock failure never replaces the caller's
    exception, and the connection is closed either way — the session
    end would release the lock with it.
    """
    if conn is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (GRANT_RUN_LOCK_KEY,))
            conn.commit()
    except Exception:
        pass
    finally:
        conn.close()


def _stored_value(value: object) -> float:
    """The left-out entry's value: the number its stored decimal
    string reads as.

    The entry holds the value as a decimal string (never a
    floating-point number), and a bare number — the int or float a
    hand-written row would hold — reads as the same number.
    """
    return float(Decimal(value))


def _run_to_dict(row) -> dict:
    """One stored run row as the route serves it.

    ``started_at`` the ISO-8601 string with its UTC offset,
    ``value`` the number the NUMERIC column holds and ``left_out``
    the stored entries in their stored order, each entry's value the
    number its stored decimal string reads as — the empty list when
    the row stored a null ``left_out``, no left-out groups.
    """
    d = dict(row)
    if isinstance(d.get("started_at"), datetime):
        d["started_at"] = d["started_at"].isoformat()
    if d.get("value") is not None:
        d["value"] = float(d["value"])
    left_out = d.get("left_out")
    if left_out is None:
        d["left_out"] = []
    elif isinstance(left_out, list):
        d["left_out"] = [
            (
                {**entry, "value": _stored_value(entry.get("value"))}
                if isinstance(entry, dict)
                else entry
            )
            for entry in left_out
        ]
    return d


class StandingGrantRunPostgresRepository:
    """PostgreSQL repository for the standing_grant_runs table."""

    def record(
        self,
        *,
        process: str,
        grant_id: int,
        started_at: datetime,
        outcome: str,
        reason: str | None,
        documents: int,
        value: Decimal,
        left_out: list[dict],
    ) -> dict:
        """Write one run row and return it, every column of it.

        ``started_at`` is the aware UTC time the run was started (the
        column's default only serves a caller that stamps nothing),
        ``documents`` and ``value`` the count and the summed value of
        the groups sent — 0 and 0 while none were — and ``left_out``
        the groups the limits left out, as their exact entries.
        ``value`` comes back as the exact Decimal the NUMERIC column
        holds, ``started_at`` as an aware UTC datetime.
        """
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO standing_grant_runs "
                "(process, grant_id, started_at, outcome, reason, "
                "documents, value, left_out) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (
                    process,
                    grant_id,
                    started_at,
                    outcome,
                    reason,
                    documents,
                    value,
                    json.dumps(left_out, default=_json_default),
                ),
            )
            return dict(cur.fetchone())

    def list(self, limit: int, process: str | None = None) -> list[dict]:
        """The stored runs, newest first, at most ``limit`` of them.

        Ordered by ``started_at`` descending, the greater ``id``
        breaking a tie of two runs stamped for the same instant;
        ``process`` narrows the answer to one process's runs when
        given, every other run left out.  Read-only: the route serves
        the audit and writes nothing.  ``started_at`` comes back as
        the ISO-8601 string with its UTC offset, ``value`` the number
        the NUMERIC column holds and ``left_out`` the stored entries
        in their stored order, each entry's value the number its
        stored decimal string reads as.
        """
        with get_cursor() as cur:
            if process is None:
                cur.execute(
                    "SELECT * FROM standing_grant_runs "
                    "ORDER BY started_at DESC, id DESC LIMIT %s",
                    (limit,),
                )
            else:
                cur.execute(
                    "SELECT * FROM standing_grant_runs "
                    "WHERE process = %s "
                    "ORDER BY started_at DESC, id DESC LIMIT %s",
                    (process, limit),
                )
            rows = cur.fetchall()
        return [_run_to_dict(row) for row in rows]

    def sent_value_on(
        self, process: str, day_start: datetime, day_end: datetime
    ) -> Decimal:
        """The summed ``value`` of one process's runs on one UTC date.

        ``day_start`` and ``day_end`` bound the date — both aware UTC
        instants, the date's midnight and the next — so the day is the
        UTC calendar date whatever the session's timezone.  Every
        run row of the process in the bounds counts, a failed run's
        attempted value among them; 0 while none was.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(value), 0) AS total "
                "FROM standing_grant_runs "
                "WHERE process = %s AND started_at >= %s AND started_at < %s",
                (process, day_start, day_end),
            )
            return Decimal(cur.fetchone()["total"])

    def latest_for(self, process: str) -> dict | None:
        """The process's latest run row, or None while it has none.

        The latest is the greatest ``started_at``, the greatest
        ``id`` breaking a tie of two runs stamped for the same
        instant — the exceptions list reads it once per in-force
        grant.  Every column comes back as the table holds it:
        ``started_at`` an aware UTC datetime, ``value`` the exact
        Decimal and ``left_out`` the stored list.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM standing_grant_runs "
                "WHERE process = %s "
                "ORDER BY started_at DESC, id DESC LIMIT 1",
                (process,),
            )
            row = cur.fetchone()
        return None if row is None else dict(row)
