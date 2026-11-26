"""Issue #1634 proof: the two dollar figures of the simulation summary.

Against the test database (as-of 2026-09-30, date.today pinned to
2026-10-02, service level 0.95 from the default), with two item-sites
seeded the way ``tests/test_issue970_freeform.py`` seeds K970 — 2.0 a
day for 360 days (smooth), lead times 8, 10 and 12 days (mean 10,
sample std 2) — whose baseline at the 0.95 service level is safety
stock ``Z95 * 4`` = 6.579415 (7 whole units) and reorder point
``2 * 10 + Z95 * 4`` = 26.579415 (27 whole units):

* A1634-1 @ SITE — the same data, unit cost 1.0 in ``item_sites``;
* A1634-2 @ SITE — the same data, NO unit cost in ``item_sites``.

Every expected number below is written out as arithmetic on the
seeded inputs, never computed by calling the code under test.

* ``demand_multiplier`` 1.5: d 3.0, sd_d 0.0, so the scenario safety
  stock ``Z95 * 6`` (10 whole units, up 3) and the reorder point
  ``3 * 10 + Z95 * 6`` = 39.869122 (40, up 13): ``dollar_impact``
  3 * 1.0 = 3.0 (safety stock 7 -> 10) and ``pipeline_dollar_impact``
  (3 * 10 - 2 * 10) * 1.0 = 10.0 (2 x 10 -> 3 x 10);
* ``lead_time_multiplier`` 2.0: lt 20, sd_lt 4, so the scenario safety
  stock ``Z95 * 8`` = 13.158829 (14 whole units, up 7) and the reorder
  point ``2 * 20 + Z95 * 8`` = 53.158829 (54, up 27): ``dollar_impact``
  7 * 1.0 = 7.0 (7 -> 14) and ``pipeline_dollar_impact``
  (2 * 20 - 2 * 10) * 1.0 = 20.0 (2 x 10 -> 2 x 20);
* ``service_level`` 0.99: the scenario safety stock ``Z99 * 4`` =
  9.305391 (10 whole units, up 3) and the reorder point
  ``2 * 10 + Z99 * 4`` = 29.305391 (30, up 3): ``dollar_impact``
  3 * 1.0 = 3.0 (7 -> 10) and ``pipeline_dollar_impact`` exactly 0.0
  — the scenario changed only the service level.

In each case ``dollar_impact`` + ``pipeline_dollar_impact`` equals the
sum of ``reorder_point_units_delta`` times the unit cost (13.0, 27.0
and 3.0), the item-site without a unit cost adds nothing and is
counted in ``item_sites_without_unit_cost``,
``POST /api/scenarios/simulate`` answers the same two figures, and
``backend/openapi.json`` lists ``pipeline_dollar_impact`` in
``SimulateSummary``.
"""

import json
import math
import statistics
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.db.connection import get_connection
from app.scenario.simulate import simulate_reorder

AS_OF = date(2026, 9, 30)
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
SITE = "SITE-1634"
I1 = "A1634-1"  # unit cost 1.0
I2 = "A1634-2"  # no unit cost

SERVICE_LEVEL = 0.95
Z95 = statistics.NormalDist().inv_cdf(SERVICE_LEVEL)  # 1.644854
Z99 = statistics.NormalDist().inv_cdf(0.99)  # 2.326348

# The seeded lead times of 8, 10 and 12 days: mean 10, sample std 2.
LT_MEAN = 10.0
LT_SD = 2.0
DAILY = 2.0
UNIT_COST = 1.0

# The baseline's formula values at the seeded inputs (d 2.0, sd_d 0.0,
# LT 10, sd_LT 2, z of the 0.95 service level): with sd_d 0.0 the
# safety-stock sigma is d * sd_LT, so the safety stock is Z95 * 4 and
# the reorder point 2 * 10 + Z95 * 4.
SS_SIGMA = DAILY * LT_SD  # 4.0
BASELINE_SS_UNITS = math.ceil(round(Z95 * SS_SIGMA, 9))  # 7
BASELINE_RP_UNITS = math.ceil(round(DAILY * LT_MEAN + Z95 * SS_SIGMA, 9))  # 27

# Per scenario: the levers, the expected scenario whole units (both
# item-sites share the seeded data) and the two expected dollar
# figures at the one unit cost.
SCENARIOS = (
    {
        # d 3.0, sd_d 0.0: ss Z95 * 6, rp 3 * 10 + Z95 * 6.
        "levers": {"demand_multiplier": 1.5},
        "ss_units": math.ceil(round(Z95 * 6.0, 9)),  # 10
        "rp_units": math.ceil(round(1.5 * DAILY * LT_MEAN + Z95 * 6.0, 9)),  # 40
        "dollar": (10 - BASELINE_SS_UNITS) * UNIT_COST,  # 3.0
        "pipeline": (1.5 * DAILY * LT_MEAN - DAILY * LT_MEAN) * UNIT_COST,  # 10.0
    },
    {
        # lt 20, sd_lt 4: ss Z95 * 8, rp 2 * 20 + Z95 * 8.
        "levers": {"lead_time_multiplier": 2.0},
        "ss_units": math.ceil(round(Z95 * 8.0, 9)),  # 14
        "rp_units": math.ceil(round(DAILY * 2.0 * LT_MEAN + Z95 * 8.0, 9)),  # 54
        "dollar": (14 - BASELINE_SS_UNITS) * UNIT_COST,  # 7.0
        "pipeline": (DAILY * 2.0 * LT_MEAN - DAILY * LT_MEAN) * UNIT_COST,  # 20.0
    },
    {
        # z of 0.99: ss Z99 * 4, rp 2 * 10 + Z99 * 4.
        "levers": {"service_level": 0.99},
        "ss_units": math.ceil(round(Z99 * 4.0, 9)),  # 10
        "rp_units": math.ceil(round(DAILY * LT_MEAN + Z99 * 4.0, 9)),  # 30
        "dollar": (10 - BASELINE_SS_UNITS) * UNIT_COST,  # 3.0
        "pipeline": 0.0,  # exactly 0: only the service level changed
    },
)


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


