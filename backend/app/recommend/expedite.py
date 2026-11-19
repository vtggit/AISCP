"""Open purchase lines to expedite, ranked by stockout urgency.

``expedite_list`` reads the latest open purchase line snapshot
(``OpenPurchaseLinePostgresRepository.list_latest``), the on-hand
quantities (``OnHandPostgresRepository``), the item-site unit costs
(``ItemSitePostgresRepository``) and the "ok" reorder recommendations
(``app.recommend.reorder.recommendations_for_all``) once each, and
calls ``app.recommend.supplier_performance.supplier_performance``
once; it writes nothing.

The stale window, the lateness and the stockout projection run off
the resolved ``as_of`` — the server's current date when the caller
supplies none. An explicit ``as_of`` goes to the recommendations read
and the supplier performance call as well; with none supplied they
keep their own defaults — the recommendations read with
``recommendations_for_all``'s default, the shared default as-of
(``app.stats.demand.default_as_of``), so a demand window never counts
days the feed has not delivered yet, and the supplier performance call
with its own default, today.

Every open line with ``open_units`` above 0 is considered. A line
promised more than 90 days before ``as_of`` (the same window
``app.recommend.stockout`` uses) is stale, and a line whose
``promised_on`` is null is left out of the list the same way; both
count only in ``stale_excluded``.

Each remaining line gets an ``expected_on``. When ``promised_on`` is
later than ``ordered_on`` it is the supplier's own promise and
``expected_on`` is ``promised_on`` (``promise_is_default`` false).
Otherwise the promised date is the ERP's default, not a supplier
commitment (#613): ``expected_on`` is ``ordered_on`` plus the
supplier's ``avg_lead_time_days`` from #604, rounded to whole days
half-up (10.5 becomes 11), falling back to ``promised_on`` when the
supplier has no figure (``promise_is_default`` true).

Two flags are computed per line. ``late``: ``expected_on`` is before
``as_of``. ``after_stockout``: the item-site has an "ok"
recommendation with daily demand ``d`` above 0 and ``expected_on`` is
after the projected stockout date, ``as_of`` plus ``floor(on_hand /
d)`` days (``as_of`` itself when the on hand is at or below 0).

Only lines with at least one flag are listed, carrying the line's
identity parsed from its ``business_key`` (``OrderType:OrderNbr:
LineNbr``; an Odoo key ``odoo:<database>:<model>:<record id>`` with
at least four parts parses the other way round — the model, the
database and the record id become the ``order_type``, the
``order_nbr`` and the ``line_nbr``, so every Odoo line keeps its
own identity), ``open_value`` (``open_units`` times the item-site
unit cost, rounded to cents, null without a cost), ``days_late``
(whole days after ``expected_on``, null when not late), the on-hand
quantity, ``d``, ``days_of_cover`` (one decimal, null without
demand, null when the quotient overflows — never non-finite),
``projected_stockout_on`` (null without demand),
``reasons`` (``"late"`` and/or ``"after_stockout"``, in that order)
and the supplier's ``on_time_rate`` (null when the supplier has no
entry).  When the suppliers read model (read once through
``SupplierPostgresRepository.list_all``) carries at least one row,
each line also gains ``vendor_code`` and ``supplier_name``, looked up
by the line's ``supplier_code`` — both null for a supplier missing
from the read model, whose line is still listed.  Lines rank by
soonest ``projected_stockout_on`` (lines
without one after every line with one), then larger ``days_late``,
then larger ``open_value``, then ``order_nbr`` and ``line_nbr``.

When the inventory items read model (read once through
``InventoryItemPostgresRepository.list_all``) carries at least one row,
each line also gains ``item_description``, looked up by the line's
``item_code`` — null for an item missing from the read model, whose
line is still listed.

The answer is ``{"as_of", "lines", "stale_excluded"}`` with ``as_of``
the resolved ISO date (the server's current date when the caller
supplies none). An ``as_of`` after today raises ``ValueError``.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from app.recommend.reorder import recommendations_for_all
from app.recommend.stockout import _STALE_WINDOW_DAYS, _floor_days
from app.recommend.supplier_performance import supplier_performance
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)
from app.stats.demand import _shift_back

_on_hand_repo = OnHandPostgresRepository()
_item_site_repo = ItemSitePostgresRepository()
_open_po_repo = OpenPurchaseLinePostgresRepository()
_suppliers_repo = SupplierPostgresRepository()
_inventory_items_repo = InventoryItemPostgresRepository()


def _as_date(value):
    """Coerce a row date column (date or datetime) to a date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _today() -> date:
    """The server's current date; tests substitute a fixed date here."""
    return date.today()


