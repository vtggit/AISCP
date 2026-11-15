"""Issue #124 proof: the reorder-point changes feed between two as-of dates.

Against the test database (every date a fixed past calendar date, the
as-of 2026-06-30, since defaulting to 2026-05-31):
  * P-124 gets 360 days of steady 10/day demand (d 10, sd_d 0) plus one
    extra 1000-unit event on 2026-06-15 (inside the last 30 days) and
    three pooled lead time observations (8, 10, 12 -> lt 10, sd_lt 2),
    so its since stats stay d 10 / sd_d 0 while its as-of stats move,
  * Q-124 has unchanged history and never appears,
  * GET /api/recommendation-changes returns the changed row with rank 1,
    its inputs, its Shapley contributions (summing to the reorder point
    difference) and its primary driver,
  * the primary_driver filter keeps the row for its driver and drops it
    for any other,
  * a since on or after as_of is a 400 with the ValueError message,
  * the CSV export carries the BOM, the exact header and one data row
    with the four contributions to two decimals, named
    recommendation-changes-2026-06-30.csv,
  * both routes answer 401 without a bearer token.
"""

import csv
import io
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

AS_OF = date(2026, 6, 30)
SINCE = AS_OF - timedelta(days=30)  # 2026-05-31
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-07-06, window start
DRIVERS = (
    "demand_level",
    "demand_variability",
    "lead_time",
    "lead_time_variability",
)

CSV_HEADER = [
    "rank",
    "item_code",
    "site_code",
    "status_then",
    "status_now",
    "reorder_point_units_then",
    "reorder_point_units_now",
    "change_units",
    "primary_driver",
    "demand_level",
    "demand_variability",
    "lead_time",
    "lead_time_variability",
    "d_then",
    "d_now",
    "lt_then",
    "lt_now",
    "unit_cost",
    "money",
    "since",
    "as_of",
    "notes",
]


def _daily_demand(item_code: str) -> list[tuple]:
    """360 daily demand events of 10 units, FIRST_DAY through AS_OF."""
    return [
        (
            f"id-de-124-{item_code}-{day_index}",
            f"de-124-{item_code}-{day_index}",
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
            f"id-lt-124-{item_code}{n}",
            f"lt-124-{item_code}{n}",
            f"SUP-{item_code}",
            item_code,
            f"2026-{month:02d}-0{1 + 2 * (n - 1)}",
            f"2026-{month:02d}-0{1 + 2 * (n - 1) + days}",
            "test",
        )
        for n, days in enumerate((8, 10, 12), start=1)
    ]


def _csv_lines(raw: str) -> list[list[str]]:
    """The CSV rows of the response, BOM stripped."""
    assert raw.startswith("\ufeff")
    return list(csv.reader(io.StringIO(raw[1:])))


