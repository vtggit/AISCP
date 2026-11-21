"""The plan release PO readback: the stored orders' answers from Acumatica.

``refresh_plan_release_readbacks()`` answers what the released
purchase orders carry in Acumatica now: it is not a route, a
maintenance entry point.  It reads every ``plan_releases`` row whose
outcome is ``created`` or ``exists``, whose ``order_nbr`` is
non-null, and whose ``erp_status`` is null or neither ``Completed``,
``Closed`` nor ``Canceled``, and calls
``AcumaticaClient.read_purchase_orders`` once per chunk of at most
``_MAX_READBACK_PER_CALL`` of their distinct order numbers, in
ascending order, merging the answers.  For every number the ERP
returns, each row with that number gets the answer's ``status`` in
``erp_status``, the answer's ``lines`` — exactly as returned — in
``erp_lines``, and now in ``erp_checked_at``; a number the ERP does
not return leaves its rows unchanged.

Returns ``{"checked", "updated"}`` — the order numbers sent to the
ERP and the rows whose readback was stored.  With no row to read it
returns ``{"checked": 0, "updated": 0}`` and makes no call.  When
Acumatica is not configured it returns ``{"checked": 0, "updated": 0,
"skipped": "not configured"}`` without raising and without touching a
row, as ``refresh_transfer_release_statuses`` does.

``release_unchanged(lines, erp_status, erp_lines)`` reads and writes
nothing: it compares one released purchase order with the readback of
it and answers whether the ERP shows it unchanged, so the scorecard
knows which of its read rows came back the way they went out.
"""

from decimal import Decimal, InvalidOperation

# The order numbers one readback call may send to Acumatica: the
# client's per-call limit for a PO read.
_MAX_READBACK_PER_CALL = 100

# How far the released side's and the ERP side's sum of one item's
# quantities may differ and still count as unchanged.
_UNCHANGED_TOLERANCE = Decimal("0.0001")


def refresh_plan_release_readbacks() -> dict:
    """Read the stored release orders back from Acumatica.

    Not a route: a maintenance entry point.  Reads every
    ``plan_releases`` row whose outcome is ``created`` or ``exists``,
    whose ``order_nbr`` is non-null, and whose ``erp_status`` is null
    or neither ``Completed``, ``Closed`` nor ``Canceled``, and calls
    ``AcumaticaClient.read_purchase_orders`` once per chunk of at most
    ``_MAX_READBACK_PER_CALL`` of their distinct order numbers, in
    ascending order, merging the answers.  For every number the ERP
    returns, each row with that number gets the answer's ``status`` in
    ``erp_status``, the answer's ``lines`` — exactly as returned — in
    ``erp_lines``, and now in ``erp_checked_at``; a number the ERP
    does not return leaves its rows unchanged.

    Returns ``{"checked", "updated"}`` — the order numbers sent to the
    ERP and the rows whose readback was stored.  With no row to read
    it returns ``{"checked": 0, "updated": 0}`` and makes no call.
    When Acumatica is not configured it returns ``{"checked": 0,
    "updated": 0, "skipped": "not configured"}`` without raising and
    without touching a row, as ``refresh_transfer_release_statuses``
    does.
    """
    from app.feed.acumatica_client import AcumaticaClient, AcumaticaConfigError
    from app.repositories.plan_releases_postgres_repository import (
        PlanReleasePostgresRepository,
    )

    try:
        AcumaticaClient()._load_config()
    except AcumaticaConfigError:
        return {"checked": 0, "updated": 0, "skipped": "not configured"}

    repo = PlanReleasePostgresRepository()
    order_nbrs = repo.list_readback_order_nbrs()
    if not order_nbrs:
        return {"checked": 0, "updated": 0}

    found: dict[str, dict] = {}
    client = AcumaticaClient()
    for start in range(0, len(order_nbrs), _MAX_READBACK_PER_CALL):
        chunk = order_nbrs[start : start + _MAX_READBACK_PER_CALL]
        found.update(client.read_purchase_orders(chunk))
    updated = repo.mark_erp_readbacks(found)
    return {"checked": len(order_nbrs), "updated": updated}


def _quantity(value: object) -> Decimal | None:
    """One line's quantity as an exact decimal.

    Integers, floats and Decimals are quantities; a bool is not a
    number, and neither is a string, None or any other type — those
    answer None, and nothing here ever raises.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _sum_by_item(rows: object, quantity_key: str) -> dict[str, Decimal] | None:
    """One side's quantities summed per ``item_code``.

    ``rows`` of None is no lines.  A line that is not a dict, that
    has no non-empty string ``item_code``, or that has no numeric
    quantity makes the whole side unusable — the function answers
    None, so a hostile line can never become an error.
    """
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        return None
    totals: dict[str, Decimal] = {}
    for row in rows:
        if not isinstance(row, dict):
            return None
        item_code = row.get("item_code")
        if not isinstance(item_code, str) or not item_code:
            return None
        quantity = _quantity(row.get(quantity_key))
        if quantity is None:
            return None
        totals[item_code] = totals.get(item_code, Decimal(0)) + quantity
    return totals


def release_unchanged(lines: object, erp_status: object, erp_lines: object) -> bool:
    """Whether the readback shows the released order unchanged.

    Reads and writes nothing.  The released side sums ``quantity`` per
    ``item_code`` over ``lines``; the ERP side sums ``order_qty`` per
    ``item_code`` over the ``erp_lines`` whose ``cancelled`` is not
    true, so an item whose every ERP line is cancelled is not on the
    ERP side, and a null ``erp_lines`` is no lines.  Answers True
    only when all three hold — ``erp_status`` is not ``Canceled``,
    both sides hold the same item codes, and each item's two sums
    differ by no more than 0.0001 — and False otherwise.
    ``received_qty``, ``completed``, ``promised``, the warehouse, and
    every price or value are not compared.  A line on either side
    that is not a dict with a non-empty string ``item_code`` and a
    numeric quantity is unusable: the readback cannot be verified, so
    the answer is False, never an error.
    """
    if erp_status == "Canceled":
        return False
    if erp_lines is not None and not isinstance(erp_lines, list):
        return False
    active_erp_lines = [
        row
        for row in (erp_lines or [])
        if not (isinstance(row, dict) and row.get("cancelled") is True)
    ]
    released = _sum_by_item(lines, "quantity")
    erp = _sum_by_item(active_erp_lines, "order_qty")
    if released is None or erp is None:
        return False
    if set(released) != set(erp):
        return False
    for item_code in released:
        try:
            difference = abs(released[item_code] - erp[item_code])
            within_tolerance = difference <= _UNCHANGED_TOLERANCE
        except InvalidOperation:
            # A NaN or an infinite difference (inf over inf) cannot be
            # within the tolerance.
            return False
        if not within_tolerance:
            return False
    return True
