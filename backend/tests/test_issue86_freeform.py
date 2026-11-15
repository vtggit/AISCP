"""Issue #86 proof: excess and dead stock, assessed and ranked by money.

Pure ``app.recommend.excess.assess_stock`` (no database, fixed as-of
2026-06-30):
  * on hand 0 (or negative) -> None,
  * never demanded -> dead/never_moved with every unit in excess and
    money = units * cost,
  * last demand 400 days before as_of (outside the 12-month window) ->
    dead/stopped, as does demand exactly on the exclusive window start,
  * demand just inside the window but zero window units -> excess with
    no months of cover,
  * 120 on hand vs 60 window demand and reorder point 20 -> excess 60,
    months_of_cover 24.0,
  * the same with reorder point 100 -> excess 20,
  * on hand at or below the justified cover -> None,
  * a missing unit cost -> money None with "unit cost missing".

Against the test database (every date a fixed past calendar date; the
as-of is 2026-06-30 and date.today is pinned to 2026-07-01 so the
as_of=None default resolves to the seeded 2026-06-30):
  * excess_ranked orders by money_tied_up descending with None last,
    breaking ties by item_code then site_code, and ranks 1..n,
  * the zero-on-hand and the on-hand-below-justified rows are absent,
  * the ok-recommended item's justified cover is
    max(ceil(window demand), recommended reorder point),
  * EXCESS_COVER_MONTHS=6 re-runs the six-month window (I-86's money
    rises to 550.00 and takes rank 1; B-86's to 80.00), and blank,
    non-numeric, zero, negative or fractional values fall back to 12.
"""

import math
import statistics
from datetime import date, timedelta

from app.db.connection import get_connection
from app.recommend.excess import assess_stock, excess_ranked
from app.recommend.reorder import recommendations_for_all

AS_OF = date(2026, 6, 30)
FIXED_TODAY = date(2026, 7, 1)
IN_WINDOW = AS_OF - timedelta(days=10)

# (key, item_code, site_code, quantity, occurred_on) spot demand events.
SPOT_EVENTS = (
    ("a1", "A-86", "SITE-1", 10.0, "2026-03-01"),
    ("a2", "A-86", "SITE-1", 20.0, "2026-06-30"),
    ("b1", "B-86", "SITE-1", 20.0, "2025-09-15"),
    ("b2", "B-86", "SITE-1", 10.0, "2026-05-15"),
    ("b3", "B-86", "SITE-2", 20.0, "2025-09-15"),
    ("b4", "B-86", "SITE-2", 10.0, "2026-05-15"),
    ("c1", "C-86", "SITE-1", 15.0, "2025-05-10"),
    ("d1", "D-86", "SITE-1", 40.0, "2026-04-10"),
    ("e1", "E-86", "SITE-1", 20.0, "2026-02-20"),
    ("z1", "Z-86", "SITE-1", 2.0, "2026-03-15"),
)


