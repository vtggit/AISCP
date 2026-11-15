"""Issue #697 proof: the dollar impact of the empirical reorder points.

``GET /api/recommendations/intermittent-impact`` behind the same
authentication as ``GET /api/recommendations`` (401 without a token),
with the same optional ``as_of`` date query parameter, used exactly
the way that route uses it (passed straight to
``recommendations_for_all``), and the same FastAPI 422 validation
error for an invalid one.  The route reads through
``recommendations_for_all(as_of)`` and writes nothing.

One row per result whose ``demand_class`` is ``"intermittent"`` or
``"lumpy"`` and whose ``empirical`` is not null, ordered by
``item_code`` then ``site_code``, with ``item_code``, ``site_code``,
``demand_class``, ``unit_cost``, ``current_reorder_point`` (the
result's ``current.reorder_point`` or null),
``empirical_reorder_point_units`` (the
``empirical.reorder_point_units``), ``reorder_point_units_delta``
(the empirical units minus the current reorder point, a null current
counting as 0) and ``dollar_delta`` (that delta times the unit cost,
null when the unit cost is absent or not positive).  The summary
carries ``item_sites`` (the row count), ``current_value`` and
``empirical_value`` (the sums of current reorder point times unit
cost and of empirical units times unit cost over the rows with a
positive unit cost, each rounded to cents), ``dollar_delta``
(``empirical_value`` minus ``current_value``),
``item_sites_without_unit_cost`` (the rows whose unit cost is
absent, zero or negative, counted together) and ``switch_on`` (the
stored or default value of
``replenishment.intermittent_recommendations``).  The response is
``{"summary": {...}, "item_sites": [...]}`` whether the switch is on
or off: the rows describe what the switch does, not whether it is on.

A unit cost carried as a ``Decimal`` (the shape a database numeric
column returns) is a positive unit cost: its row joins the money
sums, is not counted in ``item_sites_without_unit_cost``, and its
per-row ``dollar_delta`` is the exact delta times the cost — not
rounded to cents — while a negative ``Decimal`` is still excluded
and counted.

With ``recommendations_for_all`` monkeypatched where the route
imports it to five results — a lumpy one with 60 empirical units,
current reorder point 0 and unit cost 2.0; an intermittent one with
30 units, current reorder point 75 and unit cost 532.05; an
intermittent one with ``empirical`` null; a smooth "ok" one; and a
lumpy one with 5 units, a null current reorder point and no unit
cost — the three empirical item-sites come back in item_code /
site_code order: the 60-unit row reads ``reorder_point_units_delta``
60 and ``dollar_delta`` 120.0, the 30-unit row reads -45 and
-23942.25, the no-cost row reads ``dollar_delta`` null.  The summary
reads ``item_sites`` 3, ``current_value`` 39903.75,
``empirical_value`` 16081.5, ``dollar_delta`` -23822.25,
``item_sites_without_unit_cost`` 1 and ``switch_on`` false.  With the
switch stored as true through
``PUT /api/settings/replenishment.intermittent_recommendations`` as
an admin, ``switch_on`` reads true and the rows are unchanged.  A
second scenario proves unit costs of zero and negative are excluded
from the money sums and counted in
``item_sites_without_unit_cost``.  A request without a token answers
401, an invalid ``as_of`` answers a 422 validation error consistent
with ``GET /api/recommendations``, and ``backend/openapi.json``
lists the new path with its three response models.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

AS_OF = date(2026, 9, 30)
IMPACT_PATH = "/api/recommendations/intermittent-impact"


def _empirical(units: int) -> dict:
    """A plausible ``empirical`` result with ``reorder_point_units``."""
    return {
        "method": "empirical",
        "window_days": 12,
        "spans": 150,
        "spans_with_demand": 80,
        "service_level": 0.95,
        "reorder_point": float(units),
        "reorder_point_units": units,
        "mean_lead_time_demand": max(0.0, float(units) - 5.0),
        "safety_stock": 5.0,
        "safety_stock_units": 5,
        "arithmetic": (
            f"empirical reorder point = 0.95 quantile of 150 "
            f"lead-time spans = {float(units)}"
        ),
    }


def _result(
    item_code: str,
    site_code: str,
    demand_class: str,
    status: str,
    empirical: dict | None,
    current_reorder_point: float | None,
    unit_cost: float | None,
) -> dict:
    """One ``recommendations_for_all`` result, as the route reads it."""
    return {
        "item_code": item_code,
        "site_code": site_code,
        "as_of": AS_OF,
        "status": status,
        "demand_class": demand_class,
        "method": "z-sigma",
        "notes": [],
        "current": {
            "reorder_point": current_reorder_point,
            "safety_stock": None,
        },
        "unit_cost": unit_cost,
        "empirical": empirical,
    }


# The five results of the pinned scenario, deliberately not in
# item_code / site_code order: the route must order its rows itself.
ROWS = [
    # Smooth "ok": no row, whatever its empirical (null here).
    _result("I-200", "S-01", "smooth", "ok", None, 40.0, 9.5),
    # Lumpy, 5 units, no unit cost: a row with dollar_delta null.
    _result(
        "I-300",
        "S-01",
        "lumpy",
        "not_recommendable_demand",
        _empirical(5),
        None,
        None,
    ),
    # Intermittent, empirical null: no row.
    _result(
        "I-250",
        "S-01",
        "intermittent",
        "not_recommendable_demand",
        None,
        10.0,
        1.25,
    ),
    # Lumpy, 60 units, current 0, cost 2.0: delta 60, dollars 120.0.
    _result(
        "I-100",
        "S-01",
        "lumpy",
        "not_recommendable_demand",
        _empirical(60),
        0.0,
        2.0,
    ),
    # Intermittent, 30 units, current 75, cost 532.05: delta -45.
    _result(
        "I-200",
        "S-02",
        "intermittent",
        "not_recommendable_demand",
        _empirical(30),
        75.0,
        532.05,
    ),
]

EXPECTED_ROWS = [
    {
        "item_code": "I-100",
        "site_code": "S-01",
        "demand_class": "lumpy",
        "unit_cost": 2.0,
        "current_reorder_point": 0.0,
        "empirical_reorder_point_units": 60,
        "reorder_point_units_delta": 60,
        "dollar_delta": 120.0,
    },
    {
        "item_code": "I-200",
        "site_code": "S-02",
        "demand_class": "intermittent",
        "unit_cost": 532.05,
        "current_reorder_point": 75.0,
        "empirical_reorder_point_units": 30,
        "reorder_point_units_delta": -45,
        "dollar_delta": -23942.25,
    },
    {
        "item_code": "I-300",
        "site_code": "S-01",
        "demand_class": "lumpy",
        "unit_cost": None,
        "current_reorder_point": None,
        "empirical_reorder_point_units": 5,
        "reorder_point_units_delta": 5,
        "dollar_delta": None,
    },
]


def test_issue697_freeform(client, monkeypatch, admin_token):
    seen_as_of: list = []
    rows_holder: list = [ROWS]

    def fake_recommendations_for_all(as_of):
        seen_as_of.append(as_of)
        return rows_holder[0]

    # The route resolves its rows through the name it imported.
    monkeypatch.setattr(
        "app.api.recommendations.recommendations_for_all",
        fake_recommendations_for_all,
    )

    # --- the pinned scenario, switch off by default ---------------------
    response = client.get(IMPACT_PATH, params={"as_of": AS_OF.isoformat()})
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"summary", "item_sites"}
    assert body["item_sites"] == EXPECTED_ROWS
    assert body["summary"] == {
        "item_sites": 3,
        "current_value": 39903.75,
        "empirical_value": 16081.5,
        "dollar_delta": -23822.25,
        "item_sites_without_unit_cost": 1,
        "switch_on": False,
    }

    # The optional as_of is used the way GET /api/recommendations uses
    # it: passed straight to recommendations_for_all, None when absent.
    without_as_of = client.get(IMPACT_PATH)
    assert without_as_of.status_code == 200, without_as_of.text
    assert without_as_of.json() == body
    assert seen_as_of == [AS_OF, None]

    # --- the switch on: stored as true through the settings API ---------
    put = client.put(
        "/api/settings/replenishment.intermittent_recommendations",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"value": True},
    )
    assert put.status_code == 200, put.text

    switched = client.get(IMPACT_PATH, params={"as_of": AS_OF.isoformat()})
    assert switched.status_code == 200, switched.text
    switched_body = switched.json()
    # The rows describe what the switch does, not whether it is on.
    assert switched_body["item_sites"] == body["item_sites"]
    assert switched_body["summary"] == {
        **body["summary"],
        "switch_on": True,
    }

    # --- unit costs of zero and negative are excluded and counted -------
    rows_holder[0] = [
        _result(
            "I-400",
            "S-01",
            "intermittent",
            "not_recommendable_demand",
            _empirical(10),
            20.0,
            0.0,
        ),
        _result(
            "I-401",
            "S-01",
            "lumpy",
            "not_recommendable_demand",
            _empirical(20),
            5.0,
            -1.5,
        ),
        _result(
            "I-402",
            "S-01",
            "intermittent",
            "not_recommendable_demand",
            _empirical(30),
            None,
            None,
        ),
        _result(
            "I-403",
            "S-01",
            "intermittent",
            "not_recommendable_demand",
            _empirical(40),
            10.0,
            2.5,
        ),
    ]
    scenario = client.get(IMPACT_PATH, params={"as_of": AS_OF.isoformat()})
    assert scenario.status_code == 200, scenario.text
    scenario_body = scenario.json()
    assert [row["item_code"] for row in scenario_body["item_sites"]] == [
        "I-400",
        "I-401",
        "I-402",
        "I-403",
    ]
    for row in scenario_body["item_sites"]:
        if row["item_code"] == "I-403":
            assert row["reorder_point_units_delta"] == 30
            assert row["dollar_delta"] == 75.0
        else:
            assert row["dollar_delta"] is None
    assert scenario_body["summary"] == {
        "item_sites": 4,
        "current_value": 25.0,
        "empirical_value": 100.0,
        "dollar_delta": 75.0,
        "item_sites_without_unit_cost": 3,
        "switch_on": True,
    }

    # --- a Decimal unit cost is money; per-row dollars are exact --------
    # A unit cost carried as a Decimal (the shape a database numeric
    # column returns) is a positive unit cost: its rows are in the
    # money sums, not in item_sites_without_unit_cost, and their
    # per-row dollar_delta is the exact delta times the cost — not
    # rounded to cents.  A negative Decimal is still excluded.
    rows_holder[0] = [
        _result(
            "I-600",
            "S-01",
            "intermittent",
            "not_recommendable_demand",
            _empirical(2),
            1.0,
            Decimal("1.125"),
        ),
        _result(
            "I-601",
            "S-01",
            "lumpy",
            "not_recommendable_demand",
            _empirical(3),
            0.0,
            Decimal("0.3125"),
        ),
        _result(
            "I-602",
            "S-01",
            "lumpy",
            "not_recommendable_demand",
            _empirical(5),
            None,
            Decimal("-2"),
        ),
        # Intermittent with empirical null: no row.
        _result(
            "I-603",
            "S-01",
            "intermittent",
            "not_recommendable_demand",
            None,
            0.0,
            Decimal("9.99"),
        ),
        # Smooth "ok": no row.
        _result("I-604", "S-01", "smooth", "ok", _empirical(8), 12.0, Decimal("0.5")),
    ]
    decimal_response = client.get(IMPACT_PATH, params={"as_of": AS_OF.isoformat()})
    assert decimal_response.status_code == 200, decimal_response.text
    decimal_body = decimal_response.json()
    assert decimal_body["item_sites"] == [
        {
            "item_code": "I-600",
            "site_code": "S-01",
            "demand_class": "intermittent",
            "unit_cost": 1.125,
            "current_reorder_point": 1.0,
            "empirical_reorder_point_units": 2,
            "reorder_point_units_delta": 1.0,
            # delta 1 x 1.125: the exact product (not 1.12, a cent
            # rounding) and not null (a Decimal cost is a positive
            # unit cost, unlike the int/float-only check).
            "dollar_delta": 1.125,
        },
        {
            "item_code": "I-601",
            "site_code": "S-01",
            "demand_class": "lumpy",
            "unit_cost": 0.3125,
            "current_reorder_point": 0.0,
            "empirical_reorder_point_units": 3,
            "reorder_point_units_delta": 3.0,
            # delta 3 x 0.3125: the exact product, not 0.94.
            "dollar_delta": 0.9375,
        },
        {
            "item_code": "I-602",
            "site_code": "S-01",
            "demand_class": "lumpy",
            "unit_cost": -2.0,
            "current_reorder_point": None,
            "empirical_reorder_point_units": 5,
            "reorder_point_units_delta": 5,
            "dollar_delta": None,
        },
    ]
    assert decimal_body["summary"] == {
        # The Decimal-cost rows join the money sums: 1 x 1.125 and
        # 0 x 0.3125 of current value, 2 x 1.125 and 3 x 0.3125 of
        # empirical value, each rounded to cents; only the negative
        # Decimal row counts as without unit cost.
        "item_sites": 3,
        "current_value": 1.12,
        "empirical_value": 3.19,
        "dollar_delta": 2.07,
        "item_sites_without_unit_cost": 1,
        "switch_on": True,
    }

    # --- no token: 401 ---------------------------------------------------
    with TestClient(app) as unauthenticated:
        unauthenticated_response = unauthenticated.get(
            IMPACT_PATH, params={"as_of": AS_OF.isoformat()}
        )
    assert unauthenticated_response.status_code == 401

    # --- invalid as_of: the same 422 validation error as the list --------
    for bad_as_of in ("not-a-date", "2026-02-30", "2026-13-01", ""):
        impact_error = client.get(IMPACT_PATH, params={"as_of": bad_as_of})
        list_error = client.get("/api/recommendations", params={"as_of": bad_as_of})
        assert impact_error.status_code == 422, bad_as_of
        assert list_error.status_code == 422, bad_as_of
        assert impact_error.json() == list_error.json(), bad_as_of

    # --- the schema file lists the new path and its models ---------------
    schema = json.loads(
        (Path(__file__).resolve().parents[1] / "openapi.json").read_text(
            encoding="utf-8"
        )
    )
    assert IMPACT_PATH in schema["paths"]
    for model in (
        "IntermittentImpactRow",
        "IntermittentImpactSummary",
        "IntermittentImpactResponse",
    ):
        assert model in schema["components"]["schemas"], model
