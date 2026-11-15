"""Issue #661 proof: drift rows carry the recommendation's service level.

AC-1 — every row of ``app.recommend.drift.drift_ranked`` gains
``service_level_source``, copied from its recommendation the same way
the row carries ``order_quantity``, and ``service_level``, the
recommendation's ``inputs["service_level"]``.  ``DriftDecisionRow``
(the response model of ``GET /api/drift`` and ``GET /api/drift/by-key``)
gains ``service_level_source: ServiceLevelSource | None`` reusing the
``ServiceLevelSource`` model of ``app.api.recommendations`` and
``service_level: float | None``, so both endpoints return the values on
every row, including the ``include_decided`` rows.  The ``DriftRow``
model — the OData ``Drift`` set's shape — is unchanged, the ranking, the
money at stake and every other field are unchanged, and the drift CSV
export and its columns are unchanged.

AC-2 — the proof runs through the test client (the development planner
token), with ``recommendations_for_all`` monkeypatched to two "ok"
recommendations: A661, whose ``service_level_source`` is
``{"abc_class": "A", "source": "class"}`` and whose
``inputs["service_level"]`` is 0.98, and B661, whose
``service_level_source`` is ``{"abc_class": null, "source": "single"}``
and whose ``inputs["service_level"]`` is 0.95.  The item-sites' current
settings are patched so A661's 100.0 reorder point at 1.0/unit sits
41.0 units above the recommended 59.0 (rank 1, 41.0 at stake) and
B661's 60.0 sits 10.0 above its recommended 50.0 (rank 2, 10.0).  The
checks: ``GET /api/drift`` returns the exact values for both rows — the
same with ``include_decided`` true — ``GET /api/drift/by-key`` returns
the same per item-site, the OData ``Drift`` entity type's property names
still equal ``set(DriftRow.model_fields)`` with no service-level
property among them and an ``/odata/Drift`` row carries neither key,
and the drift CSV export's header row is the pre-existing column set,
unchanged.
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
ODATA_USER = "odata-machine-661"
ODATA_PASSWORD = "feed-secret-661"
SITE = "SITE-661"
AS_OF = "2026-10-25"

# The exact service-level values each recommendation carries, and thus
# the exact values a drift row must carry on every surface.
SOURCE_A = {"abc_class": "A", "source": "class"}
SERVICE_LEVEL_A = 0.98
SOURCE_B = {"abc_class": None, "source": "single"}
SERVICE_LEVEL_B = 0.95

# The drift CSV export's header row, in order — the pre-existing column
# set, unchanged by this issue (no service-level columns).
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

# The item-sites' current planning settings: A661 at 100.0 (41.0 units
# above its recommended 59.0, 41.0 at stake at 1.0/unit, rank 1),
# B661 at 60.0 (10.0 above its recommended 50.0, 10.0 at stake, rank 2).
ITEM_SITES = [
    {
        "item_code": "A661",
        "site_code": SITE,
        "reorder_point": 100.0,
        "unit_cost": 1.0,
        "order_qty": 50.0,
        "max_qty": 100.0,
    },
    {
        "item_code": "B661",
        "site_code": SITE,
        "reorder_point": 60.0,
        "unit_cost": 1.0,
        "order_qty": None,
        "max_qty": None,
    },
]


def _recommendation(
    item_code: str,
    reorder_point: float,
    service_level_source: dict,
    service_level: float,
) -> dict:
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
        "inputs": {
            "d": 4.0,
            "sd_d": 0.5,
            "lt": 10.0,
            "sd_lt": 1.0,
            "service_level": service_level,
        },
        "arithmetic": "reorder_point = lt * d + z * sigma_l",
        "demand_basis": "history",
        "other_basis": None,
        "order_quantity": None,
        "service_level_source": service_level_source,
    }


def test_issue661_freeform(monkeypatch, client):
    # Two "ok" recommendations: A661 with the class service level,
    # B661 with the single one.
    def fake_recommendations_for_all(as_of=None):
        return [
            _recommendation("A661", 59.0, dict(SOURCE_A), SERVICE_LEVEL_A),
            _recommendation("B661", 50.0, dict(SOURCE_B), SERVICE_LEVEL_B),
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

    # --- The list: both rows carry their recommendation's exact
    # service-level values; the ranking and money at stake are unchanged.
    response = client.get("/api/drift", params={"limit": 100})
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["item_code"] for row in rows] == ["A661", "B661"]
    assert [row["rank"] for row in rows] == [1, 2]
    assert rows[0]["money_at_stake"] == 41.0
    assert rows[1]["money_at_stake"] == 10.0
    assert rows[0]["service_level_source"] == SOURCE_A
    assert rows[0]["service_level"] == SERVICE_LEVEL_A
    assert rows[1]["service_level_source"] == SOURCE_B
    assert rows[1]["service_level"] == SERVICE_LEVEL_B

    # include_decided=true: the same values on the same rows.
    response = client.get(
        "/api/drift", params={"limit": 100, "include_decided": "true"}
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["item_code"] for row in rows] == ["A661", "B661"]
    assert rows[0]["service_level_source"] == SOURCE_A
    assert rows[0]["service_level"] == SERVICE_LEVEL_A
    assert rows[1]["service_level_source"] == SOURCE_B
    assert rows[1]["service_level"] == SERVICE_LEVEL_B

    # --- By key: the same service-level values per item-site.
    for item_code, source, level in (
        ("A661", SOURCE_A, SERVICE_LEVEL_A),
        ("B661", SOURCE_B, SERVICE_LEVEL_B),
    ):
        response = client.get(
            "/api/drift/by-key",
            params={"item_code": item_code, "site_code": SITE},
        )
        assert response.status_code == 200, response.text
        row = response.json()
        assert row["service_level_source"] == source, item_code
        assert row["service_level"] == level, item_code

    # --- OData unchanged: the Drift entity type is still exactly the
    # DriftRow shape, with no service-level property, and a Drift row
    # carries neither key.
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
    assert "service_level_source" not in property_names, property_names
    assert "service_level" not in property_names, property_names
    assert property_names == set(DriftRow.model_fields), property_names

    response = client.get("/odata/Drift", headers=odata_headers)
    assert response.status_code == 200, response.text
    odata_rows = response.json()["value"]
    assert {row["item_code"] for row in odata_rows} == {"A661", "B661"}
    for row in odata_rows:
        assert "service_level_source" not in row, row
        assert "service_level" not in row, row

    # --- CSV: the export's header row is the pre-existing column set
    # plus the issue #667 columns, which include service_level.
    response = client.get("/api/drift/export.csv", params={"as_of": AS_OF})
    assert response.status_code == 200, response.text
    header = next(
        csv.reader(io.StringIO(response.text.splitlines()[0].lstrip("\ufeff")))
    )
    assert header == CSV_COLUMNS
    assert "service_level_source" not in header
    assert "service_level" in header
