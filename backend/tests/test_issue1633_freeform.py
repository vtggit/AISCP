"""Issue #1633 proof: the review month's extra safety stock in the
monthly review's order-up-to level.

AC-1 — in ``app.aggregate.projection.aggregate_projection``'s
monthly review, an item-site's order-up-to level for a bucket month
is its reorder point (as before), plus ``d x days-in-month`` (as
before), plus the review month's extra safety stock
``z x (sqrt((lt + days) x sd_d^2 + d^2 x sd_lt^2) - sqrt(lt x
sd_d^2 + d^2 x sd_lt^2))`` (``days`` the bucket month's days and
``d``, ``sd_d``, ``lt``, ``sd_lt`` and ``z`` the recommendation's
``inputs``, each non-finite or missing value taken as 0, as ``d``
and ``lt`` are, the scaled inputs and the scenario's ``z`` for every
item-site when a scenario lever is given, and 0 when no lever is
given and the recommendation's ``method`` is not
``app.recommend.reorder.METHOD``), plus the signal cover (as
before).  Nothing else in the review changes: the position, the
arrival, the order quantity ``ceil(S - position)``, the as-is
figures and the response shape.

AC-2 — proven below with the three reads monkeypatched the way
``tests/test_issue484_freeform.py`` monkeypatches them (no
database), ``as_of`` 2026-01-15 and 3 months (January 31 days,
February 28, March 31), one recommendable item-site I1 at S1 with
reorder point 215, ``d`` 10, ``sd_d`` 2, ``lt`` 20, ``sd_lt`` 0,
``z`` 1.6448536, service level 0.95, unit cost 2.0, on-hand 250 and
no open lines:

  * no lever: January's order-up-to level is 215 + 310 +
    1.6448536 x (sqrt(51 x 4) - sqrt(20 x 4)) ~ 533.78, the position
    250 orders 284 units arriving 21 January (planned on-hand
    224.0); February's extra safety stock uses its 28 days,
    1.6448536 x (sqrt(48 x 4) - sqrt(80)) ~ 8.08, the position 224
    orders 280 (planned on-hand 224.0); March orders 310 (planned
    on-hand 224.0);
  * the same item-site with ``sd_d`` 0: the extra is 0 and the
    orders are 275, 280 and 310;
  * ``sd_lt`` 3 (reorder point still 215): January's order is 279 —
    the ``d^2 x sd_lt^2`` term stays inside both roots and is not
    added twice;
  * a recommendation whose ``method`` is any other string (the
    empirical method, for one): no extra safety stock, January's
    order is 275;
  * the ``service_level`` lever 0.95 given: January's order is 284,
    the same as without the lever (the group field for field);
  * ``GET /api/aggregate/projection`` answers January's 284.
"""

import math
from datetime import date

import pytest

from app.aggregate import projection
from app.aggregate.projection import aggregate_projection
from app.recommend.empirical import METHOD as EMPIRICAL_METHOD
from app.recommend.reorder import METHOD

AS_OF = date(2026, 1, 15)
URL = "/api/aggregate/projection"

BUCKET_KEYS = {
    "month",
    "projected_demand_units",
    "projected_supply_units",
    "projected_on_hand_units",
    "projected_shortfall",
    "projected_demand_value",
    "projected_supply_value",
    "signal_units",
    "planned_receipts_units",
    "planned_receipts_value",
    "planned_on_hand_units",
    "planned_shortfall",
}


def _rec(item, site, rop, d, sd_d, lt, sd_lt, method=METHOD):
    """One recommendable recommendation row as the projection sees it."""
    return {
        "status": "ok",
        "item_code": item,
        "site_code": site,
        "reorder_point_units": rop,
        "unit_cost": 2.0,
        "method": method,
        "inputs": {
            "d": d,
            "sd_d": sd_d,
            "lt": lt,
            "sd_lt": sd_lt,
            "z": 1.6448536,
            "service_level": 0.95,
        },
    }


def _install(monkeypatch, recs, on_hand_rows, line_rows):
    """Monkeypatch the three reads (no database) and count their calls."""
    calls = {"recommendations": 0, "on_hand": 0, "lines": 0}

    def fake_recommendations(as_of=None):
        calls["recommendations"] += 1
        return list(recs)

    def fake_list_rows(limit, offset, **filters):
        calls["on_hand"] += 1
        return list(on_hand_rows)

    def fake_list_latest():
        calls["lines"] += 1
        return list(line_rows)

    monkeypatch.setattr(projection, "recommendations_for_all", fake_recommendations)
    monkeypatch.setattr(projection._on_hand_repo, "list_rows", fake_list_rows)
    monkeypatch.setattr(projection._open_po_repo, "list_latest", fake_list_latest)
    return calls


