"""Issue #603 proof: the promised date travels with each lead-time observation.

Proved here:

  * **Promised date present** — ``_map_lead_time_observations`` on a
    POReceiptLine joined to its POLine whose ``PromisedDate`` is
    "2026-06-10T00:00:00Z" (order date "2026-06-01T00:00:00Z", receipt
    date "2026-06-12T00:00:00Z") maps ``promised_on`` to
    ``date(2026, 6, 10)``, with every other field exactly as before;
  * **Promised date missing** — the same receipt whose POLine carries no
    ``PromisedDate``, or an unparsable one, maps ``promised_on`` to
    ``None`` and the row is still kept (nothing new is skipped);
  * **Stored and returned** — through
    ``LeadTimeObservationPostgresRepository`` against the test database,
    an upsert stores ``promised_on``, a second upsert with a different
    ``promised_on`` updates it, and ``GET /api/lead-time-observations``
    returns it;
  * **Migration** — ``0022_lead_time_promised_on`` (revision after
    ``0021_plan_releases``) is the head and adds a nullable date
    ``promised_on`` to ``lead_time_observations``; its downgrade drops
    the column and the upgrade back to head re-adds it cleanly.
"""

import os
from datetime import date

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.db.connection import get_connection
from app.repositories.lead_time_observations_postgres_repository import (
    LeadTimeObservationPostgresRepository,
)
from app.sync.acumatica import _map_lead_time_observations
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PREV_HEAD = "0021_plan_releases"
HEAD = "0022_lead_time_promised_on"

INVENTORY_ROWS = [
    {"InventoryID": 1, "InventoryCD": "WIDGET-1", "StkItem": True},
]

RECEIPT_ROWS = [
    {
        "InventoryID": 1,
        "SiteID": 4,
        "InvtMult": 1,
        "POType": "P",
        "PONbr": "PO-603",
        "POLineNbr": 1,
        "ReceiptType": "R",
        "ReceiptNbr": "RCPT-603",
        "LineNbr": 1,
        "VendorID": "V-9",
        "ReceiptDate": "2026-06-12T00:00:00Z",
    },
]

ORDER_DATE = "2026-06-01T00:00:00Z"
PROMISED_DATE = "2026-06-10T00:00:00Z"

EXPECTED_ROW = {
    "business_key": "R:RCPT-603:1",
    "supplier_code": "V-9",
    "item_code": "WIDGET-1",
    "site_code": "4",
    "ordered_on": date(2026, 6, 1),
    "received_on": date(2026, 6, 12),
    "promised_on": date(2026, 6, 10),
    "source_system": "acumatica",
}

# Codes unique to this test, so the global list endpoint can be filtered
# down to exactly this row no matter what other observations the shared
# test database happens to hold.
DB_SUPPLIER_CODE = "V-603"
DB_ITEM_CODE = "WIDGET-603"


def _po_lines(promised):
    line = {
        "OrderType": "P",
        "OrderNbr": "PO-603",
        "LineNbr": 1,
        "OrderDate": ORDER_DATE,
    }
    if promised is not None:
        line["PromisedDate"] = promised
    return [line]


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


