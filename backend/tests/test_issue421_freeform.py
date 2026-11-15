"""Issue #421 proof: the scenario planning (what-if) simulation, backend.

AC-1 — ``app.scenario.simulate.simulate_reorder(service_level=None,
demand_multiplier=None, lead_time_multiplier=None, as_of=None)``: the
baseline of every item-site is its current recommendation from
``app.recommend.reorder.recommendations_for_all(as_of)``; only
``status == "ok"`` results are simulated (the same recommendable set).
The scenario scales the baseline's ``inputs`` — ``d' = d *
demand_multiplier``, ``sd_d' = sd_d * demand_multiplier``, ``lt' = lt *
lead_time_multiplier``, ``sd_lt' = sd_lt * lead_time_multiplier`` (a
multiplier left None is 1.0) — with the scenario service level ``sl'``
(the given ``service_level``, else the baseline's), ``z' =
NormalDist().inv_cdf(sl')`` and the same
``app.recommend.reorder._basis_result`` (same whole-unit ceiling
rounding). Each item-site row carries ``item_code``, ``site_code``,
``baseline`` and ``scenario`` (each with ``reorder_point``,
``reorder_point_units``, ``safety_stock``, ``safety_stock_units``) plus
the signed ``reorder_point_units_delta`` /
``safety_stock_units_delta`` (scenario minus baseline), ordered by
``item_code`` then ``site_code``. The ``summary`` carries ``item_sites``
(count simulated), the two delta totals, ``reorder_point_up`` /
``reorder_point_down`` / ``reorder_point_unchanged`` (counts by the sign
of ``reorder_point_units_delta``), ``dollar_impact`` (sum of
``reorder_point_units_delta`` times the item-site's ``unit_cost`` from
``item_sites``, no ``unit_cost`` contributing 0) and
``item_sites_without_unit_cost`` (count), and the ``levers`` echoed
back, each null when not given. A given ``service_level`` must be a
number strictly between 0 and 1, a given multiplier a positive finite
number — ``ValueError`` before any computation; with no lever the
scenario equals the baseline and every delta is 0. Read-only.

AC-2 — ``POST /api/scenarios/simulate`` in ``app.api.scenarios``,
mounted in ``app.main`` behind the same bearer gate as the other
planner routes; body ``{service_level?: float, demand_multiplier?:
float, lead_time_multiplier?: float}``; returns ``{summary,
item_sites}``; 422 for an out-of-range lever; the OpenAPI document
(``backend/openapi.json``) describes the route and its request and
response models.

AC-3 — this proof runs against the test database (as-of 2026-09-30,
date.today pinned to 2026-10-02, service level 0.95 from the default,
z 1.644854; z at 0.99 is 2.326348) with demand events and lead-time
observations inserted for the test:

* A421-1 @ S1 — 2.0 units a day for 360 days (12 buckets of 60), lead
  times 8, 10, 12 days (mean 10, sd 2, 3 observations), 5.0/unit:
  d 2.0, sd_d 0.0, LT 10, sd_LT 2, so safety stock z * 4 and reorder
  point 20 + z * 4 — baseline 6.579415 / 26.579415 (7 / 27 whole
  units);
* A421-2 @ S1 — the same lead times at 1.0 a day (12 buckets of 30),
  NO unit cost in item_sites: d 1.0, safety stock z * 2, reorder point
  10 + z * 2 — baseline 3.289707 / 13.289707 (4 / 14);
* A421-3 @ S2 — 0.5 a day (12 buckets of 15), the same lead times,
  2.0/unit: d 0.5, safety stock z, reorder point 5 + z — baseline
  1.644854 / 6.644854 (2 / 7);
* A421-4 @ S2 — one demand event 15 months before the as-of: every
  bucket in the window is zero -> no_demand ->
  ``not_recommendable_demand`` (NOT simulated);
* A421-5 @ S2 — 1.0 a day but no lead-time observations and no master
  lead time: ``insufficient_lead_time`` (NOT simulated).

The checks: with no levers every item-site's scenario equals its
baseline and all deltas are 0; at service level 0.99 (above the 0.95
baseline) z rises, so the safety stock and the reorder point rise for
every item-site (I1 10/30, I2 5/15, I3 3/8 whole units), and I1's
scenario safety stock 4 * z(0.99) = 9.305391 matches
``_basis_result(2.0, 0.0, 10.0, 2.0, z(0.99), 0.99)``; at demand
multiplier 1.5, d and sd_d scale and I1's reorder point rises by the
hand-computed 40 - 27 = 13 units (scenario 6 * z(0.95) + 30 =
39.869122 -> 40); at lead-time multiplier 2.0, lt and sd_lt scale
likewise (I1: 8 * z(0.95) + 40 = 53.158829 -> 54, +27 units). The
summary totals equal the sums of the per-item-site unit deltas (5/5,
22/5 and 47/12), the up/down/unchanged counts partition the item-sites
(0/0/3, 3/0/0, 3/0/0, 3/0/0), and dollar_impact equals the sum of
reorder_point_units_delta * unit_cost (17.0, 71.0 and 149.0), with
A421-2 (no unit cost) adding 0 and counted in
item_sites_without_unit_cost. The API returns the same structure for a
body with levers, 422 for a service_level of 1.5, a demand_multiplier
of 0 or -1 (and other out-of-range levers, including a hostile 1e300
that overflows the formula), and 401 without a token. Nothing is
written: every table of the test database — in particular the stored
recommendation-adjacent tables, item_sites and settings — is unchanged
after the simulate calls.

Identity (CodeAgent#750): the criteria concern login (bearer gate), so
this proof also runs as a NON-ADMIN (role "user"): this issue adds no
page and no admin-only control (the route is open to any
authenticated user, exactly like the other planner routes), so the
second pass re-runs the simulation with the non-admin development
token (``dev-secret-token:user``, roles ["user"]) and asserts it gets
the same 200 response with the same structure.
"""

