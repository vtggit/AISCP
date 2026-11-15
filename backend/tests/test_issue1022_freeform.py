"""Issue #1022 proof: the cross-ERP comparison's first side.

``GET /api/cross-erp/items`` answers one row per stored
``erp_code_mappings`` row of kind ``item``, each row the source
code's side next to the target code's side under ``odoo``.  This
slice changes the first side only: it is built only from the source
code's item-sites whose ``source_system`` is not exactly ``odoo`` —
a null ``source_system`` counts as not ``odoo`` and stays on the
first side — while the second side stays exactly as it is, the
target code's item-sites under ``odoo``.  A side's
``source_system`` stays the one source of its own item-sites as
before, and a pair whose codes differ answers exactly as before the
slice.

Everything is exercised against the test database through the app's
test client with the development stub users, the way
``test_issue1010_freeform`` seeds its data: item-sites,
``inventory_items`` and ``on_hand`` through their repositories'
``upsert_many``, demand events and lead-time observations through the
database, and the mappings through ``PUT /api/erp-mappings``.
``date.today`` is pinned (2026-10-02) and the demand ends on
2026-09-30, so the shared default as-of is 2026-09-30 whatever the
calendar says; the replenishment demand basis is the forecast (the
stored system value), so every constant-history item-site keeps its
history figures (2.0 a day).

Proved here:

  * same code in both ERPs — ``SAME-1`` at ``154`` under
    ``acumatica`` (1000 on hand) and ``SAME-1`` at ``WH`` under
    ``odoo`` (5 on hand), the item mapping ``SAME-1`` to ``SAME-1``
    stored: the first side lists only ``154`` (the ``odoo``
    item-site at ``WH`` stays off it), its ``source_system`` reads
    ``acumatica`` and its ``on_hand_units`` is ``154``'s on hand;
    the second side lists only ``WH``, with ``source_system``
    ``odoo`` (the ``acumatica`` item-site at ``154`` stays off it);
  * the flag still works — with ``154`` holding excess and ``WH``
    short, ``opportunity`` is true, ``short_side`` is 1 and
    ``excess_side`` is 0;
  * a null ``source_system`` stays on the first side — ``NULL-1``
    at ``100`` with no ``source_system`` lists on the first side
    under ``source_system`` null;
  * different codes — the pair ``DIFF-1`` to ``DIFF-2`` answers
    field by field exactly as before this slice (the source code
    holds no ``odoo`` item-site, so the new filter drops nothing);
  * per-site figures are keyed by source_system — the clash pairs
    hold their item-site under ``acumatica`` while their on-hand row
    is stored under ``odoo`` (the first holder of each table wins):
    ``DUP-1``'s side lists its own on hand (0 — none is stored under
    ``acumatica``), its own excess (0) and the pair's own
    recommendation and stockout figures (2.0 a day, ``likely``);
    ``DUP-2``'s pair on hand is excess (4280 under ``odoo``) and the
    excess stays off the ``acumatica`` side;
"""

from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
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
EXCESS_UNITS_SAME1_154 = 1000.0 - max(720.0, float(REORDER_POINT_UNITS))


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
                f"id-de-1022-{item}-{site}-{i}",
                f"de-1022-{item}-{site}-{i}",
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
                f"id-lt-1022-{item}-{i}",
                f"lt-1022-{item}-{i}",
                f"SUP-1022-{item}",
                item,
                ordered,
                received,
                "test",
            ),
        )


