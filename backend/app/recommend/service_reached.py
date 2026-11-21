"""The service levels actually reached, per ABC class, over a window.

``service_reached(days)`` answers, for the trailing window of ``days``
snapshot dates, how much of the counted item-sites' snapshot days
their class spent in stock.  It writes nothing and calls no ERP.

The window's end ``as_of`` is the latest ``snapshot_on`` in
``on_hand_snapshots``, null when the table is empty; the window's
start ``window_start`` is ``as_of`` minus ``days - 1`` days, null
when ``as_of`` is null, and the window runs from ``window_start`` to
``as_of``, both included.  The dates are worked out in SQL and
answered as ``YYYY-MM-DD`` database text, the way
``app.supply.on_hand_snapshots.capture_on_hand_snapshot`` carries
them, so a snapshot date outside Python's date range cannot make the
measure fail.

An item-site — one (``item_code``, ``site_code``) pair — counts when
it has at least one ``demand_events`` row whose ``occurred_on`` is in
the window and at least one ``on_hand_snapshots`` row in the window;
its class is its ``abc_class`` in the answer of
``app.recommend.abc.abc_for_all``, read at most once per call.  The
``service_target`` and the classes' current levels come from the
answer of ``app.recommend.service_curve._stored_answer``, read once
per call: each class' ``level`` is its entry in
``current_levels``, the level the service curve marks as current,
null when no current level is stored for the class.

The answer is ``{as_of, days, window_start, snapshot_dates,
service_target, classes, unclassified_item_sites}``: ``days`` is the
window's length as asked; ``snapshot_dates`` is the number of
distinct ``snapshot_on`` dates in the window, 0 when ``as_of`` is
null, so a missed day shows as fewer dates than ``days``; ``classes``
holds exactly three entries, for A, B and C in that order, each
``{abc_class, level, item_sites, item_site_days, days_in_stock,
share_in_stock}`` — ``item_sites`` the counted item-sites of the
class, ``item_site_days`` their ``on_hand_snapshots`` rows in the
window, one per item-site and date, ``days_in_stock`` the rows of
those whose ``quantity_on_hand`` is above 0 and ``share_in_stock``
``days_in_stock / item_site_days`` rounded to 4 places, null when no
item-site day counts for the class; ``unclassified_item_sites`` the
counted item-sites whose class is null or that ``abc_for_all`` does
not list.

It is not a fill rate: it counts only the demand the ERP recorded in
``demand_events`` — demand it never recorded, such as a sale lost
while out of stock, is not counted.  The class is the item-site's
class today, not the class it carried in the window.  A date with no
``on_hand_snapshots`` row is not counted, and nothing is rebuilt
backwards.
"""

from __future__ import annotations

from app.db.connection import get_cursor
from app.recommend.abc import abc_for_all
from app.recommend.service_curve import _stored_answer

CLASSES = ("A", "B", "C")


def service_reached(days: int = 30) -> dict:
    """The service levels actually reached, per ABC class.

    The measure of the module's docstring for the trailing window of
    ``days`` snapshot dates: the item-sites with a demand event and a
    snapshot in the window, per class, with their snapshot days, the
    days of those in stock and the share of them, beside the class'
    current service level and the stored ``service_target``.  It
    writes nothing: the two settings-side sources are
    ``app.recommend.abc.abc_for_all`` (at most once per call) and
    ``app.recommend.service_curve._stored_answer`` (once per call),
    both imported by name so a test can replace them here.
    """
    with get_cursor() as cur:
        # The window's end: the latest snapshot date, taken in the
        # database and kept as its own text so a date outside
        # Python's range never reaches a driver converter; MAX over
        # an empty table is null.
        cur.execute("SELECT MAX(snapshot_on)::text AS as_of FROM on_hand_snapshots")
        as_of = cur.fetchone()["as_of"]
        if as_of is None:
            window_start = None
            snapshot_dates = 0
            pair_rows = []
        else:
            # The window's start, in the database: as_of minus
            # days - 1 days, kept as its own text, so a start before
            # the first AD year answers as Postgres's own date text.
            cur.execute(
                "SELECT (MAX(snapshot_on) - (%s - 1))::text "
                "AS window_start "
                "FROM on_hand_snapshots",
                (days,),
            )
            window_start = cur.fetchone()["window_start"]
            # The distinct snapshot dates of the window, both ends
            # included.
            cur.execute(
                "SELECT COUNT(DISTINCT snapshot_on) AS snapshot_dates "
                "FROM on_hand_snapshots "
                "WHERE snapshot_on BETWEEN %s::date AND %s::date",
                (window_start, as_of),
            )
            snapshot_dates = cur.fetchone()["snapshot_dates"]
            # The counted item-sites — a demand event in the window
            # and a snapshot row in the window — with their snapshot
            # days and the days of those in stock.
            cur.execute(
                "WITH demanded AS ("
                "  SELECT DISTINCT item_code, site_code "
                "  FROM demand_events "
                "  WHERE occurred_on BETWEEN %s::date AND %s::date"
                ") "
                "SELECT d.item_code, d.site_code, "
                "       COUNT(*) AS item_site_days, "
                "       COUNT(*) FILTER (WHERE s.quantity_on_hand > 0) "
                "       AS days_in_stock "
                "FROM demanded d "
                "JOIN on_hand_snapshots s "
                "  ON s.item_code = d.item_code AND s.site_code = d.site_code "
                "  AND s.snapshot_on BETWEEN %s::date AND %s::date "
                "GROUP BY d.item_code, d.site_code",
                (window_start, as_of, window_start, as_of),
            )
            pair_rows = cur.fetchall()

    # The settings read, once per call: the service target and the
    # classes' current levels.
    stored = _stored_answer()
    current_levels = stored.get("current_levels") or {}

    # The classification, at most once per call — skipped when there
    # is no window, so nothing could count: the class of every
    # item-site the answer lists, by (item_code, site_code).
    class_by_pair: dict[tuple[object, object], object] = {}
    if as_of is not None:
        for row in abc_for_all():
            if isinstance(row, dict):
                class_by_pair[(row.get("item_code"), row.get("site_code"))] = row.get(
                    "abc_class"
                )

    totals = {
        cls: {"item_sites": 0, "item_site_days": 0, "days_in_stock": 0}
        for cls in CLASSES
    }
    unclassified_item_sites = 0
    for pair in pair_rows:
        key = (pair["item_code"], pair["site_code"])
        abc_class = class_by_pair.get(key)
        if abc_class in totals:
            bucket = totals[abc_class]
            bucket["item_sites"] += 1
            bucket["item_site_days"] += pair["item_site_days"]
            bucket["days_in_stock"] += pair["days_in_stock"]
        elif abc_class is None or key not in class_by_pair:
            # The class is null, or the classification does not list
            # the item-site at all.
            unclassified_item_sites += 1

    classes = []
    for cls in CLASSES:
        bucket = totals[cls]
        item_site_days = bucket["item_site_days"]
        classes.append(
            {
                "abc_class": cls,
                "level": current_levels.get(cls),
                "item_sites": bucket["item_sites"],
                "item_site_days": item_site_days,
                "days_in_stock": bucket["days_in_stock"],
                "share_in_stock": (
                    round(bucket["days_in_stock"] / item_site_days, 4)
                    if item_site_days
                    else None
                ),
            }
        )

    return {
        "as_of": as_of,
        "days": days,
        "window_start": window_start,
        "snapshot_dates": snapshot_dates,
        "service_target": stored.get("service_target"),
        "classes": classes,
        "unclassified_item_sites": unclassified_item_sites,
    }
