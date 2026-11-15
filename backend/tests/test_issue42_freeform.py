"""Issue #42 proof: lead time statistics per item and supplier.

Pure function (no database):
  * for [5, 6, 7, 8, 9, 10, 1506] the 1506 is dropped (n_dropped 1)
    and the mean and standard deviation are those of the remaining six,
  * 3 observations are never filtered (fence None),
  * 2 observations are low_confidence, and 3 are not.

Against the test database (seeded lead_time_observations rows):
  * two suppliers of one item give a pooled result over both, with a
    two-entry suppliers list,
  * an observation received 731 days before as_of is excluded, and one
    at 730 days is included,
  * /by-item for an unknown item returns 404,
  * POST/PUT/PATCH/DELETE /api/lead-time-stats are 405 (read-only API),
  * hostile inputs (huge limits, bad dates, oversized/NUL filters,
    as_of at the bottom of the calendar) never produce a 500.
"""

import math
import statistics
from datetime import date

import pytest

from app.db.connection import get_connection
from app.stats.lead_time import METHOD, compute_lead_time_stats

AS_OF = date(2026, 9, 30)

# Seeded lead_time_observations rows: business_key, supplier_code,
# item_code, ordered_on, received_on (lead_time_days is the computed
# received_on - ordered_on). LT-EDGE's included row is received exactly
# 730 days before AS_OF (2024-09-30); its excluded row 731 days before
# (2024-09-29).
SEED_ROWS = [
    ("lt-a1", "SUP-A", "LT-1", "2026-09-05", "2026-09-10"),
    ("lt-a2", "SUP-A", "LT-1", "2026-09-09", "2026-09-15"),
    ("lt-a3", "SUP-A", "LT-1", "2026-09-13", "2026-09-20"),
    ("lt-b1", "SUP-B", "LT-1", "2026-09-04", "2026-09-12"),
    ("lt-b2", "SUP-B", "LT-1", "2026-09-13", "2026-09-22"),
    ("lt-b3", "SUP-B", "LT-1", "2026-09-20", "2026-09-30"),
    ("lt-e1", "SUP-A", "LT-EDGE", "2024-09-20", "2024-09-30"),
    ("lt-e2", "SUP-B", "LT-EDGE", "2024-09-24", "2024-09-29"),
]


