"""Issue #495 proof: the aggregate projection accepts the scenario levers.

AC-1/AC-3 — ``app.aggregate.projection.aggregate_projection`` and
``GET /api/aggregate/projection`` accept three optional levers with
exactly the meaning ``POST /api/scenarios/simulate`` gives them:
``service_level`` (a number strictly between 0 and 1),
``demand_multiplier`` (a positive finite number) and
``lead_time_multiplier`` (a positive finite number), validated with
``app.scenario.simulate._validate_levers`` before anything is read and
answered with 422 when out of range.  With no lever given the output
is exactly what it is without lever arguments, field for field; with
any lever given every recommendable item-site's inputs are scaled as
``simulate_reorder`` scales them — ``d' = d x demand_multiplier``,
``sd_d' = sd_d x demand_multiplier``, ``lt' = lt x
lead_time_multiplier``, ``sd_lt' = sd_lt x lead_time_multiplier`` (a
lever not given counts as 1.0) — the service level is the given one or
else the baseline's ``inputs.service_level`` with ``z' =
NormalDist().inv_cdf(service_level')``, and the scenario reorder point
``_basis_result(d', sd_d', lt', sd_lt', z', service_level')["reorder_point_units"]``
replaces ``reorder_point_units``; the projection then uses ``d'`` in
place of ``d`` everywhere it uses ``d`` (the as-is projected demand and
its dollar value, the planned review's demand and order-up-to level)
and ``lt'`` in place of ``lt`` for planned arrivals.  Everything else
is unchanged: the open lines, the on-hand, the bucket months, the four
planned fields' rules, the response shape, the single read of each
source, the ``as_of`` and ``months`` bounds, and the 422 on a scaled
input that overflows the formula (raised as ``ValueError``, never a
500).  ``backend/openapi.json`` lists the three levers as optional
query parameters (AC-5).

AC-2/AC-4 — proven below with the three reads monkeypatched (no
database), ``as_of`` 2026-01-15, one recommendable item-site I1 at S1
with baseline inputs ``d`` 10, ``sd_d`` 0, ``lt`` 20, ``sd_lt`` 0,
``service_level`` 0.95, ``reorder_point_units`` 300, ``unit_cost``
2.0, on-hand 250 and no open lines:

  * no lever: the output equals the call without lever arguments,
    field for field (the #484 worked example: January 310 demand,
    -60 on-hand, a 360-unit order arriving 21 January, ...);
  * ``demand_multiplier`` 1.5 (3 months): the scenario reorder point
    is ``ceil(15 x 20)`` = 300; January's projected demand 465 (value
    930.0), on-hand -215, and the planned order 300 + 465 - 250 = 515
    units arriving 21 January — ``planned_receipts_units`` 515,
    ``planned_on_hand_units`` 300;
  * ``lead_time_multiplier`` 2 (3 months): the scenario reorder point
    is ``ceil(10 x 40)`` = 400; January orders 460 arriving 10
    February (January ``planned_receipts_units`` 0,
    ``planned_on_hand_units`` -60); February's position -60 + 460 =
    400 is below 680, so it orders 280 arriving 13 March (receipts
    460, planned on-hand 120); March's position 120 + 280 = 400 is
    below 710, so it orders 310 arriving 10 April, after the window
    (receipts 280, planned on-hand 90); the as-is demand and on-hand
    equal the no-lever values;
  * ``service_level`` with ``sd_d`` 2 instead (``reorder_point_units``
    215): 0.5 gives a scenario reorder point of 200 (z = 0) and 0.95
    given explicitly 215 (``ceil(200 + 1.6448536 x sqrt(80))``), each
    shown through the January planned order (S = rop + 310, position
    250): 260 and 275 units;
  * equivalence: for representative lever combinations the scenario
    reorder point the projection applies equals the one
    ``app.scenario.simulate.simulate_reorder`` reports for the same
    levers;
  * bounds: through the app with the test Bearer, ``service_level``
    1 / 0, ``demand_multiplier`` 0 / -1, ``lead_time_multiplier`` 0
    and ``demand_multiplier`` 1e308 each return 422; the no-lever and
    the ``demand_multiplier`` 1.5 requests return 200 with the values
    above; a future ``as_of`` and ``months`` 13 still return 422; and
    each read helper is called exactly once per projection.
"""

import json
import math
from datetime import date, timedelta
from pathlib import Path

from app.aggregate import projection
from app.aggregate.projection import aggregate_projection
from app.scenario import simulate as scenario_simulate
from app.scenario.simulate import simulate_reorder

AS_OF = date(2026, 1, 15)
URL = "/api/aggregate/projection"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

ONCE = {"recommendations": 1, "on_hand": 1, "lines": 1}

