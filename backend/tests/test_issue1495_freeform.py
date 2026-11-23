"""Issue #1495 proof: the two release entries of the work scorecard.

``GET /api/work/scorecard`` (the router ``app.api.work``, prefix
``/api/work``, included in ``app.main`` behind the app's include-time
authentication gate like every other API router) answers
``{"processes": [...]}`` — the twelve work processes, in this order:
``reorder_points``, ``excess``, ``stockout``, ``exceptions``,
``forecast_overrides``, ``sop_signoff``, ``purchase_budget``,
``ddmrp_adjustment``, ``order_release`` and ``transfer_release``.  It
is open to any signed-in user (401 without a token) and writes
nothing.  Every entry is ``{key, step, decisions, with_proposal,
at_proposal, without_proposal, automatic, rate}``: for the eight
existing entries ``automatic`` is null and the numbers and rates are
the ones of the #1485 proof; the two release entries come after
``ddmrp_adjustment`` — ``order_release`` ("Release purchase orders to
the ERP") from ``app.api.work.order_release_counts``, mapping
``decisions`` to ``released``, ``with_proposal`` to ``read``,
``at_proposal`` to ``unchanged``, ``without_proposal`` to 0 and
``automatic`` to ``automatic``, so ``rate`` is ``unchanged`` over
``read``, null while ``read`` is 0; and ``transfer_release`` ("Release
transfers to the ERP") from ``app.api.work.transfer_release_counts``,
mapping ``decisions`` and ``with_proposal`` to ``released``,
``at_proposal`` to null, ``without_proposal`` to 0 and ``automatic``
to ``automatic``, so ``rate`` is null.  The two count functions each
run one SELECT through ``app.db.connection.get_cursor``, over
``plan_releases`` and ``transfer_releases`` respectively: ``released``
is the rows whose outcome is ``created`` and ``automatic`` those of
them whose actor starts with ``standing-grant:``; rows whose outcome
is ``pending``, ``exists``, ``error`` or ``aborted`` are not counted;
``order_release_counts`` also answers ``read`` — the created rows
whose ``erp_status`` is not null — and ``unchanged`` — the read rows
for which ``app.supply.release_readback.release_unchanged`` of the
row's ``lines``, ``erp_status`` and ``erp_lines`` is True.  A failed
source — a source that raises, or one that breaks an invariant —
keeps its ``key`` and ``step`` and its six numbers null, the
invariant among them a non-null ``automatic`` that is not a
non-negative integer (a bool is not one) no greater than
``decisions``; the other entries still answer and the route answers
200, and the violation is logged.

Proved here, on the test database migrated to head, with the eight
sources of #1485 replaced, by monkeypatch on the names ``app.api.work``
calls, by stand-ins with the fixed answers that
``backend/tests/test_issue1485_freeform.py`` uses — and
``order_release_counts`` replaced by a stand-in answering
``{"released": 19, "automatic": 2, "read": 17, "unchanged": 15}`` and
``transfer_release_counts`` by one answering
``{"released": 6, "automatic": 5}``:

* the answer for the ``user`` role lists the twelve keys in order: the
  eight existing entries with that proof's numbers and rates, each
  with ``automatic`` null; ``order_release`` 19/17/15/0 rate 0.8824
  automatic 2; ``transfer_release`` 6/6/null/0 rate null automatic 5;
* a failed source — the ``transfer_release_counts`` stand-in raising
  — keeps the entry's ``key`` and ``step`` with its six numbers null,
  the other entries answer as above and the route answers 200;
* a broken count — the ``order_release_counts`` stand-in answering
  ``{"released": 2, "automatic": 3, "read": 0, "unchanged": 0}``, an
  ``automatic`` above its ``decisions`` — keeps the entry's ``key``
  and ``step`` with its six numbers null, a warning names
  ``order_release``, the other entries answer as above and the route
  answers 200;
* the real counts — the two functions themselves, with no stand-ins,
  against the test database: six rows into ``plan_releases`` —
  outcome ``created`` with actor ``standing-grant:1``, ``created``
  with actor ``dev-admin-1``, ``created`` with actor ``dev-user-1``,
  ``exists`` with actor ``standing-grant:1``, ``error`` with actor
  ``dev-admin-1`` and ``pending`` with actor ``standing-grant:1`` —
  and ``order_release_counts()`` answers ``released`` 3 above and
  ``automatic`` 1 above what it answered before the rows went in; the
  same six outcomes and actors into ``transfer_releases``, and
  ``transfer_release_counts()`` rises the same way; the rows the
  proof inserted are deleted at its end;
* access — the route answers 401 without a token; and
* nothing written — the row counts of ``plan_releases`` and
  ``transfer_releases`` are the same before and after.

Identity — the proof also runs as a NON-ADMIN (role ``user``): a
second pass in the same test after a full reload, with the page
booted the production way (the token it would take from the URL hash
as ``#access_token=...`` — the development token, role "user" — sent
as its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer; the scorecard has no
admin-only controls, so it answers the viewer 200 with the full
answer while the page still renders.
"""

