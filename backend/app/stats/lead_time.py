"""Lead time statistics per item and supplier.

The pure function here (``compute_lead_time_stats``) does no database
access at all: it works from a flat list of observed lead times in days.
The ``lead_time_stats_for_*`` readers pull the ``lead_time_days`` of
observations whose ``received_on`` falls in the 730-day window up to and
including ``as_of`` through the standard parameterised cursor.

An observation is an outlier when it is strictly greater than the fence
``Q3 + 3 * (Q3 - Q1)`` (``statistics.quantiles`` with ``n=4`` and
``method="inclusive"``); outliers are dropped before the mean and sample
standard deviation are computed. With fewer than four observations no
fence can be computed, nothing is dropped, and ``fence`` is None.
A NaN observation is never used and is kept out of the fence, so a NaN
in the data cannot produce a NaN fence that drops everything.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta

from app.db.connection import get_cursor
from app.stats.demand import default_as_of

WINDOW_DAYS = 730

METHOD = "Q3 + 3*IQR outlier fence, then mean and sample standard deviation"


def _shift_back(anchor: date, days: int) -> date:
    """``anchor - days``, clamped to ``date.min`` when not representable.

    A caller-supplied ``as_of`` near ``date.min`` (or an oversized
    ``days``) would otherwise raise ``OverflowError``; the window simply
    starts on the first calendar day.
    """
    try:
        return anchor - timedelta(days=days)
    except OverflowError:
        return date.min


def compute_lead_time_stats(days: list[float]) -> dict:
    """Outlier-fenced lead time summary for one observation list.

    With four or more non-NaN observations the fence is ``Q3 + 3 *
    (Q3 - Q1)`` from ``statistics.quantiles`` (``n=4``,
    ``method="inclusive"``) over those, and every other observation
    strictly greater than the fence is dropped; with fewer than four
    non-NaN observations nothing is dropped and ``fence`` is None. NaN
    observations are never used, so they cannot fence the finite ones
    out. ``mean_days``, ``std_days`` (sample, n-1; 0.0 when fewer than
    two are used), ``min_days`` and ``max_days`` are computed over the
    observations that were used. ``low_confidence`` is true when fewer
    than three observations were used.
    """
    observations = [float(value) for value in days]
    n_observations = len(observations)
    usable = [value for value in observations if not math.isnan(value)]

    fence: float | None = None
    if len(usable) >= 4:
        quartile_low, _, quartile_high = statistics.quantiles(
            usable, n=4, method="inclusive"
        )
        fence = quartile_high + 3.0 * (quartile_high - quartile_low)

    used = [value for value in usable if fence is None or value <= fence]
    n_used = len(used)

    if n_used:
        mean_days = sum(used) / n_used
        min_days = min(used)
        max_days = max(used)
        std_days = statistics.stdev(used) if n_used >= 2 else 0.0
    else:
        mean_days = 0.0
        std_days = 0.0
        min_days = 0.0
        max_days = 0.0

    return {
        "n_observations": n_observations,
        "n_used": n_used,
        "n_dropped": n_observations - n_used,
        "fence": fence,
        "mean_days": mean_days,
        "std_days": std_days,
        "min_days": min_days,
        "max_days": max_days,
        "low_confidence": n_used < 3,
        "method": METHOD,
    }


def _resolve_as_of(cur, as_of: date | None) -> date:
    """Use the given as_of, or the shared demand/lead-time default."""
    if as_of is not None:
        return as_of
    return default_as_of(cur)


def _read_window(cur, as_of: date, item_code: str | None = None) -> list[dict]:
    """Observations in the WINDOW_DAYS window up to and including as_of."""
    window_start = _shift_back(as_of, WINDOW_DAYS)
    if item_code is None:
        cur.execute(
            "SELECT item_code, supplier_code, lead_time_days "
            "FROM lead_time_observations "
            "WHERE received_on >= %s AND received_on <= %s",
            (window_start, as_of),
        )
    else:
        cur.execute(
            "SELECT item_code, supplier_code, lead_time_days "
            "FROM lead_time_observations "
            "WHERE item_code = %s AND received_on >= %s AND received_on <= %s",
            (item_code, window_start, as_of),
        )
    return cur.fetchall()


def _build_item_stats(item_code: str, as_of: date, rows: list[dict]) -> dict:
    """Pooled per-item stats plus the per-supplier breakdown."""
    pooled: list[float] = []
    by_supplier: dict[str, list[float]] = {}
    for row in rows:
        lead_time = float(row["lead_time_days"])
        pooled.append(lead_time)
        by_supplier.setdefault(row["supplier_code"], []).append(lead_time)

    suppliers = [
        {"supplier_code": supplier_code, **compute_lead_time_stats(supplier_days)}
        for supplier_code, supplier_days in sorted(by_supplier.items())
    ]
    return {
        "item_code": item_code,
        "as_of": as_of,
        "suppliers": suppliers,
        **compute_lead_time_stats(pooled),
    }


def lead_time_stats_for_all(as_of: date | None = None) -> list[dict]:
    """Lead time stats for every item with observations in the window.

    When ``as_of`` is None it falls back to
    ``app.stats.demand.default_as_of``, the same resolution
    ``app.stats.demand.demand_stats_for_all`` uses, so both statistics
    share one default as-of date. One
    result per ``item_code``: ``compute_lead_time_stats`` over all
    suppliers' observations pooled, plus a ``suppliers`` list holding
    ``compute_lead_time_stats`` per ``supplier_code``, ordered by
    ``item_code``.
    """
    with get_cursor() as cur:
        resolved_as_of = _resolve_as_of(cur, as_of)
        rows = _read_window(cur, resolved_as_of)

    rows_by_item: dict[str, list[dict]] = {}
    for row in rows:
        rows_by_item.setdefault(row["item_code"], []).append(row)

    return [
        _build_item_stats(item_code, resolved_as_of, item_rows)
        for item_code, item_rows in sorted(rows_by_item.items())
    ]


def lead_time_stats_for_item(
    item_code: str,
    as_of: date | None = None,
) -> dict | None:
    """Lead time stats for one item.

    Returns None when the item has no observation in the window;
    otherwise the pooled result with its per-supplier breakdown.
    """
    with get_cursor() as cur:
        resolved_as_of = _resolve_as_of(cur, as_of)
        rows = _read_window(cur, resolved_as_of, item_code)

    if not rows:
        return None
    return _build_item_stats(item_code, resolved_as_of, rows)
