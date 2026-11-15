"""Issue #115 proof: open purchase supply counted into the stock position.

Pure ``app.recommend.stockout.assess_stockout`` (no database, d 10,
sd_d 5, lt 10, sd_lt 2, service level 0.95, as-of 2026-06-30, so mu 100
and sigma sqrt(650)):
  * 80 on hand with no open supply keeps p_stockout 0.7836, carries
    open_supply_units 0 and no longer any "open purchase orders not
    included" note,
  * 80 on hand plus 70 open units due 2026-07-05 gives a position of
    150 -> p_stockout 0.0249 is at or below 1 - 0.95 -> None,
  * the position (on hand + open supply), not the on hand alone, drives
    expected_units_short, days_of_cover and projected_stockout_on,
  * lead_time_days is rounded to 2 decimals.

Against the test database (every date a fixed past calendar date, the
as-of 2026-06-30; date.today is pinned to 2026-07-01 so the as_of=None
default resolves to the seeded 2026-06-30):
  * two item-sites on SITE-1, each with 360 days of steady 10/day
    demand (d 10, sd_d 0, smooth) and three pooled lead time
    observations (8, 10, 12 days -> lt 10, sd_lt 2), so the window
    upper bound is as-of + 10 days = 2026-07-10,
  * P-115 (60 on hand): a line of 70 units promised 2026-07-05 counts
    and yields open_supply_units 70, open_supply_due_by 2026-07-05 and
    the "due by" note; a line promised 2026-07-20 (after as-of + 10
    days) does not count and has no note; a line promised 2026-03-01
    (more than 90 days past due) is counted stale and excluded; an
    undated line is counted undated and excluded; a fully received
    line (open_units 0) is ignored entirely,
  * Q-115 (80 on hand) with the same counted 70 units reaches a
    position of 150 and drops out of the ranking,
  * the API rows carry open_supply_units and open_supply_due_by, and
    the CSV header places both columns immediately after on_hand_units.
"""

import csv
import io
from datetime import date, timedelta

from app.db.connection import get_connection
from app.recommend.reorder import recommendations_for_all
from app.recommend.stockout import assess_stockout, stockout_ranked

AS_OF = date(2026, 6, 30)
AS_OF_ISO = "2026-06-30"
FIRST_DAY = date(2025, 7, 6)  # 360 days before AS_OF
FIXED_TODAY = date(2026, 7, 1)
AS_OF_STAMP = "2026-06-30 00:00:00+00"

D, SD_D, LT, SD_LT = 10.0, 5.0, 10.0, 2.0
SERVICE_LEVEL = 0.95
RPP_UNITS = 130

CSV_HEADERS = [
    "rank",
    "category",
    "item_code",
    "site_code",
    "p_stockout",
    "projected_stockout_on",
    "days_of_cover",
    "on_hand_units",
    "open_supply_units",
    "open_supply_due_by",
    "lead_time_days",
    "lead_time_demand",
    "recommended_reorder_point_units",
    "expected_units_short",
    "unit_cost",
    "money_at_risk",
    "notes",
    "arithmetic",
    "as_of",
]


def _daily_demand(item_code: str) -> list[tuple]:
    """360 daily demand events of 10 units, FIRST_DAY through AS_OF."""
    return [
        (
            f"id-de-115-{item_code}-{day_index}",
            f"de-115-{item_code}-{day_index}",
            item_code,
            "SITE-1",
            10.0,
            (FIRST_DAY + timedelta(days=day_index)).isoformat(),
            "shipment",
            "test",
        )
        for day_index in range(360)
    ]


def _parse(text: str) -> list[list[str]]:
    """Assert the UTF-8 BOM and parse the CSV text into rows."""
    assert text.startswith("\ufeff")
    body = text.encode("utf-8")
    assert body.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))


