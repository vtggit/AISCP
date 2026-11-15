"""Issue #99 proof: read-only stockout risk API and CSV export.

Against the test database (every date a fixed past calendar date; the
as-of is 2026-06-30 and date.today is pinned to 2026-07-01 so the
as_of=None default can never resolve to today):
  * three item-sites on SITE-1, each with 360 days of steady demand
    (2 units/day, smooth) and three pooled lead time observations
    (8, 10, 12 days -> mean 10, sample std 2), so every recommendation
    is "ok" with d 2, sd_d 0, lt 10, sd_lt 2 (mu 20, sigma 4) and a
    reorder point of 27 units:
      = "=L-99" (8 on hand, cost 2.50) -> p_stockout 0.9987, "likely",
      * "N-99" (22 on hand, no item_sites row) -> p_stockout 0.3085,
        "at_risk" with no unit cost and money_at_risk null,
      * "A-99" (24 on hand, cost 5.00) -> p_stockout 0.1587, "at_risk",
  * GET /api/stockout returns the three rows in rank order with every
    field slice 1 declares,
  * category=likely returns only the likely row,
  * limit=1&offset=1 returns the second-ranked row,
  * category=other (and other invalid filters) returns 422,
  * GET /api/stockout/export.csv returns 200 with text/csv, a filename
    containing 2026-06-30, the exact header, the BOM, p_stockout with
    4 decimals and as many data rows as the list returns with
    limit=1000,
  * no open_purchase_lines rows are seeded, so open_supply_units is 0
    and open_supply_due_by null in the API rows and empty/0.0 cells in
    the CSV (both columns immediately after on_hand_units),
  * the item code starting with "=" is written prefixed with a single
    quote,
  * both routes return 401 without a token,
  * POST /api/stockout returns 405.
"""

import csv
import io
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app as app_main
from app.recommend.stockout import stockout_ranked

AS_OF = date(2026, 6, 30)
AS_OF_ISO = "2026-06-30"
FIRST_DAY = date(2025, 7, 6)  # 360 days before AS_OF
FIXED_TODAY = date(2026, 7, 1)
AS_OF_STAMP = "2026-06-30 00:00:00+00"

HEADERS = [
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

LISTED_FIELDS = {
    "rank",
    "category",
    "item_code",
    "site_code",
    "on_hand_units",
    "open_supply_units",
    "open_supply_due_by",
    "unit_cost",
    "lead_time_days",
    "lead_time_demand",
    "p_stockout",
    "days_of_cover",
    "projected_stockout_on",
    "recommended_reorder_point_units",
    "expected_units_short",
    "money_at_risk",
    "as_of",
    "notes",
    "arithmetic",
    "item_description",
    "decision",
    "decision_lapsed",
}

AUTH_DETAIL = "Authentication required. Provide a valid Bearer token."


def _parse(text: str) -> list[list[str]]:
    """Assert the UTF-8 BOM and parse the CSV text into rows."""
    assert text.startswith("\ufeff")
    body = text.encode("utf-8")
    assert body.startswith(b"\xef\xbb\xbf")
    return list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))


def _unauth_client() -> TestClient:
    """A TestClient with no credentials, aimed at the served app."""
    return TestClient(app_main)


def _demand_rows(item_code: str) -> list[tuple]:
    """360 daily demand events of 2 units, FIRST_DAY through AS_OF."""
    return [
        (
            f"id-de-99-{item_code}-{day_index}",
            f"de-99-{item_code}-{day_index}",
            item_code,
            "SITE-1",
            2.0,
            (FIRST_DAY + timedelta(days=day_index)).isoformat(),
            "shipment",
            "test",
        )
        for day_index in range(360)
    ]


