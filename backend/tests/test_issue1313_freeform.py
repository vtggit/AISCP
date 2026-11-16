"""Issue #1313 proof: a stockout row's remedy.

``GET /api/stockout`` takes ``include_remedy``, a boolean, false by
default.  While it is false the route answers exactly as before: no
row carries a ``remedy`` key and the expedite list, the transfer
suggestions and the order plan are not read.  While it is true and at
least one row is on the page, the route reads each of the three once —
``expedite_list`` with no as-of, so it keeps its own default (today),
the way the exceptions inbox reads it, and
``build_transfer_suggestions`` and ``build_supply_plan`` at the rows'
as-of, the order plan at its 7-day horizon — the ranking and the three
reads inside one ``recommendations_memo``, so the recommendations are
computed at most once per as-of.  Every returned row then carries
``remedy``, a ``StockoutRemedy`` (``kind``, ``order_type``,
``order_nbr``, ``line_nbr``, ``expected_on``, ``from_site``,
``supplier_code``, ``supplier_name``, ``order_by``, ``units`` and
``reason``, every key that does not apply to the kind being null)
proposed by the first kind that holds a candidate for the row's
item-site — the kinds tried in the order the effective
``decisions.remedy_order`` system setting names, expedite, then
transfer, then raise an order with the default setting — or null when
no kind does; a kind whose read raised stops the search at that kind,
so a remedy later in the order is never proposed because an earlier
one could not be checked, and a read that raises never fails the list.
``StockoutRow`` itself gains nothing, and the setting is registered
the way ``replenishment.demand_basis`` is, with the six orders of the
three kinds allowed.

Everything is read through the app's test client, signed in the way
test 1010 signs in, with every read of the route replaced by a
stand-in in ``app.api.stockout`` that answers fixed data at as-of
2026-11-02 and records its arguments and whether the
recommendations memo was active when it was called, so no test reads
the wall clock:

* six stockout rows — items A, B, C, D, E and F, all at site 159,
  ranks 1 to 6, every ``StockoutRow`` field filled;
* the current-decisions read (``_repo.current_all``) and the
  item-descriptions read (``_item_descriptions``) answer nothing;
* the expedite list answers four lines, in order: E at 159, late,
  with no order number (no candidate); A at 159, ``RO`` ``000101``
  line ``1``, late, 12 open units, expected 2026-10-28, supplier
  ``S-A`` named "Alpha Supply"; B at 159, ``RO`` ``000201`` line
  ``3``, after the stockout (expected 2026-11-20, projected stockout
  2026-11-10), 30 open units, supplier ``S-B`` with no name; then A
  at 159, ``RO`` ``000102`` line ``2``, after the stockout (the list's
  own order — not a sort — is what makes A's first line the first);
* the transfer suggestions answer four, in order: C from 154 to 159
  (80 units, 80 surplus, 120 need), C from 160 to 159 (40), A from
  154 to 159 (20), and D from 159 to 160 (to another site: no
  candidate for D's row);
* the 7-day order plan answers two groups: ``S-A`` named "Alpha
  Supply", with lines for A, C and D at 159 (D's 1200 units order by
  2026-11-02), then the group without a supplier, with E at 159
  (40 units order by 2026-11-04).

Proved here:

  * without the parameter the answer is the stand-in rows, row for
    row, each with ``decision`` null, ``decision_lapsed`` false and
    ``item_description`` null — no row has a ``remedy`` key and none
    of the three reads was called;
  * with it, the same rows each carry their ``remedy``: A expedites
    ``RO`` ``000101`` line ``1`` (12 units, expected 2026-10-28,
    ``S-A``, "Alpha Supply", overdue reason); B expedites ``RO``
    ``000201`` line ``3`` (30 units, ``S-B`` with null name, the
    line's own expected and projected dates); C transfers 80 units
    from 154 (the line's surplus and the site's need in the reason);
    D raises an order of 1200 units from ``S-A`` ("Alpha Supply") by
    2026-11-02 and E one of 40 units by 2026-11-04 with supplier
    null (both the no-expedite-no-transfer reason); F's remedy is
    null — every remedy carrying every key, the keys that do not
    apply being null;
  * the reads — each called exactly once, with the memo active:
    ``expedite_list`` with no as-of, ``build_transfer_suggestions``
    with 2026-11-02 and ``build_supply_plan`` with 7 and 2026-11-02;
    with ``&include_remedy=true&offset=6`` the answer is empty and
    none of them is called;
  * the failed reads — ``expedite_list`` raising leaves every remedy
    null; ``build_transfer_suggestions`` raising leaves A and B their
    expedite and C, D, E and F null; ``build_supply_plan`` raising
    leaves A, B and C theirs and D, E and F null — each still
    answering 200;
  * another order — with the system value of
    ``decisions.remedy_order`` stored as
    ``transfer,expedite,raise_order`` the way existing tests store a
    system setting, A's remedy is the transfer of A from 154 (A has
    both an expedite line and a transfer suggestion) and B keeps its
    expedite (it has no transfer suggestion); with the default
    restored, A expedites again;
  * the schema — the app's OpenAPI document lists ``include_remedy``
    among the route's parameters (boolean, default false) and the
    route's description documents it, the items of the route's 200
    answer have a ``remedy`` property whose schema is
    ``StockoutRemedy`` with exactly the keys above, and
    ``StockoutRow`` has no ``remedy`` property; the settings registry
    holds ``decisions.remedy_order`` with its six allowed values and
    its default.

Identity — the proof also runs as a NON-ADMIN (role ``user``,
asserted via ``GET /api/auth/me``) in a second pass after a full
reload (a fresh ``TestClient`` booted the production way — the token
the page would take from the URL hash as ``#access_token=...``, with
``GET /api/auth/config`` answering publicly and ``GET /api/auth/me``
resolving the viewer): the route has no admin-only control — the
viewer reads the whole list with remedies (it renders, the same
answer as the admin's) — and a request without a token still gets
401.
"""

