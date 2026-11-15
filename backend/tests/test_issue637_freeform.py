"""Issue #637 proof: item descriptions on the drift, excess and
stockout lists.

Through the test client with the planner (development-mode) token,
each ranking function (``drift_ranked``, ``excess_ranked``,
``stockout_ranked`` as imported by the API modules) is monkeypatched
to two rows — items "CONPAPERST" and "UNKNOWN1" at one site — and the
inventory-items read (``InventoryItemPostgresRepository.list_all``)
is monkeypatched to ``{"CONPAPERST": "Letter Paper Ream Standard"}``.

* the three list routes: ``GET /api/drift``, ``GET /api/excess`` and
  ``GET /api/stockout`` each return "Letter Paper Ream Standard" for
  CONPAPERST and null for UNKNOWN1, on the plain list and again with
  ``include_decided=true`` (the decision rows extend the plain rows,
  so the field rides along),
* one read per request: the inventory-items read happens exactly once
  per list request and the CSV export routes never read it,
* CSV unchanged: each CSV export's header row is exactly the header
  the existing export tests assert (issue #64 for drift, #88 for
  excess, #99 for stockout) — the exports carry no new column.
"""

import csv
import io
from datetime import date

from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)

AS_OF = date(2026, 9, 30)
SITE = "SITE-637"

DRIFT_ROWS = [
    {
        "item_code": "CONPAPERST",
        "site_code": SITE,
        "as_of": AS_OF,
        "category": "drift",
        "rank": 1,
        "direction": "under",
        "units_gap": 12.5,
        "money_at_stake": 375.0,
        "unit_cost": 30.0,
        "notes": ["reorder point drifted below the recommended point"],
        "money_basis": "money_at_stake = units_gap * unit_cost = 12.50 * 30 = 375.00",
        "reorder_point": 100.0,
        "reorder_point_units": 100,
        "safety_stock": 20.0,
        "arithmetic": "reorder point = 80.00 + 1.64 * 12.20 = 100.00",
        "current_reorder_point": 87.5,
    },
    {
        "item_code": "UNKNOWN1",
        "site_code": SITE,
        "as_of": AS_OF,
        "category": "drift",
        "rank": 2,
        "direction": "over",
        "units_gap": -4.0,
        "money_at_stake": None,
        "unit_cost": None,
        "notes": ["cost unknown"],
        "money_basis": "money_at_stake = unknown",
        "reorder_point": 50.0,
        "reorder_point_units": 50,
        "safety_stock": 10.0,
        "arithmetic": "reorder point = 40.00 + 1.64 * 6.10 = 50.00",
        "current_reorder_point": 54.0,
    },
]

EXCESS_ROWS = [
    {
        "rank": 1,
        "category": "dead",
        "dead_reason": "never_moved",
        "item_code": "CONPAPERST",
        "site_code": SITE,
        "on_hand_units": 100.0,
        "unit_cost": 30.0,
        "last_demand_on": None,
        "demand_window_units": 0.0,
        "months_of_cover": None,
        "justified_units": 0,
        "excess_units": 100.0,
        "money_tied_up": 3000.0,
        "as_of": AS_OF,
        "notes": [],
        "arithmetic": (
            "no demand up to and including 2026-09-30: "
            "justified_units = 0; excess_units = on_hand_units = 100"
        ),
    },
    {
        "rank": 2,
        "category": "excess",
        "item_code": "UNKNOWN1",
        "site_code": SITE,
        "on_hand_units": 60.0,
        "unit_cost": None,
        "last_demand_on": date(2026, 9, 1),
        "demand_window_units": 30.0,
        "months_of_cover": 6.0,
        "justified_units": 30,
        "excess_units": 30.0,
        "money_tied_up": None,
        "as_of": AS_OF,
        "notes": ["no unit cost"],
        "arithmetic": (
            "justified_units = max(ceil(30), 0) = 30; "
            "excess_units = on_hand_units - justified_units = 30.00"
        ),
    },
]

STOCKOUT_ROWS = [
    {
        "rank": 1,
        "category": "likely",
        "item_code": "CONPAPERST",
        "site_code": SITE,
        "on_hand_units": 8.0,
        "open_supply_units": 0.0,
        "open_supply_due_by": None,
        "unit_cost": 30.0,
        "lead_time_days": 10.0,
        "lead_time_demand": 20.0,
        "p_stockout": 0.9987,
        "days_of_cover": 4.0,
        "projected_stockout_on": date(2026, 10, 2),
        "recommended_reorder_point_units": 27,
        "expected_units_short": 12.0,
        "money_at_risk": 360.0,
        "as_of": AS_OF,
        "notes": [],
        "arithmetic": (
            "mu = d * lt = 2 * 10 = 20; "
            "sigma = sqrt(10 * 0^2 + 2^2 * 2^2) = 4; "
            "p_stockout = 1 - Phi((8 - 20) / 4) = 0.9987"
        ),
    },
    {
        "rank": 2,
        "category": "at_risk",
        "item_code": "UNKNOWN1",
        "site_code": SITE,
        "on_hand_units": 22.0,
        "open_supply_units": 0.0,
        "open_supply_due_by": None,
        "unit_cost": None,
        "lead_time_days": 10.0,
        "lead_time_demand": 20.0,
        "p_stockout": 0.3085,
        "days_of_cover": 11.0,
        "projected_stockout_on": None,
        "recommended_reorder_point_units": 27,
        "expected_units_short": 0.5,
        "money_at_risk": None,
        "as_of": AS_OF,
        "notes": [],
        "arithmetic": (
            "mu = d * lt = 2 * 10 = 20; "
            "sigma = sqrt(10 * 0^2 + 2^2 * 2^2) = 4; "
            "p_stockout = 1 - Phi((22 - 20) / 4) = 0.3085"
        ),
    },
]

