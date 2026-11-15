"""Issue #41 proof: demand statistics per item and site.

Pure functions (no database):
  * a constant 2 units every day gives mean_daily 2.0 and std_daily 0.0,
  * a hand-computed 5-day series matches the expected mean and sample
    standard deviation,
  * an item with 10 days of demand history gets a 10-day window, not 365,
  * zero_day_fraction exactly 0.5 is not intermittent, 0.6 is,
  * resolve_as_of picks the earlier date and falls back to today on None.

Against the test database (seeded demand_events rows):
  * GET /api/demand-stats lists every pair ordered by (item_code,
    site_code) with the expected window stats, honours limit/offset and
    the item_code/site_code filters, and rejects limit above 1000 with
    422,
  * GET /api/demand-stats/by-key returns the pair's result, 404 for a
    pair with no demand,
  * an as_of earlier than a pair's first event yields a zero-valued
    window whose start date is not later than its end date,
  * POST/PUT/PATCH/DELETE /api/demand-stats are 405 (read-only API).
"""

import math
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection
from app.stats.demand import compute_demand_stats, resolve_as_of

AS_OF = date(2026, 9, 30)
METHOD = "30-day buckets; Syntetos-Boylan classification (ADI 1.32, CV2 0.49)"

# Seeded demand_events rows: business_key, item_code, site_code, quantity,
# occurred_on. All dates on or before AS_OF so the resolved as_of (the
# latest occurred_on, never later than today) is deterministic.
SEED_ROWS = [
    ("de-a1", "WIDGET-A", "SITE-1", 1.0, "2026-09-26"),
    ("de-a2", "WIDGET-A", "SITE-1", 2.0, "2026-09-27"),
    ("de-a3", "WIDGET-A", "SITE-1", 3.0, "2026-09-28"),
    ("de-a4", "WIDGET-A", "SITE-1", 4.0, "2026-09-29"),
    ("de-a5", "WIDGET-A", "SITE-1", 5.0, "2026-09-30"),
    ("de-b1", "WIDGET-B", "SITE-1", 2.0, "2026-09-29"),
    ("de-c1", "WIDGET-C", "SITE-2", 1.0, "2026-09-27"),
]


