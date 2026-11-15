"""Release plan for the planned orders the baseline plan places in one month.

``plan_release(month)`` answers which of the baseline plan's planned
orders of ``month`` can go out as draft purchase orders in Acumatica,
grouped the way Acumatica takes them: one draft PO per
(site, supplier) pair.

``month`` is a ``YYYY-MM`` string and must be the current calendar
month or one of the next eleven; anything else — a malformed or
non-existent month, a past month, a month further ahead — raises
``ValueError`` (422 at the route).  The planned orders are the ones
``app.aggregate.projection.planned_orders`` places in that month with
no levers, over just enough months to include it (the month's offset
from the current month plus one), filtered on ``placed_month ==
month`` — orders the review places in later months are not part of
this release.

For every planned order's item-site the supplier comes from the
item-site's purchase-line history — every row of
``OpenPurchaseLinePostgresRepository.list_latest()``: the
``supplier_code`` of the line with the latest ``ordered_on`` (a line
with no ``ordered_on`` sorts before any dated one), ties broken by the
larger ``business_key``.  An item-site whose history has no lines — or
whose latest line carries no ``supplier_code`` — has no supplier, and
its orders go to ``unreleasable`` as
``{"item_code", "site_code", "reason": "no supplier in purchase
history"}``; they never enter a group.

The remaining orders are grouped one PO per ``(site_code,
supplier_code)``, the groups sorted by site then supplier and each
group's lines sorted by item code.  Each line is
``{"item_code", "quantity", "unit_cost", "value"}`` where
``quantity`` is the ordered whole units, ``unit_cost`` the planned
order's unit cost rounded half-up to two decimal places —
``Decimal.quantize(Decimal("0.01"), ROUND_HALF_UP)``, so 0.005
becomes 0.01 and never the banker's 0.00, and 0 when the order has
none — and ``value`` the quantity at that rounded unit cost.  The
rounded unit cost is the one sent to Acumatica and the one the
release audit records, so a released group's recorded value equals
the order's extended-cost total in the ERP.  Every monetary field is
an exact ``Decimal``, never a binary float: a unit cost that is already a ``Decimal``
passes through untouched, an ``int`` converts exactly, a ``float`` by
its shortest decimal spelling and a ``str`` by parsing — and a bool,
a non-finite number, a fractional quantity or a value that is not a
number at all raises ``ValueError`` (422 at the route) rather than
becoming an inexact number silently.  Each group carries its
``origin`` tag, ``"AIERP plan <month> site <site_code> vendor
<supplier_code>"``, which is the idempotency tag the draft PO is
written under in Acumatica, its summed ``value`` and its lines.  The
function returns ``{"month", "groups", "unreleasable",
"total_value"}`` — ``total_value`` the summed value of every line of
every group — and writes nothing.
"""

import math
import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext

from app.aggregate.projection import planned_orders
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)

_MONTH_RE = re.compile(r"([0-9]{4})-([0-9]{2})")
NO_SUPPLIER_REASON = "no supplier in purchase history"


def _parse_month(month: object) -> tuple[int, int]:
    """The (year, month) of a ``YYYY-MM`` string.

    Raises ``ValueError`` when the value is not a string of exactly
    four digits, a dash and two digits naming a real calendar month —
    so ``2026-13``, ``2026-0``, ``20261``, a blank or a
    control-character string all fail closed.
    """
    if not isinstance(month, str):
        raise ValueError("month must be a YYYY-MM calendar month")
    match = _MONTH_RE.fullmatch(month)
    if match is None:
        raise ValueError("month must be a YYYY-MM calendar month")
    year, month_number = int(match[1]), int(match[2])
    try:
        date(year, month_number, 1)
    except ValueError:
        raise ValueError("month must be a YYYY-MM calendar month") from None
    return year, month_number


def exact_decimal(value: object) -> Decimal:
    """The exact decimal of one monetary value.

    A ``Decimal`` passes through untouched (the shape the database
    hands back for a ``NUMERIC`` column), an ``int`` converts
    exactly, a ``float`` converts by its shortest decimal spelling —
    the decimal the float names, not its binary expansion — and a
    ``str`` parses as a decimal.  A bool, a non-finite number or a
    string that is not a decimal raises ``ValueError`` (422 at the
    route), so a malformed unit cost can never become an inexact or
    non-finite number silently.
    """
    if isinstance(value, bool):
        raise ValueError("a monetary value must be a number")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("a monetary value must be finite")
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("a monetary value must be finite")
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            parsed = Decimal(value.strip())
        except InvalidOperation:
            raise ValueError("a monetary value must be a number") from None
        if not parsed.is_finite():
            raise ValueError("a monetary value must be finite")
        return parsed
    raise ValueError("a monetary value must be a number")


_CENT = Decimal("0.01")


def round_half_up_cents(value: Decimal) -> Decimal:
    """A monetary amount rounded half-up to two decimal places.

    ``value.quantize(Decimal("0.01"), ROUND_HALF_UP)`` — 0.005
    becomes 0.01, never the banker's 0.00 — computed in a context
    precise enough for the result, so a huge finite amount rounds
    instead of raising ``InvalidOperation``.  A value already at two
    decimals or coarser is unchanged in value; the release rounds
    only unit costs that carry more than two decimals, so the exact
    spelling of the planned cost is kept wherever it fits.
    """
    with localcontext() as ctx:
        ctx.prec = max(ctx.prec, value.adjusted() + 3)
        return value.quantize(_CENT, rounding=ROUND_HALF_UP)