# The CSV header rows exactly as the existing export tests assert
# them (issue #64 drift, #88 excess, #99 stockout).
DRIFT_HEADERS = [
    "rank",
    "category",
    "item_code",
    "site_code",
    "direction",
    "current_reorder_point",
    "recommended_reorder_point_units",
    "safety_stock_units",
    "units_gap",
    "unit_cost",
    "money_at_stake",
    "notes",
    "money_basis",
    "arithmetic",
    "as_of",
    "recommended_order_quantity_units",
    "recommended_max_units",
    "current_order_qty",
    "current_max_qty",
    "abc_class",
    "service_level",
]

EXCESS_HEADERS = [
    "rank",
    "category",
    "dead_reason",
    "item_code",
    "site_code",
    "on_hand_units",
    "justified_units",
    "excess_units",
    "months_of_cover",
    "unit_cost",
    "money_tied_up",
    "last_demand_on",
    "demand_window_units",
    "notes",
    "arithmetic",
    "as_of",
]

STOCKOUT_HEADERS = [
    "rank",
    "category",
    "item_code",
    "site_code",
    "p_stockout",
    "projected_stockout_on",
    "days_of_cover",
    "on_hand_units",
    "open_supply_units",
    "open_supply_due_by",
    "lead_time_days",
    "lead_time_demand",
    "recommended_reorder_point_units",
    "expected_units_short",
    "unit_cost",
    "money_at_risk",
    "notes",
    "arithmetic",
    "as_of",
]


def _header_of(export_response) -> list[str]:
    """The header row of a CSV export response (BOM stripped)."""
    assert export_response.status_code == 200
    assert export_response.headers["content-type"] == "text/csv; charset=utf-8"
    body = export_response.text.encode("utf-8")
    assert body.startswith(b"\xef\xbb\xbf")
    return next(csv.reader(io.StringIO(body.decode("utf-8-sig"))))


def test_issue637_freeform(client, monkeypatch):
    calls = {"list_all": 0}

    def fake_drift_ranked(as_of=None):
        return [dict(row) for row in DRIFT_ROWS]

    def fake_excess_ranked(as_of=None):
        return [dict(row) for row in EXCESS_ROWS]

    def fake_stockout_ranked(as_of=None):
        return [dict(row) for row in STOCKOUT_ROWS]

    monkeypatch.setattr("app.api.drift.drift_ranked", fake_drift_ranked)
    monkeypatch.setattr("app.api.excess.excess_ranked", fake_excess_ranked)
    monkeypatch.setattr("app.api.stockout.stockout_ranked", fake_stockout_ranked)

    def fake_list_all(self):
        calls["list_all"] += 1
        return [
            {
                "item_code": "CONPAPERST",
                "description": "Letter Paper Ream Standard",
            }
        ]

    monkeypatch.setattr(InventoryItemPostgresRepository, "list_all", fake_list_all)

    expected = {
        "CONPAPERST": "Letter Paper Ream Standard",
        "UNKNOWN1": None,
    }

    def check_listing(response, params=None):
        assert response.status_code == 200
        rows = response.json()
        assert [row["item_code"] for row in rows] == [
            "CONPAPERST",
            "UNKNOWN1",
        ]
        for row in rows:
            assert row["item_description"] == expected[row["item_code"]]

    def one_read_per_request(get):
        before = calls["list_all"]
        get()
        assert calls["list_all"] == before + 1

    # --- the three list routes: plain and include_decided ----------------
    for params in (None, {"include_decided": "true"}):
        one_read_per_request(
            lambda params=params: check_listing(client.get("/api/drift", params=params))
        )
        one_read_per_request(
            lambda params=params: check_listing(
                client.get("/api/excess", params=params)
            )
        )
        one_read_per_request(
            lambda params=params: check_listing(
                client.get("/api/stockout", params=params)
            )
        )

    # --- the CSV exports: never read the items, header unchanged ----------
    for path, headers in (
        ("/api/drift/export.csv", DRIFT_HEADERS),
        ("/api/excess/export.csv", EXCESS_HEADERS),
        ("/api/stockout/export.csv", STOCKOUT_HEADERS),
    ):
        before = calls["list_all"]
        assert _header_of(client.get(path)) == headers
        assert calls["list_all"] == before
