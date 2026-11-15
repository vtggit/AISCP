"""Issue #627 proof: item descriptions on the synced item master.

Loads the captured InventoryItem feed sample from
``tests/fixtures/aidw_inventoryitem_sample.json`` (never restating it)
and proves, against it:

* the mapping: ``_map_inventory_items`` on the sample's ``value`` rows
  keeps CONPAPERST (description "Letter Paper Ream Standard", uom
  "EA") and AAPOWERAID, and counts the non-stock ``"<N/A>"`` row as
  ``non_stock_item`` 1;
* the sync: with a fake ``iter_entities`` serving the fixture for
  ``InventoryItem`` and empty lists for the other sets, ``run_sync``
  stores the two items with no new read, and a second run with a
  changed description updates the row without a duplicate;
* expedite: the line of CONPAPERST carries ``item_description``
  "Letter Paper Ream Standard" and the line of an item missing from
  the read model carries null, looked up by ``item_code`` through one
  ``InventoryItemPostgresRepository`` read;
* release tracking: the order line of AAPOWERAID carries its
  description, the same way;
* the empty read model: with an empty ``inventory_items`` read model
  the releases response still carries ``item_description`` on the
  order line — null for the unknown item — never dropping the field
  from the body;
* the routes: ``GET /api/expedite`` and ``GET /api/aggregate/releases``
  serve the new field through the response models;
* the migration: 0025_empirical_adoptions is the head,
  upgrades and downgrades cleanly, and the index is unique at head and
  non-unique at 0023.
"""

import copy
import json
import os
from datetime import UTC, date, datetime

import httpx
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

import app.aggregate.tracking as tracking
import app.feed.acumatica_client as acumatica_client
import app.recommend.expedite as expedite_module
from app.db.connection import get_connection
from app.recommend.expedite import expedite_list
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
from app.sync.acumatica import _map_inventory_items, run_sync
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "aidw_inventoryitem_sample.json"
)

SYNC_AS_OF = datetime(2026, 10, 25, 0, 0, 0, tzinfo=UTC)
TODAY = date(2026, 10, 23)

_URL_EXPEDITE = "/api/expedite"
_URL_RELEASES = "/api/aggregate/releases"

_BASE_URL = "https://acumatica.example.com/Instance"
_TENANT = "AIERP"
_USERNAME = "svc-aierp"
_PASSWORD = "s3cret-password!"

_ACUMATICA_VARS = (
    "ACUMATICA_BASE_URL",
    "ACUMATICA_TENANT",
    "ACUMATICA_USERNAME",
    "ACUMATICA_PASSWORD",
    "ACUMATICA_ENDPOINT_VERSION",
    "ACUMATICA_CA_BUNDLE",
    "ACUMATICA_TIMEOUT_SECONDS",
    "ACUMATICA_ALLOW_INSECURE",
    "ACUMATICA_ALLOW_WRITES",
)


def _load_value() -> list:
    """The captured InventoryItem sample's value rows, loaded from disk."""
    with open(_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)["value"]


def _feed_reader(value: list):
    """A fake iter_entities: the sample for InventoryItem, empty lists
    for the other five entity sets."""

    def iter_entities(entity_set, *args, **kwargs):
        if entity_set == "InventoryItem":
            return iter(list(value))
        return iter(())

    return iter_entities


def _stored_items() -> dict:
    """The stored inventory_items rows, keyed by item_code (raw SQL)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, item_code, description, uom, source_system "
                "FROM inventory_items"
            )
            return {
                row[1]: {
                    "id": row[0],
                    "description": row[2],
                    "uom": row[3],
                    "source_system": row[4],
                }
                for row in cur.fetchall()
            }
    finally:
        conn.close()


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


def _item_code_index_definition() -> str | None:
    """The DDL of the item_code index, or None when it is absent."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE schemaname = 'public' "
                "AND indexname = 'ix_inventory_items_item_code'"
            )
            row = cur.fetchone()
            return row[0] if row else None
    finally:
        conn.close()


