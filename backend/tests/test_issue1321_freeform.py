"""Issue #1321 proof: the remedy proposed with a stockout decision.

``POST /api/stockout/decisions`` stores, with every decision it
records (accepted, dismissed or snoozed), the remedy
``GET /api/stockout?include_remedy=true`` proposed for the item-site's
ranked row — the row the decision snapshots: the ``StockoutRemedy``
object in JSON, with every key and the dates as ``YYYY-MM-DD``
strings.  The proposal rides on a new ``stockout_decision_proposals``
table (one row per proposal, the decision's id the primary key and
the foreign key with ON DELETE CASCADE, the remedy jsonb with a CHECK
on its ``kind``, ``created_at`` defaulted to ``now()``) written by a
repository with an ``insert`` and a ``for_decisions`` and nothing
else.  The route's ranked read and the three remedy reads
(``expedite_list`` with no as-of, the transfer suggestions and the
7-day order plan at the row's as-of) run inside one
``recommendations_memo``; a request the route refuses (403, 422, 404)
is refused exactly as before with none of the three reads run, and
while no remedy is proposed — or working out or storing one fails —
the decision is stored and answered as usual, with no proposal row.
Nothing in any answer changes: the POST answers 201 with exactly the
keys of ``StockoutDecision``, no answer of a post, no row of
``GET /api/stockout/decisions`` and no ``decision`` object on a row of
``GET /api/stockout`` carries a ``proposed_remedy`` key.

A new ``GET /api/stockout/proposals`` route, on the same router and
open to every signed-in user, writes nothing and answers
``StockoutProposalCounts`` over the current stockout decisions that
are accepted: ``accepted`` their number; under each kind,
``proposed`` the accepted decisions whose stored proposal has that
``kind`` and ``chosen`` those that chose the kind (their action is
it), ``total`` the sum of the three kinds; ``without_proposal`` the
accepted decisions with no stored proposal (every decision recorded
before this slice among them), so ``total.proposed`` plus
``without_proposal`` equals ``accepted``.

Everything is read through the app's test client, signed in with the
development tokens (admin and user), with every read of the routes
replaced by the stand-ins of test 1313 — six stockout rows (items A
to F at site 159, at as-of 2026-11-02), the expedite lines, the
transfer suggestions and the order plan groups, each recording its
arguments and whether the recommendations memo was active when it was
called — so no test reads the wall clock.

Proved here:

* the proposals stored with the decisions — with
  ``decisions.remedy_order`` at its default, A accepted with
  expedite, B accepted with transfer, C accepted with transfer, D
  accepted with raise_order, E dismissed (the reason "Seasonal
  stock") and F accepted with raise_order each answer 201 with
  exactly the keys of ``StockoutDecision``; for each,
  ``for_decisions`` on the stored decision's id answers the remedy
  ``GET /api/stockout?limit=1000&include_decided=true&include_remedy=
  true`` then answers for the row — A's exactly the overdue-expedite
  remedy, E's dismissal stores its proposal too, and F, whose row has
  no remedy, stores no proposal row;
* the reads — each of those posts called ``expedite_list`` once with
  no as-of, ``build_transfer_suggestions`` once with 2026-11-02 and
  ``build_supply_plan`` once with 7 and 2026-11-02, each with the
  memo active, and the route's ``stockout_ranked`` ran with the memo
  active;
* never in the way — a post signed in with the user token answers 403
  as before, calls none of the three and stores nothing, and no
  answer of a post, no row of ``GET /api/stockout/decisions`` and no
  ``decision`` object on a row of ``GET /api/stockout`` carries a
  ``proposed_remedy`` key;
* a failed read — with ``expedite_list`` raising, a decision on A is
  stored with no proposal row; with ``build_transfer_suggestions``
  raising, a decision on C has no proposal row while one on A still
  stores its expedite; each post succeeds;
* another order — with ``decisions.remedy_order`` stored as
  ``transfer,expedite,raise_order`` the way test 1313 stores it, a
  decision on A stores A's transfer from 154;
* the counts — after every stored decision is deleted (the earlier
  posts are not counted, the cascade removing their proposals), the
  store is seeded through the two repositories with nine current
  decisions at nine item-sites (five with a stored proposal) and
  ``GET /api/stockout/proposals`` answers exactly the counts of the
  seed; the user token gets the same answer and no token gets 401,
  booted the production way (``GET /api/auth/config`` public and
  ``GET /api/auth/me`` resolving the viewer, the role user);
* the migration — the script directory has exactly one head and the
  revision whose ``down_revision`` is ``0044_standing_grant_runs`` is
  in its history; the test database has the table and the constraint:
  storing ``{"kind": "ship"}`` through ``insert`` for a stored
  decision that has no proposal raises
  ``psycopg2.errors.CheckViolation`` and stores nothing.
"""

