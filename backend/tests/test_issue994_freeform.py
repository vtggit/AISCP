"""Issue #994 proof: the ERP code-clash guard across both sync runners.

Against the migrated test database, with the real repositories and the
real runners — the Odoo runner with a fake client the way
test_issue757_freeform.py does it, the Acumatica runner with an
``iter_entities`` double the way test_issue825_freeform.py does it:

* the migration: 0039_erp_code_clashes is the only Alembic head, it is
  applied, and it creates ``erp_code_clashes`` with the stated columns
  (bigint identity id, text item_code/site_code/held_by/refused_source
  not null, first_seen_at and last_seen_at timestamptz not null with a
  now() server default, resolved_at timestamptz null) and the unique
  (item_code, site_code, refused_source) constraint; the downgrade
  drops only that table and the upgrade back re-creates it;
* Odoo refused: with ITEM-A at S1 stored under acumatica (unit cost
  10), an Odoo sync offering ITEM-A at S1 (unit cost 99) plus an
  on-hand row, a demand event, a lead-time row and open purchase and
  sales lines leaves that item_sites row at unit cost 10 and
  source_system acumatica, stores none of the Odoo rows for that pair
  and stores all the Odoo rows of another pair (ITEM-B at S1); the
  result's clashes is 1;
* Acumatica refused: the mirror case — the pair stored under odoo and
  an Acumatica sync offering it — with the double asked only for the
  six entity sets;
* recorded and resolved: after the Odoo sync one erp_code_clashes row
  reads held_by acumatica, refused_source odoo and null resolved_at
  with both timestamps set; a second sync still offering the pair
  moves last_seen_at forward and adds no row; a sync offering only a
  demand event for the pair (no item-site row) is still refused and
  recorded; a sync not offering the pair sets resolved_at (the
  resolution UPDATE runs even though held_by_others is empty and the
  result has no clashes key); a later sync offering it again clears
  resolved_at;
* nothing clashes: a sync with no held pair has no clashes key,
  stores no clash row and the pair's own source updates its row as
  before;
* a null site: a lead-time row with a null site_code is stored;
* one statement: held_by_others answers 50 pairs with one SQL
  statement, counted through the normal connection path (the same
  psycopg2.connect wrapper the #980 proof uses); the production code
  nowhere swaps, patches or rebinds psycopg2.connect;
* concurrent syncs from different ERPs for the same pair are guarded
  atomically: the holder's source_system survives the race, and a
  later sync from the other ERP still cannot overwrite it.
"""

import os
import re
import threading
import time
from pathlib import Path

import psycopg2
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.db.connection import get_connection
from app.repositories.erp_code_clashes_postgres_repository import (
    ErpCodeClashPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.sync.acumatica import run_sync as run_acumatica_sync
from app.sync.odoo import run_sync as run_odoo_sync
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")
_PREVIOUS_REVISION = "0038_snapshot_signal_quantity"

AS_OF = "2026-11-05T00:00:00Z"

HELD_ITEM = "ITEM-A"
HELD_SITE = "S1"
HELD_COST = 10.0
OFFERED_COST = 99.0
OTHER_ITEM = "ITEM-B"

MIRROR_ITEM = "ITEM-C"
MIRROR_SITE = "9"
MIRROR_COST = 12.0
MIRROR_OTHER_ITEM = "ITEM-D"

SIX_TABLES = (
    "item_sites",
    "on_hand",
    "demand_events",
    "lead_time_observations",
    "open_purchase_lines",
    "open_sales_lines",
)


# ---------------------------------------------------------------------------
# Small read helpers (straight through the normal connection path).
# ---------------------------------------------------------------------------


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


def _count(table: str, item_code: str, site_code: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {table} "
                "WHERE item_code = %s AND site_code = %s",
                (item_code, site_code),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _count_business_key(table: str, business_key: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) FROM {table} WHERE business_key = %s",
                (business_key,),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _clash_rows() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT item_code, site_code, held_by, refused_source, "
                "first_seen_at, last_seen_at, resolved_at "
                "FROM erp_code_clashes ORDER BY id"
            )
            return cur.fetchall()
    finally:
        conn.close()


def _clash_for(item_code: str, site_code: str, refused_source: str):
    for row in _clash_rows():
        if (row[0], row[1], row[3]) == (item_code, site_code, refused_source):
            return row
    return None


def _table_columns() -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable, column_default, "
                "is_identity "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = "
                "'erp_code_clashes' ORDER BY ordinal_position"
            )
            return {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.fetchall()}
    finally:
        conn.close()


