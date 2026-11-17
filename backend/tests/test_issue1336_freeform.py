"""Issue #1336 proof: the remedy proposed with an excess decision.

``POST /api/excess/decisions`` stores, with every decision it
records (accepted, dismissed or snoozed), the remedy
``GET /api/excess?include_remedy=true`` proposed for the item-site's
ranked row — the row the decision snapshots: the ``ExcessRemedy``
object in JSON, with every key.  The proposal rides on a new
``excess_decision_proposals`` table (one row per proposal, the
decision's id the primary key and the foreign key with ON DELETE
CASCADE, the remedy jsonb with a CHECK on its ``kind``,
``created_at`` defaulted to ``now()``) written by a repository with
an ``insert`` and a ``for_decisions`` and nothing else.  A request
the route refuses (403, 422, 404) is refused exactly as before with
neither of the two remedy reads run, and while no remedy is proposed
— or working out or storing one fails — the decision is stored and
answered as usual, with no proposal row.  Nothing in any answer
changes: the POST answers 201 with exactly the keys of
``ExcessDecision``, no answer of a post, no row of
``GET /api/excess/decisions`` and no ``decision`` object on a row of
``GET /api/excess`` carries a ``proposed_remedy`` key.

A new ``GET /api/excess/proposals`` route, on the same router and
open to every signed-in user, writes nothing and answers
``ExcessProposalCounts`` over the current excess decisions that are
accepted: ``accepted`` their number; under each kind, ``proposed``
the accepted decisions whose stored proposal has that ``kind`` and
``chosen`` those that chose the kind (their action is it),
``total`` the sum of the three kinds; ``without_proposal`` the
accepted decisions with no stored proposal (every decision recorded
before this slice among them), so ``total.proposed`` plus
``without_proposal`` equals ``accepted``.

Everything is read through the app's test client, signed in with the
development tokens (admin and user), with every read of the routes
replaced by the stand-ins of test 1331 — seven excess rows (items A
to G at site 159, at as-of 2026-11-02), the ``item_sites`` levels
and the open purchase lines, each recording its arguments — so no
test reads the wall clock.

Proved here:

* the proposals stored with the decisions — A accepted with
  stop_replenishing, B accepted with reduce, C accepted with
  stop_replenishing, D accepted with reduce, E dismissed (the reason
  "Seasonal stock"), F accepted with dispose and G accepted with
  stop_replenishing each answer 201 with exactly the keys of
  ``ExcessDecision``; for each, ``for_decisions`` on the stored
  decision's id answers the remedy
  ``GET /api/excess?limit=1000&include_decided=true&include_remedy=
  true`` then answers for the row — A's exactly the
  stop_replenishing remedy with 42.25 on order test 1331 expects,
  E's dismissal stores its proposal too, and F, whose row has no
  remedy, stores no proposal row;
* the reads — each of those posts called ``list_rows`` once with
  ``(10**9, 0)`` and ``list_latest`` once;
* never in the way — a post signed in with the user token answers
  403 as before, calls neither read and stores nothing, and no
  answer of a post, no row of ``GET /api/excess/decisions`` and no
  ``decision`` object on a row of ``GET /api/excess`` carries a
  ``proposed_remedy`` key;
* a failed read — with ``list_rows`` raising, a decision on A is
  stored with no proposal row, and the same with ``list_latest``
  raising; each post succeeds;
* the counts — after every stored decision is deleted (the earlier
  posts are not counted, the cascade removing their proposals), the
  store is seeded through the two repositories with nine current
  decisions at nine item-sites (five with a stored proposal) and
  ``GET /api/excess/proposals`` answers exactly the counts of the
  seed; the user token gets the same answer and no token gets 401,
  booted the production way (``GET /api/auth/config`` public and
  ``GET /api/auth/me`` resolving the viewer, the role user);
* the migration — the script directory has exactly one head and the
  revision whose ``down_revision`` is
  ``0045_stockout_decision_proposals`` is in its history; the test
  database has the table and the constraint: storing
  ``{"kind": "expedite"}`` through ``insert`` for a stored decision
  that has no proposal raises ``psycopg2.errors.CheckViolation`` and
  stores nothing.
"""

import os
from datetime import date