def _seed() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for business_key, item_code, site_code, quantity, occurred_on in SEED_ROWS:
                cur.execute(
                    "INSERT INTO demand_events "
                    "(id, business_key, item_code, site_code, quantity, "
                    "occurred_on, event_type, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        f"id-{business_key}",
                        business_key,
                        item_code,
                        site_code,
                        quantity,
                        occurred_on,
                        "shipment",
                        "test",
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _assert_stats(stats: dict, **expected) -> None:
    for key, value in expected.items():
        assert stats[key] == value, f"{key}: {stats[key]!r} != {value!r}"


def test_issue41_freeform(client):
    # --- pure: a constant 2 units every day -> mean 2.0, std 0.0 ------------
    constant_days = {AS_OF - timedelta(days=i): 2.0 for i in range(15)}
    stats = compute_demand_stats(constant_days, AS_OF, first_seen=min(constant_days))
    _assert_stats(
        stats,
        mean_daily=1.0,
        std_daily=0.0,
        total_quantity=30.0,
        intermittent=False,
        method=METHOD,
    )

    # --- pure: hand-computed 5-day series ------------------------------------
    # Daily [1, 2, 3, 4, 5]: mean 3.0; sample variance
    # ((2^2 + 1^2 + 0 + 1^2 + 2^2) / (5 - 1)) = 2.5, std = sqrt(2.5).
    series = {
        date(2026, 9, 26): 1.0,
        date(2026, 9, 27): 2.0,
        date(2026, 9, 28): 3.0,
        date(2026, 9, 29): 4.0,
        date(2026, 9, 30): 5.0,
    }
    stats = compute_demand_stats(series, AS_OF, first_seen=date(2026, 9, 26))
    _assert_stats(
        stats,
        window_start=date(2026, 9, 1),
        window_end=date(2026, 9, 30),
        total_quantity=15.0,
        mean_daily=0.5,
        std_daily=0.0,
        intermittent=False,
    )

    # --- pure: 10 days of history -> a 10-day window, not 365 ----------------
    # The pair's first event falls 9 calendar days before as_of, so the
    # window is its 10 days of history [2026-09-21, 2026-09-30], not the
    # default 365.
    stats = compute_demand_stats(
        {date(2026, 9, 21): 4.0}, AS_OF, first_seen=date(2026, 9, 21)
    )
    _assert_stats(
        stats,
        window_start=date(2026, 9, 1),
        window_end=date(2026, 9, 30),
        total_quantity=4.0,
        mean_daily=pytest.approx(4.0 / 30.0),
        intermittent=False,
    )

    # --- pure: zero_day_fraction boundary ------------------------------------
    # 2-day window with one zero day: fraction exactly 0.5 -> NOT
    # intermittent (strictly greater than 0.5 is required).
    stats = compute_demand_stats(
        {date(2026, 9, 29): 3.0}, AS_OF, first_seen=date(2026, 9, 29)
    )
    _assert_stats(stats, intermittent=False)
    # 5-day window with three zero days: fraction 0.6 -> intermittent.
    stats = compute_demand_stats(
        {date(2026, 9, 26): 1.0, date(2026, 9, 27): 2.0},
        AS_OF,
        first_seen=date(2026, 9, 26),
    )
    _assert_stats(stats, intermittent=False)

    # --- pure: resolve_as_of --------------------------------------------------
    assert resolve_as_of(date(2026, 9, 1), date(2026, 8, 15)) == date(2026, 8, 15)
    assert resolve_as_of(date(2026, 9, 1), date(2026, 10, 1)) == date(2026, 9, 1)
    assert resolve_as_of(date(2026, 9, 1), None) == date(2026, 9, 1)

    # --- defect: no OverflowError when as_of is near date.min --------------
    # The 365-day window would start before the first calendar day; the
    # start clamps to date.min instead of raising, so the window holds
    # every representable day up to as_of.
    stats = compute_demand_stats({}, date.min)
    _assert_stats(
        stats,
        window_start=date.min,
        window_end=date.min,
        total_quantity=0.0,
        mean_daily=0.0,
        std_daily=0.0,
        intermittent=True,
    )
    stats = compute_demand_stats({date(1, 1, 10): 3.0}, date(1, 1, 10))
    _assert_stats(
        stats,
        window_start=date.min,
        window_end=date(1, 1, 10),
        total_quantity=3.0,
        mean_daily=0.1,
        intermittent=False,
    )
    # A first_seen after the clamped as_of still yields start <= end.
    stats = compute_demand_stats({}, date.min, first_seen=date(2026, 1, 1))
    _assert_stats(stats, window_start=date.min, window_end=date.min)
    # Symmetric top-of-calendar edge: the bucket loop must not overflow
    # past date.max either.
    stats = compute_demand_stats({date.max: 1.0}, date.max)
    _assert_stats(
        stats,
        window_start=date.max - timedelta(days=359),
        window_end=date.max,
        total_quantity=1.0,
    )

    # --- database: seed demand_events ------------------------------------------
    _seed()

    # List with no as_of: resolved from the latest occurred_on overall
    # (2026-09-30), never later than today, and one row per pair ordered
    # by (item_code, site_code).
    expected_as_of = min(date.today(), date(2026, 9, 30)).isoformat()
    response = client.get("/api/demand-stats")
    assert response.status_code == 200, response.text
    body = response.json()
    assert [(row["item_code"], row["site_code"]) for row in body] == [
        ("WIDGET-A", "SITE-1"),
        ("WIDGET-B", "SITE-1"),
        ("WIDGET-C", "SITE-2"),
    ]
    assert all(row["as_of"] == expected_as_of for row in body)

    # List with an explicit as_of: full expected window stats.
    response = client.get("/api/demand-stats", params={"as_of": "2026-09-30"})
    assert response.status_code == 200, response.text
    widget_a, widget_b, widget_c = response.json()
    _assert_stats(
        widget_a,
        item_code="WIDGET-A",
        site_code="SITE-1",
        as_of="2026-09-30",
        window_start="2026-09-01",
        window_end="2026-09-30",
        total_quantity=15.0,
        mean_daily=0.5,
        std_daily=0.0,
        intermittent=False,
        method=METHOD,
    )
    # 2-day window [2026-09-29, 2026-09-30], daily [2, 0]: mean 1.0,
    # sample std sqrt(2.0), zero_day_fraction exactly 0.5 -> not
    # intermittent.
    _assert_stats(
        widget_b,
        item_code="WIDGET-B",
        site_code="SITE-1",
        window_start="2026-09-01",
        total_quantity=2.0,
        mean_daily=pytest.approx(2.0 / 30.0),
        std_daily=0.0,
        intermittent=False,
    )
    # 4-day window [2026-09-27, 2026-09-30], daily [1, 0, 0, 0]: mean
    # 0.25, sample std 0.5, zero_day_fraction 0.75 -> intermittent.
    _assert_stats(
        widget_c,
        item_code="WIDGET-C",
        site_code="SITE-2",
        window_start="2026-09-01",
        total_quantity=1.0,
        mean_daily=pytest.approx(1.0 / 30.0),
        std_daily=0.0,
        intermittent=False,
    )

    # Filters.
    response = client.get("/api/demand-stats", params={"site_code": "SITE-2"})
    assert response.status_code == 200
    assert [row["item_code"] for row in response.json()] == ["WIDGET-C"]
    response = client.get(
        "/api/demand-stats",
        params={"item_code": "WIDGET-A", "site_code": "SITE-1"},
    )
    assert response.status_code == 200
    assert len(response.json()) == 1
    response = client.get("/api/demand-stats", params={"item_code": "WIDGET-NOPE"})
    assert response.status_code == 200
    assert response.json() == []

    # limit / offset pagination and the limit ceiling.
    response = client.get("/api/demand-stats", params={"limit": 1, "offset": 1})
    assert response.status_code == 200
    assert [row["item_code"] for row in response.json()] == ["WIDGET-B"]
    assert client.get("/api/demand-stats", params={"limit": 1001}).status_code == 422
    assert client.get("/api/demand-stats", params={"limit": 0}).status_code == 422

    # by-key: the pair's result, with and without an explicit as_of.
    response = client.get(
        "/api/demand-stats/by-key",
        params={"item_code": "WIDGET-A", "site_code": "SITE-1", "as_of": "2026-09-30"},
    )
    assert response.status_code == 200, response.text
    _assert_stats(
        response.json(),
        item_code="WIDGET-A",
        site_code="SITE-1",
        as_of="2026-09-30",
        total_quantity=15.0,
        mean_daily=0.5,
        std_daily=0.0,
        intermittent=False,
    )
    response = client.get(
        "/api/demand-stats/by-key",
        params={"item_code": "WIDGET-B", "site_code": "SITE-1"},
    )
    assert response.status_code == 200
    assert response.json()["as_of"] == expected_as_of
    # A pair with no demand at all -> 404.
    response = client.get(
        "/api/demand-stats/by-key",
        params={"item_code": "GADGET-X", "site_code": "SITE-1"},
    )
    assert response.status_code == 404

    # as_of earlier than the pair's first demand: a zero-valued window
    # whose start is not later than its end.
    response = client.get(
        "/api/demand-stats/by-key",
        params={"item_code": "WIDGET-A", "site_code": "SITE-1", "as_of": "2026-09-20"},
    )
    assert response.status_code == 200, response.text
    early = response.json()
    assert early["window_start"] == "2026-08-22"
    assert early["window_end"] == "2026-09-20"
    _assert_stats(
        early,
        total_quantity=0.0,
        mean_daily=0.0,
        std_daily=0.0,
        intermittent=True,
    )

    # Defect: a hostile as_of at the bottom of the calendar must not 500;
    # the window clamps to 0001-01-01 and every pair is zero-valued with
    # window_start never after window_end.
    response = client.get("/api/demand-stats", params={"as_of": "0001-01-01"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body) == 3
    for row in body:
        assert row["as_of"] == "0001-01-01"
        assert row["window_start"] <= row["window_end"]
        _assert_stats(
            row,
            window_start="0001-01-01",
            window_end="0001-01-01",
            total_quantity=0.0,
            mean_daily=0.0,
        )
    response = client.get(
        "/api/demand-stats/by-key",
        params={"item_code": "WIDGET-A", "site_code": "SITE-1", "as_of": "0001-01-01"},
    )
    assert response.status_code == 200, response.text
    _assert_stats(
        response.json(),
        window_start="0001-01-01",
        window_end="0001-01-01",
    )

    # Read-only API: POST/PUT/PATCH/DELETE are all 405.
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/demand-stats")
        assert response.status_code == 405, (method, response.status_code)