# (item_code, site_code, source_system, on_hand) of every item-site.
_ITEM_SITES = [
    # The same code in both ERPs: excess at 154 under acumatica,
    # short at WH under odoo (the flag's true case).
    ("SAME-1", "154", "acumatica", 1000.0),
    ("SAME-1", "WH", "odoo", 5.0),
    # Different codes, both sides healthy (the unchanged answer).
    ("DIFF-1", "154", "acumatica", 30.0),
    ("DIFF-2", "WH", "odoo", 30.0),
    # A null source_system on the first side.
    ("NULL-1", "100", None, 30.0),
    ("NULL-2", "WH", "odoo", 30.0),
]
# (item_code, site_code, item-site source_system, on-hand
# source_system, on_hand) of the clash pairs: the item-site and the
# on-hand row are stored under different source systems (the first
# holder of each table wins), so the pair's on-hand row is not under
# the item-site's source system.
_CLASH_SITES = [
    # The pair's on hand is short: the side's own on hand is absent,
    # the pair's stockout row stays on the side.
    ("DUP-1", "300", "acumatica", "odoo", 5.0),
    # The pair's on hand is excess: the other ERP's excess units stay
    # off the side.
    ("DUP-2", "300", "acumatica", "odoo", 5000.0),
]
# The pairs that carry demand (every item-site and clash pair):
# 2.0 a day for 360 days, 360 days ending on the as-of.
_DEMAND_PAIRS = [(item, site) for item, site, _system, _on_hand in _ITEM_SITES] + [
    (item, site) for item, site, _system, _onhand_system, _on_hand in _CLASH_SITES
]
# The item codes that carry lead-time observations.
_LEAD_TIME_ITEMS = sorted({item for item, _site in _DEMAND_PAIRS})


def _seed() -> None:
    """The rows through the real repositories (mappings through the
    API, below)."""
    ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": item,
                "site_code": site,
                "unit_cost": 1.0,
                "source_system": system,
            }
            for item, site, system, _on_hand in _ITEM_SITES
        ]
        + [
            {
                "item_code": item,
                "site_code": site,
                "unit_cost": 1.0,
                "source_system": system,
            }
            for item, site, system, _onhand_system, _on_hand in _CLASH_SITES
        ]
    )
    OnHandPostgresRepository().upsert_many(
        [
            {
                "item_code": item,
                "site_code": site,
                "quantity_on_hand": on_hand,
                "as_of": AS_OF,
                "source_system": system,
            }
            for item, site, system, on_hand in _ITEM_SITES
        ]
        + [
            {
                "item_code": item,
                "site_code": site,
                "quantity_on_hand": on_hand,
                "as_of": AS_OF,
                "source_system": onhand_system,
            }
            for item, site, _system, onhand_system, on_hand in _CLASH_SITES
        ]
    )
    InventoryItemPostgresRepository().upsert_many(
        [
            {
                "item_code": "SAME-1",
                "description": "Same Code Item",
                "source_system": "acumatica",
            },
            {
                "item_code": "DIFF-1",
                "description": "Different Source Item",
                "source_system": "acumatica",
            },
        ]
    )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item, site in _DEMAND_PAIRS:
                _demand(cur, item, site)
            for item in _LEAD_TIME_ITEMS:
                _lead_times(cur, item)
        conn.commit()
    finally:
        conn.close()


def _put_mapping(client, source, target):
    response = client.put(
        MAPPINGS_URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": source,
            "target_code": target,
        },
    )
    assert response.status_code == 200, response.text


