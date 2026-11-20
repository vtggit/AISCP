"""The data stamp of the reorder recommendations (issue #1463).

``data_stamp()`` answers, with one query over ``data_versions``, the
version of every input table of the reorder recommendations — the
eleven tables in ``INPUT_TABLES``, in that order, as a tuple of
integers: the number of writes the database has counted for each
table since its last reseed.  A table that has no row in
``data_versions`` reads as 0.

The stamp is the freshness token of a computed list of reorder
recommendations: comparing two stamps position by position says
whether any of the tables the recommendations read has moved since
the list was computed, so a caller can tell a stale list from a
current one — and recompute, or keep, without re-reading the tables
that did not move.  ``INPUT_TABLES`` must name every table the
reorder recommendations read, in the order the stamp reports them: a
table the recommendations read that is missing from the tuple would
move without moving any stamp position, and the stamp would stop
telling the truth.
"""

import psycopg2
import psycopg2.sql

from app.db.connection import get_cursor

__all__ = ["INPUT_TABLES", "data_stamp"]

# The eleven input tables the reorder recommendations read, in the
# order the stamp reports them (and the migration seeds them).
INPUT_TABLES = (
    "settings",
    "demand_events",
    "lead_time_observations",
    "forecast_overrides",
    "demand_signals",
    "item_sites",
    "on_hand",
    "open_purchase_lines",
    "plan_releases",
    "transfer_releases",
    "empirical_adoptions",
)


def data_stamp() -> tuple[int, ...]:
    """The version of every input table, in the order of INPUT_TABLES.

    Read in one query over ``data_versions``; a table that has no row
    reads as 0.
    """
    query = psycopg2.sql.SQL(
        "SELECT table_name, version FROM data_versions "
        "WHERE table_name = ANY({tables})"
    ).format(tables=psycopg2.sql.Literal(list(INPUT_TABLES)))
    with get_cursor() as cur:
        cur.execute(query)
        rows = {row["table_name"]: row["version"] for row in cur.fetchall()}
    return tuple(rows.get(table, 0) for table in INPUT_TABLES)
