"""Issue #88 proof: read-only excess/dead stock API and CSV export.

Against the test database (every date a fixed past calendar date; the
as-of is 2026-06-30 and date.today is pinned to 2026-07-01 so the
as_of=None default can never resolve to today):
  * GET /api/excess returns the dead never_moved, dead stopped, excess
    and no-unit-cost rows ranked by money with the no-cost row last,
    every row carrying the full field set (dead_reason null for
    excess rows),
  * category=dead&dead_reason=stopped returns only the stopped rows,
  * limit=1&offset=1 returns the second-ranked row,
  * category=other (and other invalid filters) returns 422,
  * GET /api/excess/export.csv returns 200 with text/csv, a filename
    containing 2026-06-30, the exact header, the BOM, and as many data
    rows as the list returns with limit=1000,
  * an item code starting with "=" is written prefixed with a single
    quote,
  * both routes return 401 without a token,
  * POST /api/excess returns 405.
"""

import csv
import io
from datetime import date

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app as app_main

AS_OF = "2026-06-30"
FIXED_TODAY = date(2026, 7, 1)

HEADERS = [
    "rank",
    "category",
    "dead_reason",
    "item_code",
    "site_code",
    "on_hand_units",
    "justified_units",
    "excess_units",
    "months_of_cover",
    "unit_cost",
    "money_tied_up",
    "last_demand_on",
    "demand_window_units",
    "notes",
    "arithmetic",
    "as_of",
]

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


