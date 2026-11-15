"""Freshness of the five OData feed tables (read-only).

``feed_status()`` reports, for each of the five feed tables
(:data:`FEED_TABLES`), the newest ``synced_at`` and the row count, plus
the newest ``demand_events.occurred_on`` as the data horizon.  It reads
through two module-level helpers — :func:`_table_stats` and
:func:`_data_through` — the only database seams, both going through
``app.db.connection.get_cursor``.  A table name comes only from the
fixed five-name list, never from input, so the (deliberately
unparameterised) table name in the SQL is out of hostile reach.  It
writes nothing.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.db.connection import get_cursor

#: The five OData feed tables, in the order they are reported.
FEED_TABLES = (
    "item_sites",
    "demand_events",
    "on_hand",
    "open_purchase_lines",
    "lead_time_observations",
)


def _to_utc(value: datetime) -> datetime:
    """An aware UTC copy of ``value``.

    A naive value is pinned to UTC: the feed stores UTC instants, and
    ``astimezone`` on a naive value would presume the server's local
    timezone and shift the instant instead.  An aware value is
    converted to UTC.
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _table_stats(table: str) -> tuple[datetime | None, int]:
    """Newest ``synced_at`` (aware UTC) and row count of one feed table.

    ``table`` must be one of :data:`FEED_TABLES`; anything else raises
    ``ValueError``.  The name never comes from caller input — the fixed
    list is the only source — so this guard is what keeps the
    unparameterised table name out of hostile reach.  The newest
    ``synced_at`` is normalised to an aware UTC datetime, so a naive
    value from the driver (e.g. a ``timestamp without time zone``
    column) is pinned to UTC rather than read as the server's local
    time, and the values stay mutually comparable.
    """
    if table not in FEED_TABLES:
        raise ValueError(f"unknown feed table: {table!r}")
    with get_cursor() as cur:
        cur.execute(
            f"SELECT MAX(synced_at) AS newest_synced_at, COUNT(*) AS row_count "
            f"FROM {table}"
        )
        row = cur.fetchone()
    newest = row["newest_synced_at"]
    if newest is not None:
        newest = _to_utc(newest)
    return (newest, int(row["row_count"]))


def _coerce_date(value: object) -> date | None:
    """``value`` as a ``date`` — the shape ``data_through`` must return.

    A ``datetime`` is truncated to its calendar date, a ``date`` passes
    through unchanged, and an ISO-8601 string (``YYYY-MM-DD`` with an
    optional time part) is parsed.  ``None`` stays ``None``.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value.strip().split("T", 1)[0].split(" ", 1)[0])
    raise TypeError(f"cannot coerce {type(value).__name__} to a date")


def _data_through() -> date | None:
    """Newest ``demand_events.occurred_on`` — how far the feed's data reaches.

    Coerced to a ``date`` (the column is a date, but whatever shape the
    driver hands back — datetime, date, or ISO string — is normalised),
    so the result is always ``YYYY-MM-DD`` or ``None``.
    """
    with get_cursor() as cur:
        cur.execute("SELECT MAX(occurred_on) AS newest_occurred_on FROM demand_events")
        row = cur.fetchone()
    return _coerce_date(row["newest_occurred_on"])


def _iso8601_utc(value: datetime | None) -> str | None:
    """ISO-8601 UTC string for a datetime, or ``None`` for ``None``.

    Naive values are pinned to UTC (see :func:`_to_utc`); aware values
    are converted.
    """
    if value is None:
        return None
    return _to_utc(value).isoformat()


def feed_status() -> dict:
    """Freshness of the five feed tables and the data horizon.

    Returns a dict with:

    * ``tables``: one entry per table, in :data:`FEED_TABLES` order,
      each ``{"name", "synced_at", "rows"}`` with ``synced_at`` an
      ISO-8601 UTC string (``None`` when the table has never synced);
    * ``synced_at``: the newest of the tables' ``synced_at`` values, or
      ``None`` when none has one;
    * ``oldest_synced_at``: the oldest non-null of them, or ``None`` —
      a gap between the two shows a partial sync;
    * ``data_through``: the newest ``demand_events.occurred_on`` as
      ``YYYY-MM-DD``, or ``None``.

    Reads only; it writes nothing.
    """
    stats = {table: _table_stats(table) for table in FEED_TABLES}
    tables = [
        {
            "name": table,
            "synced_at": _iso8601_utc(stats[table][0]),
            "rows": stats[table][1],
        }
        for table in FEED_TABLES
    ]
    known = [newest for newest, _ in stats.values() if newest is not None]
    data_through = _data_through()
    return {
        "tables": tables,
        "synced_at": _iso8601_utc(max(known)) if known else None,
        "oldest_synced_at": _iso8601_utc(min(known)) if known else None,
        "data_through": data_through.isoformat() if data_through is not None else None,
    }