def _release_row(order_nbr: str) -> dict:
    """One plan_releases row released as ``order_nbr``."""
    return {
        "id": 1,
        "month": "2026-11",
        "site_code": "154",
        "supplier_code": "6995",
        "origin": "AIERP plan 2026-11 site 154 vendor 6995",
        "warehouse_code": "154",
        "vendor_code": "AAVENDOR",
        "order_nbr": order_nbr,
        "outcome": "created",
        "error": None,
        "line_count": 1,
        "value": "42.00",
        "lines": [{"item_code": "AAPOWERAID", "quantity": 5, "unit_cost": "8.40"}],
        "actor": "admin",
        "created_at": "2026-10-21T09:00:00+00:00",
        "updated_at": "2026-10-21T09:00:00+00:00",
    }


def test_issue627_freeform(monkeypatch, client):
    value = _load_value()
    stock_rows = [row for row in value if row.get("StkItem") is True]
    assert [row["InventoryCD"].strip() for row in stock_rows] == [
        "CONPAPERST",
        "AAPOWERAID",
    ]

    # -- 1. The mapping -----------------------------------------------
    # The sample's stock rows are kept with the trimmed InventoryCD,
    # Descr and BaseUnit; the non-stock "<N/A>" row is counted.
    rows, skips = _map_inventory_items(value)
    assert skips == {"non_stock_item": 1}
    by_code = {row["item_code"]: row for row in rows}
    assert set(by_code) == {"CONPAPERST", "AAPOWERAID"}
    assert by_code["CONPAPERST"]["description"] == "Letter Paper Ream Standard"
    assert by_code["CONPAPERST"]["uom"] == "EA"
    for code, mapped in by_code.items():
        raw = next(row for row in value if row["InventoryCD"].strip() == code)
        assert mapped["description"] == (
            raw["Descr"].strip() if raw.get("Descr") else None
        )
        assert mapped["uom"] == raw["BaseUnit"].strip()
        assert mapped["source_system"] == "acumatica"

    # Hostile rows never raise: non-dict rows are malformed, a stock
    # row without a usable InventoryCD is skipped and counted, and a
    # stock row with blank values maps with empty description and uom.
    hostile_rows, hostile_skips = _map_inventory_items(
        [
            None,
            "junk",
            {"StkItem": True},
            {"StkItem": True, "InventoryCD": "   "},
            {
                "StkItem": "true",
                "InventoryCD": "\tX1\t",
                "Descr": "  a description  ",
                "BaseUnit": " EA ",
            },
            {"StkItem": False, "InventoryCD": "NS-1"},
        ]
    )
    assert hostile_skips == {
        "malformed_row": 2,
        "missing_item_code": 2,
        "non_stock_item": 1,
    }
    assert hostile_rows == [
        {
            "item_code": "X1",
            "description": "a description",
            "uom": "EA",
            "source_system": "acumatica",
        }
    ]

    # -- 2. The sync ----------------------------------------------------
    # The fake feed serves the sample for InventoryItem and empty lists
    # for the other sets; run_sync stores the two stock items from the
    # read it already makes, with no new read.
    result = run_sync(iter_entities=_feed_reader(value), as_of=SYNC_AS_OF)
    assert result["inventory_items"] == {"inserted": 2, "updated": 0}
    assert result["skipped"]["non_stock_item"] == 1
    assert "suppliers" not in result
    for model in (
        "item_sites",
        "demand_events",
        "lead_time_observations",
        "on_hand",
        "open_purchase_lines",
    ):
        assert result[model] == {"inserted": 0, "updated": 0}

    stored = _stored_items()
    assert set(stored) == {"CONPAPERST", "AAPOWERAID"}
    assert stored["CONPAPERST"]["description"] == "Letter Paper Ream Standard"
    assert stored["CONPAPERST"]["uom"] == "EA"
    assert stored["CONPAPERST"]["source_system"] == "acumatica"
    aapowerraid_raw = next(
        row for row in value if row["InventoryCD"].strip() == "AAPOWERAID"
    )
    assert stored["AAPOWERAID"]["description"] == aapowerraid_raw["Descr"].strip()
    conpaperst_id = stored["CONPAPERST"]["id"]

    # A second run with the CONPAPERST description changed updates the
    # row — the same row, no duplicate.
    changed = copy.deepcopy(value)
    for row in changed:
        if row["InventoryCD"].strip() == "CONPAPERST":
            row["Descr"] = "Letter Paper Ream Standard (renamed)"
    renamed_result = run_sync(iter_entities=_feed_reader(changed), as_of=SYNC_AS_OF)
    assert renamed_result["inventory_items"] == {"inserted": 0, "updated": 2}
    assert renamed_result["skipped"]["non_stock_item"] == 1
    stored = _stored_items()
    assert set(stored) == {"CONPAPERST", "AAPOWERAID"}
    # The same row was updated in place — no duplicate.
    assert stored["CONPAPERST"]["id"] == conpaperst_id
    assert stored["CONPAPERST"]["description"] == "Letter Paper Ream Standard (renamed)"

    # -- 3. Expedite ----------------------------------------------------
    # The inventory items read model, monkeypatched to the two items of
    # the sample (built from the loaded value), read once per answer.
    calls = {"inventory_items": 0}
    item_lookup = [
        {
            "item_code": row["InventoryCD"].strip(),
            "description": row["Descr"].strip() if row.get("Descr") else None,
        }
        for row in stock_rows
    ]

    def fake_inventory_items_list_all(self):
        calls["inventory_items"] += 1
        return [dict(row) for row in item_lookup]

    monkeypatch.setattr(
        InventoryItemPostgresRepository, "list_all", fake_inventory_items_list_all
    )

    def fake_open_lines(self):
        return [
            {
                "business_key": "RO:PO62701:1",
                "item_code": "CONPAPERST",
                "site_code": "154",
                "supplier_code": "6995",
                "ordered_on": date(2026, 10, 1),
                "promised_on": date(2026, 10, 20),
                "open_units": 10.0,
            },
            {
                "business_key": "RO:PO62702:1",
                "item_code": "ZZUNKNOWN627",
                "site_code": "154",
                "supplier_code": "6995",
                "ordered_on": date(2026, 10, 1),
                "promised_on": date(2026, 10, 20),
                "open_units": 10.0,
            },
        ]

    monkeypatch.setattr(
        OpenPurchaseLinePostgresRepository, "list_latest", fake_open_lines
    )
    monkeypatch.setattr(
        OnHandPostgresRepository,
        "list_rows",
        lambda self, limit, offset, **filters: [],
    )
    monkeypatch.setattr(
        ItemSitePostgresRepository,
        "list_rows",
        lambda self, limit, offset, **filters: [],
    )
    monkeypatch.setattr(
        expedite_module, "recommendations_for_all", lambda as_of=None: []
    )
    monkeypatch.setattr(
        expedite_module,
        "supplier_performance",
        lambda as_of=None, window_days=365: {
            "as_of": TODAY.isoformat(),
            "window_days": window_days,
            "suppliers": [],
        },
    )
    monkeypatch.setattr(expedite_module, "_today", lambda: TODAY)

    result = expedite_list(as_of=TODAY)
    # The item descriptions come from one read of the read model.
    assert calls["inventory_items"] == 1
    by_item = {line["item_code"]: line for line in result["lines"]}
    assert set(by_item) == {"CONPAPERST", "ZZUNKNOWN627"}
    assert by_item["CONPAPERST"]["item_description"] == "Letter Paper Ream Standard"
    # The unknown item's line is still listed, with a null description.
    assert by_item["ZZUNKNOWN627"]["item_description"] is None
    assert [line["reasons"] for line in result["lines"]] == [["late"], ["late"]]

    # The route serves the same field through the response model.
    response = client.get(_URL_EXPEDITE, params={"as_of": TODAY.isoformat()})
    assert response.status_code == 200, response.text
    by_item = {line["item_code"]: line for line in response.json()["lines"]}
    assert by_item["CONPAPERST"]["item_description"] == "Letter Paper Ream Standard"
    assert by_item["ZZUNKNOWN627"]["item_description"] is None
    # The route answered from the same fixed reads: one more model read.
    assert calls["inventory_items"] == 2

    # -- 4. Release tracking ---------------------------------------------
    for var in _ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", _BASE_URL)
    monkeypatch.setenv("ACUMATICA_TENANT", _TENANT)
    monkeypatch.setenv("ACUMATICA_USERNAME", _USERNAME)
    monkeypatch.setenv("ACUMATICA_PASSWORD", _PASSWORD)

    # One released order whose single line is AAPOWERAID, open (the
    # promise 2026-10-25 is after today 2026-10-23, nothing received).
    erp_record = {
        "OrderNbr": {"value": "PO006271"},
        "Status": {"value": "Open"},
        "Hold": {"value": False},
        "Date": {"value": "2026-10-20"},
        "PromisedOn": {"value": "2026-10-25"},
        "OrderTotal": {"value": 42.0},
        "Details": [
            {
                "InventoryID": {"value": "AAPOWERAID"},
                "WarehouseID": {"value": "154"},
                "OrderQty": {"value": 5.0},
                "QtyOnReceipts": {"value": 0.0},
                "Completed": {"value": False},
                "Cancelled": {"value": False},
                "Promised": {"value": "2026-10-25"},
            }
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/entity/auth/login"):
            return httpx.Response(204)
        if request.method == "POST" and request.url.path.endswith(
            "/entity/auth/logout"
        ):
            return httpx.Response(204)
        if request.method == "GET" and request.url.path.endswith("/PurchaseOrder"):
            return httpx.Response(200, json=[erp_record])
        return httpx.Response(404, text="unexpected request")

    monkeypatch.setattr(
        acumatica_client,
        "_build_client",
        lambda ca_bundle, timeout: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(
        tracking, "_load_release_rows", lambda: [_release_row("PO006271")]
    )

    calls["inventory_items"] = 0
    result = tracking.release_tracking(today=TODAY)
    # The line descriptions come from one read of the read model.
    assert calls["inventory_items"] == 1
    [order] = result["orders"]
    assert order["order_nbr"] == "PO006271"
    assert order["state"] == "open"
    [line] = order["lines"]
    assert line["item_code"] == "AAPOWERAID"
    assert line["item_description"] == aapowerraid_raw["Descr"].strip()

    # The route serves the same field through the response model.
    response = client.get(_URL_RELEASES)
    assert response.status_code == 200, response.text
    [order] = response.json()["orders"]
    assert order["order_nbr"] == "PO006271"
    [line] = order["lines"]
    assert line["item_code"] == "AAPOWERAID"
    assert line["item_description"] == aapowerraid_raw["Descr"].strip()
    assert calls["inventory_items"] == 2

    # The empty inventory items read model never drops the field from
    # the response: the order line still carries item_description —
    # null for the (only) unknown item — read once from the (empty)
    # read model.
    def empty_inventory_items_list_all(self):
        calls["inventory_items"] += 1
        return []

    monkeypatch.setattr(
        InventoryItemPostgresRepository,
        "list_all",
        empty_inventory_items_list_all,
    )
    calls["inventory_items"] = 0
    response = client.get(_URL_RELEASES)
    assert response.status_code == 200, response.text
    [empty_order] = response.json()["orders"]
    assert empty_order["order_nbr"] == "PO006271"
    [empty_line] = empty_order["lines"]
    assert empty_line["item_code"] == "AAPOWERAID"
    assert "item_description" in empty_line
    assert empty_line["item_description"] is None
    assert calls["inventory_items"] == 1

    # -- 5. The migration ---------------------------------------------------
    cfg = _alembic_config()
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert heads[0] == CURRENT_HEAD
    assert _alembic_revision() == CURRENT_HEAD
    # The unique index is in place at head, and the rows stored by the
    # sync are the ones it protects.
    indexdef = _item_code_index_definition()
    assert indexdef is not None
    assert "UNIQUE" in indexdef.upper()
    assert set(_stored_items()) == {"CONPAPERST", "AAPOWERAID"}

    # The downgrade restores the non-unique index and keeps the table
    # and its rows.
    alembic_command.downgrade(cfg, "0023_suppliers")
    assert _alembic_revision() == "0023_suppliers"
    indexdef = _item_code_index_definition()
    assert indexdef is not None
    assert "UNIQUE" not in indexdef.upper()
    assert set(_stored_items()) == {"CONPAPERST", "AAPOWERAID"}

    # The upgrade recreates the unique index, rows untouched.
    alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    indexdef = _item_code_index_definition()
    assert indexdef is not None
    assert "UNIQUE" in indexdef.upper()
    assert set(_stored_items()) == {"CONPAPERST", "AAPOWERAID"}
