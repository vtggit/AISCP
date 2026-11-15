"""Issue #1056 proof: the lead-time basis of the safety stock.

One item-site (I1056 at SITE-1, as-of 2026-09-30) with steady demand
(360 days of 2.0 a day, smooth) and lead-time observations from two
suppliers, seeded the way test_issue365_freeform seeds lead times:
``S-MAIN``, the main supplier, with 8 receipts whose lead times vary
widely (5 to 30 days), and ``S-SIDE`` with 3 steady receipts (10
days each).  The item-site is read through the recommendations list
(``GET /api/recommendations``) and the single item-site route
(``GET /api/recommendations/by-key``).

* Default — with ``replenishment.lead_time_basis`` unset, ``inputs.lt``
  and ``inputs.sd_lt`` equal the pooled ``mean_days`` and ``std_days``
  of ``lead_time_stats_for_item``, on the list row and the single
  item-site answer alike.
* Main supplier — with the setting stored as ``main_supplier``, they
  equal ``S-MAIN``'s entry of the item's per-supplier breakdown, and
  the reorder point is higher than with the default.
* Fallback — with both suppliers cut to 2 receipts each, so the main
  supplier has fewer than 3 used observations, the pooled figures
  come back under ``main_supplier``.
* Agreement — in each case the list row and the single item-site
  answer carry the same ``inputs.lt``, ``inputs.sd_lt`` and reorder
  point.
"""

from datetime import date, timedelta

import psycopg2
import pytest

from app.db.connection import get_connection
from app.stats.lead_time import lead_time_stats_for_item

AS_OF = date(2026, 9, 30)
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
ITEM = "I1056"
SITE = "SITE-1"
KEY = "replenishment.lead_time_basis"
MAIN = "S-MAIN"
SIDE = "S-SIDE"
RECEIVED = "2026-08-30"


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return AS_OF + timedelta(days=2)

    return _FixedDate


def _demand(cur):
    """360 days of 2.0 units a day — steady, smooth demand."""
    cur.executemany(
        "INSERT INTO demand_events (id, business_key, item_code, site_code, "
        "quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                f"id-de-1056-{i}",
                f"de-1056-{i}",
                ITEM,
                SITE,
                2.0,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(360)
        ],
    )


def _item_site(cur):
    """The item-site's current planning settings."""
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, source_system) "
        "VALUES (%s, %s, %s, 1.0, 25.0, 10.0, 'test')",
        ("id-is-1056", ITEM, SITE),
    )


def _lead_time(cur, supplier, lead_days, seq):
    """One receipt of ``lead_days`` days for the item from ``supplier``."""
    ordered = date.fromisoformat(RECEIVED) - timedelta(days=lead_days)
    cur.execute(
        "INSERT INTO lead_time_observations (id, business_key, "
        "supplier_code, item_code, ordered_on, received_on, "
        "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            f"id-lt-1056-{seq}",
            f"lt-1056-{seq}",
            supplier,
            ITEM,
            ordered.isoformat(),
            RECEIVED,
            "test",
        ),
    )


def _seed_lead_times(cur, supplier_receipts):
    """The item's receipts: ``supplier_receipts`` a list of
    (supplier, lead days) pairs, in receipt order."""
    for seq, (supplier, lead_days) in enumerate(supplier_receipts):
        _lead_time(cur, supplier, lead_days, seq)


def _rows(client):
    """The item-site's recommendation from the list and the single
    item-site route, at the as-of."""
    listed = client.get("/api/recommendations", params={"as_of": AS_OF.isoformat()})
    assert listed.status_code == 200, listed.text
    listed_rows = [
        row
        for row in listed.json()
        if row["item_code"] == ITEM and row["site_code"] == SITE
    ]
    assert len(listed_rows) == 1, listed_rows
    single = client.get(
        "/api/recommendations/by-key",
        params={"item_code": ITEM, "site_code": SITE, "as_of": AS_OF.isoformat()},
    )
    assert single.status_code == 200, single.text
    return listed_rows[0], single.json()


def _pooled_stats():
    """The item's pooled lead-time statistics and its breakdown."""
    stats = lead_time_stats_for_item(ITEM, AS_OF)
    assert stats is not None, "the item has lead-time observations"
    return stats


