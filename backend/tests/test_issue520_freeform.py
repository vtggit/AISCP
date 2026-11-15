"""Issue #520 proof: deferral candidates for the over-budget months.

AC-1 — ``app.aggregate.projection.planned_orders`` returns every
planned order the projection's monthly review places within the
horizon, in placement order: each ``item_code``, ``site_code``,
``placed_month``, ``arrival_month`` (null when the order arrives
after the horizon), ``quantity``, ``unit_cost`` (null when the
item-site has none), ``value`` (the quantity at that unit cost, 0
without one), ``d`` and ``month_end_on_hand`` (the item-site's own
planned on-hand at the end of the arrival month after that month's
receipts and demand, exactly as the review rolls it, null when the
arrival is outside the horizon).  It takes the same arguments with
the same validation and the same ``ValueError``s as
``aggregate_projection`` and is produced by the same review walk it
uses, refactored into a shared internal helper, so the two can never
disagree — ``aggregate_projection``'s output stays exactly what it
is (pinned below).  ``app.aggregate.deferral.deferral_candidates``
uses ``spend_against_budget`` for the month totals and budgets and
``planned_orders`` for the orders and returns ``{"months": [...]}``
with one entry per ``over_budget`` month: ``month``, ``overrun``
(``total_spend - budget``), ``candidates`` (the planned orders
arriving in the month, each ``item_code``, ``site_code``,
``quantity``, ``value``, ``cover_days_if_deferred`` (
``(month_end_on_hand - quantity) / d`` rounded to one decimal, null
when ``d`` is 0), ``would_stock_out`` (true when
``month_end_on_hand - quantity`` is below zero), ``suggested`` and
``cumulative_suggested_value``) ordered with the orders that would
not stock out first, by cover descending then value descending, then
the stock-out ones by cover descending, suggested in that order while
the suggested value is still below the overrun (an order that would
stock out, or has value 0, is never suggested), ``suggested_value``
(the sum of the suggested values), ``uncovered`` (
``max(0, overrun - suggested_value)``), ``next_month`` (the
following calendar month), ``next_month_total_after`` (that month's
``total_spend`` plus ``suggested_value``, null outside the horizon)
and ``next_month_over_after`` (true only when that month has a
budget and ``next_month_total_after`` exceeds it).  A deferral is
estimated one month at a time and later months are not re-planned
(the module docstring says so).  ``GET /api/aggregate/deferrals``
serves it with the same query parameters and bounds as
``GET /api/aggregate/spend``, 422 on a ``ValueError``, behind the
existing auth gate, and accepts no pagination parameters (AC-3):
every candidate of the requested month(s) comes back in a single
response.  ``backend/openapi.json`` follows.

AC-2 — proven below with the projection's three reads and the budget
list monkeypatched (no database), ``as_of`` 2026-01-15, 2 months and
no open lines.  The item-sites are: "A" at "S1" (rop 300, d 10, lt
20, unit_cost 2.0, on-hand 250), "B" at "S1" (rop 400, d 5, lt 10,
unit_cost 10.0, on-hand 300) and "C" at "S2" (rop 200, d 2, lt 5,
unit_cost 5.0, on-hand 100).  planned_orders: January A orders 360
(value 720.0) arriving January with month-end on-hand 300, B orders
255 (value 2550.0) arriving January with month-end on-hand 400, C
orders 162 (value 810.0) arriving January with month-end on-hand
200; February A orders 280, B 140 and C 56.  Budgets: January 3000
and February 4000; January's planned spend is 4080, an overrun of
1080, and deferral_candidates lists only January (February's 2240 is
under budget).  The candidates are in the order B, C, A: B has cover
29.0, would not stock out and is suggested, with cumulative 2550.0;
C has cover 19.0 and is not suggested, because the overrun is
already covered; A has cover -6.0, would stock out and is not
suggested.  suggested_value is 2550.0 and uncovered is 0;
next_month is "2026-02", next_month_total_after is 4790.0 and
next_month_over_after is true.  With January's budget at 1000
instead (overrun 3080), B and C are both suggested (cumulative
2550.0 then 3360.0), A is still never suggested, suggested_value is
3360.0 and uncovered is 0.  With January's budget at 500 and C's
unit cost set to null (January planned spend 3270, overrun 2770), B
is suggested, C has value 0 and is not suggested, A is not suggested,
suggested_value is 2550.0 and uncovered is 220.0.  aggregate_projection
on the same inputs returns exactly what it returned before this
change.  Through the app with the test Bearer, GET
/api/aggregate/deferrals?as_of=2026-01-15&months=2 answers 200 with
the January entry above, a request without a token answers 401, and
months=13 and service_level=1 answer 422.
"""

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.aggregate import projection
from app.aggregate import spend as spend_module
from app.aggregate.deferral import deferral_candidates
from app.aggregate.projection import aggregate_projection, planned_orders
from app.main import app

