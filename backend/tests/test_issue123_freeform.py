"""Issue #123 proof: reorder-point changes between two as-of dates.

Pure ``app.recommend.changes`` (no database):
  * ``reorder_point`` reproduces the exact formula
    ``app.recommend.reorder.recommend`` uses,
  * ``shapley_contributions`` puts the whole change into
    ``demand_level`` when only ``d`` moves ({d: 2, sd_d: 1, lt: 10,
    sd_lt: 2} -> {d: 3, ...}, z 1.645) with the other three exactly 0,
  * with ``d`` and ``lt`` both changing, both contributions are
    non-zero and the four sum to the exact reorder-point difference,
  * with a large finite demand change (d 1e15 -> 2e15, sd_d 0 ->
    5e13), the four still sum to the exact reorder-point difference
    within 1e-9,
  * an unchanged infinite input (d = inf on both sides, lt moving)
    contributes exactly 0.0 with no NaN anywhere.

Against the test database (every date a fixed past calendar date, the
as-of 2026-06-30, since defaulting to 2026-05-31):
  * P-123: 360 days of steady 10/day demand (d 10, sd_d 0) plus one
    extra 1000-unit event on 2026-06-15 (inside the last 30 days) and
    three pooled lead time observations (8, 10, 12 -> lt 10, sd_lt 2),
    so the since stats stay d 10 / sd_d 0 while the as-of stats move;
    it appears once with positive ``change_units``, ``money`` equal to
    ``abs(change_units) * unit_cost``, ``contributions`` summing to
    ``reorder_point_now - reorder_point_then`` within 1e-9 and
    ``primary_driver`` the largest absolute contribution,
  * Q-123 has unchanged history and does not appear,
  * a ``since`` equal to ``as_of`` raises ``ValueError``.
"""

import math
import statistics
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection
from app.recommend.changes import (
    recommendation_changes,
    reorder_point,
    shapley_contributions,
)

AS_OF = date(2026, 6, 30)
SINCE = AS_OF - timedelta(days=30)  # 2026-05-31
FIRST_DAY = date(2025, 7, 6)  # 360 days before AS_OF (window start)
Z95 = statistics.NormalDist().inv_cdf(0.95)

CONTRIBUTION_KEYS = {
    "demand_level",
    "demand_variability",
    "lead_time",
    "lead_time_variability",
}


def _daily_demand(item_code: str) -> list[tuple]:
    """360 daily demand events of 10 units, FIRST_DAY through AS_OF."""
    return [
        (
            f"id-de-123-{item_code}-{day_index}",
            f"de-123-{item_code}-{day_index}",
            item_code,
            "SITE-1",
            10.0,
            (FIRST_DAY + timedelta(days=day_index)).isoformat(),
            "shipment",
            "test",
        )
        for day_index in range(360)
    ]


def _lead_time_observations(item_code: str, month: int) -> list[tuple]:
    """Three observations of 8, 10 and 12 days received in ``month``."""
    return [
        (
            f"id-lt-123-{item_code}{n}",
            f"lt-123-{item_code}{n}",
            f"SUP-{item_code}",
            item_code,
            f"2026-{month:02d}-0{1 + 2 * (n - 1)}",
            f"2026-{month:02d}-0{1 + 2 * (n - 1) + days}",
            "test",
        )
        for n, days in enumerate((8, 10, 12), start=1)
    ]


