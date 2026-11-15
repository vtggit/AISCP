"""Issue #647 proof: drift rows carry the recommendation's order quantity.

AC-1 — every row of ``app.recommend.drift.drift_ranked`` gains
``order_quantity``, copied from its recommendation's ``order_quantity``
the same way the row carries ``demand_basis`` and ``other_basis`` —
null when the recommendation's is.  ``DriftDecisionRow`` (the response
model of ``GET /api/drift`` and ``GET /api/drift/by-key``) gains
``order_quantity: OrderQuantity | None`` reusing the ``OrderQuantity``
model of ``app.api.recommendations``, so both endpoints return the
value on every row, including the ``include_decided`` rows.  The
``DriftRow`` model — the OData ``Drift`` set's shape — is unchanged,
the ranking, the money at stake and every other field are unchanged,
and the drift CSV export and its columns are unchanged.

AC-2 — the proof runs through the test client (the development
planner token), with ``recommendations_for_all`` monkeypatched to two
"ok" recommendations: A647, whose ``order_quantity`` has
``order_quantity_units`` 171, ``recommended_max_units`` 230,
``current_order_qty`` 50 and ``current_max_qty`` 100, and B647, whose
``order_quantity`` is null.  The item-sites' current settings are
patched so A647's 100.0 reorder point at 1.0/unit sits 41.0 units
above the recommended 59.0 (rank 1, 41.0 at stake) and B647's 60.0
sits 10.0 above its recommended 50.0 (rank 2, 10.0).  The checks:
``GET /api/drift`` returns the exact ``order_quantity`` for the first
row and null for the second — the same with ``include_decided`` true —
``GET /api/drift/by-key`` returns the same per item-site, the OData
``Drift`` entity type's property names still equal
``set(DriftRow.model_fields)`` with no ``order_quantity`` among them
and an ``/odata/Drift`` row carries no ``order_quantity`` key, and the
drift CSV export's header row is the pre-existing column set,
unchanged.  The proof also calls ``assess_drift`` directly with a
recommendation whose daily demand is zero and whose item-site has
no order quantity set: it must not divide by zero, and it prices
the row at zero cycles per year and zero money at stake.
"""

import base64
import csv
import io
import xml.etree.ElementTree as ElementTree

import app.recommend.drift as drift_module
from app.api.drift import DriftRow
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

EDM = "http://docs.oasis-open.org/odata/ns/edm"
ODATA_USER = "odata-machine-647"
ODATA_PASSWORD = "feed-secret-647"
SITE = "SITE-647"
AS_OF = "2026-10-25"

# A647's recommendation's order quantity: the exact values a drift row
# must carry on every surface.
ORDER_QUANTITY = {
    "basis": "eoq",
    "clamped": None,
    "order_quantity": 170.6,
    "order_quantity_units": 171,
    "cover_days": 42.75,
    "annual_demand": 1460.0,
    "order_cost": 50.0,
    "holding_rate": 0.25,
    "unit_cost": 2.0,
    "arithmetic": (
        "order_quantity = sqrt(2 * 365 * d * order_cost / "
        "(holding_rate * unit_cost)) = 170.6"
    ),
    "recommended_max_units": 230,
    "current_order_qty": 50.0,
    "current_max_qty": 100.0,
}

