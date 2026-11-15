"""Issue #52 proof: service-level reorder point recommendations.

Pure ``app.recommend.reorder.recommend`` (no database):
  * d = 10, sd_d = 3, LT = 12, sd_LT = 2 at service level 0.95 gives
    z 1.6449 (4 places), safety_stock 37.073 and reorder_point 157.073
    (3 places), whole-unit ceilings 38 and 158, and an arithmetic
    string containing 37.07 and 157.07,
  * the same inputs at service level 0.98 give safety_stock 46.289
    (3 places),
  * an erratic item carries the "variable demand" note,
  * an intermittent item returns not_recommendable_demand,
  * a low-confidence lead time (and no lead time) return
    insufficient_lead_time unless a master lead time is given, which
    then becomes LT with sd_LT 0 and the "master lead time used" note,
  * AIERP_SERVICE_LEVEL=1.5 (and other out-of-range values) raise
    ValueError naming the variable.

Against the test database (WIDGET-52/SITE-1 with a smooth 360-day
demand, three lead-time observations and an item_sites row; AA-52 with
intermittent demand and no item_sites row; NOD-52 with only an
item_sites row; date.today pinned to 2026-10-02 so the default as_of is
the seeded 2026-09-30):
  * /by-key returns the ok result whose current settings and
    delta_reorder_point are as expected (the master lead time 15 is
    ignored with 3 observations),
  * the list route orders by item_code then site_code, honours the
    status/item_code filters, 422s a limit above 1000, and 400s (not
    500s) an out-of-range AIERP_SERVICE_LEVEL,
  * a known item-site with no demand history returns 200 with
    not_recommendable_demand / no_demand, and an unknown item-site
    returns 404,
  * POST/PUT/PATCH/DELETE are 405 (read-only API) and hostile query
    values are rejected 422.
"""

import statistics
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection
from app.recommend.reorder import (
    METHOD,
    recommend,
    recommendations_for_all,
)

AS_OF = date(2026, 9, 30)
FIXED_TODAY = date(2026, 10, 2)

DEMAND = {
    "mean_daily": 10.0,
    "std_daily": 3.0,
    "demand_class": "smooth",
    "recommendable": True,
}
LEAD_TIME = {
    "mean_days": 12.0,
    "std_days": 2.0,
    "n_used": 10,
    "low_confidence": False,
}


