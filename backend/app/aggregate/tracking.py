"""Release tracking: where the released plan POs stand in Acumatica.

``release_tracking(today=None)`` takes every ``plan_releases`` row
whose outcome is ``created`` or ``exists`` and that carries an order
number — the newest row per order number, the audit listing being
newest first — reads every order number through
``AcumaticaClient.read_purchase_orders`` (one session, reads only),
and classifies each order as of ``today`` (the current date when
omitted), in priority order:

- ``missing``: the ERP did not return the order;
- ``cancelled``: its status is ``Canceled`` or every line is
  cancelled;
- ``received``: every non-cancelled line is completed or has at least
  its ordered quantity on receipt;
- ``awaiting_approval``: the order is on hold, or its status is
  ``Pending Approval``;
- ``late``: a non-cancelled, non-completed line short of receipt
  whose expected date (judged below, like Expedite) is before
  ``today``; a blank or malformed promise is never late;
- ``partially_received``: some quantity is on receipt but the order
  is not fully received;
- ``open``: none of the above.

Each order carries ``{order_nbr, state, month, site_code,
supplier_code, warehouse_code, vendor_code, released_at, actor,
value, status, hold, promised_on, days_waiting, days_late, lines}``
with the identity fields from the release row and ``status``,
``hold``, ``promised_on`` and ``lines`` from the ERP record (null and
empty when it is missing).  When the suppliers read model (read once
through ``SupplierPostgresRepository.list_all``) carries at least one
row, every order also gains ``supplier_name``, looked up by the
order's ``vendor_code`` — the trimmed code the release used — null
for a vendor missing from the read model.  When the inventory items
read model (read once through
``InventoryItemPostgresRepository.list_all``) carries at least one
row, every line of every order also gains ``item_description``,
looked up by the line's ``item_code`` — null for an item missing
from the read model.  The supplier performance is read once per
call (never once per order) through
``app.recommend.supplier_performance.supplier_performance`` with
``as_of`` the resolved date; an as_of after the server's today is
refused, so every expected date then falls back to the line's
promised date.  A line's expected date is judged like
Expedite: a promise later than the order's ERP date (blank or
malformed: none) is the supplier's own commitment and stands as the
expected date; otherwise the promised date is the ERP's default and
the expected date is the order's ERP date plus the supplier's
``avg_lead_time_days`` — the entry of the performance read whose
``supplier_code`` equals the release row's ``supplier_code``, null
when it is missing — rounded to whole days half-up, falling back to
the promised date when the supplier has no figure.  When that
read is answered — with at least one entry, or with none — every
order also gains ``expected_on`` — the earliest expected date of
the lines that are active, not completed and short of receipt, as
an ISO date, the first such line in ERP line order when two or
more tie for the earliest, null when there is no such line — and
``promise_is_default`` — true when that earliest line's promise is
the ERP default, false when it is the supplier's own date, null
when ``expected_on`` is null; only a refused read (an as_of after
the server's today) leaves both off.  ``days_waiting`` is the
whole days between the order's ERP date and ``today`` while the
order is on hold or has status ``Pending Approval`` and null
otherwise — including when the order is already received — and
``days_late`` is ``today`` minus the earliest late line's expected
date and null unless the state is ``late``. The result is ``{as_of,
orders, counts}`` with the orders sorted by state priority — ``late``
first, then ``awaiting_approval``, ``partially_received``, ``open``,
``received``, ``cancelled``, ``missing`` — then oldest release first,
and ``counts`` the fixed set of keys ``awaiting_approval``, ``late``,
``partly_received`` and ``received`` with an integer each. With no
released order there is no Acumatica call. Nothing is written, and an
Acumatica failure raises instead of answering with a partial picture.
"""

from datetime import date
from typing import Any

from app.feed.acumatica_client import AcumaticaClient
from app.recommend import expedite
from app.recommend.expedite import _expected_on
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.plan_releases_postgres_repository import (
    PlanReleasePostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)

__all__ = ["release_tracking"]

