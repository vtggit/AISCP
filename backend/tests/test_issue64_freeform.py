"""Issue #64 proof: CSV export of the S12 reorder-point drift rows.

Pure ``app.recommend.export.drift_csv`` (no database):
  * the header row is exact,
  * money is rendered with exactly two decimals (1312.79) and the unit
    cost as 4.00 while quantities stay plain numbers (a negative
    units_gap is written as a number, not a quoted formula),
  * notes are joined with "; ",
  * a None value is an empty cell,
  * an item_code of "=SUM(A1)" is written as "'=SUM(A1)" so Excel
    never evaluates it as a formula,
  * the output starts with the UTF-8 byte-order mark,
  * row order is preserved.

Against the test database (A-64, B-64 and C-64 at SITE-64, each with
constant 10 units/day demand and lead times of 10, 12 and 14 days so
the 0.95 recommended reorder point is 152.897; as_of pinned to the
seeded 2026-09-30 and date.today pinned to 2026-10-02 for the
no-as_of export):
  * GET /api/drift/export.csv returns 200 with media type
    "text/csv; charset=utf-8" and an attachment filename containing
    the as-of date,
  * the export carries exactly the data rows of GET /api/drift with a
    large limit, in the same order,
  * the no-as_of export resolves the default as-of date,
  * direction=sideways returns 422 and POST returns 405.
"""

import csv
import io
from datetime import date, timedelta

from app.db.connection import get_connection
from app.recommend.export import drift_csv

AS_OF = date(2026, 9, 30)
FIXED_TODAY = date(2026, 10, 2)

HEADERS = [
    "rank",
    "category",
    "item_code",
    "site_code",
    "direction",
    "current_reorder_point",
    "recommended_reorder_point_units",
    "safety_stock_units",
    "units_gap",
    "unit_cost",
    "money_at_stake",
    "notes",
    "money_basis",
    "arithmetic",
    "as_of",
    "recommended_order_quantity_units",
    "recommended_max_units",
    "current_order_qty",
    "current_max_qty",
    "abc_class",
    "service_level",
]


def _parse(text: str) -> list[list[str]]:
    """Assert the UTF-8 BOM and parse the CSV text into rows."""
    assert text.startswith("\ufeff")
    body = text.encode("utf-8")
    assert body.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))


