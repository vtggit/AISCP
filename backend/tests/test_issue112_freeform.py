"""Issue #112 proof: open_purchase_lines mapping from POLine feed rows.

Against in-memory feed rows and a fake repository (no network):
  * an open RO goods line for a stock item maps to a row with open_units
    equal to BaseOpenQty, promised_on and ordered_on parsed from
    PromisedDate and OrderDate, and the business key joined from
    OrderType, OrderNbr and LineNbr;
  * a line with Completed true and a line with Cancelled true (both with
    BaseOpenQty above 0 and no Status field in the feed) map to open_units
    0, and a negative BaseOpenQty never maps below 0;
  * DP (drop-ship), BL (blanket) and SV (service) lines and a non-stock
    item are skipped and counted by reason;
  * run_sync passes the mapped rows to the open-purchase-lines repository
    and reports its upsert result under "open_purchase_lines" and its skip
    counts under "open_purchase_lines" in "skipped".

Every date is a fixed calendar date in the past (as-of 2026-06-30), never
today's date.
"""

from datetime import UTC, date, datetime

from app.sync.acumatica import (
    _map_open_purchase_lines,
    map_open_purchase_lines,
    run_sync,
)

AS_OF = datetime(2026, 6, 30, 0, 0, 0, tzinfo=UTC)
AS_OF_ISO = "2026-06-30T00:00:00+00:00"

# WIDGET-1's InventoryCD is padded with whitespace and must be trimmed.
# NONSTOCK-1 is a non-stock item and must be skipped.
INVENTORY_ROWS = [
    {"InventoryID": 1, "InventoryCD": "  WIDGET-1  ", "StkItem": True},
    {"InventoryID": 2, "InventoryCD": "WIDGET-2", "StkItem": True},
    {"InventoryID": 3, "InventoryCD": "NONSTOCK-1", "StkItem": False},
]

# The feed's POLine rows carry the boolean fields Completed and Cancelled
# and have NO Status field, so completion is decided from those two fields
# only.
PO_LINE_ROWS = [
    # An open RO goods line for a stock item: open_units is BaseOpenQty,
    # the dates are parsed and the business key is the join of
    # OrderType, OrderNbr and LineNbr.
    {
        "OrderType": "RO",
        "OrderNbr": "PO-100",
        "LineNbr": 1,
        "InventoryID": 1,
        "SiteID": 4,
        "LineType": "G",
        "VendorID": "V-9",
        "BaseOpenQty": 25,
        "Completed": False,
        "Cancelled": False,
        "PromisedDate": "2026-07-15T00:00:00Z",
        "OrderDate": "2026-06-01T00:00:00Z",
    },
    # Completed (BaseOpenQty above 0, no Status field): open_units 0.
    {
        "OrderType": "RO",
        "OrderNbr": "PO-100",
        "LineNbr": 2,
        "InventoryID": 1,
        "SiteID": 4,
        "LineType": "G",
        "VendorID": "V-9",
        "BaseOpenQty": 40,
        "Completed": True,
        "Cancelled": False,
        "PromisedDate": "2026-07-20T00:00:00Z",
        "OrderDate": "2026-06-02T00:00:00Z",
    },
    # Cancelled (BaseOpenQty above 0, no Status field): open_units 0.
    {
        "OrderType": "RO",
        "OrderNbr": "PO-101",
        "LineNbr": 1,
        "InventoryID": 2,
        "SiteID": 5,
        "LineType": "G",
        "VendorID": "V-2",
        "BaseOpenQty": 30,
        "Completed": False,
        "Cancelled": True,
        "PromisedDate": "2026-07-21T00:00:00Z",
        "OrderDate": "2026-06-03T00:00:00Z",
    },
    # A negative BaseOpenQty is clamped to 0 (never negative).
    {
        "OrderType": "RO",
        "OrderNbr": "PO-101",
        "LineNbr": 2,
        "InventoryID": 2,
        "SiteID": 5,
        "LineType": "G",
        "VendorID": "V-2",
        "BaseOpenQty": -12,
        "Completed": False,
        "Cancelled": False,
        "PromisedDate": "2026-07-22T00:00:00Z",
        "OrderDate": "2026-06-04T00:00:00Z",
    },
    # A drop-ship line: skipped and counted.
    {
        "OrderType": "DP",
        "OrderNbr": "DP-1",
        "LineNbr": 1,
        "InventoryID": 1,
        "SiteID": 4,
        "LineType": "G",
        "VendorID": "V-9",
        "BaseOpenQty": 7,
        "Completed": False,
        "Cancelled": False,
    },
    # A blanket line: skipped and counted.
    {
        "OrderType": "BL",
        "OrderNbr": "BL-1",
        "LineNbr": 1,
        "InventoryID": 1,
        "SiteID": 4,
        "LineType": "G",
        "VendorID": "V-9",
        "BaseOpenQty": 9,
        "Completed": False,
        "Cancelled": False,
    },
    # A service line: skipped and counted.
    {
        "OrderType": "RO",
        "OrderNbr": "PO-102",
        "LineNbr": 1,
        "InventoryID": 1,
        "SiteID": 4,
        "LineType": "SV",
        "VendorID": "V-9",
        "BaseOpenQty": 11,
        "Completed": False,
        "Cancelled": False,
    },
    # A non-stock item: skipped and counted.
    {
        "OrderType": "RO",
        "OrderNbr": "PO-103",
        "LineNbr": 1,
        "InventoryID": 3,
        "SiteID": 4,
        "LineType": "G",
        "VendorID": "V-9",
        "BaseOpenQty": 13,
        "Completed": False,
        "Cancelled": False,
    },
]

