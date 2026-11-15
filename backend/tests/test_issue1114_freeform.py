"""Issue #1114 proof: the fill-rate z of the service level curve.

The proof replaces ``recommendations_for_all`` in
``app.recommend.service_curve`` with a stand-in answering one ``ok``
row, the way ``backend/tests/test_issue1107_freeform.py`` does, and
reads ``GET /api/service-levels/curve`` through the app's test
client.  The row is an ``ok`` item-site with ``unit_cost`` 10 and
``inputs`` ``d`` 100, ``sd_d`` 5, ``lt`` 4 and ``sd_lt`` 0 — its
sigma is ``sqrt(4 * 5^2 + 100^2 * 0^2)`` = 10 — and
``order_quantity`` ``{"order_quantity": 20}``, so its annual value
365000 classifies it A and it takes part in the fill rate with Q 20.
The checks:

* Cycle — with nothing stored, class A's point at 0.95 has
  ``safety_stock_units`` 17, ``safety_stock_value`` 164.49,
  ``holding_cost`` 41.12 and ``expected_fill_rate`` 0.9896: the
  taking-part row is computed with the level's z, as always.
* Fill rate — with ``replenishment.service_target`` stored
  ``fill_rate``, the same point has ``safety_stock_units`` 10,
  ``safety_stock_value`` 90.23, ``holding_cost`` 22.56 and
  ``expected_fill_rate`` 0.95: the taking-part row is computed with
  its own z at every level.
* Ladder — at every level of class A's ladder the fill-rate
  ``safety_stock_units`` is 10 times
  ``app.recommend.reorder._fill_rate_z(10, level, 20)``, rounded up.
* Back — stored ``cycle`` again, the answer equals the first one.
* A row that does not take part — with the stand-in answering the
  same row without an order quantity, the ``classes`` of the
  ``fill_rate`` answer equal the ``classes`` of the ``cycle`` answer.
* Keys — the key sets of the answer, of a class and of a point are
the same in both modes.
* Sigma overflow — a row whose ``d`` 1e200 and ``sd_lt`` 1e-100 make
  ``d * d`` overflow while the sigma's radicand is finite (1e200)
  still counts, with sigma 1e100.
* Value overflow — two rows whose value terms at 0.95 sum to more
  than the largest float answer 0.0 for ``safety_stock_units``,
  ``safety_stock_value`` and ``holding_cost`` at every level, never
  a non-finite value.
"""

import json
import math
import statistics

import app.recommend.service_curve as service_curve
from app.db.connection import get_connection
from app.recommend.reorder import _fill_rate_z

URL = "/api/service-levels/curve"

ITEM_A = "A-ITEM"
SITE = "SITE-1"

SETTING_SQL = (
    "INSERT INTO settings (scope, owner, key, value, updated_by) "
    "VALUES ('system', '', %s, %s::jsonb, 'test') "
    "ON CONFLICT (scope, owner, key) DO UPDATE SET value = EXCLUDED.value"
)