def test_issue64_freeform(client, monkeypatch):
    # --- pure: header, formatting, formula guard, BOM, order ----------------
    rows = [
        {
            "rank": 1,
            "category": "drift",
            "item_code": "ZZZ",
            "site_code": "SITE-1",
            "direction": "under",
            "current_reorder_point": 100.0,
            "reorder_point_units": 153,
            "safety_stock": 30.57,
            "units_gap": -52.9,
            "unit_cost": 4.0,
            "money_at_stake": 1312.79,
            "notes": ["no reorder point set", "cost unknown"],
            "money_basis": (
                "money_at_stake = units_short_per_year * unit_cost "
                "= 328.20 * 4 = 1312.79"
            ),
            "arithmetic": "reorder point = 120.00 + 1.64 * 20.00 = 152.90",
            "as_of": AS_OF,
        },
        {
            "rank": None,
            "category": "no_reorder_point",
            "item_code": "MMM",
            "site_code": "SITE-2",
            "direction": "over",
            "current_reorder_point": None,
            "reorder_point_units": 153,
            "safety_stock": 30.57,
            "units_gap": None,
            "unit_cost": None,
            "money_at_stake": None,
            "notes": [],
            "money_basis": "money_at_stake = unknown",
            "arithmetic": "reorder point = 120.00 + 1.64 * 20.00 = 152.90",
            "as_of": AS_OF,
        },
        {
            "rank": 2,
            "category": "drift",
            "item_code": "=SUM(A1)",
            "site_code": "SITE-1",
            "direction": "over",
            "current_reorder_point": 400.0,
            "reorder_point_units": 153,
            "safety_stock": 30.57,
            "units_gap": 247.1,
            "unit_cost": 2.5,
            "money_at_stake": 617.75,
            "notes": ["capped at annual demand"],
            "money_basis": "money_at_stake = units_gap * unit_cost = 247.10 * 2.5 = 617.75",
            "arithmetic": "reorder point = 120.00 + 1.64 * 20.00 = 152.90",
            "as_of": AS_OF,
        },
    ]
    parsed = _parse(drift_csv(rows))

    assert parsed[0] == HEADERS
    first, second, third = parsed[1], parsed[2], parsed[3]

    # Money and cost: exactly two decimals, quantities plain numbers.
    assert first[HEADERS.index("money_at_stake")] == "1312.79"
    assert first[HEADERS.index("unit_cost")] == "4.00"
    assert first[HEADERS.index("units_gap")] == "-52.9"
    assert first[HEADERS.index("current_reorder_point")] == "100.0"
    assert first[HEADERS.index("recommended_reorder_point_units")] == "153"
    assert first[HEADERS.index("safety_stock_units")] == "31"
    assert first[HEADERS.index("rank")] == "1"
    assert first[HEADERS.index("as_of")] == "2026-09-30"

    # Notes joined with "; " (and an empty note list is an empty cell).
    assert first[HEADERS.index("notes")] == "no reorder point set; cost unknown"
    assert second[HEADERS.index("notes")] == ""

    # None values are empty cells.
    for column in (
        "rank",
        "current_reorder_point",
        "units_gap",
        "unit_cost",
        "money_at_stake",
    ):
        assert second[HEADERS.index(column)] == ""

    # A formula-like item_code is quote-prefixed; numbers are not.
    assert third[HEADERS.index("item_code")] == "'=SUM(A1)"
    assert third[HEADERS.index("units_gap")] == "247.1"

    # Row order is preserved (input order, not any re-sort).
    assert [row[HEADERS.index("item_code")] for row in parsed[1:]] == [
        "ZZZ",
        "MMM",
        "'=SUM(A1)",
    ]
    assert len(parsed) == 4  # BOM-prefixed header + one row per input row

    # --- seed: three item-sites at SITE-64 -----------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            first_day = AS_OF - timedelta(days=359)
            lead_times = (
                ("2026-08-22", "2026-09-01"),
                ("2026-08-29", "2026-09-10"),
                ("2026-08-28", "2026-09-11"),
            )
            for item_code in ("A-64", "B-64", "C-64"):
                cur.executemany(
                    "INSERT INTO demand_events "
                    "(id, business_key, item_code, site_code, quantity, "
                    "occurred_on, event_type, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            f"id-de-64-{item_code}-{day_index}",
                            f"de-64-{item_code}-{day_index}",
                            item_code,
                            "SITE-64",
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
                            f"id-lt-64-{item_code}-{lead_index}",
                            f"lt-64-{item_code}-{lead_index}",
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
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "order_qty, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    ("id-is-64-a", "A-64", "SITE-64", 4.0, 400.0, 100.0, "test"),
                    ("id-is-64-b", "B-64", "SITE-64", 4.0, 100.0, 100.0, "test"),
                    ("id-is-64-c", "C-64", "SITE-64", 1.0, None, 100.0, "test"),
                ],
            )
        conn.commit()
    finally:
        conn.close()

    # --- API: the export matches the ranked list, no limit -------------------
    params = {"as_of": AS_OF.isoformat()}
    response = client.get("/api/drift", params={**params, "limit": 1000})
    assert response.status_code == 200, response.text
    listed = response.json()
    assert len(listed) == 3

    export = client.get("/api/drift/export.csv", params=params)
    assert export.status_code == 200, export.text
    assert export.headers["content-type"] == "text/csv; charset=utf-8"
    disposition = export.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "drift-2026-09-30.csv" in disposition

    csv_rows = _parse(export.text)
    assert csv_rows[0] == HEADERS
    assert len(csv_rows) - 1 == len(listed)
    assert [row[HEADERS.index("item_code")] for row in csv_rows[1:]] == [
        row["item_code"] for row in listed
    ]
    # The export honors the same filters as the list endpoint.
    export_under = client.get(
        "/api/drift/export.csv", params={**params, "direction": "under"}
    )
    assert export_under.status_code == 200, export_under.text
    csv_rows_under = _parse(export_under.text)
    assert [row[HEADERS.index("item_code")] for row in csv_rows_under[1:]] == [
        row["item_code"] for row in listed if row["direction"] == "under"
    ]

    # --- API: without as_of the default as-of date is used -------------------
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    monkeypatch.setattr("app.stats.demand.date", _FixedDate)
    export_default = client.get("/api/drift/export.csv")
    assert export_default.status_code == 200, export_default.text
    assert "drift-2026-09-30.csv" in export_default.headers["content-disposition"]
    assert len(_parse(export_default.text)) - 1 == len(listed)

    # --- API: rejected inputs are 422, writes are 405 ------------------------
    assert (
        client.get(
            "/api/drift/export.csv", params={**params, "direction": "sideways"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/drift/export.csv", params={**params, "category": "other"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/drift/export.csv", params={**params, "as_of": "not-a-date"}
        ).status_code
        == 422
    )
    assert client.post("/api/drift/export.csv").status_code == 405
