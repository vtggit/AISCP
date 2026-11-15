"""Issue #1100 proof: the expected fill rate of the service level curve.

The proof replaces ``recommendations_for_all`` in
``app.recommend.service_curve`` with a stand-in answering fixed rows,
the way ``backend/tests/test_issue1096_freeform.py`` does, and reads
``GET /api/service-levels/curve`` through the app's test client.  The
rows are three ``ok`` item-sites with ``unit_cost`` 10,
``inputs.sd_lt`` 0, ``inputs.lt`` 4 and ``inputs.sd_d`` 5 (so each
sigma is ``sqrt(4 * 5^2 + d^2 * 0^2)`` = 10) and ``inputs.d`` 100,
10 and 1, which classify A, B and C.  Class A's row carries an
``order_quantity`` whose ``order_quantity`` is 100, so it takes part
in the fill rate; class B's row carries a null ``order_quantity``
(class C's row does too), so neither takes part.  The checks:

* Class A — ``fill_rate_item_sites`` 1; ``expected_fill_rate`` 0.9979
  at 0.95 and 0.9953 at 0.90 — 1 minus sigma times G(z) times the
  yearly demand D (365 times ``inputs.d``) over Q, where G(z) is the
  standard normal loss function, the standard normal density at z
  minus z times one minus the standard normal distribution at z —
  rising from each point to the next; its safety stock figures are
  as before (17 units, 164.49 value and 41.12 holding at 0.95).
* No quantity — class B has ``fill_rate_item_sites`` 0 and a null
  ``expected_fill_rate`` at every point, while its safety stock
  figures are as before.
* Weighted — with a second class A row that has ``inputs.d`` 100,
  sigma 10 and an ``order_quantity.order_quantity`` of 50, class A's
  ``fill_rate_item_sites`` is 2 and its ``expected_fill_rate`` at
  0.95 is 0.9969 — 1 minus (0.20893 x 36500 / 100 + 0.20893 x
  36500 / 50) / 73000, rounded.
* Overflow — with the stand-in's single taking-part row whose sigma
  times G(z) times D over Q overflows (``inputs.d`` 1e154,
  ``inputs.sd_d`` 1.3e154 and ``inputs.lt`` 1, so sigma 1.3e154 and
  the yearly demand 365 x 1e154, at an
  ``order_quantity.order_quantity`` of 1e-200), the
  ``expected_fill_rate`` is the clamped number 0.0 at every point,
  never null.
* No new read — the stand-in is called exactly once per request: the
  curve uses the one recommendations read it already makes.
* Schema — ``backend/openapi.json`` carries the new point and class
  fields.
"""

import json
from pathlib import Path

import app.recommend.service_curve as service_curve

URL = "/api/service-levels/curve"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

ITEM_A = "A-ITEM"
ITEM_A2 = "A2-ITEM"
ITEM_B = "B-ITEM"
ITEM_C = "C-ITEM"
ITEM_OVERFLOW = "OV-ITEM"
SITE = "SITE-1"

LADDER = [0.90, 0.92, 0.95, 0.97, 0.98, 0.99, 0.995]


