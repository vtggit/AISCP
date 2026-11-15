"""Issue #751 proof: the open sales-order lines read model.

Against the pure mapper, the AIDW feed reader and the migrated test
database:

* the migration: ``0028_open_sales_lines`` is the only alembic head, it
  is applied, and it creates ``open_sales_lines`` with the stated
  columns (the purchase-line shapes with supplier_code, promised_on and
  ordered_on replaced by a single ship_on) and a unique
  ``business_key``;
* the mapper (pure, on fixture rows): an open "SO" line maps with its
  ``BaseOpenQty`` and the date part of its ``ShipDate``, a completed
  line maps with ``open_units`` 0, a "BL" line, a "QT" line, an
  ``Operation`` "R" line and a non-stock item are skipped and counted,
  a line with no ``ShipDate`` uses ``RequestDate``, and a hostile
  non-dict row is counted, not raised;
* the sync, feed mode (``aidw_client.iter_entities`` replaced the way
  test_issue617 replaces it): the read order ends with "BAccount" then
  "SOLine", the rows are stored and reported under
  ``"open_sales_lines"``, and a reader that raises ``AidwFeedError``
  for ``SOLine`` gives ``skipped["open_sales_lines"] == "SOLine not
  available"`` and still writes the other read models;
* the repository: a second sync that sees the line completed updates it
  to ``open_units`` 0, and ``list_latest`` returns only the newest
  sync's rows;
* the downgrade drops the table and the upgrade back re-creates it.

Every date is a fixed calendar date, never today's date.
"""

import os
from datetime import UTC, date, datetime

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.db.connection import get_connection
from app.feed import aidw_client
from app.feed.aidw_client import AidwFeedError
from app.repositories.open_sales_lines_postgres_repository import (
    OpenSalesLinePostgresRepository,
)
from app.sync.acumatica import (
    _map_open_sales_lines,
    map_open_sales_lines,
    run_sync,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AS_OF_ONE = datetime(2026, 10, 28, 12, 0, 0, tzinfo=UTC)
AS_OF_TWO = datetime(2026, 10, 28, 18, 0, 0, tzinfo=UTC)
AS_OF_ONE_ISO = "2026-10-28T12:00:00+00:00"
AS_OF_TWO_ISO = "2026-10-28T18:00:00+00:00"

_SIX_ENTITY_SETS = (
    "InventoryItem",
    "INItemSite",
    "INSiteStatus",
    "INTran",
    "POLine",
    "POReceiptLine",
)

# WIDGET-1 is the stock item the mapped lines reference; NONSTOCK-1 is
# a non-stock item and must be skipped.
INVENTORY_ROWS = [
    {"InventoryID": 1, "InventoryCD": "WIDGET-1", "StkItem": True},
    {"InventoryID": 2, "InventoryCD": "NONSTOCK-1", "StkItem": False},
]

SOLINE_ROWS = [
    # An open SO line for a stock item: open_units is BaseOpenQty and
    # ship_on is the date part of ShipDate.
    {
        "OrderType": "SO",
        "OrderNbr": "SO-100",
        "LineNbr": 1,
        "InventoryID": 1,
        "SiteID": 4,
        "Operation": "I",
        "BaseOpenQty": 25,
        "Completed": False,
        "Cancelled": False,
        "ShipDate": "2026-11-05T00:00:00Z",
        "RequestDate": "2026-10-28T00:00:00Z",
    },
    # Completed (BaseOpenQty above 0): the line is kept with open_units
    # 0, because the sync is upsert-only — a closing line is zeroed,
    # not dropped.
    {
        "OrderType": "SO",
        "OrderNbr": "SO-100",
        "LineNbr": 2,
        "InventoryID": 1,
        "SiteID": 4,
        "Operation": "I",
        "BaseOpenQty": 40,
        "Completed": True,
        "Cancelled": False,
        "ShipDate": "2026-11-06T00:00:00Z",
    },
    # A blanket line: skipped and counted.
    {
        "OrderType": "BL",
        "OrderNbr": "BL-1",
        "LineNbr": 1,
        "InventoryID": 1,
        "SiteID": 4,
        "Operation": "I",
        "BaseOpenQty": 9,
        "Completed": False,
        "Cancelled": False,
    },
    # A quote line: skipped and counted.
    {
        "OrderType": "QT",
        "OrderNbr": "QT-1",
        "LineNbr": 1,
        "InventoryID": 1,
        "SiteID": 4,
        "Operation": "I",
        "BaseOpenQty": 11,
        "Completed": False,
        "Cancelled": False,
    },
    # A receipt/return (Operation other than "I"): skipped and counted.
    {
        "OrderType": "SO",
        "OrderNbr": "SO-101",
        "LineNbr": 9,
        "InventoryID": 1,
        "SiteID": 4,
        "Operation": "R",
        "BaseOpenQty": 13,
        "Completed": False,
        "Cancelled": False,
    },
    # A line for a non-stock item: skipped and counted.
    {
        "OrderType": "SO",
        "OrderNbr": "SO-101",
        "LineNbr": 1,
        "InventoryID": 2,
        "SiteID": 4,
        "Operation": "I",
        "BaseOpenQty": 17,
        "Completed": False,
        "Cancelled": False,
    },
    # No ShipDate: ship_on falls back to the date part of RequestDate.
    {
        "OrderType": "SO",
        "OrderNbr": "SO-102",
        "LineNbr": 1,
        "InventoryID": 1,
        "SiteID": 4,
        "Operation": "I",
        "BaseOpenQty": 7,
        "Completed": False,
        "Cancelled": False,
        "RequestDate": "2026-10-27T00:00:00Z",
    },
    # A hostile non-dict row: counted, not raised.
    "junk",
]

EXPECTED_ROWS = [
    {
        "business_key": "SO:SO-100:1",
        "item_code": "WIDGET-1",
        "site_code": "4",
        "order_type": "SO",
        "open_units": 25.0,
        "ship_on": date(2026, 11, 5),
        "as_of": AS_OF_ONE_ISO,
        "source_system": "acumatica",
    },
    {
        "business_key": "SO:SO-100:2",
        "item_code": "WIDGET-1",
        "site_code": "4",
        "order_type": "SO",
        "open_units": 0.0,
        "ship_on": date(2026, 11, 6),
        "as_of": AS_OF_ONE_ISO,
        "source_system": "acumatica",
    },
    {
        "business_key": "SO:SO-102:1",
        "item_code": "WIDGET-1",
        "site_code": "4",
        "order_type": "SO",
        "open_units": 7.0,
        "ship_on": date(2026, 10, 27),
        "as_of": AS_OF_ONE_ISO,
        "source_system": "acumatica",
    },
]

EXPECTED_SKIPS = {
    "malformed_row": 1,
    "non_stock_item": 1,
    "blanket": 1,
    "quote": 1,
    "receipt_or_return": 1,
}


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND_ROOT, "migrations"))
    return cfg


