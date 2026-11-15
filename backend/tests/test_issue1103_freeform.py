"""Issue #1103 proof: the ``replenishment.service_target`` setting.

The setting is a system-scope enum with allowed values ``cycle`` and
``fill_rate`` and the default ``cycle`` — registered in
``app.settings.registry`` and listed by ``GET /api/settings`` the way
test_issue274_freeform and test_issue502_freeform pin it.  The route
reads it from the stored-settings read the call already makes, with no
new query, and any value other than ``fill_rate`` means ``cycle``.

The proof seeds one item-site the way test_issue645_freeform does —
90 days of varying daily demand in three 30-day buckets of 240, 300
and 360 units (d 10, sd_d 60/sqrt(30)), three lead-time observations
of 8, 10 and 12 days (LT 10, sd_LT 2), an item_sites row with a unit
cost of 50.0 and 5 units on hand — so ``GET /api/recommendations``
answers it ``ok`` with a positive order quantity (the EOQ for
d 10, unit cost 50.0, per-order cost 50.0 and holding rate 0.25),
sigma the formula's ``sqrt(LT * sd_d^2 + d^2 * sd_LT^2)`` = 40 and a
fill-rate z strictly between 0 and 4.  It reads the route three
times:

* Cycle — with nothing stored, and again with
  ``replenishment.service_target`` stored ``cycle``: the item-site's
  recommendation is identical in both answers (the cycle computation,
  z of the 0.95 service level, ``service_level_source.source``
  ``single``).
* Fill rate — with the setting stored ``fill_rate``:
  ``service_level_source.source`` reads ``fill_rate``, ``inputs.z``
  is between 0 and 4, sigma * G(z) is within 0.001 * Q of
  (1 - 0.95) * Q (G the standard normal loss — the standard normal
  density at z minus z times one minus the standard normal
  distribution at z) unless z is 0 or 4, ``safety_stock`` equals
  z * sigma within 0.01, ``reorder_point`` equals ``d * LT`` plus
  ``safety_stock`` within 0.01, the arithmetic starts with
  ``fill rate target 0.95: z = ``, and the row carries exactly the
  same keys — and the same ``inputs`` keys — as the cycle
  recommendation.
"""

import math
import statistics
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

ADMIN_TOKEN = "dev-secret-token:admin"
SETTING_KEY = "replenishment.service_target"

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
S1 = "S1"
ITEM = "I1103-1"

SYNC_AS_OF = "2026-09-30T00:00:00+00:00"

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
LEAD_SQL = (
    "INSERT INTO lead_time_observations (id, business_key, supplier_code, "
    "item_code, ordered_on, received_on, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
ITEM_SITE_SQL = (
    "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
    "reorder_point, safety_stock, max_qty, order_qty, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
ON_HAND_SQL = (
    "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
    "as_of, source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)


def _normal_loss(z: float) -> float:
    """G(z), the standard normal loss function."""
    dist = statistics.NormalDist()
    return dist.pdf(z) - z * (1.0 - dist.cdf(z))


def _seed(cur) -> None:
    """The one item-site: varying demand and lead times, a unit cost
    and on-hand stock, the way test_issue645_freeform seeds them.

    90 days of daily demand, the daily quantity alternating 7/9 in
    the oldest 30-day bucket (240 units), 9/11 in the middle one
    (300) and 11/13 in the newest one (360): d = 10, sd_d =
    stdev(360, 300, 240)/sqrt(30) ~ 10.954.  Three lead-time
    observations of 8, 10 and 12 days: LT 10, sd_LT 2.  The item_sites
    row carries a unit cost of 50.0, and 5 units are on hand — so the
    recommendation is ``ok``, the order quantity (EOQ ~ 170.88) is
    positive and sigma = sqrt(10 * 10.954^2 + 10^2 * 2^2) = 40.
    """
    first = AS_OF - timedelta(days=89)
    for day in range(90):
        # day 0 is the oldest; bucket index (newest 0): 2, 1, 0.
        low, high = ((7, 9), (9, 11), (11, 13))[2 - (89 - day) // 30]
        quantity = low if day % 2 == 0 else high
        cur.execute(
            DEMAND_SQL,
            (
                f"id-de-1103-{day}",
                f"de-1103-{day}",
                ITEM,
                S1,
                float(quantity),
                (first + timedelta(days=day)).isoformat(),
                "shipment",
                "test",
            ),
        )
    for index, ordered, duration in (
        (1, date(2026, 8, 1), 8),
        (2, date(2026, 8, 10), 10),
        (3, date(2026, 8, 21), 12),
    ):
        cur.execute(
            LEAD_SQL,
            (
                f"id-lt-1103-{index}",
                f"lt-1103-{index}",
                "SUP-1103-1",
                ITEM,
                ordered.isoformat(),
                (ordered + timedelta(days=duration)).isoformat(),
                "test",
            ),
        )
    cur.execute(
        ITEM_SITE_SQL,
        ("id-is-1103", ITEM, S1, 50.0, 150.0, 40.0, None, None, "test"),
    )
    cur.execute(ON_HAND_SQL, ("id-oh-1103", ITEM, S1, 5.0, SYNC_AS_OF, "test"))


def _recommendation(client: TestClient) -> dict:
    response = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [(row["item_code"], row["site_code"]) for row in rows] == [(ITEM, S1)]
    return rows[0]


def test_issue1103_freeform(client, monkeypatch) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # --- Cycle: nothing stored ---------------------------------------------
    cycle = _recommendation(client)
    assert cycle["status"] == "ok"
    assert cycle["order_quantity"] is not None
    assert cycle["order_quantity"]["order_quantity"] > 0
    assert cycle["suggested_order_quantity"] > 0
    assert cycle["service_level_source"]["source"] == "single"
    assert cycle["inputs"]["z"] == pytest.approx(statistics.NormalDist().inv_cdf(0.95))

    # --- Cycle: the setting stored "cycle" — the identical recommendation ---
    response = admin.put(f"/api/settings/{SETTING_KEY}", json={"value": "cycle"})
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "cycle"
    assert _recommendation(client) == cycle

    # --- Fill rate -----------------------------------------------------------
    response = admin.put(f"/api/settings/{SETTING_KEY}", json={"value": "fill_rate"})
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "fill_rate"
    fill = _recommendation(client)

    assert fill["service_level_source"]["source"] == "fill_rate"
    inputs = fill["inputs"]
    z = inputs["z"]
    assert 0 <= z <= 4
    d, sd_d = inputs["d"], inputs["sd_d"]
    lt, sd_lt = inputs["lt"], inputs["sd_lt"]
    level = inputs["service_level"]
    sigma = math.sqrt(lt * sd_d * sd_d + d * d * sd_lt * sd_lt)
    q = fill["order_quantity"]["order_quantity"]
    if z not in (0, 4):
        assert abs(sigma * _normal_loss(z) - (1 - level) * q) <= 0.001 * q
    assert fill["safety_stock"] == pytest.approx(z * sigma, abs=0.01)
    assert fill["reorder_point"] == pytest.approx(
        d * lt + fill["safety_stock"], abs=0.01
    )
    assert fill["arithmetic"].startswith("fill rate target 0.95: z = ")

    # Same keys: the fill-rate recommendation carries exactly the
    # cycle recommendation's keys, and the same inputs keys.
    assert set(fill) == set(cycle)
    assert set(fill["inputs"]) == set(cycle["inputs"])