def whole_units(quantity: object) -> int:
    """The ordered units as a whole number.

    The plan's quantities are whole units — an ``int``, or a whole
    ``Decimal`` / ``float`` which converts exactly — and anything
    else raises ``ValueError`` (422 at the route) rather than being
    truncated.
    """
    if isinstance(quantity, bool):
        raise ValueError("a quantity must be a whole number")
    if isinstance(quantity, int):
        return quantity
    if isinstance(quantity, float):
        if not math.isfinite(quantity) or not quantity.is_integer():
            raise ValueError("a quantity must be a whole number")
        return int(quantity)
    if isinstance(quantity, Decimal):
        if not quantity.is_finite() or quantity != quantity.to_integral_value():
            raise ValueError("a quantity must be a whole number")
        return int(quantity)
    raise ValueError("a quantity must be a whole number")


def _suppliers_by_item_site(rows: list[dict]) -> dict[tuple[str, str], str | None]:
    """The supplier of each item-site's purchase-line history.

    The supplier is the ``supplier_code`` of the item-site's line with
    the latest ``ordered_on`` — a line with no ``ordered_on`` sorts
    before any dated one — with ties broken by the larger
    ``business_key``.  An item-site without lines, or whose winning
    line carries no ``supplier_code``, maps to ``None``.
    """
    best: dict[tuple[str, str], tuple] = {}
    for row in rows:
        key = (row.get("item_code"), row.get("site_code"))
        ordered_on = row.get("ordered_on")
        rank = (
            ordered_on is not None,
            ordered_on if ordered_on is not None else date.min,
            row.get("business_key") or "",
        )
        current = best.get(key)
        if current is None or rank > current[0]:
            best[key] = (rank, row.get("supplier_code"))
    return {key: supplier for key, (_rank, supplier) in best.items()}


def plan_release(month: object) -> dict:
    """The draft-PO release plan of one calendar month.

    See the module docstring for the full contract: ``month`` is
    ``YYYY-MM``, the current calendar month or one of the next eleven
    (else ``ValueError``); the planned orders are the baseline plan's
    (no levers) orders of that month; the supplier of each item-site
    is the latest ``ordered_on`` line of its purchase-line history,
    ties to the larger ``business_key``; orders without a supplier go
    to ``unreleasable``; the rest are grouped one PO per
    ``(site_code, supplier_code)`` — groups by site then supplier,
    lines by item code — each line ``{"item_code", "quantity",
    "unit_cost", "value"}`` with exact decimal monetary fields
    (``unit_cost`` the planned unit cost rounded half-up to two
    decimal places, 0 when the planned order has none), each group
    tagged with its origin
    ``"AIERP plan <month> site <site_code> vendor <supplier_code>"``
    and its summed value.  Returns ``{"month", "groups",
    "unreleasable", "total_value"}`` and writes nothing.
    """
    year, month_number = _parse_month(month)
    today = date.today()
    offset = (year - today.year) * 12 + (month_number - today.month)
    if offset < 0:
        raise ValueError(
            "month must be the current month or one of the next eleven months"
        )
    if offset > 11:
        raise ValueError(
            "month must be the current month or one of the next eleven months"
        )

    orders = [
        order
        for order in planned_orders(months=offset + 1)
        if order["placed_month"] == month
    ]
    suppliers = _suppliers_by_item_site(
        OpenPurchaseLinePostgresRepository().list_latest()
    )

    groups: dict[tuple[str, str], dict] = {}
    unreleasable: list[dict] = []
    total_value = Decimal(0)
    for order in orders:
        supplier = suppliers.get((order["item_code"], order["site_code"]))
        if supplier is None:
            unreleasable.append(
                {
                    "item_code": order["item_code"],
                    "site_code": order["site_code"],
                    "reason": NO_SUPPLIER_REASON,
                }
            )
            continue
        quantity = whole_units(order["quantity"])
        unit_cost = order["unit_cost"]
        unit_cost = Decimal(0) if unit_cost is None else exact_decimal(unit_cost)
        if unit_cost.as_tuple().exponent < -2:
            unit_cost = round_half_up_cents(unit_cost)
        line_value = Decimal(quantity) * unit_cost
        key = (order["site_code"], supplier)
        group = groups.setdefault(
            key,
            {
                "site_code": order["site_code"],
                "supplier_code": supplier,
                "origin": f"AIERP plan {month} site {order['site_code']} vendor {supplier}",
                "lines": [],
                "value": Decimal(0),
            },
        )
        group["lines"].append(
            {
                "item_code": order["item_code"],
                "quantity": quantity,
                "unit_cost": unit_cost,
                "value": line_value,
            }
        )
        group["value"] += line_value
        total_value += line_value

    release_groups = []
    for key in sorted(groups):
        group = groups[key]
        group["lines"].sort(key=lambda line: line["item_code"])
        release_groups.append(group)
    return {
        "month": month,
        "groups": release_groups,
        "unreleasable": unreleasable,
        "total_value": total_value,
    }
