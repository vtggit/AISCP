"""Issue #1039 proof: the cross-ERP comparison's move figure.

``GET /api/cross-erp/items`` answers one row per stored
``erp_code_mappings`` row of kind ``item``.  The row gains
``suggested_move_units``: null while ``opportunity`` is false, and
with the flag the smaller of two sums, rounded down to a whole
number and never below 0 — the short side's sites whose
``stockout_category`` is not null summed over their
``suggested_order_quantity`` (a null quantity counts as 0) and the
excess side's sites summed over their ``excess_units``.

Everything is exercised against the test database through the app's
test client with the development stub users, the way
``test_issue1022_freeform`` seeds its data: item-sites and
``on_hand`` through their repositories' ``upsert_many``, demand
events and lead-time observations through the database, and the one
stored item mapping through ``PUT /api/erp-mappings``.
``date.today`` is pinned (2026-10-02) and the demand ends on
2026-09-30, so the shared default as-of is 2026-09-30 whatever the
calendar says; the replenishment demand basis is the forecast (the
stored system value), so both item-sites keep their history figures
(2.0 a day).

The one mapping (``MOVE-1`` to ``MOVE-1``) holds the item at ``154``
under ``acumatica`` and at ``WH`` under ``odoo``; the on-hand rows
are re-stamped between scenarios, so the same pair plays them all:

  * capped by the excess — with ``WH`` at 0 on hand (suggesting
    ``27 - 0 = 27`` whole units, ``stockout_category`` not null) and
    ``154`` at 725.5 on hand (its excess ``725.5 - 720 = 5.5``
    units, the 720 demanded in the 360-day excess window),
    ``suggested_move_units`` is ``5`` — the excess side's excess
    units, rounded down;
  * capped by the need — with ``WH`` at 5 on hand (suggesting
    ``27 - 5 = 22``) and ``154`` at 1000 on hand (280 in excess),
    ``suggested_move_units`` is ``22`` — the short site's
    ``suggested_order_quantity``;
  * no opportunity — with both sides at 30 on hand (no risk, no
    excess), ``opportunity`` is false and ``suggested_move_units``
    is null;
  * whole units — while the flag is true the value is a JSON integer
    (not a float).

The figures: d 2.0, sd_d 0.0, LT 10 (pooled 8/10/12-day
observations), sd_LT 2.0: sigma = sqrt(10 * 0 + 2^2 * 2^2) = 4,
safety stock Z95 * 4, reorder point 20 + Z95 * 4 -> 27 whole units,
720 units demanded in the 360-day excess window (2.0 a day).
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository

ADMIN_TOKEN = "dev-secret-token:admin"

URL = "/api/cross-erp/items"
MAPPINGS_URL = "/api/erp-mappings"

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
# d 2.0, sd_d 0.0, LT 10 (pooled 8/10/12-day observations), sd_LT 2.0:
# sigma = sqrt(10 * 0 + 2^2 * 2^2) = 4, safety stock Z95 * 4, reorder
# point 20 + Z95 * 4 -> 27 whole units, 720 units demanded in the
# 360-day excess window (2.0 a day).
REORDER_POINT_UNITS = 27
EXCESS_WINDOW_UNITS = 720.0  # 2.0 a day over the 360-day window
ACU_SITE = "154"
ODO_SITE = "WH"


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


def _demand(cur, item, site, days=360, quantity=2.0):
    """``days`` of ``quantity`` units a day for one pair, ending on the
    as-of (the 360-day statistics window, 12 buckets of 60 each)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-1039-{item}-{site}-{i}",
                f"de-1039-{item}-{site}-{i}",
                item,
                site,
                quantity,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(days)
        ],
    )


def _lead_times(cur, item):
    """Three lead-time observations of 8, 10 and 12 days: mean 10,
    sample std 2, not low confidence."""
    for i, (ordered, received) in enumerate(
        (
            ("2026-08-22", "2026-08-30"),
            ("2026-08-20", "2026-08-30"),
            ("2026-08-18", "2026-08-30"),
        )
    ):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                f"id-lt-1039-{item}-{i}",
                f"lt-1039-{item}-{i}",
                f"SUP-1039-{item}",
                item,
                ordered,
                received,
                "test",
            ),
        )


