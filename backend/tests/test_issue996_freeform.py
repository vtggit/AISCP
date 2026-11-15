"""Issue #996 proof: the inventory-item names across both sync runners.

Against the migrated test database, with the real repositories and the
real runners — the Odoo runner with a fake client the way
test_issue757_freeform.py does it, the Acumatica runner with an
``iter_entities`` double the way test_issue825_freeform.py does it:

* names load: a fake client whose read_products returns ODOO-1 named
  "Oak desk" stores that row with source_system odoo, and the result's
  inventory_items reads one inserted; the other read models proceed
  (the item site is stored); an item master row names no site, so
  nothing is added to erp_code_clashes;
* Odoo refused: with SHARED-1 stored under acumatica as "Acumatica
  name", an Odoo sync offering SHARED-1 as "Odoo name" leaves the row
  exactly as it was (id, description, source_system and updated_at
  included) and the result's item_names_held is 1;
* Acumatica refused: with SHARED-2 stored under odoo, an Acumatica
  sync offering it leaves the row exactly as it was, and the result's
  item_names_held is 1;
* its own row updates: an Odoo sync offering ODOO-1 again with a new
  name updates that row, and with nothing held the result has no
  item_names_held key;
* skipped read: a client with no read_products, and a client whose
  read_products raises OdooError, each store no inventory item, add no
  inventory_items key and still store the item sites;
* test double: an inventory-items repository with no held_by_others
  skips the guard — the held row is offered to the repository anyway;
* one statement: held_by_others runs one SQL statement for 50 codes,
  counted through the normal connection path, and the production code
  never swaps, patches or rebinds psycopg2.connect.
"""

import os
import re
from pathlib import Path

import psycopg2

from app.db.connection import get_connection
from app.feed.odoo_client import OdooError
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.sync.acumatica import run_sync as run_acumatica_sync
from app.sync.odoo import run_sync as run_odoo_sync

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

AS_OF = "2026-11-05T00:00:00Z"

_SIX_ENTITY_SETS = (
    "InventoryItem",
    "INItemSite",
    "INSiteStatus",
    "INTran",
    "POLine",
    "POReceiptLine",
)


# ---------------------------------------------------------------------------
# Small read helpers (straight through the normal connection path).
# ---------------------------------------------------------------------------


def _stored(item_code: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, description, source_system, updated_at "
                "FROM inventory_items WHERE item_code = %s",
                (item_code,),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _item_site(item_code: str, site_code: str):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT unit_cost, source_system FROM item_sites "
                "WHERE item_code = %s AND site_code = %s",
                (item_code, site_code),
            )
            return cur.fetchone()
    finally:
        conn.close()


def _clash_rows() -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM erp_code_clashes")
            return cur.fetchone()[0]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The fake Odoo client (the way test_issue757_freeform.py fakes it):
# canned rows for the six reads, every read recorded, and no
# read_products on the base class — the optional eighth read is
# skipped.
# ---------------------------------------------------------------------------


def _odoo_item_site(item_code, site_code, unit_cost):
    return {
        "item_code": item_code,
        "site_code": site_code,
        "unit_cost": unit_cost,
        "reorder_point": None,
        "safety_stock": None,
        "max_qty": None,
        "order_qty": None,
        "master_lead_time_days": None,
        "source_system": "odoo",
    }


def _product(item_code, description, uom="EA"):
    return {
        "item_code": item_code,
        "description": description,
        "uom": uom,
        "source_system": "odoo",
    }


class _StubOdooClient:
    """Canned rows for the six reads, every read recorded, no
    read_products: the optional eighth read is skipped."""

    def __init__(self, rows_by_table: dict):
        self.rows_by_table = rows_by_table
        self.read_order: list[str] = []

    def _read(self, name: str):
        self.read_order.append(name)
        return list(self.rows_by_table.get(name, []))

    def read_item_sites(self):
        return self._read("item_sites")

    def read_on_hand(self, as_of=None):
        return self._read("on_hand")

    def read_demand_events(self):
        return self._read("demand_events")

    def read_lead_time_observations(self):
        return self._read("lead_time_observations")

    def read_open_purchase_lines(self, as_of=None):
        return self._read("open_purchase_lines")

    def read_open_sales_lines(self, as_of=None):
        return self._read("open_sales_lines")


class _ProductsOdooClient(_StubOdooClient):
    """Adds read_products: canned product rows, or OdooError when
    ``fail_products`` (Odoo refusing the product.product model)."""

    def __init__(self, rows_by_table: dict, products=None, fail_products=False):
        super().__init__(rows_by_table)
        self.products = products
        self.fail_products = fail_products

    def read_products(self):
        self.read_order.append("read_products")
        if self.fail_products:
            raise OdooError("stub read_products failed")
        return list(self.products)


