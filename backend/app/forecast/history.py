"""30-day demand history buckets for one item-site (issue #309).

``history_for_pair`` reads one (item_code, site_code) pair's demand from
``demand_events`` into consecutive 30-day buckets ending on the as-of
date. It shares the bucket alignment and the default as-of date with
``app.stats.demand``: bucket ``k`` (``k = 0`` newest .. ``k = 35``
oldest) covers the 30 days ending on ``as_of - 30k`` days, and a caller
that supplies no ``as_of`` gets the shared default — the latest
``occurred_on`` across all demand events, never later than today —
resolved from ``demand_events`` alone, so an empty table never raises,
even when other tables do not exist. The pair gets only the complete
30-day buckets that fit from its first-ever demand through the as-of
date — at least one, at most 36, oldest first — the same count rule
``app.stats.demand.compute_demand_stats`` uses: a first demand 35 days
before the as-of date leaves a single bucket (the last 30 days) and the
demand of the 5 earlier days is not in the history; a first demand 60
days before leaves two buckets, the day before the oldest bucket not in
the history. Every bucket carries its ``bucket_start``, its
``bucket_end`` and its ``quantity``; a day without demand counts as
zero. A pair with no demand events at all returns None.

``history_for_all`` (issue #365) returns every item-site's buckets
from a single query, whatever the number of item-sites: for each pair
it equals what ``history_for_pair`` returns for that pair and date, by
the same complete-bucket rule.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.db.connection import get_cursor
from app.db.read_cache import cached_read
from app.stats.demand import BUCKET_DAYS, _shift_back, resolve_as_of

HISTORY_N_BUCKETS = 36
HISTORY_WINDOW_DAYS = BUCKET_DAYS * HISTORY_N_BUCKETS


def _default_as_of(cur) -> date:
    """The default as-of date when the caller supplies none.

    The earlier of today and the latest ``occurred_on`` across all
    demand events (today when there are none) — the shared default,
    resolved from ``demand_events`` alone, so an empty table never
    raises, even when other tables do not exist.
    """
    cur.execute("SELECT MAX(occurred_on) AS latest FROM demand_events")
    latest = cur.fetchone()["latest"]
    return resolve_as_of(date.today(), latest)


def _buckets_for(
    daily_totals: dict[date, float],
    first_seen: date | None,
    as_of: date,
) -> list[dict] | None:
    """The pair's complete 30-day buckets from its daily totals.

    The pure core of ``history_for_pair`` and ``history_for_all``:
    ``daily_totals`` maps date to quantity for the pair (a day without
    demand absent, counting as zero) and ``first_seen`` the pair's
    first-ever ``occurred_on`` (None when it has no demand events at
    all, which returns None).  The pair gets only the complete 30-day
    buckets that fit from ``first_seen`` through the as-of date —
    ``((as_of - first_seen).days + 1) // 30``, capped at 36 and floored
    at one, the earliest bucket clamping to ``date.min`` — exactly the
    rule ``app.stats.demand.compute_demand_stats`` uses; the list comes
    back oldest first, each entry carrying ``bucket_start``,
    ``bucket_end`` and ``quantity``.
    """
    if first_seen is None:
        return None

    # Only complete 30-day buckets count: the same rule
    # ``app.stats.demand.compute_demand_stats`` uses —
    # ``((as_of - first_seen).days + 1) // 30`` — capped at 36 and
    # floored at one. A first demand 35 days before the as-of date
    # leaves a single bucket (the last 30 days), and one 60 days before
    # leaves two, the first demand itself not in the history.
    n_buckets = min(HISTORY_N_BUCKETS, ((as_of - first_seen).days + 1) // BUCKET_DAYS)
    n_buckets = min(n_buckets, ((as_of - date.min).days + 1) // BUCKET_DAYS)
    n_buckets = max(1, n_buckets)
    window_start = _shift_back(as_of, BUCKET_DAYS * (n_buckets - 1) + BUCKET_DAYS - 1)

    bucket_quantities: list[float] = [0.0] * n_buckets
    for offset in range((as_of - window_start).days + 1):
        day = window_start + timedelta(days=offset)
        bucket_quantities[(as_of - day).days // BUCKET_DAYS] += float(
            daily_totals.get(day, 0.0)
        )

    buckets: list[dict] = []
    for position in range(n_buckets):
        bucket = n_buckets - 1 - position
        buckets.append(
            {
                "bucket_start": _shift_back(
                    as_of, BUCKET_DAYS * bucket + BUCKET_DAYS - 1
                ),
                "bucket_end": _shift_back(as_of, BUCKET_DAYS * bucket),
                "quantity": bucket_quantities[bucket],
            }
        )
    return buckets


def history_for_pair(
    item_code: str,
    site_code: str,
    as_of: date | None = None,
) -> list[dict] | None:
    """The pair's demand in the complete 30-day buckets, up to 36.

    Bucket ``k`` (``k = 0`` newest .. ``k = 35`` oldest) covers the 30
    days ending on ``as_of - 30k`` days, exactly the alignment
    ``app.stats.demand`` uses, and the default ``as_of`` is the shared
    default — the latest ``occurred_on`` across all demand events,
    never later than today — resolved from ``demand_events`` alone, so
    an empty table never raises, even when other tables do not exist.
    The list comes back oldest
    first; each entry carries ``bucket_start``, ``bucket_end`` and
    ``quantity``. The count is the number of whole 30-day buckets that
    fit from the pair's first demand through the as-of date —
    ``((as_of - first_seen).days + 1) // 30``, capped at 36 and floored
    at one — exactly the rule
    ``app.stats.demand.compute_demand_stats`` uses. A day without
    demand counts as zero. Returns None when the pair
    has no demand events at all.
    """
    with get_cursor() as cur:
        resolved_as_of = as_of if as_of is not None else _default_as_of(cur)
        window_start = _shift_back(resolved_as_of, HISTORY_WINDOW_DAYS - 1)
        cur.execute(
            "SELECT occurred_on, SUM(quantity) AS day_total "
            "FROM demand_events "
            "WHERE item_code = %s AND site_code = %s "
            "AND occurred_on >= %s AND occurred_on <= %s "
            "GROUP BY occurred_on",
            (item_code, site_code, window_start, resolved_as_of),
        )
        daily_totals = {
            row["occurred_on"]: float(row["day_total"]) for row in cur.fetchall()
        }
        cur.execute(
            "SELECT MIN(occurred_on) AS first_seen "
            "FROM demand_events "
            "WHERE item_code = %s AND site_code = %s",
            (item_code, site_code),
        )
        first_seen = cur.fetchone()["first_seen"]

    return _buckets_for(daily_totals, first_seen, resolved_as_of)


@cached_read
def history_for_all(as_of: date | None = None) -> dict[tuple[str, str], list[dict]]:
    """Every item-site's 30-day history buckets, from one query.

    Returns one entry per (item_code, site_code) pair that has demand
    events, each the list ``history_for_pair`` returns for that pair
    and date — same alignment, same complete-bucket rule, a day
    without demand counting as zero — built from a single query
    whatever the number of item-sites.  Pairs with no demand events at
    all get no entry, as ``history_for_pair`` returns None for them.
    The default ``as_of`` is the shared default, resolved from
    ``demand_events`` alone like ``history_for_pair``.
    """
    with get_cursor() as cur:
        resolved_as_of = as_of if as_of is not None else _default_as_of(cur)
        window_start = _shift_back(resolved_as_of, HISTORY_WINDOW_DAYS - 1)
        # One query for every pair: each pair's first-ever demand (the
        # unbounded MIN, the complete-bucket rule's input) beside its
        # daily totals inside the full 36-bucket window.
        cur.execute(
            "SELECT f.item_code AS item_code, "
            "f.site_code AS site_code, "
            "f.first_seen AS first_seen, "
            "d.occurred_on AS occurred_on, "
            "d.day_total AS day_total "
            "FROM ( "
            "SELECT item_code, site_code, MIN(occurred_on) AS first_seen "
            "FROM demand_events "
            "GROUP BY item_code, site_code "
            ") AS f "
            "LEFT JOIN ( "
            "SELECT item_code, site_code, occurred_on, SUM(quantity) AS day_total "
            "FROM demand_events "
            "WHERE occurred_on >= %s AND occurred_on <= %s "
            "GROUP BY item_code, site_code, occurred_on "
            ") AS d ON d.item_code = f.item_code AND d.site_code = f.site_code",
            (window_start, resolved_as_of),
        )
        rows = cur.fetchall()

    first_seen_by_pair: dict[tuple[str, str], date] = {}
    daily_by_pair: dict[tuple[str, str], dict[date, float]] = {}
    for row in rows:
        key = (row["item_code"], row["site_code"])
        first_seen_by_pair[key] = row["first_seen"]
        if row["occurred_on"] is not None:
            daily_by_pair.setdefault(key, {})[row["occurred_on"]] = float(
                row["day_total"]
            )
    return {
        key: _buckets_for(daily_by_pair.get(key, {}), first_seen, resolved_as_of)
        for key, first_seen in first_seen_by_pair.items()
    }
