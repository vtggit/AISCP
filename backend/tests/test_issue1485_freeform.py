"""Issue #1485 proof: the work scorecard.

``GET /api/work/scorecard`` (the new router ``app.api.work``, prefix
``/api/work``, included in ``app.main`` behind the app's include-time
authentication gate like every other API router) answers
``{"processes": [...]}`` — the eight work processes, in this order:
``reorder_points``, ``excess``, ``stockout``, ``exceptions``,
``forecast_overrides``, ``sop_signoff``, ``purchase_budget``,
``ddmrp_adjustment``.  It is open to any signed-in user (401 without
a token) and writes nothing.  Each entry is ``{key, step, decisions,
with_proposal, at_proposal, without_proposal, rate}``, computed by
calling the named source function directly (not over HTTP):
``reorder_points`` ("Accept or dismiss a recommended reorder point")
from ``app.api.planner_decisions.decision_acceptance`` — ``decisions``
and ``with_proposal`` are ``accepted.count + dismissed.count``,
``at_proposal`` is ``accepted.count``, ``without_proposal`` is 0;
``excess`` ("Decide what to do with excess or dead stock") from
``app.api.excess_decisions.excess_proposal_counts`` — ``decisions`` is
``accepted``, ``with_proposal`` is ``total.proposed``, ``at_proposal``
is ``total.chosen``, ``without_proposal`` is ``without_proposal``;
``stockout`` ("Decide how to cover a stockout risk") from
``app.api.stockout_decisions.stockout_proposal_counts``, mapped the
same way as ``excess``; ``exceptions`` ("Acknowledge an exception")
from ``app.api.exceptions.exception_proposal_counts`` — ``decisions``
is ``acknowledged``, ``with_proposal`` is ``proposed``,
``at_proposal`` is null, ``without_proposal`` is ``without_proposal``;
``forecast_overrides`` ("Override a period's forecast") from
``app.api.forecast.forecast_override_proposal_counts`` — ``decisions``
is ``overrides``, ``with_proposal`` is ``proposed``, ``at_proposal``
is ``taken``, ``without_proposal`` is ``without_proposal``;
``sop_signoff`` ("Sign the month off") from
``app.api.sop.signoff_proposals``, given the route's own caller —
``decisions`` is ``signoffs``, ``with_proposal`` is
``proposed + not_proposed``, ``at_proposal`` is ``proposed``,
``without_proposal`` is ``without_proposal``; ``purchase_budget``
("Set each month's purchase budget") from
``app.api.purchase_budgets._record_count`` — ``decisions`` is
``saved``, ``with_proposal`` is ``with_proposal``, ``at_proposal`` is
``taken``, ``without_proposal`` is ``saved - with_proposal``; and
``ddmrp_adjustment`` ("Enter planned adjustment factors for a ramp")
from ``app.api.ddmrp.get_ddmrp_adjustment_record``, mapped the same
way as ``purchase_budget``.  ``rate`` is ``at_proposal /
with_proposal`` rounded to 4 decimals, null when ``with_proposal`` is
0 or ``at_proposal`` is null.

Proved here, on the test database migrated to head, with each of the
eight source functions replaced, by monkeypatch on the name
``app.api.work`` calls, by a stand-in with a fixed answer (drift:
accepted 6 / dismissed 2; excess: accepted 5, total proposed 4 /
chosen 3, without 1; stockout: accepted 3, total proposed 3 / chosen
1, without 0; exceptions: acknowledged 7, proposed 5, without 2;
overrides: overrides 9, proposed 4, taken 1, same-direction 3,
without 5; sign-offs: 3, proposed 2, not-proposed 1, without 0; the
budget record: saved 4, with 3, taken 2, below 1, above 0; the DDMRP
record: saved 5, with 3, taken 1, below 1, above 1):

* the answer for the ``user`` role lists the eight keys in order:
  reorder_points 8/8/6/0 rate 0.75; excess 5/4/3/1 rate 0.75;
  stockout 3/3/1/0 rate 0.3333; exceptions 7/5/null/2 rate null;
  forecast_overrides 9/4/1/5 rate 0.25; sop_signoff 3/3/2/0 rate
  0.6667; purchase_budget 4/3/2/1 rate 0.6667; ddmrp_adjustment
  5/3/1/2 rate 0.3333 — and the route's own caller is the one
  ``signoff_proposals`` is given;
* a failed source — the excess stand-in raising — keeps the entry's
  ``key`` and ``step`` with its five numbers null, the other entries
  answer as above and the route answers 200;
* a source entry that violates the scorecard invariants — the budget
  record's ``saved`` below its ``with_proposal``, the overrides'
  ``taken`` above their ``proposed`` — answers the same single
  failed-entry shape with null counts (the scorecard never emits
  contradictory numbers), and the violation is logged;
* access — the route answers 401 without a token;
* nothing written — the row counts of ``planner_decisions`` and
  ``ddmrp_adjustment_proposals`` are the same before and after; and
* ``backend/openapi.json`` carries the route.

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
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import work as work_module
from app.db.connection import get_connection
from app.main import app

_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

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

EXPECTED = {
    "reorder_points": {
        "decisions": 8,
        "with_proposal": 8,
        "at_proposal": 6,
        "without_proposal": 0,
        "rate": 0.75,
    },
    "excess": {
        "decisions": 5,
        "with_proposal": 4,
        "at_proposal": 3,
        "without_proposal": 1,
        "rate": 0.75,
    },
    "stockout": {
        "decisions": 3,
        "with_proposal": 3,
        "at_proposal": 1,
        "without_proposal": 0,
        "rate": 0.3333,
    },
    "exceptions": {
        "decisions": 7,
        "with_proposal": 5,
        "at_proposal": None,
        "without_proposal": 2,
        "rate": None,
    },
    "forecast_overrides": {
        "decisions": 9,
        "with_proposal": 4,
        "at_proposal": 1,
        "without_proposal": 5,
        "rate": 0.25,
    },
    "sop_signoff": {
        "decisions": 3,
        "with_proposal": 3,
        "at_proposal": 2,
        "without_proposal": 0,
        "rate": 0.6667,
    },
    "purchase_budget": {
        "decisions": 4,
        "with_proposal": 3,
        "at_proposal": 2,
        "without_proposal": 1,
        "rate": 0.6667,
    },
    "ddmrp_adjustment": {
        "decisions": 5,
        "with_proposal": 3,
        "at_proposal": 1,
        "without_proposal": 2,
        "rate": 0.3333,
    },
}

ENTRY_KEYS = {
    "key",
    "step",
    "decisions",
    "with_proposal",
    "at_proposal",
    "without_proposal",
    "rate",
}


def _fixed(answer):
    """A stand-in that answers ``answer`` however it is called."""

    def stand_in(*args, **kwargs):
        return answer

    return stand_in


def _row_counts() -> tuple[int, int]:
    """The row counts of the two tables the proof checks are not written."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM planner_decisions")
            planner_decisions = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM ddmrp_adjustment_proposals")
            ddmrp_adjustment = cur.fetchone()[0]
        return planner_decisions, ddmrp_adjustment
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
        entry["rate"],
    ) == tuple(
        EXPECTED[key][name]
        for name in (
            "decisions",
            "with_proposal",
            "at_proposal",
            "without_proposal",
            "rate",
        )
    ), key