EXPECTED_PO_ROWS = [
    {
        "business_key": "RO:PO-100:1",
        "item_code": "WIDGET-1",
        "site_code": "4",
        "supplier_code": "V-9",
        "order_type": "RO",
        "open_units": 25.0,
        "promised_on": date(2026, 7, 15),
        "ordered_on": date(2026, 6, 1),
        "as_of": AS_OF_ISO,
        "source_system": "acumatica",
    },
    {
        "business_key": "RO:PO-100:2",
        "item_code": "WIDGET-1",
        "site_code": "4",
        "supplier_code": "V-9",
        "order_type": "RO",
        "open_units": 0.0,
        "promised_on": date(2026, 7, 20),
        "ordered_on": date(2026, 6, 2),
        "as_of": AS_OF_ISO,
        "source_system": "acumatica",
    },
    {
        "business_key": "RO:PO-101:1",
        "item_code": "WIDGET-2",
        "site_code": "5",
        "supplier_code": "V-2",
        "order_type": "RO",
        "open_units": 0.0,
        "promised_on": date(2026, 7, 21),
        "ordered_on": date(2026, 6, 3),
        "as_of": AS_OF_ISO,
        "source_system": "acumatica",
    },
    {
        "business_key": "RO:PO-101:2",
        "item_code": "WIDGET-2",
        "site_code": "5",
        "supplier_code": "V-2",
        "order_type": "RO",
        "open_units": 0.0,
        "promised_on": date(2026, 7, 22),
        "ordered_on": date(2026, 6, 4),
        "as_of": AS_OF_ISO,
        "source_system": "acumatica",
    },
]

EXPECTED_SKIPS = {
    "drop_ship": 1,
    "blanket": 1,
    "service": 1,
    "non_stock_item": 1,
}

ENTITY_ROWS = {
    "InventoryItem": INVENTORY_ROWS,
    "INItemSite": [],
    "INSiteStatus": [],
    "INTran": [],
    "POLine": PO_LINE_ROWS,
    "POReceiptLine": [],
}


class _UpsertRecorder:
    """Stands in for a repository: records rows instead of writing them."""

    def __init__(self):
        self.calls = []

    def upsert_many(self, rows):
        self.calls.append(rows)
        return {"inserted": len(rows), "updated": 0}


def _make_feed_reader(entity_rows):
    """A stand-in for aidw_client.iter_entities that records every read."""
    read_calls = []

    def iter_entities(entity_set, *args, **kwargs):
        read_calls.append(entity_set)
        return iter(list(entity_rows[entity_set]))

    return iter_entities, read_calls