# The drift CSV export's header row, in order — the pre-existing column
# set, unchanged by this issue (no order_quantity).
CSV_COLUMNS = [
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

# The item-sites' current planning settings: A647 at 100.0 (41.0 units
# above its recommended 59.0, 41.0 at stake at 1.0/unit, rank 1),
# B647 at 60.0 (10.0 above its recommended 50.0, 10.0 at stake, rank 2).
ITEM_SITES = [
    {
        "item_code": "A647",
        "site_code": SITE,
        "reorder_point": 100.0,
        "unit_cost": 1.0,
        "order_qty": 50.0,
        "max_qty": 100.0,
    },
    {
        "item_code": "B647",
        "site_code": SITE,
        "reorder_point": 60.0,
        "unit_cost": 1.0,
        "order_qty": None,
        "max_qty": None,
    },
]


def _recommendation(item_code: str, reorder_point: float, order_quantity) -> dict:
    """One "ok" recommendation; only the fields drift reads are set."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "as_of": AS_OF,
        "status": "ok",
        "demand_class": "stable",
        "method": "mean",
        "notes": [],
        "safety_stock": reorder_point - 50.0,
        "reorder_point": reorder_point,
        "safety_stock_units": int(reorder_point - 50.0),
        "reorder_point_units": int(reorder_point),
        "inputs": {"d": 4.0, "sd_d": 0.5, "lt": 10.0, "sd_lt": 1.0},
        "arithmetic": "reorder_point = lt * d + z * sigma_l",
        "demand_basis": "history",
        "other_basis": None,
        "order_quantity": order_quantity,
    }


def test_issue647_freeform(monkeypatch, client):
    # Two "ok" recommendations: A647 with the order quantity above,
    # B647 with a null one.
    def fake_recommendations_for_all(as_of=None):
        return [
            _recommendation("A647", 59.0, dict(ORDER_QUANTITY)),
            _recommendation("B647", 50.0, None),
        ]

    def fake_list_rows(self, limit, offset, **filters):
        return [dict(row) for row in ITEM_SITES][offset : offset + limit]

    monkeypatch.setattr(
        drift_module, "recommendations_for_all", fake_recommendations_for_all
    )
    monkeypatch.setattr(ItemSitePostgresRepository, "list_rows", fake_list_rows)

    monkeypatch.setenv("AIERP_ODATA_USER", ODATA_USER)
    monkeypatch.setenv("AIERP_ODATA_PASSWORD", ODATA_PASSWORD)
    odata_headers = {
        "Authorization": "Basic "
        + base64.b64encode(f"{ODATA_USER}:{ODATA_PASSWORD}".encode()).decode()
    }

    # --- The list: the first row carries the exact order quantity, the
    # second null; the ranking and money at stake are unchanged.
    response = client.get("/api/drift", params={"limit": 100})
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["item_code"] for row in rows] == ["A647", "B647"]
    assert [row["rank"] for row in rows] == [1, 2]
    assert rows[0]["money_at_stake"] == 41.0
    assert rows[1]["money_at_stake"] == 10.0
    assert rows[0]["order_quantity"] == ORDER_QUANTITY
    assert rows[1]["order_quantity"] is None

    # include_decided=true: the same values on the same rows.
    response = client.get(
        "/api/drift", params={"limit": 100, "include_decided": "true"}
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["item_code"] for row in rows] == ["A647", "B647"]
    assert rows[0]["order_quantity"] == ORDER_QUANTITY
    assert rows[1]["order_quantity"] is None

    # --- By key: the same order_quantity per item-site.
    for item_code, expected in (("A647", ORDER_QUANTITY), ("B647", None)):
        response = client.get(
            "/api/drift/by-key",
            params={"item_code": item_code, "site_code": SITE},
        )
        assert response.status_code == 200, response.text
        row = response.json()
        if expected is None:
            assert row["order_quantity"] is None, item_code
        else:
            assert row["order_quantity"] == expected, item_code

    # --- OData unchanged: the Drift entity type is still exactly the
    # DriftRow shape, with no order_quantity property, and a Drift row
    # carries no order_quantity key.
    response = client.get("/odata/$metadata", headers=odata_headers)
    assert response.status_code == 200, response.text
    root = ElementTree.fromstring(response.text)
    schema = root.find(f"{{{EDM}}}Schema")
    assert schema is not None
    drift_type = next(
        element
        for element in schema.findall(f"{{{EDM}}}EntityType")
        if element.get("Name") == "Drift"
    )
    property_names = {
        prop.get("Name") for prop in drift_type.findall(f"{{{EDM}}}Property")
    }
    assert "order_quantity" not in property_names, property_names
    assert property_names == set(DriftRow.model_fields), property_names

    response = client.get("/odata/Drift", headers=odata_headers)
    assert response.status_code == 200, response.text
    odata_rows = response.json()["value"]
    assert {row["item_code"] for row in odata_rows} == {"A647", "B647"}
    for row in odata_rows:
        assert "order_quantity" not in row, row

    # --- CSV unchanged: the export's header row is the pre-existing
    # column set, without order_quantity.
    response = client.get("/api/drift/export.csv", params={"as_of": AS_OF})
    assert response.status_code == 200, response.text
    header = next(
        csv.reader(io.StringIO(response.text.splitlines()[0].lstrip("\ufeff")))
    )
    assert header == CSV_COLUMNS
    assert "order_quantity" not in header

    # --- Zero daily demand with no order quantity set: assess_drift
    # must not divide by zero; the row prices at zero cycles per year
    # and zero money at stake.
    zero_demand = _recommendation("Z647", 0.0, None)
    zero_demand["inputs"] = {"d": 0.0, "sd_d": 0.5, "lt": 10.0, "sd_lt": 1.0}
    result = drift_module.assess_drift(
        zero_demand,
        {"reorder_point": None, "unit_cost": 1.0, "order_qty": None},
    )
    assert result["direction"] == "under"
    assert result["order_qty"] == 0.0
    assert result["cycles_per_year"] == 0.0
    assert result["units_short_per_year"] == 0.0
    assert result["money_at_stake"] == 0.0
    assert "order quantity assumed: 30 days of demand" in result["notes"]
