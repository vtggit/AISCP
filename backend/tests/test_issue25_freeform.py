"""Issue #25 proof: Acumatica feed sync — pure mappers and read-then-write
run_sync.

Against fixture rows:
  * map_demand_events uses BaseQty (never Qty), maps each allowed
    (DocType, TranType) pair to its event_type, and drops a transfer, an
    adjustment and a stock-in line; a kit assembly keeps only its -1 lines;
    a non-stock item is skipped and InventoryCD is trimmed,
  * map_lead_time_observations joins a receipt to its POLine and skips a
    receipt dated before its order (counted),
  * map_item_sites holds the unit_cost fallback and the order_qty rule,
  * map_on_hand sums QtyOnHand over SubItemID per (InventoryID, SiteID),
  * run_sync calls iter_entities for all six entity sets and each
    upsert_many with the mapped rows, and when one read raises no
    upsert_many is called.
"""

from datetime import UTC, date, datetime

import pytest

from app.sync.acumatica import (
    map_demand_events,
    map_item_sites,
    map_lead_time_observations,
    map_on_hand,
    run_sync,
)

AS_OF = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
AS_OF_ISO = "2026-10-01T12:00:00+00:00"

# WIDGET-1's InventoryCD is padded with whitespace and must be trimmed.
# NONSTOCK-1 is a non-stock item and must be skipped by every mapper.
INVENTORY_ROWS = [
    {"InventoryID": 1, "InventoryCD": "  WIDGET-1  ", "StkItem": True},
    {"InventoryID": 2, "InventoryCD": "WIDGET-2", "StkItem": True},
    {"InventoryID": 3, "InventoryCD": "NONSTOCK-1", "StkItem": False},
]

ITEM_SITE_ROWS = [
    # AvgCost is the first positive cost -> unit_cost 2.5; order_qty 50-10.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "MinQty": 10,
        "SafetyStock": 20,
        "MaxQty": 50,
        "AvgCost": 2.5,
        "LastCost": 0,
        "StdCost": 1.0,
    },
    # AvgCost and LastCost are not positive -> unit_cost falls back to
    # StdCost 3.25; MaxQty - MinQty == 0 -> order_qty empty.
    {
        "InventoryID": 1,
        "SiteID": 5,
        "MinQty": 5,
        "SafetyStock": 0,
        "MaxQty": 5,
        "AvgCost": 0,
        "LastCost": -1,
        "StdCost": 3.25,
    },
    # No positive cost at all -> unit_cost empty; MaxQty < MinQty ->
    # order_qty empty.
    {
        "InventoryID": 2,
        "SiteID": 4,
        "MinQty": 8,
        "SafetyStock": 4,
        "MaxQty": 2,
        "AvgCost": -3,
        "LastCost": 0,
        "StdCost": 0,
    },
    # Non-stock item: skipped.
    {
        "InventoryID": 3,
        "SiteID": 4,
        "MinQty": 1,
        "SafetyStock": 1,
        "MaxQty": 2,
        "AvgCost": 9.9,
    },
]

EXPECTED_ITEM_SITE_ROWS = [
    {
        "item_code": "WIDGET-1",
        "site_code": "4",
        "unit_cost": 2.5,
        "reorder_point": 10.0,
        "safety_stock": 20.0,
        "max_qty": 50.0,
        "order_qty": 40.0,
        "master_lead_time_days": None,
        "source_system": "acumatica",
    },
    {
        "item_code": "WIDGET-1",
        "site_code": "5",
        "unit_cost": 3.25,
        "reorder_point": 5.0,
        "safety_stock": 0.0,
        "max_qty": 5.0,
        "order_qty": None,
        "master_lead_time_days": None,
        "source_system": "acumatica",
    },
    {
        "item_code": "WIDGET-2",
        "site_code": "4",
        "unit_cost": None,
        "reorder_point": 8.0,
        "safety_stock": 4.0,
        "max_qty": 2.0,
        "order_qty": None,
        "master_lead_time_days": None,
        "source_system": "acumatica",
    },
]

