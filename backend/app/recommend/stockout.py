"""Stockout risk assessment per item and site.

The pure function ``assess_stockout`` prices one item-site's stock
position (its ``on_hand`` quantity plus the ``open_supply_units`` of
open purchase supply) against demand during lead time: with ``mu =
d * lt`` and ``sigma = sqrt(lt * sd_d^2 + d^2 * sd_lt^2)`` it computes
the stockout probability ``1 - Phi((position - mu) / sigma)`` with
``statistics.NormalDist()`` and, when that probability exceeds the
acceptable level ``1 - service_level``, reports the expected units and
money at risk. ``stockout_ranked`` applies the assessment to every
``"ok"`` result of
``app.recommend.reorder.recommendations_for_all`` (each enriched with
the pair's ``on_hand`` quantity, the ``item_sites`` unit cost and the
open purchase lines due inside the lead time window) and ranks the
results by stockout probability.
"""

from __future__ import annotations

import math
import statistics
from datetime import date, timedelta
from decimal import ROUND_FLOOR, Decimal, localcontext

from app.db.connection import get_cursor
from app.recommend.reorder import recommendations_for_all
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.stats.demand import _shift_back, default_as_of

_COST_MISSING_NOTE = "unit cost missing"
_NO_ON_HAND_NOTE = "no on-hand record; treated as 0"
_NO_OPEN_SUPPLY_NOTE = "no open purchase orders due in the lead time"

#: Open purchase lines promised more than this many days before the
#: as-of are stale and excluded from the open supply.
_STALE_WINDOW_DAYS = 90

_on_hand_repo = OnHandPostgresRepository()
_item_site_repo = ItemSitePostgresRepository()
_open_po_repo = OpenPurchaseLinePostgresRepository()


def _floor_days(on_hand_units: float, d: float) -> int:
    """floor(on_hand_units / d) taken over the decimal values.

    Binary float division can land a last-ulp below an exact whole
    number of days (0.3 / 0.1 is 2.9999999999999996), which would pull
    the projected stockout date one day early; dividing the decimal
    representations at high precision keeps the floor exact (3).
    """
    with localcontext() as ctx:
        ctx.prec = 60
        quotient = Decimal(str(on_hand_units)) / Decimal(str(d))
    return int(quotient.to_integral_value(rounding=ROUND_FLOOR))


def _at_or_below_acceptable(p_stockout: float, service_level: float) -> bool:
    """True when p_stockout is at or below 1 - service_level.

    The comparison runs in exact decimal: 1 - service_level in binary
    float lands a last-ulp off the decimal threshold (1.0 - 0.9 is
    0.09999999999999998), which would let a p_stockout sitting exactly
    on the threshold slip through. p_stockout carries 4 decimals, so
    the decimal comparison is exact. Non-finite service levels fall
    back to the plain float comparison.
    """
    sl = float(service_level)
    if not math.isfinite(sl):
        return p_stockout <= 1.0 - sl
    return Decimal(str(p_stockout)) <= Decimal(1) - Decimal(str(sl))