AS_OF = date(2026, 1, 15)
URL = "/api/aggregate/deferrals"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

# The projection's three reads, monkeypatched (no database).
ON_HAND = [
    {"item_code": "A", "site_code": "S1", "quantity_on_hand": 250},
    {"item_code": "B", "site_code": "S1", "quantity_on_hand": 300},
    {"item_code": "C", "site_code": "S2", "quantity_on_hand": 100},
]


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


def _recommendations(c_unit_cost=5.0):
    """The item-sites, in recommendation (item, site) order."""
    return [
        _rec("A", "S1", 300, 10, 0.0, 20, 2.0),
        _rec("B", "S1", 400, 5, 0.0, 10, 10.0),
        _rec("C", "S2", 200, 2, 0.0, 5, c_unit_cost),
    ]


def _budget_rows(january):
    """The stored budget rows, in the repository's month order."""
    return [
        {"month": "2026-01", "amount": january},
        {"month": "2026-02", "amount": 4000.0},
    ]


def _install(monkeypatch, recs, budgets):
    """Monkeypatch the projection's three reads and the budget list
    (no database) and count their calls."""
    state = {
        "recommendations": list(recs),
        "budgets": list(budgets),
        "calls": {"recommendations": 0, "on_hand": 0, "lines": 0, "budgets": 0},
    }

    def fake_recommendations(as_of=None):
        state["calls"]["recommendations"] += 1
        return list(state["recommendations"])

    def fake_list_rows(limit, offset, **filters):
        state["calls"]["on_hand"] += 1
        return list(ON_HAND)

    def fake_list_latest():
        state["calls"]["lines"] += 1
        return []

    def fake_budget_list():
        state["calls"]["budgets"] += 1
        return [dict(row) for row in state["budgets"]]

    monkeypatch.setattr(projection, "recommendations_for_all", fake_recommendations)
    monkeypatch.setattr(projection._on_hand_repo, "list_rows", fake_list_rows)
    monkeypatch.setattr(projection._open_po_repo, "list_latest", fake_list_latest)
    monkeypatch.setattr(spend_module._budget_repo, "list", fake_budget_list)
    return state


def _order(item, site, placed, arrival, quantity, unit_cost, value, d, month_end):
    return {
        "item_code": item,
        "site_code": site,
        "placed_month": placed,
        "arrival_month": arrival,
        "quantity": quantity,
        "unit_cost": unit_cost,
        "value": value,
        "d": d,
        "month_end_on_hand": month_end,
    }


# Placement order: bucket by bucket, the site's item-sites in
# recommendation order, the sites in site_code order.
PLANNED_ORDERS = [
    # January: A orders 360 (value 720.0) arriving January with
    # month-end on-hand 300; B orders 255 (value 2550.0) arriving
    # January with month-end on-hand 400; C orders 162 (value
    # 810.0) arriving January with month-end on-hand 200.
    _order("A", "S1", "2026-01", "2026-01", 360, 2.0, 720.0, 10.0, 300.0),
    _order("B", "S1", "2026-01", "2026-01", 255, 10.0, 2550.0, 5.0, 400.0),
    _order("C", "S2", "2026-01", "2026-01", 162, 5.0, 810.0, 2.0, 200.0),
    # February: A orders 280, B 140 and C 56.
    _order("A", "S1", "2026-02", "2026-02", 280, 2.0, 560.0, 10.0, 300.0),
    _order("B", "S1", "2026-02", "2026-02", 140, 10.0, 1400.0, 5.0, 400.0),
    _order("C", "S2", "2026-02", "2026-02", 56, 5.0, 280.0, 2.0, 200.0),
]


