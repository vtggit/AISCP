"""Order quantity recommendation for recommendable item-sites.

The pure function ``recommend_order_quantity`` turns one item-site's
daily demand rate into a recommended order quantity: the economic
order quantity ``sqrt(2 * 365 * d * order_cost / (holding_rate *
unit_cost))`` when the item-site's ``unit_cost`` is a positive number,
else 30 days of demand (``"assumed_30_days"``). The quantity is
clamped to the cover band of 7 to 365 days of demand — less than the
band means reordering too often, more than it means holding a year or
more of stock — and the whole-unit ceiling (at least 1 unit) covers
``cover_days`` = ``order_quantity_units / d`` days of demand.

The per-order cost and the annual holding rate come from the
``AIERP_ORDER_COST`` and ``AIERP_HOLDING_RATE`` environment variables,
read at call time by ``order_cost_from_env`` and
``holding_rate_from_env`` (defaults ``DEFAULT_ORDER_COST`` and
``DEFAULT_HOLDING_RATE`` when unset or unparsable). ``app.recommend.
reorder`` reads them once per call and attaches the result to every
recommendable recommendation under ``order_quantity``.
"""

from __future__ import annotations

import math
import os

from app.units import ceil_units

DEFAULT_ORDER_COST = 50.0
DEFAULT_HOLDING_RATE = 0.25
MIN_COVER_DAYS = 7
MAX_COVER_DAYS = 365
ASSUMED_COVER_DAYS = 30

ORDER_COST_ENV = "AIERP_ORDER_COST"
HOLDING_RATE_ENV = "AIERP_HOLDING_RATE"

BASIS_EOQ = "eoq"
BASIS_ASSUMED = "assumed_30_days"


def _positive_finite_number(value: object) -> float | None:
    """The value as a finite number > 0, else None.

    Non-numeric values (including None, control characters and NUL
    bytes smuggled in as strings) never raise — they simply are not a
    positive number.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def order_cost_from_env() -> float:
    """Read the per-order cost from ``AIERP_ORDER_COST`` at call time.

    ``float(AIERP_ORDER_COST)`` when it parses to a finite number
    greater than 0, else ``DEFAULT_ORDER_COST`` (the variable unset,
    empty, non-numeric, non-finite or not positive all fall back).
    """
    raw = os.environ.get(ORDER_COST_ENV)
    if raw is None:
        return DEFAULT_ORDER_COST
    value = _positive_finite_number(raw)
    return value if value is not None else DEFAULT_ORDER_COST


def holding_rate_from_env() -> float:
    """Read the annual holding rate from ``AIERP_HOLDING_RATE`` at call time.

    ``float(AIERP_HOLDING_RATE)`` when it parses to a finite number
    with 0 < x <= 1, else ``DEFAULT_HOLDING_RATE`` (the variable unset,
    empty, non-numeric, non-finite, 0 or above 1 all fall back).
    """
    raw = os.environ.get(HOLDING_RATE_ENV)
    if raw is None:
        return DEFAULT_HOLDING_RATE
    try:
        value = float(raw)
    except (ValueError, OverflowError):
        return DEFAULT_HOLDING_RATE
    if not math.isfinite(value) or not (0 < value <= 1):
        return DEFAULT_HOLDING_RATE
    return value


def recommend_order_quantity(
    d: float,
    unit_cost: float | None,
    order_cost: float,
    holding_rate: float,
) -> dict | None:
    """The recommended order quantity for one item-site's demand rate.

    ``d`` is the item-site's daily demand rate (the recommendation's
    ``inputs["d"]``), ``unit_cost`` the item-site's ``unit_cost`` from
    ``item_sites`` (None when absent), ``order_cost`` the per-order
    cost and ``holding_rate`` the annual holding rate. Returns None
    when ``d`` is not a finite number > 0 (a non-recommendable
    demand rate has no meaningful order quantity).

    Otherwise ``basis`` is ``"eoq"`` with ``q`` =
    ``sqrt(2 * 365 * d * order_cost / (holding_rate * unit_cost))``
    when ``unit_cost`` is a finite number > 0, else ``"assumed_30_days"``
    with ``q`` = ``30 * d``; ``q`` is clamped to ``[7 * d, 365 * d]``
    and ``clamped`` is ``"min_cover"``, ``"max_cover"`` or None.
    Returns exactly ``basis``, ``clamped``, ``order_quantity`` (the
    exact float after clamping), ``order_quantity_units`` (``max(1,
    ceil(order_quantity))``), ``cover_days`` (``order_quantity_units /
    d``), ``annual_demand`` (``365 * d``), ``order_cost``,
    ``holding_rate``, ``unit_cost`` (as given, None when absent) and
    ``arithmetic`` (the formula with the values substituted, in the
    style of ``app.recommend.reorder._basis_result``).
    """
    demand = _positive_finite_number(d)
    if demand is None:
        return None
    cost = _positive_finite_number(unit_cost)
    if cost is not None:
        basis = BASIS_EOQ
        quantity = math.sqrt(
            2 * MAX_COVER_DAYS * demand * order_cost / (holding_rate * cost)
        )
        formula = (
            "order_quantity = sqrt(2 * 365 * d * order_cost / "
            "(holding_rate * unit_cost)) "
            f"= sqrt(2 * 365 * {demand:g} * {order_cost:g} / "
            f"({holding_rate:g} * {cost:g}))"
        )
    else:
        basis = BASIS_ASSUMED
        quantity = ASSUMED_COVER_DAYS * demand
        formula = "order_quantity = 30 * d " f"= {ASSUMED_COVER_DAYS:g} * {demand:g}"
    clamped = None
    min_cover = MIN_COVER_DAYS * demand
    max_cover = MAX_COVER_DAYS * demand
    if quantity < min_cover:
        quantity = min_cover
        clamped = "min_cover"
    elif quantity > max_cover:
        quantity = max_cover
        clamped = "max_cover"
    arithmetic = f"{formula} = "
    if clamped is None:
        arithmetic += f"{quantity:.2f}"
    else:
        raw = (
            math.sqrt(2 * MAX_COVER_DAYS * demand * order_cost / (holding_rate * cost))
            if cost is not None
            else ASSUMED_COVER_DAYS * demand
        )
        arithmetic += f"{raw:.2f}, clamped to {clamped}: {quantity:.2f}"
    units = max(1, ceil_units(quantity))
    return {
        "basis": basis,
        "clamped": clamped,
        "order_quantity": quantity,
        "order_quantity_units": units,
        "cover_days": units / demand,
        "annual_demand": MAX_COVER_DAYS * demand,
        "order_cost": order_cost,
        "holding_rate": holding_rate,
        "unit_cost": unit_cost,
        "arithmetic": arithmetic,
    }