# The "Qty" values below are decoys: BaseQty is the quantity that counts.
TRAN_ROWS = [
    # (I, INV) -> shipment; quantity 12 comes from BaseQty, not Qty 99.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "DocType": "I",
        "TranType": "INV",
        "InvtMult": -1,
        "BaseQty": -12,
        "Qty": 99,
        "TranDate": "2026-01-15T08:30:00Z",
        "RefNbr": "SO-100",
        "LineNbr": 1,
    },
    # (M, III) -> production_issue.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "DocType": "M",
        "TranType": "III",
        "InvtMult": -1,
        "BaseQty": -3,
        "Qty": 88,
        "TranDate": "2026-01-16T00:00:00",
        "RefNbr": "MO-200",
        "LineNbr": 2,
    },
    # (P, ASY) -> kit_issue.
    {
        "InventoryID": 2,
        "SiteID": 4,
        "DocType": "P",
        "TranType": "ASY",
        "InvtMult": -1,
        "BaseQty": -5,
        "Qty": 77,
        "TranDate": "2026-01-17T12:00:00",
        "RefNbr": "KIT-300",
        "LineNbr": 3,
    },
    # (I, III) -> production_issue.
    {
        "InventoryID": 2,
        "SiteID": 5,
        "DocType": "I",
        "TranType": "III",
        "InvtMult": -1,
        "BaseQty": -2,
        "Qty": 66,
        "TranDate": "2026-01-18T09:00:00",
        "RefNbr": "WO-400",
        "LineNbr": 4,
    },
    # A transfer (-1) is dropped.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "DocType": "T",
        "TranType": "TRF",
        "InvtMult": -1,
        "BaseQty": -9,
        "Qty": 9,
        "TranDate": "2026-01-18T10:00:00",
        "RefNbr": "TRF-1",
        "LineNbr": 1,
    },
    # An adjustment (-1) is dropped.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "DocType": "A",
        "TranType": "ADJ",
        "InvtMult": -1,
        "BaseQty": -1,
        "Qty": 1,
        "TranDate": "2026-01-18T11:00:00",
        "RefNbr": "ADJ-1",
        "LineNbr": 1,
    },
    # A stock-in line (+1) is dropped.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "DocType": "I",
        "TranType": "RCV",
        "InvtMult": 1,
        "BaseQty": 50,
        "Qty": 50,
        "TranDate": "2026-01-19T09:00:00",
        "RefNbr": "PO-1",
        "LineNbr": 1,
    },
    # A kit assembly: its +1 line is dropped, its -1 line is kept.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "DocType": "P",
        "TranType": "ASY",
        "InvtMult": 1,
        "BaseQty": 7,
        "Qty": 7,
        "TranDate": "2026-01-20T09:00:00",
        "RefNbr": "KIT-ASSEMBLY",
        "LineNbr": 1,
    },
    {
        "InventoryID": 1,
        "SiteID": 4,
        "DocType": "P",
        "TranType": "ASY",
        "InvtMult": -1,
        "BaseQty": -4,
        "Qty": 4,
        "TranDate": "2026-01-20T09:00:00",
        "RefNbr": "KIT-ASSEMBLY",
        "LineNbr": 2,
    },
    # Zero BaseQty is skipped.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "DocType": "I",
        "TranType": "INV",
        "InvtMult": -1,
        "BaseQty": 0,
        "Qty": 0,
        "TranDate": "2026-01-21T09:00:00",
        "RefNbr": "SO-101",
        "LineNbr": 1,
    },
    # A non-stock item is skipped.
    {
        "InventoryID": 3,
        "SiteID": 4,
        "DocType": "I",
        "TranType": "INV",
        "InvtMult": -1,
        "BaseQty": -6,
        "Qty": 6,
        "TranDate": "2026-01-22T09:00:00",
        "RefNbr": "SO-102",
        "LineNbr": 1,
    },
]