def _on_hand(acu_units, odo_units):
    """Stamp the pair's on-hand rows (re-stampable: the same as_of and
    source systems re-apply the snapshot)."""
    OnHandPostgresRepository().upsert_many(
        [
            {
                "item_code": "MOVE-1",
                "site_code": ACU_SITE,
                "quantity_on_hand": acu_units,
                "as_of": AS_OF,
                "source_system": "acumatica",
            },
            {
                "item_code": "MOVE-1",
                "site_code": ODO_SITE,
                "quantity_on_hand": odo_units,
                "as_of": AS_OF,
                "source_system": "odoo",
            },
        ]
    )


def _row(admin):
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["as_of"] == AS_OF_STR
    rows = answer["rows"]
    assert [row["source_code"] for row in rows] == ["MOVE-1"]
    return rows[0]


def test_issue1039_freeform(client, monkeypatch):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    monkeypatch.delenv("EXCESS_COVER_MONTHS", raising=False)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # -- Item-sites, demand and lead times (the way 1022 seeds). -------
    ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": "MOVE-1",
                "site_code": ACU_SITE,
                "unit_cost": 1.0,
                "source_system": "acumatica",
            },
            {
                "item_code": "MOVE-1",
                "site_code": ODO_SITE,
                "unit_cost": 1.0,
                "source_system": "odoo",
            },
        ]
    )
    _on_hand(0.0, 0.0)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for site in (ACU_SITE, ODO_SITE):
                _demand(cur, "MOVE-1", site)
            _lead_times(cur, "MOVE-1")
        conn.commit()
    finally:
        conn.close()

    # -- The forecast basis (the stored system value). -------------------
    response = admin.put(
        "/api/settings/replenishment.demand_basis", json={"value": "forecast"}
    )
    assert response.status_code == 200, response.text

    # -- The one stored item mapping. ------------------------------------
    response = admin.put(
        MAPPINGS_URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": "MOVE-1",
            "target_code": "MOVE-1",
        },
    )
    assert response.status_code == 200, response.text

    # -- Capped by the excess: the short site suggests 27 (27 - 0, a null
    #    stockout category would not count at all) while the excess side
    #    holds only 5.5 (725.5 - 720) in excess. ------------------------
    _on_hand(725.5, 0.0)
    row = _row(admin)
    assert row["opportunity"] is True
    assert row["short_side"] == 1
    assert row["excess_side"] == 0
    short_site = row["sides"][1]["sites"][0]
    excess_site = row["sides"][0]["sites"][0]
    assert short_site["site_code"] == ODO_SITE
    assert short_site["stockout_category"] is not None
    assert short_site["suggested_order_quantity"] == REORDER_POINT_UNITS  # 27 - 0
    assert excess_site["site_code"] == ACU_SITE
    assert excess_site["excess_units"] == 725.5 - EXCESS_WINDOW_UNITS  # 5.5
    assert short_site["suggested_order_quantity"] > excess_site["excess_units"]
    assert row["suggested_move_units"] == 5  # floor(5.5), the excess capped
    assert type(row["suggested_move_units"]) is int  # a whole number in JSON

    # -- Capped by the need: the excess side holds 280 (1000 - 720) in
    #    excess, more than the short site suggests. ---------------------
    _on_hand(1000.0, 5.0)
    row = _row(admin)
    assert row["opportunity"] is True
    assert row["short_side"] == 1
    assert row["excess_side"] == 0
    short_site = row["sides"][1]["sites"][0]
    excess_site = row["sides"][0]["sites"][0]
    assert short_site["stockout_category"] is not None
    assert short_site["suggested_order_quantity"] == REORDER_POINT_UNITS - 5  # 22
    assert excess_site["excess_units"] == 1000.0 - EXCESS_WINDOW_UNITS  # 280.0
    assert excess_site["excess_units"] > short_site["suggested_order_quantity"]
    assert row["suggested_move_units"] == (
        short_site["reorder_point_units"] - short_site["on_hand_units"]
    )
    assert row["suggested_move_units"] == 22
    assert type(row["suggested_move_units"]) is int  # a whole number in JSON

    # -- No opportunity: both sides healthy (no risk, no excess). -------
    _on_hand(30.0, 30.0)
    row = _row(admin)
    for side in row["sides"]:
        for site in side["sites"]:
            assert site["stockout_category"] is None
            assert site["excess_units"] == 0
    assert row["opportunity"] is False
    assert row["short_side"] is None
    assert row["excess_side"] is None
    assert row["suggested_move_units"] is None
