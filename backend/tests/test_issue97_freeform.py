"""Issue #97 proof: stockout risk, assessed and ranked by probability.

Pure ``app.recommend.stockout.assess_stockout`` (no database, d 10,
sd_d 5, lt 10, sd_lt 2, service level 0.95, as-of 2026-06-30, so mu 100
and sigma sqrt(650)):
  * 80 on hand -> "likely" with p_stockout 0.7836, lead_time_demand
    100.0, days_of_cover 8.0, projected_stockout_on 2026-07-08,
    expected_units_short 23.15 and money = 23.15 * unit cost,
  * 130 on hand -> "at_risk" with p_stockout 0.1197,
  * 150 on hand -> p_stockout 0.0249 is at or below 1 - 0.95 -> None,
  * 0 on hand -> p_stockout 1.0 and expected_units_short 100.0,
  * sigma 0 with on hand below mu -> p_stockout 1.0 (and above mu ->
    None),
  * a missing unit cost -> money None with "unit cost missing",
  * 0.3 on hand at d 0.1 (0.3 / 0.1 is 2.9999999999999996 in binary
    float) still projects the stockout exactly 3 days out,
  * a p_stockout sitting exactly on 1 - service_level is None (1.0 -
    0.9 is 0.09999999999999998 in binary float), one step above it is
    "at_risk",
  * the pure rows no longer carry any "open purchase orders not
    included" note (removed with issue #115), and every ranked row's
    notes include "no open purchase orders due in the lead time" (no
    open purchase lines are seeded).

Against the test database (every date a fixed past calendar date, the
as-of 2026-06-30; date.today is pinned to 2026-07-01 so the as_of=None
default resolves to the seeded 2026-06-30):
  * stockout_ranked orders by p_stockout descending, then
    money_at_risk descending with None last, and ranks 1..n,
  * C-97 (no on-hand row) is treated as 0 with the note and F-97
    (master lead time) ties it at p_stockout 1.0 behind it on money,
  * A-97 and B-97 tie at p_stockout 0.8413 (z -1) and A-97's priced
    money beats B-97's missing unit cost,
  * D-97 (no demand, hence no recommendation) and E-97
    (insufficient_lead_time) are excluded despite on-hand rows.
"""

from datetime import date, timedelta

from app.db.connection import get_connection
from app.recommend.reorder import recommendations_for_all
from app.recommend.stockout import assess_stockout, stockout_ranked

AS_OF = date(2026, 6, 30)
FIRST_DAY = date(2025, 7, 6)  # 360 days before AS_OF
FIXED_TODAY = date(2026, 7, 1)
AS_OF_STAMP = "2026-06-30 00:00:00+00"

D, SD_D, LT, SD_LT = 10.0, 5.0, 10.0, 2.0
SERVICE_LEVEL = 0.95
RPP_UNITS = 130


def _daily_demand(item_code: str, unit_per_day: float) -> list[tuple]:
    """360 daily demand events, FIRST_DAY through AS_OF inclusive."""
    return [
        (
            f"id-de-97-{item_code}-{day_index}",
            f"de-97-{item_code}-{day_index}",
            item_code,
            "SITE-1",
            unit_per_day,
            (FIRST_DAY + timedelta(days=day_index)).isoformat(),
            "shipment",
            "test",
        )
        for day_index in range(360)
    ]