import json
import math
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg2.sql
import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.recommend.reorder import _basis_result
from app.scenario.simulate import simulate_reorder

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
SITE = "S1"
SITE_B = "S2"
I1, I2, I3, I4, I5 = "A421-1", "A421-2", "A421-3", "A421-4", "A421-5"
COST_1, COST_3 = 5.0, 2.0  # A421-2 has no unit cost
Z95 = statistics.NormalDist().inv_cdf(0.95)  # 1.644854
Z99 = statistics.NormalDist().inv_cdf(0.99)  # 2.326348

# Hand-computed basis values (safety stock z * d * sd_LT, reorder point
# d * LT + safety stock, with d the mean daily demand, sd_d 0 for every
# constant item, LT 10 and sd_LT 2 for I1-I3).
SS_B1, RP_B1 = 4.0 * Z95, 20.0 + 4.0 * Z95  # I1 baseline: 6.579415 / 26.579415
SS_B2, RP_B2 = 2.0 * Z95, 10.0 + 2.0 * Z95  # I2 baseline: 3.289707 / 13.289707
SS_B3, RP_B3 = 1.0 * Z95, 5.0 + 1.0 * Z95  # I3 baseline: 1.644854 / 6.644854
# service level 0.99 (z 2.326348): the same d * sd_LT scaled by z.
SS_991, RP_991 = 4.0 * Z99, 20.0 + 4.0 * Z99  # I1: 9.305391 / 29.305391
SS_992, RP_992 = 2.0 * Z99, 10.0 + 2.0 * Z99  # I2: 4.652696 / 14.652696
SS_993, RP_993 = 1.0 * Z99, 5.0 + 1.0 * Z99  # I3: 2.326348 / 7.326348
# demand multiplier 1.5 (d and sd_d scaled; d * 1.5 * sd_LT for the ss):
SS_D1, RP_D1 = 6.0 * Z95, 30.0 + 6.0 * Z95  # I1: 9.869122 / 39.869122
SS_D2, RP_D2 = 3.0 * Z95, 15.0 + 3.0 * Z95  # I2: 4.934561 / 19.934561
SS_D3, RP_D3 = 1.5 * Z95, 7.5 + 1.5 * Z95  # I3: 2.467280 / 9.967280
# lead-time multiplier 2.0 (lt and sd_lt scaled; d * 2 * sd_LT for ss):
SS_L1, RP_L1 = 8.0 * Z95, 40.0 + 8.0 * Z95  # I1: 13.158829 / 53.158829
SS_L2, RP_L2 = 4.0 * Z95, 20.0 + 4.0 * Z95  # I2: 6.579415 / 26.579415
SS_L3, RP_L3 = 2.0 * Z95, 10.0 + 2.0 * Z95  # I3: 3.289707 / 13.289707

