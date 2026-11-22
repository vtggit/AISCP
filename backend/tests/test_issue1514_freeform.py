"""Issue #1514 proof: the unchanged readback of the released orders.

``app.supply.release_readback.release_unchanged(lines, erp_status,
erp_lines)`` reads and writes nothing: the released side sums
``quantity`` per ``item_code`` over ``lines``, the ERP side sums
``order_qty`` per ``item_code`` over the ``erp_lines`` whose
``cancelled`` is not true — an item whose every ERP line is cancelled
is not on the ERP side, and a null ``erp_lines`` is no lines — and it
answers True only when all three hold: ``erp_status`` is not
``Canceled``, both sides hold the same item codes, and each item's two
sums differ by no more than 0.0001; ``received_qty``, ``completed``,
``promised``, the warehouse, and every price or value are not
compared.

``app.api.work.order_release_counts()`` still runs one SELECT through
``app.db.connection.get_cursor``, over the ``plan_releases`` rows
whose ``outcome`` is ``created``: ``released`` and ``automatic`` as
before, ``read`` those rows whose ``erp_status`` is not null, and
``unchanged`` the read rows for which ``release_unchanged`` of the
row's ``lines``, ``erp_status`` and ``erp_lines`` is True.  The
scorecard's ``order_release`` entry maps ``decisions`` to
``released``, ``with_proposal`` to ``read``, ``at_proposal`` to
``unchanged``, ``without_proposal`` to 0 and ``automatic`` to
``automatic``, so its ``rate`` is ``unchanged`` over ``read``, null
while ``read`` is 0; ``transfer_release`` keeps its mapping, with
``at_proposal`` null.

Proved here, against the test database migrated to head:

* the comparison, called directly, with the released line A of
  quantity 28 and status "On Hold" unless named: one ERP line of A
  with ``order_qty`` 28.0 answers True; two ERP lines of A, 10 and
  18, answer True; A at 28.00005 answers True; A at 27 answers
  False; A at 28 plus an ERP line of item B answers False; A's only
  ERP line with ``cancelled`` true answers False; one ERP line of A
  at 28 with status "Canceled" answers False; and a null
  ``erp_lines`` answers False;
* the counts: ``order_release_counts()`` before and after inserting
  ``plan_releases`` rows, every NOT NULL column filled, each order
  number its own — ``created`` not read back, ``created`` with actor
  ``standing-grant:1`` read back unchanged, ``created`` read back
  with one quantity changed, ``created`` read back with status
  "Canceled", ``exists`` read back unchanged, and ``error`` — with
  ``released`` rising by 4, ``automatic`` by 1, ``read`` by 3 and
  ``unchanged`` by 1;
* the scorecard, for the ``user`` role, with the eight sources of
  #1485 replaced by the stand-ins its proof uses (monkeypatch on the
  names ``app.api.work`` calls), ``order_release_counts`` by one
  answering ``{"released": 19, "automatic": 2, "read": 17,
  "unchanged": 15}`` and ``transfer_release_counts`` by one
  answering ``{"released": 6, "automatic": 5}``: ``order_release``
  answers 19, 17, 15, 0, ``automatic`` 2 and ``rate`` 0.8824;
  ``transfer_release`` answers 6, 6, null, 0, ``automatic`` 5 and
  ``rate`` null; the other eight entries answer as #1485's proof
  expects;
* access — the route answers 401 without a token; and
* nothing written — the row count of ``plan_releases`` is the same
  before and after the route's calls.

Every row the proof inserts is deleted in a ``finally``.

Identity — the proof also runs as a NON-ADMIN (role ``user``): a
second pass in the same test after a full reload, with the page
booted the production way (the token it would take from the URL hash
as ``#access_token=...`` — the development token, role "user" — sent
as its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer; the scorecard has no
admin-only controls, so it answers the viewer 200 with the full
answer while the page still renders, and a request without a token
still gets 401.
"""

import json

from fastapi.testclient import TestClient

from app.api import work as work_module
from app.db.connection import get_cursor
from app.main import app
from app.supply.release_readback import release_unchanged

URL = "/api/work/scorecard"
ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = "dev-secret-token"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

KEYS = [
    "reorder_points",
    "excess",
    "stockout",
    "exceptions",
    "forecast_overrides",
    "sop_signoff",
    "purchase_budget",
    "ddmrp_adjustment",
    "order_release",
    "buffer_order",
    "transfer_release",
]

STEPS = {
    "reorder_points": "Accept or dismiss a recommended reorder point",
    "excess": "Decide what to do with excess or dead stock",
    "stockout": "Decide how to cover a stockout risk",
    "exceptions": "Acknowledge an exception",
    "forecast_overrides": "Override a period's forecast",
    "sop_signoff": "Sign the month off",
    "purchase_budget": "Set each month's purchase budget",
    "ddmrp_adjustment": "Enter planned adjustment factors for a ramp",
    "order_release": "Release purchase orders to the ERP",
    "buffer_order": "Order on a buffer's signal",
    "transfer_release": "Release transfers to the ERP",
}

