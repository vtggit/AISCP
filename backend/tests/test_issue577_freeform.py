"""Issue #577 proof: in-flight releases count as supply.

AC-1 — the projection's setup reads the in-flight releases through
the module-level ``app.aggregate.projection._in_flight_release_lines``
(one read) and turns the in-flight rows — ``plan_releases`` rows with
``outcome`` "created" or "exists", a non-null ``order_nbr`` and no
row of the newest ``open_purchase_lines`` snapshot whose
``business_key`` order number (its second ``:``-separated part)
equals that ``order_nbr`` — into on-order supply: each line
(``item_code``, ``quantity``, ``unit_cost``, at the row's
``site_code``) arrives on the first day of the row's ``month`` (the
projection's start date when that day is earlier), is valued at
``quantity x unit_cost`` and from there behaves exactly like an open
purchase line — it counts in the monthly supply, the review walk no
longer plans an order for that need, its value lands in the bucket's
supply value, ``spend_against_budget`` reports it as committed spend
and ``planned_orders``, ``plan_release`` and
``deferral_candidates`` see the same supply.  A released order the
feed already carries is counted from the feed only, never twice, and
a row whose item-site is not recommendable is ignored.  The public
signatures are unchanged.

AC-2 — proven below with the projection fakes of
``tests/test_issue520_freeform.py`` plus two monkeypatched fakes:
``_in_flight_release_lines`` and the ``open_purchase_lines`` snapshot
so it carries order numbers.  The item-site is "A" at "S1" (rop 530,
d 10, lt 20, unit_cost 2.0, on-hand 0), ``as_of`` 2026-01-15, 2
months, no levers.  With no supply the January review places a
planned order of 840 units (530 + 10 x 31 - 0) arriving in the first
month, value 1680.0.  With an in-flight release of 1479 units at
unit cost 3.0 for the item-site in January (order PO003077),
``planned_orders()`` contains no order, ``spend_against_budget()``
moves 4437.0 (1479 x 3.0) into that month's ``committed_spend`` and
its ``planned_spend`` drops by the planned order's value.  The same
release plus an open purchase line whose ``business_key`` is
"RO:PO003077:1" for that order is counted once, from the feed, with
the projection identical to the feed-only case.  Releases with
``outcome`` "error" or "aborted", or with a null ``order_nbr``,
change nothing.
"""

from datetime import date

from app.aggregate import projection
from app.aggregate import spend as spend_module
from app.aggregate.projection import aggregate_projection, planned_orders
from app.aggregate.spend import spend_against_budget

AS_OF = date(2026, 1, 15)

# The projection fakes, the same reads
# tests/test_issue520_freeform.py fakes (no database).
ON_HAND = [{"item_code": "A", "site_code": "S1", "quantity_on_hand": 0}]


def _rec():
    """The one recommendable item-site: rop 530, d 10, lt 20,
    unit_cost 2.0, on-hand 0.  With no supply the January review
    places 840 units (530 + 10 x 31 - 0), arriving January 21."""
    return {
        "status": "ok",
        "item_code": "A",
        "site_code": "S1",
        "reorder_point": 529.5,
        "reorder_point_units": 530,
        "safety_stock": 0.0,
        "safety_stock_units": 0,
        "unit_cost": 2.0,
        "inputs": {
            "d": 10.0,
            "sd_d": 0.0,
            "lt": 20.0,
            "sd_lt": 0.0,
            "z": 1.6448536269514722,
            "service_level": 0.95,
        },
    }


def _release_row(outcome, order_nbr, month="2026-01"):
    """One stored plan-release row as the one read returns it: 1479
    units of "A" at unit cost 3.0 (value 4437.0) for site "S1"."""
    return {
        "id": 1,
        "month": month,
        "site_code": "S1",
        "supplier_code": "V1",
        "origin": "AIERP plan 2026-01 site S1 vendor V1",
        "warehouse_code": "WH1",
        "vendor_code": "V1",
        "order_nbr": order_nbr,
        "outcome": outcome,
        "error": None,
        "line_count": 1,
        "value": 4437.0,
        "lines": [
            {
                "item_code": "A",
                "quantity": 1479,
                "unit_cost": "3.0",
                "value": "4437.0",
            }
        ],
        "actor": "admin",
        "created_at": "2026-01-15T00:00:00+00:00",
        "updated_at": "2026-01-15T00:00:00+00:00",
    }