from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

import app.api.stockout as stockout_api
from app.api.stockout import StockoutDecisionRow, StockoutRow
from app.main import app
from app.recommend import reorder
from app.settings.registry import get_setting

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

URL = "/api/stockout"
SETTINGS_URL = "/api/settings"
SITE = "159"
AS_OF = date(2026, 11, 2)

REMEDY_ORDER_KEY = "decisions.remedy_order"
DEFAULT_ORDER = "expedite,transfer,raise_order"
OTHER_ORDER = "transfer,expedite,raise_order"
ALL_ORDERS = (
    "expedite,transfer,raise_order",
    "expedite,raise_order,transfer",
    "transfer,expedite,raise_order",
    "transfer,raise_order,expedite",
    "raise_order,expedite,transfer",
    "raise_order,transfer,expedite",
)

RAISE_ORDER_REASON = (
    "No open order to expedite and no transfer suggested for this site."
)


def _stockout_row(item, rank, p_stockout):
    """One stand-in stockout row: every StockoutRow field filled, at
    the fixed as-of, at site 159."""
    return {
        "rank": rank,
        "category": "likely" if rank <= 3 else "at_risk",
        "item_code": item,
        "site_code": SITE,
        "on_hand_units": float(rank),
        "open_supply_units": 10.0 + rank,
        "open_supply_due_by": AS_OF + timedelta(days=rank),
        "unit_cost": 1.5,
        "lead_time_days": 10.0,
        "lead_time_demand": 20.0,
        "p_stockout": p_stockout,
        "days_of_cover": 3.5,
        "projected_stockout_on": AS_OF + timedelta(days=3 + rank),
        "recommended_reorder_point_units": 40,
        "expected_units_short": 5.0 + rank,
        "money_at_risk": 7.5 + rank,
        "as_of": AS_OF,
        "notes": [f"note {item}"],
        "arithmetic": f"{item} arithmetic",
        "item_description": f"Item {item}",
    }


