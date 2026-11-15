"""Issue #521 proof: one projection setup per deferral-candidates call.

AC-1 — ``app.aggregate.deferral.deferral_candidates`` runs
``app.aggregate.projection._projection_setup`` exactly once (so
``recommendations_for_all`` and the other projection reads each
happen once) and feeds that one setup to both the spend computation
(``app.aggregate.spend._spend_view`` over
``app.aggregate.projection._aggregate_projection_from_setup``) and
the planned-order list
(``app.aggregate.projection._planned_orders_from_setup``).  The
public ``aggregate_projection``, ``planned_orders`` and
``spend_against_budget`` are thin wrappers over the setup-taking
helpers with unchanged signatures, validation, ``ValueError``s and
results, and the setup is never mutated by either consumer: the
review walk mutates its state, so each consumer walks its own copy.

AC-2 — proven below with the same repository fakes as
``tests/test_issue520_freeform.py`` (an over-budget month with
planned orders arriving in it, and a scenario lever case), the
projection's three reads and the budget list monkeypatched (no
database), ``as_of`` 2026-01-15 and 2 months: ``deferral_candidates()``
returns exactly what the pre-change composition returns, where that
composition is computed in the test from the public
``spend_against_budget`` and ``planned_orders`` with the same
arguments; a counting wrapper monkeypatched over
``app.aggregate.projection._projection_setup`` sees exactly one call
per ``deferral_candidates()`` call, and one per
``spend_against_budget()`` or ``planned_orders()`` call;
``aggregate_projection()``, ``planned_orders()`` and
``spend_against_budget()`` give the same results before and after a
``deferral_candidates()`` call on the same fakes, so no shared state
leaks; and an invalid ``months`` value (0) and an out-of-range lever
still raise the same ``ValueError`` from ``deferral_candidates``.
"""

import copy
from datetime import date

import pytest

from app.aggregate import projection
from app.aggregate import spend as spend_module
from app.aggregate.deferral import (
    _cover_sort_key,
    _next_month_label,
    deferral_candidates,
)
from app.aggregate.projection import aggregate_projection, planned_orders
from app.aggregate.spend import spend_against_budget

AS_OF = date(2026, 1, 15)
URL = "/api/aggregate/deferrals"

# The projection's three reads, monkeypatched (no database) — the
# same fakes tests/test_issue520_freeform.py uses: an over-budget
# month (January) with planned orders arriving in it.
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


