"""Read-only what-if simulation of the reorder recommendations.

``simulate_reorder`` re-runs the reorder formula for every recommendable
item-site under one or more overridden assumptions (the "levers", all
optional — an omitted lever keeps the baseline value): a target
``service_level`` (a probability strictly between 0 and 1, used as
``z = NormalDist().inv_cdf(service_level)``), a ``demand_multiplier``
(a positive finite number scaling both the mean daily demand ``d`` and
its standard deviation ``sd_d``) and a ``lead_time_multiplier`` (a
positive finite number scaling both the mean lead time ``lt`` and its
standard deviation ``sd_lt``) and a ``service_level_by_class`` mapping (exactly the keys
``"A"``, ``"B"`` and ``"C"``, each a number strictly between 0 and 1):
when it is given, an item-site whose baseline
``service_level_source.abc_class`` is ``"A"``, ``"B"`` or ``"C"`` is
simulated at that class's level, and an unclassified item-site falls
back to the ``service_level`` lever when that is given, else the
baseline's ``inputs.service_level``.

The baseline is the item-site's current recommendation from
``app.recommend.reorder.recommendations_for_all``, pre-signal: the
recommendation's ``reorder_point`` and ``reorder_point_units`` with
the demand signals' share removed (the row's
``reorder_point_before_signals_units``), because the scenario side is
rebuilt from ``inputs`` and carries no signals — a stored signal
never changes any ``reorder_point_units_delta``,
``safety_stock_units_delta`` or ``dollar_impact``.  Only results
with ``status == "ok"`` are simulated — the same recommendable set.
The scenario is ``app.recommend.reorder._basis_result`` on the
scaled inputs, so the exact formula
``safety_stock = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2)`` and
``reorder_point = d * LT + safety_stock`` with the same whole-unit
ceiling rounding applies to baseline and scenario alike. A scenario is
never stored: the function reads through ``recommendations_for_all``
and ``item_sites`` and writes nothing.
"""

from __future__ import annotations

import math
import statistics
from datetime import date

