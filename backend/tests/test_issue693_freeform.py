"""Issue #693 proof: CSV export of the planning-parameter recommendations.

``GET /api/recommendations/export.csv`` behind the same authentication
as ``GET /api/recommendations`` (planner bearer token; 401 without one),
with the same optional ``as_of`` default as ``GET /api/drift/export.csv``
and the same 400 on a ``ValueError`` from the recommendation service.
The response is ``text/csv; charset=utf-8``, an attachment named
``planning-parameters-<as_of>.csv``, and starts with a UTF-8 byte-order
mark. One row per result of ``recommendations_for_all(as_of)``, in that
order, with no limit, and the header row is exactly the 18 columns in
order: ``item_code``, ``site_code``, ``as_of``, ``status``,
``demand_class``, ``abc_class`` (from ``service_level_source.abc_class``),
``service_level`` (from ``inputs.service_level``),
``recommended_reorder_point_units`` and
``recommended_safety_stock_units`` (from ``reorder_point_units`` and
``safety_stock_units``), ``recommended_order_quantity_units`` and
``recommended_max_units`` (from ``order_quantity``),
``current_reorder_point`` and ``current_safety_stock`` (from
``current``), ``current_order_qty`` and ``current_max_qty`` (from
``order_quantity``), ``empirical_reorder_point_units`` and
``empirical_safety_stock_units`` (from ``empirical``) and
``unit_cost``.

With ``recommendations_for_all`` monkeypatched where the route imports
it (``app.api.recommendations``) to three rows:
  * an "ok" smooth row with every nested object present and an
    ``abc_class`` of "A": every cell is the expected value,
  * a "not_recommendable_demand" intermittent row with a non-null
    ``empirical`` and null ``reorder_point``, ``safety_stock``,
    ``order_quantity`` and ``service_level_source.abc_class``: the
    recommended cells are empty and the empirical cells are filled,
  * a row whose ``item_code`` is ``=SUM(A1)``: the cell is written as
    ``'=SUM(A1)`` so Excel never evaluates it as a formula.

``backend/openapi.json`` lists the new path.
"""

import csv
import io
import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

AS_OF = date(2026, 9, 30)

HEADERS = [
    "item_code",
    "site_code",
    "as_of",
    "status",
    "demand_class",
    "abc_class",
    "service_level",
    "recommended_reorder_point_units",
    "recommended_safety_stock_units",
    "recommended_order_quantity_units",
    "recommended_max_units",
    "current_reorder_point",
    "current_safety_stock",
    "current_order_qty",
    "current_max_qty",
    "empirical_reorder_point_units",
    "empirical_safety_stock_units",
    "unit_cost",
]


def _parse(text: str) -> list[list[str]]:
    """Assert the UTF-8 BOM and parse the CSV text into rows."""
    assert text.startswith("\ufeff")
    body = text.encode("utf-8")
    assert body.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))


