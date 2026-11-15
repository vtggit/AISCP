"""Issue #652 proof: a number setting type and three numeric settings.

AC-1 — the entries, through the existing settings API:
  * GET /api/settings lists replenishment.service_level (default 0.95,
    min 0.5, max 0.9999), replenishment.order_cost (default 50.0, min
    0.01, max 100000) and replenishment.holding_rate (default 0.25,
    min 0.01, max 1) with type "number", their descriptions, allowed
    null and source "default"; the three existing entries have min and
    max null.
  * the writes: an admin PUT of replenishment.service_level 0.98 is
    stored and the entry then reads value 0.98 with source "system";
    0.4, 1.0, true and "0.9" each answer 422 with the message naming
    the bounds; a planner PUT answers 403.
  * the effect: with the recommendations monkeypatched to one
    recommendable item-site (I652, constant 2.0 a day for 360 days and
    lead times 8, 10 and 12 days, seeded the way
    test_issue360_freeform.py seeds X360), storing
    replenishment.service_level 0.98 changes the recommendation's
    inputs.service_level to 0.98, storing replenishment.order_cost 80
    makes order_quantity.order_cost 80, and after the DELETE resets the
    values return to the AIERP_ORDER_COST environment value (70) or
    the defaults (50.0 and 0.25).

AC-2 — the registration rules: a number setting without min, without
max, or with a default outside its bounds raises ValueError at
registration.
"""

import statistics
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection
from app.settings.registry import Setting, register

AS_OF = date(2026, 9, 30)
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
SITE = "SITE-1"
ITEM = "I652"

SERVICE_LEVEL_KEY = "replenishment.service_level"
ORDER_COST_KEY = "replenishment.order_cost"
HOLDING_RATE_KEY = "replenishment.holding_rate"

Z95 = statistics.NormalDist().inv_cdf(0.95)
Z98 = statistics.NormalDist().inv_cdf(0.98)

SERVICE_LEVEL_DETAIL = (
    "Setting 'replenishment.service_level' must be a number " "between 0.5 and 0.9999."
)


