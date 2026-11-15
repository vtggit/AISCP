"""Issue #360 proof: reorder points on history or the forecast.

AC-1 — the setting, through the existing settings API (default is
``history``; an admin sets ``forecast``; a non-admin is refused 403;
an unknown value is refused 422 naming the allowed values):
  * GET /api/settings lists ``replenishment.demand_basis`` with
    default ``history`` and allowed ``["history", "forecast"]``.

AC-2 — ``app.forecast.basis.forecast_demand`` against the test
database (as-of 2026-09-30, date.today pinned to 2026-10-02):
  * X360, a constant 2.0 units a day for 360 days (12 buckets of 60,
    every candidate backtests to MAE 0 so the mean method wins, all
    forecast periods 60): with a 10-day lead time the window is 10
    days and each day takes 60/30 = 2.0 of the first period's
    effective quantity, so daily = 2.0 and sd_daily =
    1.2533 * 0.0 / sqrt(30) = 0.0 (variability "backtest");
  * a 10.2-day lead time gives a window of 11 days (ceil), daily
    still 2.0;
  * W360, with only two history buckets (30 and 90) gets the mean
    method with no backtest, so variability is "history" and
    sd_daily is the historical std_daily = sqrt(1800)/sqrt(30)
    = sqrt(60) = 7.745966692414834, and daily is the mean bucket 60
    over 30 = 2.0;
  * Y360, 1.0 a day for 330 days (11 buckets of 30) then 2.0 a day
    for the last 30 (60): the mean method wins the backtest with
    MAE (0+0+0+0+0+30)/6 = 5.0 and every period 390/12 = 32.5, so
    with a 10-day window daily = 32.5/30 = 1.083333 and sd_daily =
    1.2533 * 5.0 / sqrt(30) = 1.144101;
  * a planner override of the first forecast period (2026-10-01 to
    2026-10-30) to 100 units moves daily from 32.5/30 to 100/30, by
    (100 - 32.5)/30 = 2.25, and leaves the historical demand inputs
    untouched;
  * a 35-day window crosses from the overridden first period into
    the second: 30 days take 100/30 each and 5 days take 32.5/30
    each, so daily = (100 + 5 * 32.5/30) / 35 = 3.011905;
  * the effective forecast forecast_demand uses is the one
    GET /api/forecast/by-key serves (period 1: statistical 32.5,
    effective 100; period 2: 32.5, 32.5).

AC-3 — the recommendations (X360 and Y360 with three lead-time
observations of 8, 10 and 12 days, so LT 10 and sd_LT 2, and z 1.6449
at service level 0.95; W360 the same lead time; L360 a lumpy item
(ADI 6, CV2 0.51) and N360 a stopped item (no_demand, no forecast)):
  * with the default setting, every existing field of
    GET /api/recommendations and /by-key equals what ``recommend``
    gives on the historical inputs, demand_basis is "history" and
    other_basis holds the hand-computed forecast-basis reorder point
    (Y360: d 100/30 = 3.333333, sd_d 1.144101, so safety stock
    1.644854 * sqrt(10 * 1.308967 + 4 * 11.111111) = 12.476420 and
    reorder point 33.333333 + 12.476420 = 45.809753);
  * with the setting at forecast, the two are swapped: the existing
    fields are the forecast-basis values (Y360 reorder point
    45.809753), the note "forecast demand over 10 days" is present
    (and "no backtest: historical variability used" for W360), and
    delta_reorder_point follows the active basis (Y360: 25.0 -
    45.809753 = -20.809753);
  * L360 is not recommendable on either basis, other_basis null;
  * N360 under the forecast setting is built on history with the
    note "no forecast: history used" and demand_basis "history";
  * GET /api/drift returns the same rows as before with the default
    setting (Y360 over by 25.0 - 19.796570 = 5.203430 at 2.0/unit
    = 10.406860; X360 under, mu_l 20, sigma_l 4, k 1.25, 0.20234747
    short per cycle, 12.166667 cycles a year -> 2.46189426 at
    1.0/unit).
"""

