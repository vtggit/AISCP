"""Excess and dead stock assessment per item and site.

The pure function ``assess_stock`` prices one item-site's on-hand
quantity against its recent demand. An item with no demand in the
look-back window ending at ``as_of`` is ``"dead"`` (``"never_moved"``
when it has no demand at all, else ``"stopped"``) with its whole on
hand in excess; an item holding more than its justified cover (the
in-window demand and the recommended reorder point) is ``"excess"``
with the surplus priced at the item-site's unit cost. ``excess_ranked``
applies the assessment to every positive ``on_hand`` row (enriched with
the ``item_sites`` unit cost, the pair's latest demand date and its
in-window demand quantity, and the ``"ok"`` recommended reorder point
from ``app.recommend.reorder.recommendations_for_all``) and ranks the
results by money tied up.
"""

from __future__ import annotations

import math
import os
from datetime import date, timedelta

from app.db.connection import get_cursor
from app.recommend.reorder import recommendations_for_all
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.stats.demand import default_as_of

EXCESS_COVER_MONTHS_ENV = "EXCESS_COVER_MONTHS"
DEFAULT_COVER_MONTHS = 12
_DAYS_PER_MONTH = 30

_COST_MISSING_NOTE = "unit cost missing"

_on_hand_repo = OnHandPostgresRepository()
_item_site_repo = ItemSitePostgresRepository()


def _coerce_cover_months(cover_months) -> int:
    """A positive integer cover in months, else the 12-month default.

    Unset, blank, non-numeric, zero, negative, fractional or non-finite
    values all fall back to ``DEFAULT_COVER_MONTHS``.
    """
    try:
        value = int(cover_months)
    except (TypeError, ValueError):
        return DEFAULT_COVER_MONTHS
    return value if value > 0 else DEFAULT_COVER_MONTHS


def cover_months_from_env() -> int:
    """Read the cover in months from ``EXCESS_COVER_MONTHS`` at call time.

    A positive integer is used as-is; anything else (unset, non-numeric,
    zero, negative or fractional) falls back to 12.
    """
    return _coerce_cover_months(os.environ.get(EXCESS_COVER_MONTHS_ENV))


def _as_float(value):
    """The value as a finite float, or None when it is not a real number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _window_start(as_of: date, cover_months: int) -> date:
    """``as_of`` minus ``cover_months * 30`` days, clamped to ``date.min``."""
    try:
        return as_of - timedelta(days=cover_months * _DAYS_PER_MONTH)
    except OverflowError:
        return date.min


def _money(units: float, cost: float):
    """``units * cost`` rounded to 2 places, or None when not finite."""
    value = units * cost
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, 2)


def assess_stock(
    on_hand_units,
    unit_cost,
    last_demand_on,
    demand_window_units,
    recommended_reorder_point_units,
    as_of,
    cover_months,
) -> dict | None:
    """Assess one item-site's on hand against its recent demand.

    ``on_hand_units`` is the current on-hand quantity; ``unit_cost`` the
    item-site's unit cost (None when unknown); ``last_demand_on`` the
    pair's latest ``demand_events.occurred_on`` (None when it has never
    been demanded); ``demand_window_units`` the demand quantity in the
    cover window; ``recommended_reorder_point_units`` the ``"ok"``
    recommended reorder point in whole units (None when there is no
    such recommendation); ``as_of`` the assessment date and
    ``cover_months`` the look-back in months (a non-positive or
    non-numeric value is treated as the 12-month default).

    Returns None when ``on_hand_units`` is not greater than 0, or when
    the on hand fits the justified cover. The cover window is the
    ``cover_months * 30`` days ending at ``as_of`` (start exclusive).
    With no demand after the window start and on or before ``as_of``
    the row is ``"dead"``: ``dead_reason`` is ``"never_moved"`` when
    ``last_demand_on`` is None and ``"stopped"`` otherwise,
    ``justified_units`` is 0 and ``excess_units`` is the whole on hand.
    Otherwise ``justified_units`` is max(ceil(demand_window_units),
    recommended_reorder_point_units or 0) and the row is ``"excess"``
    with ``excess_units`` = on_hand - justified (only when positive).

    Every row carries ``on_hand_units``, ``unit_cost``,
    ``last_demand_on``, ``demand_window_units``, ``months_of_cover``
    (on hand over the average monthly window demand, rounded to 1
    decimal; None when the window demand is 0), ``justified_units``,
    ``excess_units``, ``money_tied_up`` (excess units times unit cost,
    rounded to 2 decimals; None with the note ``"unit cost missing"``
    when the unit cost is None), ``as_of``, a ``notes`` list, and an
    ``arithmetic`` string writing the justified and excess formulas
    with the actual numbers.
    """
    on_hand = _as_float(on_hand_units)
    if on_hand is None or on_hand <= 0 or not isinstance(as_of, date):
        return None

    cover_months = _coerce_cover_months(cover_months)
    if last_demand_on is not None and not isinstance(last_demand_on, date):
        last_demand_on = None

    window_start = _window_start(as_of, cover_months)
    window_units = _as_float(demand_window_units) or 0.0
    reorder_point_units = _as_float(recommended_reorder_point_units) or 0.0

    has_window_demand = window_units > 0 or (
        last_demand_on is not None and window_start < last_demand_on <= as_of
    )

    notes: list[str] = []
    cost = _as_float(unit_cost)
    if cost is None:
        notes.append(_COST_MISSING_NOTE)

    if not has_window_demand:
        return {
            "category": "dead",
            "dead_reason": "never_moved" if last_demand_on is None else "stopped",
            "on_hand_units": on_hand_units,
            "unit_cost": unit_cost,
            "last_demand_on": last_demand_on,
            "demand_window_units": demand_window_units,
            "months_of_cover": None,
            "justified_units": 0,
            "excess_units": on_hand,
            "money_tied_up": _money(on_hand, cost) if cost is not None else None,
            "as_of": as_of,
            "notes": notes,
            "arithmetic": (
                f"no demand after {window_start} up to and including {as_of}: "
                f"justified_units = 0; "
                f"excess_units = on_hand_units = {on_hand:g}"
            ),
        }

    justified_units = max(math.ceil(window_units), reorder_point_units)
    excess_units = on_hand - justified_units
    if excess_units <= 0:
        return None

    months_of_cover = None
    if window_units > 0:
        months = on_hand * cover_months / window_units
        if math.isfinite(months):
            months_of_cover = round(months, 1)

    return {
        "category": "excess",
        "on_hand_units": on_hand_units,
        "unit_cost": unit_cost,
        "last_demand_on": last_demand_on,
        "demand_window_units": demand_window_units,
        "months_of_cover": months_of_cover,
        "justified_units": justified_units,
        "excess_units": excess_units,
        "money_tied_up": _money(excess_units, cost) if cost is not None else None,
        "as_of": as_of,
        "notes": notes,
        "arithmetic": (
            "justified_units = max(ceil(demand_window_units), "
            "recommended_reorder_point_units or 0) "
            f"= max(ceil({window_units:g}), {reorder_point_units:g}) "
            f"= {justified_units:g}; "
            "excess_units = on_hand_units - justified_units "
            f"= {on_hand:g} - {justified_units:g} = {excess_units:g}"
        ),
    }


def _item_sites_by_key() -> dict[tuple[str, str], dict]:
    """Every item_sites row keyed by (item_code, site_code)."""
    return {
        (row["item_code"], row["site_code"]): row
        for row in _item_site_repo.list_rows(10**9, 0)
    }


def _demand_by_pair(as_of: date, window_start: date):
    """Per (item_code, site_code): the latest occurred_on and the
    demand quantity in (window_start, as_of]."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT item_code, site_code, MAX(occurred_on) AS last_demand_on "
            "FROM demand_events "
            "GROUP BY item_code, site_code"
        )
        latest_by_pair = {
            (row["item_code"], row["site_code"]): row["last_demand_on"]
            for row in cur.fetchall()
        }
        cur.execute(
            "SELECT item_code, site_code, SUM(quantity) AS window_units "
            "FROM demand_events "
            "WHERE occurred_on > %s AND occurred_on <= %s "
            "GROUP BY item_code, site_code",
            (window_start, as_of),
        )
        window_by_pair = {
            (row["item_code"], row["site_code"]): float(row["window_units"] or 0.0)
            for row in cur.fetchall()
        }
    return latest_by_pair, window_by_pair


