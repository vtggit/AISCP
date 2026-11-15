"""Issue #871 proof: list_latest is the newest sync of each source
system.

Against the test database (every date a fixed past calendar date; the
syncs are 2026-10-30 at 06:00/07:00/07:01 UTC and 2026-10-29, never
today), seeded through upsert_many the way
test_issue105_freeform does:

  * an empty table returns [] from list_latest(),
  * two sources: purchase lines "A-1" and "A-2" from source
    "acumatica" at 07:00 and "O-1" from source "odoo" at 07:01 on the
    same day — list_latest() returns all three,
  * an older row within a source is dropped: an "acumatica" row
    "A-OLD" with an as_of a day earlier is not returned while "A-1",
    "A-2" and "O-1" are,
  * one source: with only "acumatica" rows at two as_of values only
    the newer value's rows are returned, as today,
  * the same three checks hold for
    OpenSalesLinePostgresRepository.list_latest,
  * on-order follows: item X at site S is seeded with demand, lead
    times, an item-site and on-hand the way test_issue645_freeform
    seeds them; an "acumatica" open line of 40 units for X/S at 07:00
    and an "odoo" open line for another item at 07:01; GET
    /api/recommendations then reports X/S on_order_units 40.
"""

from datetime import date, timedelta

from app.db.connection import get_connection
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.open_sales_lines_postgres_repository import (
    OpenSalesLinePostgresRepository,
)

ACUMATICA_AS_OF = "2026-10-30T07:00:00+00"
ODOO_AS_OF = "2026-10-30T07:01:00+00"
OLDER_AS_OF = "2026-10-29T07:00:00+00"
MORNING_AS_OF = "2026-10-30T06:00:00+00"

EXPECTED_PO_KEYS = {
    "id",
    "business_key",
    "item_code",
    "site_code",
    "supplier_code",
    "order_type",
    "open_units",
    "promised_on",
    "ordered_on",
    "as_of",
    "source_system",
    "synced_at",
}