def test_issue97_freeform(client, monkeypatch):
    # --- pure: 80 on hand -> "likely" --------------------------------------
    row = assess_stockout(80, 2.0, D, SD_D, LT, SD_LT, RPP_UNITS, SERVICE_LEVEL, AS_OF)
    assert row["category"] == "likely"
    assert row["p_stockout"] == 0.7836
    assert row["on_hand_units"] == 80
    assert row["unit_cost"] == 2.0
    assert row["lead_time_days"] == LT
    assert row["lead_time_demand"] == 100.0
    assert row["days_of_cover"] == 8.0
    assert row["projected_stockout_on"] == date(2026, 7, 8)
    assert row["recommended_reorder_point_units"] == RPP_UNITS
    assert row["expected_units_short"] == 23.15
    assert row["money_at_risk"] == round(23.15 * 2.0, 2)
    assert row["as_of"] == AS_OF
    assert "open purchase orders not included" not in row["notes"]
    assert row["notes"] == []
    assert "mu = d * lt = 10 * 10 = 100" in row["arithmetic"]
    assert "1 - Phi((80 - 100) / 25.4951) = 0.7836" in row["arithmetic"]

    # --- pure: 130 on hand -> "at_risk" ------------------------------------
    row = assess_stockout(130, 2.0, D, SD_D, LT, SD_LT, RPP_UNITS, SERVICE_LEVEL, AS_OF)
    assert row["category"] == "at_risk"
    assert row["p_stockout"] == 0.1197
    assert "open purchase orders not included" not in row["notes"]
    assert row["notes"] == []

    # --- pure: 150 on hand -> p_stockout below the acceptable level --------
    assert (
        assess_stockout(150, 2.0, D, SD_D, LT, SD_LT, RPP_UNITS, SERVICE_LEVEL, AS_OF)
        is None
    )

    # --- pure: 0 on hand ----------------------------------------------------
    row = assess_stockout(0, 2.0, D, SD_D, LT, SD_LT, RPP_UNITS, SERVICE_LEVEL, AS_OF)
    assert row["category"] == "likely"
    assert row["p_stockout"] == 1.0
    assert row["expected_units_short"] == 100.0
    assert row["money_at_risk"] == 200.0
    assert row["days_of_cover"] == 0.0
    assert row["projected_stockout_on"] == AS_OF
    assert "open purchase orders not included" not in row["notes"]
    assert row["notes"] == []

    # --- pure: sigma 0 -------------------------------------------------------
    row = assess_stockout(50, 1.0, 10.0, 0.0, 10.0, 0.0, 100, SERVICE_LEVEL, AS_OF)
    assert row["p_stockout"] == 1.0
    assert row["lead_time_demand"] == 100.0
    assert row["expected_units_short"] == 50.0
    assert row["money_at_risk"] == 50.0
    assert row["projected_stockout_on"] == date(2026, 7, 5)
    assert "50 < 100" in row["arithmetic"]
    assert (
        assess_stockout(100, 1.0, 10.0, 0.0, 10.0, 0.0, 100, SERVICE_LEVEL, AS_OF)
        is None
    )

    # --- pure: a missing unit cost leaves the money unpriced -----------------
    row = assess_stockout(80, None, D, SD_D, LT, SD_LT, RPP_UNITS, SERVICE_LEVEL, AS_OF)
    assert row["expected_units_short"] == 23.15
    assert row["money_at_risk"] is None
    assert "unit cost missing" in row["notes"]
    assert "open purchase orders not included" not in row["notes"]
    assert row["notes"] == ["unit cost missing"]

    # --- pure: an exact whole day of cover is not lost to float floor --------
    # 0.3 / 0.1 is 2.9999999999999996 in binary float; the floor of the
    # decimal quotient is 3, so the stockout lands 2026-07-03, not 07-02.
    row = assess_stockout(0.3, 1.0, 0.1, 0.0, 10.0, 0.0, 1, SERVICE_LEVEL, AS_OF)
    assert row["p_stockout"] == 1.0
    assert row["days_of_cover"] == 3.0
    assert row["projected_stockout_on"] == date(2026, 7, 3)
    assert row["expected_units_short"] == 0.7
    assert row["money_at_risk"] == 0.7
    # A non-integer cover still floors (7 / 2 is 3 days, not 4).
    row = assess_stockout(7, 1.0, 2.0, 0.0, 10.0, 0.0, 20, SERVICE_LEVEL, AS_OF)
    assert row["days_of_cover"] == 3.5
    assert row["projected_stockout_on"] == date(2026, 7, 3)

    # --- pure: a p_stockout exactly on the threshold is None ------------------
    # 1.0 - 0.9 is 0.09999999999999998 in binary float, so the raw float
    # comparison would let p_stockout 0.1 (= 1 - 0.9) through; the exact
    # decimal comparison keeps it None.
    assert (
        assess_stockout(132.67, 2.0, D, SD_D, LT, SD_LT, RPP_UNITS, 0.9, AS_OF) is None
    )
    # One step above the threshold it is still reported.
    row = assess_stockout(132.0, 2.0, D, SD_D, LT, SD_LT, RPP_UNITS, 0.9, AS_OF)
    assert row["category"] == "at_risk"
    assert row["p_stockout"] == 0.1047

    # --- seed: ranked item-sites with fixed past dates -----------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                _daily_demand("A-97", 5.0)
                + _daily_demand("B-97", 2.5)
                + _daily_demand("C-97", 6.0)
                + _daily_demand("E-97", 3.0)
                + _daily_demand("F-97", 1.0),
            )
            # Pooled lead times per item (received_on - ordered_on):
            #   A-97: 8, 12, 10 -> mean 10, sd 2, n_used 3
            #   B-97: 10, 8, 12 -> mean 10, sd 2, n_used 3
            #   C-97: 9, 11, 10 -> mean 10, sd 1, n_used 3
            #   E-97: 10, 11     -> n_used 2, low confidence
            #   F-97: 12, 13     -> n_used 2, low confidence
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, "
                "ordered_on, received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-lt-97-a1",
                        "lt-97-a1",
                        "SUP-A",
                        "A-97",
                        "2026-04-22",
                        "2026-04-30",
                        "test",
                    ),
                    (
                        "id-lt-97-a2",
                        "lt-97-a2",
                        "SUP-A",
                        "A-97",
                        "2026-04-29",
                        "2026-05-11",
                        "test",
                    ),
                    (
                        "id-lt-97-a3",
                        "lt-97-a3",
                        "SUP-A",
                        "A-97",
                        "2026-04-28",
                        "2026-05-08",
                        "test",
                    ),
                    (
                        "id-lt-97-b1",
                        "lt-97-b1",
                        "SUP-B",
                        "B-97",
                        "2026-05-01",
                        "2026-05-11",
                        "test",
                    ),
                    (
                        "id-lt-97-b2",
                        "lt-97-b2",
                        "SUP-B",
                        "B-97",
                        "2026-05-02",
                        "2026-05-10",
                        "test",
                    ),
                    (
                        "id-lt-97-b3",
                        "lt-97-b3",
                        "SUP-B",
                        "B-97",
                        "2026-05-03",
                        "2026-05-15",
                        "test",
                    ),
                    (
                        "id-lt-97-c1",
                        "lt-97-c1",
                        "SUP-C",
                        "C-97",
                        "2026-03-10",
                        "2026-03-19",
                        "test",
                    ),
                    (
                        "id-lt-97-c2",
                        "lt-97-c2",
                        "SUP-C",
                        "C-97",
                        "2026-03-11",
                        "2026-03-22",
                        "test",
                    ),
                    (
                        "id-lt-97-c3",
                        "lt-97-c3",
                        "SUP-C",
                        "C-97",
                        "2026-03-12",
                        "2026-03-22",
                        "test",
                    ),
                    (
                        "id-lt-97-e1",
                        "lt-97-e1",
                        "SUP-E",
                        "E-97",
                        "2026-02-10",
                        "2026-02-20",
                        "test",
                    ),
                    (
                        "id-lt-97-e2",
                        "lt-97-e2",
                        "SUP-E",
                        "E-97",
                        "2026-02-11",
                        "2026-02-22",
                        "test",
                    ),
                    (
                        "id-lt-97-f1",
                        "lt-97-f1",
                        "SUP-F",
                        "F-97",
                        "2026-01-10",
                        "2026-01-22",
                        "test",
                    ),
                    (
                        "id-lt-97-f2",
                        "lt-97-f2",
                        "SUP-F",
                        "F-97",
                        "2026-01-11",
                        "2026-01-24",
                        "test",
                    ),
                ],
            )
            # C-97 intentionally has no on_hand row.
            cur.executemany(
                "INSERT INTO on_hand "
                "(id, item_code, site_code, quantity_on_hand, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    ("id-oh-97-a", "A-97", "SITE-1", 40.0, AS_OF_STAMP, "test"),
                    ("id-oh-97-b", "B-97", "SITE-1", 20.0, AS_OF_STAMP, "test"),
                    ("id-oh-97-d", "D-97", "SITE-1", 50.0, AS_OF_STAMP, "test"),
                    ("id-oh-97-e", "E-97", "SITE-1", 75.0, AS_OF_STAMP, "test"),
                    ("id-oh-97-f", "F-97", "SITE-1", 5.0, AS_OF_STAMP, "test"),
                ],
            )
            # B-97 and D-97 intentionally have no item_sites row.
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, master_lead_time_days, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    ("id-is-97-a", "A-97", "SITE-1", 2.0, None, "test"),
                    ("id-is-97-c", "C-97", "SITE-1", 1.5, None, "test"),
                    ("id-is-97-e", "E-97", "SITE-1", 1.0, None, "test"),
                    ("id-is-97-f", "F-97", "SITE-1", 0.5, 14.0, "test"),
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
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    # --- the recommendations feeding the ranking ----------------------------
    recommendations = {
        (row["item_code"], row["site_code"]): row
        for row in recommendations_for_all(AS_OF)
    }
    for key in (
        ("A-97", "SITE-1"),
        ("B-97", "SITE-1"),
        ("C-97", "SITE-1"),
        ("F-97", "SITE-1"),
    ):
        assert recommendations[key]["status"] == "ok", key
    assert recommendations[("E-97", "SITE-1")]["status"] == "insufficient_lead_time"
    assert ("D-97", "SITE-1") not in recommendations
    assert recommendations[("F-97", "SITE-1")]["notes"] == ["master lead time used"]

    # --- stockout_ranked: as_of=None resolves to the fixed 2026-06-30 --------
    rows = stockout_ranked()
    assert all(row["as_of"] == AS_OF for row in rows)
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        # p 1.0: C-97 (money 90.0) before F-97 (money 4.50).
        ("C-97", "SITE-1"),
        ("F-97", "SITE-1"),
        # p 0.8413 tie: A-97's money 21.66 before B-97's missing cost.
        ("A-97", "SITE-1"),
        ("B-97", "SITE-1"),
    ]
    assert [row["rank"] for row in rows] == [1, 2, 3, 4]
    assert [row["p_stockout"] for row in rows] == [1.0, 1.0, 0.8413, 0.8413]
    assert [row["money_at_risk"] for row in rows] == [90.0, 4.5, 21.66, None]
    # D-97 (no recommendation) and E-97 (not "ok") are excluded.
    assert all(row["item_code"] not in ("D-97", "E-97") for row in rows)
    assert all(
        "no open purchase orders due in the lead time" in row["notes"] for row in rows
    )

    by_key = {(row["item_code"], row["site_code"]): row for row in rows}
    c_row = by_key[("C-97", "SITE-1")]
    # No on-hand row: treated as 0 with its note.
    assert c_row["on_hand_units"] == 0.0
    assert c_row["category"] == "likely"
    assert c_row["lead_time_demand"] == 60.0
    assert c_row["expected_units_short"] == 60.0
    assert c_row["money_at_risk"] == 90.0
    assert c_row["days_of_cover"] == 0.0
    assert c_row["projected_stockout_on"] == AS_OF
    assert c_row["notes"] == [
        "no open purchase orders due in the lead time",
        "no on-hand record; treated as 0",
    ]
    f_row = by_key[("F-97", "SITE-1")]
    # Master lead time: sigma 0, on hand below mu -> p 1.0; the
    # recommendation's own note is appended.
    assert f_row["lead_time_days"] == 14.0
    assert f_row["lead_time_demand"] == 14.0
    assert f_row["expected_units_short"] == 9.0
    assert f_row["money_at_risk"] == 4.5
    assert f_row["projected_stockout_on"] == date(2026, 7, 5)
    assert f_row["notes"] == [
        "no open purchase orders due in the lead time",
        "master lead time used",
    ]
    a_row = by_key[("A-97", "SITE-1")]
    assert a_row["category"] == "likely"
    assert a_row["on_hand_units"] == 40.0
    assert a_row["lead_time_demand"] == 50.0
    assert a_row["expected_units_short"] == 10.83
    assert a_row["money_at_risk"] == 21.66
    assert a_row["days_of_cover"] == 8.0
    assert a_row["projected_stockout_on"] == date(2026, 7, 8)
    assert a_row["recommended_reorder_point_units"] == 67
    b_row = by_key[("B-97", "SITE-1")]
    assert b_row["unit_cost"] is None
    assert b_row["expected_units_short"] == 5.42
    assert b_row["money_at_risk"] is None
    assert "unit cost missing" in b_row["notes"]

    # --- explicit as_of agrees with the resolved default ---------------------
    assert stockout_ranked(as_of=AS_OF) == stockout_ranked()