ROWS = [
    _stockout_row("A", 1, 0.9),
    _stockout_row("B", 2, 0.8),
    _stockout_row("C", 3, 0.7),
    _stockout_row("D", 4, 0.6),
    _stockout_row("E", 5, 0.5),
    _stockout_row("F", 6, 0.4),
]


def _expedite_line(
    item,
    order_type,
    order_nbr,
    line_nbr,
    reasons,
    open_units,
    expected_on,
    projected_stockout_on,
    supplier_code,
    supplier_name=None,
    with_name_key=True,
):
    """One stand-in expedite line; every date a date object, as
    expedite_list answers them."""
    line = {
        "order_type": order_type,
        "order_nbr": order_nbr,
        "line_nbr": line_nbr,
        "item_code": item,
        "site_code": SITE,
        "supplier_code": supplier_code,
        "ordered_on": expected_on - timedelta(days=18),
        "promised_on": expected_on,
        "expected_on": expected_on,
        "promise_is_default": "late" not in reasons,
        "open_units": open_units,
        "open_value": open_units * 1.5,
        "days_late": None,
        "on_hand_units": 1.0,
        "d": 2.0,
        "days_of_cover": 0.5,
        "projected_stockout_on": projected_stockout_on,
        "reasons": reasons,
        "supplier_on_time_rate": 0.9 if supplier_code else None,
    }
    if with_name_key:
        line["vendor_code"] = f"V-{supplier_code}" if supplier_code else None
        line["supplier_name"] = supplier_name
    return line


# In this order: E's line carries no order number (no expedite
# candidate for E), A's two lines in the list's own order (the first
# is A's remedy, the second never reached), B's line after the
# stockout.
EXPEDITE_LINES = [
    _expedite_line(
        "E",
        None,
        None,
        None,
        ["late"],
        25.0,
        date(2026, 10, 30),
        date(2026, 11, 5),
        None,
        with_name_key=False,
    ),
    _expedite_line(
        "A",
        "RO",
        "000101",
        "1",
        ["late"],
        12.0,
        date(2026, 10, 28),
        date(2026, 11, 8),
        "S-A",
        "Alpha Supply",
    ),
    _expedite_line(
        "B",
        "RO",
        "000201",
        "3",
        ["after_stockout"],
        30.0,
        date(2026, 11, 20),
        date(2026, 11, 10),
        "S-B",
        None,
    ),
    _expedite_line(
        "A",
        "RO",
        "000102",
        "2",
        ["after_stockout"],
        8.0,
        date(2026, 11, 25),
        date(2026, 11, 5),
        "S-A",
        "Alpha Supply",
    ),
]
EXPEDITE_ANSWER = {
    "as_of": "2026-11-16",
    "lines": EXPEDITE_LINES,
    "stale_excluded": 1,
}


def _suggestion(item, from_site, to_site, quantity, surplus, need):
    return {
        "item_code": item,
        "source_system": "test",
        "from_site": from_site,
        "to_site": to_site,
        "quantity": quantity,
        "unit_cost": 1.5,
        "value": Decimal(quantity) * Decimal("1.5"),
        "to_site_need": need,
        "from_site_surplus": surplus,
        "arithmetic": f"move {quantity} of {item}",
    }


# In this order: C twice (the first is C's remedy), A (A's candidate
# for the transfer-first order), and D into another site (no candidate
# for D's row at 159).
TRANSFER_SUGGESTIONS = [
    _suggestion("C", "154", SITE, 80, 80, 120),
    _suggestion("C", "160", SITE, 40, 40, 120),
    _suggestion("A", "154", SITE, 20, 25, 90),
    _suggestion("D", SITE, "160", 60, 60, 55),
]
TRANSFER_ANSWER = {
    "as_of": AS_OF,
    "suggestion_count": len(TRANSFER_SUGGESTIONS),
    "total_value": sum((s["value"] for s in TRANSFER_SUGGESTIONS), Decimal(0)),
    "suggestions": TRANSFER_SUGGESTIONS,
}


