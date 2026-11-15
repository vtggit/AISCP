"""Issue #672 proof: the ABC class is always computed and reported.

The same inputs as test_issue659_freeform.py — constant 2.0 a day for
360 days, lead times 8, 10 and 12 days — three recommendable item-sites
classed A (730000), B (109500) and C (36500) and one with no unit
cost, plus 135 filler item-sites (unit cost 0.05, annual value 36.5
each, all below the class-C cut) so the population is QA-sized: 139
item-sites.

AC-1 — switch off: the classified item-sites' service_level_source is
{"abc_class": <its class>, "source": "single"} — A, B and C for the
three valued item-sites — and the unclassified one's is
{"abc_class": null, "source": "single"}; all four read
inputs.service_level 0.95 and every other figure (their reorder point
included) equals a run with the class computation patched out.
With the switch off, recommendations_for_all on the 139 item-sites
takes no more than 25% longer than a run with the class computation
patched out — the proof times both on the same data (mean of five
runs each, after two warm-up runs).

AC-2 — switch on: unchanged — the classified three read source "class"
with their class levels (0.98, 0.95 and 0.90) and the unclassified one
keeps the single level with null and "single".  Through the API,
GET /api/recommendations/by-key and GET /api/drift carry the same
service_level_source values in both switch states (the four item-sites
A, B, C and D in both switch states).

Hostile inputs never raise or 500: a negative lead time (pooled or
master) is unusable — the formula would take the square root of a
negative number — so the master lead time stands in when it is
usable, else the status is "insufficient_lead_time"; and a demand so
large the formula overflows (its exact results are not finite floats)
is "not_recommendable_overflow" with nulls, through both the pure
function and the API.
"""

import statistics
import time
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection
from app.recommend import reorder

AS_OF = date(2026, 9, 30)
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
SITE = "SITE-1"

BY_CLASS_KEY = "replenishment.service_level_by_class"

Z90 = statistics.NormalDist().inv_cdf(0.90)
Z95 = statistics.NormalDist().inv_cdf(0.95)
Z98 = statistics.NormalDist().inv_cdf(0.98)

# The four special item-sites of test_issue659_freeform.py (d 2.0,
# sd_d 0, LT 10, sd_LT 2 — "ok"): the annual value is 365 * 2.0 *
# unit_cost — 730000, 109500 and 36500 class A, B and C; I672-D has no
# unit cost and stays unclassified.
ITEM_A = "I672-A"
ITEM_B = "I672-B"
ITEM_C = "I672-C"
ITEM_D = "I672-D"
UNIT_COSTS = {ITEM_A: 1000.0, ITEM_B: 150.0, ITEM_C: 50.0, ITEM_D: None}
# 135 filler item-sites: the QA-sized population (139 item-sites),
# each 36.5 a year — below the class-C cut of the three valued
# item-sites, so their classes stay A, B and C.
FILLER_ITEMS = [f"I672-F{i:03d}" for i in range(1, 136)]
FILLER_UNIT_COST = 0.05
# A fifth, hostile item-site seeded only for the hostile-input proof
# (after the performance measurement, so the population stays 139
# item-sites there): 360 days of 1e306 each — the mean daily demand
# overflows float and the safety-stock formula overflows with it.
ITEM_E = "I672-E"
HUGE_QUANTITY = 1e306


def _seed() -> None:
    """One recommendable item-site per item, the way
    test_issue659_freeform.py seeds its four item-sites — plus the
    135 filler item-sites that make the population QA-sized."""
    items = {**UNIT_COSTS, **dict.fromkeys(FILLER_ITEMS, FILLER_UNIT_COST)}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item, unit_cost in items.items():
                cur.executemany(
                    "INSERT INTO demand_events (id, business_key, item_code, "
                    "site_code, quantity, occurred_on, event_type, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            f"id-de-672-{item}-{i}",
                            f"de-672-{item}-{i}",
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
                            f"id-lt-672-{item}-{i}",
                            f"lt-672-{item}-{i}",
                            f"SUP-672-{item}",
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
                    (f"id-is-672-{item}", item, SITE, unit_cost),
                )
        conn.commit()
    finally:
        conn.close()