def test_issue123_freeform(client):
    # --- pure: the formula matches recommend's arithmetic -------------------
    assert reorder_point(10.0, 5.0, 10.0, 2.0, Z95) == (
        10.0 * 10.0 + Z95 * (10.0 * 5.0**2 + 10.0**2 * 2.0**2) ** 0.5
    )

    # --- pure: only d changes -> everything goes to demand_level ------------
    then_one = {"d": 2, "sd_d": 1, "lt": 10, "sd_lt": 2}
    now_one = {"d": 3, "sd_d": 1, "lt": 10, "sd_lt": 2}
    contributions_one = shapley_contributions(then_one, now_one, 1.645)
    assert set(contributions_one) == CONTRIBUTION_KEYS
    diff_one = reorder_point(**now_one, z=1.645) - reorder_point(**then_one, z=1.645)
    assert diff_one > 0
    assert contributions_one["demand_level"] == pytest.approx(diff_one, abs=1e-9)
    assert contributions_one["demand_variability"] == 0.0
    assert contributions_one["lead_time"] == 0.0
    assert contributions_one["lead_time_variability"] == 0.0
    assert sum(contributions_one.values()) == pytest.approx(diff_one, abs=1e-9)

    # --- pure: d and lt both change -> both non-zero, exact sum --------------
    now_two = {"d": 3, "sd_d": 1, "lt": 12, "sd_lt": 2}
    contributions_two = shapley_contributions(then_one, now_two, 1.645)
    assert contributions_two["demand_level"] != 0.0
    assert contributions_two["lead_time"] != 0.0
    assert contributions_two["demand_variability"] == 0.0
    assert contributions_two["lead_time_variability"] == 0.0
    diff_two = reorder_point(**now_two, z=1.645) - reorder_point(**then_one, z=1.645)
    assert sum(contributions_two.values()) == pytest.approx(diff_two, abs=1e-9)

    # --- pure: large finite demand change keeps the sum exact ---------------
    then_big = {"d": 1e15, "sd_d": 0.0, "lt": 10.0, "sd_lt": 2.0}
    now_big = {"d": 2e15, "sd_d": 5e13, "lt": 10.0, "sd_lt": 2.0}
    contributions_big = shapley_contributions(then_big, now_big, 1.645)
    diff_big = reorder_point(**now_big, z=1.645) - reorder_point(**then_big, z=1.645)
    assert contributions_big["demand_level"] != 0.0
    assert contributions_big["demand_variability"] != 0.0
    assert contributions_big["lead_time"] == 0.0
    assert contributions_big["lead_time_variability"] == 0.0
    assert sum(contributions_big.values()) == pytest.approx(diff_big, abs=1e-9)

    # --- pure: unchanged infinite input contributes exactly 0, no NaN -------
    then_inf = {"d": float("inf"), "sd_d": 1.0, "lt": 10.0, "sd_lt": 2.0}
    now_inf = {"d": float("inf"), "sd_d": 1.0, "lt": 12.0, "sd_lt": 2.0}
    contributions_inf = shapley_contributions(then_inf, now_inf, 1.645)
    for name in CONTRIBUTION_KEYS:
        assert not math.isnan(contributions_inf[name])
        assert contributions_inf[name] == 0.0

    # --- seed: steady demand, one spike in the last 30 days, lead times ------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [*_daily_demand("P-123"), *_daily_demand("Q-123")]
                + [
                    (
                        "id-de-123-spike",
                        "de-123-spike",
                        "P-123",
                        "SITE-1",
                        1000.0,
                        "2026-06-15",
                        "shipment",
                        "test",
                    )
                ],
            )
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                _lead_time_observations("P-123", 2)
                + _lead_time_observations("Q-123", 3),
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, "
                "master_lead_time_days, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    ("id-is-123-p", "P-123", "SITE-1", 2.0, None, "test"),
                    ("id-is-123-q", "Q-123", "SITE-1", 1.5, None, "test"),
                ],
            )
        conn.commit()
    finally:
        conn.close()

    # --- since equal to as_of raises ValueError ------------------------------
    with pytest.raises(ValueError):
        recommendation_changes(as_of=AS_OF, since=AS_OF)

    # --- the changed item-site appears; the unchanged one does not -----------
    rows = recommendation_changes(as_of=AS_OF)
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        ("P-123", "SITE-1")
    ]
    row = rows[0]
    assert row["as_of"] == AS_OF
    assert row["since"] == SINCE
    assert row["status_then"] == "ok"
    assert row["status_now"] == "ok"
    assert row["master_lead_time_then"] is False
    assert row["master_lead_time_now"] is False
    assert row["notes"] == []

    # since stats: 11 full 300 buckets -> d 10, sd_d 0; lt 10, sd_lt 2.
    assert row["inputs_then"] == {
        "d": 10.0,
        "sd_d": 0.0,
        "lt": 10.0,
        "sd_lt": 2.0,
    }
    assert row["reorder_point_then"] == pytest.approx(
        reorder_point(**row["inputs_then"], z=Z95), abs=1e-9
    )
    assert row["reorder_point_units_then"] == 133

    # as-of stats: bucket 0 is 1300, the other 11 buckets 300.
    assert row["inputs_now"]["lt"] == 10.0
    assert row["inputs_now"]["sd_lt"] == 2.0
    assert row["inputs_now"]["d"] == pytest.approx(4600 / 360, abs=1e-9)
    assert row["inputs_now"]["sd_d"] == pytest.approx(52.7046, abs=1e-3)
    assert row["inputs_now"]["sd_d"] > 0
    assert row["reorder_point_now"] == pytest.approx(
        reorder_point(**row["inputs_now"], z=Z95), abs=1e-9
    )
    assert row["reorder_point_units_now"] == 406

    # positive change, priced with the item-site's unit cost
    assert row["change_units"] == 406 - 133
    assert row["change_units"] > 0
    assert row["unit_cost"] == 2.0
    assert row["money"] == abs(row["change_units"]) * 2.0
    assert row["money"] == 546.0

    # Shapley contributions split the exact reorder-point difference
    contributions = row["contributions"]
    assert set(contributions) == CONTRIBUTION_KEYS
    assert contributions is not None
    assert sum(contributions.values()) == pytest.approx(
        row["reorder_point_now"] - row["reorder_point_then"], abs=1e-9
    )
    expected_driver = max(contributions, key=lambda name: abs(contributions[name]))
    assert row["primary_driver"] == expected_driver
    assert row["primary_driver"] == "demand_variability"

    # --- sorting: money descending, then item/site ---------------------------
    assert rows == sorted(
        rows,
        key=lambda r: (
            0 if r["money"] is not None else 1,
            -(r["money"] or 0.0),
            r["item_code"],
            r["site_code"],
        ),
    )