def _unique_column_sets() -> list[list[str]]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT con.conname, a.attname "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "JOIN pg_attribute a ON a.attrelid = con.conrelid "
                "AND a.attnum = ANY(con.conkey) "
                "WHERE rel.relname = 'erp_code_clashes' AND con.contype = 'u' "
                "ORDER BY con.conname, a.attnum"
            )
            by_name: dict[str, list[str]] = {}
            for name, att in cur.fetchall():
                by_name.setdefault(name, []).append(att)
            return [by_name[name] for name in sorted(by_name)]
    finally:
        conn.close()


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", _MIGRATIONS_DIR)
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


# ---------------------------------------------------------------------------
# The fake Odoo client (the way test_issue757_freeform.py fakes it):
# canned rows for the six reads, every read recorded, no write support
# and no read_supplier_terms (the optional seventh is skipped).
# ---------------------------------------------------------------------------


class _StubOdooClient:
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


def _odoo_on_hand(item_code, site_code, quantity):
    return {
        "item_code": item_code,
        "site_code": site_code,
        "quantity_on_hand": quantity,
        "as_of": AS_OF,
        "source_system": "odoo",
    }


def _odoo_demand(business_key, item_code, site_code, quantity, occurred_on):
    return {
        "business_key": business_key,
        "item_code": item_code,
        "site_code": site_code,
        "quantity": quantity,
        "occurred_on": occurred_on,
        "event_type": "shipment",
        "source_system": "odoo",
    }


def _odoo_lead_time(business_key, item_code, site_code, ordered_on, received_on):
    return {
        "business_key": business_key,
        "supplier_code": "SUP-1",
        "item_code": item_code,
        "site_code": site_code,
        "ordered_on": ordered_on,
        "received_on": received_on,
        "promised_on": None,
        "source_system": "odoo",
    }


def _odoo_open_purchase(business_key, item_code, site_code, open_units):
    return {
        "business_key": business_key,
        "item_code": item_code,
        "site_code": site_code,
        "supplier_code": "SUP-1",
        "order_type": "PO",
        "open_units": open_units,
        "promised_on": "2026-11-10",
        "ordered_on": "2026-10-01",
        "as_of": AS_OF,
        "source_system": "odoo",
    }


def _odoo_open_sales(business_key, item_code, site_code, open_units):
    return {
        "business_key": business_key,
        "item_code": item_code,
        "site_code": site_code,
        "order_type": "SO",
        "open_units": open_units,
        "ship_on": "2026-11-15",
        "as_of": AS_OF,
        "source_system": "odoo",
    }


def _seed_item_site(item_code, site_code, unit_cost, source_system) -> dict:
    """Store one item_sites row straight through the real repository."""
    return ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": item_code,
                "site_code": site_code,
                "unit_cost": unit_cost,
                "reorder_point": None,
                "safety_stock": None,
                "max_qty": None,
                "order_qty": None,
                "master_lead_time_days": None,
                "source_system": source_system,
            }
        ]
    )


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


SIX_ENTITY_SETS = (
    "InventoryItem",
    "INItemSite",
    "INSiteStatus",
    "INTran",
    "POLine",
    "POReceiptLine",
)


# ---------------------------------------------------------------------------
# Counting the SQL statements of a call, through the normal connection
# path (the same wrapper the #980 proof uses).
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