def test_issue88_freeform(client, monkeypatch):
    # --- seed: on-hand, item-site and demand rows, fixed past dates --------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # S-88: last demand 2024-01-15, outside the 12-month window ->
            # dead/stopped. X-88: 30 units on 2026-05-15, inside the window
            # -> excess. =EQ-88 and N-88 have no demand at all ->
            # dead/never_moved.
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-de-88-s",
                        "de-88-s",
                        "S-88",
                        "SITE-1",
                        20.0,
                        "2024-01-15",
                        "shipment",
                        "test",
                    ),
                    (
                        "id-de-88-x",
                        "de-88-x",
                        "X-88",
                        "SITE-1",
                        30.0,
                        "2026-05-15",
                        "shipment",
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
                    ("id-oh-88-eq", "=EQ-88", "SITE-1", 100.0, as_of_stamp, "test"),
                    ("id-oh-88-s", "S-88", "SITE-1", 50.0, as_of_stamp, "test"),
                    ("id-oh-88-x", "X-88", "SITE-1", 100.0, as_of_stamp, "test"),
                    ("id-oh-88-n", "N-88", "SITE-1", 40.0, as_of_stamp, "test"),
                ],
            )
            # N-88 intentionally has no item_sites row -> no unit cost.
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, source_system) "
                "VALUES (%s, %s, %s, %s, %s)",
                [
                    ("id-is-88-eq", "=EQ-88", "SITE-1", 10.0, "test"),
                    ("id-is-88-s", "S-88", "SITE-1", 3.0, "test"),
                    ("id-is-88-x", "X-88", "SITE-1", 2.0, "test"),
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
    monkeypatch.delenv("EXCESS_COVER_MONTHS", raising=False)

    # --- list: ranked by money, no-cost row last ----------------------------
    response = client.get("/api/excess", params={"as_of": AS_OF, "limit": 1000})
    assert response.status_code == 200, response.text
    listed = response.json()
    assert [row["item_code"] for row in listed] == ["=EQ-88", "S-88", "X-88", "N-88"]
    assert [row["rank"] for row in listed] == [1, 2, 3, 4]
    assert [row["money_tied_up"] for row in listed] == [1000.0, 150.0, 140.0, None]
    expected_fields = {
        "rank",
        "category",
        "dead_reason",
        "item_code",
        "site_code",
        "on_hand_units",
        "unit_cost",
        "last_demand_on",
        "demand_window_units",
        "months_of_cover",
        "justified_units",
        "excess_units",
        "money_tied_up",
        "as_of",
        "notes",
        "arithmetic",
        "item_description",
        "decision",
        "decision_lapsed",
    }
    assert all(set(row) == expected_fields for row in listed)
    assert all(row["as_of"] == AS_OF for row in listed)
    by_code = {row["item_code"]: row for row in listed}
    eq_row = by_code["=EQ-88"]
    assert eq_row["category"] == "dead"
    assert eq_row["dead_reason"] == "never_moved"
    assert eq_row["last_demand_on"] is None
    assert eq_row["excess_units"] == 100.0
    assert eq_row["money_tied_up"] == 1000.0
    stopped_row = by_code["S-88"]
    assert stopped_row["category"] == "dead"
    assert stopped_row["dead_reason"] == "stopped"
    assert stopped_row["last_demand_on"] == "2024-01-15"
    assert stopped_row["excess_units"] == 50.0
    excess_row = by_code["X-88"]
    assert excess_row["category"] == "excess"
    assert excess_row["dead_reason"] is None
    assert excess_row["demand_window_units"] == 30.0
    assert excess_row["justified_units"] == 30.0
    assert excess_row["excess_units"] == 70.0
    assert excess_row["months_of_cover"] == 40.0
    assert excess_row["money_tied_up"] == 140.0
    no_cost_row = by_code["N-88"]
    assert no_cost_row["category"] == "dead"
    assert no_cost_row["dead_reason"] == "never_moved"
    assert no_cost_row["unit_cost"] is None
    assert no_cost_row["money_tied_up"] is None
    assert "unit cost missing" in no_cost_row["notes"]

    # --- list: filters, paging and rejected values ---------------------------
    stopped = client.get(
        "/api/excess",
        params={"as_of": AS_OF, "category": "dead", "dead_reason": "stopped"},
    )
    assert stopped.status_code == 200, stopped.text
    stopped_rows = stopped.json()
    assert [row["item_code"] for row in stopped_rows] == ["S-88"]
    assert all(
        row["category"] == "dead" and row["dead_reason"] == "stopped"
        for row in stopped_rows
    )

    second = client.get("/api/excess", params={"as_of": AS_OF, "limit": 1, "offset": 1})
    assert second.status_code == 200, second.text
    second_rows = second.json()
    assert [row["item_code"] for row in second_rows] == ["S-88"]
    assert second_rows[0]["rank"] == 2

    assert (
        client.get(
            "/api/excess", params={"as_of": AS_OF, "category": "other"}
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/excess", params={"as_of": AS_OF, "dead_reason": "bogus"}
        ).status_code
        == 422
    )
    assert client.get("/api/excess", params={"as_of": "not-a-date"}).status_code == 422
    assert (
        client.get("/api/excess", params={"as_of": AS_OF, "limit": 0}).status_code
        == 422
    )
    assert (
        client.get("/api/excess", params={"as_of": AS_OF, "limit": 1001}).status_code
        == 422
    )
    assert (
        client.get("/api/excess", params={"as_of": AS_OF, "offset": -1}).status_code
        == 422
    )

    # --- export: BOM, header, filename, row parity, formula guard -------------
    export = client.get("/api/excess/export.csv", params={"as_of": AS_OF})
    assert export.status_code == 200, export.text
    assert export.headers["content-type"] == "text/csv; charset=utf-8"
    disposition = export.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "excess-2026-06-30.csv" in disposition

    csv_rows = _parse(export.text)
    assert csv_rows[0] == HEADERS
    assert len(csv_rows) - 1 == len(listed)
    header_index = {name: position for position, name in enumerate(HEADERS)}
    assert [row[header_index["item_code"]] for row in csv_rows[1:]] == [
        "'=EQ-88",
        "S-88",
        "X-88",
        "N-88",
    ]

    first = csv_rows[1]
    assert first[header_index["rank"]] == "1"
    assert first[header_index["category"]] == "dead"
    assert first[header_index["dead_reason"]] == "never_moved"
    assert first[header_index["site_code"]] == "SITE-1"
    assert first[header_index["on_hand_units"]] == "100.0"
    assert first[header_index["justified_units"]] == "0"
    assert first[header_index["excess_units"]] == "100.0"
    assert first[header_index["months_of_cover"]] == ""
    assert first[header_index["unit_cost"]] == "10.00"
    assert first[header_index["money_tied_up"]] == "1000.00"
    assert first[header_index["last_demand_on"]] == ""
    assert first[header_index["demand_window_units"]] == "0.0"
    assert first[header_index["as_of"]] == "2026-06-30"

    x_row = csv_rows[3]
    assert x_row[header_index["dead_reason"]] == ""
    assert x_row[header_index["justified_units"]] == "30"
    assert x_row[header_index["excess_units"]] == "70.0"
    assert x_row[header_index["months_of_cover"]] == "40.0"
    assert x_row[header_index["unit_cost"]] == "2.00"
    assert x_row[header_index["money_tied_up"]] == "140.00"
    assert x_row[header_index["last_demand_on"]] == "2026-05-15"
    assert x_row[header_index["demand_window_units"]] == "30.0"

    n_row = csv_rows[4]
    assert n_row[header_index["unit_cost"]] == ""
    assert n_row[header_index["money_tied_up"]] == ""
    assert n_row[header_index["notes"]] == "unit cost missing"

    # The export honors the same filters as the list endpoint.
    export_stopped = client.get(
        "/api/excess/export.csv",
        params={"as_of": AS_OF, "category": "dead", "dead_reason": "stopped"},
    )
    assert export_stopped.status_code == 200, export_stopped.text
    stopped_csv_rows = _parse(export_stopped.text)
    assert [row[header_index["item_code"]] for row in stopped_csv_rows[1:]] == ["S-88"]
    assert (
        client.get(
            "/api/excess/export.csv", params={"as_of": AS_OF, "category": "other"}
        ).status_code
        == 422
    )

    # --- hostile inputs never 500 ---------------------------------------------
    for params in (
        {"as_of": "2026-06-30\x00"},
        {"category": "dead\x00"},
        {"limit": "abc"},
        {"offset": "-"},
        {"as_of": "99999-01-01"},
    ):
        response = client.get("/api/excess", params={"as_of": AS_OF, **params})
        assert response.status_code == 422, (params, response.status_code)

    # --- auth: both routes 401 without a token ---------------------------------
    unauth = _unauth_client()
    for path in ("/api/excess", "/api/excess/export.csv"):
        response = unauth.get(path, params={"as_of": AS_OF})
        assert response.status_code == 401, path
        assert response.json()["detail"] == AUTH_DETAIL
        assert response.headers.get("WWW-Authenticate") == "Bearer"

    # --- writes are not allowed -------------------------------------------------
    assert client.post("/api/excess", params={"as_of": AS_OF}).status_code == 405