def _demand(cur, item):
    """2.0 units a day for 360 days, ending on the as-of."""
    cur.executemany(
        "INSERT INTO demand_events (id, business_key, item_code, site_code, "
        "quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                f"id-de-1634-{item}-{i}",
                f"de-1634-{item}-{i}",
                item,
                SITE,
                DAILY,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(360)
        ],
    )


def _lead_times(cur, item):
    """Three lead-time observations of 8, 10 and 12 days: mean 10,
    sample std 2, not low confidence."""
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
                f"id-lt-1634-{item}-{i}",
                f"lt-1634-{item}-{i}",
                f"SUP-1634-{item}",
                item,
                ordered,
                received,
                "test",
            ),
        )


def _item_site(cur, item, unit_cost):
    """The item-site's row, with or without a unit cost."""
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'test')",
        (f"id-is-1634-{item}", item, SITE, unit_cost, 25.0, 10.0),
    )


def test_issue1634_freeform(client, monkeypatch):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    # --- seed: the K970 item-site, once with and once without a cost --
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand(cur, I1)
            _lead_times(cur, I1)
            _item_site(cur, I1, UNIT_COST)
            _demand(cur, I2)
            _lead_times(cur, I2)
            _item_site(cur, I2, None)
        conn.commit()
    finally:
        conn.close()

    for scenario in SCENARIOS:
        # --- the direct function. -------------------------------------
        result = simulate_reorder(as_of=AS_OF, **scenario["levers"])
        rows = result["item_sites"]
        assert [(row["item_code"], row["site_code"]) for row in rows] == [
            (I1, SITE),
            (I2, SITE),
        ]
        # Both item-sites share the seeded data, so both rows carry
        # the same baseline, the same scenario and the same deltas.
        for row in rows:
            assert row["baseline"]["safety_stock_units"] == BASELINE_SS_UNITS
            assert row["baseline"]["reorder_point_units"] == BASELINE_RP_UNITS
            assert row["scenario"]["safety_stock_units"] == scenario["ss_units"]
            assert row["scenario"]["reorder_point_units"] == scenario["rp_units"]
            assert (
                row["safety_stock_units_delta"]
                == scenario["ss_units"] - BASELINE_SS_UNITS
            )
            assert (
                row["reorder_point_units_delta"]
                == scenario["rp_units"] - BASELINE_RP_UNITS
            )

        summary = result["summary"]
        # The change in the stock held, at the one unit cost.
        assert summary["dollar_impact"] == pytest.approx(scenario["dollar"])
        if scenario["pipeline"] == 0.0:
            # A service-level-only scenario: exactly 0, no slack.
            assert summary["pipeline_dollar_impact"] == 0.0
        else:
            # The change in demand over the lead time, unrounded.
            assert summary["pipeline_dollar_impact"] == pytest.approx(
                scenario["pipeline"]
            )
        # The two figures add up to the reorder-point delta's value:
        # the one unit cost times A1634-1's delta, A1634-2 (no unit
        # cost) adding 0.
        assert summary["dollar_impact"] + summary[
            "pipeline_dollar_impact"
        ] == pytest.approx(rows[0]["reorder_point_units_delta"] * UNIT_COST)
        assert summary["item_sites_without_unit_cost"] == 1

        # --- the API answers the same two figures. --------------------
        response = client.post("/api/scenarios/simulate", json=scenario["levers"])
        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload == result
        assert payload["summary"]["dollar_impact"] == pytest.approx(scenario["dollar"])
        assert payload["summary"]["pipeline_dollar_impact"] == (
            0.0 if scenario["pipeline"] == 0.0 else pytest.approx(scenario["pipeline"])
        )

    # --- the OpenAPI document lists the new summary field. ------------
    spec = json.loads(
        (Path(__file__).resolve().parents[1] / "openapi.json").read_text(
            encoding="utf-8"
        )
    )
    summary_schema = spec["components"]["schemas"]["SimulateSummary"]
    assert summary_schema["properties"]["pipeline_dollar_impact"] == {
        "type": "number",
        "title": "Pipeline Dollar Impact",
    }
    assert "pipeline_dollar_impact" in summary_schema["required"]
    properties = list(summary_schema["properties"])
    assert (
        properties.index("pipeline_dollar_impact")
        == properties.index("dollar_impact") + 1
    )