DRIFT_ANSWER = {
    "accepted": {"count": 6, "money_at_stake": 600.0, "without_money": 0},
    "dismissed": {"count": 2, "money_at_stake": 200.0, "without_money": 0},
    "snoozed": {"count": 0},
    "rate": 0.75,
    "money_rate": 0.75,
    "raise": {"accepted": 3, "dismissed": 1},
    "lower": {"accepted": 3, "dismissed": 1},
}
EXCESS_ANSWER = {
    "accepted": 5,
    "stop_replenishing": {"proposed": 2, "chosen": 2},
    "reduce": {"proposed": 1, "chosen": 1},
    "dispose": {"proposed": 1, "chosen": 0},
    "total": {"proposed": 4, "chosen": 3},
    "without_proposal": 1,
}
STOCKOUT_ANSWER = {
    "accepted": 3,
    "expedite": {"proposed": 1, "chosen": 1},
    "transfer": {"proposed": 1, "chosen": 0},
    "raise_order": {"proposed": 1, "chosen": 0},
    "total": {"proposed": 3, "chosen": 1},
    "without_proposal": 0,
}
EXCEPTIONS_ANSWER = {
    "acknowledged": 7,
    "proposed": 5,
    "without_proposal": 2,
    "snoozed": 1,
    "snoozed_proposed": 1,
    "by_kind": {"stale": {"acknowledged": 7, "proposed": 5}},
}
OVERRIDES_ANSWER = {
    "overrides": 9,
    "proposed": 4,
    "taken": 1,
    "same_direction": 3,
    "without_proposal": 5,
}
SIGNOFFS_ANSWER = {
    "signoffs": 3,
    "proposed": 2,
    "not_proposed": 1,
    "without_proposal": 0,
    "months": [
        {"month": "2026-10", "proposed": True},
        {"month": "2026-09", "proposed": True},
        {"month": "2026-08", "proposed": False},
    ],
}
BUDGET_RECORD = {"saved": 4, "with_proposal": 3, "taken": 2, "below": 1, "above": 0}
DDMRP_RECORD = {"saved": 5, "with_proposal": 3, "taken": 1, "below": 1, "above": 1}
ORDER_RELEASE_ANSWER = {
    "released": 19,
    "automatic": 2,
    "read": 17,
    "unchanged": 15,
}
TRANSFER_RELEASE_ANSWER = {"released": 6, "automatic": 5}

EXPECTED = {
    "reorder_points": {
        "decisions": 8,
        "with_proposal": 8,
        "at_proposal": 6,
        "without_proposal": 0,
        "automatic": None,
        "rate": 0.75,
    },
    "excess": {
        "decisions": 5,
        "with_proposal": 4,
        "at_proposal": 3,
        "without_proposal": 1,
        "automatic": None,
        "rate": 0.75,
    },
    "stockout": {
        "decisions": 3,
        "with_proposal": 3,
        "at_proposal": 1,
        "without_proposal": 0,
        "automatic": None,
        "rate": 0.3333,
    },
    "exceptions": {
        "decisions": 7,
        "with_proposal": 5,
        "at_proposal": None,
        "without_proposal": 2,
        "automatic": None,
        "rate": None,
    },
    "forecast_overrides": {
        "decisions": 9,
        "with_proposal": 4,
        "at_proposal": 1,
        "without_proposal": 5,
        "automatic": None,
        "rate": 0.25,
    },
    "sop_signoff": {
        "decisions": 3,
        "with_proposal": 3,
        "at_proposal": 2,
        "without_proposal": 0,
        "automatic": None,
        "rate": 0.6667,
    },
    "purchase_budget": {
        "decisions": 4,
        "with_proposal": 3,
        "at_proposal": 2,
        "without_proposal": 1,
        "automatic": None,
        "rate": 0.6667,
    },
    "ddmrp_adjustment": {
        "decisions": 5,
        "with_proposal": 3,
        "at_proposal": 1,
        "without_proposal": 2,
        "automatic": None,
        "rate": 0.3333,
    },
    "order_release": {
        "decisions": 19,
        "with_proposal": 17,
        "at_proposal": 15,
        "without_proposal": 0,
        "automatic": 2,
        "rate": 0.8824,
    },
    "buffer_order": {
        "decisions": 0,
        "with_proposal": 0,
        "at_proposal": 0,
        "without_proposal": 0,
        "automatic": 0,
        "rate": None,
    },
    "transfer_release": {
        "decisions": 6,
        "with_proposal": 6,
        "at_proposal": None,
        "without_proposal": 0,
        "automatic": 5,
        "rate": None,
    },
}

