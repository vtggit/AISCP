"""Deferral candidates for the over-budget months of the projected spend.

``deferral_candidates`` estimates, one month at a time, which of the
projection's planned orders arriving in an over-budget month could be
deferred to the following month so the month comes within its budget.
A deferral is estimated one month at a time and later months are NOT
re-planned: deferring an order only adds its value to the following
month's already-projected spend (``next_month_total_after``); the
following month's own planned orders are not re-run through the
review for the deferral, and no order the review places for a later
month is moved or re-sized.

The month totals and budgets come from
``app.aggregate.spend._spend_view`` and the orders from
``app.aggregate.projection._planned_orders_from_setup``, both fed from
one ``app.aggregate.projection._projection_setup`` run with the same
arguments — so ``recommendations_for_all`` and the two other
projection reads happen once each and the horizon always matches the
projection's — and the shared setup is never mutated (each consumer
walks its own copy of the review state); the computation is read-only
and writes nothing.  Every ``ValueError`` the setup raises (``months``
outside 1 to 12, a future ``as_of``, a lever out of range or a scaled
input that overflows the reorder formula) propagates unchanged.

The result is ``{"months": [...]}`` with one entry per month the
spend view flags ``over_budget``, in month order: ``month`` ("YYYY-MM"),
``overrun`` (that month's ``total_spend`` minus its budget),
``suggested_value`` (the sum of the suggested candidates' values),
``uncovered`` (``max(0, overrun - suggested_value)``), ``next_month``
(the following calendar month), ``next_month_total_after`` (that
month's ``total_spend`` plus ``suggested_value``, or None when the
month is outside the horizon) and ``next_month_over_after`` (true only
when that month has a budget and ``next_month_total_after`` exceeds
it).  ``candidates`` are the planned orders arriving in the month,
each ``item_code``, ``site_code``, ``quantity``, ``value``,
``cover_days_if_deferred`` (``(month_end_on_hand - quantity) / d``
rounded to one decimal, or None when ``d`` is 0), ``would_stock_out``
(true when ``month_end_on_hand - quantity`` is below zero),
``suggested`` and ``cumulative_suggested_value`` (the running total of
the suggested values through the candidate in candidate order).  The
candidates are ordered with the orders that would not stock out first,
by cover descending then value descending (a null cover sorts last),
then the stock-out ones by cover descending.  They are suggested in
that order while the suggested value is still below the overrun; an
order that would stock out, or has value 0, is never suggested.  No
pagination: every candidate of the requested month(s) is returned in
the single ``months`` list.
"""

from __future__ import annotations

from datetime import date

from app.aggregate import projection
from app.aggregate.spend import _spend_view


def _next_month_label(label: str) -> str:
    """The "YYYY-MM" of the calendar month following ``label``."""
    year, month = (int(part) for part in label.split("-"))
    if month == 12:
        return f"{year + 1:04d}-01"
    return f"{year:04d}-{month + 1:02d}"


def _cover_sort_key(cover: float | None):
    """Cover descending, a null cover (no daily demand) sorting last."""
    return (0, -cover) if cover is not None else (1, 0.0)


def deferral_candidates(
    as_of: date | None = None,
    months: int = 6,
    service_level: float | None = None,
    demand_multiplier: float | None = None,
    lead_time_multiplier: float | None = None,
) -> dict:
    """The deferral candidates of the over-budget months of the horizon.

    The arguments carry exactly the meaning
    ``app.aggregate.spend.spend_against_budget`` and
    ``app.aggregate.projection.planned_orders`` give them and are
    given, once, to one shared
    ``app.aggregate.projection._projection_setup`` run, which feeds
    both the spend computation and the planned-order list (the setup
    is never mutated: each consumer walks its own copy of the review
    state); every ``ValueError`` the setup raises propagates
    unchanged.

    Returns ``{"months": [...]}`` with one entry per month the spend
    view flags ``over_budget``, in month order, each ``month``
    ("YYYY-MM"), ``overrun`` (that month's ``total_spend`` minus its
    budget), the month's ``candidates`` (the planned orders arriving
    in the month, each ``item_code``, ``site_code``, ``quantity``,
    ``value``, ``cover_days_if_deferred``, ``would_stock_out``,
    ``suggested`` and ``cumulative_suggested_value``; ordered with the
    orders that would not stock out first, by cover descending then
    value descending, then the stock-out ones by cover descending;
    suggested in that order while the suggested value is still below
    the overrun, an order that would stock out or has value 0 never
    suggested), ``suggested_value`` (the sum of the suggested
    values), ``uncovered`` (``max(0, overrun - suggested_value)``),
    ``next_month`` (the following calendar month),
    ``next_month_total_after`` (that month's ``total_spend`` plus
    ``suggested_value``, or None when it is outside the horizon) and
    ``next_month_over_after`` (true only when that month has a budget
    and ``next_month_total_after`` exceeds it).  A deferral is
    estimated one month at a time and later months are not re-planned.
    """
    setup = projection._projection_setup(
        as_of,
        months,
        service_level,
        demand_multiplier,
        lead_time_multiplier,
    )
    # One setup feeds both consumers: the spend view (through the
    # projection's groups and the setup's horizon buckets, setup[0])
    # and the planned-order list.  Each walks its own copy of the
    # review state, so the shared setup is never mutated.
    spend = _spend_view(projection._aggregate_projection_from_setup(setup), setup[0])
    orders = projection._planned_orders_from_setup(setup)
    month_by_label = {row["month"]: row for row in spend["months"]}
    deferral_months: list[dict] = []
    for row in spend["months"]:
        if not row["over_budget"]:
            continue
        budget = row["budget"]
        overrun = row["total_spend"] - budget
        candidates: list[dict] = []
        for order in orders:
            if order["arrival_month"] != row["month"]:
                continue
            d = order["d"]
            month_end_on_hand = order["month_end_on_hand"]
            # An order arriving in this month arrives within the
            # horizon, so its month_end_on_hand is not None.
            cover = round((month_end_on_hand - order["quantity"]) / d, 1) if d else None
            would_stock_out = (month_end_on_hand - order["quantity"]) < 0
            candidates.append(
                {
                    "item_code": order["item_code"],
                    "site_code": order["site_code"],
                    "quantity": order["quantity"],
                    "value": order["value"],
                    "cover_days_if_deferred": cover,
                    "would_stock_out": would_stock_out,
                }
            )
        candidates.sort(
            key=lambda candidate: (
                candidate["would_stock_out"],
                _cover_sort_key(candidate["cover_days_if_deferred"]),
                0 if candidate["would_stock_out"] else -candidate["value"],
            )
        )
        suggested_value = 0.0
        for candidate in candidates:
            suggested = (
                not candidate["would_stock_out"]
                and candidate["value"] > 0
                and suggested_value < overrun
            )
            if suggested:
                suggested_value += candidate["value"]
            candidate["suggested"] = suggested
            candidate["cumulative_suggested_value"] = suggested_value
        next_label = _next_month_label(row["month"])
        next_row = month_by_label.get(next_label)
        next_total = (
            None if next_row is None else next_row["total_spend"] + suggested_value
        )
        next_over = (
            next_total is not None
            and next_row["budget"] is not None
            and next_total > next_row["budget"]
        )
        deferral_months.append(
            {
                "month": row["month"],
                "overrun": overrun,
                "suggested_value": suggested_value,
                "uncovered": max(0.0, overrun - suggested_value),
                "next_month": next_label,
                "next_month_total_after": next_total,
                "next_month_over_after": next_over,
                "candidates": candidates,
            }
        )
    return {"months": deferral_months}