EXPECTED_DEMAND_ROWS = [
    {
        "business_key": "I:SO-100:1",
        "item_code": "WIDGET-1",
        "site_code": "4",
        "quantity": 12.0,
        "occurred_on": date(2026, 1, 15),
        "event_type": "shipment",
        "source_system": "acumatica",
    },
    {
        "business_key": "M:MO-200:2",
        "item_code": "WIDGET-1",
        "site_code": "4",
        "quantity": 3.0,
        "occurred_on": date(2026, 1, 16),
        "event_type": "production_issue",
        "source_system": "acumatica",
    },
    {
        "business_key": "P:KIT-300:3",
        "item_code": "WIDGET-2",
        "site_code": "4",
        "quantity": 5.0,
        "occurred_on": date(2026, 1, 17),
        "event_type": "kit_issue",
        "source_system": "acumatica",
    },
    {
        "business_key": "I:WO-400:4",
        "item_code": "WIDGET-2",
        "site_code": "5",
        "quantity": 2.0,
        "occurred_on": date(2026, 1, 18),
        "event_type": "production_issue",
        "source_system": "acumatica",
    },
    {
        "business_key": "P:KIT-ASSEMBLY:2",
        "item_code": "WIDGET-1",
        "site_code": "4",
        "quantity": 4.0,
        "occurred_on": date(2026, 1, 20),
        "event_type": "kit_issue",
        "source_system": "acumatica",
    },
]

PO_LINE_ROWS = [
    {
        "OrderType": "P",
        "OrderNbr": "PO-100",
        "LineNbr": 1,
        "OrderDate": "2026-01-05T00:00:00Z",
    },
    {
        "OrderType": "P",
        "OrderNbr": "PO-101",
        "LineNbr": 1,
        "OrderDate": "2026-02-01T00:00:00Z",
    },
    {
        "OrderType": "P",
        "OrderNbr": "PO-102",
        "LineNbr": 1,
        "OrderDate": "2026-01-02T00:00:00Z",
    },
]

RECEIPT_ROWS = [
    # Joins to PO-100 line 1: ordered 2026-01-05, received 2026-01-20.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "InvtMult": 1,
        "POType": "P",
        "PONbr": "PO-100",
        "POLineNbr": 1,
        "ReceiptType": "R",
        "ReceiptNbr": "RCPT-1",
        "LineNbr": 1,
        "VendorID": "V-9",
        "ReceiptDate": "2026-01-20T14:00:00Z",
    },
    # No matching POLine: skipped and counted.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "InvtMult": 1,
        "POType": "P",
        "PONbr": "PO-999",
        "POLineNbr": 1,
        "ReceiptType": "R",
        "ReceiptNbr": "RCPT-2",
        "LineNbr": 1,
        "VendorID": "V-9",
        "ReceiptDate": "2026-01-21T14:00:00Z",
    },
    # Received 2026-01-01, before its order date 2026-01-02: skipped and
    # counted.
    {
        "InventoryID": 2,
        "SiteID": 5,
        "InvtMult": 1,
        "POType": "P",
        "PONbr": "PO-102",
        "POLineNbr": 1,
        "ReceiptType": "R",
        "ReceiptNbr": "RCPT-3",
        "LineNbr": 1,
        "VendorID": "V-2",
        "ReceiptDate": "2026-01-01T08:00:00Z",
    },
    # A PO issue (-1) is dropped.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "InvtMult": -1,
        "POType": "P",
        "PONbr": "PO-100",
        "POLineNbr": 1,
        "ReceiptType": "R",
        "ReceiptNbr": "RCPT-4",
        "LineNbr": 1,
        "VendorID": "V-9",
        "ReceiptDate": "2026-01-22T14:00:00Z",
    },
    # An empty PONbr is dropped.
    {
        "InventoryID": 1,
        "SiteID": 4,
        "InvtMult": 1,
        "POType": "P",
        "PONbr": "",
        "POLineNbr": 1,
        "ReceiptType": "R",
        "ReceiptNbr": "RCPT-5",
        "LineNbr": 1,
        "VendorID": "V-9",
        "ReceiptDate": "2026-01-23T14:00:00Z",
    },
]

EXPECTED_LEAD_TIME_ROWS = [
    {
        "business_key": "R:RCPT-1:1",
        "supplier_code": "V-9",
        "item_code": "WIDGET-1",
        "site_code": "4",
        "ordered_on": date(2026, 1, 5),
        "received_on": date(2026, 1, 20),
        "promised_on": None,
        "source_system": "acumatica",
    },
]