def _ok_row() -> dict:
    """An "ok" smooth recommendation with every nested object present."""
    return {
        "item_code": "ITM-001",
        "site_code": "SITE-NORD",
        "as_of": AS_OF,
        "status": "ok",
        "demand_class": "smooth",
        "method": "z-sigma",
        "notes": [],
        "demand_basis": "history",
        "other_basis": None,
        "safety_stock": 25.31,
        "reorder_point": 145.31,
        "safety_stock_units": 26,
        "reorder_point_units": 146,
        "inputs": {
            "d": 10.0,
            "sd_d": 2.0,
            "lt": 12.0,
            "sd_lt": 1.5,
            "z": 1.6448536269514722,
            "service_level": 0.95,
        },
        "arithmetic": (
            "safety stock = 1.6448536269514722 * sqrt(12.0 * 2.0^2 "
            "+ 10.0^2 * 1.5^2) = 25.31; "
            "reorder point = 10.0 * 12.0 + 25.31 = 145.31"
        ),
        "current": {"reorder_point": 120.0, "safety_stock": 15.0},
        "delta_reorder_point": -25.31,
        "on_hand_units": 40,
        "on_order_units": 0,
        "unit_cost": 4.25,
        "suggested_order_quantity": 106,
        "order_quantity": {
            "basis": "eoq",
            "clamped": None,
            "order_quantity": 120.0,
            "order_quantity_units": 120,
            "cover_days": 12.0,
            "annual_demand": 3650.0,
            "order_cost": 25.0,
            "holding_rate": 0.25,
            "unit_cost": 4.25,
            "arithmetic": (
                "eoq = sqrt(2 * 3650.0 * 25.0 / (4.25 * 0.25)) = 186.30 "
                "clamped to 12.00 days = 120.00"
            ),
            "recommended_max_units": 266,
            "current_order_qty": 100.0,
            "current_max_qty": 200.0,
        },
        "empirical": {
            "method": "empirical",
            "window_days": 12,
            "spans": 150,
            "spans_with_demand": 141,
            "service_level": 0.95,
            "reorder_point": 140.0,
            "reorder_point_units": 140,
            "mean_lead_time_demand": 120.0,
            "safety_stock": 20.0,
            "safety_stock_units": 20,
            "arithmetic": (
                "empirical reorder point = 0.95 quantile of 150 lead-time "
                "spans = 140.0; safety stock = max(0, 140.0 - 120.0) = 20.0"
            ),
        },
        "service_level_source": {"abc_class": "A", "source": "class"},
    }


def _intermittent_row() -> dict:
    """A not-recommendable intermittent row with a non-null empirical."""
    return {
        "item_code": "ITM-002",
        "site_code": "SITE-NORD",
        "as_of": AS_OF,
        "status": "not_recommendable_demand",
        "demand_class": "intermittent",
        "method": "z-sigma",
        "notes": ["intermittent demand"],
        "demand_basis": "history",
        "other_basis": None,
        "safety_stock": None,
        "reorder_point": None,
        "safety_stock_units": None,
        "reorder_point_units": None,
        "inputs": None,
        "arithmetic": None,
        "current": {"reorder_point": None, "safety_stock": None},
        "delta_reorder_point": None,
        "on_hand_units": 7,
        "on_order_units": 0,
        "unit_cost": 1.5,
        "suggested_order_quantity": None,
        "order_quantity": None,
        "empirical": {
            "method": "empirical",
            "window_days": 12,
            "spans": 150,
            "spans_with_demand": 30,
            "service_level": 0.95,
            "reorder_point": 12.0,
            "reorder_point_units": 12,
            "mean_lead_time_demand": 8.0,
            "safety_stock": 4.0,
            "safety_stock_units": 4,
            "arithmetic": (
                "empirical reorder point = 0.95 quantile of 150 lead-time "
                "spans = 12.0; safety stock = max(0, 12.0 - 8.0) = 4.0"
            ),
        },
        "service_level_source": {"abc_class": None, "source": "single"},
    }


def _formula_row() -> dict:
    """An ok row whose item_code looks like an Excel formula."""
    row = _ok_row()
    row.update(
        {
            "item_code": "=SUM(A1)",
            "site_code": "SITE-SUD",
        }
    )
    row["service_level_source"] = {"abc_class": "B", "source": "single"}
    row["reorder_point_units"] = 80
    row["safety_stock_units"] = 9
    row["inputs"] = {
        "d": 5.0,
        "sd_d": 1.0,
        "lt": 10.0,
        "sd_lt": 0.5,
        "z": 1.2815515655446004,
        "service_level": 0.9,
    }
    row["current"] = {"reorder_point": 75.0, "safety_stock": 8.0}
    row["unit_cost"] = 0.1
    row["order_quantity"] = {
        "basis": "assumed_30_days",
        "clamped": None,
        "order_quantity": 150.0,
        "order_quantity_units": 150,
        "cover_days": 30.0,
        "annual_demand": 1825.0,
        "order_cost": 25.0,
        "holding_rate": 0.25,
        "unit_cost": None,
        "arithmetic": "30 days of demand = 30 * 5.0 = 150.0",
        "recommended_max_units": 230,
        "current_order_qty": None,
        "current_max_qty": 90.0,
    }
    row["empirical"] = {
        "method": "empirical",
        "window_days": 10,
        "spans": 120,
        "spans_with_demand": 60,
        "service_level": 0.9,
        "reorder_point": 78.0,
        "reorder_point_units": 78,
        "mean_lead_time_demand": 71.0,
        "safety_stock": 7.0,
        "safety_stock_units": 7,
        "arithmetic": (
            "empirical reorder point = 0.90 quantile of 120 lead-time "
            "spans = 78.0; safety stock = max(0, 78.0 - 71.0) = 7.0"
        ),
    }
    return row


