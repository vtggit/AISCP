"""Issue #676 proof: the per-class service level lever on the simulation.

AC-1 — ``SimulateBody`` gains ``service_level_by_class`` (a mapping of
exactly the keys "A", "B" and "C", each a number strictly between 0
and 1); ``app.scenario.simulate.simulate_reorder`` gains the matching
keyword: when it is given, each simulated item-site's scenario service
level is the level of its baseline
``service_level_source.abc_class`` when that is "A", "B" or "C", else
— for the unclassified item-sites — the ``service_level`` lever when
that is given, else the baseline's ``inputs.service_level``; the other
levers apply exactly as before. The response summary gains the new key
``service_level_by_class``, the given mapping or null; the existing
``levers`` object is unchanged and keeps exactly its three keys.

AC-2 — this proof runs against the test database (as-of 2026-09-30,
date.today pinned to 2026-10-02, the default single service level
0.95) with four recommendable item-sites: three ABC-classed by their
annual values ``365 * d * unit_cost`` (182.5, 27.375 and 9.125 of the
219.0 total — A, B and C at the 0.80 / 0.95 Pareto cuts) and one
unclassified (no unit cost in ``item_sites``), each at the baseline
service level 0.95:

* A676-1 @ S1 (class A) — 0.5 a day for 360 days (12 buckets of 15),
  lead times 8, 10, 12 days (mean 10, sd 2), 1.0/unit: d 0.5, safety
  stock z * 1, reorder point 5 + z — baseline 1.644854 / 6.644854
  (2 / 7 whole units);
* A676-2 @ S1 (class B) — 0.25 a day (12 buckets of 7.5), the same
  lead times, 0.3/unit: d 0.25, safety stock z * 0.5, reorder point
  2.5 + z * 0.5 — baseline 0.822427 / 3.322427 (1 / 4);
* A676-3 @ S2 (class C) — 0.25 a day, lead times 0, 10, 20 days (mean
  10, sd 10), 0.1/unit: d 0.25, safety stock z * 2.5, reorder point
  2.5 + z * 2.5 — baseline 4.112134 / 6.612134 (5 / 7);
* A676-4 @ S2 (unclassified) — 0.25 a day, the same lead times as
  A676-2, NO unit cost in item_sites: baseline 0.822427 / 3.322427
  (1 / 4).

The checks: with ``service_level_by_class`` ``{"A": 0.98, "B": 0.95,
"C": 0.90}`` the A item-site is simulated at 0.98 (its reorder point
rises, 7 -> 8 whole units), the B item-site at 0.95 (unchanged), the C
item-site at 0.90 (its reorder point falls, 7 -> 6 whole units) and
the unclassified item-site at the baseline 0.95 (unchanged); the
summary echoes the mapping in ``service_level_by_class`` while
``levers`` is exactly ``{"service_level": null, "demand_multiplier":
null, "lead_time_multiplier": null}``, and ``dollar_impact`` is the
sum of the three classed deltas times their unit costs
(``1 * 1.0 + 0 * 0.3 + -1 * 0.1 = 0.9``, the unclassified item-site
without a unit cost contributing 0). Together with the single lever
``service_level`` 0.99, the unclassified item-site is simulated at
0.99 (its safety stock rises 1 -> 2 whole units) and the classed ones
at their class levels.
``{"A": 0.98}`` (missing keys), ``{"A": 1.2, "B": 0.95, "C": 0.9}``
and ``{"A": 0.98, "B": 0.95, "C": 0.9, "D": 0.5}`` each answer 422;
with the lever omitted ``summary.service_level_by_class`` is null and
the rest of the response is exactly the pre-change response.

AC-3 — every invalid ``service_level_by_class`` value answers 422
whose ``detail`` is a string naming the lever, never Pydantic's
default list detail.
"""

import statistics
from datetime import date, timedelta

import pytest