def _row(item_code, d, order_quantity=None, sd_d=5.0, lt=4.0, sd_lt=0.0):
    """One fixed recommendation row: sigma 10, unit cost 10, by
    default."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "as_of": None,
        "status": "ok",
        "unit_cost": 10.0,
        "inputs": {"d": d, "sd_d": sd_d, "lt": lt, "sd_lt": sd_lt},
        "order_quantity": order_quantity,
    }


class _RecommendationsStandIn:
    """The stand-in for ``recommendations_for_all`` in
    ``app.recommend.service_curve``: every call is counted, the fixed
    rows are handed back unchanged."""

    def __init__(self):
        self.calls = 0
        self.rows = [
            _row(ITEM_A, 100.0, order_quantity={"order_quantity": 100.0}),
            _row(ITEM_B, 10.0, order_quantity=None),
            _row(ITEM_C, 1.0, order_quantity=None),
        ]

    def __call__(self, as_of=None):
        self.calls += 1
        return [dict(row) for row in self.rows]


def _classes_by_name(body):
    return {entry["abc_class"]: entry for entry in body["classes"]}


def test_issue1100_freeform(client, monkeypatch) -> None:
    # The defaults the proof asserts (0.95, 0.25) hold only when the
    # environment carries no override of its own.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    stand_in = _RecommendationsStandIn()
    monkeypatch.setattr(service_curve, "recommendations_for_all", stand_in)

    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [entry["abc_class"] for entry in body["classes"]] == ["A", "B", "C"]
    classes = _classes_by_name(body)

    # --- Class A: one taking-part row (the one whose
    # order_quantity.order_quantity is a finite number above 0);
    # 0.9979 at 0.95 and 0.9953 at 0.90 — 1 - 10 x G(z) x 36500 /
    # 100 over 36500 — rising from each point to the next.
    class_a = classes["A"]
    assert class_a["item_sites"] == 1
    assert class_a["fill_rate_item_sites"] == 1
    points_a = class_a["points"]
    assert [point["level"] for point in points_a] == LADDER
    point_95 = next(point for point in points_a if point["level"] == 0.95)
    point_90 = next(point for point in points_a if point["level"] == 0.90)
    assert point_95["expected_fill_rate"] == 0.9979
    assert point_90["expected_fill_rate"] == 0.9953
    rates = [point["expected_fill_rate"] for point in points_a]
    assert all(rate is not None for rate in rates)
    assert all(later > earlier for earlier, later in zip(rates, rates[1:])), rates
    # The other fields of the point are unchanged.
    assert point_95["safety_stock_units"] == 17
    assert point_95["safety_stock_value"] == 164.49
    assert point_95["holding_cost"] == 41.12
    assert point_95["current"] is True

    # --- No quantity: class B takes part nowhere — a null
    # expected_fill_rate at every point — while its safety stock
    # figures are as before.
    class_b = classes["B"]
    assert class_b["item_sites"] == 1
    assert class_b["fill_rate_item_sites"] == 0
    for point in class_b["points"]:
        assert point["expected_fill_rate"] is None
    point_b95 = next(point for point in class_b["points"] if point["level"] == 0.95)
    assert point_b95["safety_stock_units"] == 17
    assert point_b95["safety_stock_value"] == 164.49
    assert point_b95["holding_cost"] == 41.12
    # Class C's row carries a null order quantity too: no taking-part
    # row, a null fill rate at every point, the safety stock as
    # before.
    class_c = classes["C"]
    assert class_c["fill_rate_item_sites"] == 0
    for point in class_c["points"]:
        assert point["expected_fill_rate"] is None
    point_c95 = next(point for point in class_c["points"] if point["level"] == 0.95)
    assert point_c95["safety_stock_units"] == 17
    assert point_c95["safety_stock_value"] == 164.49
    assert point_c95["holding_cost"] == 41.12

    # --- No new read: the stand-in is called exactly once per
    # request.
    assert stand_in.calls == 1, "recommendations_for_all is read once per request"

    # --- Weighted: a second class A row (d 100, sigma 10, Q 50) —
    # the rate is weighted by the yearly demand: 1 minus (0.20893 x
    # 36500 / 100 + 0.20893 x 36500 / 50) / 73000, rounded to 4
    # places.
    stand_in.rows.append(_row(ITEM_A2, 100.0, order_quantity={"order_quantity": 50.0}))
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    classes = _classes_by_name(body)
    class_a = classes["A"]
    assert class_a["item_sites"] == 2
    assert class_a["fill_rate_item_sites"] == 2
    point_95 = next(point for point in class_a["points"] if point["level"] == 0.95)
    assert point_95["expected_fill_rate"] == 0.9969
    # The other fields of the point move with the new row as before:
    # 1.6449 x 10 rounded up to a whole number, twice, at unit cost
    # 10.
    assert point_95["safety_stock_units"] == 34
    assert point_95["safety_stock_value"] == 328.97
    # The other classes are untouched by the new row.
    assert classes["B"]["fill_rate_item_sites"] == 0
    assert classes["C"]["fill_rate_item_sites"] == 0

    # The stand-in is called exactly once per request, still.
    assert stand_in.calls == 2, "recommendations_for_all is read once per request"

    # --- Overflow: the stand-in's single taking-part row is one
    # whose sigma times G(z) times D over Q does not stay finite
    # (sigma 1.3e154 from d 1e154, sd_d 1.3e154 and lt 1; the yearly
    # demand 365 * 1e154; the order quantity 1e-200) — the rate is
    # the clamped number 0.0 at every point, never null.
    stand_in.rows = [
        _row(
            ITEM_OVERFLOW,
            1e154,
            order_quantity={"order_quantity": 1e-200},
            sd_d=1.3e154,
            lt=1.0,
        )
    ]
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    classes = _classes_by_name(body)
    class_a = classes["A"]
    assert class_a["item_sites"] == 1
    assert class_a["fill_rate_item_sites"] == 1
    assert all(point["expected_fill_rate"] == 0.0 for point in class_a["points"]), [
        point["expected_fill_rate"] for point in class_a["points"]
    ]
    # The other classes take part nowhere: null rates, as before.
    assert classes["B"]["fill_rate_item_sites"] == 0
    assert classes["C"]["fill_rate_item_sites"] == 0
    for cls in ("B", "C"):
        assert all(
            point["expected_fill_rate"] is None for point in classes[cls]["points"]
        )

    # The stand-in is called exactly once per request, still.
    assert stand_in.calls == 3, "recommendations_for_all is read once per request"

    # --- Schema: backend/openapi.json carries the new fields.
    spec = json.loads(OPENAPI.read_text())
    assert "/api/service-levels/curve" in spec["paths"]
    schemas = spec["components"]["schemas"]
    point = schemas["ServiceLevelPoint"]["properties"]
    assert point["expected_fill_rate"]["anyOf"] == [
        {"type": "number"},
        {"type": "null"},
    ]
    assert "expected_fill_rate" not in schemas["ServiceLevelPoint"]["required"]
    assert schemas["ServiceLevelClass"]["properties"]["fill_rate_item_sites"] == {
        "type": "integer",
        "title": "Fill Rate Item Sites",
        "default": 0,
    }
    assert "fill_rate_item_sites" not in schemas["ServiceLevelClass"]["required"]
