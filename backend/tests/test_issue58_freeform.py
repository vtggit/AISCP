"""Issue #58 proof: reorder point drift, priced in money and ranked.

Pure ``app.recommend.drift.assess_drift`` (no database), with inputs
d = 10, sd_d = 3, LT = 12, sd_LT = 2 and the service-level-0.95
recommended reorder point 157.073:
  * current 500 at unit cost 2.5 is "over" with money_at_stake
    857.32 (2 places),
  * current 120 with order_qty 100 at unit cost 4 is "under" with
    sigma_l 22.5389, k 0.0, units_short_per_cycle 8.992,
    cycles_per_year 36.5, units_short_per_year 328.197 and
    money_at_stake 1312.79,
  * current 100 with no order_qty at unit cost 4 assumes order_qty
    300, carries "order quantity assumed: 30 days of demand" and gives
    money_at_stake 1086.07,
  * a None reorder point is treated as 0 and carries
    "no reorder point set",
  * a None unit_cost gives money_at_stake None with "cost unknown",
  * an under-protected case whose computed units_short_per_year
    exceeds 365 * d is capped to exactly 365 * d and carries
    "capped at annual demand" (d = 10, LT = 12, sd_d = 3,
    sd_LT = 2, current 0, order_qty 1 gives 3650 units short per
    year).

Against the test database (A-58..F-58 at SITE-1, each with constant
10 units/day demand and lead times of 10, 12 and 14 days, so d = 10,
sd_d = 0, LT = 12, sd_LT = 2 and recommended reorder point 152.897;
INT-58 intermittent with no ok recommendation; date.today pinned to
2026-10-02 so the default as_of is the seeded 2026-09-30):
  * drift_ranked puts every "drift" row before every
    "no_reorder_point" row, orders known money_at_stake descending
    within each category (unknown money last, by absolute
    units_gap) and ranks 1, 2, ... only the "drift" rows with a known
    money (rank None for every other row),
  * /api/drift?category=no_reorder_point returns only those rows and
    category=other 422s, /api/drift?direction=under returns only
    under rows, direction=sideways 422s, limit 1001 422s,
  * /by-key returns the item-site's row and 404s an item-site without
    an ok recommendation,
  * POST/PUT/PATCH/DELETE are 405 (read-only API) and hostile query
    values are rejected 422.
"""

import statistics
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection
from app.recommend.drift import assess_drift, drift_ranked
from app.recommend.reorder import recommend

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