import math
import statistics
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection
from app.forecast.basis import MAE_TO_STD, forecast_demand
from app.recommend.reorder import recommend
from app.stats.demand import demand_stats_for_pair
from app.stats.lead_time import lead_time_stats_for_item

AS_OF = date(2026, 9, 30)
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
SITE = "SITE-1"
KEY = "replenishment.demand_basis"
Z95 = statistics.NormalDist().inv_cdf(0.95)  # 1.644854

EXISTING_FIELDS = (
    "status",
    "demand_class",
    "safety_stock",
    "reorder_point",
    "safety_stock_units",
    "reorder_point_units",
    "inputs",
    "arithmetic",
    "method",
    "notes",
)


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


def test_issue360_freeform(client, monkeypatch, admin_headers, user_headers):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    # --- seed -------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            de = (
                "INSERT INTO demand_events (id, business_key, item_code, "
                "site_code, quantity, occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            )
            # X360: constant 2.0 a day, 360 days -> 12 buckets of 60.
            cur.executemany(
                de,
                [
                    (
                        f"id-de-360-x-{i}",
                        f"de-360-x-{i}",
                        "X360",
                        SITE,
                        2.0,
                        (FIRST_DAY + timedelta(days=i)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for i in range(360)
                ],
            )
            # Y360: 1.0 a day for 330 days (11 buckets of 30) then 2.0 a
            # day for the last 30 -> buckets [30 x 11, 60], total 390.
            cur.executemany(
                de,
                [
                    (
                        f"id-de-360-y-{i}",
                        f"de-360-y-{i}",
                        "Y360",
                        SITE,
                        1.0,
                        (FIRST_DAY + timedelta(days=i)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for i in range(330)
                ],
            )
            cur.executemany(
                de,
                [
                    (
                        f"id-de-360-y-l-{i}",
                        f"de-360-y-l-{i}",
                        "Y360",
                        SITE,
                        2.0,
                        (AS_OF - timedelta(days=29) + timedelta(days=i)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for i in range(30)
                ],
            )
            # W360: 1.0 a day for 30 days then 3.0 a day for the last 30
            # -> two buckets, [30, 90].
            cur.executemany(
                de,
                [
                    (
                        f"id-de-360-w-{i}",
                        f"de-360-w-{i}",
                        "W360",
                        SITE,
                        1.0,
                        (AS_OF - timedelta(days=59) + timedelta(days=i)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for i in range(30)
                ]
                + [
                    (
                        f"id-de-360-w-l-{i}",
                        f"de-360-w-l-{i}",
                        "W360",
                        SITE,
                        3.0,
                        (AS_OF - timedelta(days=29) + timedelta(days=i)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for i in range(30)
                ],
            )
            # L360: 10 units on the first day of the window and 2.0 a day
            # for the last 30 -> buckets [10, 0 x 10, 60]: ADI 6 and
            # CV2 (625/1225) 0.51 -> lumpy.
            cur.execute(
                de,
                (
                    "id-de-360-l-first",
                    "de-360-l-first",
                    "L360",
                    SITE,
                    10.0,
                    FIRST_DAY.isoformat(),
                    "shipment",
                    "test",
                ),
            )
            cur.executemany(
                de,
                [
                    (
                        f"id-de-360-l-{i}",
                        f"de-360-l-{i}",
                        "L360",
                        SITE,
                        2.0,
                        (AS_OF - timedelta(days=29) + timedelta(days=i)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for i in range(30)
                ],
            )
            # N360: one old demand event (14 months before the as-of) ->
            # every bucket in the window is zero -> no_demand, no forecast.
            cur.execute(
                de,
                (
                    "id-de-360-n",
                    "de-360-n",
                    "N360",
                    SITE,
                    1.0,
                    "2025-06-01",
                    "shipment",
                    "test",
                ),
            )
            # Lead times of 8, 10 and 12 days -> mean 10, sample std 2,
            # three observations (not low confidence).
            for item in ("X360", "Y360", "W360", "L360"):
                for i, (ordered, received) in enumerate(
                    (
                        ("2026-08-22", "2026-08-30"),
                        ("2026-08-20", "2026-08-30"),
                        ("2026-08-18", "2026-08-30"),
                    )
                ):
                    cur.execute(
                        "INSERT INTO lead_time_observations (id, business_key, "
                        "supplier_code, item_code, ordered_on, received_on, "
                        "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (
                            f"id-lt-360-{item}-{i}",
                            f"lt-360-{item}-{i}",
                            f"SUP-360-{item}",
                            item,
                            ordered,
                            received,
                            "test",
                        ),
                    )
            # item_sites rows: the current planning settings.
            cur.execute(
                "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
                "reorder_point, safety_stock, source_system) "
                "VALUES ('id-is-360-x', 'X360', %s, 1.0, 25.0, 10.0, 'test')",
                (SITE,),
            )
            cur.execute(
                "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
                "reorder_point, safety_stock, source_system) "
                "VALUES ('id-is-360-y', 'Y360', %s, 2.0, 25.0, 5.0, 'test')",
                (SITE,),
            )
        conn.commit()
    finally:
        conn.close()

    # --- AC-1: the setting through the existing settings API --------------
    response = client.get("/api/settings")
    assert response.status_code == 200, response.text
    entries = {entry["key"]: entry for entry in response.json()}
    entry = entries[KEY]
    assert entry["scope"] == "system"
    assert entry["type"] == "enum"
    assert entry["allowed"] == ["history", "forecast"]
    assert entry["default"] == "history"
    assert entry["value"] == "history"
    assert entry["source"] == "default"
    assert entry["description"].strip()

    response = client.put(
        f"/api/settings/{KEY}", json={"value": "forecast"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "forecast"
    assert response.json()["source"] == "system"
    entries = {e["key"]: e for e in client.get("/api/settings").json()}
    assert entries[KEY]["value"] == "forecast"
    assert entries[KEY]["source"] == "system"

    # A non-admin cannot change a system setting.
    response = client.put(
        f"/api/settings/{KEY}", json={"value": "history"}, headers=user_headers
    )
    assert response.status_code == 403

    # An unknown value is refused, naming the allowed values.
    response = client.put(
        f"/api/settings/{KEY}", json={"value": "gut feeling"}, headers=admin_headers
    )
    assert response.status_code == 422, response.text
    assert "history, forecast" in response.json()["detail"]

    # Reset to the default for the default-setting section.
    response = client.delete(f"/api/settings/{KEY}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "history"
    assert response.json()["source"] == "default"

    # --- AC-2: forecast_demand ---------------------------------------------
    # X360, 10-day lead time: window 10, all days in the first period
    # (effective 60, no override): each day takes 60/30 = 2.0, so daily
    # = (10 * 2.0) / 10 = 2.0. The backtest MAE is 0.0 on the constant
    # history, so sd_daily = 1.2533 * 0.0 / sqrt(30) = 0.0.
    result = forecast_demand("X360", SITE, AS_OF, 10)
    assert result == {
        "daily": 2.0,
        "sd_daily": 0.0,
        "window_days": 10,
        "variability": "backtest",
    }

    # A 10.2-day lead time: ceil -> an 11-day window; daily unchanged.
    result = forecast_demand("X360", SITE, AS_OF, 10.2)
    assert result["window_days"] == 11
    assert result["daily"] == pytest.approx(2.0)
    assert result["variability"] == "backtest"

    # W360, fewer than 12 history buckets: the mean method with no
    # backtest -> variability "history", sd_daily the historical
    # std_daily (sample std of [30, 90] = sqrt(1800), over sqrt(30)
    # = sqrt(60) = 7.745966692414834); daily = mean bucket 60 / 30.
    history_w = demand_stats_for_pair("W360", SITE, AS_OF)
    assert history_w["std_daily"] == pytest.approx(math.sqrt(60))
    result = forecast_demand("W360", SITE, AS_OF, 10)
    assert result["window_days"] == 10
    assert result["daily"] == pytest.approx(2.0)
    assert result["variability"] == "history"
    assert result["sd_daily"] == history_w["std_daily"]

    # Y360 before the override: the mean method wins the backtest with
    # MAE (0+0+0+0+0+30)/6 = 5.0 and every period 390/12 = 32.5, so
    # daily = 32.5/30 = 1.083333 and sd_daily = 1.2533 * 5.0 / sqrt(30)
    # = 1.144101.
    history_y_before = demand_stats_for_pair("Y360", SITE, AS_OF)
    result = forecast_demand("Y360", SITE, AS_OF, 10)
    assert result["window_days"] == 10
    assert result["daily"] == pytest.approx(32.5 / 30)
    assert result["sd_daily"] == pytest.approx(MAE_TO_STD * 5.0 / math.sqrt(30))
    assert result["variability"] == "backtest"

    # The planner override of the first forecast period (2026-10-01, the
    # day after the as-of, through 2026-10-30) to 100 units.
    response = client.post(
        "/api/forecast/overrides",
        json={
            "item_code": "Y360",
            "site_code": SITE,
            "period_start": "2026-10-01",
            "quantity": 100.0,
            "reason": "planner test for issue 360",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["applies"] is True

    # The override changes daily by the hand-computed amount: every
    # window day now takes 100/30 of the override instead of 32.5/30,
    # so daily moves from 1.083333 to 3.333333, by (100 - 32.5)/30
    # = 2.25 — and the historical inputs are untouched.
    result = forecast_demand("Y360", SITE, AS_OF, 10)
    assert result["daily"] == pytest.approx(100.0 / 30)
    assert result["daily"] - (32.5 / 30) == pytest.approx((100.0 - 32.5) / 30)
    assert result["sd_daily"] == pytest.approx(MAE_TO_STD * 5.0 / math.sqrt(30))
    history_y_after = demand_stats_for_pair("Y360", SITE, AS_OF)
    for field in ("mean_daily", "std_daily", "demand_class", "recommendable"):
        assert history_y_after[field] == history_y_before[field], field
    # Hand-computed: mean_daily = 390/360 = 1.083333, std_daily =
    # sample std of [30 x 11, 60] over sqrt(30) = sqrt(75)/sqrt(30)
    # = sqrt(2.5) = 1.581139.
    assert history_y_after["mean_daily"] == pytest.approx(390.0 / 360)
    assert history_y_after["std_daily"] == pytest.approx(math.sqrt(2.5))

    # A 35-day window crosses from the first forecast period (overridden
    # to 100) into the second (still 32.5): 30 days take 100/30 each
    # and 5 days take 32.5/30 each, so daily =
    # (30 * 100/30 + 5 * 32.5/30) / 35 = 105.416667 / 35 = 3.011905.
    result = forecast_demand("Y360", SITE, AS_OF, 35)
    assert result["window_days"] == 35
    assert result["daily"] == pytest.approx((30 * (100.0 / 30) + 5 * (32.5 / 30)) / 35)

    # The effective forecast forecast_demand uses is the one the
    # forecast API serves.
    response = client.get(
        "/api/forecast/by-key",
        params={"item_code": "Y360", "site_code": SITE, "as_of": "2026-09-30"},
    )
    assert response.status_code == 200, response.text
    row = response.json()
    assert row["forecast"][0]["quantity"] == pytest.approx(32.5)
    assert row["forecast"][0]["effective_quantity"] == pytest.approx(100.0)
    assert row["forecast"][0]["override_days"] == 30
    assert row["forecast"][1]["quantity"] == pytest.approx(32.5)
    assert row["forecast"][1]["effective_quantity"] == pytest.approx(32.5)
    assert row["forecast"][1]["override_days"] == 0
    assert row["overridden_buckets"] == 1

    # --- AC-3: default setting (history) ------------------------------------
    # Hand-computed history basis, Y360: d = 390/360 = 1.083333,
    # sd_d = sqrt(2.5) = 1.581139, LT 10, sd_LT 2, z = 1.644854:
    # safety_stock = 1.644854 * sqrt(10 * 2.5 + 4 * 1.173611)
    #              = 1.644854 * sqrt(29.694444) = 1.644854 * 5.449257
    #              = 8.963237; reorder_point = 10.833333 + 8.963237
    #              = 19.796570.
    d_y = 390.0 / 360.0
    sd_y = math.sqrt(2.5)
    ss_hist_y = Z95 * math.sqrt(10 * 2.5 + 4 * d_y**2)
    rp_hist_y = d_y * 10 + ss_hist_y
    # Hand-computed forecast basis, Y360 (first period overridden to 100):
    # d = 100/30 = 3.333333, sd_d = 1.2533 * 5.0 / sqrt(30) = 1.144101:
    # safety_stock = 1.644854 * sqrt(10 * 1.308967 + 4 * 11.111111)
    #              = 1.644854 * sqrt(57.534118) = 1.644854 * 7.585125
    #              = 12.476420; reorder_point = 33.333333 + 12.476420
    #              = 45.809753.
    d_yf = 100.0 / 30.0
    sd_yf = MAE_TO_STD * 5.0 / math.sqrt(30)
    ss_fc_y = Z95 * math.sqrt(10 * sd_yf**2 + 4 * d_yf**2)
    rp_fc_y = d_yf * 10 + ss_fc_y

    response = client.get(
        "/api/recommendations", params={"as_of": "2026-09-30", "limit": 1000}
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [(r["item_code"], r["site_code"]) for r in rows] == [
        ("L360", SITE),
        ("N360", SITE),
        ("W360", SITE),
        ("X360", SITE),
        ("Y360", SITE),
    ]
    l_row, n_row, w_row, x_row, y_row = rows

    # X360: every existing field equals what recommend gives on the
    # historical inputs. Hand-computed: d 2.0, sd_d 0.0, LT 10, sd_LT 2:
    # safety_stock = 1.644854 * sqrt(10 * 0 + 4 * 4) = 1.644854 * 4
    # = 6.579415; reorder_point = 20 + 6.579415 = 26.579415.
    demand_x = demand_stats_for_pair("X360", SITE, AS_OF)
    lt_x = lead_time_stats_for_item("X360", AS_OF)
    pure_x = recommend(demand_x, lt_x)
    for field in EXISTING_FIELDS:
        assert x_row[field] == pure_x[field], field
    assert x_row["status"] == "ok"
    assert x_row["demand_basis"] == "history"
    assert x_row["safety_stock"] == pytest.approx(Z95 * 4.0)
    assert x_row["reorder_point"] == pytest.approx(20.0 + Z95 * 4.0)
    assert x_row["safety_stock_units"] == 7
    assert x_row["reorder_point_units"] == 27
    assert x_row["notes"] == []
    assert x_row["current"] == {"reorder_point": 25.0, "safety_stock": 10.0}
    assert x_row["delta_reorder_point"] == pytest.approx(25.0 - (20.0 + Z95 * 4.0))
    # The forecast basis of a flat item: daily 2.0 and sd 0.0, the same
    # numbers, so the other basis' reorder point equals this one.
    assert x_row["other_basis"]["basis"] == "forecast"
    assert x_row["other_basis"]["reorder_point"] == pytest.approx(
        x_row["reorder_point"]
    )
    assert x_row["other_basis"]["inputs"]["d"] == pytest.approx(2.0)
    assert x_row["other_basis"]["inputs"]["sd_d"] == pytest.approx(0.0)

    # Y360 by-key: the existing fields equal the historical inputs'
    # result, demand_basis "history", and other_basis holds the
    # hand-computed forecast-basis reorder point.
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": "Y360", "site_code": SITE, "as_of": "2026-09-30"},
    )
    assert response.status_code == 200, response.text
    y = response.json()
    demand_y = demand_stats_for_pair("Y360", SITE, AS_OF)
    lt_y = lead_time_stats_for_item("Y360", AS_OF)
    pure_y = recommend(demand_y, lt_y)
    for field in EXISTING_FIELDS:
        assert y[field] == pure_y[field], field
    assert y["status"] == "ok"
    assert y["demand_basis"] == "history"
    assert y["safety_stock"] == pytest.approx(ss_hist_y)
    assert y["reorder_point"] == pytest.approx(rp_hist_y)
    assert y["safety_stock_units"] == 9
    assert y["reorder_point_units"] == 20
    assert y["notes"] == []
    assert y["current"] == {"reorder_point": 25.0, "safety_stock": 5.0}
    assert y["delta_reorder_point"] == pytest.approx(25.0 - rp_hist_y)
    assert y["other_basis"] == {
        "basis": "forecast",
        "safety_stock": pytest.approx(ss_fc_y),
        "reorder_point": pytest.approx(rp_fc_y),
        "safety_stock_units": 13,
        "reorder_point_units": 46,
        "inputs": {
            "d": pytest.approx(d_yf),
            "sd_d": pytest.approx(sd_yf),
            "lt": 10.0,
            "sd_lt": 2.0,
            "z": Z95,
            "service_level": 0.95,
        },
    }

    # W360: d 2.0, sd_d sqrt(60) = 7.745967, LT 10, sd_LT 2:
    # safety_stock = 1.644854 * sqrt(600 + 16) = 1.644854 * 24.819347
    # = 40.824193; reorder_point = 20 + 40.824193 = 60.824193. With no
    # backtest the forecast basis reuses the historical spread, so its
    # reorder point equals the history one.
    sd_w = math.sqrt(60)
    ss_w = Z95 * math.sqrt(10 * 60 + 4 * 4)
    assert w_row["status"] == "ok"
    assert w_row["demand_basis"] == "history"
    assert w_row["safety_stock"] == pytest.approx(ss_w)
    assert w_row["reorder_point"] == pytest.approx(20.0 + ss_w)
    assert w_row["safety_stock_units"] == 41
    assert w_row["reorder_point_units"] == 61
    assert w_row["other_basis"]["basis"] == "forecast"
    assert w_row["other_basis"]["reorder_point"] == pytest.approx(
        w_row["reorder_point"]
    )
    assert w_row["delta_reorder_point"] is None  # no item_sites row

    # L360 (lumpy) and N360 (no forecast): not recommendable, and with
    # the default setting no forecast note and a null other_basis.
    assert l_row["status"] == "not_recommendable_demand"
    assert l_row["demand_class"] == "lumpy"
    assert l_row["demand_basis"] == "history"
    assert l_row["other_basis"] is None
    assert l_row["safety_stock"] is None and l_row["reorder_point"] is None
    assert n_row["status"] == "not_recommendable_demand"
    assert n_row["demand_class"] == "no_demand"
    assert n_row["demand_basis"] == "history"
    assert n_row["other_basis"] is None
    assert n_row["notes"] == []

    # Not-ok shape, checked on the pure result (the response model
    # would fill defaults and hide a missing key): a not-ok result
    # carries null safety_stock / reorder_point keys — not omitted
    # ones — and demand_basis names the active basis. L360 (lumpy)
    # still gets a forecast (the sba method), so under either basis
    # the row names the basis active at the call; N360 has no
    # forecast, so on the forecast basis the no-forecast fallback
    # keeps the history basis and adds the note.
    demand_l = demand_stats_for_pair("L360", SITE, AS_OF)
    lt_l = lead_time_stats_for_item("L360", AS_OF)
    forecast_l = forecast_demand("L360", SITE, AS_OF, lt_l["mean_days"])
    assert forecast_l is not None, "a lumpy item still gets a forecast"
    for basis in ("history", "forecast"):
        not_ok = recommend(
            demand_l,
            lt_l,
            forecast=forecast_l,
            active_basis=basis,
        )
        assert not_ok["status"] == "not_recommendable_demand"
        assert "safety_stock" in not_ok and "reorder_point" in not_ok
        assert not_ok["safety_stock"] is None and not_ok["reorder_point"] is None
        assert not_ok["demand_basis"] == basis
        assert not_ok["other_basis"] is None
    not_ok_n = recommend(
        demand_stats_for_pair("N360", SITE, AS_OF),
        None,
        forecast=None,
        active_basis="forecast",
    )
    assert not_ok_n["demand_basis"] == "history"
    assert not_ok_n["notes"] == ["no forecast: history used"]
    assert not_ok_n["safety_stock"] is None and not_ok_n["reorder_point"] is None

    # Drift: the same rows as before the change with the default
    # setting. Y360 is over: 25.0 - 19.796570 = 5.203430 units at
    # 2.0/unit = 10.406860 (rank 1). X360 is under: mu_l 20, sigma_l 4,
    # k 1.25, normal loss 4 * (phi(1.25) - 1.25 * Q(1.25)) = 0.20234747
    # per cycle, 365 * 2 / 60 = 12.166667 cycles a year -> 2.46189426
    # units at 1.0/unit (rank 2). W360 has no current point.
    response = client.get("/api/drift", params={"as_of": "2026-09-30"})
    assert response.status_code == 200, response.text
    drift = response.json()
    assert [(r["item_code"], r["site_code"]) for r in drift] == [
        ("Y360", SITE),
        ("X360", SITE),
        ("W360", SITE),
    ]
    assert [r["rank"] for r in drift] == [1, 2, None]
    y_drift, x_drift, w_drift = drift
    assert y_drift["category"] == "drift"
    assert y_drift["direction"] == "over"
    assert y_drift["reorder_point"] == pytest.approx(rp_hist_y)
    assert y_drift["units_gap"] == pytest.approx(25.0 - rp_hist_y)
    assert y_drift["money_at_stake"] == pytest.approx((25.0 - rp_hist_y) * 2.0)
    assert x_drift["category"] == "drift"
    assert x_drift["direction"] == "under"
    assert x_drift["reorder_point"] == pytest.approx(20.0 + Z95 * 4.0)
    assert x_drift["mu_l"] == pytest.approx(20.0)
    assert x_drift["sigma_l"] == pytest.approx(4.0)
    assert x_drift["k"] == pytest.approx(1.25)
    # The normal loss, computed with the same NormalDist drift.py uses:
    # 4 * (pdf(1.25) - 1.25 * (1 - cdf(1.25))) = 0.20234747.
    normal = statistics.NormalDist()
    short_per_cycle = 4.0 * (normal.pdf(1.25) - 1.25 * (1.0 - normal.cdf(1.25)))
    assert x_drift["units_short_per_cycle"] == pytest.approx(short_per_cycle)
    assert x_drift["cycles_per_year"] == pytest.approx(365 * 2.0 / 60.0)
    assert x_drift["units_short_per_year"] == pytest.approx(
        short_per_cycle * 365 * 2.0 / 60.0
    )
    assert x_drift["money_at_stake"] == pytest.approx(
        short_per_cycle * 365 * 2.0 / 60.0
    )
    assert x_drift["notes"] == ["order quantity assumed: 30 days of demand"]
    assert w_drift["category"] == "no_reorder_point"

    # --- AC-3: the setting at forecast ---------------------------------------
    response = client.put(
        f"/api/settings/{KEY}", json={"value": "forecast"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text

    # Y360: the two bases are swapped — the existing fields are now the
    # forecast-basis values and other_basis holds the history basis.
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": "Y360", "site_code": SITE, "as_of": "2026-09-30"},
    )
    assert response.status_code == 200, response.text
    y = response.json()
    assert y["status"] == "ok"
    assert y["demand_basis"] == "forecast"
    assert y["safety_stock"] == pytest.approx(ss_fc_y)
    assert y["reorder_point"] == pytest.approx(rp_fc_y)
    assert y["safety_stock_units"] == 13
    assert y["reorder_point_units"] == 46
    assert y["inputs"]["d"] == pytest.approx(d_yf)
    assert y["inputs"]["sd_d"] == pytest.approx(sd_yf)
    assert y["notes"] == ["forecast demand over 10 days"]
    # The delta follows the active (forecast) basis: 25.0 - 45.809753
    # = -20.809753.
    assert y["delta_reorder_point"] == pytest.approx(25.0 - rp_fc_y)
    assert y["other_basis"] == {
        "basis": "history",
        "safety_stock": pytest.approx(ss_hist_y),
        "reorder_point": pytest.approx(rp_hist_y),
        "safety_stock_units": 9,
        "reorder_point_units": 20,
        "inputs": {
            "d": pytest.approx(d_y),
            "sd_d": pytest.approx(sd_y),
            "lt": 10.0,
            "sd_lt": 2.0,
            "z": Z95,
            "service_level": 0.95,
        },
    }

    # X360: built on the forecast; a flat item keeps the same numbers and
    # the note names the window.
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": "X360", "site_code": SITE, "as_of": "2026-09-30"},
    )
    assert response.status_code == 200, response.text
    x = response.json()
    assert x["demand_basis"] == "forecast"
    assert x["safety_stock"] == pytest.approx(Z95 * 4.0)
    assert x["reorder_point"] == pytest.approx(20.0 + Z95 * 4.0)
    assert x["notes"] == ["forecast demand over 10 days"]
    assert x["other_basis"]["basis"] == "history"
    assert x["other_basis"]["reorder_point"] == pytest.approx(x["reorder_point"])

    # W360: no backtest -> the historical spread, and the note says so.
    # (W360 has no item_sites row, so by-key 404s: use the list.)
    response = client.get(
        "/api/recommendations",
        params={"item_code": "W360", "as_of": "2026-09-30"},
    )
    assert response.status_code == 200, response.text
    w = response.json()[0]
    assert w["demand_basis"] == "forecast"
    assert w["inputs"]["sd_d"] == pytest.approx(sd_w)
    assert w["safety_stock"] == pytest.approx(ss_w)
    assert w["reorder_point"] == pytest.approx(20.0 + ss_w)
    assert w["notes"] == [
        "forecast demand over 10 days",
        "no backtest: historical variability used",
    ]
    assert w["other_basis"]["basis"] == "history"

    # L360: not recommendable on either basis, other_basis null.
    response = client.get(
        "/api/recommendations",
        params={"as_of": "2026-09-30", "item_code": "L360"},
    )
    lumps = response.json()
    assert len(lumps) == 1
    assert lumps[0]["status"] == "not_recommendable_demand"
    assert lumps[0]["demand_class"] == "lumpy"
    # L360 has a forecast, so the row names the active basis, not a
    # hardcoded history.
    assert lumps[0]["demand_basis"] == "forecast"
    assert lumps[0]["other_basis"] is None
    assert lumps[0]["safety_stock"] is None and lumps[0]["reorder_point"] is None

    # N360: no forecast under the forecast setting -> built on history,
    # with the note, demand_basis "history" and other_basis null.
    response = client.get(
        "/api/recommendations",
        params={"as_of": "2026-09-30", "item_code": "N360"},
    )
    stopped = response.json()
    assert len(stopped) == 1
    assert stopped[0]["status"] == "not_recommendable_demand"
    assert stopped[0]["demand_basis"] == "history"
    assert stopped[0]["notes"] == ["no forecast: history used"]
    assert stopped[0]["safety_stock"] is None and stopped[0]["reorder_point"] is None
    assert stopped[0]["other_basis"] is None

    # Back to the default.
    response = client.delete(f"/api/settings/{KEY}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "history"
    assert response.json()["source"] == "default"
