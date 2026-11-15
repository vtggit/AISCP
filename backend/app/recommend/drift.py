"""Money-at-stake drift assessment for reorder points.

The pure function ``assess_drift`` compares one item-site's current
reorder point with one ``"ok"`` result of
``app.recommend.reorder.recommendations_for_all`` and prices the gap:
an over-protected point ties up excess stock (units_gap * unit_cost),
an under-protected one risks stockouts, priced with the normal loss
function over demand during lead time. ``drift_ranked`` applies the
assessment to every "ok" recommendation (each enriched with the
item-site's ``item_sites`` row) and ranks the results by money at
stake.
"""

from __future__ import annotations

import math
import statistics
from datetime import date

from app.recommend.reorder import recommendations_for_all
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

_NO_REORDER_POINT_NOTE = "no reorder point set"
_ORDER_QTY_NOTE = "order quantity assumed: 30 days of demand"
_CAP_NOTE = "capped at annual demand"
_COST_UNKNOWN_NOTE = "cost unknown"

_ASSUMED_ORDER_QTY_DAYS = 30
_DAYS_PER_YEAR = 365

_repo = ItemSitePostgresRepository()


def _other_basis_units(other_basis: dict | None) -> dict | None:
    """The other demand basis' whole-unit results, or null with the source.

    A drift row carries the recommendation's ``other_basis`` reduced to
    the fields a drift row reports in whole units — ``basis``,
    ``reorder_point_units`` and ``safety_stock_units`` — and is null
    when the recommendation's ``other_basis`` is.
    """
    if other_basis is None:
        return None
    return {
        "basis": other_basis["basis"],
        "reorder_point_units": other_basis["reorder_point_units"],
        "safety_stock_units": other_basis["safety_stock_units"],
    }


def _item_sites_by_key() -> dict[tuple[str, str], dict]:
    """Every item_sites row keyed by (item_code, site_code)."""
    return {
        (row["item_code"], row["site_code"]): row for row in _repo.list_rows(10**9, 0)
    }