def _plan_line(item, quantity, order_by):
    return {
        "item_code": item,
        "site_code": SITE,
        "position_units": 10,
        "reorder_point_units": 40,
        "daily_demand": 2.0,
        "due_in_days": 0,
        "order_by": order_by,
        "lead_time_days": 10.0,
        "arrives_on": order_by + timedelta(days=10),
        "raw_quantity": quantity,
        "quantity": quantity,
        "adjustments": [],
        "short_of_need": False,
        "terms": None,
        "unit_cost": 1.5,
        "value": Decimal(quantity) * Decimal("1.5"),
        "arithmetic": f"order {quantity} of {item}",
    }


def _plan_group(supplier_code, name, lines):
    return {
        "supplier_code": supplier_code,
        "source_system": None if supplier_code is None else "test",
        "vendor_code": None if supplier_code is None else f"V-{supplier_code}",
        "name": name,
        "line_count": len(lines),
        "total_value": sum((line["value"] for line in lines), Decimal(0)),
        "minimum": None,
        "shortfall": None,
        "lines": lines,
    }


PLAN_GROUPS = [
    _plan_group(
        "S-A",
        "Alpha Supply",
        [
            _plan_line("A", 30, AS_OF),
            _plan_line("C", 120, AS_OF + timedelta(days=1)),
            _plan_line("D", 1200, AS_OF),  # order by 2026-11-02
        ],
    ),
    # The group without a supplier: E's line, order by 2026-11-04.
    _plan_group(
        None,
        None,
        [
            _plan_line("E", 40, AS_OF + timedelta(days=2)),
        ],
    ),
]
PLAN_ANSWER = {
    "as_of": AS_OF,
    "horizon_days": 7,
    "line_count": sum(group["line_count"] for group in PLAN_GROUPS),
    "total_value": sum((group["total_value"] for group in PLAN_GROUPS), Decimal(0)),
    "suppliers": PLAN_GROUPS,
}

REMEDIES = {
    "A": {
        "kind": "expedite",
        "order_type": "RO",
        "order_nbr": "000101",
        "line_nbr": "1",
        "expected_on": "2026-10-28",
        "from_site": None,
        "supplier_code": "S-A",
        "supplier_name": "Alpha Supply",
        "order_by": None,
        "units": 12,
        "reason": "The open order was expected 2026-10-28 and is overdue.",
    },
    "B": {
        "kind": "expedite",
        "order_type": "RO",
        "order_nbr": "000201",
        "line_nbr": "3",
        "expected_on": "2026-11-20",
        "from_site": None,
        "supplier_code": "S-B",
        "supplier_name": None,
        "order_by": None,
        "units": 30,
        "reason": (
            "The open order is expected 2026-11-20, after the projected "
            "stockout on 2026-11-10."
        ),
    },
    "C": {
        "kind": "transfer",
        "order_type": None,
        "order_nbr": None,
        "line_nbr": None,
        "expected_on": None,
        "from_site": "154",
        "supplier_code": None,
        "supplier_name": None,
        "order_by": None,
        "units": 80,
        "reason": (
            "No open order to expedite; site 154 holds 80 units beyond its "
            "reorder point plus one order quantity, and this site is 120 "
            "units below its reorder point."
        ),
    },
    "D": {
        "kind": "raise_order",
        "order_type": None,
        "order_nbr": None,
        "line_nbr": None,
        "expected_on": None,
        "from_site": None,
        "supplier_code": "S-A",
        "supplier_name": "Alpha Supply",
        "order_by": "2026-11-02",
        "units": 1200,
        "reason": RAISE_ORDER_REASON,
    },
    "E": {
        "kind": "raise_order",
        "order_type": None,
        "order_nbr": None,
        "line_nbr": None,
        "expected_on": None,
        "from_site": None,
        "supplier_code": None,
        "supplier_name": None,
        "order_by": "2026-11-04",
        "units": 40,
        "reason": RAISE_ORDER_REASON,
    },
    # F: no expedite line, no transfer into 159, no order plan line.
    "F": None,
}
# A's remedy when the stored order tries transfer before expedite (A
# has both an expedite line and a transfer suggestion).
A_TRANSFER_REMEDY = {
    "kind": "transfer",
    "order_type": None,
    "order_nbr": None,
    "line_nbr": None,
    "expected_on": None,
    "from_site": "154",
    "supplier_code": None,
    "supplier_name": None,
    "order_by": None,
    "units": 20,
    "reason": (
        "No open order to expedite; site 154 holds 25 units beyond its "
        "reorder point plus one order quantity, and this site is 90 "
        "units below its reorder point."
    ),
}


