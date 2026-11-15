"""Issue #1096 proof: the service level curve of the ABC classes.

The proof replaces ``recommendations_for_all`` in
``app.recommend.service_curve`` with a stand-in answering fixed rows
and reads ``GET /api/service-levels/curve`` through the app's test
client.  The rows are three ``ok`` item-sites with ``unit_cost`` 10,
``inputs.sd_lt`` 0, ``inputs.lt`` 4 and ``inputs.sd_d`` 5, so each
sigma is ``sqrt(4 * 5^2 + d^2 * 0^2)`` = 10; their ``inputs.d`` values
are 100, 10 and 1, so their annual values 365000, 36500 and 3650
classify A, B and C.  A fourth row has ``status``
``insufficient_history``.  The checks:

* Classes — three classes ``A``, ``B``, ``C`` in that order, each
  with ``item_sites`` 1, ``by_class`` false and ``current_level``
  0.95, the default single level, at the default holding rate 0.25.
* A point — class A at 0.95 has ``safety_stock_units`` 17 (1.6449
  times 10, rounded up to a whole number), ``safety_stock_value``
  164.49, ``holding_cost`` 41.12 and ``current`` true; no other point
  of class A is current.
* The ladder — class A's points are at 0.90, 0.92, 0.95, 0.97, 0.98,
  0.99 and 0.995 in that order, the first ``holding_cost_delta`` null
  and every other one positive.
* By class — with ``replenishment.service_level_by_class`` stored
  true and ``replenishment.service_level_a`` stored 0.96, class A's
  ``current_level`` is 0.96; its points then include 0.96, between
  0.95 and 0.97, and that point is current.  Class C's
  ``current_level`` is 0.90, its default.
* Excluded — the fourth row counts in no class.
* as_of — the first counted row carries a null as_of; later counted
  rows carry 2026-11-09, and the answer's as_of is the first counted
  row's (null), never a later row's.
* Numeric strings — a row whose inputs are numeric strings, and a
  row whose unit_cost is a numeric string, count in no class.
* Once — the stand-in is called exactly once per request.
* Gate and schema — an anonymous call gets 401 (the same gate as
  ``GET /api/abc``) and ``backend/openapi.json`` carries the route
  with its models.
"""

import json
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

import app.recommend.service_curve as service_curve
from app.db.connection import get_connection
from app.main import app

URL = "/api/service-levels/curve"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

AS_OF = date(2026, 11, 9)
ITEM_A = "A-ITEM"
ITEM_B = "B-ITEM"
ITEM_C = "C-ITEM"
ITEM_X = "X-ITEM"
ITEM_STR_INPUTS = "SI-ITEM"
ITEM_STR_COST = "SC-ITEM"
SITE = "SITE-1"

SETTING_SQL = (
    "INSERT INTO settings (scope, owner, key, value, updated_by) "
    "VALUES ('system', '', %s, %s::jsonb, 'test')"
)

LADDER = [0.90, 0.92, 0.95, 0.97, 0.98, 0.99, 0.995]