# The outcomes whose rows tie a release to an order number in Acumatica.
_RELEASABLE_OUTCOMES = ("created", "exists")
# The classification priority, which is also the order's sort priority.
_STATE_PRIORITY = (
    "late",
    "awaiting_approval",
    "partially_received",
    "open",
    "received",
    "cancelled",
    "missing",
)
# The response's fixed counts keys: state -> key (``partly_received``
# is the counts spelling of the ``partially_received`` state).
_COUNT_KEYS = {
    "awaiting_approval": "awaiting_approval",
    "late": "late",
    "partially_received": "partly_received",
    "received": "received",
}
_ZERO_COUNTS = {
    "awaiting_approval": 0,
    "late": 0,
    "partly_received": 0,
    "received": 0,
}


def _load_release_rows() -> list[dict]:
    """Every stored plan_releases row, newest first (the audit order)."""
    return PlanReleasePostgresRepository().list_newest()


def _supplier_names_by_vendor(suppliers: list) -> dict:
    """vendor_code -> name over the suppliers read.

    Non-dict rows and rows without a vendor_code are skipped; a
    duplicate vendor_code keeps the first row (the read is ordered by
    vendor_code, so this is a stable, deterministic choice).
    """
    names_by_vendor: dict = {}
    for row in suppliers:
        if not isinstance(row, dict):
            continue
        vendor_code = row.get("vendor_code")
        if vendor_code is None or vendor_code in names_by_vendor:
            continue
        names_by_vendor[vendor_code] = row.get("name")
    return names_by_vendor


def _item_descriptions_by_code(items: list) -> dict:
    """item_code -> description over the inventory items read.

    Non-dict rows and rows without an item_code are skipped; a
    duplicate item_code keeps the first row (the read is ordered by
    created_at DESC, so this is a stable, deterministic choice).
    """
    descriptions_by_code: dict = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        item_code = row.get("item_code")
        if item_code is None or item_code in descriptions_by_code:
            continue
        descriptions_by_code[item_code] = row.get("description")
    return descriptions_by_code


def latest_released_rows(rows: list) -> dict[str, dict]:
    """The newest row of every released order number.

    Only rows whose outcome is ``created`` or ``exists`` and that
    carry a non-empty string order number count, and — the rows
    arriving newest first — the first such row of a number is its
    newest.
    """
    latest: dict[str, dict] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get("outcome") not in _RELEASABLE_OUTCOMES:
            continue
        order_nbr = row.get("order_nbr")
        if not isinstance(order_nbr, str) or not order_nbr:
            continue
        latest.setdefault(order_nbr, row)
    return latest


def _active_lines(lines: list[dict]) -> list[dict]:
    """The order's non-cancelled lines."""
    return [line for line in lines if not line["cancelled"]]