def test_issue1022_freeform(client, monkeypatch):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    monkeypatch.delenv("EXCESS_COVER_MONTHS", raising=False)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    _seed()

    # -- The forecast basis (the stored system value). -------------------
    response = admin.put(
        "/api/settings/replenishment.demand_basis", json={"value": "forecast"}
    )
    assert response.status_code == 200, response.text

    # -- Store the item mappings: different codes, a null source_system
    #    on the first side, and the same code in both ERPs. -------------
    _put_mapping(admin, "DIFF-1", "DIFF-2")
    _put_mapping(admin, "DUP-1", "DUP-1")
    _put_mapping(admin, "DUP-2", "DUP-2")
    _put_mapping(admin, "NULL-1", "NULL-2")
    _put_mapping(admin, "SAME-1", "SAME-1")

    # -- The comparison: one row per stored item mapping, in
    #    source_code order. ----------------------------------------------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["as_of"] == AS_OF_STR
    rows = answer["rows"]
    assert [row["source_code"] for row in rows] == [
        "DIFF-1",
        "DUP-1",
        "DUP-2",
        "NULL-1",
        "SAME-1",
    ]
    by_source = {row["source_code"]: row for row in rows}

    # -- Same code in both ERPs: the first side lists only 154 (the
    #    odoo item-site at WH stays off it), its source_system reads
    #    acumatica and its on_hand_units is 154's on hand; the second
    #    side lists only WH, with source_system odoo (the acumatica
    #    item-site at 154 stays off it). ----------------------------------
    same = by_source["SAME-1"]
    assert same["target_code"] == "SAME-1"
    assert same["description"] == "Same Code Item"
    assert same["sides"][0] == {
        "source_system": "acumatica",
        "item_code": "SAME-1",
        "sites": [
            {
                "site_code": "154",
                "on_hand_units": 1000.0,
                "daily_demand": 2.0,
                "days_of_cover": 500.0,
                "status": "ok",
                "reorder_point_units": REORDER_POINT_UNITS,
                "suggested_order_quantity": 0,
                "stockout_category": None,
                "excess_units": EXCESS_UNITS_SAME1_154,
            }
        ],
        "on_hand_units": 1000.0,
    }
    assert same["sides"][1] == {
        "source_system": "odoo",
        "item_code": "SAME-1",
        "sites": [
            {
                "site_code": "WH",
                "on_hand_units": 5.0,
                "daily_demand": 2.0,
                "days_of_cover": 2.5,
                "status": "ok",
                "reorder_point_units": REORDER_POINT_UNITS,
                "suggested_order_quantity": REORDER_POINT_UNITS - 5,
                "stockout_category": "likely",
                "excess_units": 0,
            }
        ],
        "on_hand_units": 5.0,
    }
    # Regression: the first side excludes the odoo item-site and the
    # second side stays odoo-only.
    first_site_codes = [site["site_code"] for site in same["sides"][0]["sites"]]
    second_site_codes = [site["site_code"] for site in same["sides"][1]["sites"]]
    assert first_site_codes == ["154"]
    assert "WH" not in first_site_codes  # the odoo item-site stays off
    assert second_site_codes == ["WH"]
    assert "154" not in second_site_codes  # only odoo item-sites
    assert same["sides"][1]["source_system"] == "odoo"

    # -- The flag still works: 154 holds excess while WH runs short. ----
    assert same["opportunity"] is True
    assert same["short_side"] == 1
    assert same["excess_side"] == 0

    # -- A null source_system stays on the first side (a null is not
    #    exactly "odoo"): NULL-1 at 100 with no source_system lists on
    #    the first side under source_system null. ------------------------
    null_row = by_source["NULL-1"]
    assert null_row["description"] is None  # neither code has one
    assert null_row["sides"][0] == {
        "source_system": None,
        "item_code": "NULL-1",
        "sites": [
            {
                "site_code": "100",
                "on_hand_units": 30.0,
                "daily_demand": 2.0,
                "days_of_cover": 15.0,
                "status": "ok",
                "reorder_point_units": REORDER_POINT_UNITS,
                "suggested_order_quantity": 0,
                "stockout_category": None,
                "excess_units": 0,
            }
        ],
        "on_hand_units": 30.0,
    }
    assert null_row["sides"][1] == {
        "source_system": "odoo",
        "item_code": "NULL-2",
        "sites": [
            {
                "site_code": "WH",
                "on_hand_units": 30.0,
                "daily_demand": 2.0,
                "days_of_cover": 15.0,
                "status": "ok",
                "reorder_point_units": REORDER_POINT_UNITS,
                "suggested_order_quantity": 0,
                "stockout_category": None,
                "excess_units": 0,
            }
        ],
        "on_hand_units": 30.0,
    }
    assert null_row["opportunity"] is False
    assert null_row["short_side"] is None
    assert null_row["excess_side"] is None

    # -- Different codes: the pair answers field by field exactly as
    #    before this slice (the source code holds no odoo item-site, so
    #    the new filter drops nothing). -----------------------------------
    diff = by_source["DIFF-1"]
    assert diff == {
        "source_code": "DIFF-1",
        "target_code": "DIFF-2",
        "description": "Different Source Item",
        "sides": [
            {
                "source_system": "acumatica",
                "item_code": "DIFF-1",
                "sites": [
                    {
                        "site_code": "154",
                        "on_hand_units": 30.0,
                        "daily_demand": 2.0,
                        "days_of_cover": 15.0,
                        "status": "ok",
                        "reorder_point_units": REORDER_POINT_UNITS,
                        "suggested_order_quantity": 0,
                        "stockout_category": None,
                        "excess_units": 0,
                    }
                ],
                "on_hand_units": 30.0,
            },
            {
                "source_system": "odoo",
                "item_code": "DIFF-2",
                "sites": [
                    {
                        "site_code": "WH",
                        "on_hand_units": 30.0,
                        "daily_demand": 2.0,
                        "days_of_cover": 15.0,
                        "status": "ok",
                        "reorder_point_units": REORDER_POINT_UNITS,
                        "suggested_order_quantity": 0,
                        "stockout_category": None,
                        "excess_units": 0,
                    }
                ],
                "on_hand_units": 30.0,
            },
        ],
        "opportunity": False,
        "short_side": None,
        "excess_side": None,
        "suggested_move_units": None,
    }

    # -- Per-site figures are keyed by source_system: the clash pairs
    #    hold their item-site under acumatica while their on-hand row
    #    is stored under odoo (the first holder of each table wins) —
    #    the side lists its own on hand (0, none is stored under
    #    acumatica), its own excess (0) and the pair's own
    #    recommendation and stockout figures. -------------------------
    dup_short = by_source["DUP-1"]
    assert dup_short["target_code"] == "DUP-1"
    assert dup_short["description"] is None
    assert dup_short["sides"][0] == {
        "source_system": "acumatica",
        "item_code": "DUP-1",
        "sites": [
            {
                "site_code": "300",
                "on_hand_units": 0.0,
                "daily_demand": 2.0,
                "days_of_cover": 0.0,
                "status": "ok",
                "reorder_point_units": REORDER_POINT_UNITS,
                "suggested_order_quantity": REORDER_POINT_UNITS - 5,
                "stockout_category": "likely",
                "excess_units": 0,
            }
        ],
        "on_hand_units": 0.0,
    }
    assert dup_short["sides"][1] == {
        "source_system": None,
        "item_code": "DUP-1",
        "sites": [],
        "on_hand_units": 0.0,
    }
    assert dup_short["opportunity"] is False
    assert dup_short["short_side"] is None
    assert dup_short["excess_side"] is None

    dup_excess = by_source["DUP-2"]
    assert dup_excess["description"] is None
    assert dup_excess["sides"][0] == {
        "source_system": "acumatica",
        "item_code": "DUP-2",
        "sites": [
            {
                "site_code": "300",
                "on_hand_units": 0.0,
                "daily_demand": 2.0,
                "days_of_cover": 0.0,
                "status": "ok",
                "reorder_point_units": REORDER_POINT_UNITS,
                "suggested_order_quantity": 0,
                "stockout_category": None,
                "excess_units": 0,
            }
        ],
        "on_hand_units": 0.0,
    }
    assert dup_excess["sides"][1] == {
        "source_system": None,
        "item_code": "DUP-2",
        "sites": [],
        "on_hand_units": 0.0,
    }
    assert dup_excess["opportunity"] is False
    assert dup_excess["short_side"] is None
    assert dup_excess["excess_side"] is None