import psycopg2
import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.excess as excess_api
import app.api.excess_decisions as decisions_api
from app.api.excess_decisions import ExcessDecision
from app.db.connection import get_connection
from app.main import app
from app.repositories.excess_decision_proposals_postgres_repository import (
    ExcessDecisionProposalPostgresRepository,
)
from app.repositories.excess_decisions_postgres_repository import (
    ExcessDecisionPostgresRepository,
)

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

EXCESS_URL = "/api/excess"
DECISIONS_URL = "/api/excess/decisions"
PROPOSALS_URL = "/api/excess/proposals"
SITE = "159"
AS_OF = date(2026, 11, 2)
SEED_SITE = "191"


def _excess_row(
    item,
    rank,
    category,
    dead_reason,
    on_hand,
    unit_cost,
    last_demand_on,
    justified,
    excess,
    window_units,
    months_of_cover,
):
    """One stand-in excess row: every ExcessRow field filled, at the
    fixed as-of, at site 159 (the stand-ins of test 1331)."""
    return {
        "rank": rank,
        "category": category,
        "dead_reason": dead_reason,
        "item_code": item,
        "site_code": SITE,
        "on_hand_units": on_hand,
        "unit_cost": unit_cost,
        "last_demand_on": last_demand_on,
        "demand_window_units": window_units,
        "months_of_cover": months_of_cover,
        "justified_units": justified,
        "excess_units": excess,
        "money_tied_up": (
            round(excess * unit_cost, 2) if unit_cost is not None else None
        ),
        "as_of": AS_OF,
        "notes": [f"note {item}"],
        "arithmetic": f"{item} arithmetic",
        "item_description": f"Item {item}",
    }


ROWS = [
    _excess_row(
        "A",
        1,
        "excess",
        None,
        1100.0,
        2.5,
        date(2026, 10, 20),
        100.0,
        400.0,
        100.0,
        8.5,
    ),
    _excess_row(
        "B", 2, "dead", "stopped", 300.0, 3.0, date(2025, 3, 15), 0.0, 300.0, 0.0, None
    ),
    _excess_row(
        "C", 3, "excess", None, 900.0, 4.0, date(2026, 10, 1), 200.0, 700.0, 200.0, 6.2
    ),
    _excess_row(
        "D", 4, "excess", None, 1075.5, 5.0, date(2026, 9, 30), 200.0, 875.5, 200.0, 9.1
    ),
    _excess_row(
        "E", 5, "dead", "stopped", 50.0, 6.0, date(2024, 12, 1), 0.0, 50.0, 0.0, None
    ),
    _excess_row("F", 6, "dead", "never_moved", 20.0, None, None, 0.0, 20.0, 0.0, None),
    _excess_row("G", 7, "dead", "never_moved", 10.0, 1.0, None, 0.0, 10.0, 0.0, None),
]


def _item_site_row(item, site, reorder_point, max_qty, safety_stock=0):
    return {
        "item_code": item,
        "site_code": site,
        "reorder_point": reorder_point,
        "max_qty": max_qty,
        "safety_stock": safety_stock,
    }


# E has no item_sites row.  D's safety stock (900) and its levels at
# 160 are there for the rows to ignore (the stand-ins of test 1331).
ITEM_SITE_ROWS = [
    _item_site_row("A", SITE, 20, 150),
    _item_site_row("B", SITE, 40, None),
    _item_site_row("C", SITE, 75, 1500000),
    _item_site_row("D", SITE, 200, 150, safety_stock=900),
    _item_site_row("D", "160", 5000, 9000),
    _item_site_row("F", SITE, float("nan"), 0),
    _item_site_row("G", SITE, 5, float("nan")),
]


def _open_po_line(item, site, open_units, promised_on):
    return {
        "item_code": item,
        "site_code": site,
        "open_units": open_units,
        "promised_on": promised_on,
    }


OPEN_PO_LINES = [
    _open_po_line("A", SITE, 12.25, date(2025, 1, 10)),
    _open_po_line("A", SITE, 30, date(2026, 11, 20)),
    _open_po_line("A", SITE, 0, date(2026, 11, 5)),
    _open_po_line("A", "160", 99, date(2026, 11, 15)),
    _open_po_line("C", SITE, -5, date(2026, 11, 18)),
]