SITE_STATUS_ROWS = [
    # Two sub-item rows for the same (InventoryID, SiteID): summed.
    {"InventoryID": 1, "SiteID": 4, "SubItemID": "", "QtyOnHand": 30.5},
    {"InventoryID": 1, "SiteID": 4, "SubItemID": "SUB-1", "QtyOnHand": 12.5},
    {"InventoryID": 2, "SiteID": 5, "SubItemID": "", "QtyOnHand": -4.0},
    # A non-stock item is skipped.
    {"InventoryID": 3, "SiteID": 4, "SubItemID": "", "QtyOnHand": 7.0},
]

EXPECTED_ON_HAND_ROWS = [
    {
        "item_code": "WIDGET-1",
        "site_code": "4",
        "quantity_on_hand": 43.0,
        "as_of": AS_OF_ISO,
        "source_system": "acumatica",
    },
    {
        "item_code": "WIDGET-2",
        "site_code": "5",
        "quantity_on_hand": -4.0,
        "as_of": AS_OF_ISO,
        "source_system": "acumatica",
    },
]

ENTITY_ROWS = {
    "InventoryItem": INVENTORY_ROWS,
    "INItemSite": ITEM_SITE_ROWS,
    "INSiteStatus": SITE_STATUS_ROWS,
    "INTran": TRAN_ROWS,
    "POLine": PO_LINE_ROWS,
    "POReceiptLine": RECEIPT_ROWS,
}


class _UpsertRecorder:
    """Stands in for a repository: records rows instead of writing them."""

    def __init__(self):
        self.calls = []

    def upsert_many(self, rows):
        self.calls.append(rows)
        return {"inserted": len(rows), "updated": 0}


def _make_feed_reader(entity_rows, fail_on=None):
    """A stand-in for aidw_client.iter_entities that records every read."""
    read_calls = []

    def iter_entities(entity_set, *args, **kwargs):
        read_calls.append(entity_set)
        if fail_on is not None and entity_set == fail_on:
            raise RuntimeError(f"feed read failed for {entity_set}")
        return iter(list(entity_rows[entity_set]))

    return iter_entities, read_calls