def _entry(stats, supplier_code):
    entry = next(
        (e for e in stats["suppliers"] if e["supplier_code"] == supplier_code),
        None,
    )
    assert entry is not None, (supplier_code, stats["suppliers"])
    return entry


def test_issue1056_freeform(client, monkeypatch, admin_headers):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    # --- seed: steady demand, 8 wide receipts from S-MAIN, 3 from
    # --- S-SIDE, and the item-site's planning settings ---------------
    main_receipts = [5, 8, 10, 12, 15, 20, 25, 30]
    side_receipts = [10, 10, 10]
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand(cur)
            _item_site(cur)
            _seed_lead_times(
                cur,
                [(MAIN, days) for days in main_receipts]
                + [(SIDE, days) for days in side_receipts],
            )
        conn.commit()
    finally:
        conn.close()

    # --- Default: the setting unset, the pooled figures --------------
    default_list, default_single = _rows(client)
    stats = _pooled_stats()
    assert stats["n_observations"] == 11
    assert default_list["status"] == "ok"
    assert default_list["demand_class"] == "smooth"
    assert default_list["inputs"]["lt"] == pytest.approx(stats["mean_days"])
    assert default_list["inputs"]["sd_lt"] == pytest.approx(stats["std_days"])
    assert default_single["inputs"]["lt"] == pytest.approx(stats["mean_days"])
    assert default_single["inputs"]["sd_lt"] == pytest.approx(stats["std_days"])
    assert default_list["reorder_point"] == pytest.approx(
        default_single["reorder_point"]
    )
    assert default_list["inputs"]["lt"] == default_single["inputs"]["lt"]
    assert default_list["inputs"]["sd_lt"] == default_single["inputs"]["sd_lt"]

    # --- Main supplier: the setting stored as main_supplier ----------
    response = client.put(
        f"/api/settings/{KEY}", json={"value": "main_supplier"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text

    main_entry = _entry(_pooled_stats(), MAIN)
    assert main_entry["n_observations"] == 8
    main_list, main_single = _rows(client)
    assert main_list["status"] == "ok"
    assert main_list["inputs"]["lt"] == pytest.approx(main_entry["mean_days"])
    assert main_list["inputs"]["sd_lt"] == pytest.approx(main_entry["std_days"])
    assert main_single["inputs"]["lt"] == pytest.approx(main_entry["mean_days"])
    assert main_single["inputs"]["sd_lt"] == pytest.approx(main_entry["std_days"])
    assert main_list["reorder_point"] == pytest.approx(main_single["reorder_point"])
    # S-MAIN's lead times are longer and wider than the pooled ones, so
    # the safety stock and the reorder point stand higher on them.
    assert main_list["reorder_point"] > default_list["reorder_point"]
    assert main_single["reorder_point"] > default_single["reorder_point"]

    # --- Fallback: both suppliers cut to 2 receipts each ------------
    # The main supplier has fewer than 3 used observations, so the
    # pooled figures come back under main_supplier.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM lead_time_observations WHERE item_code = %s",
                (ITEM,),
            )
            _seed_lead_times(
                cur,
                [(MAIN, 10), (MAIN, 30), (SIDE, 10), (SIDE, 10)],
            )
        conn.commit()
    finally:
        conn.close()

    fallback_list, fallback_single = _rows(client)
    fallback_stats = _pooled_stats()
    assert fallback_stats["n_observations"] == 4
    assert _entry(fallback_stats, MAIN)["n_observations"] == 2
    assert fallback_list["status"] == "ok"
    assert fallback_list["inputs"]["lt"] == pytest.approx(fallback_stats["mean_days"])
    assert fallback_list["inputs"]["sd_lt"] == pytest.approx(fallback_stats["std_days"])
    assert fallback_single["inputs"]["lt"] == pytest.approx(fallback_stats["mean_days"])
    assert fallback_single["inputs"]["sd_lt"] == pytest.approx(
        fallback_stats["std_days"]
    )
    assert fallback_list["reorder_point"] == pytest.approx(
        fallback_single["reorder_point"]
    )
    assert fallback_list["inputs"]["lt"] == fallback_single["inputs"]["lt"]
    assert fallback_list["inputs"]["sd_lt"] == fallback_single["inputs"]["sd_lt"]
