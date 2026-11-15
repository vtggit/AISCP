"""Issue #508 proof: the projected spend against the stored budgets.

AC-1 — ``app.aggregate.spend.spend_against_budget`` summarizes the
site-grouped roll-forward projection as a monthly spend view against
the per-month purchase budgets: over the same calendar-month horizon
the projection covers (the buckets
``app.aggregate.projection._bucket_months`` gives from the month of
the resolved ``as_of``, today when it is None) it sums every group's
``projected_supply_value`` into ``committed_spend`` and its
``planned_receipts_value`` into ``planned_spend``, compares the total
against the stored budget of the month (null without one) and flags
the months where the total spend exceeds it.  The projection is
called exactly once with the same arguments and the budgets are read
exactly once through
``PurchaseBudgetPostgresRepository.list``; with no groups every month
of the horizon still appears with zero spend, and every ``ValueError``
the projection raises propagates unchanged.  ``GET
/api/aggregate/spend`` serves it with the same query parameters and
bounds as ``GET /api/aggregate/projection``, answering 422 on a
``ValueError`` behind the router's existing auth gate, and
``backend/openapi.json`` reflects the route.

AC-2 — proven below with ``aggregate_projection`` and the budget
repository's ``list`` monkeypatched in ``app.aggregate.spend`` (no
database).  The projection stub records its arguments and returns two
groups over the months 2026-01, 2026-02 and 2026-03: "S1" has
``projected_supply_value`` 100, 0, 50 and
``planned_receipts_value`` 200, 300, 0, with
``item_sites_without_unit_cost`` 1; "S2" has
``projected_supply_value`` 0, 25, 0 and
``planned_receipts_value`` 10, 0, 40, with
``item_sites_without_unit_cost`` 2.  The stored budgets are 2026-01 at
400, 2026-02 at 300 and 2025-12 at 999 (outside the window, ignored).
January has committed 100, planned 210, total 310, budget 400,
remaining 90 and over_budget false; February has committed 25,
planned 300, total 325, budget 300, remaining -25 and over_budget
true; March has committed 50, planned 40, total 90, budget null,
remaining null and over_budget false;
``item_sites_without_unit_cost`` is 3.  ``months=3``, ``as_of``
2026-01-15 and ``demand_multiplier=1.2`` reach the projection stub
unchanged, and each stub is called exactly once.  With an empty
projection, every month is present with zero spend.  Through the app
with the test Bearer, ``GET /api/aggregate/spend?as_of=2026-01-15&
months=3`` answers 200 with exactly those values; a request without a
token is 401; a stub raising ``ValueError`` answers 422; and
``months=13`` and ``service_level=1`` answer 422.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.aggregate import spend as spend_module
from app.main import app
from app.scenario.simulate import _validate_levers

URL = "/api/aggregate/spend"
AS_OF = date(2026, 1, 15)
MONTHS = ("2026-01", "2026-02", "2026-03")


def _group(code, supply_values, planned_values, without_unit_cost):
    """One site group's buckets over 2026-01, 2026-02 and 2026-03."""
    return {
        "group": {"dimension": "site", "code": code},
        "buckets": [
            {
                "month": month,
                "projected_demand_units": 0,
                "projected_supply_units": 0.0,
                "projected_on_hand_units": 0.0,
                "projected_shortfall": False,
                "projected_demand_value": 0.0,
                "projected_supply_value": supply_value,
                "planned_receipts_units": 0,
                "planned_receipts_value": planned_value,
                "planned_on_hand_units": 0.0,
                "planned_shortfall": False,
            }
            for month, supply_value, planned_value in zip(
                MONTHS, supply_values, planned_values
            )
        ],
        "item_sites_without_unit_cost": without_unit_cost,
    }


GROUPS = [
    _group("S1", (100, 0, 50), (200, 300, 0), 1),
    _group("S2", (0, 25, 0), (10, 0, 40), 2),
]

EXPECTED = {
    "months": [
        {
            "month": "2026-01",
            "committed_spend": 100.0,
            "planned_spend": 210.0,
            "total_spend": 310.0,
            "budget": 400.0,
            "remaining": 90.0,
            "over_budget": False,
        },
        {
            "month": "2026-02",
            "committed_spend": 25.0,
            "planned_spend": 300.0,
            "total_spend": 325.0,
            "budget": 300.0,
            "remaining": -25.0,
            "over_budget": True,
        },
        {
            "month": "2026-03",
            "committed_spend": 50.0,
            "planned_spend": 40.0,
            "total_spend": 90.0,
            "budget": None,
            "remaining": None,
            "over_budget": False,
        },
    ],
    "item_sites_without_unit_cost": 3,
}