# The remedy each stand-in row is proposed — the same remedies test
# 1331 proves GET /api/excess?include_remedy=true answers.
REMEDIES = {
    # A's on-order rule comes before its levels: 12.25 + 30, its
    # zero line and its line at 160 not counting, whatever the
    # promised dates.
    "A": {
        "kind": "stop_replenishing",
        "on_order_units": 42.25,
        "current_reorder_point": 20,
        "current_max_qty": 150,
        "reason": "42.25 units are still on order, adding to the excess. Stop buying it first.",
    },
    "B": {
        "kind": "stop_replenishing",
        "on_order_units": 0,
        "current_reorder_point": 40,
        "current_max_qty": None,
        "reason": (
            "The ERP still reorders it: its reorder point (40) or max "
            "(none) is above the 0 units its demand justifies. Stop buying it first."
        ),
    },
    "C": {
        "kind": "stop_replenishing",
        "on_order_units": 0,
        "current_reorder_point": 75,
        "current_max_qty": 1500000,
        "reason": (
            "The ERP still reorders it: its reorder point (75) or max "
            "(1500000) is above the 200 units its demand justifies. Stop buying it first."
        ),
    },
    "D": {
        "kind": "reduce",
        "on_order_units": 0,
        "current_reorder_point": 200,
        "current_max_qty": 150,
        "reason": (
            "Still in demand (last on 2026-09-30), but 875.5 units are "
            "above the 200 units its demand justifies. Use it up, "
            "transfer it or return it."
        ),
    },
    "E": {
        "kind": "dispose",
        "on_order_units": 0,
        "current_reorder_point": None,
        "current_max_qty": None,
        "reason": (
            "No demand since 2024-12-01. Scrap or sell it: the planner "
            "decides, and any write-down belongs to finance."
        ),
    },
    # F: it never moved, and the ERP no longer buys it: its reorder
    # point is not a number and its max of 0 is not above its 0
    # justified units.
    "F": None,
    "G": {
        "kind": "stop_replenishing",
        "on_order_units": 0,
        "current_reorder_point": 5,
        "current_max_qty": None,
        "reason": (
            "The ERP still reorders it: its reorder point (5) or max "
            "(none) is above the 0 units its demand justifies. Stop buying it first."
        ),
    },
}


