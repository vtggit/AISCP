"""One on-hand snapshot per capture date.

``capture_on_hand_snapshot()`` stores, in ``on_hand_snapshots``, every
row of ``on_hand`` under one ``snapshot_on`` date: the UTC date of the
latest ``as_of`` among the ``on_hand`` rows (never the server's
clock).  Each stored row carries the pair (``item_code``,
``site_code``), the row's ``quantity_on_hand``, its ``source_system``
(null when the ERP reported none) and the row's own ``as_of``.

A capture replaces the captured date's rows wholesale: the date's
stored rows are deleted first and the current ``on_hand`` rows are
written after, in one transaction.  A second capture of the same date
is therefore idempotent, and a pair no longer in ``on_hand`` leaves
the date's snapshot — the other dates' rows are never touched, and
nothing else is written.

It is not a route, a maintenance entry point.  It answers
``{"snapshot_on", "rows"}`` — the captured date as an ISO string and
the number of rows stored — or, with no ``on_hand`` row,
``{"snapshot_on": None, "rows": 0}`` without writing anything.

The capture date and every row's ``as_of`` are computed in SQL and
carried as database text — never through a Python date or datetime —
so an ``as_of`` outside Python's date range (Postgres accepts
timestamptz values far beyond the year 9999, and as far back as 4713
BC) can never crash the capture.
"""

from __future__ import annotations

from app.db.connection import get_cursor

_SNAPSHOT_INSERT = (
    "INSERT INTO on_hand_snapshots "
    "(snapshot_on, item_code, site_code, quantity_on_hand, as_of, "
    "source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s)"
)


def capture_on_hand_snapshot() -> dict:
    """Store one on-hand snapshot under the latest as-of's UTC date.

    Reads every ``on_hand`` row and stores it in
    ``on_hand_snapshots`` under ``snapshot_on`` — the UTC date of the
    latest ``as_of`` among the ``on_hand`` rows — with the row's
    ``item_code``, ``site_code``, ``quantity_on_hand``,
    ``source_system`` and own ``as_of``.  The date's stored rows are
    deleted first, so a second capture of the same date is idempotent
    and a pair no longer in ``on_hand`` leaves the date's snapshot;
    rows of every other date are never touched.  Returns
    ``{"snapshot_on", "rows"}`` — the captured date as an ISO string
    and the number of rows stored — or ``{"snapshot_on": None,
    "rows": 0}`` with no ``on_hand`` row, in which case nothing is
    written.
    """
    with get_cursor() as cur:
        # The capture date: the UTC date of the latest as_of, taken in
        # the database so the server's session timezone cannot shift
        # it, and kept as the database's own text so a value outside
        # Python's date range never reaches a driver converter.
        # MAX over an empty table is null.
        cur.execute(
            "SELECT (MAX(as_of) AT TIME ZONE 'UTC')::date::text AS latest "
            "FROM on_hand"
        )
        latest = cur.fetchone()["latest"]
        if latest is None:
            return {"snapshot_on": None, "rows": 0}
        snapshot_on = latest

        # Replace the date's rows wholesale, in this one transaction:
        # the old rows of the date are gone and every current on_hand
        # row is stored under the date, so the capture is idempotent
        # and no other date is ever touched.
        cur.execute(
            "SELECT item_code, site_code, quantity_on_hand, "
            "as_of::text AS as_of, source_system "
            "FROM on_hand ORDER BY item_code, site_code"
        )
        rows = cur.fetchall()
        cur.execute(
            "DELETE FROM on_hand_snapshots WHERE snapshot_on = %s::date",
            (snapshot_on,),
        )
        for row in rows:
            cur.execute(
                _SNAPSHOT_INSERT,
                (
                    snapshot_on,
                    row["item_code"],
                    row["site_code"],
                    row["quantity_on_hand"],
                    row["as_of"],
                    row["source_system"],
                ),
            )

    return {"snapshot_on": snapshot_on, "rows": len(rows)}
