"""Demand statistics for one item at one site (warehouse).

The pure functions here (``resolve_as_of``, ``compute_demand_stats``) do
no database access at all: they work from an as-of date and a mapping of
date to quantity for a single (item_code, site_code) pair. The
``demand_stats_for_*`` readers pull the aggregates they need from the
``demand_events`` table through the standard parameterised cursor.
``demand_stats_for_all``'s optional ``pairs`` restricts both of its
reads in SQL to the given (item_code, site_code) pairs — the window's
daily totals and the first-seen dates — and its cache key carries the
normalized pair set, so a pair-restricted read never serves an
all-pair result and vice versa.

Demand is bucketed into consecutive 30-day buckets ending on ``as_of``:
bucket ``k`` (``k = 0..11``) covers the 30 days ending on
``as_of - 30k`` days. A pair with the full history gets all 12
buckets; a pair first seen later than the start of bucket 11 gets only
the complete 30-day buckets that fit from ``first_seen`` through
``as_of`` (at least one). When ``as_of`` is so close to the first
representable date that fewer than 12 whole buckets fit, only the whole
buckets that fit are counted (at least one), the earliest bucket
clamping to ``date.min`` instead of raising ``OverflowError``.

Bucket quantities feed the Syntetos-Boylan demand classification:
``adi`` is ``n_buckets`` over the number of non-zero buckets and
``cv2`` is (population standard deviation / mean)^2 over the non-zero
bucket quantities. With the ADI 1.32 and CV2 0.49 thresholds,
``demand_class`` is ``no_demand`` (no non-zero bucket), ``smooth``
(ADI below, CV2 below), ``erratic`` (ADI below, CV2 at or above),
``intermittent`` (ADI at or above, CV2 below) or ``lumpy`` (both at or
above).
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from app.db.connection import get_cursor
from app.db.read_cache import cached_read

BUCKET_DAYS = 30
DEFAULT_N_BUCKETS = 12
DEFAULT_WINDOW_DAYS = BUCKET_DAYS * DEFAULT_N_BUCKETS

ADI_THRESHOLD = 1.32
CV2_THRESHOLD = 0.49
# A cv2 that is exactly at the threshold mathematically can land a few
# ulps below it in float arithmetic ((0.7) ** 2 < 0.49); a cv2 within
# this tolerance of the threshold counts as at or above it.
CV2_TOLERANCE = 1e-9

METHOD = "30-day buckets; Syntetos-Boylan classification (ADI 1.32, CV2 0.49)"


def _shift_back(anchor: date, days: int) -> date:
    """``anchor - days``, clamped to ``date.min`` when not representable.

    A caller-supplied ``as_of`` near ``date.min`` (or an oversized
    ``days``) would otherwise raise ``OverflowError``; the bucket simply
    starts on the first calendar day.
    """
    try:
        return anchor - timedelta(days=days)
    except OverflowError:
        return date.min


def resolve_as_of(today: date, latest_occurred_on: date | None) -> date:
    """The earlier of ``today`` and ``latest_occurred_on``.

    Returns ``today`` when ``latest_occurred_on`` is None (no demand has
    been observed yet).
    """
    if latest_occurred_on is None:
        return today
    return min(today, latest_occurred_on)


def compute_demand_stats(
    daily_totals: dict[date, float],
    as_of: date,
    first_seen: date | None = None,
) -> dict:
    """Bucket one item-site's daily demand into 30-day buckets.

    ``daily_totals`` maps date to quantity for one (item_code,
    site_code) pair. Bucket ``k`` (``k = 0..11``) covers the 30 days
    ending on ``as_of - 30k``. The pair gets all 12 buckets, or only
    the complete 30-day buckets that fit from ``first_seen`` through
    ``as_of`` (at least one) when ``first_seen`` is later than the
    start of bucket 11, or only the whole buckets that fit when
    ``as_of`` is so close to ``date.min`` that fewer than 12 whole
    buckets do (at least one; the earliest bucket clamps to
    ``date.min``). Every day in a bucket without an entry counts as 0.

    ``mean_daily`` is the mean bucket quantity over 30 and
    ``std_daily`` the sample standard deviation of the bucket
    quantities (n-1; 0.0 for a single bucket) over sqrt(30). ``adi`` is
    ``n_buckets`` over the number of non-zero buckets (None when there
    are none) and ``cv2`` the squared population coefficient of
    variation of the non-zero bucket quantities (0.0 for one, None for
    none). ``demand_class`` follows the ADI 1.32 / CV2 0.49
    thresholds; ``recommendable`` covers ``smooth`` and ``erratic``
    only, ``intermittent`` the remaining classes.
    """
    n_buckets = DEFAULT_N_BUCKETS
    if first_seen is not None:
        n_buckets = min(n_buckets, ((as_of - first_seen).days + 1) // BUCKET_DAYS)
    n_buckets = min(n_buckets, ((as_of - date.min).days + 1) // BUCKET_DAYS)
    n_buckets = max(1, n_buckets)

    window_end = as_of
    window_start = _shift_back(as_of, BUCKET_DAYS * (n_buckets - 1) + BUCKET_DAYS - 1)

    bucket_quantities: list[float] = [0.0] * n_buckets
    for offset in range((window_end - window_start).days + 1):
        day = window_start + timedelta(days=offset)
        bucket_quantities[(window_end - day).days // BUCKET_DAYS] += float(
            daily_totals.get(day, 0.0)
        )

    total_quantity = sum(bucket_quantities)
    nonzero_quantities = [quantity for quantity in bucket_quantities if quantity != 0.0]
    nonzero_buckets = len(nonzero_quantities)

    mean_daily = total_quantity / n_buckets / BUCKET_DAYS
    if n_buckets >= 2:
        std_daily = statistics.stdev(bucket_quantities) / math.sqrt(BUCKET_DAYS)
    else:
        std_daily = 0.0

    adi = n_buckets / nonzero_buckets if nonzero_buckets else None

    if nonzero_buckets == 0:
        cv2 = None
    elif nonzero_buckets == 1:
        cv2 = 0.0
    else:
        mean_nonzero = sum(nonzero_quantities) / nonzero_buckets
        if mean_nonzero == 0.0:
            # Mixed-sign buckets summing to zero leave the coefficient of
            # variation undefined; treat it as maximally regular.
            cv2 = 0.0
        else:
            cv2 = (statistics.pstdev(nonzero_quantities) / mean_nonzero) ** 2

    if nonzero_buckets == 0:
        demand_class = "no_demand"
    elif adi < ADI_THRESHOLD and cv2 < CV2_THRESHOLD - CV2_TOLERANCE:
        demand_class = "smooth"
    elif adi < ADI_THRESHOLD:
        demand_class = "erratic"
    elif cv2 < CV2_THRESHOLD - CV2_TOLERANCE:
        demand_class = "intermittent"
    else:
        demand_class = "lumpy"

    return {
        "bucket_days": BUCKET_DAYS,
        "n_buckets": n_buckets,
        "nonzero_buckets": nonzero_buckets,
        "window_start": window_start,
        "window_end": window_end,
        "total_quantity": total_quantity,
        "mean_daily": mean_daily,
        "std_daily": std_daily,
        "adi": adi,
        "cv2": cv2,
        "demand_class": demand_class,
        "recommendable": demand_class in ("smooth", "erratic"),
        "intermittent": demand_class in ("intermittent", "lumpy", "no_demand"),
        "method": METHOD,
    }


def _latest_occurred_on(cur) -> date | None:
    """The latest occurred_on across all demand events (None when empty)."""
    cur.execute("SELECT MAX(occurred_on) AS latest FROM demand_events")
    return cur.fetchone()["latest"]


def default_as_of(cur) -> date:
    """The shared default as-of date used when a caller supplies none.

    The earlier of today and the latest ``occurred_on`` across all
    demand events; when no demand rows exist, the earlier of today and
    the latest ``received_on`` across all lead time observations (today
    when there are none either).
    """
    latest = _latest_occurred_on(cur)
    if latest is None:
        cur.execute("SELECT MAX(received_on) AS latest FROM lead_time_observations")
        latest = cur.fetchone()["latest"]
    return resolve_as_of(date.today(), latest)


def _resolve_as_of(cur, as_of: date | None) -> date:
    """Use the given as_of, or the shared default when it is None."""
    if as_of is not None:
        return as_of
    return default_as_of(cur)


def demand_stats_for_all(
    as_of: date | None = None,
    with_daily: bool = False,
    pairs: tuple | None = None,
) -> list[dict]:
    """Demand stats for every (item_code, site_code) pair in demand_events.

    When ``as_of`` is None it falls back to ``default_as_of``: the
    latest ``occurred_on`` overall, never later than today. The daily
    ``SUM(quantity)`` per (item_code, site_code, occurred_on) is read
    once for the 360-day window ending on the resolved as_of, plus each
    pair's first-ever ``occurred_on``. Returns one result per pair,
    ordered by item_code then site_code.

    ``pairs``, a tuple of (item_code, site_code) pairs, restricts both
    reads in SQL to those pairs — the window's daily totals and the
    first-seen dates, the same predicate on both — so a pair's
    statistics are the same whether the read is restricted or not.
    ``None`` (the default) reads every pair exactly as before, and an
    empty tuple answers ``[]`` without a query.  The read cache's key
    carries the normalized pair set — the distinct pairs, sorted — so
    calls naming the same set in any order share one entry, and a
    pair-restricted read never serves an all-pair result, or an
    all-pair read a restricted one.

    With ``with_daily`` every result also carries ``daily_totals``, the
    pair's ``{date: day_total}`` mapping already read for the window
    (empty when the pair has no event in it), and
    ``daily_window_start``, the first day of the pair's window: the
    360-day window's start, or the pair's first-seen day when that is
    later. Without it the results are exactly as before, and the
    statements executed are the same either way.
    """
    return _demand_stats_for_all(as_of, with_daily, _normalized_pairs(pairs))


def _normalized_pairs(pairs: tuple | None) -> tuple | None:
    """The pair set of one call, normalized for the cache key: ``None``
    when the caller passed ``None`` (every pair), else the distinct
    (item_code, site_code) pairs sorted — so calls naming the same set
    in any order share one cache entry, and the SQL predicate binds
    exactly that set."""
    if pairs is None:
        return None
    return tuple(sorted({(item_code, site_code) for item_code, site_code in pairs}))


def _pair_filter(pairs: tuple | None) -> tuple[str, tuple]:
    """The pair predicate shared by the two reads: empty when the
    caller passed ``None`` (every pair), else
    ``(item_code, site_code) IN %s`` binding the normalized pair set
    — the same predicate on the window's daily totals and on the
    first-seen dates."""
    if pairs is None:
        return "", ()
    return "(item_code, site_code) IN %s", (pairs,)


@cached_read
def _demand_stats_for_all(
    as_of: date | None,
    with_daily: bool,
    pairs: tuple | None,
) -> list[dict]:
    if pairs == ():
        # The empty set names no pair: answer without a query.
        return []
    pair_sql, pair_params = _pair_filter(pairs)
    daily_sql = (
        "SELECT item_code, site_code, occurred_on, SUM(quantity) AS day_total "
        "FROM demand_events "
        "WHERE occurred_on >= %s AND occurred_on <= %s"
    )
    if pair_sql:
        daily_sql += " AND " + pair_sql
    first_seen_sql = (
        "SELECT item_code, site_code, MIN(occurred_on) AS first_seen "
        "FROM demand_events"
    )
    if pair_sql:
        first_seen_sql += " WHERE " + pair_sql
    with get_cursor() as cur:
        resolved_as_of = _resolve_as_of(cur, as_of)
        window_start = _shift_back(resolved_as_of, DEFAULT_WINDOW_DAYS - 1)
        cur.execute(
            daily_sql + " GROUP BY item_code, site_code, occurred_on",
            (window_start, resolved_as_of) + pair_params,
        )
        daily_rows = cur.fetchall()
        cur.execute(first_seen_sql + " GROUP BY item_code, site_code", pair_params)
        first_seen_rows = cur.fetchall()

    daily_by_pair: dict[tuple[str, str], dict[date, float]] = {}
    for row in daily_rows:
        key = (row["item_code"], row["site_code"])
        daily_by_pair.setdefault(key, {})[row["occurred_on"]] = float(row["day_total"])
    first_seen_by_pair = {
        (row["item_code"], row["site_code"]): row["first_seen"]
        for row in first_seen_rows
    }

    results: list[dict] = []
    for key in sorted(set(daily_by_pair) | set(first_seen_by_pair)):
        item_code, site_code = key
        stats = compute_demand_stats(
            daily_by_pair.get(key, {}),
            resolved_as_of,
            first_seen=first_seen_by_pair.get(key),
        )
        row = {
            "item_code": item_code,
            "site_code": site_code,
            "as_of": resolved_as_of,
            **stats,
        }
        if with_daily:
            first_seen = first_seen_by_pair.get(key)
            row["daily_totals"] = daily_by_pair.get(key, {})
            row["daily_window_start"] = (
                max(window_start, first_seen)
                if first_seen is not None
                else window_start
            )
        results.append(row)
    return results


def demand_stats_for_pair(
    item_code: str,
    site_code: str,
    as_of: date | None = None,
    with_daily: bool = False,
) -> dict | None:
    """Demand stats for one (item_code, site_code) pair.

    Returns None when the pair has no demand events at all; otherwise
    the ``compute_demand_stats`` output with item_code, site_code and
    as_of added. With ``with_daily`` the result also carries
    ``daily_totals``, the pair's ``{date: day_total}`` mapping already
    read for the window, and ``daily_window_start``, the first day of
    the pair's window: the 360-day window's start, or the pair's
    first-seen day when that is later. Without it the result is
    exactly as before, and the statements executed are the same
    either way.
    """
    with get_cursor() as cur:
        resolved_as_of = _resolve_as_of(cur, as_of)
        window_start = _shift_back(resolved_as_of, DEFAULT_WINDOW_DAYS - 1)
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

    if first_seen is None:
        return None
    stats = compute_demand_stats(daily_totals, resolved_as_of, first_seen=first_seen)
    row = {
        "item_code": item_code,
        "site_code": site_code,
        "as_of": resolved_as_of,
        **stats,
    }
    if with_daily:
        row["daily_totals"] = daily_totals
        row["daily_window_start"] = max(window_start, first_seen)
    return row