def _bucket(
    month, demand, on_hand, demand_value, receipts, receipts_value, planned_on_hand
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
        "planned_receipts_value": receipts_value,
        "planned_on_hand_units": planned_on_hand,
        "planned_shortfall": planned_on_hand < 0,
    }


# aggregate_projection on the same inputs: exactly what it returned
# before this change, group and field for field.
PROJECTED = [
    {
        "group": {"dimension": "site", "code": "S1"},
        "buckets": [
            _bucket("2026-01", 465, 85.0, 2170.0, 615, 3270.0, 700.0),
            _bucket("2026-02", 420, -335.0, 1960.0, 420, 1960.0, 700.0),
        ],
        "item_sites_without_unit_cost": 0,
    },
    {
        "group": {"dimension": "site", "code": "S2"},
        "buckets": [
            _bucket("2026-01", 62, 38.0, 310.0, 162, 810.0, 200.0),
            _bucket("2026-02", 56, -18.0, 280.0, 56, 280.0, 200.0),
        ],
        "item_sites_without_unit_cost": 0,
    },
]


def _candidate(item, site, quantity, value, cover, stock_out, suggested, cumulative):
    return {
        "item_code": item,
        "site_code": site,
        "quantity": quantity,
        "value": value,
        "cover_days_if_deferred": cover,
        "would_stock_out": stock_out,
        "suggested": suggested,
        "cumulative_suggested_value": cumulative,
    }


def _january(overrun, suggested_value, uncovered, total_after, over_after, candidates):
    return {
        "month": "2026-01",
        "overrun": overrun,
        "suggested_value": suggested_value,
        "uncovered": uncovered,
        "next_month": "2026-02",
        "next_month_total_after": total_after,
        "next_month_over_after": over_after,
        "candidates": candidates,
    }


# January 3000: planned spend 4080, overrun 1080.  The candidates are
# in the order B, C, A; B is suggested (cumulative 2550.0) and covers
# the overrun, so C and A are not suggested (A would stock out).
JANUARY = _january(
    1080.0,
    2550.0,
    0.0,
    4790.0,
    True,
    [
        _candidate("B", "S1", 255, 2550.0, 29.0, False, True, 2550.0),
        _candidate("C", "S2", 162, 810.0, 19.0, False, False, 2550.0),
        _candidate("A", "S1", 360, 720.0, -6.0, True, False, 2550.0),
    ],
)

# January 1000: overrun 3080; B and C are both suggested (cumulative
# 2550.0 then 3360.0); A is still never suggested.
JANUARY_BUDGET_1000 = _january(
    3080.0,
    3360.0,
    0.0,
    5600.0,
    True,
    [
        _candidate("B", "S1", 255, 2550.0, 29.0, False, True, 2550.0),
        _candidate("C", "S2", 162, 810.0, 19.0, False, True, 3360.0),
        _candidate("A", "S1", 360, 720.0, -6.0, True, False, 3360.0),
    ],
)

# January 500 and C without a unit cost: January planned spend 3270,
# overrun 2770; B is suggested, C has value 0 and is not suggested,
# A is not suggested, uncovered 220.0.
JANUARY_C_NO_COST = _january(
    2770.0,
    2550.0,
    220.0,
    # February's planned spend is 1960 without C's unit cost (its 56
    # units are worth 0), so 1960 + 2550.
    4510.0,
    True,
    [
        _candidate("B", "S1", 255, 2550.0, 29.0, False, True, 2550.0),
        _candidate("C", "S2", 162, 0.0, 19.0, False, False, 2550.0),
        _candidate("A", "S1", 360, 720.0, -6.0, True, False, 2550.0),
    ],
)