def _assert_failed(entry: dict, key: str) -> None:
    """The single failed-entry shape: key and step, five numbers null."""
    assert set(entry) == ENTRY_KEYS, key
    assert entry["key"] == key, key
    assert entry["step"] == STEPS[key], key
    assert (
        entry["decisions"],
        entry["with_proposal"],
        entry["at_proposal"],
        entry["without_proposal"],
        entry["rate"],
    ) == (None, None, None, None, None), key


def test_issue1485_freeform(client, monkeypatch, caplog):
    signoff_callers = []

    def signoff_stand_in(current_user):
        signoff_callers.append(current_user)
        return SIGNOFFS_ANSWER

    # -- The setup: the eight source functions, each replaced by a
    #    stand-in with its fixed answer. ------------------------------------
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
    monkeypatch.setattr(work_module, "signoff_proposals", signoff_stand_in)
    monkeypatch.setattr(work_module, "_record_count", _fixed(BUDGET_RECORD))
    monkeypatch.setattr(
        work_module, "get_ddmrp_adjustment_record", _fixed(DDMRP_RECORD)
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

    # -- Nothing written: the row counts before. --------------------------------
    counts_before = _row_counts()

    # -- The answer for the user role: the eight keys in order. ----------------
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"processes"}
    assert [entry["key"] for entry in body["processes"]] == KEYS
    for entry in body["processes"]:
        _assert_process(entry, entry["key"])
    # The route's own caller is the one signoff_proposals is given.
    assert len(signoff_callers) == 1
    assert signoff_callers[0].sub == VIEWER_SUB
    assert signoff_callers[0].roles == ["user"]
    # Open to any signed-in user: the admin gets the same answer.
    assert admin.get(URL).json() == body
    assert signoff_callers[-1].sub == ADMIN_SUB
    assert "admin" in signoff_callers[-1].roles

    # -- A failed source: the excess stand-in raising. ---------------------------
    def excess_boom():
        raise RuntimeError("the excess source is down")

    monkeypatch.setattr(work_module, "excess_proposal_counts", excess_boom)
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    processes = {entry["key"]: entry for entry in response.json()["processes"]}
    assert list(processes) == KEYS, "the order and the keys survive the failure"
    _assert_failed(processes["excess"], "excess")
    for key, entry in processes.items():
        if key != "excess":
            _assert_process(entry, key)

    # -- A source entry that violates the scorecard invariants: no
    #    contradictory counts, the failed-entry shape, and a logged
    #    violation. ----------------------------------------------------------------
    caplog.at_level(logging.WARNING, logger="app.api.work")
    # saved below with_proposal: the budget record's own numbers would
    # make without_proposal negative.
    monkeypatch.setattr(
        work_module,
        "_record_count",
        _fixed({"saved": 2, "with_proposal": 3, "taken": 2, "below": 1, "above": 0}),
    )
    caplog.clear()
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    processes = {entry["key"]: entry for entry in response.json()["processes"]}
    _assert_failed(processes["purchase_budget"], "purchase_budget")
    for key, entry in processes.items():
        if key not in ("excess", "purchase_budget"):
            _assert_process(entry, key)
    violations = [
        record
        for record in caplog.records
        if record.name == "app.api.work" and record.levelno >= logging.WARNING
    ]
    assert any("purchase_budget" in record.getMessage() for record in violations)
    # at_proposal above with_proposal: the overrides' taken above their
    # proposed.
    monkeypatch.setattr(
        work_module,
        "forecast_override_proposal_counts",
        _fixed(
            {
                "overrides": 9,
                "proposed": 4,
                "taken": 5,
                "same_direction": 3,
                "without_proposal": 5,
            }
        ),
    )
    caplog.clear()
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    processes = {entry["key"]: entry for entry in response.json()["processes"]}
    _assert_failed(processes["forecast_overrides"], "forecast_overrides")
    for key, entry in processes.items():
        if key not in ("excess", "purchase_budget", "forecast_overrides"):
            _assert_process(entry, key)
    violations = [
        record
        for record in caplog.records
        if record.name == "app.api.work" and record.levelno >= logging.WARNING
    ]
    assert any("forecast_overrides" in record.getMessage() for record in violations)

    # -- Nothing written: the row counts after. ----------------------------------
    assert _row_counts() == counts_before

    # -- The schema file carries the route. ---------------------------------------
    spec = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    scorecard_path = spec["paths"][URL]
    assert set(scorecard_path) == {"get"}
    response_schema = scorecard_path["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema["$ref"] == "#/components/schemas/WorkScorecard"
    assert scorecard_path["get"]["security"] == [{"bearerAuth": []}]
    scorecard_model = spec["components"]["schemas"]["WorkScorecard"]
    assert set(scorecard_model["properties"]) == {"processes"}
    process_model = spec["components"]["schemas"]["ScorecardProcess"]
    assert set(process_model["properties"]) == ENTRY_KEYS
    assert set(process_model["required"]) == {"key", "step"}

    # -- Identity, second pass in the same test after a full reload: the
    #    page boots the production way (the token it would take from the
    #    URL hash as #access_token=... — the development token, role
    #    "user" — sent as its Bearer token), /api/auth/config answers
    #    publicly and /api/auth/me resolves the viewer; the scorecard
    #    has no admin-only controls, so it answers the viewer 200 with
    #    the full answer while the page still renders. -------------------------
    monkeypatch.setattr(work_module, "excess_proposal_counts", _fixed(EXCESS_ANSWER))
    monkeypatch.setattr(work_module, "_record_count", _fixed(BUDGET_RECORD))
    monkeypatch.setattr(
        work_module, "forecast_override_proposal_counts", _fixed(OVERRIDES_ANSWER)
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