def test_issue1633_freeform(monkeypatch, client):
    # I1@S1: reorder point 215, d 10, sd_d 2, lt 20, sd_lt 0, z
    # 1.6448536 (service level 0.95), unit cost 2.0, on-hand 250, no
    # open lines, 3 months (January 31, February 28, March 31).
    recs = [_rec("I1", "S1", 215, 10, 2.0, 20, 0.0)]
    on_hand = [{"item_code": "I1", "site_code": "S1", "quantity_on_hand": 250}]

    # --- no lever: the review month's extra safety stock -------------------
    calls = _install(monkeypatch, recs, on_hand, [])
    rows = aggregate_projection(as_of=AS_OF, months=3)
    assert calls == {"recommendations": 1, "on_hand": 1, "lines": 1}
    assert len(rows) == 1
    group = rows[0]
    assert group["group"] == {"dimension": "site", "code": "S1"}
    assert group["item_sites_without_unit_cost"] == 0
    january, february, march = group["buckets"]
    for bucket in group["buckets"]:
        assert set(bucket) == BUCKET_KEYS
    # The as-is roll is what it was before the extra safety stock:
    # the group's on-hand 250 against the d x days-in-month demand.
    assert january["projected_demand_units"] == 310
    assert january["projected_on_hand_units"] == -60.0
    assert january["projected_demand_value"] == 620.0
    assert february["projected_demand_units"] == 280
    assert february["projected_on_hand_units"] == -340.0
    assert march["projected_demand_units"] == 310
    assert march["projected_on_hand_units"] == -650.0
    # January: the extra safety stock is 1.6448536 x (sqrt(51 x 4) -
    # sqrt(20 x 4)) ~ 8.78, so S = 215 + 310 + 8.78... and the
    # position 250 orders 284 units, arriving 21 January (planned
    # on-hand 224.0).
    january_extra = 1.6448536 * (math.sqrt(51.0 * 4.0) - math.sqrt(20.0 * 4.0))
    assert january_extra == pytest.approx(8.78, abs=0.005)
    assert january["planned_receipts_units"] == math.ceil(
        215 + 310 + january_extra - 250
    )
    assert january["planned_receipts_units"] == 284
    assert january["planned_receipts_value"] == 568.0
    assert january["planned_on_hand_units"] == 224.0
    # February: the extra uses its 28 days, 1.6448536 x (sqrt(48 x 4)
    # - sqrt(80)) ~ 8.08; the position 250 + 284 - 310 = 224 orders
    # 280 (planned on-hand 224.0).
    february_extra = 1.6448536 * (math.sqrt(48.0 * 4.0) - math.sqrt(20.0 * 4.0))
    assert february_extra == pytest.approx(8.08, abs=0.005)
    assert february["planned_receipts_units"] == math.ceil(
        215 + 280 + february_extra - 224
    )
    assert february["planned_receipts_units"] == 280
    assert february["planned_on_hand_units"] == 224.0
    # March: the position 224 is below 215 + 310 + 8.78...: 310 units
    # (planned on-hand 224.0).
    assert march["planned_receipts_units"] == 310
    assert march["planned_on_hand_units"] == 224.0

    # --- sd_d 0: the extra safety stock is 0 --------------------------------
    smooth = [_rec("I1", "S1", 215, 10, 0.0, 20, 0.0)]
    _install(monkeypatch, smooth, on_hand, [])
    smooth_rows = aggregate_projection(as_of=AS_OF, months=3)
    assert [
        bucket["planned_receipts_units"] for bucket in smooth_rows[0]["buckets"]
    ] == [275, 280, 310]

    # --- sd_lt 3: the d^2 x sd_lt^2 term stays inside both roots ------------
    sd_lt = [_rec("I1", "S1", 215, 10, 2.0, 20, 3.0)]
    _install(monkeypatch, sd_lt, on_hand, [])
    sd_lt_rows = aggregate_projection(as_of=AS_OF, months=3)
    # 1.6448536 x (sqrt(51 x 4 + 100 x 9) - sqrt(20 x 4 + 100 x 9)) —
    # the term in both roots, never added on top.
    sd_lt_extra = 1.6448536 * (
        math.sqrt(51.0 * 4.0 + 10.0**2 * 3.0**2)
        - math.sqrt(20.0 * 4.0 + 10.0**2 * 3.0**2)
    )
    assert sd_lt_rows[0]["buckets"][0]["planned_receipts_units"] == math.ceil(
        215 + 310 + sd_lt_extra - 250
    )
    assert sd_lt_rows[0]["buckets"][0]["planned_receipts_units"] == 279

    # --- any other method: no extra safety stock ----------------------------
    other_method = [_rec("I1", "S1", 215, 10, 2.0, 20, 0.0, method=EMPIRICAL_METHOD)]
    _install(monkeypatch, other_method, on_hand, [])
    other_rows = aggregate_projection(as_of=AS_OF, months=3)
    assert other_rows[0]["buckets"][0]["planned_receipts_units"] == 275

    # --- the service_level lever 0.95: the same as without the lever --------
    _install(monkeypatch, recs, on_hand, [])
    levered = aggregate_projection(as_of=AS_OF, months=3, service_level=0.95)
    assert levered[0]["buckets"][0]["planned_receipts_units"] == 284
    assert levered[0] == group

    # --- through the app with the test Bearer --------------------------------
    _install(monkeypatch, recs, on_hand, [])
    response = client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 3})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body[0]["buckets"][0]["planned_receipts_units"] == 284