def _count_setup_calls(monkeypatch):
    """Monkeypatch ``app.aggregate.projection._projection_setup``
    with a counting wrapper over the original and return the counter."""
    original = projection._projection_setup
    counts = {"calls": 0}

    def counting(*args, **kwargs):
        counts["calls"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(projection, "_projection_setup", counting)
    return counts


def _pre_change_deferrals(
    as_of,
    months,
    service_level=None,
    demand_multiplier=None,
    lead_time_multiplier=None,
):
    """The pre-change composition, computed in the test from the
    public ``spend_against_budget`` and ``planned_orders`` with the
    same arguments (two setups, the pre-change way): the same
    candidate ordering, suggestion and next-month arithmetic the
    pre-change ``deferral_candidates`` applied to them."""
    spend = spend_against_budget(
        as_of=as_of,
        months=months,
        service_level=service_level,
        demand_multiplier=demand_multiplier,
        lead_time_multiplier=lead_time_multiplier,
    )
    orders = planned_orders(
        as_of=as_of,
        months=months,
        service_level=service_level,
        demand_multiplier=demand_multiplier,
        lead_time_multiplier=lead_time_multiplier,
    )
    month_by_label = {row["month"]: row for row in spend["months"]}
    deferral_months: list[dict] = []
    for row in spend["months"]:
        if not row["over_budget"]:
            continue
        budget = row["budget"]
        overrun = row["total_spend"] - budget
        candidates: list[dict] = []
        for order in orders:
            if order["arrival_month"] != row["month"]:
                continue
            d = order["d"]
            month_end_on_hand = order["month_end_on_hand"]
            cover = round((month_end_on_hand - order["quantity"]) / d, 1) if d else None
            would_stock_out = (month_end_on_hand - order["quantity"]) < 0
            candidates.append(
                {
                    "item_code": order["item_code"],
                    "site_code": order["site_code"],
                    "quantity": order["quantity"],
                    "value": order["value"],
                    "cover_days_if_deferred": cover,
                    "would_stock_out": would_stock_out,
                }
            )
        candidates.sort(
            key=lambda candidate: (
                candidate["would_stock_out"],
                _cover_sort_key(candidate["cover_days_if_deferred"]),
                0 if candidate["would_stock_out"] else -candidate["value"],
            )
        )
        suggested_value = 0.0
        for candidate in candidates:
            suggested = (
                not candidate["would_stock_out"]
                and candidate["value"] > 0
                and suggested_value < overrun
            )
            if suggested:
                suggested_value += candidate["value"]
            candidate["suggested"] = suggested
            candidate["cumulative_suggested_value"] = suggested_value
        next_label = _next_month_label(row["month"])
        next_row = month_by_label.get(next_label)
        next_total = (
            None if next_row is None else next_row["total_spend"] + suggested_value
        )
        next_over = (
            next_total is not None
            and next_row["budget"] is not None
            and next_total > next_row["budget"]
        )
        deferral_months.append(
            {
                "month": row["month"],
                "overrun": overrun,
                "suggested_value": suggested_value,
                "uncovered": max(0.0, overrun - suggested_value),
                "next_month": next_label,
                "next_month_total_after": next_total,
                "next_month_over_after": next_over,
                "candidates": candidates,
            }
        )
    return {"months": deferral_months}


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


# The pinned pre-change answer: January alone over budget (planned
# spend 4080 against 3000, overrun 1080); B is suggested and covers
# the overrun, so C and A are not suggested (A would stock out).
JANUARY = {
    "month": "2026-01",
    "overrun": 1080.0,
    "suggested_value": 2550.0,
    "uncovered": 0.0,
    "next_month": "2026-02",
    "next_month_total_after": 4790.0,
    "next_month_over_after": True,
    "candidates": [
        _candidate("B", "S1", 255, 2550.0, 29.0, False, True, 2550.0),
        _candidate("C", "S2", 162, 810.0, 19.0, False, False, 2550.0),
        _candidate("A", "S1", 360, 720.0, -6.0, True, False, 2550.0),
    ],
}


def test_issue521_freeform(client, monkeypatch):
    state = _install(monkeypatch, _recommendations(), _budget_rows(3000.0))
    counts = _count_setup_calls(monkeypatch)

    # --- the pre-change composition, computed in the test from the
    #     public spend view and planned orders (two setups, the
    #     pre-change way) -----------------------------------------------
    expected = _pre_change_deferrals(as_of=AS_OF, months=2)
    assert counts["calls"] == 2
    # The pinned pre-change answer: only January is over budget.
    assert expected == {"months": [JANUARY]}

    # --- deferral_candidates: the same answer, from one setup ----------
    counts["calls"] = 0
    state["calls"] = {"recommendations": 0, "on_hand": 0, "lines": 0, "budgets": 0}
    assert deferral_candidates(as_of=AS_OF, months=2) == expected
    assert counts["calls"] == 1
    assert state["calls"] == {
        "recommendations": 1,
        "on_hand": 1,
        "lines": 1,
        "budgets": 1,
    }

    # --- one setup per public wrapper call ------------------------------
    for function in (aggregate_projection, planned_orders, spend_against_budget):
        counts["calls"] = 0
        function(as_of=AS_OF, months=2)
        assert counts["calls"] == 1

    # --- the setup-taking helpers: same results, the setup is never
    #     mutated (each consumer walks its own copy) ---------------------
    setup = projection._projection_setup(AS_OF, 2, None, None, None)
    pristine = copy.deepcopy(setup)
    groups = projection._aggregate_projection_from_setup(setup)
    orders = projection._planned_orders_from_setup(setup)
    assert setup == pristine
    assert groups == aggregate_projection(as_of=AS_OF, months=2)
    assert orders == planned_orders(as_of=AS_OF, months=2)

    # --- no shared state leaks across calls -----------------------------
    before = (
        aggregate_projection(as_of=AS_OF, months=2),
        planned_orders(as_of=AS_OF, months=2),
        spend_against_budget(as_of=AS_OF, months=2),
    )
    deferral_candidates(as_of=AS_OF, months=2)
    after = (
        aggregate_projection(as_of=AS_OF, months=2),
        planned_orders(as_of=AS_OF, months=2),
        spend_against_budget(as_of=AS_OF, months=2),
    )
    assert after == before

    # --- the scenario lever case: the same answer, from one setup ------
    lever = {"as_of": AS_OF, "months": 2, "demand_multiplier": 3.0}
    expected_lever = _pre_change_deferrals(**lever)
    counts["calls"] = 0
    assert deferral_candidates(**lever) == expected_lever
    assert counts["calls"] == 1
    # The tripled demand overruns both months and every candidate
    # would stock out, so none is suggested and the overruns stay
    # uncovered.
    assert [row["month"] for row in expected_lever["months"]] == [
        "2026-01",
        "2026-02",
    ]
    january, february = expected_lever["months"]
    assert [candidate["item_code"] for candidate in january["candidates"]] == [
        "B",
        "C",
        "A",
    ]
    assert all(candidate["would_stock_out"] for candidate in january["candidates"])
    assert not any(candidate["suggested"] for candidate in january["candidates"])
    assert january["suggested_value"] == 0.0
    assert january["uncovered"] == january["overrun"] == 3290.0
    assert january["next_month_total_after"] == 6720.0
    assert january["next_month_over_after"] is True
    assert [candidate["item_code"] for candidate in february["candidates"]] == [
        "A",
        "B",
        "C",
    ]
    assert february["uncovered"] == february["overrun"] == 2720.0
    assert february["next_month"] == "2026-03"
    assert february["next_month_total_after"] is None
    assert february["next_month_over_after"] is False

    # --- the same validation: the same ValueErrors ----------------------
    for kwargs in ({"months": 0}, {"service_level": 1}):
        with pytest.raises(ValueError) as reference:
            aggregate_projection(as_of=AS_OF, **kwargs)
        with pytest.raises(ValueError) as excinfo:
            deferral_candidates(as_of=AS_OF, **kwargs)
        assert str(excinfo.value) == str(reference.value)

    # --- the route's answer is the function's answer --------------------
    response = client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 2})
    assert response.status_code == 200, response.text
    assert response.json() == deferral_candidates(as_of=AS_OF, months=2)