BUCKET_KEYS = {
    "month",
    "projected_demand_units",
    "projected_supply_units",
    "projected_on_hand_units",
    "projected_shortfall",
    "projected_demand_value",
    "projected_supply_value",
    "signal_units",
    "planned_receipts_units",
    "planned_receipts_value",
    "planned_on_hand_units",
    "planned_shortfall",
}


def _rec(item, site, rop, d, sd_d, lt, unit_cost):
    """One recommendable recommendation row as the projection sees it."""
    return {
        "status": "ok",
        "item_code": item,
        "site_code": site,
        "reorder_point": float(rop) - 0.5,
        "reorder_point_units": rop,
        "safety_stock": 0.0,
        "safety_stock_units": 0,
        "unit_cost": unit_cost,
        "inputs": {
            "d": d,
            "sd_d": sd_d,
            "lt": lt,
            "sd_lt": 0.0,
            "z": 1.6448536269514722,
            "service_level": 0.95,
        },
    }


def _install(monkeypatch, recs, on_hand_rows, line_rows):
    """Monkeypatch the three reads (no database) and count their calls."""
    calls = {"recommendations": 0, "on_hand": 0, "lines": 0}

    def fake_recommendations(as_of=None):
        calls["recommendations"] += 1
        return list(recs)

    def fake_list_rows(limit, offset, **filters):
        calls["on_hand"] += 1
        return list(on_hand_rows)

    def fake_list_latest():
        calls["lines"] += 1
        return list(line_rows)

    monkeypatch.setattr(projection, "recommendations_for_all", fake_recommendations)
    monkeypatch.setattr(projection._on_hand_repo, "list_rows", fake_list_rows)
    monkeypatch.setattr(projection._open_po_repo, "list_latest", fake_list_latest)
    return calls


def _install_simulate(monkeypatch, recs):
    """Monkeypatch simulate_reorder's reads (no database)."""

    def fake_recommendations(as_of=None):
        return list(recs)

    def fake_list_rows(limit, offset, **filters):
        return []

    monkeypatch.setattr(
        scenario_simulate, "recommendations_for_all", fake_recommendations
    )
    monkeypatch.setattr(scenario_simulate._repo, "list_rows", fake_list_rows)


def _bucket(
    month,
    demand,
    on_hand,
    demand_value,
    receipts,
    planned_on_hand,
    planned_shortfall=False,
):
    """One bucket row with zero supply (the test has no open lines)."""
    return {
        "month": month,
        "projected_demand_units": demand,
        "projected_supply_units": 0.0,
        "projected_on_hand_units": on_hand,
        "projected_shortfall": on_hand < 0,
        "projected_demand_value": demand_value,
        "projected_supply_value": 0.0,
        "signal_units": 0.0,
        "planned_receipts_units": receipts,
        "planned_receipts_value": receipts * 2.0,
        "planned_on_hand_units": planned_on_hand,
        "planned_shortfall": planned_shortfall,
    }


def _group(*buckets):
    return {
        "group": {"dimension": "site", "code": "S1"},
        "buckets": list(buckets),
        "item_sites_without_unit_cost": 0,
    }


# Baseline: I1@S1 rop 300, d 10, sd_d 0, lt 20, sd_lt 0, service_level
# 0.95, unit_cost 2.0, on-hand 250, no open lines, 3 months (January 31
# days, February 28, March 31).  No lever: the #484 worked example.
NO_LEVER = _group(
    _bucket("2026-01", 310, -60.0, 620.0, 360, 300.0),
    _bucket("2026-02", 280, -340.0, 560.0, 280, 300.0),
    _bucket("2026-03", 310, -650.0, 620.0, 310, 300.0),
)
# demand_multiplier 1.5: d' 15, lt' 20, scenario rop ceil(15 x 20) = 300.
DEMAND_1_5 = _group(
    _bucket("2026-01", 465, -215.0, 930.0, 515, 300.0),
    _bucket("2026-02", 420, -635.0, 840.0, 420, 300.0),
    _bucket("2026-03", 465, -1100.0, 930.0, 465, 300.0),
)
# lead_time_multiplier 2: d 10, lt' 40, scenario rop ceil(10 x 40) = 400.
LEAD_TIME_2 = _group(
    _bucket("2026-01", 310, -60.0, 620.0, 0, -60.0, planned_shortfall=True),
    _bucket("2026-02", 280, -340.0, 560.0, 460, 120.0),
    _bucket("2026-03", 310, -650.0, 620.0, 280, 90.0),
)
# service_level with sd_d 2 (baseline rop 215): the as-is demand and
# on-hand equal the no-lever values; only the planned review changes.
SL_0_5 = _group(
    _bucket("2026-01", 310, -60.0, 620.0, 260, 200.0),
    _bucket("2026-02", 280, -340.0, 560.0, 280, 200.0),
    _bucket("2026-03", 310, -650.0, 620.0, 310, 200.0),
)
SL_0_95 = _group(
    _bucket("2026-01", 310, -60.0, 620.0, 275, 215.0),
    _bucket("2026-02", 280, -340.0, 560.0, 280, 215.0),
    _bucket("2026-03", 310, -650.0, 620.0, 310, 215.0),
)