def _seed(item_code: str) -> None:
    """One recommendable item-site: I652, constant 2.0 a day for 360
    days (12 buckets of 60, "smooth") with three lead-time
    observations of 8, 10 and 12 days (LT 10, sd_LT 2) — the same seed
    test_issue360_freeform.py gives X360."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events (id, business_key, item_code, "
                "site_code, quantity, occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        f"id-de-652-{i}",
                        f"de-652-{i}",
                        item_code,
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
                        f"id-lt-652-{i}",
                        f"lt-652-{i}",
                        "SUP-652",
                        item_code,
                        ordered,
                        received,
                        "test",
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def test_issue652_freeform(client, monkeypatch, admin_headers, user_headers):
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    _seed(ITEM)

    # --- AC-1: the entries --------------------------------------------------
    response = client.get("/api/settings")
    assert response.status_code == 200, response.text
    entries = {e["key"]: e for e in response.json()}
    new_entries = {
        SERVICE_LEVEL_KEY: (
            0.95,
            0.5,
            0.9999,
            "The cycle service level reorder points are computed at.",
        ),
        ORDER_COST_KEY: (
            50.0,
            0.01,
            100000,
            "The cost of placing one purchase order, used for the "
            "recommended order quantity.",
        ),
        HOLDING_RATE_KEY: (
            0.25,
            0.01,
            1,
            "The yearly cost of holding stock, as a fraction of its unit cost.",
        ),
    }
    for key, (default, low, high, description) in new_entries.items():
        entry = entries[key]
        assert entry["scope"] == "system"
        assert entry["type"] == "number"
        assert entry["default"] == default
        assert entry["min"] == low
        assert entry["max"] == high
        assert entry["allowed"] is None
        assert entry["description"] == description
        assert entry["value"] == default
        assert entry["source"] == "default"
    for key in (
        "decisions.require_action_choice",
        "queue.default_filter",
        "replenishment.demand_basis",
    ):
        assert entries[key]["min"] is None
        assert entries[key]["max"] is None

    # --- AC-1: the writes ----------------------------------------------------
    response = client.put(
        f"/api/settings/{SERVICE_LEVEL_KEY}",
        json={"value": 0.98},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["value"] == 0.98
    assert response.json()["source"] == "system"
    entries = {e["key"]: e for e in client.get("/api/settings").json()}
    assert entries[SERVICE_LEVEL_KEY]["value"] == 0.98
    assert entries[SERVICE_LEVEL_KEY]["source"] == "system"

    # Below the min, above the max, a boolean and a string each answer
    # 422 with the message naming the bounds.
    for bad in (0.4, 1.0, True, "0.9"):
        response = client.put(
            f"/api/settings/{SERVICE_LEVEL_KEY}",
            json={"value": bad},
            headers=admin_headers,
        )
        assert response.status_code == 422, (bad, response.text)
        assert response.json() == {"detail": SERVICE_LEVEL_DETAIL}

    # A planner (the non-admin token) cannot change a system setting.
    response = client.put(
        f"/api/settings/{SERVICE_LEVEL_KEY}",
        json={"value": 0.9},
        headers=user_headers,
    )
    assert response.status_code == 403, response.text

    # --- AC-1: the effect, one recommendable item-site -----------------------
    def _recommendation() -> dict:
        response = client.get(
            "/api/recommendations",
            params={"item_code": ITEM, "as_of": AS_OF.isoformat()},
        )
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 1, rows
        return rows[0]

    # Nothing stored: the defaults apply (z 1.644854 at service level
    # 0.95, order cost 50.0, holding rate 0.25).
    response = client.delete(
        f"/api/settings/{SERVICE_LEVEL_KEY}", headers=admin_headers
    )
    assert response.status_code == 200, response.text
    row = _recommendation()
    assert row["status"] == "ok"
    assert row["demand_basis"] == "history"
    assert row["inputs"]["service_level"] == 0.95
    assert row["inputs"]["z"] == pytest.approx(Z95)
    assert row["safety_stock"] == pytest.approx(Z95 * 4.0)
    assert row["reorder_point"] == pytest.approx(20.0 + Z95 * 4.0)
    assert row["order_quantity"]["order_cost"] == 50.0
    assert row["order_quantity"]["holding_rate"] == 0.25

    # Storing the service level changes the recommendation's service
    # level (and its z, safety stock and reorder point).
    response = client.put(
        f"/api/settings/{SERVICE_LEVEL_KEY}",
        json={"value": 0.98},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    row = _recommendation()
    assert row["inputs"]["service_level"] == 0.98
    assert row["inputs"]["z"] == pytest.approx(Z98)
    assert row["safety_stock"] == pytest.approx(Z98 * 4.0)
    assert row["reorder_point"] == pytest.approx(20.0 + Z98 * 4.0)

    # Storing the order cost changes order_quantity's order cost.
    response = client.put(
        f"/api/settings/{ORDER_COST_KEY}",
        json={"value": 80},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    row = _recommendation()
    assert row["order_quantity"]["order_cost"] == 80

    # Storing the holding rate changes order_quantity's holding rate.
    response = client.put(
        f"/api/settings/{HOLDING_RATE_KEY}",
        json={"value": 0.3},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    row = _recommendation()
    assert row["order_quantity"]["holding_rate"] == 0.3

    # The resets: with nothing stored the values return to the
    # environment value, then to the defaults.
    for key in (SERVICE_LEVEL_KEY, ORDER_COST_KEY, HOLDING_RATE_KEY):
        response = client.delete(f"/api/settings/{key}", headers=admin_headers)
        assert response.status_code == 200, response.text
        assert response.json()["source"] == "default"

    monkeypatch.setenv("AIERP_ORDER_COST", "70")
    row = _recommendation()
    assert row["order_quantity"]["order_cost"] == 70
    assert row["order_quantity"]["holding_rate"] == 0.25
    assert row["inputs"]["service_level"] == 0.95

    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    row = _recommendation()
    assert row["order_quantity"]["order_cost"] == 50.0
    assert row["order_quantity"]["holding_rate"] == 0.25
    assert row["inputs"]["service_level"] == 0.95

    # --- AC-1: hostile values never 500 --------------------------------------
    for bad in (None, {}, 1e300, "0.9" + "\x00", -5):
        response = client.put(
            f"/api/settings/{SERVICE_LEVEL_KEY}",
            json={"value": bad},
            headers=admin_headers,
        )
        assert response.status_code == 422, (bad, response.text)
        assert response.json() == {"detail": SERVICE_LEVEL_DETAIL}

    # --- AC-2: the registration rules -----------------------------------------
    with pytest.raises(ValueError):
        register(
            Setting(
                key="test.issue652.no_min",
                scope="system",
                type="number",
                default=0.5,
                description="a number setting without a min",
                max=1.0,
            )
        )
    with pytest.raises(ValueError):
        register(
            Setting(
                key="test.issue652.no_max",
                scope="system",
                type="number",
                default=0.5,
                description="a number setting without a max",
                min=0.0,
            )
        )
    with pytest.raises(ValueError):
        register(
            Setting(
                key="test.issue652.above_max",
                scope="system",
                type="number",
                default=1.5,
                description="a number setting above its max",
                min=0.0,
                max=1.0,
            )
        )
    with pytest.raises(ValueError):
        register(
            Setting(
                key="test.issue652.below_min",
                scope="system",
                type="number",
                default=-0.5,
                description="a number setting below its min",
                min=0.0,
                max=1.0,
            )
        )