def _rank_sort_key(row: dict):
    """money_tied_up descending with unknown money last, then item_code,
    then site_code."""
    money = row["money_tied_up"]
    if money is not None:
        return (0, -money, row["item_code"], row["site_code"])
    return (1, 0.0, row["item_code"], row["site_code"])


def excess_ranked(as_of: date | None = None) -> list[dict]:
    """Ranked excess/dead rows for every positive on_hand row.

    Reads every ``on_hand`` row with ``quantity_on_hand`` > 0, joins
    ``item_sites`` for the unit cost, takes each pair's latest
    ``demand_events.occurred_on`` and its demand quantity in the cover
    window, and takes ``reorder_point_units`` from
    ``app.recommend.reorder.recommendations_for_all(as_of)`` where the
    status is ``"ok"``. The cover in months is read from
    ``EXCESS_COVER_MONTHS`` at call time (a positive integer, default
    12; anything else falls back to 12) and the window is the
    ``cover_months * 30`` days ending at ``as_of`` (start exclusive).
    ``as_of`` is resolved exactly like ``drift_ranked`` when it is None
    (``app.stats.demand.default_as_of``).

    Returns the non-None ``assess_stock`` rows with ``item_code``,
    ``site_code`` and a 1-based ``rank`` added, ordered by
    ``money_tied_up`` descending with None last, then ``item_code``,
    then ``site_code``.
    """
    cover_months = cover_months_from_env()
    if as_of is None:
        with get_cursor() as cur:
            as_of = default_as_of(cur)
    window_start = _window_start(as_of, cover_months)

    item_site_rows = _item_sites_by_key()
    latest_by_pair, window_by_pair = _demand_by_pair(as_of, window_start)
    ok_recommendations = {
        (row["item_code"], row["site_code"]): row
        for row in recommendations_for_all(as_of)
        if row["status"] == "ok"
    }

    results: list[dict] = []
    for row in _on_hand_repo.list_rows(10**9, 0):
        quantity = row["quantity_on_hand"]
        if quantity is None or not quantity > 0:
            continue
        key = (row["item_code"], row["site_code"])
        item_site = item_site_rows.get(key)
        recommendation = ok_recommendations.get(key)
        assessed = assess_stock(
            quantity,
            item_site["unit_cost"] if item_site is not None else None,
            latest_by_pair.get(key),
            window_by_pair.get(key, 0.0),
            (
                recommendation["reorder_point_units"]
                if recommendation is not None
                else None
            ),
            as_of,
            cover_months,
        )
        if assessed is None:
            continue
        assessed["item_code"] = row["item_code"]
        assessed["site_code"] = row["site_code"]
        results.append(assessed)

    results.sort(key=_rank_sort_key)
    for rank, row in enumerate(results, start=1):
        row["rank"] = rank
    return results
