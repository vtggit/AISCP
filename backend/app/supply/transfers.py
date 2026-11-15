"""Transfer suggestions: moving stock between item-sites of one item.

The pure function ``suggest_transfers`` takes the rows of
``app.recommend.reorder.recommendations_for_all`` and the
``(item_code, site_code)`` to ``source_system`` mapping the item_sites
read gives, and returns the inter-site transfer suggestions.

Per item-site, from the rows with status "ok" and a numeric
``order_quantity.order_quantity_units``: the position is on hand plus
on order; a site NEEDS when its reorder point stands above the
position (the difference, in whole units) and HOLDS when the position
stands above the reorder point plus one order quantity (the excess, in
whole units) — a site only gives what it holds beyond its reorder
point plus one order quantity.  Per item, across its item-sites of the
SAME ``source_system`` (a pair the item_sites read does not name has no
source system and never matches): each site with a need is matched
with the sites that have a surplus — the largest need is served first,
from the largest surplus first; each match suggests the smaller of the
remaining need and the remaining surplus, in whole units, and both
remainders are reduced by it; matching stops when either side is
exhausted; a site never gives to itself.

Each suggestion carries ``item_code``, ``source_system``,
``from_site``, ``to_site``, ``quantity``, ``unit_cost`` (the receiving
item-site's), ``value`` (the quantity times the unit cost to cents,
None without a cost), ``to_site_need``, ``from_site_surplus`` and
``arithmetic`` — one line of text, e.g. "site 159 needs 120 (ROP 400
\u2212 position 280); site 154 holds 300 beyond ROP + order quantity;
move 120".
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal


def _number(value) -> float | None:
    """A finite real number as a float, else None.

    None, bools, non-numeric values and non-finite numbers are not
    numbers — stored data the route cannot hold is skipped, never a 500.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def _whole_units(value: float) -> int:
    """The whole units in a non-negative quantity, tolerant of float
    noise: a value within 1e-9 of an integer is that integer, every
    other value floors to the unit below."""
    return math.floor(value + 1e-9)


def _format_number(value: float | None) -> str:
    """A position value for the arithmetic line: whole numbers without
    a decimal point (280.0 reads "280", 2.5 reads "2.5")."""
    number = _number(value)
    if number is None:
        return "0"
    if number.is_integer():
        return str(int(number))
    return str(number)


def _site_position(recommendation) -> dict | None:
    """One item-site's position against its reorder point and order
    quantity, or None when the row cannot hold a transfer: not a
    mapping, not status "ok", a pair that is not two strings, no
    numeric ``order_quantity.order_quantity_units``, or a position the
    route cannot hold (non-numeric on hand, on order or reorder
    point).

    ``need`` is the reorder point above the position, in whole units
    (0 when the position meets or beats the point); ``surplus`` is the
    position above the reorder point plus one order quantity, in whole
    units (0 when it does not stand above them) — a site only gives
    what it holds beyond its reorder point plus one order quantity.
    """
    if not isinstance(recommendation, dict) or recommendation.get("status") != "ok":
        return None
    item_code = recommendation.get("item_code")
    site_code = recommendation.get("site_code")
    if not isinstance(item_code, str) or not isinstance(site_code, str):
        return None
    order_quantity = recommendation.get("order_quantity")
    order_units = (
        _number(order_quantity.get("order_quantity_units"))
        if isinstance(order_quantity, dict)
        else None
    )
    if order_units is None:
        return None
    on_hand = _number(recommendation.get("on_hand_units"))
    on_order = _number(recommendation.get("on_order_units"))
    reorder_point = _number(recommendation.get("reorder_point_units"))
    if on_hand is None or on_order is None or reorder_point is None:
        return None
    position = on_hand + on_order
    need = _whole_units(reorder_point - position) if reorder_point > position else 0
    surplus = (
        _whole_units(position - (reorder_point + order_units))
        if position > reorder_point + order_units
        else 0
    )
    return {
        "item_code": item_code,
        "site_code": site_code,
        "position": position,
        "reorder_point": reorder_point,
        "need": need,
        "surplus": surplus,
        "unit_cost": _number(recommendation.get("unit_cost")),
    }


def _arithmetic(receiver: dict, donor: dict, quantity: int) -> str:
    """The suggestion's one line of text: the receiving site's need
    with its reorder point and position, the giving site's surplus
    beyond reorder point plus order quantity, and the move — e.g.
    "site 159 needs 120 (ROP 400 \u2212 position 280); site 154 holds
    300 beyond ROP + order quantity; move 120"."""
    return (
        f"site {receiver['site_code']} needs {receiver['need']} "
        f"(ROP {_format_number(receiver['reorder_point'])} \u2212 position "
        f"{_format_number(receiver['position'])}); "
        f"site {donor['site_code']} holds {donor['surplus']} beyond ROP + "
        f"order quantity; move {quantity}"
    )


def suggest_transfers(
    recommendations: list[dict],
    source_system_by_pair: dict[tuple[str, str], str | None],
) -> list[dict]:
    """The inter-site transfer suggestions, in the order matched.

    ``recommendations`` are the rows of
    ``app.recommend.reorder.recommendations_for_all``;
    ``source_system_by_pair`` maps ``(item_code, site_code)`` to the
    item_sites row's ``source_system`` (a pair the read does not name
    maps to None, or is simply absent).  The match and the fields of
    each suggestion are the module's documented rule: per item, across
    the item-sites of the same source system, the largest need served
    first from the largest surplus first, each match the smaller of the
    two remainders in whole units, a site never giving to itself.
    """
    groups: dict[tuple[str, str], list[dict]] = {}
    for recommendation in recommendations:
        site = _site_position(recommendation)
        if site is None:
            continue
        source_system = source_system_by_pair.get(
            (site["item_code"], site["site_code"])
        )
        if source_system is None:
            continue
        groups.setdefault((site["item_code"], source_system), []).append(site)

    suggestions: list[dict] = []
    for (item_code, source_system), group in groups.items():
        receivers = sorted(
            (site for site in group if site["need"] > 0),
            key=lambda site: (-site["need"], site["site_code"]),
        )
        donors = [
            {**site, "remaining": site["surplus"]}
            for site in group
            if site["surplus"] > 0
        ]
        for receiver in receivers:
            remaining_need = receiver["need"]
            while remaining_need > 0:
                candidates = [
                    donor
                    for donor in donors
                    if donor["remaining"] > 0
                    and donor["site_code"] != receiver["site_code"]
                ]
                if not candidates:
                    break
                top = max(donor["remaining"] for donor in candidates)
                donor = min(
                    (donor for donor in candidates if donor["remaining"] == top),
                    key=lambda site: site["site_code"],
                )
                quantity = min(remaining_need, donor["remaining"])
                remaining_need -= quantity
                donor["remaining"] -= quantity
                unit_cost = receiver["unit_cost"]
                value = (
                    (Decimal(str(unit_cost)) * quantity).quantize(
                        Decimal("0.01"), rounding=ROUND_HALF_UP
                    )
                    if unit_cost is not None
                    else None
                )
                suggestions.append(
                    {
                        "item_code": item_code,
                        "source_system": source_system,
                        "from_site": donor["site_code"],
                        "to_site": receiver["site_code"],
                        "quantity": quantity,
                        "unit_cost": unit_cost,
                        "value": value,
                        "to_site_need": receiver["need"],
                        "from_site_surplus": donor["surplus"],
                        "arithmetic": _arithmetic(receiver, donor, quantity),
                    }
                )
    return suggestions
