"""Issue #49 proof: 30-day bucket demand stats, Syntetos-Boylan class.

Pure functions (no database):
  * 12 buckets of exactly 60 units each give mean_daily 2.0,
    std_daily 0.0 and class smooth,
  * a hand-computed 12-bucket series matches the expected mean, the
    sample standard deviation scaled by 1/sqrt(30), the adi and the
    cv2,
  * one example each of smooth, erratic, intermittent and lumpy with
    hand-computed ADI and CV2, plus no_demand,
  * an item first seen 65 days before as_of gets n_buckets 2,
  * recommendable is true only for smooth and erratic, and the
    intermittent flag is true exactly for intermittent, lumpy and
    no_demand,
  * a cv2 exactly at the 0.49 threshold (buckets 17 and 3) counts as
    at or above it: erratic, not smooth, and lumpy, not intermittent,
  * a NaN among the lead time observations is dropped without fencing
    the finite observations out to a zeroed summary.

Against the test database (demand row dated 2026-06-30, receipt row
dated 2026-06-20, date.today monkeypatched to 2026-07-15):
  * lead_time_stats_for_all() without as_of reports as_of 2026-06-30,
    the shared default (the latest demand occurred_on),
  * GET /api/demand-stats returns 200 with the new response model,
  * /by-key is 404 for a missing pair and POST/PUT/PATCH/DELETE are
    405 (read-only API).
"""

import math
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection
from app.stats.demand import METHOD, compute_demand_stats, demand_stats_for_all
from app.stats.lead_time import compute_lead_time_stats, lead_time_stats_for_all

AS_OF = date(2026, 9, 30)
FIXED_TODAY = date(2026, 7, 15)


def _daily_for_bucket_totals(totals: list[float]):
    """(daily_totals, first_seen) placing each bucket total on its last day.

    Bucket k's last day is AS_OF - 30k. ``first_seen`` defaults to the
    earliest bucket's start (AS_OF - 30*(len-1) - 29) so every bucket
    is a complete 30-day bucket.
    """
    daily = {
        AS_OF - timedelta(days=30 * k): float(total)
        for k, total in enumerate(totals)
        if total
    }
    first_seen = AS_OF - timedelta(days=30 * (len(totals) - 1) + 29)
    return daily, first_seen