# Whole-unit ceilings (the same math.ceil the formula applies).
U = {
    (I1, "b"): (math.ceil(RP_B1), math.ceil(SS_B1)),  # (27, 7)
    (I1, "99"): (math.ceil(RP_991), math.ceil(SS_991)),  # (30, 10)
    (I1, "d"): (math.ceil(RP_D1), math.ceil(SS_D1)),  # (40, 10)
    (I1, "l"): (math.ceil(RP_L1), math.ceil(SS_L1)),  # (54, 14)
    (I2, "b"): (math.ceil(RP_B2), math.ceil(SS_B2)),  # (14, 4)
    (I2, "99"): (math.ceil(RP_992), math.ceil(SS_992)),  # (15, 5)
    (I2, "d"): (math.ceil(RP_D2), math.ceil(SS_D2)),  # (20, 5)
    (I2, "l"): (math.ceil(RP_L2), math.ceil(SS_L2)),  # (27, 7)
    (I3, "b"): (math.ceil(RP_B3), math.ceil(SS_B3)),  # (7, 2)
    (I3, "99"): (math.ceil(RP_993), math.ceil(SS_993)),  # (8, 3)
    (I3, "d"): (math.ceil(RP_D3), math.ceil(SS_D3)),  # (10, 3)
    (I3, "l"): (math.ceil(RP_L3), math.ceil(SS_L3)),  # (14, 4)
}
# The levers echoed back: all three keys, null when not given.
ECHO_NONE = {
    "service_level": None,
    "demand_multiplier": None,
    "lead_time_multiplier": None,
}
ECHO_99 = {
    "service_level": 0.99,
    "demand_multiplier": None,
    "lead_time_multiplier": None,
}
ECHO_D = {
    "service_level": None,
    "demand_multiplier": 1.5,
    "lead_time_multiplier": None,
}
ECHO_L = {
    "service_level": None,
    "demand_multiplier": None,
    "lead_time_multiplier": 2.0,
}

# Per-scenario expected summary numbers (rp delta total, ss delta total,
# dollar impact at unit costs 5.0 / none / 2.0).
EXPECTED = {
    "none": {
        "deltas": {I1: (0, 0), I2: (0, 0), I3: (0, 0)},
        "rp_total": 0,
        "ss_total": 0,
        "up": 0,
        "down": 0,
        "unchanged": 3,
        "dollar": 0.0,
    },
    "99": {
        "deltas": {I1: (3, 3), I2: (1, 1), I3: (1, 1)},
        "rp_total": 5,
        "ss_total": 5,
        "up": 3,
        "down": 0,
        "unchanged": 0,
        # 3 * 5.0 + 1 * (no cost -> 0) + 1 * 2.0
        "dollar": 3 * COST_1 + 1 * COST_3,
    },
    "d": {
        "deltas": {I1: (13, 3), I2: (6, 1), I3: (3, 1)},
        "rp_total": 22,
        "ss_total": 5,
        "up": 3,
        "down": 0,
        "unchanged": 0,
        "dollar": 13 * COST_1 + 3 * COST_3,
    },
    "l": {
        "deltas": {I1: (27, 7), I2: (13, 3), I3: (7, 2)},
        "rp_total": 47,
        "ss_total": 12,
        "up": 3,
        "down": 0,
        "unchanged": 0,
        "dollar": 27 * COST_1 + 7 * COST_3,
    },
}


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