def test_issue58_freeform(client, monkeypatch):
    # --- pure: the 0.95 reference recommendation ---------------------------
    recommendation = recommend(DEMAND, LEAD_TIME, service_level=0.95)
    assert recommendation["status"] == "ok"
    assert round(recommendation["reorder_point"], 3) == 157.073
    assert recommendation["inputs"] == {
        "d": 10.0,
        "sd_d": 3.0,
        "lt": 12.0,
        "sd_lt": 2.0,
        "z": statistics.NormalDist().inv_cdf(0.95),
        "service_level": 0.95,
    }
    recommended = recommendation["reorder_point"]

    def site(reorder_point, unit_cost, order_qty):
        return {
            "reorder_point": reorder_point,
            "unit_cost": unit_cost,
            "order_qty": order_qty,
        }

    # --- pure: an over-protected point ties up excess stock ----------------
    result = assess_drift(recommendation, site(500, 2.5, 100))
    assert result["direction"] == "over"
    assert result["units_gap"] == pytest.approx(500.0 - recommended)
    assert round(result["money_at_stake"], 2) == 857.32
    assert result["unit_cost"] == 2.5
    assert result["notes"] == []
    assert "units_gap * unit_cost" in result["money_basis"]
    assert "857.32" in result["money_basis"]
    for under_key in (
        "mu_l",
        "sigma_l",
        "k",
        "units_short_per_cycle",
        "order_qty",
        "cycles_per_year",
        "units_short_per_year",
    ):
        assert under_key not in result

    # --- pure: under-protected at the mean demand during lead time ---------
    result = assess_drift(recommendation, site(120, 4.0, 100))
    assert result["direction"] == "under"
    assert result["units_gap"] == pytest.approx(120.0 - recommended)
    assert result["mu_l"] == 120.0
    assert round(result["sigma_l"], 4) == 22.5389
    assert result["k"] == 0.0
    assert round(result["units_short_per_cycle"], 3) == 8.992
    assert result["cycles_per_year"] == 36.5
    assert round(result["units_short_per_year"], 3) == 328.197
    assert round(result["money_at_stake"], 2) == 1312.79
    assert result["notes"] == []
    assert "units_short_per_year * unit_cost" in result["money_basis"]
    assert "1312.79" in result["money_basis"]

    # --- pure: a missing order quantity is assumed as 30 days of demand ----
    result = assess_drift(recommendation, site(100, 4.0, None))
    assert result["direction"] == "under"
    assert result["order_qty"] == 300.0
    assert "order quantity assumed: 30 days of demand" in result["notes"]
    assert "capped at annual demand" not in result["notes"]
    assert round(result["money_at_stake"], 2) == 1086.07

    # --- pure: a missing reorder point is treated as 0 ----------------------
    result = assess_drift(recommendation, site(None, 2.5, 100))
    assert result["direction"] == "under"
    assert "no reorder point set" in result["notes"]
    assert result["units_gap"] == pytest.approx(-recommended)
    assert result["units_short_per_year"] == 3650.0
    assert "capped at annual demand" in result["notes"]
    assert result["money_at_stake"] == pytest.approx(9125.0)

    # --- pure: an unknown unit cost leaves the money unpriced ---------------
    result = assess_drift(recommendation, site(500, None, 100))
    assert result["direction"] == "over"
    assert result["money_at_stake"] is None
    assert result["unit_cost"] is None
    assert "cost unknown" in result["notes"]
    assert "unknown" in result["money_basis"]
    result = assess_drift(recommendation, site(100, None, 100))
    assert result["direction"] == "under"
    assert result["money_at_stake"] is None
    assert "cost unknown" in result["notes"]

    # --- pure: the shortage is capped at the annual demand ------------------
    result = assess_drift(recommendation, site(0, 1.0, 1))
    assert result["direction"] == "under"
    assert "no reorder point set" in result["notes"]
    assert "capped at annual demand" in result["notes"]
    assert result["cycles_per_year"] == 3650.0
    assert result["units_short_per_year"] == 3650.0
    assert result["money_at_stake"] == pytest.approx(3650.0)

    # --- seed: six ranked item-sites and one intermittent -------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            first_day = AS_OF - timedelta(days=359)
            lead_times = (
                ("2026-08-22", "2026-09-01"),
                ("2026-08-29", "2026-09-10"),
                ("2026-08-28", "2026-09-11"),
            )
            for item_code in ("A-58", "B-58", "C-58", "D-58", "E-58", "F-58"):
                cur.executemany(
                    "INSERT INTO demand_events "
                    "(id, business_key, item_code, site_code, quantity, "
                    "occurred_on, event_type, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            f"id-de-58-{item_code}-{day_index}",
                            f"de-58-{item_code}-{day_index}",
                            item_code,
                            "SITE-1",
                            10.0,
                            (first_day + timedelta(days=day_index)).isoformat(),
                            "shipment",
                            "test",
                        )
                        for day_index in range(360)
                    ],
                )
                cur.executemany(
                    "INSERT INTO lead_time_observations "
                    "(id, business_key, supplier_code, item_code, "
                    "ordered_on, received_on, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            f"id-lt-58-{item_code}-{lead_index}",
                            f"lt-58-{item_code}-{lead_index}",
                            f"SUP-{item_code}",
                            item_code,
                            ordered_on,
                            received_on,
                            "test",
                        )
                        for lead_index, (ordered_on, received_on) in enumerate(
                            lead_times
                        )
                    ],
                )
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-de-58-int-1",
                        "de-58-int-1",
                        "INT-58",
                        "SITE-1",
                        60.0,
                        "2025-10-04",
                        "shipment",
                        "test",
                    ),
                    (
                        "id-de-58-int-2",
                        "de-58-int-2",
                        "INT-58",
                        "SITE-1",
                        60.0,
                        "2026-09-30",
                        "shipment",
                        "test",
                    ),
                ],
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "order_qty, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    ("id-is-58-a", "A-58", "SITE-1", 2.0, 400.0, 100.0, "test"),
                    ("id-is-58-b", "B-58", "SITE-1", 4.0, 100.0, 100.0, "test"),
                    ("id-is-58-c", "C-58", "SITE-1", 0.5, 600.0, 100.0, "test"),
                    ("id-is-58-d", "D-58", "SITE-1", None, 50.0, 100.0, "test"),
                    ("id-is-58-e", "E-58", "SITE-1", 1.0, None, 100.0, "test"),
                    ("id-is-58-f", "F-58", "SITE-1", None, None, None, "test"),
                    ("id-is-58-int", "INT-58", "SITE-1", 1.0, 10.0, 10.0, "test"),
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

    # Seeded items: d = 10, sd_d = 0, LT = 12, sd_LT = 2, so sigma_l = 20.
    z95 = statistics.NormalDist().inv_cdf(0.95)
    normal = statistics.NormalDist()
    expected_reorder_point = 120.0 + z95 * 20.0
    assert round(expected_reorder_point, 3) == 152.897

    def expected_under(current, unit_cost, order_qty):
        k = (current - 120.0) / 20.0
        per_cycle = 20.0 * (normal.pdf(k) - k * (1.0 - normal.cdf(k)))
        per_year = min(per_cycle * (3650.0 / order_qty), 3650.0)
        money = None if unit_cost is None else per_year * unit_cost
        return money, per_year

    money_a = (400.0 - expected_reorder_point) * 2.0
    money_b, usy_b = expected_under(100.0, 4.0, 100.0)
    money_c = (600.0 - expected_reorder_point) * 0.5
    money_d, usy_d = expected_under(50.0, None, 100.0)
    money_e, usy_e = expected_under(0.0, 1.0, 100.0)
    money_f, usy_f = expected_under(0.0, None, 300.0)
    assert money_d is None and money_f is None and money_e == 3650.0

    # --- drift_ranked: category blocks, money order, ranks ------------------
    rows = drift_ranked(as_of=AS_OF)
    assert [row["item_code"] for row in rows] == [
        "B-58",
        "A-58",
        "C-58",
        "D-58",
        "E-58",
        "F-58",
    ]
    assert [row["category"] for row in rows] == ["drift"] * 4 + ["no_reorder_point"] * 2
    assert [row["rank"] for row in rows] == [1, 2, 3, None, None, None]
    by_code = {row["item_code"]: row for row in rows}

    for row in rows:
        assert row["site_code"] == "SITE-1"
        assert row["as_of"] == AS_OF
        assert row["reorder_point"] == pytest.approx(expected_reorder_point)
        assert row["reorder_point_units"] == 153
        assert row["safety_stock"] == pytest.approx(z95 * 20.0)
        assert "152.90" in row["arithmetic"]

    assert by_code["B-58"]["direction"] == "under"
    assert by_code["B-58"]["current_reorder_point"] == 100.0
    assert by_code["B-58"]["money_at_stake"] == pytest.approx(money_b)
    assert by_code["B-58"]["units_short_per_year"] == pytest.approx(usy_b)
    assert by_code["A-58"]["direction"] == "over"
    assert by_code["A-58"]["units_gap"] == pytest.approx(400.0 - expected_reorder_point)
    assert by_code["A-58"]["money_at_stake"] == pytest.approx(money_a)
    assert by_code["C-58"]["money_at_stake"] == pytest.approx(money_c)
    assert by_code["D-58"]["direction"] == "under"
    assert by_code["D-58"]["money_at_stake"] is None
    assert by_code["D-58"]["units_short_per_year"] == pytest.approx(usy_d)
    assert "cost unknown" in by_code["D-58"]["notes"]
    assert by_code["E-58"]["category"] == "no_reorder_point"
    assert by_code["E-58"]["current_reorder_point"] is None
    assert "no reorder point set" in by_code["E-58"]["notes"]
    assert "capped at annual demand" in by_code["E-58"]["notes"]
    assert by_code["E-58"]["units_short_per_year"] == usy_e == 3650.0
    assert by_code["E-58"]["money_at_stake"] == pytest.approx(money_e)
    assert by_code["F-58"]["category"] == "no_reorder_point"
    assert by_code["F-58"]["money_at_stake"] is None
    assert "cost unknown" in by_code["F-58"]["notes"]
    assert "order quantity assumed: 30 days of demand" in by_code["F-58"]["notes"]
    assert by_code["F-58"]["order_qty"] == 300.0
    assert by_code["F-58"]["units_short_per_year"] == pytest.approx(usy_f)
    assert "INT-58" not in by_code  # no ok recommendation, so not ranked

    # --- API: the ranked list -------------------------------------------------
    params = {"as_of": AS_OF.isoformat()}
    response = client.get("/api/drift", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == [
        "B-58",
        "A-58",
        "C-58",
        "D-58",
        "E-58",
        "F-58",
    ]
    assert [row["rank"] for row in body] == [1, 2, 3, None, None, None]
    first = body[0]
    assert first["as_of"] == "2026-09-30"
    assert first["category"] == "drift"
    assert first["direction"] == "under"
    assert first["current_reorder_point"] == 100.0
    assert first["mu_l"] == 120.0
    assert first["sigma_l"] == 20.0
    assert first["k"] == -1.0
    assert first["order_qty"] == 100.0
    assert first["cycles_per_year"] == 36.5
    assert first["units_short_per_year"] == pytest.approx(usy_b)
    assert first["money_at_stake"] == pytest.approx(money_b)
    assert first["unit_cost"] == 4.0
    assert first["reorder_point"] == pytest.approx(expected_reorder_point)
    assert first["reorder_point_units"] == 153
    assert first["safety_stock"] == pytest.approx(z95 * 20.0)
    assert "152.90" in first["arithmetic"]
    assert "units_short_per_year * unit_cost" in first["money_basis"]

    response = client.get(
        "/api/drift", params={**params, "category": "no_reorder_point"}
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["item_code"] for row in rows] == ["E-58", "F-58"]
    assert all(row["category"] == "no_reorder_point" for row in rows)

    response = client.get("/api/drift", params={**params, "direction": "under"})
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["item_code"] for row in rows] == ["B-58", "D-58", "E-58", "F-58"]
    assert all(row["direction"] == "under" for row in rows)

    response = client.get("/api/drift", params={**params, "direction": "over"})
    assert response.status_code == 200, response.text
    assert [row["item_code"] for row in response.json()] == ["A-58", "C-58"]

    response = client.get("/api/drift", params={**params, "limit": 2, "offset": 2})
    assert response.status_code == 200, response.text
    assert [row["item_code"] for row in response.json()] == ["C-58", "D-58"]

    # --- API: rejected inputs are 422, never 500 -----------------------------
    assert (
        client.get("/api/drift", params={**params, "category": "other"}).status_code
        == 422
    )
    assert (
        client.get("/api/drift", params={**params, "direction": "sideways"}).status_code
        == 422
    )
    assert client.get("/api/drift", params={**params, "limit": 1001}).status_code == 422
    assert client.get("/api/drift", params={**params, "limit": 0}).status_code == 422
    assert client.get("/api/drift", params={**params, "limit": ""}).status_code == 422
    assert (
        client.get("/api/drift", params={**params, "limit": "x" * 100}).status_code
        == 422
    )
    assert client.get("/api/drift", params={**params, "offset": -1}).status_code == 422
    assert (
        client.get("/api/drift", params={**params, "as_of": "not-a-date"}).status_code
        == 422
    )

    # --- API: by-key ------------------------------------------------------------
    response = client.get(
        "/api/drift/by-key", params={"item_code": "A-58", "site_code": "SITE-1"}
    )
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["item_code"] == "A-58"
    assert row["direction"] == "over"
    assert row["rank"] == 2
    assert row["money_at_stake"] == pytest.approx(money_a)
    assert row["current_reorder_point"] == 400.0

    # An item-site without an ok recommendation is a 404, and so is a key
    # that is not in item_sites at all.
    response = client.get(
        "/api/drift/by-key", params={"item_code": "INT-58", "site_code": "SITE-1"}
    )
    assert response.status_code == 404
    assert (
        client.get(
            "/api/drift/by-key",
            params={"item_code": "GHOST-58", "site_code": "SITE-1"},
        ).status_code
        == 404
    )
    assert (
        client.get("/api/drift/by-key", params={"item_code": "A-58"}).status_code == 422
    )
    assert (
        client.get(
            "/api/drift/by-key",
            params={"item_code": "A-58\x00", "site_code": "SITE-1"},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/drift/by-key",
            params={"item_code": "X" * 65, "site_code": "SITE-1"},
        ).status_code
        == 422
    )

    # --- read-only: every mutating method is 405 -------------------------------
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/drift")
        assert response.status_code == 405, (method, response.status_code)
    assert client.post("/api/drift/by-key", json={}).status_code == 405