def _open_line():
    """The open purchase line of the released order: business_key
    "RO:PO003077:1", 1479 open units promised in January."""
    return {
        "id": "open-line-1",
        "business_key": "RO:PO003077:1",
        "item_code": "A",
        "site_code": "S1",
        "supplier_code": "V1",
        "order_type": "RO",
        "open_units": 1479.0,
        "promised_on": date(2026, 1, 15),
        "ordered_on": date(2026, 1, 15),
        "as_of": "2026-01-15T00:00:00+00:00",
        "source_system": "acumatica",
        "synced_at": "2026-01-15T00:00:00+00:00",
    }


def _install(monkeypatch, releases, open_lines):
    """Monkeypatch the projection's reads (no database) and count
    their calls, the way test_issue520_freeform.py does — plus the
    in-flight release read (``_in_flight_release_lines``) and the
    ``open_purchase_lines`` snapshot with its order numbers."""
    state = {
        "releases": list(releases),
        "open_lines": list(open_lines),
        "calls": {
            "recommendations": 0,
            "on_hand": 0,
            "lines": 0,
            "releases": 0,
            "budgets": 0,
        },
    }

    def fake_recommendations(as_of=None):
        state["calls"]["recommendations"] += 1
        return [_rec()]

    def fake_list_rows(limit, offset, **filters):
        state["calls"]["on_hand"] += 1
        return list(ON_HAND)

    def fake_list_latest():
        state["calls"]["lines"] += 1
        return [dict(line) for line in state["open_lines"]]

    def fake_release_lines(as_of=None):
        state["calls"]["releases"] += 1
        return [
            dict(row) if isinstance(row, dict) else row for row in state["releases"]
        ]

    def fake_budget_list():
        state["calls"]["budgets"] += 1
        return []

    monkeypatch.setattr(projection, "recommendations_for_all", fake_recommendations)
    monkeypatch.setattr(projection._on_hand_repo, "list_rows", fake_list_rows)
    monkeypatch.setattr(projection._open_po_repo, "list_latest", fake_list_latest)
    monkeypatch.setattr(projection, "_in_flight_release_lines", fake_release_lines)
    monkeypatch.setattr(spend_module._budget_repo, "list", fake_budget_list)
    return state


def _order(placed, arrival, quantity, value, month_end):
    return {
        "item_code": "A",
        "site_code": "S1",
        "placed_month": placed,
        "arrival_month": arrival,
        "quantity": quantity,
        "unit_cost": 2.0,
        "value": value,
        "d": 10.0,
        "month_end_on_hand": month_end,
    }


# With no supply: January's order of 840 (value 1680.0) arriving
# January 21 and February's 280 (value 560.0).
NO_SUPPLY_ORDERS = [
    _order("2026-01", "2026-01", 840, 1680.0, 530.0),
    _order("2026-02", "2026-02", 280, 560.0, 530.0),
]