import os
from datetime import date, timedelta
from decimal import Decimal

import psycopg2
import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.stockout as stockout_api
import app.api.stockout_decisions as decisions_api
from app.api.stockout_decisions import StockoutDecision
from app.db.connection import get_connection
from app.main import app
from app.recommend import reorder
from app.repositories.stockout_decision_proposals_postgres_repository import (
    StockoutDecisionProposalPostgresRepository,
)
from app.repositories.stockout_decisions_postgres_repository import (
    StockoutDecisionPostgresRepository,
)

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

STOCKOUT_URL = "/api/stockout"
DECISIONS_URL = "/api/stockout/decisions"
PROPOSALS_URL = "/api/stockout/proposals"
SETTINGS_URL = "/api/settings"
SITE = "159"
AS_OF = date(2026, 11, 2)
SEED_SITE = "191"
REMEDY_ORDER_KEY = "decisions.remedy_order"
OTHER_ORDER = "transfer,expedite,raise_order"

RAISE_ORDER_REASON = (
    "No open order to expedite and no transfer suggested for this site."
)

# A's stored proposal, exactly: the overdue expedite line.
A_PROPOSAL = {
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
}


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

# The remedy each stand-in row is proposed with the default
# decisions.remedy_order — the same remedies test 1313 proves
# GET /api/stockout?include_remedy=true answers.
REMEDIES = {
    "A": A_PROPOSAL,
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
# has both an expedite line and a transfer suggestion): A's transfer
# from 154.
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
    recording every call's as-of argument and whether the
    recommendations memo was active when it was called."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.memo_flags = []

    def __call__(self, as_of=None):
        self.calls.append(as_of)
        self.memo_flags.append(reorder._recommendations_memo.get() is not None)
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
        self.memo_flags = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        self.memo_flags.append(reorder._recommendations_memo.get() is not None)
        if self.raises is not None:
            raise self.raises
        return self.answer


def _seed_remedy(kind: str, index: int) -> dict:
    """One full StockoutRemedy of ``kind``, every key filled, for the
    seeded proposal store."""
    if kind == "expedite":
        return {
            "kind": "expedite",
            "order_type": "RO",
            "order_nbr": f"000{index:03d}",
            "line_nbr": "1",
            "expected_on": "2026-10-28",
            "from_site": None,
            "supplier_code": f"S-{index}",
            "supplier_name": None,
            "order_by": None,
            "units": 10 + index,
            "reason": "a seeded expedite",
        }
    if kind == "transfer":
        return {
            "kind": "transfer",
            "order_type": None,
            "order_nbr": None,
            "line_nbr": None,
            "expected_on": None,
            "from_site": "154",
            "supplier_code": None,
            "supplier_name": None,
            "order_by": None,
            "units": 20 + index,
            "reason": "a seeded transfer",
        }
    return {
        "kind": "raise_order",
        "order_type": None,
        "order_nbr": None,
        "line_nbr": None,
        "expected_on": None,
        "from_site": None,
        "supplier_code": f"S-{index}",
        "supplier_name": None,
        "order_by": "2026-11-02",
        "units": 30 + index,
        "reason": "a seeded raised order",
    }


# The counts seed: nine current decisions at nine item-sites, each
# item-site's only decision — by decision, action and stored
# proposal's kind.
SEED = [
    ("P1", "accepted", "expedite", "expedite"),
    ("P2", "accepted", "transfer", "expedite"),
    ("P3", "accepted", "transfer", "transfer"),
    ("P4", "accepted", "raise_order", "transfer"),
    ("P5", "accepted", "raise_order", "raise_order"),
    ("P6", "accepted", "expedite", None),
    ("P7", "accepted", "raise_order", None),
    ("P8", "dismissed", "expedite", None),
    ("P9", "snoozed", "raise_order", None),
]
EXPECTED_COUNTS = {
    "accepted": 7,
    "expedite": {"proposed": 2, "chosen": 1},
    "transfer": {"proposed": 2, "chosen": 1},
    "raise_order": {"proposed": 1, "chosen": 1},
    "total": {"proposed": 5, "chosen": 3},
    "without_proposal": 2,
}

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")


def _body(item: str, decision: str, **fields) -> dict:
    """One decision body at the fixed site, the extra fields (``action``
    or ``reason``) appended."""
    return {"item_code": item, "site_code": SITE, "decision": decision, **fields}


POSTS = [
    ("A", _body("A", "accepted", action="expedite")),
    ("B", _body("B", "accepted", action="transfer")),
    ("C", _body("C", "accepted", action="transfer")),
    ("D", _body("D", "accepted", action="raise_order")),
    ("E", _body("E", "dismissed", reason="Seasonal stock")),
    ("F", _body("F", "accepted", action="raise_order")),
]
REMEDY_PARAMS = {"limit": 1000, "include_decided": "true", "include_remedy": "true"}


def test_issue1321_freeform(client, monkeypatch):
    _proposal_repo = StockoutDecisionProposalPostgresRepository()

    # -- The stand-ins: every read of the routes.  In app.api.stockout,
    #    the list route's ranking, the three remedy reads (the decision
    #    route's proposed_remedy runs through the same module names, so
    #    these stand in for it too) and the item descriptions; in
    #    app.api.stockout_decisions, the ranked read through which the
    #    POST finds the item-site's row. ------------------------------
    ranked = _StockoutRanked(ROWS)
    ranked_decisions = _StockoutRanked(ROWS)
    expedite = _Read(EXPEDITE_ANSWER)
    transfers = _Read(TRANSFER_ANSWER)
    plan = _Read(PLAN_ANSWER)
    monkeypatch.setattr(stockout_api, "stockout_ranked", ranked)
    monkeypatch.setattr(stockout_api, "_item_descriptions", lambda: {})
    monkeypatch.setattr(stockout_api, "expedite_list", expedite)
    monkeypatch.setattr(stockout_api, "build_transfer_suggestions", transfers)
    monkeypatch.setattr(stockout_api, "build_supply_plan", plan)
    monkeypatch.setattr(decisions_api, "stockout_ranked", ranked_decisions)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    def _snapshot(
        expedite_read=expedite,
        transfer_read=transfers,
        plan_read=plan,
    ) -> tuple:
        return (
            len(expedite_read.calls),
            len(transfer_read.calls),
            len(plan_read.calls),
            len(ranked_decisions.calls),
        )

    def _assert_post_reads(
        pre: tuple,
        expedite_read=expedite,
        transfer_read=transfers,
        plan_read=plan,
    ) -> None:
        # The post's own reads: each of the three once, at the right
        # as-of, with the memo active, and the route's ranked read in
        # the same memo.  The failed-read posts pass the failing
        # stand-in for its healthy twin.
        assert expedite_read.calls[pre[0] :] == [((), {})]  # no as-of
        assert transfer_read.calls[pre[1] :] == [((AS_OF,), {})]
        assert plan_read.calls[pre[2] :] == [((7, AS_OF), {})]
        assert ranked_decisions.calls[pre[3] :] == [None]
        assert expedite_read.memo_flags[pre[0] :] == [True]
        assert transfer_read.memo_flags[pre[1] :] == [True]
        assert plan_read.memo_flags[pre[2] :] == [True]
        assert ranked_decisions.memo_flags[pre[3] :] == [True]

    # -- Stored with each decision: the six posts, the default
    #    decisions.remedy_order. ---------------------------------------
    stored = {}
    for item, body in POSTS:
        pre = _snapshot()
        response = admin.post(DECISIONS_URL, json=body)
        assert response.status_code == 201, response.text
        answered = response.json()
        # The answer is exactly StockoutDecision's keys — no
        # proposed_remedy in it.
        assert set(answered.keys()) == set(StockoutDecision.model_fields)
        assert "proposed_remedy" not in answered
        _assert_post_reads(pre)
        decision_id = answered["id"]
        stored[item] = decision_id
        # The proposal answers the remedy the stockout list then
        # answers for the row.
        listing = admin.get(STOCKOUT_URL, params=REMEDY_PARAMS)
        assert listing.status_code == 200, listing.text
        row = next(
            candidate for candidate in listing.json() if candidate["item_code"] == item
        )
        remedy = row["remedy"]
        expected = {decision_id: remedy} if remedy is not None else {}
        assert _proposal_repo.for_decisions([decision_id]) == expected

    # A's proposal is exactly the overdue-expedite remedy, E's
    # dismissal stored its proposal too, and F's row — no remedy —
    # stored no proposal row.
    assert _proposal_repo.for_decisions([stored["A"]]) == {stored["A"]: A_PROPOSAL}
    assert _proposal_repo.for_decisions([stored["E"]]) == {stored["E"]: REMEDIES["E"]}
    assert _proposal_repo.for_decisions([stored["F"]]) == {}

    # -- Answers unchanged: no proposed_remedy anywhere. ----------------
    response = admin.get(DECISIONS_URL, params={"limit": 1000})
    assert response.status_code == 200, response.text
    decisions_answered = response.json()
    assert len(decisions_answered) == len(POSTS)
    for row in decisions_answered:
        assert "proposed_remedy" not in row
    response = admin.get(
        STOCKOUT_URL, params={"limit": 1000, "include_decided": "true"}
    )
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["item_code"] for row in rows] == ["A", "B", "C", "D", "E", "F"]
    for row in rows:
        assert row["decision"] is not None
        assert "proposed_remedy" not in row
        assert "proposed_remedy" not in row["decision"]

    # -- Never in the way: the user token is refused as before. ---------
    pre = _snapshot()
    response = viewer.post(
        DECISIONS_URL, json=_body("A", "accepted", action="expedite")
    )
    assert response.status_code == 403
    assert expedite.calls[pre[0] :] == []
    assert transfers.calls[pre[1] :] == []
    assert plan.calls[pre[2] :] == []
    assert ranked_decisions.calls[pre[3] :] == []
    # ... and it stored nothing: A's history is unchanged.
    history = admin.get(
        DECISIONS_URL, params={"item_code": "A", "site_code": SITE}
    ).json()
    assert len(history) == 1  # A's one accepted decision, no more
    assert _proposal_repo.for_decisions([stored["A"]]) == {stored["A"]: A_PROPOSAL}

    # -- A failed read: the expedite list raises; the decision on A is
    #    stored with no proposal row. ------------------------------------
    expedite_down = _Read(None, raises=RuntimeError("the expedite read is down"))
    monkeypatch.setattr(stockout_api, "expedite_list", expedite_down)
    pre = _snapshot(expedite_read=expedite_down)
    response = admin.post(DECISIONS_URL, json=_body("A", "accepted", action="expedite"))
    assert response.status_code == 201, response.text
    assert set(response.json().keys()) == set(StockoutDecision.model_fields)
    _assert_post_reads(pre, expedite_read=expedite_down)
    assert _proposal_repo.for_decisions([response.json()["id"]]) == {}
    monkeypatch.setattr(stockout_api, "expedite_list", expedite)

    # -- A failed read: the transfer suggestions raise; the decision on
    #    C has no proposal row, while one on A still stores its
    #    expedite. --------------------------------------------------------
    transfers_down = _Read(None, raises=RuntimeError("the transfer read is down"))
    monkeypatch.setattr(stockout_api, "build_transfer_suggestions", transfers_down)
    pre = _snapshot(transfer_read=transfers_down)
    response = admin.post(DECISIONS_URL, json=_body("C", "accepted", action="transfer"))
    assert response.status_code == 201, response.text
    c_decision_id = response.json()["id"]
    _assert_post_reads(pre, transfer_read=transfers_down)
    assert _proposal_repo.for_decisions([c_decision_id]) == {}
    pre = _snapshot(transfer_read=transfers_down)
    response = admin.post(DECISIONS_URL, json=_body("A", "accepted", action="expedite"))
    assert response.status_code == 201, response.text
    a_decision_id = response.json()["id"]
    _assert_post_reads(pre, transfer_read=transfers_down)
    assert _proposal_repo.for_decisions([a_decision_id]) == {a_decision_id: A_PROPOSAL}
    monkeypatch.setattr(stockout_api, "build_transfer_suggestions", transfers)

    # -- Another order: transfer first, stored the way test 1313 stores
    #    a system setting — a decision on A stores A's transfer from
    #    154. -------------------------------------------------------------
    put = admin.put(f"{SETTINGS_URL}/{REMEDY_ORDER_KEY}", json={"value": OTHER_ORDER})
    assert put.status_code == 200, put.text
    assert put.json()["value"] == OTHER_ORDER
    assert put.json()["source"] == "system"
    response = admin.post(DECISIONS_URL, json=_body("A", "accepted", action="expedite"))
    assert response.status_code == 201, response.text
    assert _proposal_repo.for_decisions([response.json()["id"]]) == {
        response.json()["id"]: A_TRANSFER_REMEDY
    }
    deleted = admin.delete(f"{SETTINGS_URL}/{REMEDY_ORDER_KEY}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["source"] == "default"

    # -- The counts.  Every stored decision is deleted first, so the
    #    earlier posts are not counted — the foreign key's cascade
    #    removes their proposals with them. -------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM stockout_decisions")
        conn.commit()
    finally:
        conn.close()
    all_ids = list(stored.values()) + [c_decision_id, a_decision_id]
    assert _proposal_repo.for_decisions(all_ids) == {}  # the cascade
    assert StockoutDecisionPostgresRepository().current_all() == []

    # The proposal store, seeded through the two repositories: nine
    # current decisions at nine item-sites, each item-site's only
    # decision.
    decision_repo = StockoutDecisionPostgresRepository()
    seed_ids = {}
    for index, (item, decision, action, kind) in enumerate(SEED, start=1):
        seed_ids[item] = decision_repo.insert(
            id=f"seed-{item.lower()}",
            item_code=item,
            site_code=SEED_SITE,
            decision=decision,
            action=action,
            reason="a seeded decision",
            snooze_until=None,
            as_of=AS_OF,
            category="likely",
            p_stockout=0.5,
            on_hand_units=1.0,
            open_supply_units=1.0,
            expected_units_short=1.0,
            money_at_risk=1.0,
            decided_by="seed",
            decided_by_name=None,
        )["id"]
        if kind is not None:
            _proposal_repo.insert(seed_ids[item], _seed_remedy(kind, index))

    response = admin.get(PROPOSALS_URL)
    assert response.status_code == 200, response.text
    counts = response.json()
    assert counts == EXPECTED_COUNTS
    # The invariant: every accepted decision is either proposed a kind
    # or has no proposal.
    assert (
        counts["total"]["proposed"] + counts["without_proposal"] == counts["accepted"]
    )

    # -- Identity, second pass after a full reload: the route has no
    #    admin-only control.  The page boots the production way for the
    #    non-admin (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer: the viewer reads the counts
    #    (it renders, the same answer as the admin's) — and a request
    #    without a token still gets 401. ----------------------------------
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
    response = viewer_reloaded.get(PROPOSALS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_COUNTS
    assert anon.get(PROPOSALS_URL).status_code == 401

    # -- The migration: one head, the new revision in the history, and
    #    the table and its constraint in the test database. --------------
    directory = ScriptDirectory(_MIGRATIONS_DIR)
    heads = directory.get_heads()
    assert len(heads) == 1
    head = next(iter(heads))
    history = list(directory.walk_revisions())
    assert head in [script.revision for script in history]
    successors = [
        script
        for script in history
        if script.down_revision == "0044_standing_grant_runs"
    ]
    assert len(successors) == 1
    assert successors[0].revision == "0045_stockout_decision_proposals"
    # The constraint: a remedy kind the decision's actions do not carry
    # is a check violation, and nothing is stored.  P6 is a stored
    # decision that has no proposal.
    p6_id = seed_ids["P6"]
    with pytest.raises(psycopg2.errors.CheckViolation):
        _proposal_repo.insert(p6_id, {"kind": "ship"})
    assert _proposal_repo.for_decisions([p6_id]) == {}