def test_issue124_freeform(client):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [*_daily_demand("P-124"), *_daily_demand("Q-124")]
                + [
                    (
                        "id-de-124-spike",
                        "de-124-spike",
                        "P-124",
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
                _lead_time_observations("P-124", 2)
                + _lead_time_observations("Q-124", 3),
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, "
                "master_lead_time_days, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    ("id-is-124-p", "P-124", "SITE-1", 2.0, None, "test"),
                    ("id-is-124-q", "Q-124", "SITE-1", 1.5, None, "test"),
                ],
            )
        conn.commit()
    finally:
        conn.close()

    # --- since on or after as_of -> 400 with the ValueError message ----------
    for since in ("2026-06-30", "2026-07-01"):
        response = client.get(
            "/api/recommendation-changes",
            params={"as_of": AS_OF.isoformat(), "since": since},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == (
            f"since ({since}) must be earlier than as_of (2026-06-30)"
        )

    # --- the list: the changed row with rank 1 and its contributions ---------
    response = client.get(
        "/api/recommendation-changes", params={"as_of": AS_OF.isoformat()}
    )
    assert response.status_code == 200
    body = response.json()
    assert [(row["item_code"], row["site_code"]) for row in body] == [
        ("P-124", "SITE-1")
    ]
    row = body[0]
    assert row["rank"] == 1
    assert row["as_of"] == "2026-06-30"
    assert row["since"] == "2026-05-31"
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
    assert row["reorder_point_units_then"] == 133
    # as-of stats: bucket 0 is 1300, the other 11 buckets 300.
    assert row["inputs_now"]["lt"] == 10.0
    assert row["inputs_now"]["sd_lt"] == 2.0
    assert row["inputs_now"]["d"] == pytest.approx(4600 / 360, abs=1e-9)
    assert row["inputs_now"]["sd_d"] > 0
    assert row["reorder_point_units_now"] == 406
    assert row["change_units"] == 273
    assert row["unit_cost"] == 2.0
    assert row["money"] == 546.0
    contributions = row["contributions"]
    assert set(contributions) == set(DRIVERS)
    assert sum(contributions.values()) == pytest.approx(
        row["reorder_point_now"] - row["reorder_point_then"], abs=1e-9
    )
    expected_driver = max(contributions, key=lambda name: abs(contributions[name]))
    assert row["primary_driver"] == expected_driver

    # --- the primary_driver filter keeps or drops the row ---------------------
    response = client.get(
        "/api/recommendation-changes",
        params={"as_of": AS_OF.isoformat(), "primary_driver": expected_driver},
    )
    assert response.status_code == 200
    kept = response.json()
    assert len(kept) == 1
    assert kept[0]["rank"] == 1
    assert kept[0]["item_code"] == "P-124"
    for other in DRIVERS:
        if other == expected_driver:
            continue
        response = client.get(
            "/api/recommendation-changes",
            params={"as_of": AS_OF.isoformat(), "primary_driver": other},
        )
        assert response.status_code == 200
        assert response.json() == []

    # --- paging: rank is assigned after filtering, before paging --------------
    response = client.get(
        "/api/recommendation-changes",
        params={"as_of": AS_OF.isoformat(), "limit": 1, "offset": 0},
    )
    assert response.status_code == 200
    assert [r["rank"] for r in response.json()] == [1]
    response = client.get(
        "/api/recommendation-changes",
        params={"as_of": AS_OF.isoformat(), "limit": 1, "offset": 1},
    )
    assert response.status_code == 200
    assert response.json() == []
    for params in (
        {"as_of": AS_OF.isoformat(), "limit": 0},
        {"as_of": AS_OF.isoformat(), "limit": 1001},
        {"as_of": AS_OF.isoformat(), "offset": -1},
        {"as_of": AS_OF.isoformat(), "primary_driver": "bogus"},
        {"since": "not-a-date"},
        {"as_of": AS_OF.isoformat(), "since": "2026-06-30" + chr(0)},
    ):
        assert (
            client.get("/api/recommendation-changes", params=params).status_code == 422
        )

    # --- the CSV export ---------------------------------------------------------
    response = client.get(
        "/api/recommendation-changes/export.csv", params={"as_of": AS_OF.isoformat()}
    )
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/csv; charset=utf-8"
    assert response.headers["content-disposition"] == (
        'attachment; filename="recommendation-changes-2026-06-30.csv"'
    )
    lines = _csv_lines(response.content.decode("utf-8"))
    assert lines[0] == CSV_HEADER
    assert len(lines) == 2
    data = dict(zip(lines[0], lines[1]))
    assert data["rank"] == "1"
    assert data["item_code"] == "P-124"
    assert data["site_code"] == "SITE-1"
    assert data["status_then"] == "ok"
    assert data["status_now"] == "ok"
    assert data["reorder_point_units_then"] == "133"
    assert data["reorder_point_units_now"] == "406"
    assert data["change_units"] == "273"
    assert data["primary_driver"] == expected_driver
    for name in DRIVERS:
        assert data[name] == f"{contributions[name]:.2f}"
    assert data["d_then"] == "10.0"
    assert data["lt_then"] == "10.0"
    assert data["lt_now"] == "10.0"
    assert data["d_now"] == str(float(row["inputs_now"]["d"]))
    assert data["unit_cost"] == "2.00"
    assert data["money"] == "546.00"
    assert data["since"] == "2026-05-31"
    assert data["as_of"] == "2026-06-30"
    assert data["notes"] == ""

    # --- the CSV honours the same filters as the list --------------------------
    response = client.get(
        "/api/recommendation-changes/export.csv",
        params={"as_of": AS_OF.isoformat(), "primary_driver": expected_driver},
    )
    assert response.status_code == 200
    assert len(_csv_lines(response.content.decode("utf-8"))) == 2
    for other in DRIVERS:
        if other == expected_driver:
            continue
        response = client.get(
            "/api/recommendation-changes/export.csv",
            params={"as_of": AS_OF.isoformat(), "primary_driver": other},
        )
        assert response.status_code == 200
        assert _csv_lines(response.content.decode("utf-8")) == [CSV_HEADER]
    response = client.get(
        "/api/recommendation-changes/export.csv",
        params={"as_of": AS_OF.isoformat(), "since": AS_OF.isoformat()},
    )
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "since (2026-06-30) must be earlier than as_of (2026-06-30)"
    )

    # --- both routes 401 without a bearer token ---------------------------------
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/recommendation-changes").status_code == 401
        assert (
            anonymous.get("/api/recommendation-changes/export.csv").status_code == 401
        )