def _finite_float(value):
    """The value as a finite float, or None when it is not a real
    number."""
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def _whole_days_half_up(days: float) -> int:
    """``days`` in whole days, ties rounded half-up (10.5 becomes 11).

    Python's built-in ``round`` rounds ties to even (10.5 becomes
    10); the expected date rounds ties up instead.
    """
    return int(Decimal(str(days)).to_integral_value(rounding=ROUND_HALF_UP))


def _money(units: float, unit_cost: float):
    """``units * unit_cost`` rounded to cents, or None when not
    finite."""
    value = units * unit_cost
    if math.isnan(value) or math.isinf(value):
        return None
    return round(value, 2)


def _suppliers_by_code(suppliers: list) -> dict:
    """supplier_code -> (vendor_code, name) over the suppliers read.

    Non-dict rows and rows without a supplier_code are skipped; a
    duplicate supplier_code keeps the first row (the read is ordered
    by vendor_code, so this is a stable, deterministic choice).
    """
    by_code: dict = {}
    for row in suppliers:
        if not isinstance(row, dict):
            continue
        supplier_code = row.get("supplier_code")
        if supplier_code is None or supplier_code in by_code:
            continue
        by_code[supplier_code] = (row.get("vendor_code"), row.get("name"))
    return by_code


def _descriptions_by_code(items: list) -> dict:
    """item_code -> description over the inventory items read.

    Non-dict rows and rows without an item_code are skipped; a
    duplicate item_code keeps the first row (the read is ordered by
    created_at DESC, so this is a stable, deterministic choice).
    """
    by_code: dict = {}
    for row in items:
        if not isinstance(row, dict):
            continue
        item_code = row.get("item_code")
        if item_code is None or item_code in by_code:
            continue
        by_code[item_code] = row.get("description")
    return by_code


def _parse_business_key(business_key):
    """(order_type, order_nbr, line_nbr) from the line's business key
    ``OrderType:OrderNbr:LineNbr`` (each None when the key is missing
    or too short to carry it).

    An Odoo key — first part ``odoo`` with at least four parts,
    ``odoo:<database>:<model>:<record id>`` — parses the other way
    round: the model (the third part) is the ``order_type``, the
    database (the second part) the ``order_nbr`` and the LAST part
    (the record id) the ``line_nbr``, so every Odoo line keeps its
    own identity (issue #815)."""
    if not isinstance(business_key, str):
        return None, None, None
    parts = business_key.split(":")
    if parts[0] == "odoo" and len(parts) >= 4:
        return parts[2] or None, parts[1] or None, parts[-1] or None
    order_type = parts[0] or None
    order_nbr = (parts[1] or None) if len(parts) > 1 else None
    line_nbr = (parts[2] or None) if len(parts) > 2 else None
    return order_type, order_nbr, line_nbr