def test_issue672_freeform(client, monkeypatch, admin_headers):
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    _seed()

    def _rows() -> dict[str, dict]:
        response = client.get(
            "/api/recommendations",
            params={"as_of": AS_OF.isoformat(), "limit": 1000},
        )
        assert response.status_code == 200, response.text
        rows = response.json()
        assert len(rows) == 139, len(rows)
        return {row["item_code"]: row for row in rows}

    # The expected (abc_class, source) of the four special item-sites
    # in each switch state; the levels are 0.98/0.95/0.90/0.95 with
    # the switch on, 0.95 for all four with it off.
    sources_off = {
        ITEM_A: ("A", "single"),
        ITEM_B: ("B", "single"),
        ITEM_C: ("C", "single"),
        ITEM_D: (None, "single"),
    }
    sources_on = {
        ITEM_A: ("A", "class"),
        ITEM_B: ("B", "class"),
        ITEM_C: ("C", "class"),
        ITEM_D: (None, "single"),
    }
    levels_on = {ITEM_A: 0.98, ITEM_B: 0.95, ITEM_C: 0.90, ITEM_D: 0.95}
    z_on = {ITEM_A: Z98, ITEM_B: Z95, ITEM_C: Z90, ITEM_D: Z95}

    def _check(rows, sources, levels):
        for item, (abc_class, source) in sources.items():
            row = rows[item]
            assert row["status"] == "ok", row["item_code"]
            assert row["inputs"]["service_level"] == levels[item]
            assert row["service_level_source"] == {
                "abc_class": abc_class,
                "source": source,
            }

    # --- AC-1: the switch off (the default) ----------------------------
    rows_off = _rows()
    _check(rows_off, sources_off, {item: 0.95 for item in sources_off})
    for item in sources_off:
        assert rows_off[item]["inputs"]["z"] == pytest.approx(Z95)

    # The switch-off figures: a run with the class computation patched
    # out (the pre-#672 behavior) gives the same numbers on every
    # field but the reported class.
    off_rows = {row["item_code"]: row for row in reorder.recommendations_for_all(AS_OF)}
    original_classify = reorder._abc_class_by_key
    monkeypatch.setattr(reorder, "_abc_class_by_key", lambda *args, **kwargs: {})
    patched_rows = {
        row["item_code"]: row for row in reorder.recommendations_for_all(AS_OF)
    }
    for item in sources_off:
        unpatched = {
            key: value
            for key, value in off_rows[item].items()
            if key != "service_level_source"
        }
        patched = {
            key: value
            for key, value in patched_rows[item].items()
            if key != "service_level_source"
        }
        assert patched == unpatched, item
        assert off_rows[item]["reorder_point"] == patched_rows[item]["reorder_point"]

    # --- AC-1: the performance, with the switch off ---------------------
    def _timed_runs(count: int) -> list[float]:
        durations = []
        for _ in range(count):
            started = time.perf_counter()
            reorder.recommendations_for_all(AS_OF)
            durations.append(time.perf_counter() - started)
        return durations

    _timed_runs(2)  # warm up: connections and plans
    baseline = _timed_runs(5)  # the class computation patched out
    monkeypatch.setattr(reorder, "_abc_class_by_key", original_classify)
    actual = _timed_runs(5)  # the class computation in place
    mean_baseline = sum(baseline) / len(baseline)
    mean_actual = sum(actual) / len(actual)
    assert mean_actual <= 1.25 * mean_baseline, (baseline, actual)

    # --- AC-2: the switch on ---------------------------------------------
    response = client.put(
        f"/api/settings/{BY_CLASS_KEY}", json={"value": True}, headers=admin_headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["value"] is True

    rows_on = _rows()
    _check(rows_on, sources_on, levels_on)
    for item, z in z_on.items():
        assert rows_on[item]["inputs"]["z"] == pytest.approx(z)
    assert rows_on[ITEM_A]["reorder_point"] == pytest.approx(20.0 + Z98 * 4.0)
    assert rows_on[ITEM_C]["reorder_point"] == pytest.approx(20.0 + Z90 * 4.0)

    # --- AC-2: through the API, the switch on -----------------------------
    for item, (abc_class, source) in sources_on.items():
        response = client.get(
            "/api/recommendations/by-key",
            params={"item_code": item, "site_code": SITE, "as_of": AS_OF.isoformat()},
        )
        assert response.status_code == 200, response.text
        by_key = response.json()
        assert by_key["service_level_source"] == {
            "abc_class": abc_class,
            "source": source,
        }
        assert by_key["inputs"]["service_level"] == levels_on[item]

    response = client.get(
        "/api/drift", params={"as_of": AS_OF.isoformat(), "limit": 1000}
    )
    assert response.status_code == 200, response.text
    drift_by_item = {row["item_code"]: row for row in response.json()}
    for item, (abc_class, source) in sources_on.items():
        assert drift_by_item[item]["service_level_source"] == {
            "abc_class": abc_class,
            "source": source,
        }
        assert drift_by_item[item]["service_level"] == levels_on[item]

    # --- AC-2: through the API, the switch off ----------------------------
    response = client.put(
        f"/api/settings/{BY_CLASS_KEY}",
        json={"value": False},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    for item in (ITEM_A, ITEM_B, ITEM_C, ITEM_D):
        abc_class, source = sources_off[item]
        response = client.get(
            "/api/recommendations/by-key",
            params={"item_code": item, "site_code": SITE, "as_of": AS_OF.isoformat()},
        )
        assert response.status_code == 200, response.text
        assert response.json()["service_level_source"] == {
            "abc_class": abc_class,
            "source": source,
        }

    response = client.get(
        "/api/drift", params={"as_of": AS_OF.isoformat(), "limit": 1000}
    )
    assert response.status_code == 200, response.text
    drift_by_item = {row["item_code"]: row for row in response.json()}
    for item in (ITEM_A, ITEM_B, ITEM_C, ITEM_D):
        abc_class, source = sources_off[item]
        assert drift_by_item[item]["service_level_source"] == {
            "abc_class": abc_class,
            "source": source,
        }
        assert drift_by_item[item]["service_level"] == 0.95

    # --- hostile inputs: a negative lead time never raises -------------
    demand_ok = {
        "mean_daily": 2.0,
        "std_daily": 1.0,
        "demand_class": "smooth",
        "recommendable": True,
    }
    pooled_lt = {
        "mean_days": 10.0,
        "std_days": 2.0,
        "n_used": 5,
        "low_confidence": False,
    }
    negative_pooled = {**pooled_lt, "mean_days": -8.0}
    # Without a usable master lead time the status is the same as an
    # absent lead time.
    result = reorder.recommend(demand_ok, negative_pooled)
    assert result["status"] == "insufficient_lead_time"
    # With a usable master lead time it stands in (LT 10, sd_LT 0).
    result = reorder.recommend(demand_ok, negative_pooled, master_lead_time_days=10.0)
    assert result["status"] == "ok", result
    assert result["inputs"]["lt"] == 10.0
    assert result["inputs"]["sd_lt"] == 0.0
    assert "master lead time used" in result["notes"]
    # A negative master lead time is unusable too.
    result = reorder.recommend(demand_ok, None, master_lead_time_days=-8.0)
    assert result["status"] == "insufficient_lead_time"

    # --- hostile inputs: an overflowing demand never 500s ---------------
    huge = {**demand_ok, "mean_daily": 1e306}
    result = reorder.recommend(huge, pooled_lt)
    assert result["status"] == "not_recommendable_overflow", result
    assert result["safety_stock"] is None
    assert result["reorder_point"] is None
    result = reorder.recommend({**demand_ok, "mean_daily": float("inf")}, pooled_lt)
    assert result["status"] == "not_recommendable_overflow"
    # ...and through the API: 360 days of 1e306 overflow the formula;
    # the list and the single-key paths answer 200, never 500.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events (id, business_key, item_code, "
                "site_code, quantity, occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        f"id-de-672e-{i}",
                        f"de-672e-{i}",
                        ITEM_E,
                        SITE,
                        HUGE_QUANTITY,
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
                        f"id-lt-672e-{i}",
                        f"lt-672e-{i}",
                        "SUP-672E",
                        ITEM_E,
                        ordered,
                        received,
                        "test",
                    ),
                )
            cur.execute(
                "INSERT INTO item_sites (id, item_code, site_code, "
                "unit_cost, reorder_point, safety_stock, source_system) "
                "VALUES (%s, %s, %s, %s, NULL, NULL, 'test')",
                ("id-is-672e", ITEM_E, SITE, 1.0),
            )
        conn.commit()
    finally:
        conn.close()
    response = client.get(
        "/api/recommendations", params={"as_of": AS_OF.isoformat(), "limit": 1000}
    )
    assert response.status_code == 200, response.text
    rows_by_item = {row["item_code"]: row for row in response.json()}
    assert rows_by_item[ITEM_E]["status"] == "not_recommendable_overflow"
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": ITEM_E, "site_code": SITE, "as_of": AS_OF.isoformat()},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "not_recommendable_overflow"
