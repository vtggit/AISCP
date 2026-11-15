"""Empirical lead-time demand reorder points from a window of daily totals.

``empirical_reorder_point`` is the pure, database-free core of the
empirical method: given one item-site's daily demand totals for a
window of days, it sums every overlapping span of ``L`` consecutive
days — ``L`` the lead time in whole days, ``max(1, ceil(lead_time_days))``
— and takes the service-level quantile of those span totals as the
reorder point: the spans are the sum of the ``L`` days starting at
each start day from the first through the (number of days - L + 1)-th,
``n`` is their number, and ``k = ceil(service_level * n)`` clamped to
1..n picks the ``k``-th smallest total.  The result carries the
reorder point (floored at 0.0) with its whole-unit ``math.ceil`` twin,
the mean of the span totals (``mean_lead_time_demand``) and the safety
stock ``max(0, reorder_point - mean_lead_time_demand)`` with its
whole-unit twin, plus a one-line ``arithmetic`` of the formula with the
numbers substituted.

Refusals follow the layer's degrade-or-raise discipline: a
``service_level`` that is not strictly between 0 and 1 (non-numeric and
non-finite values included) raises ``ValueError``; a ``lead_time_days``
that is negative, NaN, infinite or not a number at all answers ``None``
— as does a window of fewer than ``L`` days or a window that is not a
pair of dates — and a ``daily_totals`` argument that is not a mapping
is read as empty.  A day's demand is ``daily_totals.get(day, 0.0)``
verbatim: the stored value is used exactly as stored, a non-finite
value (NaN or infinity) included, and flows uncoerced into the span
totals, the mean and the reorder point; a result that is itself not
finite (an infinite span total) carries through its whole-unit field
as-is, since ``math.ceil`` is undefined for it.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

from app.units import ceil_units

METHOD = "empirical lead-time demand"


def empirical_reorder_point(
    daily_totals: dict[date, float],
    window_start: date,
    as_of: date,
    lead_time_days: float,
    service_level: float,
) -> dict | None:
    """The empirical reorder point of one window of daily demand totals.

    The days run from ``window_start`` through ``as_of`` inclusive and
    a day's demand is ``daily_totals.get(day, 0.0)``.  ``L`` is the
    lead time in whole days, ``max(1, ceil(lead_time_days))``; the
    spans are the sums of the ``L`` days starting at each start day
    from the first through the (number of days - L + 1)-th and ``n``
    is their number.  The reorder point is the ``k``-th smallest span
    total, ``k = ceil(service_level * n)`` clamped to 1..n, floored at
    0.0; ``mean_lead_time_demand`` is the mean of the span totals and
    ``safety_stock`` is ``max(0.0, reorder_point -
    mean_lead_time_demand)``.

    Refusals: a ``service_level`` not strictly between 0 and 1
    (non-numeric or non-finite included) raises ``ValueError``; a
    negative, NaN or infinite ``lead_time_days`` — and a window of
    fewer than ``L`` days, or one that is not a pair of dates —
    answers ``None``.

    The result carries exactly these keys: ``method`` (METHOD),
    ``window_days`` (L), ``spans`` (n), ``spans_with_demand`` (the
    number of span totals > 0), ``service_level``, ``reorder_point``,
    ``reorder_point_units`` (``math.ceil``),
    ``mean_lead_time_demand``, ``safety_stock``,
    ``safety_stock_units`` (``math.ceil``) and ``arithmetic`` — the
    human-readable formula with the numbers substituted.
    """
    try:
        service = float(service_level)
    except (TypeError, ValueError):
        raise ValueError(
            "service_level must be a number strictly between 0 and 1"
        ) from None
    if not math.isfinite(service) or not 0.0 < service < 1.0:
        raise ValueError("service_level must be a number strictly between 0 and 1")

    try:
        lead = float(lead_time_days)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(lead) or lead < 0:
        return None
    window_days = max(1, math.ceil(lead))

    if not isinstance(daily_totals, dict):
        daily_totals = {}
    if not isinstance(window_start, date) or not isinstance(as_of, date):
        return None
    number_of_days = (as_of - window_start).days + 1
    if number_of_days < window_days:
        return None

    demands = []
    day = window_start
    for _ in range(number_of_days):
        demands.append(daily_totals.get(day, 0.0))
        day += timedelta(days=1)
    prefix = [0.0]
    for demand in demands:
        prefix.append(prefix[-1] + demand)
    totals = [
        prefix[i + window_days] - prefix[i]
        for i in range(number_of_days - window_days + 1)
    ]
    spans = len(totals)
    ordered = sorted(totals)
    k = max(1, min(spans, math.ceil(service * spans)))
    reorder_point = max(0.0, ordered[k - 1])
    mean_lead_time_demand = sum(totals) / spans
    safety_stock = max(0.0, reorder_point - mean_lead_time_demand)
    if math.isfinite(reorder_point):
        reorder_point_units = ceil_units(reorder_point)
    else:
        reorder_point_units = reorder_point
    if math.isfinite(safety_stock):
        safety_stock_units = ceil_units(safety_stock)
    else:
        safety_stock_units = safety_stock
    pct = f"{service * 100:g}"
    arithmetic = (
        f"reorder point = the {pct}% quantile of {spans} overlapping "
        f"{window_days}-day demand totals = {reorder_point:g}; "
        f"mean {window_days}-day demand = {mean_lead_time_demand:.2f}; "
        f"safety stock = max(0, {reorder_point:g} - {mean_lead_time_demand:.2f}) "
        f"= {safety_stock:.2f}"
    )
    return {
        "method": METHOD,
        "window_days": window_days,
        "spans": spans,
        "spans_with_demand": sum(1 for total in totals if total > 0),
        "service_level": service,
        "reorder_point": reorder_point,
        "reorder_point_units": reorder_point_units,
        "mean_lead_time_demand": mean_lead_time_demand,
        "safety_stock": safety_stock,
        "safety_stock_units": safety_stock_units,
        "arithmetic": arithmetic,
    }
