"""Issue #1208 proof: the standing grant run on the triage list.

``GET /api/exceptions`` gains the standing grants as its fifteenth
source, listed "standing_grants" when it fails: the source reads the
grants in force, once per call for each of ``order_plan_release``
and ``transfer_release``, through the module attribute
``_standing_grants_repo`` (a
``ErpStandingGrantPostgresRepository``), and for each process with a
grant in force its latest run row, through
``_standing_grant_runs_repo.latest_for(process)`` (a
``StandingGrantRunPostgresRepository``) — the process's row with the
greatest ``started_at``, the greatest ``id`` breaking a tie, or None.
The current time is read once per call, through the module's
``_utcnow()``.  The ``erp.standing_grants_enabled`` switch from the
stored system settings the list already reads once per call: a
stored false raises no row of the kind, a missing value, or one that
is not a boolean, counts as on.

A latest run under the grant in force that failed, that skipped, or
that released with a ``left_out`` that is not empty raises one run
row; a ``nothing_due`` run, a ``released`` run with an empty
``left_out`` and a latest run under another grant raise no run row.
The grant's time — the later of the grant's ``created_at`` and the
latest run's ``started_at`` under the grant, or ``created_at``
alone while it has none — more than
``STANDING_GRANT_IDLE_HOURS`` (26 — one daily sync plus two hours)
hours before ``_utcnow()`` raises one idle row, whatever the latest
run's outcome.  A row is kind "standing_grant_run", title
"Standing grant run needs attention", view "#/releases",
``item_code`` and ``site_code`` null, severity "high" for a failed
run and for the idle row, else "medium", the key
"standing_grant_run:<run id>" (the idle row's
"standing_grant_run:idle:<grant id>:<the latest run id under the
grant, or 0>") and ``detected_on`` the run's UTC date (the idle
row's the grant's time plus 26 hours) — neither ever the response's
``as_of``.  A failure of either read lists "standing_grants" in
``sources_failed`` with the other sources still answering, status
200, and the route never answers 500.  The kind stands directly
after "order_below_minimum" in ``_KIND_ORDER`` — the last kind.
Every other kind, source and field is unchanged.

Everything is read through the app's test client the way
``test_issue1169_freeform`` reads the triage list, with ``_utcnow``
in ``app.api.exceptions`` replaced by a stand-in answering a fixed
aware UTC time.  The grants are created and revoked through the
#1173 routes (``POST /api/standing-grants`` and
``POST /api/standing-grants/{id}/revoke``) the way
``test_issue1198_freeform`` calls them, reading each grant's
``created_at`` from the route's answer.  The run rows are inserted
into ``standing_grant_runs`` directly, with the ``grant_id``,
``started_at``, ``outcome``, ``reason``, ``documents``, ``value``
and ``left_out`` each case needs.  Every time is relative to the
grant's ``created_at``, never a fixed date, so the proof holds on
any day it runs: a run's ``started_at`` is the grant's
``created_at`` plus whole hours, on a whole minute, and the clock
stands two hours after the newest run unless a case says otherwise.
Every expected ``detected_on`` and detail is built from those
times.
"""

import json
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
from app.db.connection import get_cursor
from app.main import app

URL = "/api/exceptions"
GRANTS_URL = "/api/standing-grants"
ADMIN_TOKEN = "dev-secret-token:admin"
SETTING_KEY = "erp.standing_grants_enabled"
WRITES_OFF_REASON = "Acumatica writes are disabled"
STALE_DATA_REASON = "the synced Acumatica data is older than 26 hours"
TITLE = "Standing grant run needs attention"


class _Clock:
    """The stand-in for ``_utcnow``: answers the one fixed aware
    UTC instant the case sets."""

    def __init__(self) -> None:
        self.now: datetime | None = None

    def __call__(self) -> datetime:
        assert self.now is not None, "the clock was not set for this case"
        return self.now


def _on_the_minute(value: datetime) -> datetime:
    """The instant on a whole minute: a run's ``started_at`` is the
    grant's ``created_at`` plus whole hours, on a whole minute."""
    return value.replace(second=0, microsecond=0)


def _fmt(value: datetime) -> str:
    """The detail's time: UTC as ``YYYY-MM-DD HH:MM UTC``."""
    return f"{value:%Y-%m-%d %H:%M} UTC"