def test_issue49_freeform(client, monkeypatch):
    # --- pure: 12 buckets of exactly 60 units each --------------------------
    # 2 units on every day of the full 360-day window: each bucket holds
    # exactly 60, so mean_daily = 60/30 = 2.0, std_daily 0.0,
    # adi = 12/12 = 1.0 and cv2 = 0.0 -> smooth.
    daily = {AS_OF - timedelta(days=i): 2.0 for i in range(360)}
    stats = compute_demand_stats(daily, AS_OF, first_seen=min(daily))
    assert stats["bucket_days"] == 30
    assert stats["n_buckets"] == 12
    assert stats["nonzero_buckets"] == 12
    assert stats["window_start"] == AS_OF - timedelta(days=359)
    assert stats["window_end"] == AS_OF
    assert stats["total_quantity"] == pytest.approx(720.0)
    assert stats["mean_daily"] == pytest.approx(2.0)
    assert stats["std_daily"] == pytest.approx(0.0)
    assert stats["adi"] == pytest.approx(1.0)
    assert stats["cv2"] == pytest.approx(0.0)
    assert stats["demand_class"] == "smooth"
    assert stats["recommendable"] is True
    assert stats["intermittent"] is False
    assert stats["method"] == METHOD

    # --- pure: hand-computed 12-bucket series --------------------------------
    # Bucket totals [30, 0, 0, 0, 30, 0, 0, 0, 30, 0, 0, 30]:
    #   total 120, mean bucket 120/12 = 10 -> mean_daily 10/30 = 1/3,
    #   sample variance (4*20^2 + 8*10^2)/(12-1) = 2400/11
    #   -> std_daily sqrt(2400/11)/sqrt(30) = sqrt(80/11),
    #   adi = 12/4 = 3.0 (>= 1.32), cv2 = 0.0 (equal non-zero buckets,
    #   < 0.49) -> intermittent.
    daily, first_seen = _daily_for_bucket_totals(
        [30, 0, 0, 0, 30, 0, 0, 0, 30, 0, 0, 30]
    )
    stats = compute_demand_stats(daily, AS_OF, first_seen=first_seen)
    assert stats["n_buckets"] == 12
    assert stats["nonzero_buckets"] == 4
    assert stats["window_start"] == AS_OF - timedelta(days=359)
    assert stats["total_quantity"] == pytest.approx(120.0)
    assert stats["mean_daily"] == pytest.approx(1.0 / 3.0)
    assert stats["std_daily"] == pytest.approx(math.sqrt(80.0 / 11.0))
    assert stats["adi"] == pytest.approx(3.0)
    assert stats["cv2"] == pytest.approx(0.0)
    assert stats["demand_class"] == "intermittent"

    # --- pure: the Syntetos-Boylan class matrix ------------------------------
    examples = {}

    # smooth: six equal buckets of 60 -> ADI 6/6 = 1.0 (< 1.32),
    # CV2 0.0 (< 0.49).
    daily, first_seen = _daily_for_bucket_totals([60] * 6)
    stats = compute_demand_stats(daily, AS_OF, first_seen=first_seen)
    assert stats["n_buckets"] == 6
    assert stats["adi"] == pytest.approx(1.0)
    assert stats["cv2"] == pytest.approx(0.0)
    examples["smooth"] = stats

    # erratic: [10, 10, 10, 50] -> ADI 4/4 = 1.0 (< 1.32); non-zero
    # mean 20, population variance (3*10^2 + 30^2)/4 = 300
    # -> CV2 300/20^2 = 0.75 (>= 0.49).
    daily, first_seen = _daily_for_bucket_totals([10, 10, 10, 50])
    stats = compute_demand_stats(daily, AS_OF, first_seen=first_seen)
    assert stats["n_buckets"] == 4
    assert stats["adi"] == pytest.approx(1.0)
    assert stats["cv2"] == pytest.approx(0.75)
    examples["erratic"] = stats

    # intermittent: [0, 0, 30, 0, 0, 0, 0, 30] -> ADI 8/2 = 4.0
    # (>= 1.32), CV2 0.0 (equal non-zero buckets, < 0.49).
    daily, first_seen = _daily_for_bucket_totals([0, 0, 30, 0, 0, 0, 0, 30])
    stats = compute_demand_stats(daily, AS_OF, first_seen=first_seen)
    assert stats["n_buckets"] == 8
    assert stats["adi"] == pytest.approx(4.0)
    assert stats["cv2"] == pytest.approx(0.0)
    examples["intermittent"] = stats

    # lumpy: [0, 0, 0, 0, 0, 90, 0, 10] -> ADI 8/2 = 4.0 (>= 1.32);
    # non-zero [90, 10]: mean 50, population standard deviation 40
    # -> CV2 (40/50)^2 = 0.64 (>= 0.49).
    daily, first_seen = _daily_for_bucket_totals([0, 0, 0, 0, 0, 90, 0, 10])
    stats = compute_demand_stats(daily, AS_OF, first_seen=first_seen)
    assert stats["n_buckets"] == 8
    assert stats["adi"] == pytest.approx(4.0)
    assert stats["cv2"] == pytest.approx(0.64)
    examples["lumpy"] = stats

    # no_demand: nothing in any bucket -> adi and cv2 are None.
    daily, first_seen = _daily_for_bucket_totals([0] * 4)
    stats = compute_demand_stats(daily, AS_OF, first_seen=first_seen)
    assert stats["n_buckets"] == 4
    assert stats["nonzero_buckets"] == 0
    assert stats["total_quantity"] == pytest.approx(0.0)
    assert stats["mean_daily"] == pytest.approx(0.0)
    assert stats["adi"] is None
    assert stats["cv2"] is None
    examples["no_demand"] = stats

    for name, stats in examples.items():
        assert stats["demand_class"] == name

    # --- pure: cv2 exactly at the 0.49 boundary counts as >= -----------------
    # Non-zero buckets [17, 3] have population cv2 (17 - 3)^2 / (17 + 3)^2
    # = 196 / 400 = 0.49 exactly, but (pstdev / mean) ** 2 evaluates to
    # 0.48999999999999994 in floats; the class must still treat the cv2 as
    # >= 0.49.
    daily, first_seen = _daily_for_bucket_totals([17, 3])
    stats = compute_demand_stats(daily, AS_OF, first_seen=first_seen)
    assert stats["n_buckets"] == 2
    assert stats["nonzero_buckets"] == 2
    assert stats["adi"] == pytest.approx(1.0)
    assert stats["cv2"] == pytest.approx(0.49)
    assert stats["demand_class"] == "erratic"
    assert stats["recommendable"] is True
    assert stats["intermittent"] is False

    # The same boundary with adi above 1.32 -> lumpy, not intermittent.
    daily, first_seen = _daily_for_bucket_totals([0, 0, 0, 0, 0, 17, 0, 3])
    stats = compute_demand_stats(daily, AS_OF, first_seen=first_seen)
    assert stats["n_buckets"] == 8
    assert stats["nonzero_buckets"] == 2
    assert stats["adi"] == pytest.approx(4.0)
    assert stats["cv2"] == pytest.approx(0.49)
    assert stats["demand_class"] == "lumpy"
    assert stats["recommendable"] is False
    assert stats["intermittent"] is True

    # --- pure: first seen 65 days before as_of -> n_buckets 2 ----------------
    # 66 days of history hold two complete 30-day buckets
    # ([as_of-59, as_of-30] and [as_of-29, as_of]), not three.
    stats = compute_demand_stats({}, AS_OF, first_seen=AS_OF - timedelta(days=65))
    assert stats["n_buckets"] == 2
    assert stats["window_start"] == AS_OF - timedelta(days=59)
    assert stats["window_end"] == AS_OF
    assert stats["demand_class"] == "no_demand"

    # --- pure: recommendable and intermittent follow the class ----------------
    for name in ("smooth", "erratic"):
        assert examples[name]["recommendable"] is True
        assert examples[name]["intermittent"] is False
    for name in ("intermittent", "lumpy", "no_demand"):
        assert examples[name]["recommendable"] is False
        assert examples[name]["intermittent"] is True

    # --- pure: a NaN cannot fence the lead time summary to zeros --------------
    # A NaN in the data used to poison the fence, and a NaN fence compares
    # against nothing, dropping every observation to a zeroed summary. The
    # fence is now computed from the non-NaN observations, and the NaN is
    # just not used.
    stats = compute_lead_time_stats([10.0, 20.0, 30.0, 40.0, float("nan")])
    assert stats["n_observations"] == 5
    assert stats["fence"] == pytest.approx(77.5)
    assert stats["n_used"] == 4
    assert stats["n_dropped"] == 1
    assert stats["mean_days"] == pytest.approx(25.0)
    assert stats["std_days"] == pytest.approx(math.sqrt(500.0 / 3.0))
    assert stats["min_days"] == pytest.approx(10.0)
    assert stats["max_days"] == pytest.approx(40.0)
    assert stats["low_confidence"] is False
    # Same multiset with the NaN first: the summary must not depend on
    # where the NaN sits.
    stats = compute_lead_time_stats([float("nan"), 10.0, 20.0, 30.0, 40.0])
    assert stats["fence"] == pytest.approx(77.5)
    assert stats["n_used"] == 4
    assert stats["mean_days"] == pytest.approx(25.0)
    # Fewer than four usable observations: no fence, the NaN is just not
    # used.
    stats = compute_lead_time_stats([10.0, float("nan"), 20.0])
    assert stats["fence"] is None
    assert stats["n_used"] == 2
    assert stats["n_dropped"] == 1
    assert stats["mean_days"] == pytest.approx(15.0)
    assert stats["low_confidence"] is True

    # --- pure: hostile as_of at the calendar edges never 500 ------------------
    # Near date.min no whole bucket fits; the single bucket clamps to
    # date.min instead of raising OverflowError.
    stats = compute_demand_stats({}, date.min)
    assert stats["n_buckets"] == 1
    assert stats["window_start"] == stats["window_end"] == date.min
    stats = compute_demand_stats({date.min: 3.0}, date.min)
    assert stats["nonzero_buckets"] == 1
    assert stats["demand_class"] == "smooth"
    # Symmetric top-of-calendar edge: the bucket loop must not overflow
    # past date.max either.
    stats = compute_demand_stats({}, date.max)
    assert stats["n_buckets"] == 12
    assert stats["window_start"] == date.max - timedelta(days=359)

    # --- database: one shared default as-of -----------------------------------
    # Demand dated 2026-06-30, receipt dated 2026-06-20, and today pinned
    # to 2026-07-15: the default as_of is the latest demand occurred_on,
    # so lead time stats share the demand default.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    "id-de-x1",
                    "de-x1",
                    "WIDGET-A",
                    "SITE-1",
                    5.0,
                    "2026-06-30",
                    "shipment",
                    "test",
                ),
            )
            cur.execute(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, "
                "ordered_on, received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    "id-lt-x1",
                    "lt-x1",
                    "SUP-A",
                    "WIDGET-A",
                    "2026-06-01",
                    "2026-06-20",
                    "test",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    rows = lead_time_stats_for_all()
    assert len(rows) == 1
    assert rows[0]["item_code"] == "WIDGET-A"
    assert rows[0]["as_of"] == date(2026, 6, 30)
    demand_rows = demand_stats_for_all()
    assert [row["as_of"] for row in demand_rows] == [date(2026, 6, 30)]

    # --- routes: 200 with the new model, 404 missing pair, 405 POST -----------
    response = client.get("/api/demand-stats", params={"as_of": "2026-06-30"})
    assert response.status_code == 200, response.text
    assert [row["item_code"] for row in response.json()] == ["WIDGET-A"]

    response = client.get(
        "/api/demand-stats/by-key",
        params={"item_code": "WIDGET-A", "site_code": "SITE-1", "as_of": "2026-06-30"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "item_code",
        "site_code",
        "as_of",
        "window_start",
        "window_end",
        "bucket_days",
        "n_buckets",
        "nonzero_buckets",
        "total_quantity",
        "mean_daily",
        "std_daily",
        "adi",
        "cv2",
        "demand_class",
        "recommendable",
        "intermittent",
        "method",
    }
    # First seen on as_of itself: one bucket [2026-06-01, 2026-06-30]
    # holding 5 units -> smooth, mean_daily 5/30.
    assert body["as_of"] == "2026-06-30"
    assert body["bucket_days"] == 30
    assert body["n_buckets"] == 1
    assert body["nonzero_buckets"] == 1
    assert body["window_start"] == "2026-06-01"
    assert body["window_end"] == "2026-06-30"
    assert body["total_quantity"] == pytest.approx(5.0)
    assert body["mean_daily"] == pytest.approx(5.0 / 30.0)
    assert body["std_daily"] == pytest.approx(0.0)
    assert body["adi"] == pytest.approx(1.0)
    assert body["cv2"] == pytest.approx(0.0)
    assert body["demand_class"] == "smooth"
    assert body["recommendable"] is True
    assert body["intermittent"] is False
    assert body["method"] == METHOD

    # A pair with no demand at all -> 404.
    response = client.get(
        "/api/demand-stats/by-key",
        params={"item_code": "GADGET-X", "site_code": "SITE-1"},
    )
    assert response.status_code == 404

    # Read-only API: POST/PUT/PATCH/DELETE are all 405.
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/demand-stats")
        assert response.status_code == 405, (method, response.status_code)