def test_issue112_freeform():
    today = datetime.now(UTC).date()

    # 1. map_open_purchase_lines: an open RO goods line for a stock item
    #    maps to a row with open_units equal to BaseOpenQty, promised_on
    #    and ordered_on parsed from PromisedDate and OrderDate, the
    #    business key joined from OrderType, OrderNbr and LineNbr,
    #    supplier_code from VendorID, and the sync's as_of stamped; a
    #    completed line and a cancelled line (both with BaseOpenQty above
    #    0 and no Status field) map to open_units 0; a negative
    #    BaseOpenQty never maps below 0; DP, BL and SV lines and a
    #    non-stock item are skipped and counted by reason.
    rows, skips = _map_open_purchase_lines(PO_LINE_ROWS, INVENTORY_ROWS, AS_OF)
    assert rows == EXPECTED_PO_ROWS
    assert skips == EXPECTED_SKIPS
    assert map_open_purchase_lines(PO_LINE_ROWS, INVENTORY_ROWS, AS_OF) == (
        EXPECTED_PO_ROWS
    )

    # Every mapped date is a fixed calendar date in the past, never
    # today's date.
    for row in EXPECTED_PO_ROWS:
        assert row["promised_on"] is not None and row["promised_on"] < today
        assert row["ordered_on"] is not None and row["ordered_on"] < today
        assert row["as_of"] != today.isoformat()

    # Completed/Cancelled are read with the module's _is_true: the string
    # "true" counts as true even though the feed carries no Status field.
    string_flag_rows = [
        {
            "OrderType": "RO",
            "OrderNbr": "PO-104",
            "LineNbr": 1,
            "InventoryID": 1,
            "SiteID": 4,
            "LineType": "G",
            "VendorID": "V-9",
            "BaseOpenQty": 15,
            "Completed": "true",
            "Cancelled": "false",
        }
    ]
    string_flag_mapped = map_open_purchase_lines(
        string_flag_rows, INVENTORY_ROWS, AS_OF
    )
    assert len(string_flag_mapped) == 1
    assert string_flag_mapped[0]["open_units"] == 0.0

    # A line with no dates (and a numeric-string BaseOpenQty, no VendorID
    # and no LineType) maps with promised_on and ordered_on None.
    sparse_rows = [
        {
            "OrderType": "RO",
            "OrderNbr": "PO-105",
            "LineNbr": 1,
            "InventoryID": 1,
            "SiteID": 4,
            "BaseOpenQty": "7.5",
            "Completed": False,
            "Cancelled": False,
        }
    ]
    sparse = map_open_purchase_lines(sparse_rows, INVENTORY_ROWS, AS_OF)
    assert len(sparse) == 1
    assert sparse[0]["open_units"] == 7.5
    assert sparse[0]["promised_on"] is None
    assert sparse[0]["ordered_on"] is None
    assert sparse[0]["supplier_code"] is None
    assert sparse[0]["business_key"] == "RO:PO-105:1"

    # as_of is stamped exactly as map_on_hand stamps it: a date as_of is
    # that date, a string as_of passes through verbatim, never replaced by
    # the current time.
    as_of_date = date(2026, 6, 30)
    assert [
        row["as_of"]
        for row in map_open_purchase_lines(PO_LINE_ROWS, INVENTORY_ROWS, as_of_date)
    ] == [as_of_date.isoformat()] * len(EXPECTED_PO_ROWS)
    assert (
        map_open_purchase_lines(PO_LINE_ROWS, INVENTORY_ROWS, AS_OF_ISO)[0]["as_of"]
        == AS_OF_ISO
    )

    # 2. Hostile rows never raise: non-dict rows and rows with missing or
    #    unparseable fields are skipped and counted, never written.
    hostile_rows, hostile_skips = _map_open_purchase_lines(
        [
            None,
            "junk",
            5,
            {},
            {"InventoryID": 1, "SiteID": None, "OrderType": "RO"},
            {
                "InventoryID": 1,
                "SiteID": 4,
                "BaseOpenQty": "not-a-number",
                "Completed": False,
                "Cancelled": False,
            },
        ],
        INVENTORY_ROWS,
        AS_OF,
    )
    assert hostile_rows == []
    assert hostile_skips == {
        "malformed_row": 3,
        "unknown_item": 1,
        "missing_site": 1,
        "missing_order_type": 1,
    }

    # 3. run_sync passes the mapped POLine rows to the open-purchase-lines
    #    repository and reports its result under "open_purchase_lines" and
    #    its skip counts under "open_purchase_lines" in "skipped"; nothing
    #    else in the sync changes.
    item_sites_recorder = _UpsertRecorder()
    demand_events_recorder = _UpsertRecorder()
    lead_time_recorder = _UpsertRecorder()
    on_hand_recorder = _UpsertRecorder()
    open_po_recorder = _UpsertRecorder()
    fake_iter_entities, read_calls = _make_feed_reader(ENTITY_ROWS)

    result = run_sync(
        iter_entities=fake_iter_entities,
        as_of=AS_OF,
        item_sites_repo=item_sites_recorder,
        demand_events_repo=demand_events_recorder,
        lead_time_observations_repo=lead_time_recorder,
        on_hand_repo=on_hand_recorder,
        open_purchase_lines_repo=open_po_recorder,
    )

    assert read_calls == [
        "InventoryItem",
        "INItemSite",
        "INSiteStatus",
        "INTran",
        "POLine",
        "POReceiptLine",
    ]
    assert open_po_recorder.calls == [EXPECTED_PO_ROWS]
    assert result["open_purchase_lines"] == {"inserted": 4, "updated": 0}
    assert result["skipped"]["open_purchase_lines"] == EXPECTED_SKIPS
    assert result["item_sites"] == {"inserted": 0, "updated": 0}
    assert result["demand_events"] == {"inserted": 0, "updated": 0}
    assert result["lead_time_observations"] == {"inserted": 0, "updated": 0}
    assert result["on_hand"] == {"inserted": 0, "updated": 0}
