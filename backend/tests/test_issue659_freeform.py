"""Issue #659 proof: a service level per ABC class.

AC-1 — the entries, through the existing settings API:
  * GET /api/settings lists the four new entries with their types,
    defaults and bounds: replenishment.service_level_a (default 0.98,
    min 0.5, max 0.9999), replenishment.service_level_b (default 0.95),
    replenishment.service_level_c (default 0.90), all number, and
    replenishment.service_level_by_class (boolean, default false); the
    planner (the non-admin token) sees them and a planner PUT answers
    403.

AC-1/AC-2 — the effect, with the recommendation inputs seeded the way
test_issue652_freeform.py seeds I652 (constant 2.0 a day for 360 days,
lead times 8, 10 and 12 days), four item-sites:
  * switch off: every recommendation's inputs.service_level is 0.95
    and its service_level_source is {"abc_class": null, "source":
    "single"};
  * switch on, after an admin PUT replenishment.service_level_by_class
    true: the three valued item-sites class A (730000), B (109500) and
    C (36500) read 0.98, 0.95 and 0.90 with service_level_source
    class A, B and C and source "class" (the single-key call classes
    the same population); the A item's reorder point is higher than
    with the switch off and the C item's lower;
  * with replenishment.service_level_c stored 0.85 the C item reads
    0.85 and the others are unchanged;
  * the item-site with no unit cost stays unclassified: the single
    service level, abc_class null, source "single".
"""

import statistics
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection

AS_OF = date(2026, 9, 30)
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
SITE = "SITE-1"

BY_CLASS_KEY = "replenishment.service_level_by_class"
LEVEL_A_KEY = "replenishment.service_level_a"
LEVEL_B_KEY = "replenishment.service_level_b"
LEVEL_C_KEY = "replenishment.service_level_c"

Z90 = statistics.NormalDist().inv_cdf(0.90)
Z85 = statistics.NormalDist().inv_cdf(0.85)
Z95 = statistics.NormalDist().inv_cdf(0.95)
Z98 = statistics.NormalDist().inv_cdf(0.98)

# The four item-sites: constant 2.0 a day for 360 days (d 2.0, sd_d 0)
# with lead times 8, 10 and 12 (LT 10, sd_LT 2, three observations —
# "ok", not low confidence).  The annual value is 365 * 2.0 * unit_cost
# — 730000, 109500 and 36500 class A, B and C (shares before 0.0,
# 0.8333 and 0.9583); I659-D has no unit cost and stays unclassified.
ITEM_A = "I659-A"
ITEM_B = "I659-B"
ITEM_C = "I659-C"
ITEM_D = "I659-D"
UNIT_COSTS = {ITEM_A: 1000.0, ITEM_B: 150.0, ITEM_C: 50.0, ITEM_D: None}