def test_issue495_freeform(monkeypatch, client):
    recs = [_rec("I1", "S1", 300, 10, 0.0, 20, 2.0)]
    on_hand = [{"item_code": "I1", "site_code": "S1", "quantity_on_hand": 250}]

    # --- no lever: field for field what it is today ------------------------
    calls = _install(monkeypatch, recs, on_hand, [])
    no_lever = aggregate_projection(as_of=AS_OF, months=3)
    assert calls == ONCE
    calls = _install(monkeypatch, recs, on_hand, [])
    explicit_none = aggregate_projection(
        as_of=AS_OF,
        months=3,
        service_level=None,
        demand_multiplier=None,
        lead_time_multiplier=None,
    )
    assert calls == ONCE
    assert no_lever == explicit_none
    assert no_lever == [NO_LEVER]
    for bucket in no_lever[0]["buckets"]:
        assert set(bucket) == BUCKET_KEYS

    # --- demand_multiplier 1.5 (3 months) ----------------------------------
    # d' 15, lt' 20: the scenario reorder point is ceil(15 x 20) = 300.
    # January: the position 250 is below S 300 + 465 = 765, so a planned
    # order of 515 units arrives on 21 January (in the bucket).
    calls = _install(monkeypatch, recs, on_hand, [])
    demand_scaled = aggregate_projection(as_of=AS_OF, months=3, demand_multiplier=1.5)
    assert calls == ONCE
    assert demand_scaled == [DEMAND_1_5]
    january = demand_scaled[0]["buckets"][0]
    assert january["projected_demand_units"] == 465
    assert january["projected_demand_value"] == 930.0
    assert january["projected_on_hand_units"] == -215.0
    assert january["planned_receipts_units"] == 300 + 465 - 250 == 515
    assert january["planned_on_hand_units"] == 300.0

    # --- lead_time_multiplier 2 (3 months) ---------------------------------
    # d 10, lt' 40: the scenario reorder point is ceil(10 x 40) = 400.
    # January: 400 + 310 - 250 = 460 units, arriving 10 February, so
    # January receives nothing (planned on-hand -60).  February: the
    # position -60 + 460 = 400 is below 680, so it orders 280 arriving
    # 13 March (receipts 460, planned on-hand 120).  March: the
    # position 120 + 280 = 400 is below 710, so it orders 310 arriving
    # 10 April, after the window (receipts 280, planned on-hand 90).
    calls = _install(monkeypatch, recs, on_hand, [])
    lead_scaled = aggregate_projection(as_of=AS_OF, months=3, lead_time_multiplier=2.0)
    assert calls == ONCE
    assert lead_scaled == [LEAD_TIME_2]
    for as_is, scaled in zip(NO_LEVER["buckets"], lead_scaled[0]["buckets"]):
        assert scaled["projected_demand_units"] == as_is["projected_demand_units"]
        assert scaled["projected_demand_value"] == as_is["projected_demand_value"]
        assert scaled["projected_on_hand_units"] == as_is["projected_on_hand_units"]

    # --- service_level with sd_d 2 instead (baseline rop 215) --------------
    # 0.5: z = 0, so the scenario reorder point is ceil(200 + 0) = 200 and
    # the January planned order is S - position = 200 + 310 - 250 = 260
    # units.  0.95 given explicitly: ceil(200 + 1.6448536 x sqrt(80)) =
    # 215, so the January planned order is 215 + 310 - 250 = 275 units.
    sd2 = [_rec("I1", "S1", 215, 10, 2.0, 20, 2.0)]
    calls = _install(monkeypatch, sd2, on_hand, [])
    half = aggregate_projection(as_of=AS_OF, months=3, service_level=0.5)
    assert calls == ONCE
    assert half == [SL_0_5]
    assert half[0]["buckets"][0]["planned_receipts_units"] == 260
    calls = _install(monkeypatch, sd2, on_hand, [])
    ninety_five = aggregate_projection(as_of=AS_OF, months=3, service_level=0.95)
    assert calls == ONCE
    assert ninety_five == [SL_0_95]
    assert ninety_five[0]["buckets"][0]["planned_receipts_units"] == 275
    for as_is, scaled in zip(NO_LEVER["buckets"], half[0]["buckets"]):
        assert scaled["projected_demand_units"] == as_is["projected_demand_units"]
        assert scaled["projected_on_hand_units"] == as_is["projected_on_hand_units"]

    # --- equivalence with the scenario projection --------------------------
    # For representative lever combinations the scenario reorder point the
    # projection applies (shown through the January planned order, S =
    # rop + d' x 31 at position 250) equals the one simulate_reorder
    # reports for the same levers.
    for service_level, demand_multiplier, lead_time_multiplier in (
        (None, 1.5, None),
        (None, None, 2.0),
        (0.9, None, None),
        (0.9, 1.5, 2.0),
    ):
        calls = _install(monkeypatch, recs, on_hand, [])
        _install_simulate(monkeypatch, recs)
        simulation = simulate_reorder(
            service_level=service_level,
            demand_multiplier=demand_multiplier,
            lead_time_multiplier=lead_time_multiplier,
            as_of=AS_OF,
        )
        scenario_rop = simulation["item_sites"][0]["scenario"]["reorder_point_units"]
        rows = aggregate_projection(
            as_of=AS_OF,
            months=3,
            service_level=service_level,
            demand_multiplier=demand_multiplier,
            lead_time_multiplier=lead_time_multiplier,
        )
        assert calls == ONCE
        demand_factor = (
            float(demand_multiplier) if demand_multiplier is not None else 1.0
        )
        lead_factor = (
            float(lead_time_multiplier) if lead_time_multiplier is not None else 1.0
        )
        # The January order (position 250, S = scenario_rop + d' x 31)
        # arrives on the first of January plus ceil(lt') days: compare it
        # in the bucket whose month contains that date.
        expected_order = math.ceil(scenario_rop + 10.0 * demand_factor * 31 - 250.0)
        arrival = date(2026, 1, 1) + timedelta(days=math.ceil(20.0 * lead_factor))
        arrival_bucket = next(
            bucket
            for bucket in rows[0]["buckets"]
            if bucket["month"] == f"{arrival.year:04d}-{arrival.month:02d}"
        )
        assert arrival_bucket["planned_receipts_units"] == expected_order
    # The first two combinations pin the scenario semantics exactly:
    # ceil(15 x 20) = 300 and ceil(10 x 40) = 400.
    _install_simulate(monkeypatch, recs)
    assert (
        simulate_reorder(demand_multiplier=1.5, as_of=AS_OF)["item_sites"][0][
            "scenario"
        ]["reorder_point_units"]
        == 300
    )
    assert (
        simulate_reorder(lead_time_multiplier=2.0, as_of=AS_OF)["item_sites"][0][
            "scenario"
        ]["reorder_point_units"]
        == 400
    )

    # --- bounds through the app with the test Bearer -----------------------
    calls = _install(monkeypatch, recs, on_hand, [])
    response = client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 3})
    assert response.status_code == 200, response.text
    assert response.json() == [NO_LEVER]
    assert calls == ONCE
    calls = _install(monkeypatch, recs, on_hand, [])
    response = client.get(
        URL,
        params={"as_of": AS_OF.isoformat(), "months": 3, "demand_multiplier": "1.5"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == [DEMAND_1_5]
    assert calls == ONCE
    # An out-of-range lever is validated before anything is read: 422,
    # and the read helpers stay uncalled.
    calls = _install(monkeypatch, recs, on_hand, [])
    for params in (
        {"as_of": AS_OF.isoformat(), "months": 3, "service_level": "1"},
        {"as_of": AS_OF.isoformat(), "months": 3, "service_level": "0"},
        {"as_of": AS_OF.isoformat(), "months": 3, "demand_multiplier": "0"},
        {"as_of": AS_OF.isoformat(), "months": 3, "demand_multiplier": "-1"},
        {"as_of": AS_OF.isoformat(), "months": 3, "lead_time_multiplier": "0"},
    ):
        response = client.get(URL, params=params)
        assert response.status_code == 422, (params, response.text)
    assert calls == {"recommendations": 0, "on_hand": 0, "lines": 0}
    # A valid but huge multiplier reaches the reads (exactly once each)
    # and then overflows the formula: 422, never a 500.
    response = client.get(
        URL,
        params={"as_of": AS_OF.isoformat(), "months": 3, "demand_multiplier": "1e308"},
    )
    assert response.status_code == 422, response.text
    assert calls == ONCE
    # A future as_of and months above 12 are still 422.
    future = (date.today() + timedelta(days=1)).isoformat()
    assert client.get(URL, params={"as_of": future, "months": 3}).status_code == 422
    assert (
        client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 13}).status_code
        == 422
    )

    # --- the schema lists the three levers as optional query parameters ----
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    parameters = {
        parameter["name"]: parameter
        for parameter in spec["paths"][URL]["get"]["parameters"]
    }
    for name in ("service_level", "demand_multiplier", "lead_time_multiplier"):
        parameter = parameters[name]
        assert parameter["in"] == "query"
        assert parameter["required"] is False
        assert parameter["schema"]["anyOf"] == [{"type": "number"}, {"type": "null"}]
