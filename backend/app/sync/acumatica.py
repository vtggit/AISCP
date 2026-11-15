"""Pure Acumatica -> AIERP mappers and the read-then-write sync runner.

``map_item_sites``, ``map_demand_events``, ``map_lead_time_observations``,
``map_on_hand``, ``map_open_purchase_lines``, ``map_open_sales_lines``,
``map_supplier_terms`` and ``_map_inventory_items``
turn raw AIDW feed rows into the row dicts the matching Postgres
repositories' ``upsert_many`` expects; every emitted row carries
``source_system = "acumatica"``.

Common rules (every mapper):

- ``item_code`` is the referenced ``InventoryItem.InventoryCD`` (looked up
  by ``InventoryID``, surrounding whitespace trimmed);
- only stock items (``StkItem`` true) are kept, rows for other items are
  skipped and counted;
- ``site_code`` is the integer ``SiteID`` rendered as text;
- the item master mapper (``_map_inventory_items``) keeps only the stock
  items of the ``InventoryItem`` set itself: ``item_code`` is the trimmed
  ``InventoryCD``, ``description`` the trimmed ``Descr`` (null when
  missing or blank) and ``uom`` the trimmed ``BaseUnit``; every
  non-stock row is counted as ``non_stock_item``.

``run_sync`` reads the six entity sets through
:func:`app.feed.aidw_client.iter_entities` — iterator exhaustion is read
completion, and no row-count or completion signal from the ERP is required
or validated — and only after all six reads succeed writes the mapped rows
through the repositories' ``upsert_many`` with ``as_of`` stamped to the
sync's start time.  When the sync reads from the configured AIDW feed
(no injected ``iter_entities`` double), it also reads the ``BAccount``,
``SOLine`` and ``POVendorInventory`` entity sets through the same feed
reader after the six reads (``SOLine`` right after ``BAccount`` and
``POVendorInventory`` right after ``SOLine``) and writes the vendor rows
(``Type`` "VE") through the suppliers repository, the mapped
sales-line rows through the open sales lines repository and the active
vendor-inventory rows through the supplier terms repository; a feed that
does not expose a set (an ``AidwFeedError``) skips that read model and
records the skip under ``"skipped"`` instead of failing the sync.
An injected ``iter_entities`` double stands in for
the six entity sets and is not asked for ``BAccount``, ``SOLine`` or
``POVendorInventory``.

Between the last read and mapping and the first write, ``run_sync``
runs the shared code-clash guard
(:func:`app.sync.clashes.apply_clash_guard`) over the six row lists —
item sites, on hand, demand events, lead time observations, open
purchase lines and open sales lines: rows of a pair that ``item_sites``
already stores under a different ``source_system`` are removed — the
first holder keeps its row exactly as it was, its ``source_system``
included — each held pair is recorded in ``erp_code_clashes`` and
this source's still-open clash rows whose pair was not held this time
get ``resolved_at`` set.  Supplier terms and suppliers are untouched.
When a pair is held, the result gains ``clashes`` — the number of held
pairs; with no clash the result shape is unchanged.  The guard is
skipped when the injected item-sites repository has no
``held_by_others``, the way the Odoo runner skips an optional reader
the client does not have.

Between the last read (and mapping) and the first write, the
inventory-item rows pass the shared item-name guard
(:func:`app.sync.clashes.apply_item_name_guard`): the rows of a code
``inventory_items`` stores under a different ``source_system`` are
removed — the first holder keeps its row exactly as it was, its
``source_system`` included — and an item master row names no site, so
nothing is added to ``erp_code_clashes``.  When a row is removed the
result gains ``item_names_held`` — the number of rows removed — only
when it is above 0, so the result shape is unchanged when nothing is
held.  The guard is skipped when the injected inventory-items
repository has no ``held_by_others``.

The mappers are pure and hostile-input safe: non-dict rows, unknown or
non-stock items, and rows missing a required field are skipped and counted
by reason instead of raising.
"""

import math
from collections import Counter
from datetime import UTC, date, datetime