def _seed() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for (
                business_key,
                supplier_code,
                item_code,
                ordered_on,
                received_on,
            ) in SEED_ROWS:
                cur.execute(
                    "INSERT INTO lead_time_observations "
                    "(id, business_key, supplier_code, item_code, "
                    "ordered_on, received_on, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        f"id-{business_key}",
                        business_key,
                        supplier_code,
                        item_code,
                        ordered_on,
                        received_on,
                        "test",
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _assert_stats(stats: dict, **expected) -> None:
    for key, value in expected.items():
        assert stats[key] == value, f"{key}: {stats[key]!r} != {value!r}"


def test_issue42_freeform(client):
    # --- pure: the 1506-day outlier is fenced out ---------------------------
    remaining = [5, 6, 7, 8, 9, 10]
    stats = compute_lead_time_stats([5, 6, 7, 8, 9, 10, 1506])
    assert stats["n_observations"] == 7
    assert stats["n_dropped"] == 1
    assert stats["n_used"] == 6
    quartile_low, _, quartile_high = statistics.quantiles(
        [5, 6, 7, 8, 9, 10, 1506], n=4, method="inclusive"
    )
    assert stats["fence"] == pytest.approx(
        quartile_high + 3 * (quartile_high - quartile_low)
    )
    _assert_stats(
        stats,
        mean_days=pytest.approx(statistics.mean(remaining)),
        std_days=pytest.approx(statistics.stdev(remaining)),
        min_days=5.0,
        max_days=10.0,
        low_confidence=False,
        method=METHOD,
    )

    # --- pure: 3 observations are never filtered ----------------------------
    stats = compute_lead_time_stats([5, 5000, 5001])
    assert stats["fence"] is None
    assert stats["n_dropped"] == 0
    assert stats["n_used"] == 3
    assert stats["low_confidence"] is False

    # --- pure: low_confidence boundary --------------------------------------
    assert compute_lead_time_stats([5, 7])["low_confidence"] is True
    assert compute_lead_time_stats([5, 7, 9])["low_confidence"] is False

    # --- database: two suppliers of one item, pooled + per-supplier ---------
    _seed()
    response = client.get("/api/lead-time-stats", params={"as_of": "2026-09-30"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == ["LT-1", "LT-EDGE"]

    lt1 = body[0]
    assert lt1["as_of"] == "2026-09-30"
    quartile_low, _, quartile_high = statistics.quantiles(
        [5, 6, 7, 8, 9, 10], n=4, method="inclusive"
    )
    _assert_stats(
        lt1,
        n_observations=6,
        n_used=6,
        n_dropped=0,
        fence=pytest.approx(quartile_high + 3 * (quartile_high - quartile_low)),
        mean_days=7.5,
        std_days=pytest.approx(math.sqrt(3.5)),
        min_days=5.0,
        max_days=10.0,
        low_confidence=False,
        method=METHOD,
    )
    assert len(lt1["suppliers"]) == 2
    by_supplier = {row["supplier_code"]: row for row in lt1["suppliers"]}
    assert set(by_supplier) == {"SUP-A", "SUP-B"}
    _assert_stats(
        by_supplier["SUP-A"],
        n_observations=3,
        n_used=3,
        n_dropped=0,
        mean_days=pytest.approx(6.0),
        std_days=pytest.approx(1.0),
        min_days=5.0,
        max_days=7.0,
        low_confidence=False,
        method=METHOD,
    )
    _assert_stats(
        by_supplier["SUP-B"],
        n_observations=3,
        n_used=3,
        n_dropped=0,
        mean_days=pytest.approx(9.0),
        std_days=pytest.approx(1.0),
        min_days=8.0,
        max_days=10.0,
        low_confidence=False,
        method=METHOD,
    )

    # --- database: 730-day window boundary ----------------------------------
    # The row received 730 days before as_of is in; the row 731 days
    # before (and its supplier) is out, so the item sees exactly one
    # observation.
    edge = body[1]
    assert edge["item_code"] == "LT-EDGE"
    _assert_stats(
        edge,
        n_observations=1,
        n_used=1,
        n_dropped=0,
        mean_days=10.0,
        std_days=0.0,
        min_days=10.0,
        max_days=10.0,
        low_confidence=True,
    )
    assert [row["supplier_code"] for row in edge["suppliers"]] == ["SUP-A"]

    # by-item: the item's pooled result with its per-supplier breakdown.
    response = client.get(
        "/api/lead-time-stats/by-item",
        params={"item_code": "LT-EDGE", "as_of": "2026-09-30"},
    )
    assert response.status_code == 200, response.text
    edge_by_item = response.json()
    assert edge_by_item["item_code"] == "LT-EDGE"
    _assert_stats(edge_by_item, n_observations=1, mean_days=10.0, low_confidence=True)
    assert [row["supplier_code"] for row in edge_by_item["suppliers"]] == ["SUP-A"]

    # --- filters and pagination ---------------------------------------------
    response = client.get(
        "/api/lead-time-stats",
        params={"item_code": "LT-1", "as_of": "2026-09-30"},
    )
    assert response.status_code == 200
    assert [row["item_code"] for row in response.json()] == ["LT-1"]
    response = client.get(
        "/api/lead-time-stats",
        params={"item_code": "GADGET-NOPE", "as_of": "2026-09-30"},
    )
    assert response.status_code == 200
    assert response.json() == []
    response = client.get(
        "/api/lead-time-stats", params={"limit": 1, "offset": 1, "as_of": "2026-09-30"}
    )
    assert response.status_code == 200
    assert [row["item_code"] for row in response.json()] == ["LT-EDGE"]

    # limit ceiling and the offset bounds reject with 422, not 500.
    assert client.get("/api/lead-time-stats", params={"limit": 1001}).status_code == 422
    assert client.get("/api/lead-time-stats", params={"limit": 0}).status_code == 422
    assert client.get("/api/lead-time-stats", params={"offset": -1}).status_code == 422

    # --- by-item: 404 for an unknown item ------------------------------------
    response = client.get(
        "/api/lead-time-stats/by-item",
        params={"item_code": "GADGET-NOPE", "as_of": "2026-09-30"},
    )
    assert response.status_code == 404
    # Missing the required item_code is a validation error, not a 500.
    assert client.get("/api/lead-time-stats/by-item").status_code == 422

    # --- hostile inputs never 500 --------------------------------------------
    # An as_of at the bottom of the calendar clamps the window start to
    # date.min; no observation is that old, so every list is empty.
    response = client.get("/api/lead-time-stats", params={"as_of": "0001-01-01"})
    assert response.status_code == 200, response.text
    assert response.json() == []
    response = client.get(
        "/api/lead-time-stats/by-item",
        params={"item_code": "LT-1", "as_of": "0001-01-01"},
    )
    assert response.status_code == 404
    # Non-date as_of, oversized filter, and NUL bytes are rejected 422.
    assert (
        client.get("/api/lead-time-stats", params={"as_of": "not-a-date"}).status_code
        == 422
    )
    assert (
        client.get("/api/lead-time-stats", params={"item_code": "x" * 200}).status_code
        == 422
    )
    assert (
        client.get("/api/lead-time-stats", params={"item_code": "LT-1\x00"}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/lead-time-stats/by-item",
            params={"item_code": "LT-1\x00", "as_of": "2026-09-30"},
        ).status_code
        == 422
    )

    # Read-only API: POST/PUT/PATCH/DELETE are all 405.
    for method in ("post", "put", "patch", "delete"):
        response = getattr(client, method)("/api/lead-time-stats")
        assert response.status_code == 405, (method, response.status_code)
