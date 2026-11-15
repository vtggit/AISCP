"""Issue #1115 proof: the service level suggestion.

The proof replaces ``recommendations_for_all`` in
``app.recommend.service_curve`` with a stand-in answering three
``ok`` rows, the way ``backend/tests/test_issue1114_freeform.py``
does, and reads ``GET /api/service-levels/suggestion`` through the
app's test client (which signs in as a plain non-admin user).  The
rows are one per class: ``A-ITEM`` with ``unit_cost`` 20, ``inputs``
``d`` 100, ``sd_d`` 30, ``lt`` 4 and ``sd_lt`` 0 — sigma 60 — and
``order_quantity`` ``{"order_quantity": 60}``; ``B-ITEM`` with
``unit_cost`` 30, ``d`` 10, ``sd_d`` 5, ``lt`` 9 and ``sd_lt`` 0 —
sigma 15 — and ``{"order_quantity": 20}``; and ``C-ITEM`` with
``unit_cost`` 20, ``d`` 4, ``sd_d`` 3, ``lt`` 16 and ``sd_lt`` 0 —
sigma 12 — and ``{"order_quantity": 10}``.  Their annual values
(730000, 109500 and 29200) put them in the classes A, B and C.  The
checks:

* Current and highest — with nothing stored and
  ``target_fill_rate`` 0.97, ``current`` has the levels 0.95, 0.95
  and 0.95, ``holding_cost`` 777.18 and ``expected_fill_rate``
  0.9794, and ``highest`` has the level 0.995 three times,
  ``holding_cost`` 1217.08 and ``expected_fill_rate`` 0.9984.
* The same service for less — that answer's ``suggested`` has the
  levels A 0.95, B 0.9 and C 0.9, ``holding_cost`` 714.52 and
  ``expected_fill_rate`` 0.9766, and its ``holding_cost_delta`` is
  -62.66.
* More service — with ``target_fill_rate`` 0.99, ``suggested`` has
  the levels A 0.98, B 0.92 and C 0.95, ``holding_cost`` 872.88 and
  ``expected_fill_rate`` 0.9903, and its ``holding_cost_delta`` is
  95.7.
* Out of reach — with ``target_fill_rate`` 0.999, ``suggested`` and
  ``holding_cost_delta`` are null and ``highest`` is as above.
* Bad input — a missing ``target_fill_rate``, ``0.4``, ``1`` and
  ``abc`` each answer 422.
* Nothing takes part — with the stand-in answering the three rows
  without their order quantities, ``suggested`` is null and the
  ``expected_fill_rate`` of ``current`` is null.
* Fill-rate mode — with ``replenishment.service_target`` stored
  ``fill_rate``, the ``holding_cost`` of ``current`` equals the sum
  of the ``holding_cost`` of the curve's three current points, and
  its ``expected_fill_rate`` is at least 0.95.
* The search does not repeat the solve — with
  ``replenishment.service_target`` stored ``fill_rate`` and
  ``_fill_rate_z`` in ``app.recommend.service_curve`` replaced by a
  wrapper that counts its calls and hands each one through, one read
  of the route with ``target_fill_rate`` 0.97 calls it fewer than 100
  times; solving the three rows again for each of the 343
  combinations would take more than 1000.
* The gate — a signed-in plain user (role ``user``) reads the same
  answer, and missing or invalid credentials answer 401, as on the
  curve.
* The curve is untouched — ``GET /api/service-levels/curve`` answers
  the same before and after a call to the new route.
"""

import json
import os

from fastapi.testclient import TestClient

import app.recommend.service_curve as service_curve
from app.db.connection import get_connection

URL = "/api/service-levels/suggestion"
CURVE_URL = "/api/service-levels/curve"
SITE = "SITE-1"

SETTING_SQL = (
    "INSERT INTO settings (scope, owner, key, value, updated_by) "
    "VALUES ('system', '', %s, %s::jsonb, 'test') "
    "ON CONFLICT (scope, owner, key) DO UPDATE SET value = EXCLUDED.value"
)


