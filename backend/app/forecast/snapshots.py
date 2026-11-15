"""Capture and list forecast snapshots (issue #331).

``capture_snapshot(as_of=None)`` stores, for one as-of date, exactly
the forecast buckets ``GET /api/forecast`` returns for that date:
every item-site that has a forecast, twelve rows each (one per 30-day
horizon bucket, the first starting the day after the as-of date).
With no ``as_of`` it snapshots the shared default as-of date
(``app.stats.demand.default_as_of`` — the same date the forecast list
resolves) and the rows have origin ``live``; with an ``as_of`` given
they have origin ``backfill``.

Every bucket row also stores the effective forecast beside the
statistical one [rule: OVR-4]: a live capture stores, for each
bucket, the ``effective_quantity``, ``override_days`` and
``signal_quantity`` the forecast list shows for it at capture time
— ``signal_quantity`` the counted signal quantity of the bucket,
the same value the forecast list adds to the bucket's
``effective_quantity`` — and a backfill stores
``effective_quantity`` equal to ``quantity``, ``override_days`` 0 and
``signal_quantity`` 0 (a hindcast has no overrides and no signals)
— the statistical columns (``quantity``, ``method``,
``demand_class``, ``history_buckets``) are exactly the ones the
capture stored before the columns existed.  A signal withdrawn after
a capture leaves the stored rows as they were: they record what was
believed on the as-of date.  A stored row whose ``effective_quantity``
is null — a row written before migration 0015 — is read with
``effective_quantity`` equal to its ``quantity`` and ``override_days``
0, by ``stored_snapshot_rows``; a stored row whose ``signal_quantity``
is null — a row written before migration 0038 — is a row the
capture stored before the column existed.

One snapshot per date: when a snapshot for the as-of date already
exists, nothing is stored and the stored snapshot's fields are
returned with ``created`` false.  The first snapshot of a date is
never updated or deleted, whatever its origin; the database rejects
any later insert for the same date (the ``forecast_snapshots_guard``
trigger — rows written by an earlier, different transaction are the
marker of the first snapshot, while a snapshot's own batch insert
shares the inserting transaction's xid and never collides with
itself), and a snapshot's rows are stored together or not at all —
one transaction, committed once.

Each snapshot also stores its snapshot-level record in
``forecast_snapshot_records`` — one row per as-of date carrying
``origin``, ``item_sites``, ``rows`` and ``created_at`` — in the same
transaction as the bucket rows.  The list and the already-exists
result read their snapshot-level fields from that stored record, not
from the bucket rows; the record's ``created_at`` is the database's
``now()`` at insert time (its server default), while the bucket rows
carry the application's timestamp.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import psycopg2
import psycopg2.errors
import psycopg2.extras

from app.db.connection import get_connection, get_cursor
from app.stats.demand import default_as_of

HORIZON_BUCKETS = 12

_RECORDS_SELECT_SQL = (
    "SELECT as_of, origin, item_sites, rows, created_at "
    "FROM forecast_snapshot_records WHERE as_of = %s"
)

_BUCKETS_PRESENT_SQL = (
    "SELECT 1 AS present FROM forecast_snapshots WHERE as_of = %s LIMIT 1"
)

_LIST_SQL = (
    "SELECT as_of, origin, item_sites, rows, created_at "
    "FROM forecast_snapshot_records "
    "ORDER BY as_of DESC LIMIT %s OFFSET %s"
)


def _stored_fields(cur, as_of: date, record: dict | None) -> dict:
    """The stored snapshot's result fields, from its stored record.

    The record row is written in the same transaction as the
    snapshot's bucket rows, so it is the snapshot-level source of
    truth; ``record`` is None only when bucket rows exist without a
    record — a state the API itself can never produce (a capture
    writes both together), reachable only through out-of-band
    inserts, where the rows are the best available description.
    """
    if record is not None:
        return {
            "as_of": as_of,
            "origin": record["origin"],
            "item_sites": int(record["item_sites"]),
            "rows": int(record["rows"]),
        }
    cur.execute(
        "SELECT MIN(origin) AS origin, COUNT(*) AS rows, "
        "COUNT(DISTINCT (item_code, site_code)) AS item_sites "
        "FROM forecast_snapshots WHERE as_of = %s",
        (as_of,),
    )
    stored = cur.fetchone()
    return {
        "as_of": as_of,
        "origin": stored["origin"],
        "item_sites": int(stored["item_sites"]),
        "rows": int(stored["rows"]),
    }


def _snapshot_rows_for(as_of: date, apply_overrides: bool) -> list[dict]:
    """The per-bucket rows a capture stores for ``as_of``.

    Built from ``app.forecast.listing.forecast_rows`` — the exact
    rows ``GET /api/forecast`` returns for the date, on a fixed number
    of queries, not one history query per item-site: one row per
    item-site that has a forecast (``no_demand`` item-sites are left
    out), twelve buckets each, with the same boundaries, statistical
    quantities, method, demand class and history bucket count.  With
    ``apply_overrides`` true (a live capture) each bucket stores the
    ``effective_quantity``, ``override_days`` and ``signal_quantity``
    the forecast list shows for it at capture time [rule: OVR-3]
    [rule: OVR-4] — read from the same ``forecast_rows`` call, no
    new query; with it false (a backfill — a hindcast has no
    overrides and no signals) the bucket stores ``effective_quantity``
    equal to its ``quantity``, ``override_days`` 0 and
    ``signal_quantity`` 0.
    """
    # Imported here, not at module level: app.forecast.listing imports
    # HORIZON_BUCKETS from this module.
    from app.forecast.listing import forecast_rows

    snapshot_rows: list[dict] = []
    for row in forecast_rows(as_of):
        for bucket_index, bucket in enumerate(row["forecast"], start=1):
            snapshot_rows.append(
                {
                    "item_code": row["item_code"],
                    "site_code": row["site_code"],
                    "bucket_index": bucket_index,
                    "bucket_start": bucket["bucket_start"],
                    "bucket_end": bucket["bucket_end"],
                    "quantity": bucket["quantity"],
                    "effective_quantity": (
                        bucket["quantity"]
                        if not apply_overrides
                        else bucket["effective_quantity"]
                    ),
                    "override_days": (
                        0 if not apply_overrides else bucket["override_days"]
                    ),
                    "signal_quantity": (
                        0 if not apply_overrides else bucket["signal_quantity"]
                    ),
                    "method": row["method"],
                    "demand_class": row["demand_class"],
                    "history_buckets": row["history_buckets"],
                }
            )
    return snapshot_rows


def capture_snapshot(as_of: date | None = None) -> dict:
    """Store (or return) the forecast snapshot for one as-of date.

    With no ``as_of`` the shared default as-of date is used and the
    rows have origin ``live``; with an ``as_of`` given they have
    origin ``backfill``, and their buckets store ``effective_quantity``
    equal to ``quantity`` and ``override_days`` 0 (a hindcast has no
    overrides), while a live capture stores the effective forecast
    the forecast list shows at capture time [rule: OVR-4].  Returns
    ``{"as_of", "origin", "created", "item_sites", "rows"}`` — exactly
    those keys, with no ``created_at``: ``created`` is true only when
    rows were stored —
    false both for a date that already has a snapshot (where
    ``origin``, ``item_sites`` and ``rows`` describe the stored
    snapshot, read from its stored record) and for a date whose
    forecast is empty (nothing is stored and no snapshot is
    recorded, so a later capture of the date can still be the first
    to store rows).
    """
    origin = "backfill" if as_of is not None else "live"
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if as_of is None:
                as_of = default_as_of(cur)
            # Serialize concurrent captures for this date: a second
            # capture waits here until the first commits, then finds
            # its rows and returns them instead of inserting again.
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))", (as_of.isoformat(),)
            )
            cur.execute(_RECORDS_SELECT_SQL, (as_of,))
            record = cur.fetchone()
            if record is None:
                cur.execute(_BUCKETS_PRESENT_SQL, (as_of,))
                already_snapshotted = cur.fetchone() is not None
            else:
                already_snapshotted = True
            if already_snapshotted:
                return {**_stored_fields(cur, as_of, record), "created": False}

            snapshot_rows = _snapshot_rows_for(as_of, apply_overrides=origin == "live")
            item_sites = len(
                {(row["item_code"], row["site_code"]) for row in snapshot_rows}
            )
            if snapshot_rows:
                created_at = datetime.now(UTC)
                # The record's created_at is the database's now() at
                # insert time (its server default); the bucket rows
                # carry the application's timestamp. The record — not
                # the rows — is the snapshot-level source of truth.
                cur.execute(
                    "INSERT INTO forecast_snapshot_records "
                    "(as_of, origin, item_sites, rows) "
                    "VALUES (%s, %s, %s, %s)",
                    (as_of, origin, item_sites, len(snapshot_rows)),
                )
                cur.executemany(
                    "INSERT INTO forecast_snapshots "
                    "(id, as_of, origin, item_code, site_code, bucket_index, "
                    "bucket_start, bucket_end, quantity, effective_quantity, "
                    "override_days, signal_quantity, method, demand_class, "
                    "history_buckets, created_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                    "%s, %s, %s, %s)",
                    [
                        (
                            str(uuid4()),
                            as_of,
                            origin,
                            row["item_code"],
                            row["site_code"],
                            row["bucket_index"],
                            row["bucket_start"],
                            row["bucket_end"],
                            row["quantity"],
                            row["effective_quantity"],
                            row["override_days"],
                            row["signal_quantity"],
                            row["method"],
                            row["demand_class"],
                            row["history_buckets"],
                            created_at,
                        )
                        for row in snapshot_rows
                    ],
                )
            conn.commit()
            return {
                "as_of": as_of,
                "origin": origin,
                # False when nothing was stored: an empty forecast
                # leaves no snapshot behind (rows 0, item_sites 0).
                "created": bool(snapshot_rows),
                "item_sites": item_sites,
                "rows": len(snapshot_rows),
            }
    except psycopg2.errors.UniqueViolation:
        # A concurrent capture committed first (or a direct insert
        # raced past the check); hand back the stored snapshot
        # instead of failing — the date keeps exactly one snapshot.
        conn.rollback()
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(_RECORDS_SELECT_SQL, (as_of,))
            record = cur.fetchone()
            if record is None:
                cur.execute(_BUCKETS_PRESENT_SQL, (as_of,))
                if cur.fetchone() is None:
                    raise
            return {**_stored_fields(cur, as_of, record), "created": False}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_snapshots(limit: int = 50, offset: int = 0) -> list[dict]:
    """The stored snapshots, newest as-of date first.

    Each entry carries the snapshot-level fields ``as_of``,
    ``origin``, ``item_sites``, ``rows`` and ``created_at`` — read
    from the stored snapshot record, not derived from the bucket
    rows.  ``limit`` (1 to 500) and ``offset`` page the list.
    """
    with get_cursor() as cur:
        cur.execute(_LIST_SQL, (limit, offset))
        return [
            {
                "as_of": row["as_of"],
                "origin": row["origin"],
                "item_sites": int(row["item_sites"]),
                "rows": int(row["rows"]),
                "created_at": row["created_at"],
            }
            for row in cur.fetchall()
        ]


_ROWS_SELECT_SQL = (
    "SELECT id, as_of, origin, item_code, site_code, bucket_index, "
    "bucket_start, bucket_end, quantity, "
    "COALESCE(effective_quantity, quantity) AS effective_quantity, "
    "CASE WHEN effective_quantity IS NULL THEN 0 "
    "ELSE COALESCE(override_days, 0) END AS override_days, "
    "method, demand_class, history_buckets, created_at "
    "FROM forecast_snapshots WHERE as_of = %s "
    "ORDER BY item_code, site_code, bucket_index"
)


def stored_snapshot_rows(as_of: date) -> list[dict]:
    """The stored snapshot rows for one as-of date.

    Every stored column of each bucket row, ordered by ``item_code``,
    ``site_code`` and then ``bucket_index``.  A row whose
    ``effective_quantity`` is null — stored before migration 0015,
    with both effective columns null — is read with
    ``effective_quantity`` equal to its ``quantity`` and
    ``override_days`` 0, whatever ``override_days`` it stores: the
    statistical forecast a hindcast stores, and what no override
    covered.
    """
    with get_cursor() as cur:
        cur.execute(_ROWS_SELECT_SQL, (as_of,))
        return [dict(row) for row in cur.fetchall()]