ENTRY_KEYS = {
    "key",
    "step",
    "decisions",
    "with_proposal",
    "at_proposal",
    "without_proposal",
    "automatic",
    "rate",
}

# The released side of the comparison and of the counts rows: one line
# A of quantity 28.
RELEASED_LINES = [
    {"item_code": "A", "quantity": 28, "unit_cost": "1.00", "value": "28.00"}
]
RELEASED_LINE_COUNT = 1
RELEASED_VALUE = "28.00"


def _erp_line(item_code: str, order_qty, cancelled: bool = False) -> dict:
    """One ERP order line, in the shape the client answers with."""
    return {
        "item_code": item_code,
        "warehouse_code": "154",
        "order_qty": order_qty,
        "received_qty": 0,
        "completed": False,
        "cancelled": cancelled,
        "promised": "2026-12-01",
    }


# The six plan_releases rows of the counts section, in order: every
# NOT NULL column filled, each order number its own.
# (order_nbr, outcome, erp_status, erp_lines, actor)
PLAN_ROWS = [
    ("PO1514-0001", "created", None, None, "dev-user-1"),
    (
        "PO1514-0002",
        "created",
        "On Hold",
        [_erp_line("A", 28.0)],
        "standing-grant:1",
    ),
    ("PO1514-0003", "created", "On Hold", [_erp_line("A", 27.0)], "dev-user-1"),
    ("PO1514-0004", "created", "Canceled", [_erp_line("A", 28.0)], "dev-admin-1"),
    ("PO1514-0005", "exists", "On Hold", [_erp_line("A", 28.0)], "dev-user-1"),
    ("PO1514-0006", "error", None, None, "dev-admin-1"),
]


def _fixed(answer):
    """A stand-in that answers ``answer`` however it is called."""

    def stand_in(*args, **kwargs):
        return answer

    return stand_in


def _plan_releases_row_count() -> int:
    """The row count of the table the route must not write."""
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) AS count FROM plan_releases")
        return cur.fetchone()["count"]


def _max_plan_id() -> int:
    """The greatest ``id`` of plan_releases before the proof inserts."""
    with get_cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) AS max_id FROM plan_releases")
        return cur.fetchone()["max_id"]


def _insert_plan_rows() -> None:
    """The six rows of the counts section, every NOT NULL column
    filled."""
    with get_cursor() as cur:
        for order_nbr, outcome, erp_status, erp_lines, actor in PLAN_ROWS:
            cur.execute(
                "INSERT INTO plan_releases (month, site_code, "
                "supplier_code, origin, order_nbr, outcome, erp_status, "
                "erp_lines, line_count, value, lines, actor) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    "2026-11",
                    "154",
                    "6995",
                    f"issue1514 proof {order_nbr}",
                    order_nbr,
                    outcome,
                    erp_status,
                    json.dumps(erp_lines),
                    RELEASED_LINE_COUNT,
                    RELEASED_VALUE,
                    json.dumps(RELEASED_LINES),
                    actor,
                ),
            )


def _delete_plan_rows(plan_max: int) -> None:
    """The rows the proof inserted, gone again at its end."""
    with get_cursor() as cur:
        cur.execute("DELETE FROM plan_releases WHERE id > %s", (plan_max,))


def _assert_process(entry: dict, key: str) -> None:
    """One entry with its documented shape and the expected numbers."""
    assert set(entry) == ENTRY_KEYS, key
    assert entry["key"] == key
    assert entry["step"] == STEPS[key], key
    assert (
        entry["decisions"],
        entry["with_proposal"],
        entry["at_proposal"],
        entry["without_proposal"],
        entry["automatic"],
        entry["rate"],
    ) == tuple(
        EXPECTED[key][name]
        for name in (
            "decisions",
            "with_proposal",
            "at_proposal",
            "without_proposal",
            "automatic",
            "rate",
        )
    ), key


