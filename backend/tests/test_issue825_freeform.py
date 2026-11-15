"""Issue #825 proof: the supplier terms read model.

Against the pure mapper, the AIDW feed reader and the migrated test
database:

* the migration: 0033_supplier_terms is the only alembic head, it is
  applied, and it creates ``supplier_terms`` with the stated columns
  and a unique (source_system, business_key) constraint;
* the mapper (pure, on fixture rows): an active POVendorInventory row
  with MinOrdQty/MaxOrdQty/LotSize 25, VLeadTime 20 and
  AddLeadTimeDays 2 maps to min, max and lot 25, lead time 22 and
  default true; an active row with all quantities 0 and VLeadTime 10
  maps to null min, max and lot and lead time 10; the inactive row is
  skipped and counted, and a row with an unknown InventoryID is
  skipped and counted;
* the sync, feed mode (``aidw_client.iter_entities`` replaced the way
  test_issue751 replaces it): the read order ends with "BAccount",
  "SOLine" then "POVendorInventory", the rows are stored and reported
  under ``"supplier_terms"`` and a second sync updates rather than
  inserts;
* the optional read: a reader that raises ``AidwFeedError`` for
  ``POVendorInventory`` gives ``skipped["supplier_terms"] ==
  "POVendorInventory not available"``, leaves the other read models
  synced and puts no ``supplier_terms`` key in the result;
* the route: ``GET /api/supplier-terms?item_code=...`` answers the
  stored rows with their fields, ``GET /api/supplier-terms`` answers
  401 without a token, and a hostile filter never gets a 500;
* the identity: the route answers the same rows for a NON-ADMIN
  (role "user") booted the production way — a fresh TestClient with
  the token the page would hold from its #access_token=... fragment,
  GET /api/auth/config, then GET /api/auth/me;
* the downgrade drops the table and the upgrade back re-creates it.
"""