def assess_stockout(
    on_hand_units,
    unit_cost,
    d,
    sd_d,
    lt,
    sd_lt,
    recommended_reorder_point_units,
    service_level,
    as_of,
    *,
    open_supply_units: float = 0,
) -> dict | None:
    """Assess one item-site's on hand for stockout risk.

    ``on_hand_units`` is the current on-hand quantity and
    ``open_supply_units`` the open purchase supply due within the lead
    time (0 when none); the stock position
    ``on_hand_units + open_supply_units`` is what is priced below.
    ``unit_cost`` the item-site's unit cost (None when unknown); ``d``
    the mean daily demand, ``sd_d`` its standard deviation; ``lt`` the
    mean lead time in days, ``sd_lt`` its standard deviation;
    ``recommended_reorder_point_units`` the ``"ok"`` recommended
    reorder point in whole units (carried through, None when there is
    no such recommendation); ``service_level`` the target service level
    and ``as_of`` the assessment date.

    With ``mu = d * lt`` (the mean demand during lead time) and
    ``sigma = sqrt(lt * sd_d^2 + d^2 * sd_lt^2)`` its standard
    deviation, ``p_stockout`` is ``1 - Phi((position - mu) / sigma)``
    computed with ``statistics.NormalDist()``, rounded to 4 decimals
    (with ``sigma`` 0 it is 1.0 when the position is below ``mu``,
    else 0.0). Returns None when ``p_stockout`` is at or below the
    acceptable level ``1 - service_level`` (compared in exact
    decimal).

    Otherwise the row is ``"likely"`` when ``p_stockout`` exceeds 0.5
    and ``"at_risk"`` otherwise, and carries ``on_hand_units``,
    ``open_supply_units``, ``unit_cost``, ``lead_time_days`` (``lt``,
    rounded to 2 decimals), ``lead_time_demand`` (``mu``, rounded to 2
    decimals), ``p_stockout``, ``days_of_cover`` (position / ``d``
    rounded to 1 decimal, None when ``d`` is 0),
    ``projected_stockout_on`` (``as_of`` plus floor (position / ``d``)
    days, the floor taken over the decimal values so an exact whole day
    of cover is not lost to binary float error, None when ``d`` is 0),
    ``recommended_reorder_point_units``, ``expected_units_short``
    (``sigma * (pdf(z) - z * (1 - Phi(z)))`` with ``z = (position -
    mu) / sigma``, rounded to 2 decimals; max(0, mu - position) when
    ``sigma`` is 0) and ``money_at_risk`` (``expected_units_short``
    times ``unit_cost``, rounded to 2 decimals; None with the note
    ``"unit cost missing"`` when ``unit_cost`` is None). Every row
    carries ``as_of``, a ``notes`` list (``"unit cost missing"`` when
    the cost is None) and an ``arithmetic`` string writing ``mu``,
    ``sigma`` and ``p_stockout`` with the values substituted.
    """
    d = float(d)
    sd_d = float(sd_d)
    lt = float(lt)
    sd_lt = float(sd_lt)
    on_hand_units = float(on_hand_units)
    open_supply_units = float(open_supply_units)
    position = on_hand_units + open_supply_units

    mu = d * lt
    sigma = math.sqrt(lt * sd_d * sd_d + d * d * sd_lt * sd_lt)
    if sigma > 0:
        z = (position - mu) / sigma
        normal = statistics.NormalDist()
        p_raw = 1.0 - normal.cdf(z)
        expected_units_short = sigma * (normal.pdf(z) - z * (1.0 - normal.cdf(z)))
    else:
        z = None
        p_raw = 1.0 if position < mu else 0.0
        expected_units_short = max(0.0, mu - position)

    p_stockout = round(p_raw, 4)
    if _at_or_below_acceptable(p_stockout, service_level):
        return None

    if d == 0:
        days_of_cover = None
        projected_stockout_on = None
    else:
        days_of_cover = round(position / d, 1)
        projected_stockout_on = as_of + timedelta(days=_floor_days(position, d))

    expected_units_short = round(expected_units_short, 2)
    notes: list[str] = []
    if unit_cost is None:
        money_at_risk = None
        notes.append(_COST_MISSING_NOTE)
    else:
        money_at_risk = round(expected_units_short * float(unit_cost), 2)

    if z is not None:
        p_term = (
            "p_stockout = 1 - Phi((position - mu) / sigma) "
            f"= 1 - Phi(({position:g} - {mu:g}) / {sigma:g}) "
            f"= {p_stockout:g}"
        )
    else:
        p_term = (
            "p_stockout = 1 if position < mu else 0 (sigma = 0) "
            f"= {p_stockout:g} ({position:g} "
            f"{'<' if position < mu else '>='} {mu:g})"
        )
    arithmetic = (
        f"mu = d * lt = {d:g} * {lt:g} = {mu:g}; "
        "sigma = sqrt(lt * sd_d^2 + d^2 * sd_lt^2) "
        f"= sqrt({lt:g} * {sd_d:g}^2 + {d:g}^2 * {sd_lt:g}^2) = {sigma:g}; "
        f"{p_term}"
    )

    return {
        "category": "likely" if p_stockout > 0.5 else "at_risk",
        "on_hand_units": on_hand_units,
        "open_supply_units": open_supply_units,
        "unit_cost": unit_cost,
        "lead_time_days": round(lt, 2),
        "lead_time_demand": round(mu, 2),
        "p_stockout": p_stockout,
        "days_of_cover": days_of_cover,
        "projected_stockout_on": projected_stockout_on,
        "recommended_reorder_point_units": recommended_reorder_point_units,
        "expected_units_short": expected_units_short,
        "money_at_risk": money_at_risk,
        "as_of": as_of,
        "notes": notes,
        "arithmetic": arithmetic,
    }


def _item_sites_by_key() -> dict[tuple[str, str], dict]:
    """Every item_sites row keyed by (item_code, site_code)."""
    return {
        (row["item_code"], row["site_code"]): row
        for row in _item_site_repo.list_rows(10**9, 0)
    }


def _on_hand_by_key() -> dict[tuple[str, str], float]:
    """The quantity_on_hand of every on_hand row keyed by its pair."""
    return {
        (row["item_code"], row["site_code"]): row["quantity_on_hand"]
        for row in _on_hand_repo.list_rows(10**9, 0)
    }