def assess_drift(recommendation: dict, item_site: dict) -> dict:
    """Price the drift between a current and a recommended reorder point.

    ``recommendation`` is one "ok" result of
    ``app.recommend.reorder.recommendations_for_all`` (only its
    ``reorder_point`` and the ``inputs`` ``d``, ``sd_d``, ``lt`` and
    ``sd_lt`` are read). ``item_site`` holds the item-site's current
    ``reorder_point``, ``unit_cost`` and ``order_qty`` from
    ``item_sites``; any of the three may be None.

    The current reorder point is ``item_site["reorder_point"]``,
    treated as 0 with the note ``"no reorder point set"`` when it is
    None or not positive. ``units_gap`` is current minus recommended
    and ``direction`` is ``"over"`` when the current point exceeds the
    recommended one, else ``"under"``.

    An ``"over"`` point ties up excess stock: ``money_at_stake`` is
    ``units_gap * unit_cost``. An ``"under"`` point risks shortage:
    with ``mu_l`` = d * lt and ``sigma_l`` = sqrt(lt * sd_d^2 +
    d^2 * sd_lt^2), ``k`` = (current - mu_l) / sigma_l and
    ``units_short_per_cycle`` is the normal loss
    ``sigma_l * (pdf(k) - k * (1 - cdf(k)))`` computed with
    ``statistics.NormalDist()`` (or max(0, mu_l - current) when
    sigma_l is 0). ``order_qty`` is the item-site's when positive,
    else 30 days of demand with the note ``"order quantity assumed:
    30 days of demand"``; ``cycles_per_year`` is 365 * d / order_qty
    (0 when order_qty is not positive — zero daily demand with no
    order quantity set) and ``units_short_per_year`` the per-cycle
    shortage times the
    cycles, capped at the annual demand 365 * d (you cannot be short
    more units than customers asked for) with the note ``"capped at
    annual demand"``; ``money_at_stake`` is
    ``units_short_per_year * unit_cost``.

    When ``unit_cost`` is None or not positive ``money_at_stake`` is
    None and the note ``"cost unknown"`` is added. Returns
    ``direction``, ``units_gap``, ``money_at_stake``, ``unit_cost``,
    ``notes``, a ``money_basis`` string writing the applicable formula
    with the values substituted and the result, and, for ``"under"``,
    ``mu_l``, ``sigma_l``, ``k``, ``units_short_per_cycle``,
    ``order_qty``, ``cycles_per_year`` and ``units_short_per_year``.
    """
    recommended = float(recommendation["reorder_point"])
    inputs = recommendation["inputs"]
    d = float(inputs["d"])
    sd_d = float(inputs["sd_d"])
    lt = float(inputs["lt"])
    sd_lt = float(inputs["sd_lt"])

    notes: list[str] = []

    current = item_site["reorder_point"]
    if current is None or current <= 0:
        current = 0.0
        notes.append(_NO_REORDER_POINT_NOTE)

    unit_cost = item_site["unit_cost"]
    if unit_cost is not None and unit_cost <= 0:
        unit_cost = None

    units_gap = current - recommended
    direction = "over" if current > recommended else "under"

    if direction == "over":
        result = {
            "direction": direction,
            "units_gap": units_gap,
            "unit_cost": unit_cost,
        }
        if unit_cost is None:
            result["money_at_stake"] = None
            notes.append(_COST_UNKNOWN_NOTE)
            money_basis = (
                "money_at_stake = units_gap * unit_cost "
                f"= {units_gap:.2f} * unknown cost = unknown"
            )
        else:
            money = units_gap * unit_cost
            result["money_at_stake"] = money
            money_basis = (
                "money_at_stake = units_gap * unit_cost "
                f"= {units_gap:.2f} * {unit_cost:g} = {money:.2f}"
            )
        result["notes"] = notes
        result["money_basis"] = money_basis
        return result

    mu_l = d * lt
    sigma_l = math.sqrt(lt * sd_d * sd_d + d * d * sd_lt * sd_lt)
    if sigma_l > 0:
        k = (current - mu_l) / sigma_l
        normal = statistics.NormalDist()
        units_short_per_cycle = sigma_l * (normal.pdf(k) - k * (1.0 - normal.cdf(k)))
    else:
        k = None
        units_short_per_cycle = max(0.0, mu_l - current)

    order_qty = item_site["order_qty"]
    if order_qty is None or order_qty <= 0:
        order_qty = _ASSUMED_ORDER_QTY_DAYS * d
        notes.append(_ORDER_QTY_NOTE)

    cycles_per_year = _DAYS_PER_YEAR * d / order_qty if order_qty > 0 else 0.0
    units_short_per_year = units_short_per_cycle * cycles_per_year
    annual_demand = _DAYS_PER_YEAR * d
    if units_short_per_year > annual_demand:
        units_short_per_year = annual_demand
        notes.append(_CAP_NOTE)

    result = {
        "direction": direction,
        "units_gap": units_gap,
        "unit_cost": unit_cost,
        "mu_l": mu_l,
        "sigma_l": sigma_l,
        "k": k,
        "units_short_per_cycle": units_short_per_cycle,
        "order_qty": order_qty,
        "cycles_per_year": cycles_per_year,
        "units_short_per_year": units_short_per_year,
    }
    if unit_cost is None:
        result["money_at_stake"] = None
        notes.append(_COST_UNKNOWN_NOTE)
        money_basis = (
            "money_at_stake = units_short_per_year * unit_cost "
            f"= {units_short_per_year:.2f} * unknown cost = unknown"
        )
    else:
        money = units_short_per_year * unit_cost
        result["money_at_stake"] = money
        money_basis = (
            "money_at_stake = units_short_per_year * unit_cost "
            f"= {units_short_per_year:.2f} * {unit_cost:g} = {money:.2f}"
        )
    result["notes"] = notes
    result["money_basis"] = money_basis
    return result