def _row(item_code, d, status="ok", as_of=AS_OF):
    """One fixed recommendation row: sigma 10, unit cost 10."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "as_of": as_of,
        "status": status,
        "unit_cost": 10.0,
        "inputs": {"d": d, "sd_d": 5.0, "lt": 4.0, "sd_lt": 0.0},
    }


class _RecommendationsStandIn:
    """The stand-in for ``recommendations_for_all`` in
    ``app.recommend.service_curve``: every call is counted, the fixed
    rows are handed back unchanged."""

    def __init__(self):
        self.calls = 0
        self.rows = [
            _row(ITEM_A, 100.0, as_of=None),
            _row(ITEM_B, 10.0),
            _row(ITEM_C, 1.0),
            _row(ITEM_X, 1.0, status="insufficient_history"),
            # A numeric string is not a finite number: neither of
            # these rows counts, in any class.
            {
                "item_code": ITEM_STR_INPUTS,
                "site_code": SITE,
                "as_of": AS_OF,
                "status": "ok",
                "unit_cost": 10.0,
                "inputs": {"d": "0.1", "sd_d": "5.0", "lt": "4.0", "sd_lt": "0.0"},
            },
            {
                "item_code": ITEM_STR_COST,
                "site_code": SITE,
                "as_of": AS_OF,
                "status": "ok",
                "unit_cost": "10.0",
                "inputs": {"d": 0.2, "sd_d": 5.0, "lt": 4.0, "sd_lt": 0.0},
            },
        ]

    def __call__(self, as_of=None):
        self.calls += 1
        return [dict(row) for row in self.rows]


def _classes_by_name(body):
    return {entry["abc_class"]: entry for entry in body["classes"]}


def test_issue1096_freeform(client, monkeypatch) -> None:
    # The defaults the proof asserts (0.95, 0.25) hold only when the
    # environment carries no override of its own.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    stand_in = _RecommendationsStandIn()
    monkeypatch.setattr(service_curve, "recommendations_for_all", stand_in)

    # --- Classes: A, B, C in order, one site each, the default
    # single level 0.95, the default holding rate 0.25.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    # as_of — the first counted row (class A) carries a null as_of
    # while the later counted rows carry 2026-11-09: the answer is the
    # first counted row's as_of, null, and never falls through to a
    # later row's.
    assert body["as_of"] is None
    assert body["by_class"] is False
    assert body["holding_rate"] == 0.25
    assert [entry["abc_class"] for entry in body["classes"]] == ["A", "B", "C"]
    classes = _classes_by_name(body)
    for entry in body["classes"]:
        assert entry["item_sites"] == 1
        assert entry["current_level"] == 0.95

    # --- A point: class A at 0.95 — 1.6449 x 10 rounded up to a
    # whole number, the value at unit cost 10, the holding at 0.25.
    class_a = classes["A"]
    assert [point["level"] for point in class_a["points"]] == LADDER
    point_95 = next(point for point in class_a["points"] if point["level"] == 0.95)
    assert point_95["safety_stock_units"] == 17
    assert point_95["safety_stock_value"] == 164.49
    assert point_95["holding_cost"] == 41.12
    assert point_95["current"] is True
    # No other point of class A is current.
    assert [point["current"] for point in class_a["points"]] == [
        False,
        False,
        True,
        False,
        False,
        False,
        False,
    ]

    # --- The ladder: the first holding_cost_delta is null, every
    # other one is positive (holding costs grow with the level).
    deltas = [point["holding_cost_delta"] for point in class_a["points"]]
    assert deltas[0] is None
    assert all(delta > 0 for delta in deltas[1:]), deltas

    # --- Excluded: the fourth row (insufficient_history) counts in
    # no class, and neither do the numeric-string rows (string
    # inputs, string unit_cost) — the three classes hold exactly the
    # three ok rows whose numbers are real numbers.
    assert sum(entry["item_sites"] for entry in body["classes"]) == 3
    for entry in body["classes"]:
        assert entry["item_sites"] == 1

    # --- Once: the stand-in is called exactly once per request.
    assert stand_in.calls == 1, "recommendations_for_all is read once per request"

    # --- By class: the switch on and class A's level stored 0.96.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                SETTING_SQL,
                ("replenishment.service_level_by_class", json.dumps(True)),
            )
            cur.execute(
                SETTING_SQL, ("replenishment.service_level_a", json.dumps(0.96))
            )
        conn.commit()
    finally:
        conn.close()

    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["by_class"] is True
    classes = _classes_by_name(body)

    # Class A: its level is the stored 0.96 — the ladder gains 0.96
    # between 0.95 and 0.97, and that point is the current one.
    class_a = classes["A"]
    assert class_a["current_level"] == 0.96
    levels_a = [point["level"] for point in class_a["points"]]
    assert levels_a == [0.90, 0.92, 0.95, 0.96, 0.97, 0.98, 0.99, 0.995]
    point_96 = next(point for point in class_a["points"] if point["level"] == 0.96)
    assert point_96["current"] is True
    assert [point["current"] for point in class_a["points"]] == [
        False,
        False,
        False,
        True,
        False,
        False,
        False,
        False,
    ]

    # Class C keeps its default single-class level 0.90: the 0.90
    # point of the ladder is its current one.
    class_c = classes["C"]
    assert class_c["current_level"] == 0.90
    point_c = next(point for point in class_c["points"] if point["level"] == 0.90)
    assert point_c["current"] is True
    assert sum(point["current"] for point in class_c["points"]) == 1

    # The stand-in is called exactly once per request, still.
    assert stand_in.calls == 2, "recommendations_for_all is read once per request"

    # --- Gate: the route answers behind the same auth gate as
    # GET /api/abc — an anonymous call gets 401.
    anon = TestClient(app)
    response = anon.get(URL)
    assert response.status_code == 401
    assert anon.get("/api/abc").status_code == 401
    # The gate is decided before the read: no extra call happened.
    assert stand_in.calls == 2

    # --- Schema: backend/openapi.json carries the route and its
    # models.
    spec = json.loads(OPENAPI.read_text())
    assert "/api/service-levels/curve" in spec["paths"]
    operation = spec["paths"]["/api/service-levels/curve"]["get"]
    schema_ref = operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    assert schema_ref.endswith("#/components/schemas/ServiceLevelCurveResponse")
    schemas = spec["components"]["schemas"]
    assert set(schemas["ServiceLevelCurveResponse"]["properties"]) == {
        "as_of",
        "by_class",
        "service_target",
        "holding_rate",
        "classes",
    }
    assert set(schemas["ServiceLevelClass"]["properties"]) == {
        "abc_class",
        "item_sites",
        "fill_rate_item_sites",
        "current_level",
        "points",
    }
    assert set(schemas["ServiceLevelPoint"]["properties"]) == {
        "level",
        "safety_stock_units",
        "safety_stock_value",
        "holding_cost",
        "holding_cost_delta",
        "expected_fill_rate",
        "current",
    }