def _open_supply_for(
    lines: list[dict], as_of: date, lead_time_days: float
) -> tuple[float, date | None, list[str]]:
    """The open purchase supply of one item-site due in the lead time.

    Sums the ``open_units`` of the lines with ``open_units`` above 0
    whose ``promised_on`` is not None, not more than 90 days before
    ``as_of`` and not after ``as_of`` plus the lead time in days
    (rounded down). Returns the sum, the latest counted ``promised_on``
    (None when nothing counts) and the notes: the
    ``"open purchase orders: <units> units due by <date>"`` note (or
    ``"no open purchase orders due in the lead time"`` when nothing
    counts) followed, when the counts are above 0, by the ``"<n>
    stale open purchase lines excluded"`` and ``"<n> undated open
    purchase lines excluded"`` notes.
    """
    lower = _shift_back(as_of, _STALE_WINDOW_DAYS)
    try:
        upper = as_of + timedelta(days=math.floor(lead_time_days))
    except OverflowError:
        upper = date.max
    units = 0.0
    due_by: date | None = None
    stale = 0
    undated = 0
    for line in lines:
        open_units = float(line["open_units"])
        if open_units <= 0:
            continue
        promised_on = line["promised_on"]
        if promised_on is None:
            undated += 1
        elif promised_on < lower:
            stale += 1
        elif promised_on <= upper:
            units += open_units
            if due_by is None or promised_on > due_by:
                due_by = promised_on
    if due_by is None:
        notes: list[str] = [_NO_OPEN_SUPPLY_NOTE]
    else:
        notes = [f"open purchase orders: {units:g} units due by {due_by.isoformat()}"]
    if stale > 0:
        notes.append(f"{stale} stale open purchase lines excluded")
    if undated > 0:
        notes.append(f"{undated} undated open purchase lines excluded")
    return units, due_by, notes


def _rank_sort_key(row: dict):
    """p_stockout descending, then money_at_risk descending with None
    last, then item_code, then site_code."""
    money = row["money_at_risk"]
    if money is not None:
        return (-row["p_stockout"], 0, -money, row["item_code"], row["site_code"])
    return (-row["p_stockout"], 1, 0.0, row["item_code"], row["site_code"])


def stockout_ranked(as_of: date | None = None) -> list[dict]:
    """Ranked stockout-risk rows for every "ok" recommendation.

    Takes every ``"ok"`` row of
    ``app.recommend.reorder.recommendations_for_all(as_of)``, reads
    ``d``, ``sd_d``, ``lt`` and ``sd_lt`` and ``service_level`` from
    the row's ``inputs`` and ``reorder_point_units`` from the row, and
    enriches each with the pair's ``quantity_on_hand`` from ``on_hand``
    (0 with the note ``"no on-hand record; treated as 0"`` when the
    pair has none), the ``item_sites`` unit cost and the open purchase
    supply of the pair's ``open_purchase_lines`` rows (read once from
    ``OpenPurchaseLinePostgresRepository.list_latest``): the lines with
    ``open_units`` above 0 due inside the window from 90 days before
    ``as_of`` through ``as_of`` plus the lead time in days (rounded
    down) count toward ``open_supply_units``, the latest counted
    ``promised_on`` becomes ``open_supply_due_by`` (None when nothing
    counts), and the row's notes gain the "due by" note (or
    ``"no open purchase orders due in the lead time"`` when nothing
    counts) plus the stale and undated exclusion notes when those
    counts are above 0. ``as_of`` is resolved exactly like
    ``excess_ranked`` when it is None
    (``app.stats.demand.default_as_of``).

    Returns the non-None ``assess_stockout`` rows with the
    recommendation's own ``notes`` appended to the row's notes and with
    ``item_code``, ``site_code`` and a 1-based ``rank`` added, ordered
    by ``p_stockout`` descending, then ``money_at_risk`` descending
    with None last, then ``item_code``, then ``site_code``.
    """
    if as_of is None:
        with get_cursor() as cur:
            as_of = default_as_of(cur)
    on_hand_by_key = _on_hand_by_key()
    item_site_rows = _item_sites_by_key()
    lines_by_key: dict[tuple[str, str], list[dict]] = {}
    for line in _open_po_repo.list_latest():
        lines_by_key.setdefault((line["item_code"], line["site_code"]), []).append(line)

    results: list[dict] = []
    for recommendation in recommendations_for_all(as_of):
        if recommendation["status"] != "ok":
            continue
        key = (recommendation["item_code"], recommendation["site_code"])
        quantity = on_hand_by_key.get(key)
        extra_notes: list[str] = []
        if quantity is None:
            quantity = 0.0
            extra_notes.append(_NO_ON_HAND_NOTE)
        item_site = item_site_rows.get(key)
        inputs = recommendation["inputs"]
        open_supply_units, open_supply_due_by, open_supply_notes = _open_supply_for(
            lines_by_key.get(key, []), as_of, inputs["lt"]
        )
        assessed = assess_stockout(
            quantity,
            item_site["unit_cost"] if item_site is not None else None,
            inputs["d"],
            inputs["sd_d"],
            inputs["lt"],
            inputs["sd_lt"],
            recommendation["reorder_point_units"],
            inputs["service_level"],
            as_of,
            open_supply_units=open_supply_units,
        )
        if assessed is None:
            continue
        assessed["open_supply_due_by"] = open_supply_due_by
        assessed["notes"] = open_supply_notes + assessed["notes"]
        assessed["notes"].extend(extra_notes)
        assessed["notes"].extend(recommendation["notes"])
        assessed["item_code"] = recommendation["item_code"]
        assessed["site_code"] = recommendation["site_code"]
        results.append(assessed)

    results.sort(key=_rank_sort_key)
    for rank, row in enumerate(results, start=1):
        row["rank"] = rank
    return results