def _iso_date(value: object) -> date | None:
    """The date of one ISO date string; blank or malformed is None."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def _supplier_entries_by_code(performance: dict) -> dict:
    """supplier_code -> entry over the supplier performance read.

    Non-dict entries and entries without a supplier_code are skipped;
    a duplicate supplier_code keeps the first entry (the read is
    ordered by on_time_rate ascending with nulls last, then late
    descending, then supplier_code, so this is a stable,
    deterministic choice).
    """
    by_code: dict = {}
    for entry in performance.get("suppliers") or []:
        if not isinstance(entry, dict):
            continue
        supplier_code = entry.get("supplier_code")
        if supplier_code is None or supplier_code in by_code:
            continue
        by_code[supplier_code] = entry
    return by_code


def _expected_lines(
    lines: list[dict],
    order_date: date | None,
    supplier: dict | None,
) -> list[tuple[dict, date, bool]]:
    """(line, expected date, promise_is_default) per judged line.

    The judged lines are the order's lines that are active, not
    completed and short of receipt, in ERP line order.  Each one's
    expected date is ``_expected_on(promised, order_date, supplier)``
    — imported from ``app.recommend.expedite`` and judged there, like
    Expedite: a promise later than the order's ERP date is the
    supplier's own commitment (``promise_is_default`` false);
    otherwise the promised date is the ERP's default and the expected
    date is the order's ERP date plus the supplier's
    ``avg_lead_time_days`` rounded to whole days half-up (true),
    falling back to the promised date when the supplier has no figure
    (true).  A line whose promised date is blank or malformed
    promises nothing parseable, so it carries no expected date and is
    never late.
    """
    expected: list[tuple[dict, date, bool]] = []
    for line in _active_lines(lines):
        if line["completed"]:
            continue
        if line["received_qty"] >= line["order_qty"]:
            continue
        promised = _iso_date(line["promised"])
        if promised is None:
            continue
        expected_on, promise_is_default = _expected_on(promised, order_date, supplier)
        expected.append((line, expected_on, promise_is_default))
    return expected


def _classify_order(
    row: dict,
    erp: dict | None,
    today: date,
    supplier_names_by_vendor: dict | None = None,
    item_descriptions_by_code: dict | None = None,
    supplier_entries_by_code: dict | None = None,
) -> dict[str, Any]:
    """One released order with its state as of ``today``.

    When ``supplier_names_by_vendor`` is not None — the suppliers read
    model carried at least one row — the order also gains
    ``supplier_name``, looked up by its ``vendor_code`` (the trimmed
    code the release used); null when the vendor is missing from it.
    When ``item_descriptions_by_code`` is not None — the inventory
    items read model carried at least one row — every line also gains
    ``item_description``, looked up by the line's ``item_code``;
    null when the item is missing from it.  When
    ``supplier_entries_by_code`` is not None — the supplier
    performance read was answered, with at least one entry or with
    none — the order also gains ``expected_on`` (the earliest
    expected date of the lines that are active, not completed and
    short of receipt, the first such line in ERP line order when
    two or more tie, null when there is no such line) and
    ``promise_is_default`` (true for the ERP default, false for
    the supplier's own date, null when ``expected_on`` is null);
    the entry of a line's order is looked up by the release row's
    ``supplier_code``, null when it is missing — an answered empty
    read is an empty lookup, so every expected date then falls
    back to the promised date.
    """
    lines = erp["lines"] if erp is not None else []
    if item_descriptions_by_code is not None:
        lines = [
            {
                **line,
                "item_description": item_descriptions_by_code.get(
                    line.get("item_code")
                ),
            }
            for line in lines
        ]
    status = erp["status"] if erp is not None else None
    hold = erp["hold"] if erp is not None else None
    promised_on = erp["promised_on"] if erp is not None else None
    # The order's ERP date (blank or malformed: None) and the
    # performance entry of the release's supplier: together they
    # judge every line's expected date, like Expedite.
    order_date = _iso_date(erp["date"]) if erp is not None else None
    supplier = (
        supplier_entries_by_code.get(row.get("supplier_code"))
        if supplier_entries_by_code is not None
        else None
    )
    expected_lines = _expected_lines(lines, order_date, supplier)
    late = [
        (line, expected_on)
        for line, expected_on, _ in expected_lines
        if expected_on < today
    ]
    active = _active_lines(lines)
    awaiting = hold or status == "Pending Approval"
    if erp is None:
        state = "missing"
    elif status == "Canceled" or not active:
        state = "cancelled"
    elif all(
        line["completed"] or line["received_qty"] >= line["order_qty"]
        for line in active
    ):
        state = "received"
    elif awaiting:
        state = "awaiting_approval"
    elif late:
        state = "late"
    elif any(line["received_qty"] > 0 for line in active):
        state = "partially_received"
    else:
        state = "open"
    days_waiting = None
    if erp is not None and awaiting and order_date is not None:
        days_waiting = (today - order_date).days
    days_late = None
    if state == "late":
        # The earliest late line: the smallest expected date, the
        # first late line in ERP line order when two or more tie.
        _, earliest = min(late, key=lambda item: item[1])
        days_late = (today - earliest).days
    order = {
        "order_nbr": row["order_nbr"],
        "state": state,
        "month": row.get("month"),
        "site_code": row.get("site_code"),
        "supplier_code": row.get("supplier_code"),
        "warehouse_code": row.get("warehouse_code"),
        "vendor_code": row.get("vendor_code"),
        "released_at": row.get("created_at"),
        "actor": row.get("actor"),
        "value": row.get("value"),
        "status": status,
        "hold": hold,
        "promised_on": promised_on,
        "days_waiting": days_waiting,
        "days_late": days_late,
        "lines": lines,
    }
    if supplier_entries_by_code is not None:
        if expected_lines:
            _, earliest_expected, promise_is_default = min(
                expected_lines, key=lambda item: item[1]
            )
            order["expected_on"] = earliest_expected.isoformat()
            order["promise_is_default"] = promise_is_default
        else:
            order["expected_on"] = None
            order["promise_is_default"] = None
    if supplier_names_by_vendor is not None:
        order["supplier_name"] = supplier_names_by_vendor.get(row.get("vendor_code"))
    return order


def release_tracking(today: date | None = None) -> dict[str, Any]:
    """The tracking view of every released order, as of ``today``.

    ``today`` defaults to the current date and is answered in
    ``as_of`` as ``YYYY-MM-DD``. The newest ``plan_releases`` row per
    released order number (outcomes ``created`` and ``exists`` with an
    order number) is read from Acumatica in one session and
    classified; the orders are sorted by state priority — ``late``
    first, then ``awaiting_approval``, ``partially_received``,
    ``open``, ``received``, ``cancelled``, ``missing`` — then oldest
    release first, and ``counts`` carries the fixed keys
    ``awaiting_approval``, ``late``, ``partly_received`` and
    ``received`` with an integer each. With nothing released the
    answer is the empty order list and zero counts, with no Acumatica
    call and no supplier performance read. The supplier performance
    is read once per call (``supplier_performance`` with ``as_of``
    the resolved date, never once per order) for the line expected
    dates, like Expedite; an as_of after the server's today is
    refused, so every expected date falls back to the line's
    promised date and the two fields stay off the order. An
    Acumatica configuration, auth, or
    transport failure raises — never a partial picture. Nothing is
    written.
    """
    as_of_date = today if today is not None else date.today()
    as_of = as_of_date.isoformat()
    latest = latest_released_rows(_load_release_rows())
    if not latest:
        return {"as_of": as_of, "orders": [], "counts": dict(_ZERO_COUNTS)}
    # The suppliers read model, once: the display name of every
    # vendor, looked up by the vendor code the release used.
    supplier_names_by_vendor = _supplier_names_by_vendor(
        SupplierPostgresRepository().list_all()
    )
    # The inventory items read model, once: the description of every
    # item, looked up by the line's item code.
    item_descriptions_by_code = _item_descriptions_by_code(
        InventoryItemPostgresRepository().list_all()
    )
    # The supplier performance, once per call (not once per order):
    # the lead-time figures that judge every line's expected date,
    # like Expedite — the same ``supplier_performance`` read Expedite
    # makes, imported through ``app.recommend.expedite``.  An as_of
    # after the server's today raises ValueError and the read is
    # refused — every expected date falls back to the line's
    # promised date, and the two fields stay off the order; an
    # answered read, with entries or with none, carries them on
    # every order, the empty one judging every expected date as the
    # promised date.
    try:
        performance = expedite.supplier_performance(as_of=as_of_date)
    except ValueError:
        performance = None
    supplier_entries_by_code = (
        None if performance is None else _supplier_entries_by_code(performance)
    )
    found = AcumaticaClient().read_purchase_orders(list(latest))
    orders = [
        _classify_order(
            row,
            found.get(order_nbr),
            as_of_date,
            supplier_names_by_vendor or None,
            item_descriptions_by_code or None,
            supplier_entries_by_code,
        )
        for order_nbr, row in latest.items()
    ]
    orders.sort(
        key=lambda order: (
            _STATE_PRIORITY.index(order["state"]),
            order["released_at"] or "",
            order["order_nbr"],
        )
    )
    counts = dict(_ZERO_COUNTS)
    for order in orders:
        key = _COUNT_KEYS.get(order["state"])
        if key is not None:
            counts[key] += 1
    return {"as_of": as_of, "orders": orders, "counts": counts}