def _alembic_revision() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _table_exists(name: str) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = %s",
                (name,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _table_columns() -> dict:
    """The table's columns as (data_type, max_length, nullability, default)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'open_sales_lines' "
                "ORDER BY ordinal_position"
            )
            return {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.fetchall()}
    finally:
        conn.close()


def _unique_column_sets() -> list[list[str]]:
    """Every explicit UNIQUE constraint's column set (the primary key is
    contype "p" in pg_constraint and is not listed here)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT con.conname, a.attname "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_attribute a ON a.attrelid = con.conrelid "
                "AND a.attnum = ANY(con.conkey) "
                "WHERE rel.relname = 'open_sales_lines' AND con.contype = 'u' "
                "ORDER BY con.conname, a.attnum"
            )
            by_name: dict[str, list[str]] = {}
            for name, att in cur.fetchall():
                by_name.setdefault(name, []).append(att)
            return [by_name[name] for name in sorted(by_name)]
    finally:
        conn.close()


def _feed_reader(inventory_rows, sol_rows, fail_on=None):
    """A fake iter_entities installed as the configured AIDW feed reader.

    Serves the given rows for InventoryItem and SOLine and empty lists
    for every other entity set (including BAccount).  Records the read
    order so the test can prove SOLine is read right after BAccount.
    When ``fail_on`` is set, that entity set raises ``AidwFeedError``
    instead of yielding rows.
    """
    read_order = []

    def iter_entities(entity_set, *args, **kwargs):
        read_order.append(entity_set)
        if fail_on is not None and entity_set == fail_on:
            raise AidwFeedError(f"entity set '{entity_set}' returned HTTP 404")
        if entity_set == "InventoryItem":
            return iter(list(inventory_rows))
        if entity_set == "SOLine":
            return iter(list(sol_rows))
        return iter(())

    return iter_entities, read_order