def test_issue520_freeform(client, monkeypatch):
    # --- planned orders: the January and February reviews --------------
    state = _install(monkeypatch, _recommendations(), _budget_rows(3000.0))
    assert planned_orders(as_of=AS_OF, months=2) == PLANNED_ORDERS
    # The three reads once, no budget read (the budgets are the
    # spend view's).
    assert state["calls"] == {
        "recommendations": 1,
        "on_hand": 1,
        "lines": 1,
        "budgets": 0,
    }

    # --- deferral candidates: only January is over budget --------------
    state["calls"] = {"recommendations": 0, "on_hand": 0, "lines": 0, "budgets": 0}
    assert deferral_candidates(as_of=AS_OF, months=2) == {"months": [JANUARY]}
    assert state["calls"] == {
        "recommendations": 1,
        "on_hand": 1,
        "lines": 1,
        "budgets": 1,
    }

    # --- the same arguments, the same validation -----------------------
    for kwargs in ({"months": 13}, {"as_of": date(2030, 1, 1)}, {"service_level": 1}):
        for function in (aggregate_projection, planned_orders, deferral_candidates):
            with pytest.raises(ValueError):
                function(**kwargs)

    # --- aggregate_projection stays exactly what it is -----------------
    assert aggregate_projection(as_of=AS_OF, months=2) == PROJECTED

    # --- January's budget at 1000: B and C both suggested --------------
    state["budgets"] = _budget_rows(1000.0)
    result = deferral_candidates(as_of=AS_OF, months=2)
    assert result == {"months": [JANUARY_BUDGET_1000]}

    # --- January's budget at 500 and C without a unit cost -------------
    state["recommendations"] = _recommendations(c_unit_cost=None)
    state["budgets"] = _budget_rows(500.0)
    result = deferral_candidates(as_of=AS_OF, months=2)
    assert result == {"months": [JANUARY_C_NO_COST]}

    # --- through the app with the test Bearer --------------------------
    state["recommendations"] = _recommendations()
    state["budgets"] = _budget_rows(3000.0)
    response = client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 2})
    assert response.status_code == 200, response.text
    # A single response, no pagination: the January entry above and
    # nothing else (February's 2240 is under budget).
    assert response.json() == {"months": [JANUARY]}

    # --- the mount-time Bearer gate -------------------------------------
    with TestClient(app) as anonymous:
        response = anonymous.get(URL, params={"as_of": AS_OF.isoformat(), "months": 2})
    assert response.status_code == 401, response.text
    assert response.headers.get("WWW-Authenticate") == "Bearer"

    # --- a ValueError answers 422, never a 500 --------------------------
    state["recommendations"] = []
    response = client.get(
        URL, params={"as_of": AS_OF.isoformat(), "months": 2, "service_level": "1"}
    )
    assert response.status_code == 422, response.text

    # --- the same bounds as the spend route ------------------------------
    response = client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 13})
    assert response.status_code == 422, response.text

    # --- the schema follows (AC-3: no pagination parameters) ------------
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    parameters = {
        parameter["name"]: parameter
        for parameter in spec["paths"][URL]["get"]["parameters"]
    }
    # The spend route's query parameters and the shared auth scheme,
    # nothing else: no pagination.
    assert set(parameters) == {
        "as_of",
        "months",
        "service_level",
        "demand_multiplier",
        "lead_time_multiplier",
        "authorization",
    }
    for name in (
        "as_of",
        "months",
        "service_level",
        "demand_multiplier",
        "lead_time_multiplier",
    ):
        assert parameters[name]["in"] == "query"
        assert parameters[name]["required"] is False
    months_parameter = parameters["months"]
    assert months_parameter["schema"]["minimum"] == 1
    assert months_parameter["schema"]["maximum"] == 12