def _rank_sort_key(row: dict):
    """Every "drift" row before every "no_reorder_point" row; within each
    category money_at_stake descending with unknown money last (ordered
    by the absolute units_gap, descending)."""
    group = 0 if row["category"] == "drift" else 1
    money = row["money_at_stake"]
    if money is not None:
        return (group, 0, -money, row["item_code"], row["site_code"])
    return (group, 1, -abs(row["units_gap"]), row["item_code"], row["site_code"])


def drift_ranked(as_of: date | None = None) -> list[dict]:
    """Ranked drift rows for every item-site with an "ok" recommendation.

    Takes the "ok" results of ``recommendations_for_all(as_of)`` and
    applies ``assess_drift`` with each item-site's ``item_sites`` row
    (a missing row means all three values are None). Returns one result
    per item-site with ``item_code``, ``site_code``, ``as_of``, the
    recommendation's ``reorder_point``, ``reorder_point_units``,
    ``safety_stock``, ``arithmetic``, ``demand_basis``,
    ``other_basis`` (the recommendation's ``other_basis`` reduced to
    exactly its ``basis``, ``reorder_point_units`` and
    ``safety_stock_units``, or null when the recommendation's is),
    ``order_quantity`` (the recommendation's ``order_quantity``,
    null when the recommendation's is), ``service_level_source``
    (the recommendation's ``service_level_source``, null when the
    recommendation's is) and ``service_level`` (the recommendation's
    ``inputs["service_level"]``, null when the recommendation's
    ``inputs`` is absent or carries no ``service_level``), the current
    reorder point,
    every ``assess_drift`` field and ``rank``.
    Each result carries
    ``category``: ``"no_reorder_point"`` when the current reorder point
    is None or not positive (such items are often intentionally not
    stocked, e.g. made to order, and are listed for confirmation rather
    than ranked), else ``"drift"``. Results are ordered with every
    ``"drift"`` row before every ``"no_reorder_point"`` row; within
    each category by ``money_at_stake`` descending, with rows whose
    ``money_at_stake`` is None last (ordered by the absolute
    ``units_gap``, descending). ``rank`` is 1, 2, ... over the
    ``"drift"`` rows with a known ``money_at_stake``, and None for
    every other row.
    """
    item_site_rows = _item_sites_by_key()
    results: list[dict] = []
    for recommendation in recommendations_for_all(as_of):
        if recommendation["status"] != "ok":
            continue
        row = item_site_rows.get(
            (recommendation["item_code"], recommendation["site_code"])
        )
        current_reorder_point = row["reorder_point"] if row is not None else None
        item_site = {
            "reorder_point": current_reorder_point,
            "unit_cost": row["unit_cost"] if row is not None else None,
            "order_qty": row["order_qty"] if row is not None else None,
        }
        category = (
            "no_reorder_point"
            if current_reorder_point is None or current_reorder_point <= 0
            else "drift"
        )
        results.append(
            {
                "item_code": recommendation["item_code"],
                "site_code": recommendation["site_code"],
                "as_of": recommendation["as_of"],
                "reorder_point": recommendation["reorder_point"],
                "reorder_point_units": recommendation["reorder_point_units"],
                "safety_stock": recommendation["safety_stock"],
                "arithmetic": recommendation["arithmetic"],
                "demand_basis": recommendation.get("demand_basis"),
                "other_basis": _other_basis_units(recommendation.get("other_basis")),
                "order_quantity": recommendation.get("order_quantity"),
                "service_level_source": recommendation.get("service_level_source"),
                "service_level": (recommendation.get("inputs") or {}).get(
                    "service_level"
                ),
                "current_reorder_point": current_reorder_point,
                "category": category,
                **assess_drift(recommendation, item_site),
            }
        )
    results.sort(key=_rank_sort_key)
    rank = 0
    for row in results:
        if row["category"] == "drift" and row["money_at_stake"] is not None:
            rank += 1
            row["rank"] = rank
        else:
            row["rank"] = None
    return results