def _seed(cur) -> None:
    """The fixed-date rows: demand events, lead times, item-sites."""
    de = (
        "INSERT INTO demand_events (id, business_key, item_code, "
        "site_code, quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
    )
    # I1: 2.0 a day for 360 days -> 12 buckets of 60 (d 2.0, sd_d 0.0).
    cur.executemany(
        de,
        [
            (
                f"id-de-421-1-{i}",
                f"de-421-1-{i}",
                I1,
                SITE,
                2.0,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(360)
        ],
    )
    # I2: 1.0 a day for 360 days -> 12 buckets of 30 (d 1.0, sd_d 0.0).
    cur.executemany(
        de,
        [
            (
                f"id-de-421-2-{i}",
                f"de-421-2-{i}",
                I2,
                SITE,
                1.0,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(360)
        ],
    )
    # I3: 0.5 a day for 360 days -> 12 buckets of 15 (d 0.5, sd_d 0.0).
    cur.executemany(
        de,
        [
            (
                f"id-de-421-3-{i}",
                f"de-421-3-{i}",
                I3,
                SITE_B,
                0.5,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(360)
        ],
    )
    # I4: one demand event 15 months before the as-of: no bucket in the
    # window has demand -> no_demand -> not_recommendable_demand.
    cur.execute(
        de,
        (
            "id-de-421-4",
            "de-421-4",
            I4,
            SITE_B,
            1.0,
            "2025-06-01",
            "shipment",
            "test",
        ),
    )
    # I5: 1.0 a day for 360 days, but no lead-time observations and no
    # master lead time -> insufficient_lead_time.
    cur.executemany(
        de,
        [
            (
                f"id-de-421-5-{i}",
                f"de-421-5-{i}",
                I5,
                SITE_B,
                1.0,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(360)
        ],
    )
    # Lead times of 8, 10 and 12 days -> mean 10, sample std 2, three
    # observations (not low confidence, below the outlier fence).
    for item in (I1, I2, I3):
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
                    f"id-lt-421-{i}-{item}",
                    f"lt-421-{i}-{item}",
                    f"SUP-421-{item}",
                    item,
                    ordered,
                    received,
                    "test",
                ),
            )
    # item_sites rows: A421-2 deliberately has NO unit cost.
    for item, site, unit_cost in (
        (I1, SITE, COST_1),
        (I2, SITE, None),
        (I3, SITE_B, COST_3),
        (I4, SITE_B, 1.0),
        (I5, SITE_B, 1.0),
    ):
        cur.execute(
            "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
            "reorder_point, source_system) VALUES (%s, %s, %s, %s, %s, %s)",
            (f"id-is-421-{item}", item, site, unit_cost, 50.0, "test"),
        )


def _basis_of(item: str, scenario: str) -> dict:
    """The expected baseline/scenario block of one item-site.

    The "none" scenario (no levers) is the baseline itself.
    """
    if scenario == "none":
        scenario = "b"
    if scenario == "b":
        ss, rp = {"1": (SS_B1, RP_B1), "2": (SS_B2, RP_B2), "3": (SS_B3, RP_B3)}[
            item[-1]
        ]
    elif scenario == "99":
        ss, rp = {"1": (SS_991, RP_991), "2": (SS_992, RP_992), "3": (SS_993, RP_993)}[
            item[-1]
        ]
    elif scenario == "d":
        ss, rp = {"1": (SS_D1, RP_D1), "2": (SS_D2, RP_D2), "3": (SS_D3, RP_D3)}[
            item[-1]
        ]
    else:
        ss, rp = {"1": (SS_L1, RP_L1), "2": (SS_L2, RP_L2), "3": (SS_L3, RP_L3)}[
            item[-1]
        ]
    return {
        "reorder_point": pytest.approx(rp),
        "reorder_point_units": U[(item, scenario)][0],
        "safety_stock": pytest.approx(ss),
        "safety_stock_units": U[(item, scenario)][1],
    }


def _check_scenario(
    client,
    scenario: str,
    levers: dict,
    expected_echo: dict,
) -> list[dict]:
    """One lever scenario: the direct function and the API agree, every
    item-site row and the summary match the hand-computed values."""
    result = simulate_reorder(as_of=AS_OF, **levers)

    # The API returns the same structure for a body with the levers.
    response = client.post("/api/scenarios/simulate", json=levers)
    assert response.status_code == 200, response.text
    assert response.json() == result

    rows = result["item_sites"]
    # Only the three "ok" item-sites are simulated, in the
    # recommendation order (item_code then site_code).
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (I1, SITE),
        (I2, SITE),
        (I3, SITE_B),
    ]
    # The baseline of every row is the item-site's live recommendation.
    listed = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert listed.status_code == 200, listed.text
    by_key = {(row["item_code"], row["site_code"]): row for row in listed.json()}
    for row in rows:
        live = by_key[(row["item_code"], row["site_code"])]
        assert live["status"] == "ok"
        assert row["baseline"] == {
            "reorder_point": live["reorder_point"],
            "reorder_point_units": live["reorder_point_units"],
            "safety_stock": live["safety_stock"],
            "safety_stock_units": live["safety_stock_units"],
        }

    expected = EXPECTED[scenario]
    for row in rows:
        item = row["item_code"]
        assert row["baseline"] == _basis_of(item, "b")
        assert row["scenario"] == _basis_of(item, scenario)
        rp_delta, ss_delta = expected["deltas"][item]
        assert row["reorder_point_units_delta"] == rp_delta
        assert row["safety_stock_units_delta"] == ss_delta
        # The deltas are scenario minus baseline, whole units.
        assert (
            row["reorder_point_units_delta"]
            == row["scenario"]["reorder_point_units"]
            - row["baseline"]["reorder_point_units"]
        )
        assert (
            row["safety_stock_units_delta"]
            == row["scenario"]["safety_stock_units"]
            - row["baseline"]["safety_stock_units"]
        )

    summary = result["summary"]
    # The totals are the sums of the per-item-site unit deltas.
    assert summary["reorder_point_units_delta_total"] == expected["rp_total"]
    assert summary["safety_stock_units_delta_total"] == expected["ss_total"]
    assert summary["reorder_point_units_delta_total"] == sum(
        row["reorder_point_units_delta"] for row in rows
    )
    assert summary["safety_stock_units_delta_total"] == sum(
        row["safety_stock_units_delta"] for row in rows
    )
    assert summary["item_sites"] == 3
    # The up/down/unchanged counts partition the item-sites by the sign
    # of reorder_point_units_delta.
    assert summary["reorder_point_up"] == expected["up"]
    assert summary["reorder_point_down"] == expected["down"]
    assert summary["reorder_point_unchanged"] == expected["unchanged"]
    assert (
        summary["reorder_point_up"]
        + summary["reorder_point_down"]
        + summary["reorder_point_unchanged"]
        == summary["item_sites"]
    )
    # dollar_impact is the sum of reorder_point_units_delta * unit_cost;
    # A421-2 has no unit cost: it adds 0 and is counted separately.
    assert summary["dollar_impact"] == pytest.approx(expected["dollar"])
    assert summary["item_sites_without_unit_cost"] == 1
    # The levers applied are echoed back, each null when not given.
    assert summary["levers"] == expected_echo
    return rows


def _snapshot_tables() -> dict:
    """Every row of every table, so any write anywhere is visible."""

    def _normalize(value):
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        if isinstance(value, float) and math.isnan(value):
            return "nan"
        return value

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version' "
                "ORDER BY tablename"
            )
            snapshot = {}
            for (table,) in cur.fetchall():
                cur.execute(
                    psycopg2.sql.SQL("SELECT * FROM {}").format(
                        psycopg2.sql.Identifier(table)
                    )
                )
                snapshot[table] = sorted(
                    tuple(_normalize(v) for v in row) for row in cur.fetchall()
                )
            return snapshot
    finally:
        conn.close()