def _row(order_quantity=None, item_code=ITEM_A, unit_cost=10.0, inputs=None):
    """One recommendation row: the fixed sigma-10, unit-cost-10 row
    by default, overridable for the overflow proofs."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "as_of": None,
        "status": "ok",
        "unit_cost": unit_cost,
        "inputs": (
            inputs
            if inputs is not None
            else {"d": 100.0, "sd_d": 5.0, "lt": 4.0, "sd_lt": 0.0}
        ),
        "order_quantity": order_quantity,
    }


class _RecommendationsStandIn:
    """The stand-in for ``recommendations_for_all`` in
    ``app.recommend.service_curve``: every call is counted, the fixed
    row is handed back unchanged, or the given rows when one is set
    for the overflow proofs."""

    def __init__(self, order_quantity=None, rows=None):
        self.calls = 0
        self.rows = rows if rows is not None else [_row(order_quantity)]

    def __call__(self, as_of=None):
        self.calls += 1
        return [dict(row) for row in self.rows]


def _store_setting(value) -> None:
    # The settings table is truncated after the test: no cleanup.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                SETTING_SQL, ("replenishment.service_target", json.dumps(value))
            )
        conn.commit()
    finally:
        conn.close()


def _class_a(body):
    return next(entry for entry in body["classes"] if entry["abc_class"] == "A")


def _point(body, level):
    return next(point for point in _class_a(body)["points"] if point["level"] == level)


def test_issue1114_freeform(client, monkeypatch) -> None:
    # The defaults the proof asserts (0.95, 0.25) hold only when the
    # environment carries no override of its own.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    stand_in = _RecommendationsStandIn(order_quantity={"order_quantity": 20})
    monkeypatch.setattr(service_curve, "recommendations_for_all", stand_in)

    # --- Cycle: with nothing stored, the taking-part row is computed
    # with the level's z, as always.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body_cycle = response.json()
    assert body_cycle["service_target"] == "cycle"
    point_95 = _point(body_cycle, 0.95)
    assert point_95["safety_stock_units"] == 17
    assert point_95["safety_stock_value"] == 164.49
    assert point_95["holding_cost"] == 41.12
    assert point_95["expected_fill_rate"] == 0.9896

    # --- Fill rate: the taking-part row is computed with its own z
    # at every level — the z _fill_rate_z(sigma, level, Q) returns,
    # the rule the reorder recommendations follow in that mode.
    _store_setting("fill_rate")
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body_fill = response.json()
    assert body_fill["service_target"] == "fill_rate"
    point_95 = _point(body_fill, 0.95)
    assert point_95["safety_stock_units"] == 10
    assert point_95["safety_stock_value"] == 90.23
    assert point_95["holding_cost"] == 22.56
    assert point_95["expected_fill_rate"] == 0.95
    point_99 = _point(body_fill, 0.99)
    assert point_99["safety_stock_units"] == 17
    assert point_99["safety_stock_value"] == 166.31
    assert point_99["expected_fill_rate"] == 0.99
    point_90 = _point(body_fill, 0.90)
    assert point_90["safety_stock_units"] == 5
    assert point_90["safety_stock_value"] == 49.29
    # At every level of the ladder the units are 10 times the
    # row's own z, rounded up.
    for point in _class_a(body_fill)["points"]:
        level = point["level"]
        assert point["safety_stock_units"] == math.ceil(
            10.0 * _fill_rate_z(10.0, level, 20.0)
        )

    # --- Back: stored cycle again, the answer equals the first one.
    _store_setting("cycle")
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == body_cycle

    # --- A row that does not take part: the same row without an
    # order quantity — the fill_rate classes equal the cycle classes.
    monkeypatch.setattr(
        service_curve, "recommendations_for_all", _RecommendationsStandIn()
    )
    _store_setting("fill_rate")
    response = client.get(URL)
    assert response.status_code == 200, response.text
    classes_fill = response.json()["classes"]
    _store_setting("cycle")
    response = client.get(URL)
    assert response.status_code == 200, response.text
    classes_cycle = response.json()["classes"]
    assert classes_fill == classes_cycle

    # --- Keys: the key sets of the answer, of a class and of a point
    # are the same in both modes.
    assert set(body_cycle) == set(body_fill)
    for entry_cycle, entry_fill in zip(body_cycle["classes"], body_fill["classes"]):
        assert set(entry_cycle) == set(entry_fill)
        assert [point["level"] for point in entry_cycle["points"]] == [
            point["level"] for point in entry_fill["points"]
        ]
        for point_cycle, point_fill in zip(entry_cycle["points"], entry_fill["points"]):
            assert set(point_cycle) == set(point_fill)

    # --- Sigma's intermediate overflow: d * d is 1e400 (infinite)
    # while the sigma's radicand d^2 * sd_lt^2 = (d * sd_lt)^2 is
    # 1e200 — the row must still count, with sigma 1e100.
    _store_setting("cycle")
    monkeypatch.setattr(
        service_curve,
        "recommendations_for_all",
        _RecommendationsStandIn(
            rows=[
                _row(
                    item_code="SIGMA-1",
                    unit_cost=1.0,
                    inputs={"d": 1e200, "sd_d": 0.0, "lt": 0.0, "sd_lt": 1e-100},
                )
            ]
        ),
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body_sigma = response.json()
    z95 = statistics.NormalDist().inv_cdf(0.95)
    class_sigma = _class_a(body_sigma)
    assert class_sigma["item_sites"] == 1
    point_sigma = _point(body_sigma, 0.95)
    assert point_sigma["safety_stock_units"] == math.ceil(z95 * 1e100)
    assert point_sigma["safety_stock_value"] == round(z95 * 1e100, 2)

    # --- Overflowing value: at 0.95 each of the two rows'
    # z * sigma * unit_cost is ~1.6e308, so the finite terms sum to
    # more than the largest float — every point answers 0.0, never a
    # non-finite value.
    monkeypatch.setattr(
        service_curve,
        "recommendations_for_all",
        _RecommendationsStandIn(
            rows=[
                _row(
                    item_code="OVERFLOW-1",
                    unit_cost=1e154,
                    inputs={"d": 100.0, "sd_d": 1e154, "lt": 1.0, "sd_lt": 0.0},
                ),
                _row(
                    item_code="OVERFLOW-2",
                    unit_cost=1e154,
                    inputs={"d": 100.0, "sd_d": 1e154, "lt": 1.0, "sd_lt": 0.0},
                ),
            ]
        ),
    )
    response = client.get(URL)
    assert response.status_code == 200, response.text
    class_overflow = _class_a(response.json())
    assert class_overflow["item_sites"] == 2
    for point in class_overflow["points"]:
        assert point["safety_stock_units"] == 0
        assert point["safety_stock_value"] == 0.0
        assert point["holding_cost"] == 0.0