from app.recommend.reorder import (
    _basis_result,
    recommendations_for_all,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

_repo = ItemSitePostgresRepository()

#: The three ABC class keys the ``service_level_by_class`` lever maps.
_CLASS_KEYS = frozenset({"A", "B", "C"})

#: The one error message of the ``service_level_by_class`` lever; it
#: names the lever for the API's 422 string detail.
_SERVICE_LEVEL_BY_CLASS_ERROR = (
    "service_level_by_class must map exactly the keys 'A', 'B' and 'C' "
    "to numbers strictly between 0 and 1"
)


def _validate_levers(
    service_level: float | None,
    demand_multiplier: float | None,
    lead_time_multiplier: float | None,
    service_level_by_class: object = None,
) -> None:
    """Raise ``ValueError`` on an out-of-range lever, before any computation.

    A given ``service_level`` must be a number strictly between 0 and 1;
    a given ``demand_multiplier`` or ``lead_time_multiplier`` must be a
    positive finite number; a given ``service_level_by_class`` must be
    a mapping of exactly the keys ``"A"``, ``"B"`` and ``"C"``, each to
    a number strictly between 0 and 1. Every lever is optional; ``None``
    means "not given".
    """
    if service_level is not None:
        if not isinstance(service_level, (int, float)):
            raise ValueError("service_level must be a number strictly between 0 and 1")
        if not (0.0 < float(service_level) < 1.0):
            raise ValueError("service_level must be a number strictly between 0 and 1")
    for name, value in (
        ("demand_multiplier", demand_multiplier),
        ("lead_time_multiplier", lead_time_multiplier),
    ):
        if value is None:
            continue
        if not isinstance(value, (int, float)):
            raise ValueError(f"{name} must be a positive finite number")
        if not math.isfinite(float(value)) or float(value) <= 0.0:
            raise ValueError(f"{name} must be a positive finite number")
    if service_level_by_class is not None:
        if not isinstance(service_level_by_class, dict):
            raise ValueError(_SERVICE_LEVEL_BY_CLASS_ERROR)
        if set(service_level_by_class) != _CLASS_KEYS:
            raise ValueError(_SERVICE_LEVEL_BY_CLASS_ERROR)
        for value in service_level_by_class.values():
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(_SERVICE_LEVEL_BY_CLASS_ERROR)
            if not (0.0 < float(value) < 1.0):
                raise ValueError(_SERVICE_LEVEL_BY_CLASS_ERROR)


def _basis_block(result: dict) -> dict:
    """The four reported values of one ``_basis_result`` result."""
    return {
        "reorder_point": result["reorder_point"],
        "reorder_point_units": result["reorder_point_units"],
        "safety_stock": result["safety_stock"],
        "safety_stock_units": result["safety_stock_units"],
    }


def simulate_reorder(
    service_level: float | None = None,
    demand_multiplier: float | None = None,
    lead_time_multiplier: float | None = None,
    service_level_by_class: dict[str, float] | None = None,
    as_of: date | None = None,
) -> dict:
    """Simulate the reorder recommendations under the given levers.

    Every item-site's baseline is its current recommendation from
    ``recommendations_for_all(as_of)``; only ``status == "ok"`` results
    are simulated (the same recommendable set). For each, the baseline's
    ``inputs`` are scaled — ``d' = d * demand_multiplier``,
    ``sd_d' = sd_d * demand_multiplier``, ``lt' = lt *
    lead_time_multiplier``, ``sd_lt' = sd_lt * lead_time_multiplier``
    (a multiplier left None is 1.0) — with the scenario service level
    ``sl'`` = the ``service_level_by_class`` level of the baseline's
    ``service_level_source.abc_class`` when that lever is given and the
    class is ``"A"``, ``"B"`` or ``"C"``, else ``service_level`` when
    given, else the baseline's ``inputs.service_level`` (an
    unclassified item-site with the per-class lever given falls back to
    the single lever, else the baseline), ``z' =
    NormalDist().inv_cdf(sl')`` and the scenario is
    ``_basis_result(d', sd_d', lt', sd_lt', z', sl')``.

    Returns ``{"summary", "item_sites"}``: one row per simulated
    item-site (``item_code``, ``site_code``, ``baseline`` and
    ``scenario`` each carrying ``reorder_point``,
    ``reorder_point_units``, ``safety_stock`` and
    ``safety_stock_units``, plus the signed
    ``reorder_point_units_delta`` and ``safety_stock_units_delta``,
    scenario minus baseline) ordered by ``item_code`` then ``site_code``
    as the recommendations are, and the portfolio ``summary``:
    ``item_sites`` (count simulated), the two delta totals,
    ``reorder_point_up`` / ``reorder_point_down`` /
    ``reorder_point_unchanged`` (item-site counts by the sign of
    ``reorder_point_units_delta``), ``dollar_impact`` (the sum over
    item-sites of ``reorder_point_units_delta`` times the item-site's
    ``unit_cost`` from ``item_sites``, item-sites without a ``unit_cost``
    contributing 0), the ``levers`` applied (each null when not given)
    and ``service_level_by_class`` (the given per-class mapping, null
    when not given).

    Raises ``ValueError`` before any computation when a given lever is
    out of range (see ``_validate_levers``), and when a scaled input
    overflows the formula. With no lever the scenario equals the
    baseline and every delta is 0. Nothing is written.
    """
    _validate_levers(
        service_level,
        demand_multiplier,
        lead_time_multiplier,
        service_level_by_class,
    )
    demand_factor = float(demand_multiplier) if demand_multiplier is not None else 1.0
    lead_factor = (
        float(lead_time_multiplier) if lead_time_multiplier is not None else 1.0
    )

    baselines = recommendations_for_all(as_of)
    item_sites = {
        (row["item_code"], row["site_code"]): row for row in _repo.list_rows(10**9, 0)
    }

    rows: list[dict] = []
    reorder_point_total = 0
    safety_stock_total = 0
    reorder_point_up = 0
    reorder_point_down = 0
    dollar_impact = 0.0
    without_unit_cost = 0
    for baseline in baselines:
        if baseline["status"] != "ok":
            continue
        item_code = baseline["item_code"]
        site_code = baseline["site_code"]
        inputs = baseline["inputs"]
        # The baseline the scenario compares against, pre-signal: the
        # scenario side is rebuilt from ``inputs`` and carries no
        # demand signals, so a stored signal must not change any
        # delta or the dollar impact.
        signal_units = float(baseline.get("signal_units") or 0.0)
        baseline_units = baseline.get(
            "reorder_point_before_signals_units",
            baseline["reorder_point_units"],
        )
        baseline_block = _basis_block(baseline)
        if signal_units:
            baseline_block["reorder_point"] = baseline["reorder_point"] - signal_units
            baseline_block["reorder_point_units"] = baseline_units
        scaled_d = float(inputs["d"]) * demand_factor
        scaled_sd_d = float(inputs["sd_d"]) * demand_factor
        scaled_lt = float(inputs["lt"]) * lead_factor
        scaled_sd_lt = float(inputs["sd_lt"]) * lead_factor
        abc_class = (baseline.get("service_level_source") or {}).get("abc_class")
        if service_level_by_class is not None and abc_class in service_level_by_class:
            # A classed item-site takes its class's level from the
            # per-class lever, whatever the single service_level lever
            # is.
            scenario_level = float(service_level_by_class[abc_class])
        else:
            # An unclassified item-site (or no per-class lever) keeps
            # today's rule: the single lever when given, else the
            # baseline's own service level.
            scenario_level = (
                float(service_level)
                if service_level is not None
                else float(inputs["service_level"])
            )
        z = statistics.NormalDist().inv_cdf(scenario_level)
        try:
            scenario = _basis_result(
                scaled_d, scaled_sd_d, scaled_lt, scaled_sd_lt, z, scenario_level
            )
        except (OverflowError, ValueError) as exc:
            # A hostile lever (e.g. a huge multiplier) overflows the
            # formula: report it as an out-of-range lever, never 500.
            raise ValueError(
                f"item site {item_code} at {site_code}: the scaled inputs "
                f"overflow the reorder formula"
            ) from exc
        reorder_point_delta = scenario["reorder_point_units"] - baseline_units
        safety_stock_delta = (
            scenario["safety_stock_units"] - baseline["safety_stock_units"]
        )
        reorder_point_total += reorder_point_delta
        safety_stock_total += safety_stock_delta
        if reorder_point_delta > 0:
            reorder_point_up += 1
        elif reorder_point_delta < 0:
            reorder_point_down += 1
        row = item_sites.get((item_code, site_code))
        unit_cost = row.get("unit_cost") if row is not None else None
        if unit_cost is None:
            without_unit_cost += 1
        else:
            dollar_impact += reorder_point_delta * float(unit_cost)
        rows.append(
            {
                "item_code": item_code,
                "site_code": site_code,
                "baseline": baseline_block,
                "scenario": _basis_block(scenario),
                "reorder_point_units_delta": reorder_point_delta,
                "safety_stock_units_delta": safety_stock_delta,
            }
        )

    summary = {
        "item_sites": len(rows),
        "reorder_point_units_delta_total": reorder_point_total,
        "safety_stock_units_delta_total": safety_stock_total,
        "reorder_point_up": reorder_point_up,
        "reorder_point_down": reorder_point_down,
        "reorder_point_unchanged": len(rows) - reorder_point_up - reorder_point_down,
        "dollar_impact": dollar_impact,
        "item_sites_without_unit_cost": without_unit_cost,
        "levers": {
            "service_level": service_level,
            "demand_multiplier": demand_multiplier,
            "lead_time_multiplier": lead_time_multiplier,
        },
        "service_level_by_class": service_level_by_class,
    }
    return {"summary": summary, "item_sites": rows}