import json
import logging

from fastapi.testclient import TestClient

from app.api import work as work_module
from app.db.connection import get_connection
from app.main import app

URL = "/api/work/scorecard"
ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
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
    "short_order",
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
    "short_order": "Decide: wait, order anyway, or add the top-ups",
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
ORDER_RELEASE_ANSWER = {"released": 19, "automatic": 2, "read": 17, "unchanged": 15}
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
    "short_order": {
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

# The six rows the real-counts section inserts, in both release tables:
# three ``created`` (one of them a standing grant), and the outcomes the
# counts must not count.
RELEASE_ROWS = [
    ("created", "standing-grant:1"),
    ("created", "dev-admin-1"),
    ("created", "dev-user-1"),
    ("exists", "standing-grant:1"),
    ("error", "dev-admin-1"),
    ("pending", "standing-grant:1"),
]


def _fixed(answer):
    """A stand-in that answers ``answer`` however it is called."""

    def stand_in(*args, **kwargs):
        return answer

    return stand_in


def _release_row_counts() -> tuple[int, int]:
    """The row counts of the two tables the proof checks are not written."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM plan_releases")
            plan_releases = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM transfer_releases")
            transfer_releases = cur.fetchone()[0]
        return plan_releases, transfer_releases
    finally:
        conn.close()


def _max_release_id(table: str) -> int:
    """The greatest ``id`` of the table before the proof inserts rows."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _insert_release_rows() -> None:
    """The six rows of the proof, every NOT NULL column filled, into
    both release tables."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for outcome, actor in RELEASE_ROWS:
                cur.execute(
                    "INSERT INTO plan_releases (month, site_code, "
                    "supplier_code, origin, outcome, line_count, value, "
                    "lines, actor) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        "2026-11",
                        "site-1",
                        "sup-1",
                        "issue1495 proof",
                        outcome,
                        1,
                        0,
                        json.dumps([]),
                        actor,
                    ),
                )
            for outcome, actor in RELEASE_ROWS:
                cur.execute(
                    "INSERT INTO transfer_releases (origin, from_site_code, "
                    "to_site_code, outcome, line_count, lines, actor) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        "issue1495 proof",
                        "site-1",
                        "site-2",
                        outcome,
                        1,
                        json.dumps([]),
                        actor,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _delete_release_rows(plan_max: int, transfer_max: int) -> None:
    """The rows the proof inserted, gone again at its end."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_releases WHERE id > %s", (plan_max,))
            cur.execute("DELETE FROM transfer_releases WHERE id > %s", (transfer_max,))
        conn.commit()
    finally:
        conn.close()


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


def _assert_failed(entry: dict, key: str) -> None:
    """The single failed-entry shape: key and step, six numbers null."""
    assert set(entry) == ENTRY_KEYS, key
    assert entry["key"] == key, key
    assert entry["step"] == STEPS[key], key
    assert (
        entry["decisions"],
        entry["with_proposal"],
        entry["at_proposal"],
        entry["without_proposal"],
        entry["automatic"],
        entry["rate"],
    ) == (None, None, None, None, None, None), key