def _expected_on(
    promised_on: date,
    ordered_on: date | None,
    supplier: dict | None,
) -> tuple[date, bool]:
    """The line's ``expected_on`` and ``promise_is_default``.

    A ``promised_on`` later than ``ordered_on`` is the supplier's own
    commitment and stands as the expected date. Otherwise the promised
    date is the ERP's default: the expected date is ``ordered_on``
    plus the supplier's ``avg_lead_time_days`` rounded to whole days
    half-up, falling back to ``promised_on`` when the supplier has no
    figure (or the order has no date to start from).
    """
    if ordered_on is not None and promised_on > ordered_on:
        return promised_on, False
    avg_lead = (
        _finite_float(supplier.get("avg_lead_time_days"))
        if supplier is not None
        else None
    )
    if ordered_on is not None and avg_lead is not None:
        try:
            return ordered_on + timedelta(days=_whole_days_half_up(avg_lead)), True
        except OverflowError:
            pass
    return promised_on, True


def _sort_key(row: dict):
    """Soonest projected stockout first (no projected date last), then
    larger days_late (None last), then larger open_value (None last),
    then order_nbr and line_nbr."""
    projected = row["projected_stockout_on"]
    days_late = row["days_late"]
    open_value = row["open_value"]
    return (
        projected is None,
        projected if projected is not None else date.min,
        days_late is None,
        -(days_late or 0),
        open_value is None,
        -(open_value or 0.0),
        row["order_nbr"] or "",
        row["line_nbr"] or "",
    )