EXPECTED_SO_KEYS = {
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

X = "I871-X"
S = "S871"
REC_AS_OF = date(2026, 10, 30)
FIRST_DAY = REC_AS_OF - timedelta(days=359)


def _po_row(
    business_key, item_code, site_code, open_units, as_of, source_system, **overrides
):
    """A purchase-line sync row; overrides fill the nullable columns."""
    row = {
        "business_key": business_key,
        "item_code": item_code,
        "site_code": site_code,
        "supplier_code": None,
        "order_type": "PO",
        "open_units": open_units,
        "promised_on": None,
        "ordered_on": None,
        "as_of": as_of,
        "source_system": source_system,
    }
    row.update(overrides)
    return row


def _so_row(
    business_key, item_code, site_code, open_units, as_of, source_system, **overrides
):
    """A sales-line sync row; overrides fill the nullable columns."""
    row = {
        "business_key": business_key,
        "item_code": item_code,
        "site_code": site_code,
        "order_type": "SO",
        "open_units": open_units,
        "ship_on": None,
        "as_of": as_of,
        "source_system": source_system,
    }
    row.update(overrides)
    return row


def _clear(statement: str) -> None:
    """Run one fixed DELETE statement against the test database."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _seed_recommendation_inputs(cur) -> None:
    """Item X at site S the way test_issue645_freeform seeds A1:
    10.0 a day for 360 days ending on the as-of, three 5-day lead
    times, an item-site with unit cost 2.0, reorder point 120.0,
    safety stock 5.0, and 30 on hand."""
    cur.executemany(
        "INSERT INTO demand_events (id, business_key, item_code, "
        "site_code, quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                f"id-de-871-{i}",
                f"de-871-{i}",
                X,
                S,
                10.0,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(360)
        ],
    )
    for i in range(3):
        ordered = date(2026, 9, 20) + timedelta(days=i)
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                f"id-lt-871-{i}",
                f"lt-871-{i}",
                "SUP-871",
                X,
                ordered.isoformat(),
                (ordered + timedelta(days=5)).isoformat(),
                "test",
            ),
        )
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, max_qty, order_qty, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
        ("id-is-871", X, S, 2.0, 120.0, 5.0, 400.0, 50.0, "test"),
    )
    cur.execute(
        "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
        "as_of, source_system) VALUES (%s, %s, %s, %s, %s, %s)",
        ("id-oh-871", X, S, 30.0, "2026-10-30T00:00:00+00:00", "test"),
    )


def test_issue871_freeform(client):
    repo = OpenPurchaseLinePostgresRepository()

    # 1. An empty table returns [].
    assert repo.list_latest() == []

    # 2. Two sources on the same day: every source's newest rows come
    #    back, even though "odoo"'s sync is a minute newer than
    #    "acumatica"'s.
    assert repo.upsert_many(
        [
            _po_row("A-1", "ITEM-A", "SITE-A", 12.0, ACUMATICA_AS_OF, "acumatica"),
            _po_row("A-2", "ITEM-A", "SITE-A", 5.0, ACUMATICA_AS_OF, "acumatica"),
            _po_row("O-1", "ITEM-O", "SITE-O", 9.0, ODOO_AS_OF, "odoo"),
        ]
    ) == {"inserted": 3, "updated": 0}

    latest = repo.list_latest()
    assert [r["business_key"] for r in latest] == ["A-1", "A-2", "O-1"]
    assert all(set(r) == EXPECTED_PO_KEYS for r in latest)
    assert all(r["as_of"].startswith("2026-10-30") for r in latest)
    o1 = next(r for r in latest if r["business_key"] == "O-1")
    assert o1["source_system"] == "odoo"

    # 3. An older row within a source is dropped: A-OLD's as_of is a
    #    day earlier than acumatica's newest sync, so it is not the
    #    newest sync of its source and does not come back, while the
    #    newest rows of both sources remain.
    assert repo.upsert_many(
        [
            _po_row("A-OLD", "ITEM-OLD", "SITE-A", 3.0, OLDER_AS_OF, "acumatica"),
        ]
    ) == {"inserted": 1, "updated": 0}

    latest = repo.list_latest()
    keys = {r["business_key"] for r in latest}
    assert "A-OLD" not in keys
    assert keys == {"A-1", "A-2", "O-1"}

    # 4. One source: only the newer as_of's rows are returned, as
    #    today.
    _clear("DELETE FROM open_purchase_lines")
    assert repo.upsert_many(
        [
            _po_row("P-OLD", "ITEM-P", "SITE-P", 1.0, MORNING_AS_OF, "acumatica"),
            _po_row("P-1", "ITEM-P", "SITE-P", 2.0, ACUMATICA_AS_OF, "acumatica"),
            _po_row("P-2", "ITEM-P", "SITE-P", 3.0, ACUMATICA_AS_OF, "acumatica"),
        ]
    ) == {"inserted": 3, "updated": 0}

    latest = repo.list_latest()
    assert [r["business_key"] for r in latest] == ["P-1", "P-2"]
    assert all(r["as_of"].startswith("2026-10-30T07:00:00") for r in latest)

    # --- sales lines: the same three checks ------------------------------
    sales = OpenSalesLinePostgresRepository()

    # 5. An empty table returns [].
    assert sales.list_latest() == []

    # 6. Two sources on the same day.
    assert sales.upsert_many(
        [
            _so_row("SA-1", "ITEM-SA", "SITE-SA", 4.0, ACUMATICA_AS_OF, "acumatica"),
            _so_row("SA-2", "ITEM-SA", "SITE-SA", 6.0, ACUMATICA_AS_OF, "acumatica"),
            _so_row("SO-1", "ITEM-SO", "SITE-SO", 8.0, ODOO_AS_OF, "odoo"),
        ]
    ) == {"inserted": 3, "updated": 0}

    latest = sales.list_latest()
    assert [r["business_key"] for r in latest] == ["SA-1", "SA-2", "SO-1"]
    assert all(set(r) == EXPECTED_SO_KEYS for r in latest)

    # 7. An older row within a source is dropped.
    assert sales.upsert_many(
        [
            _so_row("SA-OLD", "ITEM-SAOLD", "SITE-SA", 2.0, OLDER_AS_OF, "acumatica"),
        ]
    ) == {"inserted": 1, "updated": 0}

    keys = {r["business_key"] for r in sales.list_latest()}
    assert "SA-OLD" not in keys
    assert keys == {"SA-1", "SA-2", "SO-1"}

    # 8. One source: only the newer as_of's rows are returned.
    _clear("DELETE FROM open_sales_lines")
    assert sales.upsert_many(
        [
            _so_row("SP-OLD", "ITEM-SP", "SITE-SP", 1.0, MORNING_AS_OF, "acumatica"),
            _so_row("SP-1", "ITEM-SP", "SITE-SP", 2.0, ACUMATICA_AS_OF, "acumatica"),
            _so_row("SP-2", "ITEM-SP", "SITE-SP", 3.0, ACUMATICA_AS_OF, "acumatica"),
        ]
    ) == {"inserted": 3, "updated": 0}

    latest = sales.list_latest()
    assert [r["business_key"] for r in latest] == ["SP-1", "SP-2"]

    # --- on-order follows --------------------------------------------------
    _clear("DELETE FROM open_purchase_lines")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed_recommendation_inputs(cur)
        conn.commit()
    finally:
        conn.close()

    # The 40-unit "acumatica" open line for X/S at 07:00 and the
    # "odoo" open line for another item at 07:01: under the newest
    # sync of each source system, the acumatica line is still the
    # newest acumatica line, so it counts as on-order supply.
    assert repo.upsert_many(
        [
            _po_row(
                "OL-871-X",
                X,
                S,
                40.0,
                ACUMATICA_AS_OF,
                "acumatica",
                supplier_code="SUP-871",
            ),
            _po_row("OL-871-Y", "I871-Y", S, 7.0, ODOO_AS_OF, "odoo"),
        ]
    ) == {"inserted": 2, "updated": 0}

    response = client.get(
        "/api/recommendations", params={"as_of": REC_AS_OF.isoformat()}
    )
    assert response.status_code == 200, response.text
    rows = {(row["item_code"], row["site_code"]): row for row in response.json()}
    assert rows[(X, S)]["on_order_units"] == 40