class _StockoutRanked:
    """The stand-in ranking: six fixed rows at the fixed as-of,
    recording its as-of argument and whether the memo was active when
    it was called."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.memo_active = None

    def __call__(self, as_of=None):
        self.calls.append(as_of)
        self.memo_active = reorder._recommendations_memo.get() is not None
        return self.rows


class _Read:
    """The stand-in for one of the three remedy reads.

    Answers a fixed payload (or raises) and records every call's
    arguments and whether the recommendations memo was active when it
    was called (app.recommend.reorder._recommendations_memo.get() is
    not None).
    """

    def __init__(self, answer, raises=None):
        self.answer = answer
        self.raises = raises
        self.calls = []
        self.memo_active = None

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.memo_active = reorder._recommendations_memo.get() is not None
        if self.raises is not None:
            raise self.raises
        return self.answer


def _jsonable(row: dict) -> dict:
    """The answered shape of one stand-in row: dates as ISO strings,
    the way the response serializes them."""
    return {
        key: (value.isoformat() if isinstance(value, date) else value)
        for key, value in row.items()
    }


def _expected_row(standin: dict) -> dict:
    return {
        **standin,
        "decision": None,
        "decision_lapsed": False,
        "item_description": None,
    }


def _expected_remedy_row(standin: dict, remedy) -> dict:
    return {**_expected_row(standin), "remedy": remedy}


def _remedies_of(body: list[dict]) -> dict[str, dict | None]:
    return {row["item_code"]: row["remedy"] for row in body}


def test_issue1313_freeform(client, monkeypatch):
    # -- The stand-ins: every read of the route, in app.api.stockout. --
    ranked = _StockoutRanked(ROWS)
    expedite = _Read(EXPEDITE_ANSWER)
    transfers = _Read(TRANSFER_ANSWER)
    plan = _Read(PLAN_ANSWER)
    monkeypatch.setattr(stockout_api, "stockout_ranked", ranked)
    monkeypatch.setattr(stockout_api._repo, "current_all", lambda: [])
    monkeypatch.setattr(stockout_api, "_item_descriptions", lambda: {})
    monkeypatch.setattr(stockout_api, "expedite_list", expedite)
    monkeypatch.setattr(stockout_api, "build_transfer_suggestions", transfers)
    monkeypatch.setattr(stockout_api, "build_supply_plan", plan)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    anon = TestClient(app)
    base_params = {"limit": 1000, "include_decided": "true"}
    remedy_params = {**base_params, "include_remedy": "true"}

    # -- Without the parameter: exactly today's answer. -----------------
    response = admin.get(URL, params=base_params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == ["A", "B", "C", "D", "E", "F"]
    for standin, row in zip(ROWS, body):
        assert row == _jsonable(_expected_row(standin)), row
        assert "remedy" not in row
    assert ranked.calls == [None]  # the ranking, at the route's as-of (none)
    # None of the three remedy reads was called.
    assert expedite.calls == []
    assert transfers.calls == []
    assert plan.calls == []

    # -- With it: the same rows, each with its remedy. -------------------
    response = admin.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert [row["item_code"] for row in body] == ["A", "B", "C", "D", "E", "F"]
    for standin, row in zip(ROWS, body):
        assert row == _jsonable(
            _expected_remedy_row(standin, REMEDIES[row["item_code"]])
        ), row
        # Every remedy carries every key; the keys that do not apply
        # are null (the exact-dict equality above), and the row gains
        # exactly the remedy key over today's shape.
        assert set(row.keys()) == set(StockoutRow.model_fields) | {
            "decision",
            "decision_lapsed",
            "remedy",
        }
    assert _remedies_of(body) == REMEDIES

    # -- The reads: each once, with the memo active, at the right as-of.
    assert ranked.calls == [None, None]
    assert ranked.memo_active is True  # the ranking runs in the one memo
    assert expedite.calls == [((), {})]  # no as-of: its own default
    assert transfers.calls == [((AS_OF,), {})]
    assert plan.calls == [((7, AS_OF), {})]
    assert expedite.memo_active is True
    assert transfers.memo_active is True
    assert plan.memo_active is True

    # -- The empty page: no row, none of the three is read. --------------
    response = admin.get(URL, params={**remedy_params, "offset": 6})
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert expedite.calls == [((), {})]
    assert transfers.calls == [((AS_OF,), {})]
    assert plan.calls == [((7, AS_OF), {})]

    # -- A failed read: expedite_list raises, every remedy is null. ------
    expedite_down = _Read(None, raises=RuntimeError("the expedite read is down"))
    monkeypatch.setattr(stockout_api, "expedite_list", expedite_down)
    response = admin.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    assert _remedies_of(response.json()) == {item: None for item in "ABCDEF"}
    assert expedite_down.calls == [((), {})]
    assert expedite_down.memo_active is True
    # The other two reads were still read once this request.
    assert len(transfers.calls) == 2
    assert len(plan.calls) == 2

    # -- A failed read: the transfer suggestions raise (the expedite
    #    read healthy again). A and B keep their expedite; C, D, E and F
    #    read null. ---------------------------------------------------------
    monkeypatch.setattr(stockout_api, "expedite_list", expedite)
    transfers_down = _Read(None, raises=RuntimeError("the transfer read is down"))
    monkeypatch.setattr(stockout_api, "build_transfer_suggestions", transfers_down)
    response = admin.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    remedies = _remedies_of(response.json())
    assert remedies["A"] == REMEDIES["A"]
    assert remedies["B"] == REMEDIES["B"]
    assert [remedies[item] for item in "CDEF"] == [None, None, None, None]
    assert transfers_down.calls == [((AS_OF,), {})]
    assert transfers_down.memo_active is True

    # -- A failed read: the order plan raises (the other two reads
    #    healthy again). A, B and C keep theirs; D, E and F read null. -----
    monkeypatch.setattr(stockout_api, "build_transfer_suggestions", transfers)
    plan_down = _Read(None, raises=RuntimeError("the order plan read is down"))
    monkeypatch.setattr(stockout_api, "build_supply_plan", plan_down)
    response = admin.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    remedies = _remedies_of(response.json())
    assert remedies["A"] == REMEDIES["A"]
    assert remedies["B"] == REMEDIES["B"]
    assert remedies["C"] == REMEDIES["C"]
    assert [remedies[item] for item in "DEF"] == [None, None, None]
    assert plan_down.calls == [((7, AS_OF), {})]
    assert plan_down.memo_active is True
    # The route writes nothing: the decisions store is untouched (the
    # current-decisions read answered nothing for every request).
    assert response.json()[0]["decision"] is None

    # -- Another order: transfer first (every read healthy again),
    #    stored the way existing tests store a system setting. -------------
    monkeypatch.setattr(stockout_api, "build_supply_plan", plan)
    put = admin.put(f"{SETTINGS_URL}/{REMEDY_ORDER_KEY}", json={"value": OTHER_ORDER})
    assert put.status_code == 200, put.text
    assert put.json()["value"] == OTHER_ORDER
    assert put.json()["source"] == "system"
    response = admin.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    remedies = _remedies_of(response.json())
    # A has both an expedite line and a transfer suggestion: the
    # stored order tries transfer first.
    assert remedies["A"] == A_TRANSFER_REMEDY
    # B has no transfer suggestion: it keeps its expedite.
    assert remedies["B"] == REMEDIES["B"]
    # The others are unchanged by the order.
    assert remedies["C"] == REMEDIES["C"]
    assert remedies["D"] == REMEDIES["D"]
    assert remedies["E"] == REMEDIES["E"]
    assert remedies["F"] is None

    # With the default restored, A expedites again.
    deleted = admin.delete(f"{SETTINGS_URL}/{REMEDY_ORDER_KEY}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["value"] == DEFAULT_ORDER
    assert deleted.json()["source"] == "default"
    response = admin.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    default_body = response.json()
    assert _remedies_of(default_body) == REMEDIES
    assert default_body[0]["remedy"]["kind"] == "expedite"

    # -- The settings registry holds the new setting. ---------------------
    setting = get_setting(REMEDY_ORDER_KEY)
    assert setting is not None
    assert setting.scope == "system"
    assert setting.type == "enum"
    assert setting.default == DEFAULT_ORDER
    assert tuple(setting.allowed) == ALL_ORDERS
    assert setting.description == (
        "The order in which a stockout row's remedy is looked for: "
        "the first kind that has a candidate is proposed."
    )

    # -- The schema: the parameter, the remedy property and the
    #    StockoutRemedy keys; StockoutRow gains nothing. -------------------
    spec = app.openapi()
    get_stockout = spec["paths"][URL]["get"]
    parameters = {param["name"]: param for param in get_stockout["parameters"]}
    include_remedy = parameters["include_remedy"]
    assert include_remedy["in"] == "query"
    assert include_remedy["schema"]["type"] == "boolean"
    assert include_remedy["schema"]["default"] is False
    # The route's description documents the parameter and the field.
    assert "include_remedy" in get_stockout["description"]
    assert "remedy" in get_stockout["description"]
    for name in ("limit", "offset", "category", "as_of", "include_decided"):
        assert name in parameters, name
    list_schema = get_stockout["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert list_schema["type"] == "array"
    assert list_schema["items"] == {"$ref": "#/components/schemas/StockoutDecisionRow"}
    row_schema = spec["components"]["schemas"]["StockoutDecisionRow"]
    remedy_property = row_schema["properties"]["remedy"]
    assert {"$ref": "#/components/schemas/StockoutRemedy"} in remedy_property.get(
        "anyOf", []
    ), remedy_property
    assert {"type": "null"} in remedy_property.get("anyOf", []), remedy_property
    remedy_schema = spec["components"]["schemas"]["StockoutRemedy"]
    assert set(remedy_schema["properties"]) == {
        "kind",
        "order_type",
        "order_nbr",
        "line_nbr",
        "expected_on",
        "from_site",
        "supplier_code",
        "supplier_name",
        "order_by",
        "units",
        "reason",
    }
    assert "remedy" not in StockoutRow.model_fields
    # The route's include_remedy parameter is not a property of the
    # row: the row's schema is StockoutRow plus exactly decision,
    # decision_lapsed and remedy, and the parameter is part of neither
    # the model's fields nor its schema (it rides on the answered row's
    # dict as private state the serializer reads).
    assert set(row_schema["properties"]) == set(StockoutRow.model_fields) | {
        "decision",
        "decision_lapsed",
        "remedy",
    }
    assert "include_remedy" not in row_schema["properties"]
    assert "include_remedy" not in StockoutDecisionRow.model_fields
    assert "include_remedy" not in StockoutDecisionRow.model_json_schema()["properties"]

    # -- Identity, second pass after a full reload: the route has no
    #    admin-only control.  The page boots the production way for the
    #    non-admin (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the viewer reads the whole
    #    list with remedies (it renders, the same answer as the
    #    admin's) — and a request without a token still gets 401. -----
    assert TestClient(app).get("/api/auth/config").status_code == 200
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    response = viewer_reloaded.get(URL, params=remedy_params)
    assert response.status_code == 200, response.text
    assert response.json() == default_body
    assert anon.get(URL, params=remedy_params).status_code == 401