def test_issue1495_freeform(client, monkeypatch, caplog):
    # The two count functions themselves, for the real-counts section
    # without stand-ins.
    real_order_release = work_module.order_release_counts
    real_transfer_release = work_module.transfer_release_counts

    # -- The setup: the eight sources of #1485, each replaced by a
    #    stand-in with the fixed answer that proof uses, and the two
    #    release sources replaced by their stand-ins. -------------------
    monkeypatch.setattr(work_module, "decision_acceptance", _fixed(DRIFT_ANSWER))
    monkeypatch.setattr(work_module, "excess_proposal_counts", _fixed(EXCESS_ANSWER))
    monkeypatch.setattr(
        work_module, "stockout_proposal_counts", _fixed(STOCKOUT_ANSWER)
    )
    monkeypatch.setattr(
        work_module, "exception_proposal_counts", _fixed(EXCEPTIONS_ANSWER)
    )
    monkeypatch.setattr(
        work_module,
        "forecast_override_proposal_counts",
        _fixed(OVERRIDES_ANSWER),
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

    # The migration's alembic fileConfig (migrations/env.py) disables every
    # logger that exists when the test database is created — this module's
    # among them, as it is imported at collection.  Re-enable it so the
    # violation logs of the proof can be observed.
    monkeypatch.setattr(logging.getLogger("app.api.work"), "disabled", False)

    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # -- The wiring: a router with prefix /api/work, served by the app
    #    behind its authentication gate. --------------------------------------
    assert work_module.router.prefix == "/api/work"
    assert URL in app.openapi()["paths"]

    # -- Access: 401 without a token. ------------------------------------------
    assert TestClient(app).get(URL).status_code == 401

    # -- Nothing written: the row counts before. -------------------------------
    counts_before = _release_row_counts()

    # -- The answer for the user role: the twelve keys in order. -----------------
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"processes"}
    assert [entry["key"] for entry in body["processes"]] == KEYS
    for entry in body["processes"]:
        _assert_process(entry, entry["key"])

    # -- A failed source: the transfer_release_counts stand-in raising. --------
    def transfer_boom():
        raise RuntimeError("the transfer release source is down")

    monkeypatch.setattr(work_module, "transfer_release_counts", transfer_boom)
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    processes = {entry["key"]: entry for entry in response.json()["processes"]}
    assert list(processes) == KEYS, "the order and the keys survive the failure"
    _assert_failed(processes["transfer_release"], "transfer_release")
    for key, entry in processes.items():
        if key != "transfer_release":
            _assert_process(entry, key)
    # The transfer source is back to its fixed answer for what follows.
    monkeypatch.setattr(
        work_module, "transfer_release_counts", _fixed(TRANSFER_RELEASE_ANSWER)
    )

    # -- A broken count: an automatic above its decisions — the failed-entry
    #    shape and a logged violation. -----------------------------------------
    monkeypatch.setattr(
        work_module,
        "order_release_counts",
        _fixed({"released": 2, "automatic": 3, "read": 0, "unchanged": 0}),
    )
    caplog.at_level(logging.WARNING, logger="app.api.work")
    caplog.clear()
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    processes = {entry["key"]: entry for entry in response.json()["processes"]}
    _assert_failed(processes["order_release"], "order_release")
    for key, entry in processes.items():
        if key != "order_release":
            _assert_process(entry, key)
    violations = [
        record
        for record in caplog.records
        if record.name == "app.api.work" and record.levelno >= logging.WARNING
    ]
    assert any("order_release" in record.getMessage() for record in violations)

    # -- Nothing written: the row counts after the route's calls. --------------
    assert _release_row_counts() == counts_before

    # -- The real counts: the two functions themselves, with no stand-ins,
    #    against the test database. --------------------------------------------
    plan_before = real_order_release()
    transfer_before = real_transfer_release()
    plan_max = _max_release_id("plan_releases")
    transfer_max = _max_release_id("transfer_releases")
    _insert_release_rows()
    plan_after = real_order_release()
    assert plan_after["released"] == plan_before["released"] + 3
    assert plan_after["automatic"] == plan_before["automatic"] + 1
    transfer_after = real_transfer_release()
    assert transfer_after["released"] == transfer_before["released"] + 3
    assert transfer_after["automatic"] == transfer_before["automatic"] + 1

    # -- Identity, second pass in the same test after a full reload: the
    #    page boots the production way (the token it would take from the
    #    URL hash as #access_token=... — the development token, role
    #    "user" — sent as its Bearer token), /api/auth/config answers
    #    publicly and /api/auth/me resolves the viewer; the scorecard
    #    has no admin-only controls, so it answers the viewer 200 with
    #    the full answer while the page still renders. -------------------------
    monkeypatch.setattr(
        work_module, "order_release_counts", _fixed(ORDER_RELEASE_ANSWER)
    )
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

    # -- The rows the proof inserted are deleted at its end. --------------------
    _delete_release_rows(plan_max, transfer_max)
