"""Issue #617 proof: the suppliers read model.

Loads the captured BAccount feed sample from
``tests/fixtures/aidw_baccount_sample.json`` (never restating it) and
proves, against it:

* the mapping: ``_map_suppliers`` on the sample's ``value`` rows keeps
  exactly the three "VE" rows, trims the padded ``AcctCD`` ("AAVENDOR "
  becomes ``vendor_code`` "AAVENDOR"), renders ``BAccountID`` 6995 as
``supplier_code`` "6995", and counts the "CU" and "EP" rows as
  ``not_vendor`` 2;
* the sync: the default ``run_sync`` call (the fake feed reader, serving
  the sample for ``BAccount`` read after the six other sets, which serve
  empty lists, is installed as the configured AIDW feed reader) stores
  exactly the three vendors in the suppliers read model without any
  opt-in, and a second run with one name changed updates it;
* the missing set: an AIDW feed reader that raises ``AidwFeedError``
  for ``BAccount`` still completes, writes the other read models, and
  reports "BAccount not available" under "skipped" without writing
  suppliers;
* the route: ``GET /api/suppliers`` gives 401 without a token and 200
  with the planner token, listing the three vendors ordered by
  ``vendor_code`` as ``supplier_code``, ``vendor_code``, ``name`` and
  ``status``;
* the migration: 0023_suppliers upgrades and downgrades cleanly.
"""

import copy
import json
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
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)
from app.sync.acumatica import _map_suppliers, run_sync
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FIXTURE = os.path.join(
    os.path.dirname(__file__), "fixtures", "aidw_baccount_sample.json"
)

AS_OF = datetime(2026, 10, 24, 0, 0, 0, tzinfo=UTC)

_SIX_ENTITY_SETS = (
    "InventoryItem",
    "INItemSite",
    "INSiteStatus",
    "INTran",
    "POLine",
    "POReceiptLine",
)


def _load_baccount_value() -> list:
    """The captured BAccount sample's value rows, loaded from disk."""
    with open(_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)["value"]


def _feed_reader(baccount_value):
    """A fake iter_entities: the sample for BAccount, empty lists otherwise.

    Records the read order so the test can prove BAccount is read after
    the six other entity sets.
    """
    read_order = []

    def iter_entities(entity_set, *args, **kwargs):
        read_order.append(entity_set)
        if entity_set == "BAccount":
            return iter(list(baccount_value))
        return iter(())

    return iter_entities, read_order


class _UpsertRecorder:
    """Stands in for a repository: records rows instead of writing them."""

    def __init__(self):
        self.calls = []

    def upsert_many(self, rows):
        self.calls.append(rows)
        return {"inserted": len(rows), "updated": 0}


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