def test_issue115_freeform(client, monkeypatch):
    # --- pure: 80 on hand, no open supply ----------------------------------
    row = assess_stockout(80, 2.0, D, SD_D, LT, SD_LT, RPP_UNITS, SERVICE_LEVEL, AS_OF)
    assert row is not None
    assert row["category"] == "likely"
    assert row["p_stockout"] == 0.7836
    assert row["on_hand_units"] == 80
    assert row["open_supply_units"] == 0.0
    assert row["lead_time_days"] == LT
    assert row["lead_time_demand"] == 100.0
    assert row["days_of_cover"] == 8.0
    assert row["projected_stockout_on"] == date(2026, 7, 8)
    assert row["expected_units_short"] == 23.15
    assert row["money_at_risk"] == 46.3
    assert row["as_of"] == AS_OF
    assert "open purchase orders not included" not in row["notes"]
    assert row["notes"] == []
    assert "1 - Phi((80 - 100) / 25.4951) = 0.7836" in row["arithmetic"]

    # --- pure: 80 on hand plus 70 open units -> position 150 -> None --------
    assert (
        assess_stockout(
            80,
            2.0,
            D,
            SD_D,
            LT,
            SD_LT,
            RPP_UNITS,
            SERVICE_LEVEL,
            AS_OF,
            open_supply_units=70,
        )
        is None
    )

    # --- pure: the position drives every position-dependent field -----------
    # 80 on hand + 20 open units: position 100, z 0.
    row = assess_stockout(
        80,
        2.0,
        D,
        SD_D,
        LT,
        SD_LT,
        RPP_UNITS,
        SERVICE_LEVEL,
        AS_OF,
        open_supply_units=20,
    )
    assert row["category"] == "at_risk"
    assert row["on_hand_units"] == 80
    assert row["open_supply_units"] == 20.0
    assert row["p_stockout"] == 0.5
    assert row["days_of_cover"] == 10.0
    assert row["projected_stockout_on"] == date(2026, 7, 10)
    assert row["expected_units_short"] == 10.17
    assert row["money_at_risk"] == round(10.17 * 2.0, 2)
    assert "1 - Phi((100 - 100) / 25.4951) = 0.5" in row["arithmetic"]

    # --- pure: lead_time_days is rounded to 2 decimals ----------------------
    row = assess_stockout(
        80, 2.0, D, SD_D, 10.123, SD_LT, RPP_UNITS, SERVICE_LEVEL, AS_OF
    )
    assert row["lead_time_days"] == 10.12

    # --- seed: steady demand, pooled lead times, on hand, costs --------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                _daily_demand("P-115") + _daily_demand("Q-115"),
            )
            # P-115 and Q-115: 8, 10, 12 days -> mean 10, sample std 2.
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-lt-115-p1",
                        "lt-115-p1",
                        "SUP-P",
                        "P-115",
                        "2026-02-01",
                        "2026-02-09",
                        "test",
                    ),
                    (
                        "id-lt-115-p2",
                        "lt-115-p2",
                        "SUP-P",
                        "P-115",
                        "2026-02-05",
                        "2026-02-15",
                        "test",
                    ),
                    (
                        "id-lt-115-p3",
                        "lt-115-p3",
                        "SUP-P",
                        "P-115",
                        "2026-02-08",
                        "2026-02-20",
                        "test",
                    ),
                    (
                        "id-lt-115-q1",
                        "lt-115-q1",
                        "SUP-Q",
                        "Q-115",
                        "2026-03-01",
                        "2026-03-09",
                        "test",
                    ),
                    (
                        "id-lt-115-q2",
                        "lt-115-q2",
                        "SUP-Q",
                        "Q-115",
                        "2026-03-05",
                        "2026-03-15",
                        "test",
                    ),
                    (
                        "id-lt-115-q3",
                        "lt-115-q3",
                        "SUP-Q",
                        "Q-115",
                        "2026-03-08",
                        "2026-03-20",
                        "test",
                    ),
                ],
            )
            cur.executemany(
                "INSERT INTO on_hand "
                "(id, item_code, site_code, quantity_on_hand, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    ("id-oh-115-p", "P-115", "SITE-1", 60.0, AS_OF_STAMP, "test"),
                    ("id-oh-115-q", "Q-115", "SITE-1", 80.0, AS_OF_STAMP, "test"),
                ],
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, master_lead_time_days, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    ("id-is-115-p", "P-115", "SITE-1", 2.0, None, "test"),
                    ("id-is-115-q", "Q-115", "SITE-1", 1.0, None, "test"),
                ],
            )
            # P-115: one counted line (due 2026-07-05, inside the window),
            # one line due 2026-07-20 (after as-of + 10 days), one stale
            # line (promised 2026-03-01, more than 90 days before as-of),
            # one undated line and one fully received line (open_units 0).
            # Q-115: one counted line of the same 70 units.
            cur.executemany(
                "INSERT INTO open_purchase_lines "
                "(id, business_key, item_code, site_code, supplier_code, "
                "order_type, open_units, promised_on, ordered_on, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-op-115-p1",
                        "op-115-p1",
                        "P-115",
                        "SITE-1",
                        "SUP-P",
                        "RO",
                        70.0,
                        "2026-07-05",
                        "2026-06-20",
                        AS_OF_STAMP,
                        "test",
                    ),
                    (
                        "id-op-115-p2",
                        "op-115-p2",
                        "P-115",
                        "SITE-1",
                        "SUP-P",
                        "RO",
                        100.0,
                        "2026-07-20",
                        "2026-06-21",
                        AS_OF_STAMP,
                        "test",
                    ),
                    (
                        "id-op-115-p3",
                        "op-115-p3",
                        "P-115",
                        "SITE-1",
                        "SUP-P",
                        "RO",
                        40.0,
                        "2026-03-01",
                        "2026-02-01",
                        AS_OF_STAMP,
                        "test",
                    ),
                    (
                        "id-op-115-p4",
                        "op-115-p4",
                        "P-115",
                        "SITE-1",
                        "SUP-P",
                        "RO",
                        25.0,
                        None,
                        "2026-06-01",
                        AS_OF_STAMP,
                        "test",
                    ),
                    (
                        "id-op-115-p5",
                        "op-115-p5",
                        "P-115",
                        "SITE-1",
                        "SUP-P",
                        "RO",
                        0.0,
                        "2026-07-06",
                        "2026-06-22",
                        AS_OF_STAMP,
                        "test",
                    ),
                    (
                        "id-op-115-q1",
                        "op-115-q1",
                        "Q-115",
                        "SITE-1",
                        "SUP-Q",
                        "RO",
                        70.0,
                        "2026-07-05",
                        "2026-06-20",
                        AS_OF_STAMP,
                        "test",
                    ),
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
    for key in (("P-115", "SITE-1"), ("Q-115", "SITE-1")):
        assert recommendations[key]["status"] == "ok", key
        assert recommendations[key]["inputs"]["d"] == 10.0
        assert recommendations[key]["inputs"]["sd_d"] == 0.0
        assert recommendations[key]["inputs"]["lt"] == 10.0
        assert recommendations[key]["inputs"]["sd_lt"] == 2.0
        assert recommendations[key]["inputs"]["service_level"] == 0.95

    # --- stockout_ranked: open supply counted per item-site -----------------
    rows = stockout_ranked(AS_OF)
    # Q-115's 80 on hand plus the counted 70 open units reaches a position
    # of 150 (z 2.5 with this pair's sigma 20, p 0.0062 at or below
    # 1 - 0.95) and drops out of the ranking; only P-115 remains.
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        ("P-115", "SITE-1")
    ]
    assert [row["rank"] for row in rows] == [1]
    p_row = rows[0]
    assert p_row["category"] == "at_risk"
    assert p_row["on_hand_units"] == 60.0
    assert p_row["open_supply_units"] == 70.0
    assert p_row["open_supply_due_by"] == date(2026, 7, 5)
    assert p_row["lead_time_days"] == 10.0
    assert p_row["p_stockout"] == 0.0668
    assert p_row["days_of_cover"] == 13.0
    assert p_row["projected_stockout_on"] == date(2026, 7, 13)
    assert p_row["expected_units_short"] == 0.59
    assert p_row["money_at_risk"] == 1.18
    # The 2026-07-20 line (after as-of + 10 days) counts neither in the
    # units nor in any note; the 2026-03-01 line is stale; the undated
    # line and the open_units 0 line leave no trace.
    assert p_row["notes"] == [
        "open purchase orders: 70 units due by 2026-07-05",
        "1 stale open purchase lines excluded",
        "1 undated open purchase lines excluded",
    ]

    # --- as_of=None resolves to the fixed 2026-06-30 -------------------------
    assert all(row["as_of"] == AS_OF for row in rows)
    assert stockout_ranked() == stockout_ranked(AS_OF)

    # --- the API carries the two new fields ----------------------------------
    response = client.get("/api/stockout", params={"as_of": AS_OF_ISO, "limit": 1000})
    assert response.status_code == 200, response.text
    listed = response.json()
    assert len(listed) == 1
    api_row = listed[0]
    assert api_row["item_code"] == "P-115"
    assert api_row["on_hand_units"] == 60.0
    assert api_row["open_supply_units"] == 70.0
    assert api_row["open_supply_due_by"] == "2026-07-05"
    assert api_row["p_stockout"] == 0.0668
    assert api_row["notes"] == [
        "open purchase orders: 70 units due by 2026-07-05",
        "1 stale open purchase lines excluded",
        "1 undated open purchase lines excluded",
    ]

    # --- the CSV carries both columns immediately after on_hand_units --------
    export = client.get("/api/stockout/export.csv", params={"as_of": AS_OF_ISO})
    assert export.status_code == 200, export.text
    assert export.headers["content-type"] == "text/csv; charset=utf-8"
    assert "stockout-2026-06-30.csv" in export.headers["content-disposition"]
    csv_rows = _parse(export.text)
    assert csv_rows[0] == CSV_HEADERS
    assert (
        csv_rows[0].index("open_supply_units") == csv_rows[0].index("on_hand_units") + 1
    )
    assert (
        csv_rows[0].index("open_supply_due_by")
        == csv_rows[0].index("on_hand_units") + 2
    )
    header_index = {name: position for position, name in enumerate(CSV_HEADERS)}
    data = csv_rows[1]
    assert len(csv_rows) - 1 == len(listed)
    assert data[header_index["rank"]] == "1"
    assert data[header_index["item_code"]] == "P-115"
    assert data[header_index["on_hand_units"]] == "60.0"
    assert data[header_index["open_supply_units"]] == "70.0"
    assert data[header_index["open_supply_due_by"]] == "2026-07-05"
    assert data[header_index["p_stockout"]] == "0.0668"
    assert data[header_index["lead_time_days"]] == "10.0"
    assert data[header_index["days_of_cover"]] == "13.0"
    assert data[header_index["projected_stockout_on"]] == "2026-07-13"
    assert data[header_index["money_at_risk"]] == "1.18"
    assert data[header_index["notes"]] == (
        "open purchase orders: 70 units due by 2026-07-05; "
        "1 stale open purchase lines excluded; "
        "1 undated open purchase lines excluded"
    )
    assert data[header_index["as_of"]] == AS_OF_ISO