def test_issue25_freeform():
    # 1. map_item_sites: InventoryCD trimmed to WIDGET-1, site_code is the
    #    integer SiteID as text, unit_cost is the first positive of
    #    (AvgCost, LastCost, StdCost) else empty, order_qty is MaxQty -
    #    MinQty when positive else empty, master_lead_time_days empty, and
    #    the non-stock item is skipped.
    assert map_item_sites(ITEM_SITE_ROWS, INVENTORY_ROWS) == (EXPECTED_ITEM_SITE_ROWS)

    # 2. map_demand_events: BaseQty is used (not the Qty decoys), each
    #    allowed (DocType, TranType) pair maps to its event_type, a
    #    transfer, an adjustment and a stock-in line are dropped, a kit
    #    assembly keeps only its -1 lines, zero BaseQty is skipped, a
    #    non-stock item is skipped, and the padded InventoryCD is trimmed.
    assert map_demand_events(TRAN_ROWS, INVENTORY_ROWS) == (EXPECTED_DEMAND_ROWS)

    # 3. map_lead_time_observations: the receipt joins its POLine
    #    (ordered_on from POLine.OrderDate, received_on from ReceiptDate),
    #    supplier_code is VendorID as text, and a receipt with no matching
    #    order line or dated before its order is skipped.
    assert map_lead_time_observations(RECEIPT_ROWS, PO_LINE_ROWS, INVENTORY_ROWS) == (
        EXPECTED_LEAD_TIME_ROWS
    )

    # 4. map_on_hand: QtyOnHand is summed over SubItemID per
    #    (InventoryID, SiteID), the non-stock item is skipped, and the
    #    sync's as_of is stamped on every row.
    assert map_on_hand(SITE_STATUS_ROWS, INVENTORY_ROWS, AS_OF) == (
        EXPECTED_ON_HAND_ROWS
    )
    # A string as_of passes through verbatim.
    assert [
        row["as_of"] for row in map_on_hand(SITE_STATUS_ROWS, INVENTORY_ROWS, AS_OF_ISO)
    ] == [AS_OF_ISO, AS_OF_ISO]

    # A date as_of is stamped as that date: it is never replaced by the
    # current time.
    as_of_date = date(2026, 10, 1)
    assert [
        row["as_of"]
        for row in map_on_hand(SITE_STATUS_ROWS, INVENTORY_ROWS, as_of_date)
    ] == [as_of_date.isoformat(), as_of_date.isoformat()]

    # 5. Hostile rows never raise: non-dict rows and missing fields are
    #    skipped.
    assert map_demand_events([None, "junk", 5, {}], INVENTORY_ROWS) == []
    assert map_item_sites([{"InventoryID": 1, "SiteID": None}], INVENTORY_ROWS) == []
    assert (
        map_lead_time_observations(
            [{"InventoryID": 1, "InvtMult": 1, "PONbr": "PO-1", "POLineNbr": 1}],
            [None, {"OrderType": "P", "OrderNbr": "PO-1", "LineNbr": 1}],
            INVENTORY_ROWS,
        )
        == []
    )
    assert (
        map_on_hand(
            [None, {"InventoryID": 1, "SiteID": "not-a-site"}],
            INVENTORY_ROWS,
            AS_OF,
        )
        == []
    )

    # 6. run_sync reads all six entity sets (exhaustion is completion, no
    #    count validation) and, only after every read succeeds, writes each
    #    mapped row list through the matching upsert_many with as_of = the
    #    sync's start time.
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
    assert item_sites_recorder.calls == [EXPECTED_ITEM_SITE_ROWS]
    assert demand_events_recorder.calls == [EXPECTED_DEMAND_ROWS]
    assert lead_time_recorder.calls == [EXPECTED_LEAD_TIME_ROWS]
    assert on_hand_recorder.calls == [EXPECTED_ON_HAND_ROWS]
    # The POLine fixture rows carry no InventoryID, so the new
    # open-purchase-lines mapper maps none of them.
    assert open_po_recorder.calls == [[]]
    assert result == {
        "item_sites": {"inserted": 3, "updated": 0},
        "demand_events": {"inserted": 5, "updated": 0},
        "lead_time_observations": {"inserted": 1, "updated": 0},
        "on_hand": {"inserted": 2, "updated": 0},
        "open_purchase_lines": {"inserted": 0, "updated": 0},
        "inventory_items": {"inserted": 2, "updated": 0},
        "skipped": {
            "non_stock_item": 4,
            "not_demand": 4,
            "zero_quantity": 1,
            "missing_order_line": 1,
            "receipt_before_order": 1,
            "not_receipt": 2,
            "open_purchase_lines": {"unknown_item": 3},
        },
    }

    # A date as_of reaches the on_hand rows through run_sync as that
    # date, not the current time.
    on_hand_date_recorder = _UpsertRecorder()
    run_sync(
        iter_entities=fake_iter_entities,
        as_of=as_of_date,
        item_sites_repo=_UpsertRecorder(),
        demand_events_repo=_UpsertRecorder(),
        lead_time_observations_repo=_UpsertRecorder(),
        on_hand_repo=on_hand_date_recorder,
        open_purchase_lines_repo=_UpsertRecorder(),
    )
    assert [row["as_of"] for row in on_hand_date_recorder.calls[0]] == [
        as_of_date.isoformat(),
        as_of_date.isoformat(),
    ]

    # 7. When one read raises, the exception propagates and no upsert_many
    #    is called at all.
    broken_iter_entities, broken_reads = _make_feed_reader(
        ENTITY_ROWS, fail_on="POLine"
    )
    failed_repos = [_UpsertRecorder() for _ in range(4)]
    with pytest.raises(RuntimeError, match="feed read failed"):
        run_sync(
            iter_entities=broken_iter_entities,
            as_of=AS_OF,
            item_sites_repo=failed_repos[0],
            demand_events_repo=failed_repos[1],
            lead_time_observations_repo=failed_repos[2],
            on_hand_repo=failed_repos[3],
        )
    assert broken_reads == [
        "InventoryItem",
        "INItemSite",
        "INSiteStatus",
        "INTran",
        "POLine",
    ]
    for repo in failed_repos:
        assert repo.calls == []