class _StubInventoryItemsRepository:
    """Stands in for the inventory-items repository: records the rows it
    was handed and has no held_by_others — the guard must be skipped."""

    def __init__(self):
        self.calls: list[list[dict]] = []

    def upsert_many(self, rows):
        self.calls.append(rows)
        return {"inserted": len(rows), "updated": 0}


# ---------------------------------------------------------------------------
# The Acumatica iter_entities double (the way test_issue825_freeform.py
# doubles it): canned rows per entity set, the read order recorded.
# ---------------------------------------------------------------------------


def _acumatica_double(rows_by_set: dict):
    read_order: list[str] = []

    def iter_entities(entity_set, *args, **kwargs):
        read_order.append(entity_set)
        return iter(list(rows_by_set.get(entity_set, [])))

    return iter_entities, read_order


# ---------------------------------------------------------------------------
# Counting the SQL statements of a call, through the normal connection
# path (the same wrapper the #980 and #994 proofs use).
# ---------------------------------------------------------------------------


class _CountingCursor:
    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def execute(self, sql, *args, **kwargs):
        self._counter[0] += 1
        self._queries.append(sql)
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _CountingConnection:
    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def cursor(self, *args, **kwargs):
        return _CountingCursor(
            self._real.cursor(*args, **kwargs), self._counter, self._queries
        )

    def __getattr__(self, name):
        return getattr(self._real, name)


def _measure(fn):
    """Run fn with psycopg2.connect wrapped; (result, statements, SQL)."""
    counter = [0]
    queries: list[str] = []
    real_connect = psycopg2.connect

    def counting_connect(**params):
        return _CountingConnection(real_connect(**params), counter, queries)

    psycopg2.connect = counting_connect
    try:
        result = fn()
    finally:
        psycopg2.connect = real_connect
    return result, counter[0], queries