def test_issue86_freeform(client, monkeypatch):
    # --- pure: nothing on hand -> nothing to assess ------------------------
    assert assess_stock(0, 2.5, None, 0, None, AS_OF, 12) is None
    assert assess_stock(-5, 2.5, IN_WINDOW, 60, 20, AS_OF, 12) is None

    # --- pure: never demanded -> dead/never_moved --------------------------
    row = assess_stock(100, 2.5, None, 0, None, AS_OF, 12)
    assert row["category"] == "dead"
    assert row["dead_reason"] == "never_moved"
    assert row["justified_units"] == 0
    assert row["excess_units"] == 100
    assert row["money_tied_up"] == 250.0
    assert row["months_of_cover"] is None
    assert row["on_hand_units"] == 100
    assert row["unit_cost"] == 2.5
    assert row["last_demand_on"] is None
    assert row["demand_window_units"] == 0
    assert row["as_of"] == AS_OF
    assert row["notes"] == []
    assert "excess_units = on_hand_units = 100" in row["arithmetic"]

    # --- pure: demand stopped outside the window -> dead/stopped -----------
    row = assess_stock(80, 1.5, AS_OF - timedelta(days=400), 0, 10, AS_OF, 12)
    assert row["category"] == "dead"
    assert row["dead_reason"] == "stopped"
    assert row["justified_units"] == 0
    assert row["excess_units"] == 80
    assert row["money_tied_up"] == 120.0
    # Demand exactly on the exclusive window start counts as stopped too.
    row = assess_stock(50, 1.0, AS_OF - timedelta(days=360), 0, None, AS_OF, 12)
    assert row["dead_reason"] == "stopped"

    # --- pure: demand just inside the window with zero window units --------
    row = assess_stock(50, 1.0, AS_OF - timedelta(days=359), 0, None, AS_OF, 12)
    assert row["category"] == "excess"
    assert row["justified_units"] == 0
    assert row["excess_units"] == 50
    assert row["months_of_cover"] is None

    # --- pure: the excess case -----------------------------------------------
    row = assess_stock(120, 2.5, IN_WINDOW, 60, 20, AS_OF, 12)
    assert row["category"] == "excess"
    assert row["justified_units"] == 60
    assert row["excess_units"] == 60
    assert row["money_tied_up"] == 150.0
    assert row["months_of_cover"] == 24.0
    assert row["notes"] == []
    assert "max(ceil(60), 20) = 60" in row["arithmetic"]
    assert "120 - 60 = 60" in row["arithmetic"]

    # The reorder point dominates the window demand.
    row = assess_stock(120, 2.5, IN_WINDOW, 60, 100, AS_OF, 12)
    assert row["justified_units"] == 100
    assert row["excess_units"] == 20
    assert row["money_tied_up"] == 50.0
    assert "max(ceil(60), 100) = 100" in row["arithmetic"]

    # --- pure: on hand within the justified cover -> nothing to flag --------
    assert assess_stock(50, 2.5, IN_WINDOW, 60, 20, AS_OF, 12) is None
    assert assess_stock(60, 2.5, IN_WINDOW, 60, 20, AS_OF, 12) is None

    # --- pure: a missing unit cost leaves the money unpriced -----------------
    row = assess_stock(120, None, IN_WINDOW, 60, 20, AS_OF, 12)
    assert row["category"] == "excess"
    assert row["money_tied_up"] is None
    assert "unit cost missing" in row["notes"]
    row = assess_stock(100, None, None, 0, None, AS_OF, 12)
    assert row["category"] == "dead"
    assert row["money_tied_up"] is None
    assert "unit cost missing" in row["notes"]

    # --- seed: ranked item-sites with fixed past dates -----------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            i86_first_day = date(2025, 7, 6)
            i86_rows = [
                (
                    f"id-de-86-i-{day_index}",
                    f"de-86-i-{day_index}",
                    "I-86",
                    "SITE-1",
                    5.0,
                    (i86_first_day + timedelta(days=day_index)).isoformat(),
                    "shipment",
                    "test",
                )
                for day_index in range(360)
            ]
            spot_rows = [
                (
                    f"id-de-86-{key}",
                    f"de-86-{key}",
                    item_code,
                    site_code,
                    quantity,
                    occurred_on,
                    "shipment",
                    "test",
                )
                for key, item_code, site_code, quantity, occurred_on in SPOT_EVENTS
            ]
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                i86_rows + spot_rows,
            )
            # Lead times 10, 12 and 14 days: LT 12, sd_LT 2, n_used 3.
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, "
                "ordered_on, received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-lt-86-1",
                        "lt-86-1",
                        "SUP-I",
                        "I-86",
                        "2026-04-22",
                        "2026-05-02",
                        "test",
                    ),
                    (
                        "id-lt-86-2",
                        "lt-86-2",
                        "SUP-I",
                        "I-86",
                        "2026-04-29",
                        "2026-05-11",
                        "test",
                    ),
                    (
                        "id-lt-86-3",
                        "lt-86-3",
                        "SUP-I",
                        "I-86",
                        "2026-04-28",
                        "2026-05-12",
                        "test",
                    ),
                ],
            )
            as_of_stamp = "2026-06-30 00:00:00+00"
            cur.executemany(
                "INSERT INTO on_hand "
                "(id, item_code, site_code, quantity_on_hand, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    ("id-oh-86-a", "A-86", "SITE-1", 100.0, as_of_stamp, "test"),
                    ("id-oh-86-b1", "B-86", "SITE-1", 90.0, as_of_stamp, "test"),
                    ("id-oh-86-b2", "B-86", "SITE-2", 90.0, as_of_stamp, "test"),
                    ("id-oh-86-c", "C-86", "SITE-1", 50.0, as_of_stamp, "test"),
                    ("id-oh-86-d", "D-86", "SITE-1", 20.0, as_of_stamp, "test"),
                    ("id-oh-86-e", "E-86", "SITE-1", 60.0, as_of_stamp, "test"),
                    ("id-oh-86-f", "F-86", "SITE-1", 10.0, as_of_stamp, "test"),
                    ("id-oh-86-g", "G-86", "SITE-1", 0.0, as_of_stamp, "test"),
                    ("id-oh-86-i", "I-86", "SITE-1", 2000.0, as_of_stamp, "test"),
                    ("id-oh-86-z", "Z-86", "SITE-1", 5.0, as_of_stamp, "test"),
                ],
            )
            # E-86 and Z-86 intentionally have no item_sites row.
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, source_system) "
                "VALUES (%s, %s, %s, %s, %s)",
                [
                    ("id-is-86-a", "A-86", "SITE-1", 2.0, "test"),
                    ("id-is-86-b1", "B-86", "SITE-1", 1.0, "test"),
                    ("id-is-86-b2", "B-86", "SITE-2", 1.0, "test"),
                    ("id-is-86-c", "C-86", "SITE-1", 5.0, "test"),
                    ("id-is-86-d", "D-86", "SITE-1", 4.0, "test"),
                    ("id-is-86-f", "F-86", "SITE-1", 3.0, "test"),
                    ("id-is-86-i", "I-86", "SITE-1", 0.5, "test"),
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

    # I-86 (smooth 5 units/day, LT 12 sd 2) is the only "ok" recommendation.
    recommendations = {
        (row["item_code"], row["site_code"]): row
        for row in recommendations_for_all(AS_OF)
    }
    i86_recommendation = recommendations[("I-86", "SITE-1")]
    assert i86_recommendation["status"] == "ok"
    z95 = statistics.NormalDist().inv_cdf(0.95)
    expected_rrp_units = math.ceil(5.0 * 12.0 + z95 * 10.0)
    assert expected_rrp_units == 77
    assert i86_recommendation["reorder_point_units"] == expected_rrp_units
    for key in (
        ("A-86", "SITE-1"),
        ("B-86", "SITE-1"),
        ("C-86", "SITE-1"),
        ("D-86", "SITE-1"),
        ("E-86", "SITE-1"),
        ("Z-86", "SITE-1"),
    ):
        assert recommendations[key]["status"] != "ok"

    # --- excess_ranked: as_of=None resolves to the fixed 2026-06-30 ---------
    monkeypatch.delenv("EXCESS_COVER_MONTHS", raising=False)
    rows = excess_ranked()
    assert rows and all(row["as_of"] == AS_OF for row in rows)
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        ("C-86", "SITE-1"),  # 250.00 dead/stopped
        ("A-86", "SITE-1"),  # 140.00
        ("I-86", "SITE-1"),  # 100.00
        ("B-86", "SITE-1"),  # 60.00, tie broken by item_code/site_code
        ("B-86", "SITE-2"),  # 60.00
        ("F-86", "SITE-1"),  # 30.00 dead/never_moved
        ("E-86", "SITE-1"),  # None money last, then item_code
        ("Z-86", "SITE-1"),  # None money
    ]
    assert [row["rank"] for row in rows] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [row["money_tied_up"] for row in rows] == [
        250.0,
        140.0,
        100.0,
        60.0,
        60.0,
        30.0,
        None,
        None,
    ]
    # G-86 (zero on hand) and D-86 (on hand below the justified cover)
    # are not reported.
    assert all(row["item_code"] not in ("G-86", "D-86") for row in rows)

    by_key = {(row["item_code"], row["site_code"]): row for row in rows}
    stopped = by_key[("C-86", "SITE-1")]
    assert stopped["category"] == "dead"
    assert stopped["dead_reason"] == "stopped"
    assert stopped["excess_units"] == 50
    assert stopped["months_of_cover"] is None
    never_moved = by_key[("F-86", "SITE-1")]
    assert never_moved["category"] == "dead"
    assert never_moved["dead_reason"] == "never_moved"
    assert never_moved["excess_units"] == 10
    a_row = by_key[("A-86", "SITE-1")]
    assert a_row["category"] == "excess"
    assert a_row["demand_window_units"] == 30
    assert a_row["justified_units"] == 30
    assert a_row["excess_units"] == 70
    assert a_row["months_of_cover"] == 40.0
    i_row = by_key[("I-86", "SITE-1")]
    assert i_row["justified_units"] == max(1800, expected_rrp_units)
    assert i_row["excess_units"] == 200
    assert i_row["months_of_cover"] == 13.3
    assert f"max(ceil(1800), {expected_rrp_units}) = 1800" in i_row["arithmetic"]
    e_row = by_key[("E-86", "SITE-1")]
    assert e_row["unit_cost"] is None
    assert e_row["excess_units"] == 40
    assert e_row["money_tied_up"] is None
    assert "unit cost missing" in e_row["notes"]
    z_row = by_key[("Z-86", "SITE-1")]
    assert z_row["excess_units"] == 3
    assert z_row["money_tied_up"] is None

    # --- EXCESS_COVER_MONTHS=6: the six-month window re-runs the ranking ----
    monkeypatch.setenv("EXCESS_COVER_MONTHS", "6")
    rows = excess_ranked(as_of=AS_OF)
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        ("I-86", "SITE-1"),  # 550.00 (900 units of six-month demand)
        ("C-86", "SITE-1"),  # 250.00
        ("A-86", "SITE-1"),  # 140.00
        ("B-86", "SITE-1"),  # 80.00 (only 10 units left in the window)
        ("B-86", "SITE-2"),  # 80.00
        ("F-86", "SITE-1"),  # 30.00
        ("E-86", "SITE-1"),  # None money last
        ("Z-86", "SITE-1"),  # None money
    ]
    assert [row["rank"] for row in rows] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [row["money_tied_up"] for row in rows] == [
        550.0,
        250.0,
        140.0,
        80.0,
        80.0,
        30.0,
        None,
        None,
    ]
    by_key = {(row["item_code"], row["site_code"]): row for row in rows}
    assert by_key[("I-86", "SITE-1")]["excess_units"] == 1100
    assert by_key[("I-86", "SITE-1")]["months_of_cover"] == 13.3
    assert by_key[("B-86", "SITE-1")]["excess_units"] == 80
    assert by_key[("A-86", "SITE-1")]["months_of_cover"] == 20.0

    # --- hostile EXCESS_COVER_MONTHS values fall back to the 12-month -------
    default_money = [250.0, 140.0, 100.0, 60.0, 60.0, 30.0, None, None]
    for raw in ("abc", "", "0", "-4", "2.5"):
        monkeypatch.setenv("EXCESS_COVER_MONTHS", raw)
        fallback = excess_ranked(as_of=AS_OF)
        assert [row["money_tied_up"] for row in fallback] == default_money, raw
    monkeypatch.delenv("EXCESS_COVER_MONTHS", raising=False)