from app.db.connection import get_connection
from app.recommend.reorder import _basis_result
from app.scenario.simulate import _validate_levers, simulate_reorder

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)
FIXED_TODAY = date(2026, 10, 2)
SITE = "S1"
SITE_B = "S2"
IA, IB, IC, IU = "A676-1", "A676-2", "A676-3", "A676-4"
COST_A, COST_B, COST_C = 1.0, 0.3, 0.1  # A676-4 has no unit cost
Z95 = statistics.NormalDist().inv_cdf(0.95)  # 1.644854
Z98 = statistics.NormalDist().inv_cdf(0.98)  # 2.053749
Z90 = statistics.NormalDist().inv_cdf(0.90)  # 1.281552
Z99 = statistics.NormalDist().inv_cdf(0.99)  # 2.326348

SLEBC = {"A": 0.98, "B": 0.95, "C": 0.90}

# (safety_stock, reorder_point) and whole units (reorder_point_units,
# safety_stock_units) per item-site and scenario: "b" the 0.95
# baseline, "98" / "90" / "99" at the named service level.
EXPECTED = {
    # d 0.5, sigma 1: ss z, rp 5 + z.
    IA: {
        "b": (Z95, 5.0 + Z95, 7, 2),
        "98": (Z98, 5.0 + Z98, 8, 3),
    },
    # d 0.25, sigma 0.5: ss 0.5z, rp 2.5 + 0.5z.
    IB: {
        "b": (0.5 * Z95, 2.5 + 0.5 * Z95, 4, 1),
        "99": (0.5 * Z99, 2.5 + 0.5 * Z99, 4, 2),
    },
    # d 0.25, sigma 2.5: ss 2.5z, rp 2.5 + 2.5z.
    IC: {
        "b": (2.5 * Z95, 2.5 + 2.5 * Z95, 7, 5),
        "90": (2.5 * Z90, 2.5 + 2.5 * Z90, 6, 4),
    },
    # The unclassified item: d 0.25, sigma 0.5, like IB.
    IU: {
        "b": (0.5 * Z95, 2.5 + 0.5 * Z95, 4, 1),
        "99": (0.5 * Z99, 2.5 + 0.5 * Z99, 4, 2),
    },
}