def _create_grant(admin: TestClient, process: str) -> dict:
    """Create one grant through the #1173 route, its
    ``created_at`` parsed from the route's answer as an aware UTC
    datetime."""
    response = admin.post(
        GRANTS_URL,
        json={
            "process": process,
            "max_document_value": 1000,
            "max_daily_value": 10000,
            "max_data_age_hours": 26,
        },
    )
    assert response.status_code == 201, response.text
    row = response.json()
    assert row["in_force"] is True
    assert row["created_at"] is not None
    row["created_at"] = datetime.fromisoformat(row["created_at"])
    assert row["created_at"].tzinfo is not None
    return row


def _revoke(admin: TestClient, grant_id: int) -> None:
    response = admin.post(f"{GRANTS_URL}/{grant_id}/revoke")
    assert response.status_code == 200, response.text
    assert response.json()["in_force"] is False


def _insert_run(
    process: str,
    grant_id: int,
    started_at: datetime,
    outcome: str,
    reason: str | None = None,
    documents: int = 0,
    value: str = "0",
    left_out: list | None = None,
) -> int:
    """Insert one ``standing_grant_runs`` row directly and return
    its id."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO standing_grant_runs "
            "(process, grant_id, started_at, outcome, reason, "
            "documents, value, left_out) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                process,
                grant_id,
                started_at,
                outcome,
                reason,
                documents,
                value,
                json.dumps(left_out if left_out is not None else []),
            ),
        )
        return cur.fetchone()["id"]


def test_issue1208_freeform(client, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(exceptions, "_utcnow", clock)
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    def _get() -> dict:
        response = client.get(URL)
        assert response.status_code == 200, response.text
        return response.json()

    def _rows(body: dict) -> list[dict]:
        return [row for row in body["rows"] if row["kind"] == "standing_grant_run"]

    # -- No grant: run rows stored, no grant in force — no row of
    #    the kind, the source is not failed. ------------------------
    grant_a = _create_grant(admin, "order_plan_release")
    base_a = _on_the_minute(grant_a["created_at"])
    run_a = _insert_run(
        "order_plan_release",
        grant_a["id"],
        base_a + timedelta(hours=1),
        "failed",
        reason=WRITES_OFF_REASON,
        documents=2,
        value="150.00",
    )
    assert run_a is not None
    _revoke(admin, grant_a["id"])
    clock.now = base_a + timedelta(hours=3)  # two hours after the run
    body = _get()
    assert _rows(body) == []
    assert "standing_grants" not in body["sources_failed"]

    # -- Failed: a latest run under the grant that failed — exactly
    #    one high row, every field of it. ---------------------------
    grant_b = _create_grant(admin, "order_plan_release")
    base_b = _on_the_minute(grant_b["created_at"])
    t_failed = base_b + timedelta(hours=1)
    run_b = _insert_run(
        "order_plan_release",
        grant_b["id"],
        t_failed,
        "failed",
        reason=WRITES_OFF_REASON,
        documents=2,
        value="150.00",
    )
    clock.now = t_failed + timedelta(hours=2)
    body = _get()
    rows = _rows(body)
    assert [row["key"] for row in rows] == [f"standing_grant_run:{run_b}"]
    row = rows[0]
    assert row["kind"] == "standing_grant_run"
    assert row["severity"] == "high"
    assert row["item_code"] is None
    assert row["site_code"] is None
    assert row["title"] == TITLE
    assert row["view"] == "#/releases"
    assert row["detected_on"] == t_failed.date().isoformat()
    assert row["detail"] == (
        f"The standing grant for the order plan failed at "
        f"{_fmt(t_failed)}: {WRITES_OFF_REASON}."
    )

    # -- Skipped: a latest run that skipped — one medium row. -------
    t_skipped = base_b + timedelta(hours=3)
    run_c = _insert_run(
        "order_plan_release",
        grant_b["id"],
        t_skipped,
        "skipped",
        reason=STALE_DATA_REASON,
    )
    clock.now = t_skipped + timedelta(hours=2)
    body = _get()
    rows = _rows(body)
    assert [row["key"] for row in rows] == [f"standing_grant_run:{run_c}"]
    row = rows[0]
    assert row["severity"] == "medium"
    assert row["detected_on"] == t_skipped.date().isoformat()
    assert row["detail"] == (
        f"The standing grant for the order plan sent nothing at "
        f"{_fmt(t_skipped)}: {STALE_DATA_REASON}."
    )

    # -- Held back: a latest run released with a non-empty
    #    left_out — one medium row. ---------------------------------
    t_released = base_b + timedelta(hours=4)
    run_d = _insert_run(
        "order_plan_release",
        grant_b["id"],
        t_released,
        "released",
        documents=3,
        value="250.00",
        left_out=[
            {
                "site_code": "159",
                "supplier_code": "S-1208-1",
                "value": "90.00",
                "reason": "document_limit",
            },
            {
                "site_code": "159",
                "supplier_code": "S-1208-2",
                "value": "120.00",
                "reason": "daily_limit",
            },
        ],
    )
    clock.now = t_released + timedelta(hours=2)
    body = _get()
    rows = _rows(body)
    assert [row["key"] for row in rows] == [f"standing_grant_run:{run_d}"]
    row = rows[0]
    assert row["severity"] == "medium"
    assert row["detected_on"] == t_released.date().isoformat()
    assert row["detail"] == (
        f"The standing grant for the order plan sent 3 document(s) "
        f"at {_fmt(t_released)} and held 2 back over its limits."
    )

    # -- Only the latest: a failed run followed by a later released
    #    run with an empty left_out raises nothing. -----------------
    t_e1 = base_b + timedelta(hours=5)
    _insert_run(
        "order_plan_release",
        grant_b["id"],
        t_e1,
        "failed",
        reason=WRITES_OFF_REASON,
    )
    t_e2 = base_b + timedelta(hours=6)
    _insert_run(
        "order_plan_release",
        grant_b["id"],
        t_e2,
        "released",
        documents=1,
        value="40.00",
    )
    clock.now = t_e2 + timedelta(hours=2)
    body = _get()
    assert _rows(body) == []

    # So does a later nothing_due run.
    t_e3 = base_b + timedelta(hours=7)
    _insert_run("order_plan_release", grant_b["id"], t_e3, "nothing_due")
    clock.now = t_e3 + timedelta(hours=2)
    body = _get()
    assert _rows(body) == []

    # A failed run under a revoked grant, with no run yet under the
    # new grant, raises no run row.
    t_e4 = base_b + timedelta(hours=8)
    _insert_run(
        "order_plan_release",
        grant_b["id"],
        t_e4,
        "failed",
        reason=WRITES_OFF_REASON,
    )
    _revoke(admin, grant_b["id"])
    grant_c = _create_grant(admin, "order_plan_release")
    clock.now = t_e4 + timedelta(hours=2)
    body = _get()
    assert _rows(body) == []

    # -- Idle: a grant with no run raises an idle row 27 hours after
    #    its created_at, the key ending in :0, and none at 25
    #    hours. ------------------------------------------------------
    base_c = _on_the_minute(grant_c["created_at"])
    clock.now = grant_c["created_at"] + timedelta(hours=27)
    body = _get()
    rows = _rows(body)
    assert [row["key"] for row in rows] == [
        f"standing_grant_run:idle:{grant_c['id']}:0"
    ]
    row = rows[0]
    assert row["severity"] == "high"
    assert (
        row["detected_on"]
        == (grant_c["created_at"] + timedelta(hours=26)).date().isoformat()
    )
    assert row["detail"] == (
        f"The standing grant for the order plan has not run since "
        f"{_fmt(grant_c['created_at'])}."
    )
    clock.now = grant_c["created_at"] + timedelta(hours=25)
    body = _get()
    assert _rows(body) == []

    # With the clock 27 hours after the grant's only run — released
    # with nothing left out — one high row keyed by the run; none
    # at 25 hours.
    # Later than every run the earlier phases stored under the
    # revoked grants, so the grant's run is the process's latest.
    t_idle = base_c + timedelta(hours=9)
    run_f = _insert_run(
        "order_plan_release",
        grant_c["id"],
        t_idle,
        "released",
        documents=1,
        value="42.00",
    )
    clock.now = t_idle + timedelta(hours=27)
    body = _get()
    rows = _rows(body)
    assert [row["key"] for row in rows] == [
        f"standing_grant_run:idle:{grant_c['id']}:{run_f}"
    ]
    row = rows[0]
    assert row["severity"] == "high"
    assert row["detected_on"] == (t_idle + timedelta(hours=26)).date().isoformat()
    assert row["detail"] == (
        f"The standing grant for the order plan has not run since " f"{_fmt(t_idle)}."
    )
    clock.now = t_idle + timedelta(hours=25)
    body = _get()
    assert _rows(body) == []

    # A failed latest run 27 hours old raises both its run row and
    # the idle row.
    t_both = base_c + timedelta(hours=10)
    run_g = _insert_run(
        "order_plan_release",
        grant_c["id"],
        t_both,
        "failed",
        reason=WRITES_OFF_REASON,
    )
    clock.now = t_both + timedelta(hours=27)
    body = _get()
    rows = _rows(body)
    assert sorted(row["key"] for row in rows) == sorted(
        [
            f"standing_grant_run:{run_g}",
            f"standing_grant_run:idle:{grant_c['id']}:{run_g}",
        ]
    )
    by_key = {row["key"]: row for row in rows}
    run_row = by_key[f"standing_grant_run:{run_g}"]
    assert run_row["severity"] == "high"
    assert run_row["detected_on"] == t_both.date().isoformat()
    assert run_row["detail"] == (
        f"The standing grant for the order plan failed at "
        f"{_fmt(t_both)}: {WRITES_OFF_REASON}."
    )
    idle_row = by_key[f"standing_grant_run:idle:{grant_c['id']}:{run_g}"]
    assert idle_row["severity"] == "high"
    assert idle_row["detected_on"] == (t_both + timedelta(hours=26)).date().isoformat()
    assert idle_row["detail"] == (
        f"The standing grant for the order plan has not run since " f"{_fmt(t_both)}."
    )

    # -- Transfers: the grant in force for transfer_release whose
    #    latest run failed — the detail names transfers. -----------
    _revoke(admin, grant_c["id"])
    grant_d = _create_grant(admin, "transfer_release")
    t_transfers = _on_the_minute(grant_d["created_at"]) + timedelta(hours=1)
    run_h = _insert_run(
        "transfer_release",
        grant_d["id"],
        t_transfers,
        "failed",
        reason=WRITES_OFF_REASON,
        documents=2,
        value="80.00",
    )
    clock.now = t_transfers + timedelta(hours=2)
    body = _get()
    rows = _rows(body)
    assert [row["key"] for row in rows] == [f"standing_grant_run:{run_h}"]
    row = rows[0]
    assert row["severity"] == "high"
    assert row["detail"] == (
        f"The standing grant for transfers failed at "
        f"{_fmt(t_transfers)}: {WRITES_OFF_REASON}."
    )

    # -- The switch: stored false by an admin — no row of the
    #    kind. -------------------------------------------------------
    stored = admin.put(f"/api/settings/{SETTING_KEY}", json={"value": False})
    assert stored.status_code == 200, stored.text
    assert stored.json()["value"] is False
    body = _get()
    assert _rows(body) == []
    assert "standing_grants" not in body["sources_failed"]

    # The stored value removed — the rows return.
    removed = admin.delete(f"/api/settings/{SETTING_KEY}")
    assert removed.status_code == 200, removed.text
    assert removed.json()["value"] is True  # the registry default
    body = _get()
    assert [row["key"] for row in _rows(body)] == [f"standing_grant_run:{run_h}"]

    # The grant revoked — none is raised.
    _revoke(admin, grant_d["id"])
    body = _get()
    assert _rows(body) == []

    # -- A failure: the runs read raises — status 200,
    #    "standing_grants" in sources_failed. -----------------------
    grant_e = _create_grant(admin, "order_plan_release")
    t_broken = _on_the_minute(grant_e["created_at"]) + timedelta(hours=1)
    run_i = _insert_run(
        "order_plan_release",
        grant_e["id"],
        t_broken,
        "failed",
        reason=WRITES_OFF_REASON,
    )
    assert run_i is not None
    clock.now = t_broken + timedelta(hours=2)

    class _BrokenRunsRepo:
        def latest_for(self, process: str) -> dict | None:
            raise RuntimeError("the runs are down")

    monkeypatch.setattr(exceptions, "_standing_grant_runs_repo", _BrokenRunsRepo())
    body = _get()
    assert "standing_grants" in body["sources_failed"]
    assert _rows(body) == []

    # The same holds with the grants read raising.
    class _BrokenGrantsRepo:
        def in_force_for(self, process: str) -> dict | None:
            raise RuntimeError("the grants are down")

    monkeypatch.setattr(exceptions, "_standing_grants_repo", _BrokenGrantsRepo())
    body = _get()
    assert "standing_grants" in body["sources_failed"]
    assert _rows(body) == []

    # -- The order: the kind stands directly after
    #    order_below_minimum — the last kind. ------------------------
    assert (
        exceptions._KIND_ORDER["standing_grant_run"]
        == exceptions._KIND_ORDER["order_below_minimum"] + 1
    )