def _seed() -> None:
    """One recommendable item-site per item, the way
    test_issue652_freeform.py seeds I652 — plus the item_sites row with
    the item's unit_cost (null for I659-D)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item, unit_cost in UNIT_COSTS.items():
                suffix = item[-1]
                cur.executemany(
                    "INSERT INTO demand_events (id, business_key, item_code, "
                    "site_code, quantity, occurred_on, event_type, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            f"id-de-659-{suffix}-{i}",
                            f"de-659-{suffix}-{i}",
                            item,
                            SITE,
                            2.0,
                            (FIRST_DAY + timedelta(days=i)).isoformat(),
                            "shipment",
                            "test",
                        )
                        for i in range(360)
                    ],
                )
                for i, (ordered, received) in enumerate(
                    (
                        ("2026-08-22", "2026-08-30"),
                        ("2026-08-20", "2026-08-30"),
                        ("2026-08-18", "2026-08-30"),
                    )
                ):
                    cur.execute(
                        "INSERT INTO lead_time_observations (id, business_key, "
                        "supplier_code, item_code, ordered_on, received_on, "
                        "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (
                            f"id-lt-659-{suffix}-{i}",
                            f"lt-659-{suffix}-{i}",
                            f"SUP-659-{suffix}",
                            item,
                            ordered,
                            received,
                            "test",
                        ),
                    )
                cur.execute(
                    "INSERT INTO item_sites (id, item_code, site_code, "
                    "unit_cost, reorder_point, safety_stock, source_system) "
                    "VALUES (%s, %s, %s, %s, NULL, NULL, 'test')",
                    (f"id-is-659-{suffix}", item, SITE, unit_cost),
                )
        conn.commit()
    finally:
        conn.close()


def test_issue659_freeform(client, monkeypatch, admin_headers, user_headers):
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    _seed()

    def _rows() -> dict[str, dict]:
        response = client.get(
            "/api/recommendations",
            params={"as_of": AS_OF.isoformat()},
        )
        assert response.status_code == 200, response.text
        rows = response.json()
        assert [row["item_code"] for row in rows] == [
            ITEM_A,
            ITEM_B,
            ITEM_C,
            ITEM_D,
        ]
        return {row["item_code"]: row for row in rows}

    # --- AC-1: the four new entries -------------------------------------
    response = client.get("/api/settings")
    assert response.status_code == 200, response.text
    entries = {entry["key"]: entry for entry in response.json()}
    new_entries = {
        LEVEL_A_KEY: (
            "number",
            0.98,
            0.5,
            0.9999,
            "The service level for class A item-sites (the items that "
            "make up the first 80% of annual consumption value).",
        ),
        LEVEL_B_KEY: (
            "number",
            0.95,
            0.5,
            0.9999,
            "The service level for class B item-sites.",
        ),
        BY_CLASS_KEY: (
            "boolean",
            False,
            None,
            None,
            "Use a service level per ABC class instead of the single " "service level.",
        ),
        LEVEL_C_KEY: (
            "number",
            0.90,
            0.5,
            0.9999,
            "The service level for class C item-sites.",
        ),
    }
    for key, (type_, default, low, high, description) in new_entries.items():
        entry = entries[key]
        assert entry["scope"] == "system"
        assert entry["type"] == type_
        assert entry["default"] == default
        assert entry["min"] == low
        assert entry["max"] == high
        assert entry["allowed"] is None
        assert entry["description"] == description
        assert entry["value"] == default
        assert entry["source"] == "default"

    # The planner sees the entries and cannot write a system setting.
    response = client.get("/api/settings", headers=user_headers)
    assert response.status_code == 200, response.text
    planner_entries = {entry["key"]: entry for entry in response.json()}
    assert planner_entries[BY_CLASS_KEY]["editable"] is False
    response = client.put(
        f"/api/settings/{BY_CLASS_KEY}", json={"value": True}, headers=user_headers
    )
    assert response.status_code == 403, response.text

    # --- AC-2: the switch off (the default) ------------------------------
    rows = _rows()
    for item, abc_class in (
        (ITEM_A, "A"),
        (ITEM_B, "B"),
        (ITEM_C, "C"),
        (ITEM_D, None),
    ):
        row = rows[item]
        assert row["status"] == "ok", row["item_code"]
        assert row["inputs"]["service_level"] == 0.95
        assert row["inputs"]["z"] == pytest.approx(Z95)
        assert row["service_level_source"] == {
            "abc_class": abc_class,
            "source": "single",
        }
    reorder_a_off = rows[ITEM_A]["reorder_point"]
    reorder_c_off = rows[ITEM_C]["reorder_point"]

    # --- AC-2: the switch on ----------------------------------------------
    response = client.put(
        f"/api/settings/{BY_CLASS_KEY}", json={"value": True}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["value"] is True
    assert response.json()["source"] == "system"

    rows = _rows()
    expected = {
        ITEM_A: (0.98, Z98, "A", "class"),
        ITEM_B: (0.95, Z95, "B", "class"),
        ITEM_C: (0.90, Z90, "C", "class"),
        ITEM_D: (0.95, Z95, None, "single"),
    }
    for item, (level, z, abc_class, source) in expected.items():
        row = rows[item]
        assert row["inputs"]["service_level"] == level, row["item_code"]
        assert row["inputs"]["z"] == pytest.approx(z)
        assert row["service_level_source"] == {
            "abc_class": abc_class,
            "source": source,
        }
    assert rows[ITEM_A]["reorder_point"] == pytest.approx(20.0 + Z98 * 4.0)
    assert rows[ITEM_C]["reorder_point"] == pytest.approx(20.0 + Z90 * 4.0)
    assert rows[ITEM_A]["reorder_point"] > reorder_a_off
    assert rows[ITEM_C]["reorder_point"] < reorder_c_off

    # The single-key call classes the same population.
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": ITEM_A, "site_code": SITE, "as_of": AS_OF.isoformat()},
    )
    assert response.status_code == 200, response.text
    by_key = response.json()
    assert by_key["inputs"]["service_level"] == 0.98
    assert by_key["inputs"]["z"] == pytest.approx(Z98)
    assert by_key["service_level_source"] == {"abc_class": "A", "source": "class"}

    # --- AC-2: the class level changed --------------------------------------
    response = client.put(
        f"/api/settings/{LEVEL_C_KEY}", json={"value": 0.85}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    rows = _rows()
    assert rows[ITEM_C]["inputs"]["service_level"] == 0.85
    assert rows[ITEM_C]["inputs"]["z"] == pytest.approx(Z85)
    assert rows[ITEM_C]["reorder_point"] == pytest.approx(20.0 + Z85 * 4.0)
    assert rows[ITEM_C]["service_level_source"] == {
        "abc_class": "C",
        "source": "class",
    }
    # The other classes and the unclassified item-site are untouched.
    assert rows[ITEM_A]["inputs"]["service_level"] == 0.98
    assert rows[ITEM_B]["inputs"]["service_level"] == 0.95
    assert rows[ITEM_D]["inputs"]["service_level"] == 0.95
    assert rows[ITEM_D]["service_level_source"] == {
        "abc_class": None,
        "source": "single",
    }