def test_issue617_freeform(client, monkeypatch):
    repo = SupplierPostgresRepository()
    value = _load_baccount_value()

    # 1. The mapping: exactly the three "VE" rows of the sample are kept,
    #    "AAVENDOR " is trimmed to "AAVENDOR", BAccountID 6995 becomes
    #    supplier_code "6995", and the "CU" and "EP" rows are counted as
    #    not_vendor 2.
    rows, skips = _map_suppliers(value)
    assert skips == {"not_vendor": 2}
    assert len(rows) == 3

    ve_ids = {str(row["BAccountID"]) for row in value if row["Type"] == "VE"}
    non_vendor_ids = {str(row["BAccountID"]) for row in value if row["Type"] != "VE"}
    by_id = {row["supplier_code"]: row for row in rows}
    assert set(by_id) == ve_ids
    assert not set(by_id) & non_vendor_ids

    assert by_id["6995"]["vendor_code"] == "AAVENDOR"
    raw_by_id = {str(row["BAccountID"]): row for row in value}
    for code, mapped in by_id.items():
        raw = raw_by_id[code]
        assert mapped["vendor_code"] == raw["AcctCD"].strip()
        assert mapped["name"] == raw["AcctName"].strip()
        assert mapped["status"] == raw["Status"]
        assert mapped["source_system"] == "acumatica"

    # Hostile BAccount rows never raise: non-dict rows are malformed, a
    # VE row without a usable BAccountID is skipped and counted, and a
    # VE row without AcctCD/AcctName/Status maps with empty values.
    hostile_rows, hostile_skips = _map_suppliers(
        [
            None,
            "junk",
            5,
            {"Type": "VE"},
            {"Type": "VE", "BAccountID": "not-an-int"},
            {"Type": "VE", "BAccountID": True},
            {"Type": "VC", "BAccountID": 1},
            {"BAccountID": 2},
        ]
    )
    assert hostile_rows == []
    assert hostile_skips == {
        "malformed_row": 3,
        "missing_supplier_code": 3,
        "not_vendor": 2,
    }
    sparse, sparse_skips = _map_suppliers([{"Type": "VE", "BAccountID": 99}])
    assert sparse_skips == {}
    assert sparse == [
        {
            "supplier_code": "99",
            "vendor_code": None,
            "name": None,
            "status": None,
            "source_system": "acumatica",
        }
    ]

    # 2. The sync with a fake iter_entities: the sample is served for
    #    BAccount (read after the six other sets, which serve empty
    #    lists).  The fake is installed as the configured AIDW feed
    #    reader, so this is the production-shaped default run_sync
    #    call: no opt-in flag, BAccount is read after the six reads,
    #    run_sync stores exactly the three vendors, and the result
    #    holds the suppliers repository counts.
    fake_iter_entities, read_order = _feed_reader(value)
    monkeypatch.setattr(aidw_client, "iter_entities", fake_iter_entities)
    result = run_sync(as_of=AS_OF)
    assert read_order == [*_SIX_ENTITY_SETS, "BAccount", "SOLine", "POVendorInventory"]
    # The default sync populated the suppliers read model: the result
    # carries the suppliers repository counts.
    assert result["suppliers"] == {"inserted": 3, "updated": 0}
    assert result["skipped"]["not_vendor"] == 2
    for model in (
        "item_sites",
        "demand_events",
        "lead_time_observations",
        "on_hand",
        "open_purchase_lines",
    ):
        assert result[model] == {"inserted": 0, "updated": 0}

    stored = {row["supplier_code"]: row for row in repo.list_all()}
    assert set(stored) == ve_ids
    assert stored["6995"]["vendor_code"] == "AAVENDOR"
    assert stored["6995"]["name"] == raw_by_id["6995"]["AcctName"].strip()

    # A second run, with one name changed, updates it.
    renamed_value = copy.deepcopy(value)
    for row in renamed_value:
        if row["BAccountID"] == 6995:
            row["AcctName"] = row["AcctName"] + " (renamed)"
    renamed_reader, _ = _feed_reader(renamed_value)
    monkeypatch.setattr(aidw_client, "iter_entities", renamed_reader)
    renamed_result = run_sync(as_of=AS_OF)
    assert renamed_result["suppliers"] == {"inserted": 0, "updated": 3}
    stored = {row["supplier_code"]: row for row in repo.list_all()}
    assert set(stored) == ve_ids
    assert (
        stored["6995"]["name"] == raw_by_id["6995"]["AcctName"].strip() + " (renamed)"
    )

    # 3. The route: 401 without a token, 200 with the planner token,
    #    listing the three vendors ordered by vendor_code.
    with TestClient(app) as unauthenticated:
        assert unauthenticated.get("/api/suppliers").status_code == 401

    response = client.get("/api/suppliers")
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 3
    expected = sorted(
        (row for row in stored.values()), key=lambda row: row["vendor_code"]
    )
    for field in ("supplier_code", "vendor_code", "name", "status"):
        assert [item[field] for item in items] == [row[field] for row in expected]

    # 4. Missing set: an iter_entities that raises AidwFeedError for
    #    BAccount still completes, writes the other read models, and
    #    reports "BAccount not available" without writing suppliers.
    item_sites_recorder = _UpsertRecorder()
    demand_events_recorder = _UpsertRecorder()
    lead_time_recorder = _UpsertRecorder()
    on_hand_recorder = _UpsertRecorder()
    open_po_recorder = _UpsertRecorder()
    missing_read_order = []

    def missing_iter_entities(entity_set, *args, **kwargs):
        missing_read_order.append(entity_set)
        if entity_set == "BAccount":
            raise AidwFeedError("entity set 'BAccount' returned HTTP 404")
        if entity_set == "InventoryItem":
            return iter([{"InventoryID": 1, "InventoryCD": "W-617", "StkItem": True}])
        if entity_set == "INItemSite":
            return iter([{"InventoryID": 1, "SiteID": 9}])
        return iter(())

    monkeypatch.setattr(aidw_client, "iter_entities", missing_iter_entities)
    missing_result = run_sync(
        as_of=AS_OF,
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
    assert missing_result["skipped"]["suppliers"] == "BAccount not available"
    assert "suppliers" not in missing_result
    assert missing_result["item_sites"] == {"inserted": 1, "updated": 0}
    assert item_sites_recorder.calls == [
        [
            {
                "item_code": "W-617",
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
    # No suppliers were written by the missing-set sync: the stored
    # suppliers are still exactly the three vendors from the sample.
    assert {row["supplier_code"] for row in repo.list_all()} == ve_ids

    # 5. The migration: 0023_suppliers is the head, downgrades to 0022
    #    by dropping the table and upgrades back cleanly.
    cfg = _alembic_config()
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert heads[0] == CURRENT_HEAD
    assert _alembic_revision() == CURRENT_HEAD

    alembic_command.downgrade(cfg, "0022_lead_time_promised_on")
    assert _alembic_revision() == "0022_lead_time_promised_on"
    assert not _table_exists("suppliers")

    alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_exists("suppliers")