from app.feed import aidw_client
from app.feed.aidw_client import AidwFeedError
from app.repositories.demand_events_postgres_repository import (
    DemandEventPostgresRepository,
)
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.lead_time_observations_postgres_repository import (
    LeadTimeObservationPostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.open_sales_lines_postgres_repository import (
    OpenSalesLinePostgresRepository,
)
from app.repositories.supplier_terms_postgres_repository import (
    SupplierTermsPostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)
from app.sync.clashes import apply_clash_guard, apply_item_name_guard

__all__ = [
    "map_demand_events",
    "map_item_sites",
    "map_lead_time_observations",
    "map_on_hand",
    "map_open_purchase_lines",
    "map_open_sales_lines",
    "map_suppliers",
    "map_supplier_terms",
    "run_sync",
]

SOURCE_SYSTEM = "acumatica"

_ENTITY_SETS = (
    "InventoryItem",
    "INItemSite",
    "INSiteStatus",
    "INTran",
    "POLine",
    "POReceiptLine",
)

_DEMAND_EVENT_TYPES = {
    ("I", "INV"): "shipment",
    ("M", "III"): "production_issue",
    ("P", "ASY"): "kit_issue",
    ("I", "III"): "production_issue",
}

_UNIT_COST_FIELDS = ("AvgCost", "LastCost", "StdCost")


# ---------------------------------------------------------------------------
# Small parsing helpers (hostile-input safe: never raise on odd values)
# ---------------------------------------------------------------------------


def _parse_number(value):
    """A finite float for int/float/numeric-string values, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _parse_date(value):
    """The calendar date part of a date, datetime, or ISO-8601 string, else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(text).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
    return None


def _is_true(value):
    """JSON/OData booleans, plus the strings "true"/"1", count as true."""
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return False


def _qty_or_null(value):
    """A parsed quantity read as null when missing or 0, else the number."""
    number = _parse_number(value)
    if number is None or number == 0:
        return None
    return number


def _site_code(value):
    """An integer SiteID rendered as text, or None when it is not an integer."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else None
    if isinstance(value, str):
        number = _parse_number(value)
        if number is not None and number.is_integer():
            return str(int(number))
    return None


def _supplier_code(value):
    """An integer BAccountID rendered as text, or None when it is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else None
    if isinstance(value, str):
        number = _parse_number(value)
        if number is not None and number.is_integer():
            return str(int(number))
    return None


def _text(value):
    """A value rendered as trimmed text, or None for None/blank."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _join_key(*parts):
    return ":".join("" if part is None else str(part) for part in parts)


def _as_of_text(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    return datetime.now(UTC).isoformat()


def _po_key(*values):
    """A (type, number, line) join key, or None when a part is unhashable."""
    if any(isinstance(value, (list, dict, set)) for value in values):
        return None
    return values


def _iter_rows(rows, skips):
    """Yield the dict rows of ``rows``; non-dict entries count as malformed."""
    if rows is None:
        return
    if isinstance(rows, (str, bytes)):
        skips["malformed_row"] += 1
        return
    try:
        iterator = iter(rows)
    except TypeError:
        skips["malformed_row"] += 1
        return
    for row in iterator:
        if not isinstance(row, dict):
            skips["malformed_row"] += 1
            continue
        yield row


def _inventory_index(inventory_rows):
    """Map InventoryID -> {"code": trimmed InventoryCD, "stock": bool}."""
    index = {}
    for row in _iter_rows(inventory_rows, Counter()):
        item_id = row.get("InventoryID")
        if isinstance(item_id, (list, dict, set)) or item_id is None:
            continue
        if item_id in index:
            continue
        code = row.get("InventoryCD")
        code = _text(code) or ""
        index[item_id] = {"code": code, "stock": _is_true(row.get("StkItem"))}
    return index


def _resolve_item(index, row, skips):
    """The trimmed InventoryCD for row's InventoryID, or None (counted)."""
    item_id = row.get("InventoryID")
    try:
        item = index.get(item_id)
    except TypeError:
        item = None
    if item is None or not item["code"]:
        skips["unknown_item"] += 1
        return None
    if not item["stock"]:
        skips["non_stock_item"] += 1
        return None
    return item["code"]


# ---------------------------------------------------------------------------
# Mappers (internal versions return (rows, skips_by_reason))
# ---------------------------------------------------------------------------


def _map_item_sites(item_site_rows, inventory_rows):
    index = _inventory_index(inventory_rows)
    skips = Counter()
    rows = []
    for raw in _iter_rows(item_site_rows, skips):
        item_code = _resolve_item(index, raw, skips)
        if item_code is None:
            continue
        site_code = _site_code(raw.get("SiteID"))
        if site_code is None:
            skips["missing_site"] += 1
            continue
        min_qty = _parse_number(raw.get("MinQty"))
        safety_stock = _parse_number(raw.get("SafetyStock"))
        max_qty = _parse_number(raw.get("MaxQty"))
        unit_cost = None
        for field in _UNIT_COST_FIELDS:
            candidate = _parse_number(raw.get(field))
            if candidate is not None and candidate > 0:
                unit_cost = candidate
                break
        order_qty = None
        if min_qty is not None and max_qty is not None and max_qty - min_qty > 0:
            order_qty = max_qty - min_qty
        rows.append(
            {
                "item_code": item_code,
                "site_code": site_code,
                "unit_cost": unit_cost,
                "reorder_point": min_qty,
                "safety_stock": safety_stock,
                "max_qty": max_qty,
                "order_qty": order_qty,
                "master_lead_time_days": None,
                "source_system": SOURCE_SYSTEM,
            }
        )
    return rows, dict(skips)


def _map_demand_events(tran_rows, inventory_rows):
    index = _inventory_index(inventory_rows)
    skips = Counter()
    rows = []
    for raw in _iter_rows(tran_rows, skips):
        item_code = _resolve_item(index, raw, skips)
        if item_code is None:
            continue
        try:
            event_type = _DEMAND_EVENT_TYPES.get(
                (raw.get("DocType"), raw.get("TranType"))
            )
        except TypeError:
            event_type = None
        multiplier = _parse_number(raw.get("InvtMult"))
        if event_type is None or multiplier != -1:
            skips["not_demand"] += 1
            continue
        base_qty = _parse_number(raw.get("BaseQty"))
        if base_qty is None or base_qty == 0:
            skips["zero_quantity"] += 1
            continue
        occurred_on = _parse_date(raw.get("TranDate"))
        if occurred_on is None:
            skips["missing_transaction_date"] += 1
            continue
        site_code = _site_code(raw.get("SiteID"))
        if site_code is None:
            skips["missing_site"] += 1
            continue
        rows.append(
            {
                "business_key": _join_key(
                    raw.get("DocType"), raw.get("RefNbr"), raw.get("LineNbr")
                ),
                "item_code": item_code,
                "site_code": site_code,
                "quantity": abs(base_qty),
                "occurred_on": occurred_on,
                "event_type": event_type,
                "source_system": SOURCE_SYSTEM,
            }
        )
    return rows, dict(skips)


def _map_lead_time_observations(receipt_rows, po_line_rows, inventory_rows):
    index = _inventory_index(inventory_rows)
    skips = Counter()
    order_lines = {}
    for raw in _iter_rows(po_line_rows, skips):
        key = _po_key(raw.get("OrderType"), raw.get("OrderNbr"), raw.get("LineNbr"))
        if key is not None and key not in order_lines:
            order_lines[key] = raw
    rows = []
    for raw in _iter_rows(receipt_rows, skips):
        item_code = _resolve_item(index, raw, skips)
        if item_code is None:
            continue
        multiplier = _parse_number(raw.get("InvtMult"))
        if multiplier != 1 or _text(raw.get("PONbr")) is None:
            skips["not_receipt"] += 1
            continue
        key = _po_key(raw.get("POType"), raw.get("PONbr"), raw.get("POLineNbr"))
        order_line = None
        if key is not None:
            try:
                order_line = order_lines.get(key)
            except TypeError:
                order_line = None
        if order_line is None:
            skips["missing_order_line"] += 1
            continue
        ordered_on = _parse_date(order_line.get("OrderDate"))
        received_on = _parse_date(raw.get("ReceiptDate"))
        if ordered_on is None or received_on is None:
            skips["missing_date"] += 1
            continue
        if received_on < ordered_on:
            skips["receipt_before_order"] += 1
            continue
        site_code = _site_code(raw.get("SiteID"))
        if site_code is None:
            skips["missing_site"] += 1
            continue
        rows.append(
            {
                "business_key": _join_key(
                    raw.get("ReceiptType"), raw.get("ReceiptNbr"), raw.get("LineNbr")
                ),
                "supplier_code": _text(raw.get("VendorID")),
                "item_code": item_code,
                "site_code": site_code,
                "ordered_on": ordered_on,
                "received_on": received_on,
                "promised_on": _parse_date(order_line.get("PromisedDate")),
                "source_system": SOURCE_SYSTEM,
            }
        )
    return rows, dict(skips)


def _map_on_hand(site_status_rows, inventory_rows, as_of):
    index = _inventory_index(inventory_rows)
    skips = Counter()
    totals = {}
    for raw in _iter_rows(site_status_rows, skips):
        item_id = raw.get("InventoryID")
        try:
            item = index.get(item_id)
        except TypeError:
            item = None
        if item is None or not item["code"]:
            skips["unknown_item"] += 1
            continue
        if not item["stock"]:
            skips["non_stock_item"] += 1
            continue
        site_code = _site_code(raw.get("SiteID"))
        if site_code is None:
            skips["missing_site"] += 1
            continue
        quantity = _parse_number(raw.get("QtyOnHand")) or 0.0
        totals[(item["code"], site_code)] = (
            totals.get((item["code"], site_code), 0.0) + quantity
        )
    stamped = _as_of_text(as_of)
    rows = [
        {
            "item_code": item_code,
            "site_code": site_code,
            "quantity_on_hand": total,
            "as_of": stamped,
            "source_system": SOURCE_SYSTEM,
        }
        for (item_code, site_code), total in totals.items()
    ]
    return rows, dict(skips)


def _map_open_purchase_lines(po_line_rows, inventory_rows, as_of):
    index = _inventory_index(inventory_rows)
    skips = Counter()
    rows = []
    stamped = _as_of_text(as_of)
    for raw in _iter_rows(po_line_rows, skips):
        item_code = _resolve_item(index, raw, skips)
        if item_code is None:
            continue
        site_code = _site_code(raw.get("SiteID"))
        if site_code is None:
            skips["missing_site"] += 1
            continue
        order_type = _text(raw.get("OrderType"))
        if order_type is None:
            skips["missing_order_type"] += 1
            continue
        if order_type == "DP":
            skips["drop_ship"] += 1
            continue
        if order_type == "BL":
            skips["blanket"] += 1
            continue
        if _text(raw.get("LineType")) == "SV":
            skips["service"] += 1
            continue
        if _is_true(raw.get("Completed")) or _is_true(raw.get("Cancelled")):
            open_units = 0.0
        else:
            open_units = _parse_number(raw.get("BaseOpenQty")) or 0.0
            if open_units < 0.0:
                open_units = 0.0
        rows.append(
            {
                "business_key": _join_key(
                    order_type, raw.get("OrderNbr"), raw.get("LineNbr")
                ),
                "item_code": item_code,
                "site_code": site_code,
                "supplier_code": _text(raw.get("VendorID")),
                "order_type": order_type,
                "open_units": open_units,
                "promised_on": _parse_date(raw.get("PromisedDate")),
                "ordered_on": _parse_date(raw.get("OrderDate")),
                "as_of": stamped,
                "source_system": SOURCE_SYSTEM,
            }
        )
    return rows, dict(skips)


def _map_open_sales_lines(so_line_rows, inventory_rows, as_of):
    index = _inventory_index(inventory_rows)
    skips = Counter()
    rows = []
    stamped = _as_of_text(as_of)
    for raw in _iter_rows(so_line_rows, skips):
        item_code = _resolve_item(index, raw, skips)
        if item_code is None:
            continue
        site_code = _site_code(raw.get("SiteID"))
        if site_code is None:
            skips["missing_site"] += 1
            continue
        order_type = _text(raw.get("OrderType"))
        if order_type is None:
            skips["missing_order_type"] += 1
            continue
        if order_type == "BL":
            skips["blanket"] += 1
            continue
        if order_type == "QT":
            skips["quote"] += 1
            continue
        if _text(raw.get("Operation")) != "I":
            skips["receipt_or_return"] += 1
            continue
        if _text(raw.get("LineType")) == "MI":
            skips["miscellaneous"] += 1
            continue
        if _is_true(raw.get("Completed")) or _is_true(raw.get("Cancelled")):
            open_units = 0.0
        else:
            open_units = _parse_number(raw.get("BaseOpenQty")) or 0.0
            if open_units < 0.0:
                open_units = 0.0
        rows.append(
            {
                "business_key": _join_key(
                    order_type, raw.get("OrderNbr"), raw.get("LineNbr")
                ),
                "item_code": item_code,
                "site_code": site_code,
                "order_type": order_type,
                "open_units": open_units,
                "ship_on": _parse_date(raw.get("ShipDate"))
                or _parse_date(raw.get("RequestDate")),
                "as_of": stamped,
                "source_system": SOURCE_SYSTEM,
            }
        )
    return rows, dict(skips)


def _map_suppliers(baccount_rows):
    """BAccount rows -> suppliers upsert rows (vendors only).

    Keeps only rows whose ``Type`` is "VE"; every other row is counted
    as ``not_vendor``.  ``supplier_code`` is the integer ``BAccountID``
    rendered as text, ``vendor_code`` the trimmed ``AcctCD``, ``name``
    the trimmed ``AcctName`` and ``status`` the ``Status``.
    """
    skips = Counter()
    rows = []
    for raw in _iter_rows(baccount_rows, skips):
        if raw.get("Type") != "VE":
            skips["not_vendor"] += 1
            continue
        supplier_code = _supplier_code(raw.get("BAccountID"))
        if supplier_code is None:
            skips["missing_supplier_code"] += 1
            continue
        rows.append(
            {
                "supplier_code": supplier_code,
                "vendor_code": _text(raw.get("AcctCD")),
                "name": _text(raw.get("AcctName")),
                "status": _text(raw.get("Status")),
                "source_system": SOURCE_SYSTEM,
            }
        )
    return rows, dict(skips)


def _map_supplier_terms(vendor_inventory_rows, inventory_rows):
    """POVendorInventory rows -> supplier_terms upsert rows (active only).

    Keeps only the rows whose ``Active`` is true; every other row is
    counted as ``inactive``.  ``business_key`` is
    ``POVendorInventory:<RecordID>``, ``item_code`` the referenced
    ``InventoryCD`` resolved with the shared inventory index (an
    unresolvable item is skipped and counted) and ``supplier_code`` the
    ``VendorID`` rendered as text the way ``_supplier_code`` renders a
    ``BAccountID`` (a row without one is skipped and counted as
    ``missing_vendor``).  ``min_order_qty``, ``max_order_qty`` and
    ``lot_size`` come from ``MinOrdQty``, ``MaxOrdQty`` and ``LotSize``
    with 0 or a missing value stored as null, ``lead_time_days`` is
    ``VLeadTime`` plus ``AddLeadTimeDays`` (null when ``VLeadTime`` is
    missing) and ``is_default`` the ``IsDefault`` flag.
    """
    index = _inventory_index(inventory_rows)
    skips = Counter()
    rows = []
    for raw in _iter_rows(vendor_inventory_rows, skips):
        if not _is_true(raw.get("Active")):
            skips["inactive"] += 1
            continue
        item_code = _resolve_item(index, raw, skips)
        if item_code is None:
            continue
        supplier_code = _supplier_code(raw.get("VendorID"))
        if supplier_code is None:
            skips["missing_vendor"] += 1
            continue
        vendor_lead_time = _parse_number(raw.get("VLeadTime"))
        if vendor_lead_time is None:
            lead_time_days = None
        else:
            extra = _parse_number(raw.get("AddLeadTimeDays")) or 0.0
            lead_time_days = vendor_lead_time + extra
        rows.append(
            {
                "business_key": _join_key("POVendorInventory", raw.get("RecordID")),
                "item_code": item_code,
                "supplier_code": supplier_code,
                "min_order_qty": _qty_or_null(raw.get("MinOrdQty")),
                "max_order_qty": _qty_or_null(raw.get("MaxOrdQty")),
                "lot_size": _qty_or_null(raw.get("LotSize")),
                "lead_time_days": lead_time_days,
                "is_default": _is_true(raw.get("IsDefault")),
                "source_system": SOURCE_SYSTEM,
            }
        )
    return rows, dict(skips)


def _map_inventory_items(inventory_rows):
    """InventoryItem rows -> inventory_items upsert rows (stock items only).

    Keeps only the rows whose ``StkItem`` is true; every other row is
    counted as ``non_stock_item``.  ``item_code`` is the trimmed
    ``InventoryCD`` (a stock row without one is skipped and counted as
    ``missing_item_code``), ``description`` the trimmed ``Descr`` (null
    when missing or blank) and ``uom`` the trimmed ``BaseUnit``.
    """
    skips = Counter()
    rows = []
    for raw in _iter_rows(inventory_rows, skips):
        if not _is_true(raw.get("StkItem")):
            skips["non_stock_item"] += 1
            continue
        item_code = _text(raw.get("InventoryCD"))
        if item_code is None:
            skips["missing_item_code"] += 1
            continue
        rows.append(
            {
                "item_code": item_code,
                "description": _text(raw.get("Descr")),
                "uom": _text(raw.get("BaseUnit")),
                "source_system": SOURCE_SYSTEM,
            }
        )
    return rows, dict(skips)


# ---------------------------------------------------------------------------
# Public mappers (pure: they return the row dicts only)
# ---------------------------------------------------------------------------


def map_item_sites(item_site_rows, inventory_rows):
    """INItemSite rows -> item_sites upsert rows (source_system "acumatica")."""
    return _map_item_sites(item_site_rows, inventory_rows)[0]


def map_demand_events(tran_rows, inventory_rows):
    """INTran rows -> demand_events upsert rows (source_system "acumatica")."""
    return _map_demand_events(tran_rows, inventory_rows)[0]


def map_lead_time_observations(receipt_rows, po_line_rows, inventory_rows):
    """POReceiptLine rows -> lead_time_observations upsert rows."""
    return _map_lead_time_observations(receipt_rows, po_line_rows, inventory_rows)[0]


def map_on_hand(site_status_rows, inventory_rows, as_of):
    """INSiteStatus rows -> on_hand upsert rows stamped with ``as_of``."""
    return _map_on_hand(site_status_rows, inventory_rows, as_of)[0]


def map_open_purchase_lines(po_line_rows, inventory_rows, as_of):
    """POLine rows -> open_purchase_lines upsert rows stamped with ``as_of``."""
    return _map_open_purchase_lines(po_line_rows, inventory_rows, as_of)[0]


def map_open_sales_lines(so_line_rows, inventory_rows, as_of):
    """SOLine rows -> open_sales_lines upsert rows stamped with ``as_of``."""
    return _map_open_sales_lines(so_line_rows, inventory_rows, as_of)[0]


def map_suppliers(baccount_rows):
    """BAccount rows -> suppliers upsert rows (source_system "acumatica")."""
    return _map_suppliers(baccount_rows)[0]


def map_supplier_terms(vendor_inventory_rows, inventory_rows):
    """POVendorInventory rows -> supplier_terms upsert rows (active only,
    source_system "acumatica")."""
    return _map_supplier_terms(vendor_inventory_rows, inventory_rows)[0]


# ---------------------------------------------------------------------------
# Sync runner
# ---------------------------------------------------------------------------


def run_sync(
    iter_entities=None,
    as_of=None,
    item_sites_repo=None,
    demand_events_repo=None,
    lead_time_observations_repo=None,
    on_hand_repo=None,
    open_purchase_lines_repo=None,
    inventory_items_repo=None,
    suppliers_repo=None,
    open_sales_lines_repo=None,
    supplier_terms_repo=None,
):
    """Read all six entity sets and BAccount, then write the mapped rows once.

    Reads ``InventoryItem``, ``INItemSite``, ``INSiteStatus``, ``INTran``,
    ``POLine`` and ``POReceiptLine`` through ``iter_entities`` (defaulting to
    :func:`app.feed.aidw_client.iter_entities`). Exhausting each iterator is
    read completion; no row-count or completion signal from the ERP is
    required or validated, so a partial extract is accepted. Any read failure
    raises before anything is written.

    When the sync reads from the configured AIDW feed (no injected
    ``iter_entities`` double), the ``BAccount``, ``SOLine`` and
    ``POVendorInventory`` entity sets are also read through the same feed
    reader after the six reads (``SOLine`` right after ``BAccount`` and
    ``POVendorInventory`` right after ``SOLine``); the vendor rows are
    written through the suppliers repository's ``upsert_many``, the
    mapped sales-line rows through the open sales lines repository's
    ``upsert_many`` and the mapped vendor-inventory rows through the
    supplier terms repository's ``upsert_many`` (each defaulting to the
    real repository); an injected ``iter_entities`` double stands in for
    the six entity sets and is not asked for ``BAccount``,
    ``SOLine`` or ``POVendorInventory``.
    Each read is optional: a feed that does not expose
    ``BAccount`` raises ``AidwFeedError``, in which case the sync records
    ``{"suppliers": "BAccount not available"}`` under ``"skipped"``, writes
    no suppliers and everything else proceeds exactly as today; a feed
    that does not expose ``SOLine`` raises ``AidwFeedError`` the same
    way, in which case the sync records
    ``{"open_sales_lines": "SOLine not available"}`` under
    ``"skipped"`` and writes no open sales lines; a feed that does not
    expose ``POVendorInventory`` raises ``AidwFeedError`` the same way,
    in which case the sync records
    ``{"supplier_terms": "POVendorInventory not available"}`` under
    ``"skipped"`` and writes no supplier terms; any other read failure
    raises before anything is written, as today.

    Between the last read and mapping and the first write, the shared
    code-clash guard (``app.sync.clashes.apply_clash_guard``) removes
    rows of a pair ``item_sites`` stores under a different
    ``source_system`` — the first holder keeps its row — records each
    held pair in ``erp_code_clashes`` and resolves this source's
    still-open clash rows whose pair was not held this time; supplier
    terms and suppliers are untouched.  When a pair is held the result
    gains ``clashes`` — the number of held pairs; with no clash the
    result shape is unchanged.  The guard is skipped when the injected
    item-sites repository has no ``held_by_others``.

    Before the writes, the inventory-item rows pass the shared
    item-name guard (``app.sync.clashes.apply_item_name_guard``): the
    rows of a code ``inventory_items`` stores under a different
    ``source_system`` are removed — the first holder keeps its row
    exactly as it was — and an item master row names no site, so
    nothing is added to ``erp_code_clashes``.  When a row is removed
    the result gains ``item_names_held`` — the number of rows removed
    — only when it is above 0.  The guard is skipped when the
    injected inventory-items repository has no ``held_by_others``.

    Only after all the reads succeed, the mapped rows are written through
    the repositories' ``upsert_many`` with ``as_of`` (defaulting to the
    sync's start time) stamped on the on_hand, open_purchase_lines and
    open_sales_lines rows.  The item master rows of the ``InventoryItem``
    read are
    written through the inventory items repository's ``upsert_many``
    (defaulting to the real repository) after the other writes, with no
    new read.  Returns ``{"item_sites": {...}, "demand_events": {...},
    "lead_time_observations": {...}, "on_hand": {...},
    "open_purchase_lines": {...}, "inventory_items": {...},
    "suppliers": {...}, "open_sales_lines": {...},
    "supplier_terms": {...},
    "skipped": {...}}`` holding each repository's
    counts and the skip counts by reason, the open_purchase_lines skips
    nested under the ``"open_purchase_lines"`` key of ``"skipped"``;
    when the ``BAccount`` set is available, the ``"suppliers"`` entry
    holds the suppliers repository's counts, when the ``SOLine`` set is
    available the ``"open_sales_lines"`` entry holds the open sales lines
    repository's counts, when the ``POVendorInventory`` set is available
    the ``"supplier_terms"`` entry holds the supplier terms repository's
    counts, and the
    ``"inventory_items"`` entry always holds the inventory items
    repository's counts, and when the item-name guard removed a row
    the ``"item_names_held"`` entry holds the number of rows removed.
    """
    started_at = as_of if as_of is not None else datetime.now(UTC)
    feed_iter = (
        iter_entities if iter_entities is not None else aidw_client.iter_entities
    )
    reads = {}
    for entity_set in _ENTITY_SETS:
        reads[entity_set] = list(feed_iter(entity_set))

    item_site_rows, item_site_skips = _map_item_sites(
        reads["INItemSite"], reads["InventoryItem"]
    )
    demand_rows, demand_skips = _map_demand_events(
        reads["INTran"], reads["InventoryItem"]
    )
    lead_time_rows, lead_time_skips = _map_lead_time_observations(
        reads["POReceiptLine"], reads["POLine"], reads["InventoryItem"]
    )
    on_hand_rows, on_hand_skips = _map_on_hand(
        reads["INSiteStatus"], reads["InventoryItem"], started_at
    )
    open_po_rows, open_po_skips = _map_open_purchase_lines(
        reads["POLine"], reads["InventoryItem"], started_at
    )
    inventory_item_rows, inventory_item_skips = _map_inventory_items(
        reads["InventoryItem"]
    )

    # The BAccount read happens only when the sync reads from the
    # configured AIDW feed client: an injected iter_entities double
    # stands in for the six entity sets and is not asked for BAccount.
    supplier_rows = None
    open_sales_rows = None
    supplier_term_rows = None
    if iter_entities is None:
        try:
            supplier_rows = list(feed_iter("BAccount"))
        except AidwFeedError:
            # The feed does not expose BAccount: the suppliers read model
            # is skipped and the six read models proceed as usual.
            supplier_rows = None
        try:
            open_sales_rows = list(feed_iter("SOLine"))
        except AidwFeedError:
            # The feed does not expose SOLine: the open sales lines read
            # model is skipped and the other read models proceed as usual.
            open_sales_rows = None
        try:
            supplier_term_rows = list(feed_iter("POVendorInventory"))
        except AidwFeedError:
            # The feed does not expose POVendorInventory: the supplier
            # terms read model is skipped and the other read models
            # proceed as usual.
            supplier_term_rows = None

    # The open sales lines are mapped before the first write (feed
    # mode only): the code-clash guard needs their pairs.
    open_so_rows: list[dict] = []
    open_so_skips: Counter = Counter()
    if iter_entities is None and open_sales_rows is not None:
        open_so_rows, open_so_skips = _map_open_sales_lines(
            open_sales_rows, reads["InventoryItem"], started_at
        )

    skipped = Counter()
    skipped.update(open_so_skips)
    skipped.update(item_site_skips)
    skipped.update(demand_skips)
    skipped.update(lead_time_skips)
    skipped.update(on_hand_skips)
    skipped.update(inventory_item_skips)

    if item_sites_repo is None:
        item_sites_repo = ItemSitePostgresRepository()
    if demand_events_repo is None:
        demand_events_repo = DemandEventPostgresRepository()
    if lead_time_observations_repo is None:
        lead_time_observations_repo = LeadTimeObservationPostgresRepository()
    if on_hand_repo is None:
        on_hand_repo = OnHandPostgresRepository()
    if open_purchase_lines_repo is None:
        open_purchase_lines_repo = OpenPurchaseLinePostgresRepository()
    if inventory_items_repo is None:
        inventory_items_repo = InventoryItemPostgresRepository()
    if supplier_terms_repo is None:
        supplier_terms_repo = SupplierTermsPostgresRepository()

    # Then the shared code-clash guard: it removes rows of a pair
    # item_sites stores under a different source_system — the first
    # holder keeps its row — records each held pair in
    # erp_code_clashes and resolves this source's still-open clash
    # rows whose pair was not held this time.  Skipped when the
    # item-sites repository has no held_by_others.
    clash_count = apply_clash_guard(
        SOURCE_SYSTEM,
        [
            item_site_rows,
            on_hand_rows,
            demand_rows,
            lead_time_rows,
            open_po_rows,
            open_so_rows,
        ],
        item_sites_repo,
    )

    # Then the item-name guard: it removes the rows of a code
    # inventory_items stores under a different source_system — the
    # first holder keeps its row exactly as it was — and an item
    # master row names no site, so nothing is added to
    # erp_code_clashes.  Skipped when the inventory-items
    # repository has no held_by_others.
    item_names_held = apply_item_name_guard(
        SOURCE_SYSTEM, inventory_item_rows, inventory_items_repo
    )

    result = {
        "item_sites": item_sites_repo.upsert_many(item_site_rows),
        "demand_events": demand_events_repo.upsert_many(demand_rows),
        "lead_time_observations": lead_time_observations_repo.upsert_many(
            lead_time_rows
        ),
        "on_hand": on_hand_repo.upsert_many(on_hand_rows),
        "open_purchase_lines": open_purchase_lines_repo.upsert_many(open_po_rows),
    }
    if iter_entities is None:
        if supplier_rows is None:
            skipped["suppliers"] = "BAccount not available"
        else:
            if suppliers_repo is None:
                suppliers_repo = SupplierPostgresRepository()
            mapped_suppliers, supplier_skips = _map_suppliers(supplier_rows)
            skipped.update(supplier_skips)
            result["suppliers"] = suppliers_repo.upsert_many(mapped_suppliers)
        if open_sales_rows is None:
            skipped["open_sales_lines"] = "SOLine not available"
        else:
            if open_sales_lines_repo is None:
                open_sales_lines_repo = OpenSalesLinePostgresRepository()
            result["open_sales_lines"] = open_sales_lines_repo.upsert_many(open_so_rows)
        if supplier_term_rows is None:
            skipped["supplier_terms"] = "POVendorInventory not available"
        else:
            supplier_term_rows_mapped, term_skips = _map_supplier_terms(
                supplier_term_rows, reads["InventoryItem"]
            )
            skipped.update(term_skips)
            result["supplier_terms"] = supplier_terms_repo.upsert_many(
                supplier_term_rows_mapped
            )
    # The item master rows come from the InventoryItem read the sync
    # already made: no new read, written after the other writes.
    result["inventory_items"] = inventory_items_repo.upsert_many(inventory_item_rows)
    result["skipped"] = {
        **dict(skipped),
        "open_purchase_lines": dict(open_po_skips),
    }
    if item_names_held > 0:
        result["item_names_held"] = item_names_held
    if clash_count > 0:
        result["clashes"] = clash_count
    return result