def test_issue996_freeform(client):
    repo = InventoryItemPostgresRepository()

    # 1. Names load: the fake client's read_products returns ODOO-1
    #    named "Oak desk"; the row is stored with source_system odoo,
    #    the result's inventory_items reads one inserted, and the other
    #    read models proceed (the item site is stored).  An item master
    #    row names no site, so nothing is added to erp_code_clashes.
    stub = _ProductsOdooClient(
        {"item_sites": [_odoo_item_site("ODOO-1", "S1", 10.0)]},
        products=[_product("ODOO-1", "Oak desk")],
    )
    result = run_odoo_sync(client=stub, as_of=AS_OF)
    assert result["inventory_items"] == {"inserted": 1, "updated": 0}
    row = _stored("ODOO-1")
    assert row is not None
    assert row[1] == "Oak desk"
    assert row[2] == "odoo"
    assert result["item_sites"] == {"inserted": 1, "updated": 0}
    assert _item_site("ODOO-1", "S1") == (10.0, "odoo")
    # Nothing held: the result shape is unchanged.
    assert "item_names_held" not in result
    assert _clash_rows() == 0

    # 2. Odoo refused: with SHARED-1 stored under acumatica as
    #    "Acumatica name", an Odoo sync offering SHARED-1 as "Odoo
    #    name" leaves the row exactly as it was and reports one held
    #    name; the other offered row (ODOO-1, its own) still updates.
    assert (
        repo.create(
            {
                "item_code": "SHARED-1",
                "description": "Acumatica name",
                "uom": "EA",
                "source_system": "acumatica",
            }
        )["item_code"]
        == "SHARED-1"
    )
    before = _stored("SHARED-1")
    stub = _ProductsOdooClient(
        {"item_sites": [_odoo_item_site("ODOO-1", "S1", 10.0)]},
        products=[_product("SHARED-1", "Odoo name"), _product("ODOO-1", "Oak desk")],
    )
    result = run_odoo_sync(client=stub, as_of=AS_OF)
    assert result["item_names_held"] == 1
    assert result["inventory_items"] == {"inserted": 0, "updated": 1}
    assert _stored("SHARED-1") == before
    # Nothing is added to erp_code_clashes for the held name.
    assert _clash_rows() == 0

    # 3. Acumatica refused: with SHARED-2 stored under odoo, an
    #    Acumatica sync offering it leaves the row exactly as it was
    #    and reports one held name; no inventory-item row is written.
    assert (
        repo.create(
            {
                "item_code": "SHARED-2",
                "description": "Odoo name",
                "uom": "EA",
                "source_system": "odoo",
            }
        )["item_code"]
        == "SHARED-2"
    )
    before = _stored("SHARED-2")
    double, read_order = _acumatica_double(
        {
            "InventoryItem": [
                {
                    "InventoryID": 1,
                    "InventoryCD": "SHARED-2",
                    "StkItem": True,
                    "Descr": "Acumatica name 2",
                    "BaseUnit": "EA",
                }
            ]
        }
    )
    result = run_acumatica_sync(iter_entities=double, as_of=AS_OF)
    assert read_order == list(_SIX_ENTITY_SETS)
    assert result["item_names_held"] == 1
    assert result["inventory_items"] == {"inserted": 0, "updated": 0}
    assert _stored("SHARED-2") == before
    assert _clash_rows() == 0

    # 4. Its own row updates: an Odoo sync offering ODOO-1 again with a
    #    new name updates that row, and with nothing held the result
    #    has no item_names_held key.
    stub = _ProductsOdooClient(
        {"item_sites": [_odoo_item_site("ODOO-1", "S1", 10.0)]},
        products=[_product("ODOO-1", "Oak desk, wide")],
    )
    result = run_odoo_sync(client=stub, as_of=AS_OF)
    assert result["inventory_items"] == {"inserted": 0, "updated": 1}
    assert "item_names_held" not in result
    row = _stored("ODOO-1")
    assert row[1] == "Oak desk, wide"
    assert row[2] == "odoo"

    # 5. Skipped read: a client with no read_products stores no
    #    inventory item, adds no inventory_items key and still stores
    #    the item sites.
    stub = _StubOdooClient({"item_sites": [_odoo_item_site("SITE-X", "S9", 5.0)]})
    result = run_odoo_sync(client=stub, as_of=AS_OF)
    assert "inventory_items" not in result
    assert "item_names_held" not in result
    assert _stored("SITE-X") is None
    assert result["item_sites"] == {"inserted": 1, "updated": 0}
    assert _item_site("SITE-X", "S9") == (5.0, "odoo")

    # 6. Skipped read: a client whose read_products raises OdooError
    #    stores no inventory item, adds no inventory_items key and
    #    still stores the item sites.
    failing = _ProductsOdooClient(
        {"item_sites": [_odoo_item_site("SITE-Y", "S9", 5.0)]},
        products=[_product("SITE-Y", "Should never be stored")],
        fail_products=True,
    )
    result = run_odoo_sync(client=failing, as_of=AS_OF)
    assert "inventory_items" not in result
    assert "item_names_held" not in result
    assert _stored("SITE-Y") is None
    assert result["item_sites"] == {"inserted": 1, "updated": 0}
    assert _item_site("SITE-Y", "S9") == (5.0, "odoo")

    # 7. Test double: an inventory-items repository with no
    #    held_by_others skips the guard — the held row is offered to
    #    the repository anyway, unfiltered.
    assert (
        repo.create(
            {
                "item_code": "SHARED-3",
                "description": "Acumatica name 3",
                "uom": "EA",
                "source_system": "acumatica",
            }
        )["item_code"]
        == "SHARED-3"
    )
    double_repo = _StubInventoryItemsRepository()
    stub = _ProductsOdooClient({}, products=[_product("SHARED-3", "Odoo name 3")])
    result = run_odoo_sync(client=stub, as_of=AS_OF, inventory_items_repo=double_repo)
    assert double_repo.calls == [[_product("SHARED-3", "Odoo name 3")]]
    assert result["inventory_items"] == {"inserted": 1, "updated": 0}
    assert "item_names_held" not in result

    # 8. One statement: held_by_others answers 50 codes with one SQL
    #    statement, counted through the normal connection path, and
    #    maps exactly the codes stored under a different source.
    assert (
        repo.create(
            {
                "item_code": "BULK-00",
                "description": "bulk",
                "uom": "EA",
                "source_system": "acumatica",
            }
        )["item_code"]
        == "BULK-00"
    )
    assert (
        repo.create(
            {
                "item_code": "BULK-01",
                "description": "bulk",
                "uom": "EA",
                "source_system": "odoo",
            }
        )["item_code"]
        == "BULK-01"
    )
    codes = ["BULK-00", "BULK-01"]
    codes += [f"BULK-{index:02d}" for index in range(2, 50)]
    assert len(codes) == 50
    held, statements, _ = _measure(lambda: repo.held_by_others("odoo", codes))
    assert statements == 1, statements
    assert held == {"BULK-00": "acumatica"}
    # Codes stored under the asking source itself are not held.
    assert "BULK-01" not in held
    # An empty code list answers empty.
    assert repo.held_by_others("odoo", []) == {}

    # 9. The production code never swaps, patches or rebinds
    #    psycopg2.connect (the counting above wraps it in the test
    #    only, the way the #980 and #994 proofs do).
    rebind = re.compile(r"^\s*psycopg2\.connect\s*=(?!=)", re.MULTILINE)
    for path in sorted(Path(os.path.join(_BACKEND_ROOT, "app")).rglob("*.py")):
        assert not rebind.search(path.read_text(encoding="utf-8")), path.name