def _observation_columns() -> dict:
    """lead_time_observations columns as (data_type, is_nullable)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'lead_time_observations'"
            )
            return {r[0]: (r[1], r[2]) for r in cur.fetchall()}
    finally:
        conn.close()


def _ensure_migrated_table() -> None:
    """Rebuild lead_time_observations through the real migrations when an
    earlier test in the session left it in a pre-0022 shape.

    test_issue319 drops the table and re-creates it from a 0004-era
    snapshot while the database stays at head, so a later test can find
    the column missing (or the table gone after a partial downgrade).
    The downgrade to 0003 plus the upgrade back to head rebuilds the
    table exactly as the migrations define it; with the column present
    this is a no-op.
    """
    if "promised_on" in _observation_columns():
        return
    cfg = _alembic_config()
    alembic_command.downgrade(cfg, "0003_demand_events")
    alembic_command.upgrade(cfg, "head")


def test_issue603_freeform(client):
    _ensure_migrated_table()

    # -- Promised date present: mapped, every other field unchanged. -----
    rows, skips = _map_lead_time_observations(
        RECEIPT_ROWS, _po_lines(PROMISED_DATE), INVENTORY_ROWS
    )
    assert rows == [EXPECTED_ROW]
    assert skips == {}

    # -- Promised date missing: None, and the row is still kept. --------
    for promised in (None, "not-a-date"):
        rows, skips = _map_lead_time_observations(
            RECEIPT_ROWS, _po_lines(promised), INVENTORY_ROWS
        )
        assert len(rows) == 1, promised
        row = dict(rows[0])
        assert row["promised_on"] is None, promised
        row.pop("promised_on")
        expected = dict(EXPECTED_ROW)
        expected.pop("promised_on")
        assert row == expected, promised
        assert skips == {}

    # -- Stored and returned: upsert, update, GET. ----------------------
    repo = LeadTimeObservationPostgresRepository()
    base = {
        "business_key": EXPECTED_ROW["business_key"],
        "supplier_code": DB_SUPPLIER_CODE,
        "item_code": DB_ITEM_CODE,
        "site_code": EXPECTED_ROW["site_code"],
        "ordered_on": EXPECTED_ROW["ordered_on"],
        "received_on": EXPECTED_ROW["received_on"],
        "source_system": EXPECTED_ROW["source_system"],
    }
    assert repo.upsert_many([dict(base, promised_on=date(2026, 6, 10))]) == {
        "inserted": 1,
        "updated": 0,
    }
    assert repo.upsert_many([dict(base, promised_on=date(2026, 7, 4))]) == {
        "inserted": 0,
        "updated": 1,
    }
    stored = repo.get_by_business_key(base["business_key"])
    assert stored is not None
    assert stored["promised_on"] == date(2026, 7, 4)

    response = client.get(
        "/api/lead-time-observations",
        params={
            "supplier_code": base["supplier_code"],
            "item_code": base["item_code"],
        },
    )
    assert response.status_code == 200
    body = response.json()
    # The global endpoint lists every observation in the database, so do
    # not assume it returns a single row: pick this test's row out of the
    # filtered results by its unique business_key.
    api_rows = [row for row in body if row["business_key"] == base["business_key"]]
    assert len(api_rows) == 1
    api_row = api_rows[0]
    assert api_row["business_key"] == base["business_key"]
    assert api_row["promised_on"] == "2026-07-04"
    assert api_row["ordered_on"] == "2026-06-01"
    assert api_row["received_on"] == "2026-06-12"
    assert api_row["lead_time_days"] == 11
    assert api_row["supplier_code"] == base["supplier_code"]
    assert api_row["item_code"] == base["item_code"]
    assert api_row["site_code"] == base["site_code"]
    assert api_row["source_system"] == base["source_system"]
    assert isinstance(api_row["id"], str) and api_row["id"]
    assert isinstance(api_row["synced_at"], str) and api_row["synced_at"]

    # -- The migration upgrades and downgrades cleanly. -----------------
    cfg = _alembic_config()
    assert _alembic_revision() == CURRENT_HEAD
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert list(heads) == [CURRENT_HEAD]
    columns = _observation_columns()
    assert columns["promised_on"] == ("date", "YES")
    try:
        alembic_command.downgrade(cfg, PREV_HEAD)
        assert _alembic_revision() == PREV_HEAD
        columns = _observation_columns()
        # The downgrade drops exactly the promised_on column.
        assert "promised_on" not in columns
        assert "business_key" in columns  # the rest of the table stays
    finally:
        alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    columns = _observation_columns()
    assert columns["promised_on"] == ("date", "YES")