# The annual values (365 * d * unit_cost) of the three classed
# item-sites: 182.5, 27.375 and 9.125 of the 219.0 total, so the
# cumulative shares before them are 0, 0.8333 and 0.9583 — A, B and C
# at the 0.80 / 0.95 Pareto cuts.
ANNUAL = {
    IA: 365.0 * 0.5 * COST_A,
    IB: 365.0 * 0.25 * COST_B,
    IC: 365.0 * 0.25 * COST_C,
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
    for item, site, quantity in (
        (IA, SITE, 0.5),
        (IB, SITE, 0.25),
        (IC, SITE_B, 0.25),
        (IU, SITE_B, 0.25),
    ):
        cur.executemany(
            de,
            [
                (
                    f"id-de-676-{item}-{i}",
                    f"de-676-{item}-{i}",
                    item,
                    site,
                    quantity,
                    (FIRST_DAY + timedelta(days=i)).isoformat(),
                    "shipment",
                    "test",
                )
                for i in range(360)
            ],
        )
    # Lead times: 8, 10 and 12 days (mean 10, sample std 2) for A676-1,
    # A676-2 and the unclassified item; 0, 10 and 20 days (mean 10,
    # sample std 10) for A676-3.
    lt = (
        "INSERT INTO lead_time_observations (id, business_key, "
        "supplier_code, item_code, ordered_on, received_on, "
        "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)"
    )
    for item, ordered_days in (
        (IA, (8, 10, 12)),
        (IB, (8, 10, 12)),
        (IC, (0, 10, 20)),
        (IU, (8, 10, 12)),
    ):
        for i, span in enumerate(ordered_days):
            received = date(2026, 8, 30)
            ordered = received - timedelta(days=span)
            cur.execute(
                lt,
                (
                    f"id-lt-676-{i}-{item}",
                    f"lt-676-{i}-{item}",
                    f"SUP-676-{item}",
                    item,
                    ordered.isoformat(),
                    received.isoformat(),
                    "test",
                ),
            )
    # item_sites rows: the unclassified item deliberately has NO unit
    # cost.
    for item, site, unit_cost in (
        (IA, SITE, COST_A),
        (IB, SITE, COST_B),
        (IC, SITE_B, COST_C),
        (IU, SITE_B, None),
    ):
        cur.execute(
            "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
            "reorder_point, source_system) VALUES (%s, %s, %s, %s, %s, %s)",
            (f"id-is-676-{item}", item, site, unit_cost, 50.0, "test"),
        )


def _basis(item: str, scenario: str) -> dict:
    """The expected baseline/scenario block of one item-site."""
    ss, rp, rp_units, ss_units = EXPECTED[item][scenario]
    return {
        "reorder_point": pytest.approx(rp),
        "reorder_point_units": rp_units,
        "safety_stock": pytest.approx(ss),
        "safety_stock_units": ss_units,
    }


def _rows(result: dict) -> dict:
    return {row["item_code"]: row for row in result["item_sites"]}


def test_issue676_freeform(client, monkeypatch):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # --- The four baselines: the classes and the 0.95 single level. ---
    # The class of each item-site comes from its annual value
    # 365 * d * unit_cost in the population of the three valued
    # item-sites (the unclassified one has no unit cost, hence no
    # value).
    assert pytest.approx(sum(ANNUAL.values())) == 219.0
    listed = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert listed.status_code == 200, listed.text
    by_key = {(row["item_code"], row["site_code"]): row for row in listed.json()}
    for key, abc_class in (
        ((IA, SITE), "A"),
        ((IB, SITE), "B"),
        ((IC, SITE_B), "C"),
        ((IU, SITE_B), None),
    ):
        row = by_key[key]
        assert row["status"] == "ok"
        assert row["service_level_source"] == {
            "abc_class": abc_class,
            "source": "single",
        }
        assert row["inputs"]["service_level"] == pytest.approx(0.95)

    # --- AC-1 / AC-2: the per-class lever. -----------------------------
    # The classed item-sites are simulated at their class's level (0.98
    # / 0.95 / 0.90), the unclassified one at the baseline 0.95: A
    # rises (7 -> 8 units), B and the unclassified one stay, C falls
    # (7 -> 6 units).
    result = simulate_reorder(service_level_by_class=SLEBC, as_of=AS_OF)
    response = client.post(
        "/api/scenarios/simulate", json={"service_level_by_class": SLEBC}
    )
    assert response.status_code == 200, response.text
    assert response.json() == result
    rows = _rows(result)
    assert list(rows) == [IA, IB, IC, IU]
    assert rows[IA]["scenario"] == _basis(IA, "98")
    expected = _basis_result(0.5, 0.0, 10.0, 2.0, Z98, 0.98)
    assert rows[IA]["scenario"] == {
        "reorder_point": pytest.approx(expected["reorder_point"]),
        "reorder_point_units": expected["reorder_point_units"],
        "safety_stock": pytest.approx(expected["safety_stock"]),
        "safety_stock_units": expected["safety_stock_units"],
    }
    assert rows[IB]["scenario"] == rows[IB]["baseline"]
    assert rows[IC]["scenario"] == _basis(IC, "90")
    assert rows[IU]["scenario"] == rows[IU]["baseline"]
    for item, row in rows.items():
        assert row["baseline"] == _basis(item, "b")
    # A higher, B unchanged, C lower — in raw value and whole units.
    assert rows[IA]["scenario"]["reorder_point"] > rows[IA]["baseline"]["reorder_point"]
    assert rows[IA]["reorder_point_units_delta"] == 1
    assert rows[IB]["reorder_point_units_delta"] == 0
    assert rows[IB]["safety_stock_units_delta"] == 0
    assert rows[IC]["scenario"]["reorder_point"] < rows[IC]["baseline"]["reorder_point"]
    assert rows[IC]["reorder_point_units_delta"] == -1
    assert rows[IU]["reorder_point_units_delta"] == 0
    # dollar_impact: the sum of the three classed deltas times their
    # unit costs (1 * 1.0 + 0 * 0.3 + -1 * 0.1 = 0.9); the
    # unclassified item-site has no unit cost, so it contributes 0 and
    # is counted separately.
    assert result["summary"]["dollar_impact"] == pytest.approx(
        1 * COST_A + 0 * COST_B + -1 * COST_C
    )
    assert result["summary"]["dollar_impact"] == pytest.approx(
        sum(
            row["reorder_point_units_delta"]
            * {"A676-1": COST_A, "A676-2": COST_B, "A676-3": COST_C}.get(
                row["item_code"], 0.0
            )
            for row in result["item_sites"]
        )
    )
    assert result["summary"]["item_sites"] == 4
    assert result["summary"]["item_sites_without_unit_cost"] == 1
    # The echo: the mapping in the NEW key, and the existing levers
    # object exactly its three keys (all null).
    assert result["summary"]["service_level_by_class"] == SLEBC
    assert result["summary"]["levers"] == {
        "service_level": None,
        "demand_multiplier": None,
        "lead_time_multiplier": None,
    }
    assert set(result["summary"]["levers"]) == {
        "service_level",
        "demand_multiplier",
        "lead_time_multiplier",
    }
    assert result["summary"]["reorder_point_units_delta_total"] == 0
    assert result["summary"]["safety_stock_units_delta_total"] == 0
    assert (
        result["summary"]["reorder_point_up"],
        result["summary"]["reorder_point_down"],
        result["summary"]["reorder_point_unchanged"],
    ) == (1, 1, 2)

    # --- AC-2: together with the single service level lever. -----------
    # The classed item-sites stay at their class levels (not 0.99); the
    # unclassified item-site is simulated at 0.99 (its safety stock
    # rises, 1 -> 2 whole units, its reorder point stays at 4).
    result = simulate_reorder(
        service_level=0.99, service_level_by_class=SLEBC, as_of=AS_OF
    )
    response = client.post(
        "/api/scenarios/simulate",
        json={"service_level": 0.99, "service_level_by_class": SLEBC},
    )
    assert response.status_code == 200, response.text
    assert response.json() == result
    rows = _rows(result)
    assert rows[IA]["scenario"] == _basis(IA, "98")
    assert rows[IB]["scenario"] == rows[IB]["baseline"]
    assert rows[IC]["scenario"] == _basis(IC, "90")
    assert rows[IU]["scenario"] == _basis(IU, "99")
    expected = _basis_result(0.25, 0.0, 10.0, 2.0, Z99, 0.99)
    assert rows[IU]["scenario"] == {
        "reorder_point": pytest.approx(expected["reorder_point"]),
        "reorder_point_units": expected["reorder_point_units"],
        "safety_stock": pytest.approx(expected["safety_stock"]),
        "safety_stock_units": expected["safety_stock_units"],
    }
    assert rows[IU]["reorder_point_units_delta"] == 0
    assert rows[IU]["safety_stock_units_delta"] == 1
    assert result["summary"]["service_level_by_class"] == SLEBC
    assert result["summary"]["levers"] == {
        "service_level": 0.99,
        "demand_multiplier": None,
        "lead_time_multiplier": None,
    }
    assert result["summary"]["reorder_point_units_delta_total"] == 0
    assert result["summary"]["safety_stock_units_delta_total"] == 1
    assert (
        result["summary"]["reorder_point_up"],
        result["summary"]["reorder_point_down"],
        result["summary"]["reorder_point_unchanged"],
    ) == (1, 1, 2)
    assert result["summary"]["dollar_impact"] == pytest.approx(
        1 * COST_A + 0 * COST_B + -1 * COST_C
    )

    # --- AC-2 / AC-3: the refusals, with a string detail. --------------
    for body in (
        {"service_level_by_class": {"A": 0.98}},
        {"service_level_by_class": {"A": 1.2, "B": 0.95, "C": 0.9}},
        {"service_level_by_class": {"A": 0.98, "B": 0.95, "C": 0.9, "D": 0.5}},
    ):
        response = client.post("/api/scenarios/simulate", json=body)
        assert response.status_code == 422, (body, response.text)
    # AC-3: the detail is a string naming the lever, for every invalid
    # value — not Pydantic's default list detail.
    for mapping in (
        {"A": 0.98},
        {"A": 1.2, "B": 0.95, "C": 0.9},
        {"A": 0.98, "B": 0.95, "C": 0.9, "D": 0.5},
        {"A": 0.98, "B": 0.95},
        {"a": 0.98, "B": 0.95, "C": 0.9},
        {"A": 0.0, "B": 0.95, "C": 0.9},
        {"A": 0.98, "B": 0.95, "C": 1.0},
        {"A": "0.98", "B": 0.95, "C": 0.9},
        {"A": None, "B": 0.95, "C": 0.9},
        {"A": True, "B": 0.95, "C": 0.9},
        "abc",
        5,
        [0.98, 0.95, 0.9],
    ):
        response = client.post(
            "/api/scenarios/simulate", json={"service_level_by_class": mapping}
        )
        assert response.status_code == 422, (mapping, response.text)
        detail = response.json()["detail"]
        assert isinstance(detail, str), (mapping, response.text)
        assert "service_level_by_class" in detail, (mapping, detail)
    # An explicit null is "not given", like an omitted lever.
    response = client.post(
        "/api/scenarios/simulate", json={"service_level_by_class": None}
    )
    assert response.status_code == 200, response.text
    assert response.json()["summary"]["service_level_by_class"] is None
    # The direct function and the engine's validation raise ValueError
    # for the same invalid values, before any computation.
    for levers in (
        {"service_level_by_class": {"A": 0.98}},
        {"service_level_by_class": {"A": 1.2, "B": 0.95, "C": 0.9}},
        {"service_level_by_class": {"A": 0.98, "B": 0.95, "C": 0.9, "D": 0.5}},
        {"service_level_by_class": {"A": "0.98", "B": 0.95, "C": 0.9}},
        {"service_level_by_class": "abc"},
        {"service_level_by_class": 5},
    ):
        with pytest.raises(ValueError):
            simulate_reorder(as_of=AS_OF, **levers)
    for mapping in (
        {"A": 0.98},
        {"A": 1.2, "B": 0.95, "C": 0.9},
        {"A": 0.98, "B": 0.95, "C": 0.9, "D": 0.5},
        {"A": "0.98", "B": 0.95, "C": 0.9},
        "abc",
        5,
    ):
        with pytest.raises(ValueError, match="service_level_by_class"):
            _validate_levers(None, None, None, mapping)
    # The valid mapping still validates cleanly, alone or with the
    # other levers.
    _validate_levers(None, None, None, SLEBC)
    _validate_levers(0.99, 1.5, 2.0, SLEBC)

    # --- AC-2: the lever omitted — the pre-change response. ------------
    # summary.service_level_by_class is null and the rest of the
    # response is exactly what the route answered before this change:
    # every scenario equals its baseline, every delta 0, and the
    # summary the nine pre-change keys with the pre-change values.
    result = simulate_reorder(as_of=AS_OF)
    response = client.post("/api/scenarios/simulate", json={})
    assert response.status_code == 200, response.text
    assert response.json() == result
    summary = result["summary"]
    assert summary["service_level_by_class"] is None
    legacy_summary = {
        key: value for key, value in summary.items() if key != "service_level_by_class"
    }
    assert legacy_summary == {
        "item_sites": 4,
        "reorder_point_units_delta_total": 0,
        "safety_stock_units_delta_total": 0,
        "reorder_point_up": 0,
        "reorder_point_down": 0,
        "reorder_point_unchanged": 4,
        "dollar_impact": 0.0,
        "item_sites_without_unit_cost": 1,
        "levers": {
            "service_level": None,
            "demand_multiplier": None,
            "lead_time_multiplier": None,
        },
    }
    for row in result["item_sites"]:
        item = row["item_code"]
        assert row["baseline"] == _basis(item, "b")
        assert row["scenario"] == row["baseline"]
        assert row["reorder_point_units_delta"] == 0
        assert row["safety_stock_units_delta"] == 0