def test_issue577_freeform(monkeypatch):
    state = _install(monkeypatch, [], [])

    # --- the no-supply baseline: the January planned order of 840 ----
    assert planned_orders(as_of=AS_OF, months=2) == NO_SUPPLY_ORDERS
    # The setup's reads: the three reads issue 520 fakes plus exactly
    # one in-flight release read, through the helper.
    assert state["calls"] == {
        "recommendations": 1,
        "on_hand": 1,
        "lines": 1,
        "releases": 1,
        "budgets": 0,
    }
    baseline = aggregate_projection(as_of=AS_OF, months=2)
    spend = spend_against_budget(as_of=AS_OF, months=2)
    assert spend["months"][0]["committed_spend"] == 0.0
    assert spend["months"][0]["planned_spend"] == 1680.0

    # --- the planned order disappears ---------------------------------
    # The in-flight release: 1479 units at unit cost 3.0 for the
    # item-site in January (order PO003077), written created.  Its
    # line arrives on the first day of the month, earlier than the
    # projection's start date, so on 2026-01-15.
    state["releases"] = [_release_row("created", "PO003077")]
    assert planned_orders(as_of=AS_OF, months=2) == []
    # spend_against_budget moves 4437.0 (1479 x 3.0, the line's own
    # unit cost, not the item-site's 2.0) into January's
    # committed_spend; its planned_spend drops by the planned
    # order's value, 1680.0, to 0.0.
    spend = spend_against_budget(as_of=AS_OF, months=2)
    january, february = spend["months"]
    assert january["committed_spend"] == 4437.0
    assert january["planned_spend"] == 0.0
    assert january["total_spend"] == 4437.0
    assert february["committed_spend"] == 0.0
    # The supply line in the projection: January's supply units and
    # value, the rolled on-hand, no planned receipts any more.
    buckets = aggregate_projection(as_of=AS_OF, months=2)[0]["buckets"]
    assert buckets[0]["projected_supply_units"] == 1479.0
    assert buckets[0]["projected_supply_value"] == 4437.0
    assert buckets[0]["projected_on_hand_units"] == 1169.0
    assert buckets[0]["planned_receipts_units"] == 0
    assert buckets[1]["projected_supply_units"] == 0.0
    assert buckets[1]["projected_on_hand_units"] == 889.0

    # --- no double count -----------------------------------------------
    # The feed-only case: the open purchase line of the order.
    state["releases"] = []
    state["open_lines"] = [_open_line()]
    feed_only = aggregate_projection(as_of=AS_OF, months=2)
    assert planned_orders(as_of=AS_OF, months=2) == []
    assert (
        spend_against_budget(as_of=AS_OF, months=2)["months"][0]["committed_spend"]
        == 1479.0 * 2.0
    )  # the feed line at the item-site's unit cost
    # The same release plus the line: counted once, from the feed —
    # the projection is identical to the feed-only case.
    state["releases"] = [_release_row("created", "PO003077")]
    assert aggregate_projection(as_of=AS_OF, months=2) == feed_only
    assert planned_orders(as_of=AS_OF, months=2) == []

    # --- ignored rows ----------------------------------------------------
    # error, aborted and a null order_nbr: none of them is in-flight.
    state["open_lines"] = []
    for outcome, order_nbr in (
        ("error", "PO003077"),
        ("aborted", "PO003078"),
        ("created", None),
    ):
        state["releases"] = [_release_row(outcome, order_nbr)]
        assert aggregate_projection(as_of=AS_OF, months=2) == baseline
        assert planned_orders(as_of=AS_OF, months=2) == NO_SUPPLY_ORDERS
        spend = spend_against_budget(as_of=AS_OF, months=2)
        assert spend["months"][0]["committed_spend"] == 0.0
        assert spend["months"][0]["planned_spend"] == 1680.0

    # --- hostile rows never raise (never a 500) --------------------------
    state["releases"] = [
        dict(_release_row("created", "PO003079"), month="2026-13"),
        dict(_release_row("created", "PO003080"), month="not-a-month"),
        dict(
            _release_row("created", "PO003081"),
            lines=[
                {"item_code": "A", "quantity": "abc", "unit_cost": "3.0"},
                {"item_code": "A", "quantity": None, "unit_cost": 3.0},
                {
                    "item_code": "A",
                    "quantity": "1e999999999999999999",
                    "unit_cost": 3.0,
                },
                {"quantity": 5, "unit_cost": 3.0},
                "not-a-line",
                {"item_code": 7, "quantity": 5, "unit_cost": 3.0},
            ],
        ),
        dict(_release_row("created", "PO003082"), lines="not-a-list"),
        "not-a-row",
    ]
    assert aggregate_projection(as_of=AS_OF, months=2) == baseline
    assert planned_orders(as_of=AS_OF, months=2) == NO_SUPPLY_ORDERS