def expedite_list(as_of: date | None = None) -> dict:
    """Open purchase lines to expedite, ranked by stockout urgency.

    See the module docstring for the line selection, the
    ``expected_on`` rule, the two flags and the ranking. ``as_of`` is
    the server's current date when None; one after today raises
    ``ValueError``. The stale window, the lateness and the stockout
    projection run off it either way; an explicit ``as_of`` goes to
    the recommendations read and the supplier performance call as
    well, while with none supplied the recommendations read keeps
    ``recommendations_for_all``'s default (the shared default as-of)
    and the supplier performance call keeps its own default, today.
    The four reads, the suppliers read and the supplier performance
    call each happen exactly once, and nothing is written.
    """
    explicit_as_of = as_of is not None
    if as_of is None:
        as_of = _today()
    else:
        as_of = _as_date(as_of)
        if as_of is None:
            raise ValueError("as_of must be a date")
        if as_of > _today():
            raise ValueError("as_of must not be after today")

    on_hand_by_key = {
        (row["item_code"], row["site_code"]): row["quantity_on_hand"]
        for row in _on_hand_repo.list_rows(10**9, 0)
    }
    item_site_by_key = {
        (row["item_code"], row["site_code"]): row
        for row in _item_site_repo.list_rows(10**9, 0)
    }
    if explicit_as_of:
        # The caller's date goes to both reads, as before.
        recommendations = recommendations_for_all(as_of)
        performance = supplier_performance(as_of=as_of)
    else:
        # No caller date: the recommendations read keeps its own
        # default (the shared default as-of, so a demand window never
        # counts days the feed has not delivered yet) and the
        # supplier performance call keeps its own default, today,
        # while the stale window, the lateness and the stockout
        # projection run off today.
        recommendations = recommendations_for_all()
        performance = supplier_performance()
    ok_recommendations = {
        (row["item_code"], row["site_code"]): row
        for row in recommendations
        if isinstance(row, dict) and row.get("status") == "ok"
    }
    suppliers_by_code = {
        entry["supplier_code"]: entry
        for entry in performance.get("suppliers", [])
        if isinstance(entry, dict) and entry.get("supplier_code") is not None
    }
    # The suppliers read model, once: the vendor code and display
    # name of every supplier, looked up by the line's supplier_code.
    vendor_lookup_by_code = _suppliers_by_code(_suppliers_repo.list_all())
    # The inventory items read model, once: the description of every
    # item, looked up by the line's item_code.
    descriptions_by_code = _descriptions_by_code(_inventory_items_repo.list_all())

    lower = _shift_back(as_of, _STALE_WINDOW_DAYS)
    stale_excluded = 0
    rows: list[dict] = []
    for line in _open_po_repo.list_latest():
        open_units = _finite_float(line.get("open_units"))
        if open_units is None or open_units <= 0:
            continue
        promised_on = _as_date(line.get("promised_on"))
        if promised_on is None or promised_on < lower:
            # Undated lines and lines promised more than the stale
            # window before as_of are left out of the list; both count
            # here only.
            stale_excluded += 1
            continue

        item_code = line.get("item_code")
        site_code = line.get("site_code")
        key = (item_code, site_code)
        ordered_on = _as_date(line.get("ordered_on"))
        supplier = suppliers_by_code.get(line.get("supplier_code"))
        expected_on, promise_is_default = _expected_on(
            promised_on, ordered_on, supplier
        )

        quantity = _finite_float(on_hand_by_key.get(key))
        if quantity is None:
            quantity = 0.0
        d = None
        recommendation = ok_recommendations.get(key)
        if recommendation is not None:
            inputs = recommendation.get("inputs")
            if isinstance(inputs, dict):
                d = _finite_float(inputs.get("d"))
        if d is not None and d <= 0:
            d = None

        days_of_cover = None
        projected_stockout_on = None
        if d is not None:
            # A huge on hand against a tiny daily demand overflows
            # the quotient to infinity: the cover then reads null
            # (the projected stockout still lands on date.max),
            # never a non-finite figure in the answer.
            cover = quantity / d
            days_of_cover = round(cover, 1) if math.isfinite(cover) else None
            if quantity <= 0:
                projected_stockout_on = as_of
            else:
                try:
                    projected_stockout_on = as_of + timedelta(
                        days=_floor_days(quantity, d)
                    )
                except OverflowError:
                    projected_stockout_on = date.max

        late = expected_on < as_of
        after_stockout = (
            projected_stockout_on is not None and expected_on > projected_stockout_on
        )
        if not (late or after_stockout):
            continue
        reasons = []
        if late:
            reasons.append("late")
        if after_stockout:
            reasons.append("after_stockout")

        item_site = item_site_by_key.get(key)
        unit_cost = (
            _finite_float(item_site["unit_cost"]) if item_site is not None else None
        )
        order_type, order_nbr, line_nbr = _parse_business_key(line.get("business_key"))
        row = {
            "order_type": order_type,
            "order_nbr": order_nbr,
            "line_nbr": line_nbr,
            "item_code": item_code,
            "site_code": site_code,
            "supplier_code": line.get("supplier_code"),
            "ordered_on": ordered_on,
            "promised_on": promised_on,
            "expected_on": expected_on,
            "promise_is_default": promise_is_default,
            "open_units": open_units,
            "open_value": (
                _money(open_units, unit_cost) if unit_cost is not None else None
            ),
            "days_late": (as_of - expected_on).days if late else None,
            "on_hand_units": quantity,
            "d": d,
            "days_of_cover": days_of_cover,
            "projected_stockout_on": projected_stockout_on,
            "reasons": reasons,
            "supplier_on_time_rate": (
                _finite_float(supplier.get("on_time_rate"))
                if supplier is not None
                else None
            ),
        }
        if vendor_lookup_by_code:
            # The read model carries at least one supplier: every
            # line gains the vendor code and display name, null when
            # the supplier is missing from it — never dropped, never
            # a failure.
            vendor_code, supplier_name = vendor_lookup_by_code.get(
                row["supplier_code"], (None, None)
            )
            row["vendor_code"] = vendor_code
            row["supplier_name"] = supplier_name
        if descriptions_by_code:
            # The read model carries at least one item: every line
            # gains the item's description, null when the item is
            # missing from it — never dropped, never a failure.
            row["item_description"] = descriptions_by_code.get(row["item_code"])
        rows.append(row)

    rows.sort(key=_sort_key)
    return {
        "as_of": as_of.isoformat(),
        "lines": rows,
        "stale_excluded": stale_excluded,
    }