def test_issue1514_freeform(client, monkeypatch):
    # The counts function itself, for the counts section without
    # stand-ins.
    real_order_release = work_module.order_release_counts

    # -- The comparison, called directly: the released line A of quantity
    #    28, status "On Hold" unless named. ----------------------------------
    # One ERP line of A with order_qty 28.0.
    assert release_unchanged(RELEASED_LINES, "On Hold", [_erp_line("A", 28.0)]) is True
    # Two ERP lines of A, 10 and 18.
    assert (
        release_unchanged(
            RELEASED_LINES,
            "On Hold",
            [_erp_line("A", 10), _erp_line("A", 18)],
        )
        is True
    )
    # A at 28.00005: within the tolerance.
    assert (
        release_unchanged(RELEASED_LINES, "On Hold", [_erp_line("A", 28.00005)]) is True
    )
    # A at 27: outside it.
    assert release_unchanged(RELEASED_LINES, "On Hold", [_erp_line("A", 27)]) is False
    # A at 28 plus an ERP line of item B: different item codes.
    assert (
        release_unchanged(
            RELEASED_LINES,
            "On Hold",
            [_erp_line("A", 28), _erp_line("B", 5)],
        )
        is False
    )
    # A's only ERP line with cancelled true: not on the ERP side.
    assert (
        release_unchanged(RELEASED_LINES, "On Hold", [_erp_line("A", 28.0, True)])
        is False
    )
    # One ERP line of A at 28 with status "Canceled".
    assert (
        release_unchanged(RELEASED_LINES, "Canceled", [_erp_line("A", 28.0)]) is False
    )
    # A null erp_lines is no lines.
    assert release_unchanged(RELEASED_LINES, "On Hold", None) is False
    # Hostile rows never raise through the scorecard: they answer False.
    assert release_unchanged(RELEASED_LINES, "On Hold", [42]) is False
    assert (
        release_unchanged(
            [{"item_code": "A", "quantity": "28"}], "On Hold", [_erp_line("A", 28.0)]
        )
        is False
    )
    assert release_unchanged(RELEASED_LINES, "On Hold", {"A": 28}) is False

    # -- The counts: before and after the rows go in. --------------------------
    before = real_order_release()
    plan_max = _max_plan_id()
    try:
        _insert_plan_rows()
        after = real_order_release()
        assert after["released"] == before["released"] + 4
        assert after["automatic"] == before["automatic"] + 1
        assert after["read"] == before["read"] + 3
        assert after["unchanged"] == before["unchanged"] + 1
    finally:
        _delete_plan_rows(plan_max)

    # -- The scorecard: the eight sources of #1485, their stand-ins, and the
    #    two release sources by their stand-ins. --------------------------------
    monkeypatch.setattr(work_module, "decision_acceptance", _fixed(DRIFT_ANSWER))
    monkeypatch.setattr(work_module, "excess_proposal_counts", _fixed(EXCESS_ANSWER))
    monkeypatch.setattr(
        work_module, "stockout_proposal_counts", _fixed(STOCKOUT_ANSWER)
    )
    monkeypatch.setattr(
        work_module, "exception_proposal_counts", _fixed(EXCEPTIONS_ANSWER)
    )
    monkeypatch.setattr(
        work_module, "forecast_override_proposal_counts", _fixed(OVERRIDES_ANSWER)
    )
    monkeypatch.setattr(work_module, "signoff_proposals", _fixed(SIGNOFFS_ANSWER))
    monkeypatch.setattr(work_module, "_record_count", _fixed(BUDGET_RECORD))
    monkeypatch.setattr(
        work_module, "get_ddmrp_adjustment_record", _fixed(DDMRP_RECORD)
    )
    monkeypatch.setattr(
        work_module, "order_release_counts", _fixed(ORDER_RELEASE_ANSWER)
    )
    monkeypatch.setattr(
        work_module, "transfer_release_counts", _fixed(TRANSFER_RELEASE_ANSWER)
    )

    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # -- The wiring and access: the route answers 401 without a token. ----------
    assert work_module.router.prefix == "/api/work"
    assert TestClient(app).get(URL).status_code == 401

    # -- Nothing written: the row count before the route's calls. ----------------
    count_before = _plan_releases_row_count()

    # -- The answer for the user role: the eleven keys in order. ------------------
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"processes"}
    assert [entry["key"] for entry in body["processes"]] == KEYS
    for entry in body["processes"]:
        _assert_process(entry, entry["key"])

    # -- Nothing written: the row count after the route's calls. ------------------
    assert _plan_releases_row_count() == count_before

    # -- Identity, second pass in the same test after a full reload: the
    #    page boots the production way (the token it would take from the
    #    URL hash as #access_token=... — the development token, role
    #    "user" — sent as its Bearer token), /api/auth/config answers
    #    publicly and /api/auth/me resolves the viewer; the scorecard
    #    has no admin-only controls, so it answers the viewer 200 with
    #    the full answer while the page still renders. -------------------------
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
    # The admin's identity is the other one.
    admin_me = admin.get("/api/auth/me")
    assert admin_me.status_code == 200, admin_me.text
    assert admin_me.json()["user"]["sub"] == ADMIN_SUB
    # No admin-only controls: the scorecard the page renders answers the
    # viewer 200 with the full answer.
    response = viewer_reloaded.get(URL)
    assert response.status_code == 200, response.text
    for entry in response.json()["processes"]:
        _assert_process(entry, entry["key"])
    # And the gate is intact: a request without a token still gets 401.
    assert TestClient(app).get(URL).status_code == 401