def test_issue421_freeform(client, monkeypatch, user_headers):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # Everything below is read-only: the snapshot before the simulate
    # calls must equal the snapshot after them (stored
    # recommendations-adjacent tables, item_sites and settings included).
    before = _snapshot_tables()

    # --- AC-3: with no levers the scenario equals the baseline. -------
    rows = _check_scenario(client, "none", {}, ECHO_NONE)
    for row in rows:
        assert row["scenario"] == row["baseline"]
        assert row["reorder_point_units_delta"] == 0
        assert row["safety_stock_units_delta"] == 0

    # --- AC-3: service level 0.99 vs the 0.95 baseline. ---------------
    # z rises (1.644854 -> 2.326348), so the safety stock and the
    # reorder point rise for EVERY item-site.
    rows = _check_scenario(client, "99", {"service_level": 0.99}, ECHO_99)
    for row in rows:
        assert row["scenario"]["safety_stock"] > row["baseline"]["safety_stock"]
        assert row["scenario"]["reorder_point"] > row["baseline"]["reorder_point"]
    # I1's hand-computed scenario safety stock: 4 * z(0.99) = 9.305391,
    # exactly what _basis_result gives with the new z.
    i1 = rows[0]
    expected_i1 = _basis_result(2.0, 0.0, 10.0, 2.0, Z99, 0.99)
    assert i1["scenario"]["safety_stock"] == pytest.approx(SS_991)
    assert i1["scenario"]["safety_stock"] == pytest.approx(expected_i1["safety_stock"])
    assert i1["scenario"]["reorder_point"] == pytest.approx(
        expected_i1["reorder_point"]
    )

    # --- AC-3: demand multiplier 1.5 (d and sd_d scale). --------------
    rows = _check_scenario(client, "d", {"demand_multiplier": 1.5}, ECHO_D)
    # I1: the scaled inputs (d 3.0, sd_d 0.0) give the hand-computed
    # scenario 30 + 6 * z(0.95) = 39.869122 -> 40 whole units, a rise of
    # 40 - 27 = 13 reorder-point units.
    expected_i1 = _basis_result(2.0 * 1.5, 0.0 * 1.5, 10.0, 2.0, Z95, 0.95)
    i1 = rows[0]
    assert i1["scenario"] == {
        "reorder_point": pytest.approx(expected_i1["reorder_point"]),
        "reorder_point_units": 40,
        "safety_stock": pytest.approx(expected_i1["safety_stock"]),
        "safety_stock_units": 10,
    }
    assert i1["reorder_point_units_delta"] == 40 - 27 == 13

    # --- AC-3: lead-time multiplier 2.0 (lt and sd_lt scale). ---------
    rows = _check_scenario(client, "l", {"lead_time_multiplier": 2.0}, ECHO_L)
    expected_i1 = _basis_result(2.0, 0.0, 10.0 * 2.0, 2.0 * 2.0, Z95, 0.95)
    i1 = rows[0]
    assert i1["scenario"] == {
        "reorder_point": pytest.approx(expected_i1["reorder_point"]),
        "reorder_point_units": 54,
        "safety_stock": pytest.approx(expected_i1["safety_stock"]),
        "safety_stock_units": 14,
    }
    assert i1["reorder_point_units_delta"] == 54 - 27 == 27

    # --- AC-3: the not-recommendable item-sites are not simulated. ----
    result = simulate_reorder(as_of=AS_OF)
    assert I4 not in {row["item_code"] for row in result["item_sites"]}
    assert I5 not in {row["item_code"] for row in result["item_sites"]}
    by_key = {
        (row["item_code"], row["site_code"]): row
        for row in client.get(
            "/api/recommendations", params={"as_of": AS_OF_STR}
        ).json()
    }
    assert by_key[(I4, SITE_B)]["status"] == "not_recommendable_demand"
    assert by_key[(I4, SITE_B)]["demand_class"] == "no_demand"
    assert by_key[(I5, SITE_B)]["status"] == "insufficient_lead_time"

    # --- AC-3: out-of-range levers are 422, never 500. ----------------
    for body in (
        {"service_level": 1.5},
        {"service_level": 0.0},
        {"service_level": "abc"},
        {"demand_multiplier": 0.0},
        {"demand_multiplier": -1.0},
        {"lead_time_multiplier": 0.0},
        {"lead_time_multiplier": -2.0},
        # A finite but hostile multiplier: the scaled inputs overflow
        # the formula; that is an out-of-range lever (422), not a 500.
        {"demand_multiplier": 1e300},
    ):
        response = client.post("/api/scenarios/simulate", json=body)
        assert response.status_code == 422, (body, response.text)
    # The direct function raises ValueError for the same levers, before
    # any computation.
    for levers in (
        {"service_level": 1.5},
        {"demand_multiplier": 0.0},
        {"lead_time_multiplier": -1.0},
        {"demand_multiplier": 1e300},
    ):
        with pytest.raises(ValueError):
            simulate_reorder(as_of=AS_OF, **levers)

    # --- AC-3: 401 without a token. -----------------------------------
    with TestClient(app) as anonymous:
        response = anonymous.post("/api/scenarios/simulate", json={})
    assert response.status_code == 401

    # --- AC-2: the OpenAPI document describes the route and models. ---
    spec = json.loads(
        (Path(__file__).resolve().parents[1] / "openapi.json").read_text(
            encoding="utf-8"
        )
    )
    operation = spec["paths"]["/api/scenarios/simulate"]["post"]
    request_schema = operation["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"] == "#/components/schemas/SimulateBody"
    response_schema = operation["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert response_schema["$ref"] == "#/components/schemas/SimulateResponse"
    for model in (
        "SimulateBody",
        "SimulateResponse",
        "SimulateSummary",
        "SimulateItemSite",
        "SimulateBasis",
        "SimulateLevers",
    ):
        assert model in spec["components"]["schemas"], model

    # --- Identity: the non-admin (role "user") second pass. -----------
    # This issue adds no page and no admin-only control; the route is
    # open to any authenticated user like the other planner routes. The
    # non-admin development token (roles ["user"]) gets the same 200
    # with the same structure.
    response = client.post(
        "/api/scenarios/simulate",
        json={"service_level": 0.99, "demand_multiplier": 1.5},
        headers=user_headers,
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload.keys()) == {"summary", "item_sites"}
    assert payload["summary"]["levers"] == {
        "service_level": 0.99,
        "demand_multiplier": 1.5,
        "lead_time_multiplier": None,
    }
    assert [(row["item_code"], row["site_code"]) for row in payload["item_sites"]] == [
        (I1, SITE),
        (I2, SITE),
        (I3, SITE_B),
    ]
    expected_direct = simulate_reorder(
        service_level=0.99, demand_multiplier=1.5, as_of=AS_OF
    )
    assert payload == expected_direct

    # --- AC-3: nothing is written. -------------------------------------
    after = _snapshot_tables()
    assert after == before