import os
from datetime import UTC, datetime

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.feed import aidw_client
from app.feed.aidw_client import AidwFeedError
from app.main import app
from app.repositories.supplier_terms_postgres_repository import (
    SupplierTermsPostgresRepository,
)
from app.sync.acumatica import (
    _map_supplier_terms,
    map_supplier_terms,
    run_sync,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AS_OF_ONE = datetime(2026, 10, 31, 12, 0, 0, tzinfo=UTC)
AS_OF_TWO = datetime(2026, 10, 31, 18, 0, 0, tzinfo=UTC)

_SIX_ENTITY_SETS = (
    "InventoryItem",
    "INItemSite",
    "INSiteStatus",
    "INTran",
    "POLine",
    "POReceiptLine",
)

# W-825 is the stock item the vendor-inventory rows reference; the
# unknown row references InventoryID 999, which is not in the index.
INVENTORY_ROWS = [
    {"InventoryID": 1, "InventoryCD": "W-825", "StkItem": True},
]

POVENDOR_ROWS = [
    # (a) active with quantities and a split lead time.
    {
        "RecordID": "R-1",
        "Active": True,
        "InventoryID": 1,
        "VendorID": 7001,
        "MinOrdQty": 25,
        "MaxOrdQty": 25,
        "LotSize": 25,
        "VLeadTime": 20,
        "AddLeadTimeDays": 2,
        "IsDefault": True,
    },
    # (b) active, all quantities 0 (stored as null) and no
    # AddLeadTimeDays.
    {
        "RecordID": "R-2",
        "Active": True,
        "InventoryID": 1,
        "VendorID": 7002,
        "MinOrdQty": 0,
        "MaxOrdQty": 0,
        "LotSize": 0,
        "VLeadTime": 10,
        "IsDefault": False,
    },
    # (c) inactive: not stored.
    {
        "RecordID": "R-3",
        "Active": False,
        "InventoryID": 1,
        "VendorID": 7003,
        "MinOrdQty": 5,
        "MaxOrdQty": 5,
        "LotSize": 5,
        "VLeadTime": 3,
        "IsDefault": True,
    },
]

# A fourth row whose InventoryID is not in the inventory index: the
# mapper skips it and counts it.
UNKNOWN_ROWS = [
    {
        "RecordID": "R-4",
        "Active": True,
        "InventoryID": 999,
        "VendorID": 7004,
        "MinOrdQty": 7,
        "MaxOrdQty": 7,
        "LotSize": 7,
        "VLeadTime": 4,
        "IsDefault": False,
    },
]

EXPECTED_ROWS = [
    {
        "business_key": "POVendorInventory:R-1",
        "item_code": "W-825",
        "supplier_code": "7001",
        "min_order_qty": 25.0,
        "max_order_qty": 25.0,
        "lot_size": 25.0,
        "lead_time_days": 22.0,
        "is_default": True,
        "source_system": "acumatica",
    },
    {
        "business_key": "POVendorInventory:R-2",
        "item_code": "W-825",
        "supplier_code": "7002",
        "min_order_qty": None,
        "max_order_qty": None,
        "lot_size": None,
        "lead_time_days": 10.0,
        "is_default": False,
        "source_system": "acumatica",
    },
]

EXPECTED_SKIPS = {
    "inactive": 1,
    "unknown_item": 1,
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
                "WHERE table_schema = 'public' AND table_name = 'supplier_terms' "
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
                "WHERE rel.relname = 'supplier_terms' AND con.contype = 'u' "
                "ORDER BY con.conname, a.attnum"
            )
            by_name: dict[str, list[str]] = {}
            for name, att in cur.fetchall():
                by_name.setdefault(name, []).append(att)
            return [by_name[name] for name in sorted(by_name)]
    finally:
        conn.close()


def _feed_reader(inventory_rows, vendor_inventory_rows, fail_on=None):
    """A fake iter_entities installed as the configured AIDW feed reader.

    Serves the given rows for InventoryItem and POVendorInventory and
    empty lists for every other entity set (including BAccount and
    SOLine).  Records the read order so the test can prove
    POVendorInventory is read right after SOLine.  When ``fail_on`` is
    set, that entity set raises ``AidwFeedError`` instead of yielding
    rows.
    """
    read_order = []

    def iter_entities(entity_set, *args, **kwargs):
        read_order.append(entity_set)
        if fail_on is not None and entity_set == fail_on:
            raise AidwFeedError(f"entity set '{entity_set}' returned HTTP 404")
        if entity_set == "InventoryItem":
            return iter(list(inventory_rows))
        if entity_set == "POVendorInventory":
            return iter(list(vendor_inventory_rows))
        return iter(())

    return iter_entities, read_order


class _UpsertRecorder:
    """Stands in for a repository: records rows instead of writing them."""

    def __init__(self):
        self.calls = []

    def upsert_many(self, rows):
        self.calls.append(rows)
        return {"inserted": len(rows), "updated": 0}


def test_issue825_freeform(client, monkeypatch, user_token):
    repo = SupplierTermsPostgresRepository()
    cfg = _alembic_config()

    # 1. The migration: 0033_supplier_terms is the only head, it is
    #    applied, and the table has the stated columns and the unique
    #    (source_system, business_key) constraint.
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert heads[0] == CURRENT_HEAD
    assert _alembic_revision() == CURRENT_HEAD
    columns = _table_columns()
    assert set(columns) == {
        "id",
        "business_key",
        "source_system",
        "item_code",
        "supplier_code",
        "min_order_qty",
        "max_order_qty",
        "lot_size",
        "lead_time_days",
        "is_default",
        "synced_at",
    }
    assert columns["id"] == ("character varying", 64, "NO", None)
    assert columns["business_key"] == ("character varying", 255, "NO", None)
    assert columns["source_system"] == ("character varying", 32, "NO", None)
    assert columns["item_code"] == ("character varying", 64, "NO", None)
    assert columns["supplier_code"] == ("character varying", 64, "NO", None)
    for column in ("min_order_qty", "max_order_qty", "lot_size", "lead_time_days"):
        assert columns[column] == ("numeric", None, "YES", None), column
    assert columns["is_default"] == ("boolean", None, "NO", "false")
    assert columns["synced_at"][0] == "timestamp with time zone"
    assert columns["synced_at"][2] == "NO"
    assert columns["synced_at"][3] is not None
    # (source_system, business_key) carries the table's unique constraint.
    assert [set(cols) for cols in _unique_column_sets()] == [
        {"source_system", "business_key"}
    ]

    # 2. The mapper (pure, on fixture rows): (a) maps with its
    #    quantities and its split lead time, (b) maps its 0 quantities
    #    as null and its VLeadTime as the lead time, (c) is skipped and
    #    counted, and the unknown item is skipped and counted.
    rows, skips = _map_supplier_terms(POVENDOR_ROWS + UNKNOWN_ROWS, INVENTORY_ROWS)
    assert rows == EXPECTED_ROWS
    assert skips == EXPECTED_SKIPS
    assert (
        map_supplier_terms(POVENDOR_ROWS + UNKNOWN_ROWS, INVENTORY_ROWS)
        == EXPECTED_ROWS
    )
    assert rows[0]["business_key"] == "POVendorInventory:R-1"
    assert rows[0]["lead_time_days"] == 22.0
    assert rows[1]["min_order_qty"] is None
    assert rows[1]["max_order_qty"] is None
    assert rows[1]["lot_size"] is None
    assert rows[1]["lead_time_days"] == 10.0

    # 3. The sync, feed mode: the configured AIDW feed reader is
    #    replaced the way test_issue751 replaces it, the read order
    #    ends with "BAccount", "SOLine" then "POVendorInventory", the
    #    rows are stored and reported under "supplier_terms".
    reader, read_order = _feed_reader(INVENTORY_ROWS, POVENDOR_ROWS)
    monkeypatch.setattr(aidw_client, "iter_entities", reader)
    result = run_sync(as_of=AS_OF_ONE)
    assert read_order == [
        *_SIX_ENTITY_SETS,
        "BAccount",
        "SOLine",
        "POVendorInventory",
    ]
    assert result["supplier_terms"] == {"inserted": 2, "updated": 0}
    assert result["skipped"]["inactive"] == 1
    assert "unknown_item" not in result["skipped"]
    stored = {row["business_key"]: row for row in repo.list()}
    assert set(stored) == {"POVendorInventory:R-1", "POVendorInventory:R-2"}
    stored_a = stored["POVendorInventory:R-1"]
    assert stored_a["item_code"] == "W-825"
    assert stored_a["supplier_code"] == "7001"
    assert stored_a["min_order_qty"] == 25
    assert stored_a["max_order_qty"] == 25
    assert stored_a["lot_size"] == 25
    assert stored_a["lead_time_days"] == 22
    assert stored_a["is_default"] is True
    assert stored_a["source_system"] == "acumatica"
    stored_b = stored["POVendorInventory:R-2"]
    assert stored_b["supplier_code"] == "7002"
    assert stored_b["min_order_qty"] is None
    assert stored_b["max_order_qty"] is None
    assert stored_b["lot_size"] is None
    assert stored_b["lead_time_days"] == 10
    assert stored_b["is_default"] is False
    # The inactive row was not stored, and the ordering is item_code,
    # is_default descending, then supplier_code.
    assert [row["business_key"] for row in repo.list()] == [
        "POVendorInventory:R-1",
        "POVendorInventory:R-2",
    ]

    # 4. Idempotence: a second sync of the same rows updates rather
    #    than inserts, and the table still holds exactly the two rows.
    second_reader, second_read_order = _feed_reader(INVENTORY_ROWS, POVENDOR_ROWS)
    monkeypatch.setattr(aidw_client, "iter_entities", second_reader)
    second_result = run_sync(as_of=AS_OF_TWO)
    assert second_read_order == [
        *_SIX_ENTITY_SETS,
        "BAccount",
        "SOLine",
        "POVendorInventory",
    ]
    assert second_result["supplier_terms"] == {"inserted": 0, "updated": 2}
    assert len(repo.list()) == 2

    # 5. The optional read: a reader that raises AidwFeedError for
    #    POVendorInventory leaves the other read models synced and puts
    #    no "supplier_terms" key in the result.
    item_sites_recorder = _UpsertRecorder()
    demand_events_recorder = _UpsertRecorder()
    lead_time_recorder = _UpsertRecorder()
    on_hand_recorder = _UpsertRecorder()
    open_po_recorder = _UpsertRecorder()
    missing_read_order = []

    def missing_iter_entities(entity_set, *args, **kwargs):
        missing_read_order.append(entity_set)
        if entity_set == "POVendorInventory":
            raise AidwFeedError("entity set 'POVendorInventory' returned HTTP 404")
        if entity_set == "InventoryItem":
            return iter([{"InventoryID": 1, "InventoryCD": "W-825", "StkItem": True}])
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
    assert missing_result["skipped"]["supplier_terms"] == (
        "POVendorInventory not available"
    )
    assert "supplier_terms" not in missing_result
    # The other read models are still written.
    assert missing_result["item_sites"] == {"inserted": 1, "updated": 0}
    assert item_sites_recorder.calls == [
        [
            {
                "item_code": "W-825",
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
    # No supplier terms were written by the missing-set sync: the
    # stored rows are still the two rows from section 3.
    assert [row["business_key"] for row in repo.list()] == [
        "POVendorInventory:R-1",
        "POVendorInventory:R-2",
    ]

    # 6. The route: 401 without a token, 200 with the signed-in user's
    #    token, answering the stored rows with their fields.
    with TestClient(app) as unauthenticated:
        assert unauthenticated.get("/api/supplier-terms").status_code == 401

    response = client.get("/api/supplier-terms", params={"item_code": "W-825"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 2
    first, second = body
    assert first["business_key"] == "POVendorInventory:R-1"
    assert first["item_code"] == "W-825"
    assert first["supplier_code"] == "7001"
    assert first["min_order_qty"] == 25
    assert first["max_order_qty"] == 25
    assert first["lot_size"] == 25
    assert first["lead_time_days"] == 22
    assert first["is_default"] is True
    assert first["source_system"] == "acumatica"
    assert second["business_key"] == "POVendorInventory:R-2"
    assert second["min_order_qty"] is None
    assert second["max_order_qty"] is None
    assert second["lot_size"] is None
    assert second["lead_time_days"] == 10
    assert second["is_default"] is False

    # The supplier_code filter answers the one matching row.
    filtered = client.get("/api/supplier-terms", params={"supplier_code": "7001"})
    assert filtered.status_code == 200
    assert [row["business_key"] for row in filtered.json()] == ["POVendorInventory:R-1"]

    # Hostile filters never get a 500: an overlong filter is rejected,
    # a NUL byte is rejected and a control-character filter answers
    # an empty list.
    assert (
        client.get("/api/supplier-terms", params={"item_code": "x" * 65}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/supplier-terms",
            params={"supplier_code": "700" + chr(0) + "1"},
        ).status_code
        == 422
    )
    control = client.get(
        "/api/supplier-terms", params={"item_code": "W" + chr(1) + "-825"}
    )
    assert control.status_code == 200
    assert control.json() == []

    # --- Identity: the non-admin pass, after a full reload -------------
    # The route answers any signed-in user.  A fresh client (full
    # reload) carries the development token the page would hold from
    # its #access_token=... fragment: sub dev-user-1, role "user" —
    # a non-admin.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})

    config = viewer.get("/api/auth/config")
    assert config.status_code == 200
    assert config.json()["authEnabled"] is True

    me = viewer.get("/api/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["authenticated"] is True
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]

    # The page still renders for the non-admin: its data call answers
    # the same stored rows as the signed-in client above.
    page = viewer.get("/api/supplier-terms", params={"item_code": "W-825"})
    assert page.status_code == 200
    assert [row["business_key"] for row in page.json()] == [
        "POVendorInventory:R-1",
        "POVendorInventory:R-2",
    ]

    # 7. The downgrade drops the table and the upgrade back re-creates
    #    it cleanly.
    alembic_command.downgrade(cfg, "0032_exception_first_seen")
    assert _alembic_revision() == "0032_exception_first_seen"
    assert not _table_exists("supplier_terms")
    alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_exists("supplier_terms")