def test_issue994_freeform(client):
    item_sites_repo = ItemSitePostgresRepository()
    clashes_repo = ErpCodeClashPostgresRepository()

    # 1. The migration: the new revision is the only Alembic head, it is
    #    applied, and the table has the stated columns and the unique
    #    (item_code, site_code, refused_source) constraint.
    heads = ScriptDirectory(_MIGRATIONS_DIR).get_heads()
    assert list(heads) == [CURRENT_HEAD]
    assert _alembic_revision() == CURRENT_HEAD
    columns = _table_columns()
    assert set(columns) == {
        "id",
        "item_code",
        "site_code",
        "held_by",
        "refused_source",
        "first_seen_at",
        "last_seen_at",
        "resolved_at",
    }
    assert columns["id"][0] == "bigint"
    assert columns["id"][1] == "NO"
    assert columns["id"][3] in ("YES", "ALWAYS")  # a generated identity
    for column in ("item_code", "site_code", "held_by", "refused_source"):
        assert columns[column] == ("text", "NO", None, "NO"), column
    for column in ("first_seen_at", "last_seen_at"):
        assert columns[column] == (
            "timestamp with time zone",
            "NO",
            "now()",
            "NO",
        ), column
    assert columns["resolved_at"] == ("timestamp with time zone", "YES", None, "NO")
    assert [set(cols) for cols in _unique_column_sets()] == [
        {"item_code", "site_code", "refused_source"}
    ]

    # 2. Seed: ITEM-A at S1 is stored under acumatica with unit cost 10
    #    (the first holder), through the real repository.
    assert _seed_item_site(HELD_ITEM, HELD_SITE, HELD_COST, "acumatica") == {
        "inserted": 1,
        "updated": 0,
    }
    assert _item_site(HELD_ITEM, HELD_SITE) == (HELD_COST, "acumatica")

    # 3. Odoo refused: an Odoo sync offering ITEM-A at S1 (unit cost 99)
    #    plus an on-hand row, a demand event, a lead-time row and open
    #    lines leaves the held item_sites row exactly as it was, stores
    #    none of the Odoo rows for that pair, stores all the Odoo rows
    #    of the other pair (ITEM-B at S1) and reports clashes 1.
    held_offer = {
        "item_sites": [
            _odoo_item_site(HELD_ITEM, HELD_SITE, OFFERED_COST),
            _odoo_item_site(OTHER_ITEM, HELD_SITE, 5.0),
        ],
        "on_hand": [_odoo_on_hand(HELD_ITEM, HELD_SITE, 7.0)],
        "demand_events": [
            _odoo_demand(
                "odoo:994:demand:held:1", HELD_ITEM, HELD_SITE, 3.0, "2026-11-01"
            )
        ],
        "lead_time_observations": [
            _odoo_lead_time(
                "odoo:994:lead:held:1",
                HELD_ITEM,
                HELD_SITE,
                "2026-10-01",
                "2026-11-01",
            )
        ],
        "open_purchase_lines": [
            _odoo_open_purchase("odoo:994:po:held:1", HELD_ITEM, HELD_SITE, 4.0)
        ],
        "open_sales_lines": [
            _odoo_open_sales("odoo:994:so:held:1", HELD_ITEM, HELD_SITE, 2.0)
        ],
    }
    stub = _StubOdooClient(held_offer)
    result = run_odoo_sync(client=stub)
    assert stub.read_order == [
        "item_sites",
        "on_hand",
        "demand_events",
        "lead_time_observations",
        "open_purchase_lines",
        "open_sales_lines",
    ]
    assert result["clashes"] == 1
    assert set(result) == set(SIX_TABLES) | {"clashes"}
    # The first holder keeps it: the held row is exactly as it was.
    assert _item_site(HELD_ITEM, HELD_SITE) == (HELD_COST, "acumatica")
    # None of the Odoo rows of the held pair were stored.
    assert _count("on_hand", HELD_ITEM, HELD_SITE) == 0
    assert _count("demand_events", HELD_ITEM, HELD_SITE) == 0
    assert _count("lead_time_observations", HELD_ITEM, HELD_SITE) == 0
    assert _count("open_purchase_lines", HELD_ITEM, HELD_SITE) == 0
    assert _count("open_sales_lines", HELD_ITEM, HELD_SITE) == 0
    # All the Odoo rows of the other pair were stored.
    assert result["item_sites"] == {"inserted": 1, "updated": 0}
    assert _item_site(OTHER_ITEM, HELD_SITE) == (5.0, "odoo")

    # 4. Recorded: one erp_code_clashes row reads held_by acumatica,
    #    refused_source odoo and null resolved_at, with both timestamps
    #    set (a new row sets both).
    rows = _clash_rows()
    assert len(rows) == 1
    (
        item_code,
        site_code,
        held_by,
        refused_source,
        first_seen,
        last_seen,
        resolved_at,
    ) = rows[0]
    assert (item_code, site_code) == (HELD_ITEM, HELD_SITE)
    assert held_by == "acumatica"
    assert refused_source == "odoo"
    assert first_seen is not None
    assert last_seen is not None
    assert first_seen == last_seen
    assert resolved_at is None

    # 5. Still offering the pair: last_seen_at moves forward and no row
    #    is added; the other pair's row is updated, not re-inserted.
    time.sleep(0.05)
    stub = _StubOdooClient(held_offer)
    second = run_odoo_sync(client=stub)
    assert second["clashes"] == 1
    rows = _clash_rows()
    assert len(rows) == 1
    assert rows[0][5] > last_seen
    last_seen = rows[0][5]
    assert second["item_sites"] == {"inserted": 0, "updated": 1}

    # 6. A pair only in a child list: an Odoo demand event for the held
    #    pair, with no Odoo item-site row for it, is still refused and
    #    recorded.
    child_only = {
        "demand_events": [
            _odoo_demand(
                "odoo:994:demand:held:2", HELD_ITEM, HELD_SITE, 2.0, "2026-11-02"
            )
        ]
    }
    time.sleep(0.05)
    third = run_odoo_sync(client=_StubOdooClient(child_only))
    assert third["clashes"] == 1
    assert _count("demand_events", HELD_ITEM, HELD_SITE) == 0
    rows = _clash_rows()
    assert len(rows) == 1
    assert rows[0][5] > last_seen
    last_seen = rows[0][5]

    # 7. Not offering the pair: the result has no clashes key (held
    #    empty) yet the resolution UPDATE still runs — the open row gets
    #    resolved_at set, so a clash someone fixed in the ERP closes
    #    itself.
    other_only = {
        "item_sites": [_odoo_item_site(OTHER_ITEM, HELD_SITE, 6.0)],
    }
    fourth = run_odoo_sync(client=_StubOdooClient(other_only))
    assert "clashes" not in fourth
    assert fourth["item_sites"] == {"inserted": 0, "updated": 1}
    row = _clash_for(HELD_ITEM, HELD_SITE, "odoo")
    assert row is not None
    assert row[6] is not None

    # 8. Offering it again: resolved_at is cleared and last_seen_at
    #    moves forward again.
    time.sleep(0.05)
    fifth = run_odoo_sync(client=_StubOdooClient(held_offer))
    assert fifth["clashes"] == 1
    row = _clash_for(HELD_ITEM, HELD_SITE, "odoo")
    assert row[6] is None
    assert row[5] > last_seen

    # 9. A null site: a lead-time row with a null site_code is stored
    #    (a null site identifies no pair, so the guard keeps the row).
    null_site = {
        "lead_time_observations": [
            _odoo_lead_time(
                "odoo:994:lead:null-site",
                "ITEM-E",
                None,
                "2026-10-01",
                "2026-11-01",
            )
        ],
    }
    sixth = run_odoo_sync(client=_StubOdooClient(null_site))
    assert "clashes" not in sixth
    assert sixth["lead_time_observations"] == {"inserted": 1, "updated": 0}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT site_code, source_system FROM lead_time_observations "
                "WHERE business_key = 'odoo:994:lead:null-site'"
            )
            stored_null_site = cur.fetchone()
    finally:
        conn.close()
    assert stored_null_site == (None, "odoo")
    # No clash row for the null-site pair.
    assert _clash_for("ITEM-E", None, "odoo") is None

    # 10. Nothing clashes: a sync with no held pair has no clashes key
    #     and stores no clash row, and the pair's own source updates its
    #     row as before.
    seventh = run_odoo_sync(client=_StubOdooClient(other_only))
    assert "clashes" not in seventh
    assert seventh["item_sites"] == {"inserted": 0, "updated": 1}
    assert _clash_for(OTHER_ITEM, HELD_SITE, "odoo") is None
    assert _item_site(OTHER_ITEM, HELD_SITE) == (6.0, "odoo")

    # 11. Acumatica refused: the mirror case — ITEM-C at 9 stored under
    #     odoo (unit cost 12), an Acumatica sync offering it.  The
    #     double stands in for the six entity sets only.
    assert _seed_item_site(MIRROR_ITEM, MIRROR_SITE, MIRROR_COST, "odoo") == {
        "inserted": 1,
        "updated": 0,
    }
    acumatica_offer = {
        "InventoryItem": [
            {"InventoryID": 1, "InventoryCD": MIRROR_ITEM, "StkItem": True},
            {"InventoryID": 2, "InventoryCD": MIRROR_OTHER_ITEM, "StkItem": True},
        ],
        "INItemSite": [
            {"InventoryID": 1, "SiteID": 9, "AvgCost": 999.0},
            {"InventoryID": 2, "SiteID": 9, "AvgCost": 20.0},
        ],
        "INSiteStatus": [
            {"InventoryID": 1, "SiteID": 9, "QtyOnHand": 4.0},
        ],
        "INTran": [
            {
                "InventoryID": 1,
                "SiteID": 9,
                "DocType": "I",
                "TranType": "INV",
                "InvtMult": -1,
                "BaseQty": 3.0,
                "TranDate": "2026-11-01",
                "RefNbr": "T-994",
                "LineNbr": 1,
            }
        ],
        "POLine": [
            {
                "InventoryID": 1,
                "SiteID": 9,
                "OrderType": "PO",
                "OrderNbr": "PO-994",
                "LineNbr": 1,
                "BaseOpenQty": 5.0,
                "OrderDate": "2026-10-01",
                "PromisedDate": "2026-11-10",
                "VendorID": 700,
            },
            {
                "InventoryID": 2,
                "SiteID": 9,
                "OrderType": "PO",
                "OrderNbr": "PO-995",
                "LineNbr": 1,
                "BaseOpenQty": 2.0,
                "OrderDate": "2026-10-02",
                "PromisedDate": "2026-11-12",
                "VendorID": 701,
            },
            {
                "InventoryID": 1,
                "SiteID": 9,
                "OrderType": "RO",
                "OrderNbr": "RO-994",
                "LineNbr": 7,
                "BaseOpenQty": 0.0,
                "OrderDate": "2026-10-05",
                "PromisedDate": "2026-11-01",
                "VendorID": 700,
            },
        ],
        "POReceiptLine": [
            {
                "InventoryID": 1,
                "SiteID": 9,
                "POType": "RO",
                "PONbr": "RO-994",
                "POLineNbr": 7,
                "InvtMult": 1,
                "ReceiptType": "IR",
                "ReceiptNbr": "R-994",
                "LineNbr": 7,
                "ReceiptDate": "2026-11-02",
            }
        ],
    }
    double, read_order = _acumatica_double(acumatica_offer)
    mirror_result = run_acumatica_sync(iter_entities=double, as_of=AS_OF)
    assert read_order == list(SIX_ENTITY_SETS)
    assert mirror_result["clashes"] == 1
    # The first holder keeps it: the held row is exactly as it was.
    assert _item_site(MIRROR_ITEM, MIRROR_SITE) == (MIRROR_COST, "odoo")
    # None of the Acumatica rows of the held pair were stored.
    assert _count("on_hand", MIRROR_ITEM, MIRROR_SITE) == 0
    assert _count("demand_events", MIRROR_ITEM, MIRROR_SITE) == 0
    assert _count("lead_time_observations", MIRROR_ITEM, MIRROR_SITE) == 0
    assert _count("open_purchase_lines", MIRROR_ITEM, MIRROR_SITE) == 0
    # The rows of the other pair (and the untouched inventory items)
    # were stored.
    assert mirror_result["item_sites"] == {"inserted": 1, "updated": 0}
    assert _item_site(MIRROR_OTHER_ITEM, MIRROR_SITE) == (20.0, "acumatica")
    assert mirror_result["on_hand"] == {"inserted": 0, "updated": 0}
    assert mirror_result["demand_events"] == {"inserted": 0, "updated": 0}
    assert mirror_result["lead_time_observations"] == {"inserted": 0, "updated": 0}
    assert mirror_result["open_purchase_lines"] == {"inserted": 1, "updated": 0}
    assert _count_business_key("open_purchase_lines", "PO:PO-995:1") == 1
    assert mirror_result["inventory_items"] == {"inserted": 2, "updated": 0}
    # Recorded: held_by odoo, refused_source acumatica, still open.
    row = _clash_for(MIRROR_ITEM, MIRROR_SITE, "acumatica")
    assert row is not None
    assert row[2] == "odoo"
    assert row[6] is None

    # 12. One statement: held_by_others answers 50 pairs with one SQL
    #     statement, counted through the normal connection path, and
    #     maps exactly the pairs stored under a different source.
    assert _seed_item_site("BULK-00", "00", 1.0, "acumatica") == {
        "inserted": 1,
        "updated": 0,
    }
    pairs = [(HELD_ITEM, HELD_SITE), (MIRROR_ITEM, MIRROR_SITE), ("BULK-00", "00")]
    pairs += [(f"BULK-{index:02d}", f"{index:02d}") for index in range(3, 50)]
    assert len(pairs) == 50
    held, statements, _ = _measure(
        lambda: item_sites_repo.held_by_others("odoo", pairs)
    )
    assert statements == 1, statements
    assert held == {(HELD_ITEM, HELD_SITE): "acumatica", ("BULK-00", "00"): "acumatica"}
    # Pairs stored under the asking source itself are not held.
    assert (MIRROR_ITEM, MIRROR_SITE) not in held
    # An empty pair list answers empty.
    assert item_sites_repo.held_by_others("odoo", []) == {}

    # 13. The production code never swaps, patches or rebinds
    #     psycopg2.connect (the counting above wraps it in the test
    #     only, the way the #980 proof does).
    rebind = re.compile(r"^\s*psycopg2\.connect\s*=(?!=)", re.MULTILINE)
    for path in sorted(Path(os.path.join(_BACKEND_ROOT, "app")).rglob("*.py")):
        assert not rebind.search(path.read_text(encoding="utf-8")), path.name

    # 14. Concurrent syncs from different ERPs for the same pair: the
    #     holder's source_system survives the race, and a later sync
    #     from the other ERP still cannot overwrite it.
    assert _seed_item_site("ITEM-F", "9", HELD_COST, "acumatica") == {
        "inserted": 1,
        "updated": 0,
    }
    barrier = threading.Barrier(2)
    odoo_errors: list[BaseException] = []
    acumatica_errors: list[BaseException] = []

    def odoo_race():
        try:
            barrier.wait()
            offer = {
                "item_sites": [
                    _odoo_item_site("ITEM-F", "9", OFFERED_COST),
                    _odoo_item_site(OTHER_ITEM, HELD_SITE, 7.0),
                ],
                "on_hand": [_odoo_on_hand("ITEM-F", "9", 1.0)],
            }
            run_odoo_sync(client=_StubOdooClient(offer))
        except BaseException as exc:  # pragma: no cover - reported below
            odoo_errors.append(exc)

    def acumatica_race():
        try:
            barrier.wait()
            offer = {
                "InventoryItem": [
                    {"InventoryID": 1, "InventoryCD": "ITEM-F", "StkItem": True}
                ],
                "INItemSite": [
                    {"InventoryID": 1, "SiteID": 9, "AvgCost": 11.0},
                ],
                "INSiteStatus": [
                    {"InventoryID": 1, "SiteID": 9, "QtyOnHand": 6.0},
                ],
            }
            double, _ = _acumatica_double(offer)
            run_acumatica_sync(iter_entities=double, as_of=AS_OF)
        except BaseException as exc:  # pragma: no cover - reported below
            acumatica_errors.append(exc)

    threads = [
        threading.Thread(target=odoo_race),
        threading.Thread(target=acumatica_race),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert odoo_errors == [] and acumatica_errors == []
    # The holder keeps its source_system; its own sync updated its own
    # row's data, and the odoo sync refused.
    assert _item_site("ITEM-F", "9") == (11.0, "acumatica")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT source_system FROM on_hand "
                "WHERE item_code = 'ITEM-F' AND site_code = '9'"
            )
            on_hand_source = cur.fetchone()[0]
    finally:
        conn.close()
    assert on_hand_source == "acumatica"
    row = _clash_for("ITEM-F", "9", "odoo")
    assert row is not None
    assert row[2] == "acumatica"
    assert row[6] is None
    # A later sync from the other ERP still cannot overwrite the held
    # source_system.
    later = run_odoo_sync(
        client=_StubOdooClient(
            {"item_sites": [_odoo_item_site("ITEM-F", "9", OFFERED_COST)]}
        )
    )
    assert later["clashes"] == 1
    assert _item_site("ITEM-F", "9") == (11.0, "acumatica")

    # 15. The downgrade drops only the new table and the upgrade back
    #     re-creates it cleanly.
    cfg = _alembic_config()
    alembic_command.downgrade(cfg, _PREVIOUS_REVISION)
    assert _alembic_revision() == _PREVIOUS_REVISION
    assert not _table_exists("erp_code_clashes")
    alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_exists("erp_code_clashes")
    # The repository still works on the re-created table.
    stored = clashes_repo.record_clash("ITEM-G", "1", "acumatica", "odoo")
    assert stored["item_code"] == "ITEM-G"
    assert stored["resolved_at"] is None
    assert clashes_repo.resolve_unoffered("odoo", set()) == 1