def test_issue52_freeform(client, monkeypatch):
    # --- pure: the 0.95 reference case ------------------------------------
    result = recommend(DEMAND, LEAD_TIME, service_level=0.95)
    assert result["status"] == "ok"
    assert result["demand_class"] == "smooth"
    assert round(result["inputs"]["z"], 4) == 1.6449
    assert round(result["safety_stock"], 3) == 37.073
    assert round(result["reorder_point"], 3) == 157.073
    assert result["safety_stock_units"] == 38
    assert result["reorder_point_units"] == 158
    assert "37.07" in result["arithmetic"]
    assert "157.07" in result["arithmetic"]
    assert result["method"] == METHOD
    assert "variable demand" not in result["notes"]
    assert result["inputs"] == {
        "d": 10.0,
        "sd_d": 3.0,
        "lt": 12.0,
        "sd_lt": 2.0,
        "z": statistics.NormalDist().inv_cdf(0.95),
        "service_level": 0.95,
    }

    # --- pure: the same inputs at service level 0.98 -----------------------
    result = recommend(DEMAND, LEAD_TIME, service_level=0.98)
    assert round(result["safety_stock"], 3) == 46.289

    # --- pure: erratic demand carries the variable demand note -------------
    erratic = dict(DEMAND, demand_class="erratic")
    result = recommend(erratic, LEAD_TIME, service_level=0.95)
    assert result["status"] == "ok"
    assert "variable demand" in result["notes"]

    # --- pure: intermittent demand is not recommendable --------------------
    intermittent = {
        "mean_daily": 0.5,
        "std_daily": 1.0,
        "demand_class": "intermittent",
        "recommendable": False,
    }
    result = recommend(intermittent, LEAD_TIME)
    assert result["status"] == "not_recommendable_demand"
    assert result["demand_class"] == "intermittent"
    assert result["safety_stock"] is None and result["reorder_point"] is None

    # --- pure: lead time confidence and the master fallback ----------------
    low_confidence = {
        "mean_days": 12.0,
        "std_days": 2.0,
        "n_used": 2,
        "low_confidence": True,
    }
    assert recommend(DEMAND, low_confidence)["status"] == "insufficient_lead_time"
    assert recommend(DEMAND, None)["status"] == "insufficient_lead_time"
    result = recommend(DEMAND, low_confidence, master_lead_time_days=14.0)
    assert result["status"] == "ok"
    assert result["inputs"]["lt"] == 14.0
    assert result["inputs"]["sd_lt"] == 0.0
    assert "master lead time used" in result["notes"]
    assert "14" in result["arithmetic"]

    # --- service level from the environment, read at call time -------------
    monkeypatch.setenv("AIERP_SERVICE_LEVEL", "1.5")
    with pytest.raises(ValueError, match="AIERP_SERVICE_LEVEL"):
        recommendations_for_all(as_of=AS_OF)
    monkeypatch.setenv("AIERP_SERVICE_LEVEL", "0.5")
    with pytest.raises(ValueError, match="AIERP_SERVICE_LEVEL"):
        recommendations_for_all(as_of=AS_OF)
    monkeypatch.setenv("AIERP_SERVICE_LEVEL", "0.9999")
    with pytest.raises(ValueError, match="AIERP_SERVICE_LEVEL"):
        recommendations_for_all(as_of=AS_OF)
    monkeypatch.setenv("AIERP_SERVICE_LEVEL", "not-a-number")
    with pytest.raises(ValueError, match="AIERP_SERVICE_LEVEL"):
        recommendations_for_all(as_of=AS_OF)
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    # --- seed: WIDGET-52 smooth, AA-52 intermittent, NOD-52 no demand ------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            first_day = AS_OF - timedelta(days=359)
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        f"id-de-52-{i}",
                        f"de-52-{i}",
                        "WIDGET-52",
                        "SITE-1",
                        2.0,
                        (first_day + timedelta(days=i)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for i in range(360)
                ],
            )
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-de-aa-1",
                        "de-aa-1",
                        "AA-52",
                        "SITE-1",
                        60.0,
                        "2025-10-04",
                        "shipment",
                        "test",
                    ),
                    (
                        "id-de-aa-2",
                        "de-aa-2",
                        "AA-52",
                        "SITE-1",
                        60.0,
                        "2026-09-30",
                        "shipment",
                        "test",
                    ),
                ],
            )
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, "
                "ordered_on, received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-lt-52-1",
                        "lt-52-1",
                        "SUP-52",
                        "WIDGET-52",
                        "2026-08-22",
                        "2026-09-01",
                        "test",
                    ),
                    (
                        "id-lt-52-2",
                        "lt-52-2",
                        "SUP-52",
                        "WIDGET-52",
                        "2026-08-29",
                        "2026-09-10",
                        "test",
                    ),
                    (
                        "id-lt-52-3",
                        "lt-52-3",
                        "SUP-52",
                        "WIDGET-52",
                        "2026-08-28",
                        "2026-09-11",
                        "test",
                    ),
                ],
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "safety_stock, master_lead_time_days, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-is-52-1",
                        "WIDGET-52",
                        "SITE-1",
                        1.0,
                        40.0,
                        10.0,
                        15.0,
                        "test",
                    ),
                    ("id-is-nod-1", "NOD-52", "SITE-1", 1.0, 5.0, 1.0, None, "test"),
                ],
            )
        conn.commit()
    finally:
        conn.close()

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    monkeypatch.setattr("app.stats.demand.date", _FixedDate)
    z95 = statistics.NormalDist().inv_cdf(0.95)
    expected_safety_stock = z95 * 4.0  # sqrt(12*0 + 2^2 * 2^2)
    expected_reorder_point = 2 * 12 + expected_safety_stock

    # --- by-key: the seeded smooth item ------------------------------------
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": "WIDGET-52", "site_code": "SITE-1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["item_code"] == "WIDGET-52"
    assert body["site_code"] == "SITE-1"
    assert body["as_of"] == "2026-09-30"
    assert body["status"] == "ok"
    assert body["demand_class"] == "smooth"
    assert body["current"] == {"reorder_point": 40.0, "safety_stock": 10.0}
    assert body["safety_stock"] == pytest.approx(expected_safety_stock)
    assert body["reorder_point"] == pytest.approx(expected_reorder_point)
    assert body["safety_stock_units"] == 7
    assert body["reorder_point_units"] == 31
    assert body["delta_reorder_point"] == pytest.approx(40.0 - expected_reorder_point)
    # Three observations beat the master lead time 15.
    assert body["inputs"]["lt"] == 12.0
    assert body["inputs"]["sd_lt"] == 2.0
    assert body["inputs"]["d"] == 2.0
    assert body["inputs"]["sd_d"] == 0.0
    assert "master lead time used" not in body["notes"]
    assert "6.58" in body["arithmetic"] and "30.58" in body["arithmetic"]

    # --- list: ordering, filters, limit ceiling -----------------------------
    response = client.get("/api/recommendations", params={"as_of": "2026-09-30"})
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        ("AA-52", "SITE-1"),
        ("WIDGET-52", "SITE-1"),
    ]
    aa = rows[0]
    assert aa["status"] == "not_recommendable_demand"
    assert aa["demand_class"] == "intermittent"
    assert aa["current"] == {"reorder_point": None, "safety_stock": None}
    assert aa["delta_reorder_point"] is None
    assert aa["safety_stock"] is None and aa["reorder_point"] is None
    assert rows[1]["delta_reorder_point"] == pytest.approx(
        40.0 - expected_reorder_point
    )

    response = client.get(
        "/api/recommendations",
        params={"as_of": "2026-09-30", "status": "ok"},
    )
    assert [row["item_code"] for row in response.json()] == ["WIDGET-52"]
    response = client.get(
        "/api/recommendations",
        params={"as_of": "2026-09-30", "item_code": "AA-52"},
    )
    assert [row["item_code"] for row in response.json()] == ["AA-52"]
    assert client.get("/api/recommendations", params={"limit": 1001}).status_code == 422
    assert len(client.get("/api/recommendations", params={"offset": 1}).json()) == 1

    # --- by-key: known item-site with no demand history ----------------------
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": "NOD-52", "site_code": "SITE-1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "not_recommendable_demand"
    assert body["demand_class"] == "no_demand"
    assert body["current"] == {"reorder_point": 5.0, "safety_stock": 1.0}
    assert body["safety_stock"] is None and body["reorder_point"] is None
    assert body["delta_reorder_point"] is None

    # --- by-key: unknown item-site is a 404 ----------------------------------
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": "GHOST-52", "site_code": "SITE-1"},
    )
    assert response.status_code == 404

    # --- read-only: every mutating method is 405 -----------------------------
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/recommendations")
        assert response.status_code == 405, (method, response.status_code)

    # --- hostile inputs: 422, never 500 --------------------------------------
    assert (
        client.get(
            "/api/recommendations/by-key",
            params={"item_code": "WIDGET-52\x00", "site_code": "SITE-1"},
        ).status_code
        == 422
    )
    assert (
        client.get("/api/recommendations", params={"item_code": "X" * 65}).status_code
        == 422
    )
    assert (
        client.get("/api/recommendations", params={"as_of": "not-a-date"}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/recommendations", params={"item_code": "WID\x1fGET"}
        ).status_code
        == 200
    )

    # --- the service level is read at call time and 400s, never 500s ---------
    monkeypatch.setenv("AIERP_SERVICE_LEVEL", "1.5")
    response = client.get("/api/recommendations", params={"as_of": "2026-09-30"})
    assert response.status_code == 400
    assert "AIERP_SERVICE_LEVEL" in response.json()["detail"]
    monkeypatch.setenv("AIERP_SERVICE_LEVEL", "0.98")
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": "WIDGET-52", "site_code": "SITE-1"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["safety_stock"] == pytest.approx(
        statistics.NormalDist().inv_cdf(0.98) * 4.0
    )
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
