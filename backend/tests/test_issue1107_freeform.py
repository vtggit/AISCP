"""Issue #1107 proof: the service target of the service level curve.

The proof replaces ``recommendations_for_all`` in
``app.recommend.service_curve`` with a stand-in answering one ``ok``
row, the way ``backend/tests/test_issue1100_freeform.py`` does, and
reads ``GET /api/service-levels/curve`` through the app's test
client.  The row is an ``ok`` item-site with ``unit_cost`` 10,
``inputs.sd_lt`` 0, ``inputs.lt`` 4 and ``inputs.sd_d`` 5 (so its
sigma is ``sqrt(4 * 5^2 + 100^2 * 0^2)`` = 10) and ``inputs.d`` 100,
so its annual value 365000 classifies it A.  The checks:

* Default — with nothing stored, the answer's ``service_target`` is
  ``cycle``.
* Fill rate — with ``replenishment.service_target`` stored
  ``fill_rate``, it is ``fill_rate``.
* Back — stored ``cycle`` again, it is ``cycle``.
* Unchanged — the ``classes`` of the three answers are equal: the
  service target is reported, not recomputed, and nothing else of the
  answer moves with it.
"""

import json

import app.recommend.service_curve as service_curve
from app.db.connection import get_connection

URL = "/api/service-levels/curve"

ITEM_A = "A-ITEM"
SITE = "SITE-1"

SETTING_SQL = (
    "INSERT INTO settings (scope, owner, key, value, updated_by) "
    "VALUES ('system', '', %s, %s::jsonb, 'test') "
    "ON CONFLICT (scope, owner, key) DO UPDATE SET value = EXCLUDED.value"
)


def _row():
    """The one fixed recommendation row: sigma 10, unit cost 10."""
    return {
        "item_code": ITEM_A,
        "site_code": SITE,
        "as_of": None,
        "status": "ok",
        "unit_cost": 10.0,
        "inputs": {"d": 100.0, "sd_d": 5.0, "lt": 4.0, "sd_lt": 0.0},
        "order_quantity": None,
    }


class _RecommendationsStandIn:
    """The stand-in for ``recommendations_for_all`` in
    ``app.recommend.service_curve``: every call is counted, the fixed
    row is handed back unchanged."""

    def __init__(self):
        self.calls = 0
        self.rows = [_row()]

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


def test_issue1107_freeform(client, monkeypatch) -> None:
    # The defaults the proof asserts (0.95, 0.25) hold only when the
    # environment carries no override of its own.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    stand_in = _RecommendationsStandIn()
    monkeypatch.setattr(service_curve, "recommendations_for_all", stand_in)

    # --- Default: with nothing stored, the answer's service target
    # is cycle.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["service_target"] == "cycle"
    assert [entry["abc_class"] for entry in body["classes"]] == ["A", "B", "C"]
    classes_default = body["classes"]

    # --- Fill rate: with replenishment.service_target stored
    # fill_rate, it is fill_rate.
    _store_setting("fill_rate")
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["service_target"] == "fill_rate"
    classes_fill_rate = body["classes"]

    # --- Back: stored cycle again, it is cycle.
    _store_setting("cycle")
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["service_target"] == "cycle"
    classes_back = body["classes"]

    # --- Unchanged: the classes of the three answers are equal — the
    # service target is reported from the one stored-settings read the
    # curve already makes, nothing else of the answer moves with it.
    assert classes_fill_rate == classes_default
    assert classes_back == classes_default

    # The stand-in is called exactly once per request: the service
    # target comes from the one stored-settings read, no new query.
    assert stand_in.calls == 3, "recommendations_for_all is read once per request"