def test_issue693_freeform(client, monkeypatch):
    rows = [_ok_row(), _intermittent_row(), _formula_row()]

    def fake_recommendations_for_all(as_of):
        assert as_of == AS_OF
        return rows

    # The route resolves its rows through the name it imported.
    monkeypatch.setattr(
        "app.api.recommendations.recommendations_for_all",
        fake_recommendations_for_all,
    )

    # --- the export response -------------------------------------------
    params = {"as_of": AS_OF.isoformat()}
    response = client.get("/api/recommendations/export.csv", params=params)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert (
        response.headers["content-disposition"]
        == 'attachment; filename="planning-parameters-2026-09-30.csv"'
    )

    parsed = _parse(response.text)

    # --- the header row: exactly the 18 columns in order ----------------
    assert parsed[0] == HEADERS
    assert len(parsed[0]) == 18
    # One row per result of recommendations_for_all, in order, no limit.
    assert len(parsed) == 1 + len(rows)

    # --- the ok row: every cell is the expected value -------------------
    ok = parsed[1]
    assert ok == [
        "ITM-001",
        "SITE-NORD",
        "2026-09-30",
        "ok",
        "smooth",
        "A",
        "0.95",
        "146",
        "26",
        "120",
        "266",
        "120.0",
        "15.0",
        "100.0",
        "200.0",
        "140",
        "20",
        "4.25",
    ]

    # --- the intermittent row: empty recommendations, empirical filled --
    intermittent = parsed[2]
    assert intermittent[HEADERS.index("item_code")] == "ITM-002"
    assert intermittent[HEADERS.index("status")] == "not_recommendable_demand"
    assert intermittent[HEADERS.index("demand_class")] == "intermittent"
    assert intermittent[HEADERS.index("abc_class")] == ""
    assert intermittent[HEADERS.index("service_level")] == ""
    assert intermittent[HEADERS.index("current_reorder_point")] == ""
    assert intermittent[HEADERS.index("current_safety_stock")] == ""
    for column in (
        "recommended_reorder_point_units",
        "recommended_safety_stock_units",
        "recommended_order_quantity_units",
        "recommended_max_units",
        "current_order_qty",
        "current_max_qty",
    ):
        assert intermittent[HEADERS.index(column)] == "", column
    assert intermittent[HEADERS.index("empirical_reorder_point_units")] == "12"
    assert intermittent[HEADERS.index("empirical_safety_stock_units")] == "4"
    assert intermittent[HEADERS.index("unit_cost")] == "1.50"

    # --- the formula guard: a leading '=' is neutralized ----------------
    assert parsed[3][HEADERS.index("item_code")] == "'=SUM(A1)"

    # --- no token: 401 ---------------------------------------------------
    with TestClient(app) as unauthenticated:
        unauthenticated_response = unauthenticated.get(
            "/api/recommendations/export.csv", params=params
        )
    assert unauthenticated_response.status_code == 401

    # --- the schema file lists the new path ------------------------------
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "openapi.json").read_text(
            encoding="utf-8"
        )
    )
    assert "/api/recommendations/export.csv" in schema["paths"]
