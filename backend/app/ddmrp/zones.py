"""The pure DDMRP buffer-zone arithmetic.

No database access: ``buffer_zones`` works from one item-site's
average daily usage (ADU), its lead time in days, its order cycle in
days (or None), its Syntetos-Boylan demand class, the coefficient of
variation of its demand (or None) and the stored values of the
``ddmrp.*`` settings — a mapping of setting key to value, where a
missing key or a value the setting cannot hold (not a number, or
outside its bounds) falls back to the setting's registry default.

The red zone's base is the ADU times the lead time in days times the
lead-time factor of the lead-time category ("short" below 15 days,
"medium" for 15 to 45 days, "long" above); the red safety stock is
the base times the variability factor of the variability category
("high" for intermittent or lumpy demand, otherwise "low" below a
coefficient of variation of 0.5, "medium" for 0.5 to 1.0 inclusive,
"high" above, and "low" when the coefficient is null).  The yellow
zone is the ADU times the lead time in days; the green zone is the
largest of the ADU times the order cycle (left out when the order
cycle is null), the red base and the minimum order quantity (0: the
feed carries no minimum order quantity yet); and each zone top is
the ceiling, in whole units, of the sum of the zones up to it.  A
one-line ``arithmetic`` string shows the numbers behind each zone.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.settings.registry import get_setting
from app.units import ceil_units

# The feed carries no minimum order quantity yet: the green zone's
# third candidate is the constant zero.
MOQ: float = 0.0

ADU_WINDOW_KEY = "ddmrp.adu_window_days"

LEAD_TIME_FACTOR_KEYS = {
    "short": "ddmrp.lead_time_factor_short",
    "medium": "ddmrp.lead_time_factor_medium",
    "long": "ddmrp.lead_time_factor_long",
}

VARIABILITY_FACTOR_KEYS = {
    "low": "ddmrp.variability_factor_low",
    "medium": "ddmrp.variability_factor_medium",
    "high": "ddmrp.variability_factor_high",
}

# Every field ``buffer_zones`` returns, in order.
ZONE_FIELDS = (
    "lead_time_category",
    "lead_time_factor",
    "variability_category",
    "variability_factor",
    "red_base",
    "red_safety",
    "red",
    "yellow",
    "green",
    "top_of_red",
    "top_of_yellow",
    "top_of_green",
    "arithmetic",
)


def lead_time_category(dlt_days: float) -> str:
    """The lead-time category of a lead time in days: "short" below
    15, "medium" for 15 to 45 inclusive, "long" above."""
    if dlt_days < 15:
        return "short"
    if dlt_days <= 45:
        return "medium"
    return "long"


def variability_category(demand_class: str, cv: float | None) -> str:
    """The variability category of one item-site's demand: "high" for
    intermittent or lumpy demand; otherwise from the coefficient of
    variation — "low" below 0.5, "medium" for 0.5 to 1.0 inclusive,
    "high" above, and "low" when it is null."""
    if demand_class in ("intermittent", "lumpy"):
        return "high"
    if cv is None or cv < 0.5:
        return "low"
    if cv <= 1.0:
        return "medium"
    return "high"


def setting_value(settings: Mapping[str, Any] | None, key: str) -> float:
    """The value of one registered number setting for a call: the
    value in *settings* when it is a number within the setting's
    bounds, else the registry default."""
    setting = get_setting(key)
    if setting is None or setting.type != "number":
        raise ValueError(f"setting {key!r} is not a registered number setting")
    value = (settings or {}).get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(setting.default)
    if not (setting.min <= value <= setting.max):
        return float(setting.default)
    return float(value)


def buffer_zones(
    adu: float,
    dlt_days: float,
    order_cycle_days: float | None,
    demand_class: str,
    cv: float | None,
    settings: Mapping[str, Any] | None = None,
) -> dict:
    """The DDMRP buffer zones of one item-site, from its inputs.

    ``adu`` is the average daily usage, ``dlt_days`` the lead time in
    days, ``order_cycle_days`` the item-site's order cycle in days
    (None when it has no recommended order quantity) and
    ``demand_class`` / ``cv`` its demand statistics' class and
    coefficient of variation (``cv`` None when it is null there).
    ``settings`` maps the ``ddmrp.*`` setting keys to their stored
    values (an empty mapping uses every registry default).  Returns
    one dict holding, under exactly the keys of ``ZONE_FIELDS``, the
    lead-time and variability categories and their factors, the red
    base (adu x dlt x lead-time factor), the red safety stock (the
    base x the variability factor), the red, yellow and green zones,
    the whole-unit tops (ceilings of red, red + yellow and
    red + yellow + green) and the one-line ``arithmetic`` string.
    """
    lead_category = lead_time_category(dlt_days)
    lead_factor = setting_value(settings, LEAD_TIME_FACTOR_KEYS[lead_category])
    variability = variability_category(demand_class, cv)
    variability_factor = setting_value(settings, VARIABILITY_FACTOR_KEYS[variability])

    red_base = adu * dlt_days * lead_factor
    red_safety = red_base * variability_factor
    red = red_base + red_safety
    yellow = adu * dlt_days
    green_candidates = [red_base, MOQ]
    if order_cycle_days is not None:
        green_candidates.append(adu * order_cycle_days)
    green = max(green_candidates)

    top_of_red = ceil_units(red)
    top_of_yellow = ceil_units(red + yellow)
    top_of_green = ceil_units(red + yellow + green)

    if order_cycle_days is not None:
        green_expression = (
            "max(adu * order_cycle_days, red_base, moq) "
            f"= max({adu:g} * {order_cycle_days:g}, {red_base:g}, {MOQ:g})"
        )
    else:
        green_expression = f"max(red_base, moq) = max({red_base:g}, {MOQ:g})"
    arithmetic = (
        "red_base = adu * dlt * lead_time_factor "
        f"= {adu:g} * {dlt_days:g} * {lead_factor:g} = {red_base:g}; "
        "red_safety = red_base * variability_factor "
        f"= {red_base:g} * {variability_factor:g} = {red_safety:g}; "
        "red = red_base + red_safety "
        f"= {red_base:g} + {red_safety:g} = {red:g}; "
        f"yellow = adu * dlt = {adu:g} * {dlt_days:g} = {yellow:g}; "
        f"green = {green_expression} = {green:g}; "
        f"top_of_red = ceil(red) = {top_of_red}; "
        f"top_of_yellow = ceil(red + yellow) = {top_of_yellow}; "
        "top_of_green = ceil(red + yellow + green) = "
        f"{top_of_green}"
    )

    return {
        "lead_time_category": lead_category,
        "lead_time_factor": lead_factor,
        "variability_category": variability,
        "variability_factor": variability_factor,
        "red_base": red_base,
        "red_safety": red_safety,
        "red": red,
        "yellow": yellow,
        "green": green,
        "top_of_red": top_of_red,
        "top_of_yellow": top_of_yellow,
        "top_of_green": top_of_green,
        "arithmetic": arithmetic,
    }