EMPTY_EXPECTED = {
    "months": [
        {
            "month": "2026-01",
            "committed_spend": 0.0,
            "planned_spend": 0.0,
            "total_spend": 0.0,
            "budget": 400.0,
            "remaining": 400.0,
            "over_budget": False,
        },
        {
            "month": "2026-02",
            "committed_spend": 0.0,
            "planned_spend": 0.0,
            "total_spend": 0.0,
            "budget": 300.0,
            "remaining": 300.0,
            "over_budget": False,
        },
        {
            "month": "2026-03",
            "committed_spend": 0.0,
            "planned_spend": 0.0,
            "total_spend": 0.0,
            "budget": None,
            "remaining": None,
            "over_budget": False,
        },
    ],
    "item_sites_without_unit_cost": 0,
}

# The stored budgets, in the repository's month order: 2025-12 is
# outside the 2026-01 to 2026-03 window and must be ignored.
BUDGETS = [
    {"month": "2025-12", "amount": 999.0},
    {"month": "2026-01", "amount": 400.0},
    {"month": "2026-02", "amount": 300.0},
]


def _install(monkeypatch, state):
    """Monkeypatch the projection and the budget list in
    ``app.aggregate.spend`` (no database) and count their calls."""
    calls = {"projection": 0, "budgets": 0}
    recorded = {}

    def fake_projection(
        as_of=None,
        months=6,
        service_level=None,
        demand_multiplier=None,
        lead_time_multiplier=None,
    ):
        calls["projection"] += 1
        recorded["kwargs"] = {
            "as_of": as_of,
            "months": months,
            "service_level": service_level,
            "demand_multiplier": demand_multiplier,
            "lead_time_multiplier": lead_time_multiplier,
        }
        if state["raise"] is not None:
            raise state["raise"]
        # The real projection validates the levers before anything is
        # read; the stub mirrors that so the route's 422 bounds hold
        # with the projection stubbed.
        _validate_levers(service_level, demand_multiplier, lead_time_multiplier)
        if not isinstance(months, int) or isinstance(months, bool):
            raise ValueError("months must be an integer between 1 and 12")
        if not (1 <= months <= 12):
            raise ValueError("months must be between 1 and 12")
        return list(state["groups"])

    def fake_budget_list():
        calls["budgets"] += 1
        return [dict(row) for row in BUDGETS]

    monkeypatch.setattr(spend_module, "aggregate_projection", fake_projection)
    monkeypatch.setattr(spend_module._budget_repo, "list", fake_budget_list)
    return calls, recorded


def test_issue508_freeform(client, monkeypatch):
    state = {"groups": list(GROUPS), "raise": None}
    calls, recorded = _install(monkeypatch, state)

    # --- the direct call: the values and the passthrough arguments ----
    result = spend_module.spend_against_budget(
        as_of=AS_OF,
        months=3,
        service_level=None,
        demand_multiplier=1.2,
        lead_time_multiplier=None,
    )
    assert result == EXPECTED
    assert recorded["kwargs"] == {
        "as_of": AS_OF,
        "months": 3,
        "service_level": None,
        "demand_multiplier": 1.2,
        "lead_time_multiplier": None,
    }
    assert calls == {"projection": 1, "budgets": 1}

    # --- an empty projection: every month present with zero spend ----
    state["groups"] = []
    calls["projection"] = 0
    calls["budgets"] = 0
    result = spend_module.spend_against_budget(as_of=AS_OF, months=3)
    assert result == EMPTY_EXPECTED
    assert calls == {"projection": 1, "budgets": 1}
    state["groups"] = list(GROUPS)

    # --- a projection ValueError propagates unchanged ------------------
    state["raise"] = ValueError("as_of cannot be after the current server date")
    calls["projection"] = 0
    calls["budgets"] = 0
    with pytest.raises(
        ValueError, match="as_of cannot be after the current server date"
    ):
        spend_module.spend_against_budget(as_of=AS_OF, months=3)
    assert calls == {"projection": 1, "budgets": 0}
    state["raise"] = None

    # --- through the app with the test Bearer --------------------------
    calls["projection"] = 0
    calls["budgets"] = 0
    response = client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 3})
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED
    assert calls == {"projection": 1, "budgets": 1}

    # --- the mount-time Bearer gate -------------------------------------
    with TestClient(app) as anonymous:
        response = anonymous.get(URL, params={"as_of": AS_OF.isoformat(), "months": 3})
    assert response.status_code == 401, response.text
    assert response.headers.get("WWW-Authenticate") == "Bearer"

    # --- a stub raising ValueError answers 422, never a 500 ------------
    state["raise"] = ValueError("boom")
    response = client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 3})
    assert response.status_code == 422, response.text
    assert response.json()["detail"] == "boom"
    state["raise"] = None

    # --- the same bounds as the projection route ------------------------
    response = client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 13})
    assert response.status_code == 422, response.text
    response = client.get(
        URL, params={"as_of": AS_OF.isoformat(), "months": 3, "service_level": "1"}
    )
    assert response.status_code == 422, response.text