def _row(item_code, unit_cost, d, sd_d, lt, order_quantity):
    """One recommendation row of the proof: ``sd_lt`` 0, so the sigma
    is the square root of ``lt * sd_d^2``."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "as_of": None,
        "status": "ok",
        "unit_cost": unit_cost,
        "inputs": {"d": d, "sd_d": sd_d, "lt": lt, "sd_lt": 0.0},
        "order_quantity": order_quantity,
    }


ROWS = [
    _row("A-ITEM", 20.0, 100.0, 30.0, 4.0, {"order_quantity": 60.0}),
    _row("B-ITEM", 30.0, 10.0, 5.0, 9.0, {"order_quantity": 20.0}),
    _row("C-ITEM", 20.0, 4.0, 3.0, 16.0, {"order_quantity": 10.0}),
]


class _RecommendationsStandIn:
    """The stand-in for ``recommendations_for_all`` in
    ``app.recommend.service_curve``: the three rows, or the same
    three rows without their order quantities (nothing takes part in
    the fill rate)."""

    def __init__(self, with_quantities=True):
        self.rows = [dict(row) for row in ROWS]
        if not with_quantities:
            for row in self.rows:
                row["order_quantity"] = None

    def __call__(self, as_of=None):
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


def test_issue1115_freeform(client, monkeypatch) -> None:
    # The defaults the proof asserts (0.95, 0.25, cycle) hold only
    # when the environment carries no override of its own.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    monkeypatch.setattr(
        service_curve, "recommendations_for_all", _RecommendationsStandIn()
    )
    from app.main import app

    # --- The curve before anything else: the new route must answer
    # the same after it.
    curve_before = client.get(CURVE_URL)
    assert curve_before.status_code == 200, curve_before.text

    # --- Current, highest and the same service for less: target
    # 0.97.
    response = client.get(URL, params={"target_fill_rate": 0.97})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] is None
    assert body["service_target"] == "cycle"
    assert body["holding_rate"] == 0.25
    assert body["target_fill_rate"] == 0.97
    assert body["current"] == {
        "levels": {"A": 0.95, "B": 0.95, "C": 0.95},
        "expected_fill_rate": 0.9794,
        "holding_cost": 777.18,
    }
    assert body["highest"] == {
        "levels": {"A": 0.995, "B": 0.995, "C": 0.995},
        "expected_fill_rate": 0.9984,
        "holding_cost": 1217.08,
    }
    assert body["suggested"] == {
        "levels": {"A": 0.95, "B": 0.9, "C": 0.9},
        "expected_fill_rate": 0.9766,
        "holding_cost": 714.52,
    }
    assert body["holding_cost_delta"] == -62.66

    # --- More service: target 0.99.
    response = client.get(URL, params={"target_fill_rate": 0.99})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested"] == {
        "levels": {"A": 0.98, "B": 0.92, "C": 0.95},
        "expected_fill_rate": 0.9903,
        "holding_cost": 872.88,
    }
    assert body["holding_cost_delta"] == 95.7

    # --- Out of reach: target 0.999.
    response = client.get(URL, params={"target_fill_rate": 0.999})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested"] is None
    assert body["holding_cost_delta"] is None
    assert body["highest"] == {
        "levels": {"A": 0.995, "B": 0.995, "C": 0.995},
        "expected_fill_rate": 0.9984,
        "holding_cost": 1217.08,
    }

    # --- Bad input: missing, out of range or non-numeric.
    assert client.get(URL).status_code == 422
    for bad in (0.4, 1, "abc"):
        response = client.get(URL, params={"target_fill_rate": bad})
        assert response.status_code == 422, response.text

    # --- The curve is untouched by the new route.
    curve_after = client.get(CURVE_URL)
    assert curve_after.status_code == 200, curve_after.text
    assert curve_after.json() == curve_before.json()

    # --- Nothing takes part: the three rows without their order
    # quantities.
    monkeypatch.setattr(
        service_curve, "recommendations_for_all", _RecommendationsStandIn(False)
    )
    response = client.get(URL, params={"target_fill_rate": 0.97})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["suggested"] is None
    assert body["current"]["expected_fill_rate"] is None
    assert body["holding_cost_delta"] is None

    # --- Fill-rate mode: current's holding cost is the sum of the
    # curve's three current points, and its expected fill rate is at
    # least 0.95.
    monkeypatch.setattr(
        service_curve, "recommendations_for_all", _RecommendationsStandIn()
    )
    _store_setting("fill_rate")
    curve_fill_before = client.get(CURVE_URL)
    assert curve_fill_before.status_code == 200, curve_fill_before.text
    curve_fill = curve_fill_before.json()
    assert curve_fill["service_target"] == "fill_rate"
    response = client.get(URL, params={"target_fill_rate": 0.97})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["service_target"] == "fill_rate"
    current_points = [
        point
        for entry in curve_fill["classes"]
        for point in entry["points"]
        if point["current"]
    ]
    assert len(current_points) == 3
    assert body["current"]["holding_cost"] == round(
        sum(point["holding_cost"] for point in current_points), 2
    )
    assert body["current"]["expected_fill_rate"] is not None
    assert body["current"]["expected_fill_rate"] >= 0.95
    curve_fill_after = client.get(CURVE_URL)
    assert curve_fill_after.status_code == 200, curve_fill_after.text
    assert curve_fill_after.json() == curve_fill_before.json()

    # --- The search does not repeat the solve: one read of the
    # route calls _fill_rate_z fewer than 100 times (solving the
    # three rows again for each of the 343 combinations would take
    # more than 1000).
    original_z = service_curve._fill_rate_z
    calls = 0

    def counting_z(sigma, level, quantity):
        nonlocal calls
        calls += 1
        return original_z(sigma, level, quantity)

    monkeypatch.setattr(service_curve, "_fill_rate_z", counting_z)
    calls = 0
    response = client.get(URL, params={"target_fill_rate": 0.97})
    assert response.status_code == 200, response.text
    assert calls < 100

    # --- The gate: a signed-in plain user (role 'user') reads the
    # same answer, and missing or invalid credentials answer 401, as
    # on the curve.
    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    user_client = TestClient(app, headers={"Authorization": f"Bearer {dev_token}:user"})
    response = user_client.get(URL, params={"target_fill_rate": 0.97})
    assert response.status_code == 200, response.text
    assert response.json() == body
    anonymous = TestClient(app)
    assert anonymous.get(URL, params={"target_fill_rate": 0.97}).status_code == 401
    assert anonymous.get(CURVE_URL).status_code == 401
    invalid = TestClient(app, headers={"Authorization": "Bearer not-a-token"})
    assert invalid.get(URL, params={"target_fill_rate": 0.97}).status_code == 401