class _UpsertRecorder:
    """Stands in for a repository: records rows instead of writing them."""

    def __init__(self):
        self.calls = []

    def upsert_many(self, rows):
        self.calls.append(rows)
        return {"inserted": len(rows), "updated": 0}


def test_issue751_freeform(client, monkeypatch):
    repo = OpenSalesLinePostgresRepository()
    cfg = _alembic_config()

    # 1. The migration: 0028_open_sales_lines is the only head, it is
    #    applied, and the table has the stated columns and a unique
    #    business_key.
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert heads[0] == CURRENT_HEAD
    assert _alembic_revision() == CURRENT_HEAD
    columns = _table_columns()
    assert set(columns) == {
        "id",
        "business_key",
        "item_code",
        "site_code",
        "order_type",
        "open_units",
        "ship_on",
        "as_of",
        "source_system",
        "synced_at",
    }
    assert columns["id"] == ("character varying", 64, "NO", None)
    assert columns["business_key"] == ("character varying", 255, "NO", None)
    assert columns["item_code"] == ("character varying", 64, "NO", None)
    assert columns["site_code"] == ("character varying", 64, "NO", None)
    assert columns["order_type"] == ("character varying", 16, "NO", None)
    assert columns["open_units"] == ("double precision", None, "NO", None)
    assert columns["ship_on"] == ("date", None, "YES", None)
    assert columns["as_of"] == ("timestamp with time zone", None, "NO", None)
    assert columns["source_system"] == ("character varying", 64, "YES", None)
    assert columns["synced_at"][0] == "timestamp with time zone"
    assert columns["synced_at"][2] == "NO"
    assert columns["synced_at"][3] is not None
    # business_key carries the table's unique constraint.
    assert _unique_column_sets() == [["business_key"]]

    # 2. The mapper (pure, on fixture rows): the open "SO" line maps
    #    with its BaseOpenQty and the date part of ShipDate, the
    #    completed line maps with open_units 0, the "BL" line, the "QT"
    #    line, the Operation "R" line and the non-stock item are skipped
    #    and counted, the line with no ShipDate uses RequestDate, and
    #    the hostile non-dict row is counted, not raised.
    rows, skips = _map_open_sales_lines(SOLINE_ROWS, INVENTORY_ROWS, AS_OF_ONE)
    assert rows == EXPECTED_ROWS
    assert skips == EXPECTED_SKIPS
    assert map_open_sales_lines(SOLINE_ROWS, INVENTORY_ROWS, AS_OF_ONE) == (
        EXPECTED_ROWS
    )
    open_row = rows[0]
    assert open_row["open_units"] == 25.0
    assert open_row["ship_on"] == date(2026, 11, 5)
    assert rows[1]["open_units"] == 0.0
    assert rows[2]["ship_on"] == date(2026, 10, 27)
    assert {row["business_key"] for row in rows} == {
        "SO:SO-100:1",
        "SO:SO-100:2",
        "SO:SO-102:1",
    }

    # 3. The sync, feed mode: the configured AIDW feed reader is
    #    replaced the way test_issue617 replaces it, the read order
    #    ends with "BAccount" then "SOLine", the rows are stored and
    #    reported under "open_sales_lines".
    reader, read_order = _feed_reader(INVENTORY_ROWS, SOLINE_ROWS)
    monkeypatch.setattr(aidw_client, "iter_entities", reader)
    result = run_sync(as_of=AS_OF_ONE)
    assert read_order == [*_SIX_ENTITY_SETS, "BAccount", "SOLine", "POVendorInventory"]
    assert result["open_sales_lines"] == {"inserted": 3, "updated": 0}
    assert result["skipped"]["blanket"] == 1
    assert result["skipped"]["quote"] == 1
    assert result["skipped"]["receipt_or_return"] == 1
    assert result["skipped"]["malformed_row"] == 1
    # non_stock_item is counted twice: once by the inventory-items
    # mapper (the NONSTOCK-1 master row) and once by the open-sales-
    # lines mapper (the line that references it).
    assert result["skipped"]["non_stock_item"] == 2
    stored = {row["business_key"]: row for row in repo.list_latest()}
    assert set(stored) == {"SO:SO-100:1", "SO:SO-100:2", "SO:SO-102:1"}
    assert stored["SO:SO-100:1"]["open_units"] == 25.0
    assert stored["SO:SO-100:1"]["ship_on"] == date(2026, 11, 5)
    assert stored["SO:SO-100:1"]["as_of"] == AS_OF_ONE_ISO
    assert stored["SO:SO-100:1"]["source_system"] == "acumatica"
    assert stored["SO:SO-100:2"]["open_units"] == 0.0

    # 4. The repository: a second sync that sees the line completed
    #    updates it to open_units 0, and list_latest returns only the
    #    newest sync's rows.
    completed_row = dict(SOLINE_ROWS[0], Completed=True)
    second_reader, second_read_order = _feed_reader(INVENTORY_ROWS, [completed_row])
    monkeypatch.setattr(aidw_client, "iter_entities", second_reader)
    second_result = run_sync(as_of=AS_OF_TWO)
    assert second_read_order == [
        *_SIX_ENTITY_SETS,
        "BAccount",
        "SOLine",
        "POVendorInventory",
    ]
    assert second_result["open_sales_lines"] == {"inserted": 0, "updated": 1}
    latest = repo.list_latest()
    assert len(latest) == 1
    (latest_row,) = latest
    assert latest_row["business_key"] == "SO:SO-100:1"
    assert latest_row["open_units"] == 0.0
    assert latest_row["as_of"] == AS_OF_TWO_ISO

    # 5. Feed without SOLine: a reader that raises AidwFeedError for
    #    SOLine gives skipped["open_sales_lines"] == "SOLine not
    #    available" and still writes the other read models.
    item_sites_recorder = _UpsertRecorder()
    demand_events_recorder = _UpsertRecorder()
    lead_time_recorder = _UpsertRecorder()
    on_hand_recorder = _UpsertRecorder()
    open_po_recorder = _UpsertRecorder()
    missing_read_order = []

    def missing_iter_entities(entity_set, *args, **kwargs):
        missing_read_order.append(entity_set)
        if entity_set == "SOLine":
            raise AidwFeedError("entity set 'SOLine' returned HTTP 404")
        if entity_set == "InventoryItem":
            return iter([{"InventoryID": 1, "InventoryCD": "W-751", "StkItem": True}])
        if entity_set == "INItemSite":
            return iter([{"InventoryID": 1, "SiteID": 9}])
        return iter(())

    monkeypatch.setattr(aidw_client, "iter_entities", missing_iter_entities)
    missing_result = run_sync(
        as_of=AS_OF_TWO,
        item_sites_repo=item_sites_recorder,
        demand_events_repo=demand_events_recorder,
        lead_time_observations_repo=lead_time_recorder,
        on_hand_repo=on_hand_recorder,
        open_purchase_lines_repo=open_po_recorder,
    )
    assert missing_read_order == [
        *_SIX_ENTITY_SETS,
        "BAccount",
        "SOLine",
        "POVendorInventory",
    ]
    assert missing_result["skipped"]["open_sales_lines"] == ("SOLine not available")
    assert "open_sales_lines" not in missing_result
    # The other read models are still written.
    assert missing_result["item_sites"] == {"inserted": 1, "updated": 0}
    assert item_sites_recorder.calls == [
        [
            {
                "item_code": "W-751",
                "site_code": "9",
                "unit_cost": None,
                "reorder_point": None,
                "safety_stock": None,
                "max_qty": None,
                "order_qty": None,
                "master_lead_time_days": None,
                "source_system": "acumatica",
            }
        ]
    ]
    for recorder in (
        demand_events_recorder,
        lead_time_recorder,
        on_hand_recorder,
        open_po_recorder,
    ):
        assert recorder.calls == [[]]
    # No open sales lines were written by the missing-set sync: the
    # stored rows are still the newest sync's rows from section 4.
    assert [row["business_key"] for row in repo.list_latest()] == ["SO:SO-100:1"]

    # 6. The downgrade drops the table and the upgrade back re-creates
    #    it cleanly.
    alembic_command.downgrade(cfg, "0027_ddmrp_buffers")
    assert _alembic_revision() == "0027_ddmrp_buffers"
    assert not _table_exists("open_sales_lines")
    alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_exists("open_sales_lines")
