"""Fitting a raw order quantity to a supplier's ordering terms.

The pure function ``fit_to_terms`` takes a raw order quantity (in
fractional units) and a supplier's ordering terms — ``min_order_qty``,
``lot_size`` and ``max_order_qty``, each a number or null — and returns
the orderable whole-unit quantity: starting from ``ceil(raw_quantity)``,
a set minimum below the quantity raises the quantity to the minimum
(adjustment ``"raised_to_minimum"``), a set lot size rounds the
quantity up to the next whole multiple of the lot (adjustment
``"rounded_to_lot"``, only when that changes the quantity) and a set
maximum above which the quantity sits caps the quantity at the maximum
(adjustment ``"capped_at_maximum"``).  ``short_of_need`` is true when
the final quantity is below ``ceil(raw_quantity)`` — the order then
covers less than the raw need, only a maximum can cause that.  With
null terms the quantity is just ``ceil(raw_quantity)``, with no
adjustments and no shortfall.
"""

from __future__ import annotations

import math
from decimal import Decimal

from app.units import ceil_units


def _number(value) -> float | None:
    """A finite real number as a float, else None.

    None, bools, non-numeric values and non-finite floats are not
    numbers — a term the caller cannot hold simply stands for no term,
    never an error.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
    else:
        return None
    return number if math.isfinite(number) else None


def fit_to_terms(raw_quantity, terms) -> dict:
    """Fit a raw order quantity to the supplier's ordering terms.

    ``terms`` holds ``min_order_qty``, ``lot_size`` and
    ``max_order_qty``, each a number or null (a mapping, or None for no
    terms).  Returns ``{"quantity", "adjustments", "short_of_need"}``:
    ``quantity`` in whole units, ``adjustments`` the names of the rules
    that CHANGED the quantity, in the order they were applied —
    ``"raised_to_minimum"``, ``"rounded_to_lot"``,
    ``"capped_at_maximum"`` — and ``short_of_need`` true when the final
    quantity is below ``ceil(raw_quantity)``.
    """
    raw = _number(raw_quantity)
    need = ceil_units(raw) if raw is not None else 0
    quantity = float(need)
    adjustments: list[str] = []

    if terms:
        min_qty = _number(terms.get("min_order_qty"))
        lot_size = _number(terms.get("lot_size"))
        max_qty = _number(terms.get("max_order_qty"))

        if min_qty is not None and quantity < min_qty:
            quantity = math.ceil(min_qty)
            adjustments.append("raised_to_minimum")

        if lot_size is not None and lot_size > 0:
            # IEEE division is exact when the true quotient is a whole
            # multiple, so a true multiple is never rounded up; the
            # epsilon absorbs float noise on near-whole quotients.
            rounded = math.ceil(quantity / lot_size - 1e-9) * lot_size
            if rounded != quantity:
                quantity = rounded
                adjustments.append("rounded_to_lot")

        if max_qty is not None and quantity > max_qty:
            quantity = math.floor(max_qty)
            adjustments.append("capped_at_maximum")

    quantity = int(quantity)
    return {
        "quantity": quantity,
        "adjustments": adjustments,
        "short_of_need": quantity < need,
    }