def test_issue99_freeform(client, monkeypatch):
    # --- seed: steady demand, pooled lead times, on hand, costs ------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item_code in ("=L-99", "N-99", "A-99"):
                cur.executemany(
                    "INSERT INTO demand_events "
                    "(id, business_key, item_code, site_code, quantity, "
                    "occurred_on, event_type, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    _demand_rows(item_code),
                )
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    # =L-99: 8, 10, 12 days -> mean 10, sample std 2.
                    (
                        "id-lt-99-l1",
                        "lt-99-l1",
                        "SUP-L",
                        "=L-99",
                        "2026-05-01",
                        "2026-05-09",
                        "test",
                    ),
                    (
                        "id-lt-99-l2",
                        "lt-99-l2",
                        "SUP-L",
                        "=L-99",
                        "2026-05-05",
                        "2026-05-15",
                        "test",
                    ),
                    (
                        "id-lt-99-l3",
                        "lt-99-l3",
                        "SUP-L",
                        "=L-99",
                        "2026-05-08",
                        "2026-05-20",
                        "test",
                    ),
                    # N-99: the same 8, 10, 12 pattern.
                    (
                        "id-lt-99-n1",
                        "lt-99-n1",
                        "SUP-N",
                        "N-99",
                        "2026-04-01",
                        "2026-04-09",
                        "test",
                    ),
                    (
                        "id-lt-99-n2",
                        "lt-99-n2",
                        "SUP-N",
                        "N-99",
                        "2026-04-05",
                        "2026-04-15",
                        "test",
                    ),
                    (
                        "id-lt-99-n3",
                        "lt-99-n3",
                        "SUP-N",
                        "N-99",
                        "2026-04-08",
                        "2026-04-20",
                        "test",
                    ),
                    # A-99: the same 8, 10, 12 pattern.
                    (
                        "id-lt-99-a1",
                        "lt-99-a1",
                        "SUP-A",
                        "A-99",
                        "2026-03-01",
                        "2026-03-09",
                        "test",
                    ),
                    (
                        "id-lt-99-a2",
                        "lt-99-a2",
                        "SUP-A",
                        "A-99",
                        "2026-03-05",
                        "2026-03-15",
                        "test",
                    ),
                    (
                        "id-lt-99-a3",
                        "lt-99-a3",
                        "SUP-A",
                        "A-99",
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
                    ("id-oh-99-l", "=L-99", "SITE-1", 8.0, AS_OF_STAMP, "test"),
                    ("id-oh-99-n", "N-99", "SITE-1", 22.0, AS_OF_STAMP, "test"),
                    ("id-oh-99-a", "A-99", "SITE-1", 24.0, AS_OF_STAMP, "test"),
                ],
            )
            # N-99 intentionally has no item_sites row -> no unit cost.
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, master_lead_time_days, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    ("id-is-99-l", "=L-99", "SITE-1", 2.5, None, "test"),
                    ("id-is-99-a", "A-99", "SITE-1", 5.0, None, "test"),
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

    # --- stockout_ranked: a likely, an at-risk and a no-unit-cost row ------
    ranked = stockout_ranked(AS_OF)
    assert [row["item_code"] for row in ranked] == ["=L-99", "N-99", "A-99"]
    assert [row["category"] for row in ranked] == ["likely", "at_risk", "at_risk"]
    assert [row["unit_cost"] for row in ranked] == [2.5, None, 5.0]
    assert [row["money_at_risk"] for row in ranked] == [30.0, None, 1.65]
    assert all(row["as_of"] == AS_OF for row in ranked)
    assert all(
        "no open purchase orders due in the lead time" in row["notes"] for row in ranked
    )
    no_cost = ranked[1]
    assert no_cost["money_at_risk"] is None
    assert "unit cost missing" in no_cost["notes"]

    # --- list: rank order and the full field set ----------------------------
    response = client.get("/api/stockout", params={"as_of": AS_OF_ISO, "limit": 1000})
    assert response.status_code == 200, response.text
    listed = response.json()
    assert [row["item_code"] for row in listed] == ["=L-99", "N-99", "A-99"]
    assert [row["rank"] for row in listed] == [1, 2, 3]
    assert [row["p_stockout"] for row in listed] == [0.9987, 0.3085, 0.1587]
    assert [row["category"] for row in listed] == ["likely", "at_risk", "at_risk"]
    assert all(set(row) == LISTED_FIELDS for row in listed)
    assert all(row["site_code"] == "SITE-1" for row in listed)
    assert all(row["as_of"] == AS_OF_ISO for row in listed)
    by_code = {row["item_code"]: row for row in listed}
    likely_row = by_code["=L-99"]
    assert likely_row["on_hand_units"] == 8.0
    assert likely_row["open_supply_units"] == 0.0
    assert likely_row["open_supply_due_by"] is None
    assert likely_row["unit_cost"] == 2.5
    assert likely_row["lead_time_days"] == 10.0
    assert likely_row["lead_time_demand"] == 20.0
    assert likely_row["days_of_cover"] == 4.0
    assert likely_row["projected_stockout_on"] == "2026-07-04"
    assert likely_row["recommended_reorder_point_units"] == 27
    assert likely_row["expected_units_short"] == 12.0
    assert likely_row["money_at_risk"] == 30.0
    assert "mu = d * lt = 2 * 10 = 20" in likely_row["arithmetic"]
    n_row = by_code["N-99"]
    assert n_row["unit_cost"] is None
    assert n_row["money_at_risk"] is None
    assert n_row["days_of_cover"] == 11.0
    assert n_row["projected_stockout_on"] == "2026-07-11"
    assert n_row["expected_units_short"] == 0.79
    assert n_row["notes"] == [
        "no open purchase orders due in the lead time",
        "unit cost missing",
    ]
    a_row = by_code["A-99"]
    assert a_row["money_at_risk"] == 1.65
    assert a_row["projected_stockout_on"] == "2026-07-12"
    assert a_row["expected_units_short"] == 0.33

    # --- list: filters and paging -------------------------------------------
    likely = client.get(
        "/api/stockout", params={"as_of": AS_OF_ISO, "category": "likely"}
    )
    assert likely.status_code == 200, likely.text
    likely_rows = likely.json()
    assert [row["item_code"] for row in likely_rows] == ["=L-99"]
    assert all(row["category"] == "likely" for row in likely_rows)

    second = client.get(
        "/api/stockout", params={"as_of": AS_OF_ISO, "limit": 1, "offset": 1}
    )
    assert second.status_code == 200, second.text
    second_rows = second.json()
    assert [row["item_code"] for row in second_rows] == ["N-99"]
    assert second_rows[0]["rank"] == 2
    assert second_rows[0]["category"] == "at_risk"

    assert (
        client.get(
            "/api/stockout", params={"as_of": AS_OF_ISO, "category": "other"}
        ).status_code
        == 422
    )
    assert (
        client.get("/api/stockout", params={"as_of": "not-a-date"}).status_code == 422
    )
    assert (
        client.get("/api/stockout", params={"as_of": AS_OF_ISO, "limit": 0}).status_code
        == 422
    )
    assert (
        client.get(
            "/api/stockout", params={"as_of": AS_OF_ISO, "limit": 1001}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/stockout", params={"as_of": AS_OF_ISO, "offset": -1}
        ).status_code
        == 422
    )

    # --- export: BOM, header, filename, row parity, formula guard -----------
    export = client.get("/api/stockout/export.csv", params={"as_of": AS_OF_ISO})
    assert export.status_code == 200, export.text
    assert export.headers["content-type"] == "text/csv; charset=utf-8"
    disposition = export.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "2026-06-30" in disposition
    assert "stockout-2026-06-30.csv" in disposition

    csv_rows = _parse(export.text)
    assert csv_rows[0] == HEADERS
    assert len(csv_rows) - 1 == len(listed)
    header_index = {name: position for position, name in enumerate(HEADERS)}
    # The "=" item code is written prefixed with a single quote.
    assert [row[header_index["item_code"]] for row in csv_rows[1:]] == [
        "'=L-99",
        "N-99",
        "A-99",
    ]
    # p_stockout with exactly 4 decimals.
    assert [row[header_index["p_stockout"]] for row in csv_rows[1:]] == [
        "0.9987",
        "0.3085",
        "0.1587",
    ]

    first = csv_rows[1]
    assert first[header_index["rank"]] == "1"
    assert first[header_index["category"]] == "likely"
    assert first[header_index["site_code"]] == "SITE-1"
    assert first[header_index["projected_stockout_on"]] == "2026-07-04"
    assert first[header_index["days_of_cover"]] == "4.0"
    assert first[header_index["on_hand_units"]] == "8.0"
    assert first[header_index["open_supply_units"]] == "0.0"
    assert first[header_index["open_supply_due_by"]] == ""
    assert first[header_index["lead_time_days"]] == "10.0"
    assert first[header_index["lead_time_demand"]] == "20.0"
    assert first[header_index["recommended_reorder_point_units"]] == "27"
    assert first[header_index["expected_units_short"]] == "12.0"
    assert first[header_index["unit_cost"]] == "2.50"
    assert first[header_index["money_at_risk"]] == "30.00"
    assert (
        first[header_index["notes"]] == "no open purchase orders due in the lead time"
    )
    assert first[header_index["arithmetic"]].startswith("mu = d * lt = 2 * 10 = 20")
    assert first[header_index["as_of"]] == AS_OF_ISO

    n_csv = csv_rows[2]
    assert n_csv[header_index["unit_cost"]] == ""
    assert n_csv[header_index["money_at_risk"]] == ""
    assert n_csv[header_index["open_supply_units"]] == "0.0"
    assert n_csv[header_index["notes"]] == (
        "no open purchase orders due in the lead time; unit cost missing"
    )

    a_csv = csv_rows[3]
    assert a_csv[header_index["unit_cost"]] == "5.00"
    assert a_csv[header_index["money_at_risk"]] == "1.65"
    assert a_csv[header_index["expected_units_short"]] == "0.33"

    # The export honors the same category filter as the list endpoint.
    export_likely = client.get(
        "/api/stockout/export.csv", params={"as_of": AS_OF_ISO, "category": "likely"}
    )
    assert export_likely.status_code == 200, export_likely.text
    likely_csv_rows = _parse(export_likely.text)
    assert [row[header_index["item_code"]] for row in likely_csv_rows[1:]] == ["'=L-99"]
    assert (
        client.get(
            "/api/stockout/export.csv",
            params={"as_of": AS_OF_ISO, "category": "other"},
        ).status_code
        == 422
    )

    # --- hostile inputs never 500 --------------------------------------------
    for params in (
        {"as_of": "2026-06-30\x00"},
        {"category": "likely\x00"},
        {"limit": "abc"},
        {"offset": "-"},
        {"as_of": "99999-01-01"},
    ):
        response = client.get("/api/stockout", params={"as_of": AS_OF_ISO, **params})
        assert response.status_code == 422, (params, response.status_code)
    # The export validates the same category and as_of filters (it has no
    # limit/offset to reject).
    for params in (
        {"as_of": "2026-06-30\x00"},
        {"category": "likely\x00"},
        {"as_of": "99999-01-01"},
    ):
        export_response = client.get(
            "/api/stockout/export.csv", params={"as_of": AS_OF_ISO, **params}
        )
        assert export_response.status_code == 422, (params, export_response.status_code)

    # --- auth: both routes 401 without a token --------------------------------
    unauth = _unauth_client()
    for path in ("/api/stockout", "/api/stockout/export.csv"):
        response = unauth.get(path, params={"as_of": AS_OF_ISO})
        assert response.status_code == 401, path
        assert response.json()["detail"] == AUTH_DETAIL
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    # --- writes are not allowed ------------------------------------------------
    assert client.post("/api/stockout", params={"as_of": AS_OF_ISO}).status_code == 405