class _ExcessRanked:
    """The stand-in ranking: seven fixed rows at the fixed as-of,
    recording its as-of argument."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __call__(self, as_of=None):
        self.calls.append(as_of)
        return self.rows


class _Read:
    """The stand-in for one of the two remedy reads.

    Answers a fixed payload (or raises) and records every call's
    arguments, so a test can see when the read ran and with what.
    """

    def __init__(self, answer, raises=None):
        self.answer = answer
        self.raises = raises
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.answer


def _seed_remedy(kind: str, index: int) -> dict:
    """One full ExcessRemedy of ``kind``, every key filled, for the
    seeded proposal store."""
    return {
        "kind": kind,
        "on_order_units": 10.0 + index,
        "current_reorder_point": 20.0 + index,
        "current_max_qty": 100.0 + index,
        "reason": f"a seeded {kind} proposal",
    }


# The counts seed: nine current decisions at nine item-sites, each
# item-site's only decision — by decision, action and stored
# proposal's kind.
SEED = [
    ("S1", "accepted", "stop_replenishing", "stop_replenishing"),
    ("S2", "accepted", "reduce", "stop_replenishing"),
    ("S3", "accepted", "reduce", "reduce"),
    ("S4", "accepted", "dispose", "reduce"),
    ("S5", "accepted", "dispose", "dispose"),
    ("S6", "accepted", "stop_replenishing", None),
    ("S7", "accepted", "dispose", None),
    ("S8", "dismissed", "stop_replenishing", None),
    ("S9", "snoozed", "dispose", None),
]
EXPECTED_COUNTS = {
    "accepted": 7,
    "stop_replenishing": {"proposed": 2, "chosen": 1},
    "reduce": {"proposed": 2, "chosen": 1},
    "dispose": {"proposed": 1, "chosen": 1},
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
    ("A", _body("A", "accepted", action="stop_replenishing")),
    ("B", _body("B", "accepted", action="reduce")),
    ("C", _body("C", "accepted", action="stop_replenishing")),
    ("D", _body("D", "accepted", action="reduce")),
    ("E", _body("E", "dismissed", reason="Seasonal stock")),
    ("F", _body("F", "accepted", action="dispose")),
    ("G", _body("G", "accepted", action="stop_replenishing")),
]
REMEDY_PARAMS = {"limit": 1000, "include_decided": "true", "include_remedy": "true"}


def test_issue1336_freeform(client, monkeypatch):
    proposal_repo = ExcessDecisionProposalPostgresRepository()

    # -- The stand-ins: every read of the routes.  In app.api.excess,
    #    the list route's ranking, the two remedy reads (the decision
    #    route's proposed_remedy runs through the same module names, so
    #    these stand in for it too) and the item descriptions; in
    #    app.api.excess_decisions, the ranked read through which the
    #    POST finds the item-site's row. ------------------------------
    ranked = _ExcessRanked(ROWS)
    ranked_decisions = _ExcessRanked(ROWS)
    item_sites = _Read(ITEM_SITE_ROWS)
    open_po = _Read(OPEN_PO_LINES)
    monkeypatch.setattr(excess_api, "excess_ranked", ranked)
    monkeypatch.setattr(excess_api, "_item_descriptions", lambda: {})
    monkeypatch.setattr(excess_api._item_site_repo, "list_rows", item_sites)
    monkeypatch.setattr(excess_api._open_po_repo, "list_latest", open_po)
    monkeypatch.setattr(decisions_api, "excess_ranked", ranked_decisions)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    def _snapshot(
        item_sites_read=item_sites,
        open_po_read=open_po,
    ) -> tuple:
        return (
            len(item_sites_read.calls),
            len(open_po_read.calls),
            len(ranked_decisions.calls),
        )

    def _assert_post_reads(
        pre: tuple,
        item_sites_read=item_sites,
        open_po_read=open_po,
    ) -> None:
        # The post's own reads: each of the two once, and the route's
        # ranked read through which it finds the row.
        assert item_sites_read.calls[pre[0] :] == [((10**9, 0), {})]
        assert open_po_read.calls[pre[1] :] == [((), {})]
        assert ranked_decisions.calls[pre[2] :] == [None]

    # -- Stored with each decision: the seven posts. -------------------
    stored = {}
    for item, body in POSTS:
        pre = _snapshot()
        response = admin.post(DECISIONS_URL, json=body)
        assert response.status_code == 201, response.text
        answered = response.json()
        # The answer is exactly ExcessDecision's keys — no
        # proposed_remedy in it.
        assert set(answered.keys()) == set(ExcessDecision.model_fields)
        assert "proposed_remedy" not in answered
        _assert_post_reads(pre)
        decision_id = answered["id"]
        stored[item] = decision_id
        # The proposal answers the remedy the excess list then
        # answers for the row.
        listing = admin.get(EXCESS_URL, params=REMEDY_PARAMS)
        assert listing.status_code == 200, listing.text
        row = next(
            candidate for candidate in listing.json() if candidate["item_code"] == item
        )
        remedy = row["remedy"]
        expected = {decision_id: remedy} if remedy is not None else {}
        assert proposal_repo.for_decisions([decision_id]) == expected

    # A's proposal is exactly the stop_replenishing remedy with 42.25
    # on order test 1331 expects, E's dismissal stored its proposal
    # too, and F's row — no remedy — stored no proposal row.
    assert REMEDIES["A"]["on_order_units"] == 42.25
    assert proposal_repo.for_decisions([stored["A"]]) == {stored["A"]: REMEDIES["A"]}
    assert proposal_repo.for_decisions([stored["E"]]) == {stored["E"]: REMEDIES["E"]}
    assert proposal_repo.for_decisions([stored["F"]]) == {}

    # -- Answers unchanged: no proposed_remedy anywhere. ----------------
    response = admin.get(DECISIONS_URL, params={"limit": 1000})
    assert response.status_code == 200, response.text
    decisions_answered = response.json()
    assert len(decisions_answered) == len(POSTS)
    for row in decisions_answered:
        assert "proposed_remedy" not in row
    response = admin.get(EXCESS_URL, params={"limit": 1000, "include_decided": "true"})
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["item_code"] for row in rows] == list("ABCDEFG")
    for row in rows:
        assert row["decision"] is not None
        assert "proposed_remedy" not in row
        assert "proposed_remedy" not in row["decision"]

    # -- Never in the way: the user token is refused as before. ---------
    pre = _snapshot()
    response = viewer.post(
        DECISIONS_URL, json=_body("A", "accepted", action="stop_replenishing")
    )
    assert response.status_code == 403
    assert item_sites.calls[pre[0] :] == []
    assert open_po.calls[pre[1] :] == []
    assert ranked_decisions.calls[pre[2] :] == []
    # ... and it stored nothing: A's history is unchanged.
    history = admin.get(
        DECISIONS_URL, params={"item_code": "A", "site_code": SITE}
    ).json()
    assert len(history) == 1  # A's one accepted decision, no more
    assert proposal_repo.for_decisions([stored["A"]]) == {stored["A"]: REMEDIES["A"]}

    # -- A failed read: list_rows raises; the decision on A is stored
    #    with no proposal row. -------------------------------------------
    item_sites_down = _Read(None, raises=RuntimeError("the item_sites read is down"))
    monkeypatch.setattr(excess_api._item_site_repo, "list_rows", item_sites_down)
    pre = _snapshot(item_sites_read=item_sites_down)
    response = admin.post(
        DECISIONS_URL, json=_body("A", "accepted", action="stop_replenishing")
    )
    assert response.status_code == 201, response.text
    assert set(response.json().keys()) == set(ExcessDecision.model_fields)
    # Both reads ran — the two reads are independent — but with the
    # levels read down the stop-replenishing rules cannot be checked:
    # no remedy, no proposal row.
    assert item_sites_down.calls[pre[0] :] == [((10**9, 0), {})]
    assert open_po.calls[pre[1] :] == [((), {})]
    failed_list_rows_id = response.json()["id"]
    assert proposal_repo.for_decisions([failed_list_rows_id]) == {}
    monkeypatch.setattr(excess_api._item_site_repo, "list_rows", item_sites)

    # -- A failed read: list_latest raises; the decision on A is stored
    #    with no proposal row. --------------------------------------------
    open_po_down = _Read(
        None, raises=RuntimeError("the open purchase lines read is down")
    )
    monkeypatch.setattr(excess_api._open_po_repo, "list_latest", open_po_down)
    pre = _snapshot(open_po_read=open_po_down)
    response = admin.post(
        DECISIONS_URL, json=_body("A", "accepted", action="stop_replenishing")
    )
    assert response.status_code == 201, response.text
    assert set(response.json().keys()) == set(ExcessDecision.model_fields)
    assert item_sites.calls[pre[0] :] == [((10**9, 0), {})]
    assert open_po_down.calls[pre[1] :] == [((), {})]
    failed_list_latest_id = response.json()["id"]
    assert proposal_repo.for_decisions([failed_list_latest_id]) == {}
    monkeypatch.setattr(excess_api._open_po_repo, "list_latest", open_po)

    # -- The counts.  Every stored decision is deleted first, so the
    #    earlier posts are not counted — the foreign key's cascade
    #    removes their proposals with them. -------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM excess_decisions")
        conn.commit()
    finally:
        conn.close()
    all_ids = list(stored.values()) + [failed_list_rows_id, failed_list_latest_id]
    assert proposal_repo.for_decisions(all_ids) == {}  # the cascade
    assert ExcessDecisionPostgresRepository().current_all() == []

    # The proposal store, seeded through the two repositories: nine
    # current decisions at nine item-sites, each item-site's only
    # decision.
    decision_repo = ExcessDecisionPostgresRepository()
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
            category="excess",
            dead_reason=None,
            on_hand_units=1.0,
            excess_units=1.0,
            money_tied_up=1.0,
            decided_by="seed",
            decided_by_name=None,
        )["id"]
        if kind is not None:
            proposal_repo.insert(seed_ids[item], _seed_remedy(kind, index))

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
        if script.down_revision == "0045_stockout_decision_proposals"
    ]
    assert len(successors) == 1
    assert successors[0].revision == head
    # The constraint: a remedy kind the decision's actions do not carry
    # is a check violation, and nothing is stored.  S6 is a stored
    # decision that has no proposal.
    s6_id = seed_ids["S6"]
    with pytest.raises(psycopg2.errors.CheckViolation):
        proposal_repo.insert(s6_id, {"kind": "expedite"})
    assert proposal_repo.for_decisions([s6_id]) == {}
