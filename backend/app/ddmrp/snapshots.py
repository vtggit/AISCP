"""One DDMRP plan status snapshot per capture date.

``capture_ddmrp_snapshot`` stores, in ``ddmrp_status_snapshots``, one
row per plan row of ``GET /api/ddmrp/plan`` — built with the very
same shared code the plan route uses (``ddmrp_zone_rows`` over
``plan_ddmrp``), as of the plan's own as-of date (the demand
statistics' default as-of date) when the caller names no ``as_of``
date and as of the named date otherwise.  Each stored row carries the
pair, the plan row's ``planning_zone`` ("red", "yellow", "green" or
"over") and ``on_hand_status`` ("stocked_out", "critical", "low" or
"ok"), and the plan's ``net_flow``, ``on_hand`` and the three zone
tops.

A capture replaces the captured date's rows wholesale: the stored
rows of the date are removed and one row per plan row is written, so
a buffer absent from the plan on that date is removed from the
snapshot, a second capture of the same date is idempotent, and a date
never holds two rows of one pair (the (``snapshot_on``,
``item_code``, ``site_code``) primary key).  It returns
``{"snapshot_on", "rows"}`` and writes nothing else.
"""

from __future__ import annotations

from datetime import date

from app.api.ddmrp import ddmrp_zone_rows
from app.db.connection import get_cursor
from app.ddmrp.plan import plan_ddmrp

_SNAPSHOT_SQL = (
    "INSERT INTO ddmrp_status_snapshots "
    "(snapshot_on, item_code, site_code, planning_zone, on_hand_status, "
    "net_flow, on_hand, top_of_red, top_of_yellow, top_of_green) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def capture_ddmrp_snapshot(as_of: date | None = None) -> dict:
    """Store one DDMRP plan status snapshot.

    Builds the plan rows with the same shared code
    ``GET /api/ddmrp/plan`` uses (``ddmrp_zone_rows`` over
    ``plan_ddmrp``), as of the plan's own as-of date when ``as_of`` is
    None and as of the named date otherwise, and replaces every
    ``ddmrp_status_snapshots`` row of that date with one row per plan
    row: the pair, the plan's ``planning_zone`` and ``on_hand_status``
    and the plan's ``net_flow``, ``on_hand`` and zone tops.  A buffer
    absent from the plan on that date is removed from the snapshot, a
    second capture of the same date is idempotent, and a date never
    holds two rows of one pair.  Returns ``{"snapshot_on", "rows"}``
    (``rows`` the stored rows, one per plan row) and writes nothing
    else.
    """
    snapshot_on, zone_rows = ddmrp_zone_rows(as_of)
    plan = plan_ddmrp(snapshot_on, zone_rows)
    rows = [
        {
            "snapshot_on": snapshot_on,
            "item_code": row["item_code"],
            "site_code": row["site_code"],
            "planning_zone": row["planning_zone"],
            "on_hand_status": row["on_hand_status"],
            "net_flow": float(row["net_flow"]),
            "on_hand": float(row["on_hand"]),
            "top_of_red": float(row["top_of_red"]),
            "top_of_yellow": float(row["top_of_yellow"]),
            "top_of_green": float(row["top_of_green"]),
        }
        for row in plan["rows"]
    ]

    with get_cursor() as cur:
        # Replace the date's rows wholesale: the plan's pairs are the
        # snapshot's pairs, so a pair that fell out of the plan (or
        # left the designation) is removed with the date's old rows.
        cur.execute(
            "DELETE FROM ddmrp_status_snapshots WHERE snapshot_on = %s",
            (snapshot_on,),
        )
        for row in rows:
            cur.execute(
                _SNAPSHOT_SQL,
                (
                    row["snapshot_on"],
                    row["item_code"],
                    row["site_code"],
                    row["planning_zone"],
                    row["on_hand_status"],
                    row["net_flow"],
                    row["on_hand"],
                    row["top_of_red"],
                    row["top_of_yellow"],
                    row["top_of_green"],
                ),
            )

    return {"snapshot_on": snapshot_on, "rows": rows}
