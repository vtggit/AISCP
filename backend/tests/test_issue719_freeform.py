"""Issue #719 proof: POST /api/parameter-writeback/confirm.

The confirm writes the outstanding accepted decisions back to their
ERPs: the body carries 1 to 200 item-site pairs, no duplicate pair,
admin only (the same ``require_role("admin")`` the planner decisions
use), and every pair is checked against the SAME preview rows
``GET /api/parameter-writeback/preview`` builds — shared code, not
copied.  ``item_sites`` and ``planner_decisions`` are seeded directly
in the test database the way the issue 715 proof does (fixed
decided_at values pin the "latest decision per item-site"
selection), and the two client classes in the route module are
replaced with fakes that record their calls and answer as each case
needs.

Proved here:

  * an Acumatica row and an Odoo row both write — each fake is
    called once with the preview's proposed values; two audit rows
    are stored with outcome "written" (the outcome, the ERP's before
    and after values, the requested values, the decision id, and the
    token subject and display name, as the planner decisions record
    them); both ``item_sites`` rows now hold the after values
    (``order_qty`` derived as max minus reorder point, null when not
    positive); ``GET /api/drift/decisions/summary`` counts them as
    applied; the preview no longer lists them;
  * repeat — a second confirm of the same pairs answers "skipped" /
    "not outstanding", with no new client call and no new audit row;
  * a failing writer — a fake raising ``AcumaticaError`` answers
    "failed" with the error stored (the exception's class name and
    message), ``item_sites`` untouched, and the next pair still
    processed;
  * not writable — a pair with no ``item_sites`` row is "skipped"
    with "no item_sites row" (and an unsupported source system with
    "unsupported source system"), storing no audit row and making no
    ERP call;
  * the per-item before/after payload — every written pair carries
    the before and after values (reorder_point, max_qty, order_qty,
    order_qty null when the derived value is not positive) mirrored
    on the preview's numeric shape, serialized as JSON numbers, not
    Decimal strings;
  * sanitized errors — a failed write answers the stable public
    reason "writer_error", and the history route answers
    "writer_error" for it too: neither the public confirm result nor
    the history row exposes the exception's class name, message, or
    provider internals, while the audit table itself keeps them;
  * refusals — a planner (non-admin) token answers 403 with zero
    client calls, no repository read and no audit row (before any
    repository read, ERP write, or audit row creation); no token
    answers 401; a duplicate pair, a NUL byte, an empty list and
    more than 200 items answer 422 — never a 500;
  * the schema file — ``backend/openapi.json`` lists the path;
  * the identity requirement — the proof also runs as a NON-ADMIN
    (role "user"): a second pass after a full reload (a fresh
    TestClient booted the production way — the token the page would
    take from the URL hash as ``#access_token=...``, with
    ``GET /api/auth/config`` answering publicly and
    ``GET /api/auth/me`` resolving the viewer) sees the admin-only
    control refused 403 (nothing stored, no ERP call) while the
    page still renders, and no token is still a 401.
"""

import json
import os
from datetime import date
from pathlib import Path

import psycopg2.extras
from fastapi.testclient import TestClient

import app.api.parameter_writeback as parameter_writeback
from app.db.connection import get_connection
from app.feed.acumatica_client import AcumaticaError
from app.main import app

CONFIRM_URL = "/api/parameter-writeback/confirm"
PREVIEW_URL = "/api/parameter-writeback/preview"
HISTORY_URL = "/api/parameter-writeback/history"
SUMMARY_URL = "/api/drift/decisions/summary"

_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

SITE = "SITE-719"
AS_OF = date(2026, 10, 1)

ADMIN_TOKEN = "dev-secret-token:admin"
PLANNER_TOKEN = "dev-secret-token:user"
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

# The failing writer's error: it deliberately carries provider
# internals (a class name and an api-key fragment) that the public
# routes must never expose.
FAIL_ITEM = "ITEM-719-C"
FAIL_MESSAGE = (
    "boom: the Acumatica ItemWarehouse read for ITEM-719-C at "
    "SITE-719 failed (api-key=SECRET-719)"
)

# item_code -> (item_sites id, reorder_point, max_qty, order_qty,
# source_system); ITEM-719-E has no item_sites row at all.
ITEM_SITES = {
    "ITEM-719-A": ("is-719-a", 97.0, 150.0, 25.0, "acumatica"),
    "ITEM-719-B": ("is-719-b", 52.0, 40.0, None, "odoo"),
    FAIL_ITEM: ("is-719-c", 90.0, 140.0, 30.0, "acumatica"),
    "ITEM-719-D": ("is-719-d", 80.0, 120.0, 20.0, "aidw-feed"),
    "ITEM-719-G": ("is-719-g", 20.0, 90.0, 10.0, "acumatica"),
}

# decision id -> (item_code, decision, recommended units, money,
# decided_at)
DECISIONS = {
    "pd-719-a": ("ITEM-719-A", "accepted", 100, 5000.0, "2026-10-01T09:00:00+00"),
    "pd-719-b": ("ITEM-719-B", "accepted", 50, 2000.0, "2026-10-01T10:00:00+00"),
    "pd-719-c": (FAIL_ITEM, "accepted", 95, 3000.0, "2026-10-01T11:00:00+00"),
    "pd-719-d": ("ITEM-719-D", "accepted", 100, 4000.0, "2026-10-01T12:00:00+00"),
    "pd-719-e": ("ITEM-719-E", "accepted", 60, None, "2026-10-01T13:00:00+00"),
    "pd-719-g": ("ITEM-719-G", "accepted", 22, 1000.0, "2026-10-01T14:00:00+00"),
}


class FakeAcumaticaClient:
    """The fake for the route module's AcumaticaClient.

    Records every call; FAIL_ITEM raises ``AcumaticaError`` (the
    failing writer); every other call answers "written" with the
    ERP's before values (the seeded item_sites values) and the
    requested values as the read-back.
    """

    calls: list[dict] = []

    def write_item_warehouse_parameters(
        self, *, item_code: str, site_code: str, reorder_point: float, max_qty: float
    ) -> dict:
        FakeAcumaticaClient.calls.append(
            {
                "item_code": item_code,
                "site_code": site_code,
                "reorder_point": reorder_point,
                "max_qty": max_qty,
            }
        )
        if item_code == FAIL_ITEM:
            raise AcumaticaError(FAIL_MESSAGE)
        reorder, max_value, _ = ITEM_SITES[item_code][1:4]
        return {
            "outcome": "written",
            "before": {"reorder_point": reorder, "max_qty": max_value},
            "after": {"reorder_point": reorder_point, "max_qty": max_qty},
        }


class FakeOdooClient:
    """The fake for the route module's OdooClient: records every call
    and answers "written" the way the Acumatica fake does."""

    calls: list[dict] = []

    def write_reorder_rule(
        self, *, item_code: str, site_code: str, reorder_point: float, max_qty: float
    ) -> dict:
        FakeOdooClient.calls.append(
            {
                "item_code": item_code,
                "site_code": site_code,
                "reorder_point": reorder_point,
                "max_qty": max_qty,
            }
        )
        reorder, max_value, _ = ITEM_SITES[item_code][1:4]
        return {
            "outcome": "written",
            "before": {"reorder_point": reorder, "max_qty": max_value},
            "after": {"reorder_point": reorder_point, "max_qty": max_qty},
        }


def _seed() -> None:
    """Seed item_sites and planner_decisions directly in the database."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item_code, (
                site_id,
                reorder,
                max_qty,
                order_qty,
                source,
            ) in ITEM_SITES.items():
                cur.execute(
                    "INSERT INTO item_sites "
                    "(id, item_code, site_code, unit_cost, reorder_point, "
                    " max_qty, order_qty, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        site_id,
                        item_code,
                        SITE,
                        1.0,
                        reorder,
                        max_qty,
                        order_qty,
                        source,
                    ),
                )
            for decision_id, (
                item_code,
                decision,
                units,
                money,
                decided_at,
            ) in DECISIONS.items():
                cur.execute(
                    "INSERT INTO planner_decisions "
                    "(id, item_code, site_code, decision, reason, snooze_until, "
                    " as_of, recommended_reorder_point_units, current_reorder_point, "
                    " money_at_stake, decided_by, decided_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        decision_id,
                        item_code,
                        SITE,
                        decision,
                        None,
                        None,
                        AS_OF,
                        units,
                        80.0,
                        money,
                        "seeder",
                        decided_at,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _item_site(item_code: str) -> tuple:
    """The item-site's (reorder_point, max_qty, order_qty)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reorder_point, max_qty, order_qty FROM item_sites "
                "WHERE item_code = %s AND site_code = %s",
                (item_code, SITE),
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None
    return row


def _audit_rows() -> list[dict]:
    """The raw parameter_writebacks rows, oldest first."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM parameter_writebacks ORDER BY written_at, id")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _audit_for(item_code: str) -> list[dict]:
    return [row for row in _audit_rows() if row["item_code"] == item_code]


def test_issue719_freeform(monkeypatch, client):
    # The two client classes in the route module are replaced with
    # fakes that record their calls and answer as each case needs.
    FakeAcumaticaClient.calls = []
    FakeOdooClient.calls = []
    monkeypatch.setattr(parameter_writeback, "AcumaticaClient", FakeAcumaticaClient)
    monkeypatch.setattr(parameter_writeback, "OdooClient", FakeOdooClient)

    # Track every preview-repository read: a non-admin 403 must never
    # reach it (AC-5: 403 before any repository read, ERP write, or
    # audit row creation).
    repo_reads: list = []
    real_outstanding = parameter_writeback._repo.outstanding_accepted

    def _tracking_outstanding():
        repo_reads.append(1)
        return real_outstanding()

    monkeypatch.setattr(
        parameter_writeback._repo, "outstanding_accepted", _tracking_outstanding
    )

    _seed()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    planner = TestClient(app, headers={"Authorization": f"Bearer {PLANNER_TOKEN}"})
    anon = TestClient(app)

    # --- The starting picture (the preview the confirm shares). ------
    response = admin.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    preview = response.json()
    assert [row["item_code"] for row in preview["rows"]] == [
        "ITEM-719-A",  # 5000
        "ITEM-719-D",  # 4000
        FAIL_ITEM,  # 3000
        "ITEM-719-B",  # 2000
        "ITEM-719-G",  # 1000
        "ITEM-719-E",  # null money, last
    ]
    assert preview["count"] == 6
    assert preview["writable_count"] == 4  # A, B, C, G
    by_item = {row["item_code"]: row for row in preview["rows"]}
    assert by_item["ITEM-719-A"]["proposed_reorder_point"] == 100.0
    assert by_item["ITEM-719-A"]["proposed_max_qty"] == 125.0  # 100.0 + 25.0
    assert by_item["ITEM-719-B"]["proposed_reorder_point"] == 50.0
    assert by_item["ITEM-719-B"]["proposed_max_qty"] == 50.0  # 40.0 < 50.0
    assert by_item[FAIL_ITEM]["proposed_reorder_point"] == 95.0
    assert by_item[FAIL_ITEM]["proposed_max_qty"] == 125.0  # 95.0 + 30.0
    assert by_item["ITEM-719-G"]["proposed_reorder_point"] == 22.0
    assert by_item["ITEM-719-G"]["proposed_max_qty"] == 32.0  # 22.0 + 10.0
    assert by_item["ITEM-719-D"]["writable"] is False
    assert by_item["ITEM-719-D"]["blocked_reason"] == "unsupported source system"
    assert by_item["ITEM-719-E"]["writable"] is False
    assert by_item["ITEM-719-E"]["blocked_reason"] == "no item_sites row"

    summary = admin.get(SUMMARY_URL)
    assert summary.status_code == 200, summary.text
    buckets = summary.json()
    assert buckets["accepted"]["count"] == 6
    assert buckets["applied"]["count"] == 0
    assert buckets["outstanding"]["count"] == 6
    assert _audit_rows() == []

    # --- An Acumatica row and an Odoo row both write. -----------------
    response = admin.post(
        CONFIRM_URL,
        json={
            "items": [
                {"item_code": "ITEM-719-A", "site_code": SITE},
                {"item_code": "ITEM-719-B", "site_code": SITE},
            ]
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload.keys()) == {
        "results",
        "written",
        "unchanged",
        "failed",
        "skipped",
    }
    assert payload["written"] == 2
    assert payload["unchanged"] == 0
    assert payload["failed"] == 0
    assert payload["skipped"] == 0
    results = payload["results"]
    assert [result["item_code"] for result in results] == [
        "ITEM-719-A",
        "ITEM-719-B",
    ]
    for result in results:
        assert set(result.keys()) == {
            "item_code",
            "site_code",
            "outcome",
            "reason",
            "before",
            "after",
        }
        assert result["site_code"] == SITE
        assert result["outcome"] == "written"
        assert result["reason"] is None
        for values in (result["before"], result["after"]):
            assert set(values.keys()) == {"reorder_point", "max_qty", "order_qty"}
            for value in values.values():
                # JSON numbers, not Decimal strings.
                assert value is None or isinstance(value, (int, float))
                assert not isinstance(value, bool)

    # The per-item before/after payload: the preview's current values
    # as the before, the after values (with the derived order_qty) as
    # the after.
    result_a, result_b = results
    assert result_a["before"] == {
        "reorder_point": 97.0,
        "max_qty": 150.0,
        "order_qty": 25.0,
    }
    assert result_a["after"] == {
        "reorder_point": 100.0,
        "max_qty": 125.0,
        "order_qty": 25.0,
    }
    assert result_b["before"] == {
        "reorder_point": 52.0,
        "max_qty": 40.0,
        "order_qty": None,
    }
    # 50.0 - 50.0 is not positive -> order_qty null.
    assert result_b["after"] == {
        "reorder_point": 50.0,
        "max_qty": 50.0,
        "order_qty": None,
    }
    # Raw JSON: the numeric fields are numbers, not strings.
    raw = json.loads(response.text)
    for result in raw["results"]:
        for values in (result["before"], result["after"]):
            for key, value in values.items():
                assert value is None or isinstance(value, (int, float)), (
                    key,
                    value,
                )

    # Each fake was called once, with the preview's proposed values.
    assert FakeAcumaticaClient.calls == [
        {
            "item_code": "ITEM-719-A",
            "site_code": SITE,
            "reorder_point": 100.0,
            "max_qty": 125.0,
        }
    ]
    assert FakeOdooClient.calls == [
        {
            "item_code": "ITEM-719-B",
            "site_code": SITE,
            "reorder_point": 50.0,
            "max_qty": 50.0,
        }
    ]

    # Two audit rows with outcome "written": the outcome, the before
    # and after values, the requested values, the decision id, and the
    # token subject and display name, as the planner decisions record
    # them.
    audit = _audit_rows()
    assert len(audit) == 2
    written_a = next(row for row in audit if row["item_code"] == "ITEM-719-A")
    written_b = next(row for row in audit if row["item_code"] == "ITEM-719-B")
    for row in (written_a, written_b):
        assert row["outcome"] == "written"
        assert row["error"] is None
        assert row["source_system"] in {"acumatica", "odoo"}
        assert row["written_by"] == "dev-admin-1"
        assert row["written_by_name"] == "admin"
    assert written_a["site_code"] == SITE
    assert written_a["decision_id"] == "pd-719-a"
    assert written_a["source_system"] == "acumatica"
    assert written_a["before_reorder_point"] == 97.0
    assert written_a["before_max_qty"] == 150.0
    assert written_a["after_reorder_point"] == 100.0
    assert written_a["after_max_qty"] == 125.0
    assert written_a["requested_reorder_point"] == 100.0
    assert written_a["requested_max_qty"] == 125.0
    assert written_b["decision_id"] == "pd-719-b"
    assert written_b["source_system"] == "odoo"
    assert written_b["before_reorder_point"] == 52.0
    assert written_b["before_max_qty"] == 40.0
    assert written_b["after_reorder_point"] == 50.0
    assert written_b["after_max_qty"] == 50.0
    assert written_b["requested_reorder_point"] == 50.0
    assert written_b["requested_max_qty"] == 50.0

    # Both item_sites rows now hold the after values (order_qty
    # derived, null when not positive).
    assert _item_site("ITEM-719-A") == (100.0, 125.0, 25.0)
    assert _item_site("ITEM-719-B") == (50.0, 50.0, None)

    # The summary counts them as applied; the preview no longer lists
    # them.
    buckets = admin.get(SUMMARY_URL).json()
    assert buckets["applied"]["count"] == 2
    assert buckets["applied"]["money_at_stake"] == 7000.0  # 5000.0 + 2000.0
    assert buckets["outstanding"]["count"] == 4
    preview = admin.get(PREVIEW_URL).json()
    assert [row["item_code"] for row in preview["rows"]] == [
        "ITEM-719-D",  # 4000
        FAIL_ITEM,  # 3000
        "ITEM-719-G",  # 1000
        "ITEM-719-E",  # null money, last
    ]
    assert preview["count"] == 4
    assert preview["writable_count"] == 2

    # --- Repeat: the same pairs answer "not outstanding". ------------
    ac_calls, oo_calls = len(FakeAcumaticaClient.calls), len(FakeOdooClient.calls)
    response = admin.post(
        CONFIRM_URL,
        json={
            "items": [
                {"item_code": "ITEM-719-A", "site_code": SITE},
                {"item_code": "ITEM-719-B", "site_code": SITE},
            ]
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert (
        payload["written"],
        payload["unchanged"],
        payload["failed"],
        payload["skipped"],
    ) == (0, 0, 0, 2)
    for result in payload["results"]:
        assert result["outcome"] == "skipped"
        assert result["reason"] == "not outstanding"
        assert result["before"] is None
        assert result["after"] is None
    # No new client call, no new audit row.
    assert len(FakeAcumaticaClient.calls) == ac_calls
    assert len(FakeOdooClient.calls) == oo_calls
    assert len(_audit_rows()) == 2

    # --- A failing writer: failed, stored, next pair processed. ------
    response = admin.post(
        CONFIRM_URL,
        json={
            "items": [
                {"item_code": FAIL_ITEM, "site_code": SITE},
                {"item_code": "ITEM-719-G", "site_code": SITE},
            ]
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert (
        payload["written"],
        payload["unchanged"],
        payload["failed"],
        payload["skipped"],
    ) == (1, 0, 1, 0)
    failed_result, written_result = payload["results"]
    assert failed_result["item_code"] == FAIL_ITEM
    assert failed_result["outcome"] == "failed"
    # The stable public reason: no exception class name, message, or
    # provider internals in the public result.
    assert failed_result["reason"] == "writer_error"
    assert "AcumaticaError" not in response.text
    assert "SECRET-719" not in response.text
    assert "boom" not in response.text
    assert failed_result["before"] is None
    assert failed_result["after"] is None
    # The next pair was still processed.
    assert written_result["item_code"] == "ITEM-719-G"
    assert written_result["outcome"] == "written"
    assert written_result["reason"] is None
    assert written_result["before"] == {
        "reorder_point": 20.0,
        "max_qty": 90.0,
        "order_qty": 10.0,
    }
    assert written_result["after"] == {
        "reorder_point": 22.0,
        "max_qty": 32.0,
        "order_qty": 10.0,
    }
    # The fake recorded the failing call, then answered the next pair.
    assert [call["item_code"] for call in FakeAcumaticaClient.calls] == [
        "ITEM-719-A",
        FAIL_ITEM,
        "ITEM-719-G",
    ]
    assert FakeOdooClient.calls and len(FakeOdooClient.calls) == 1

    # The failed audit row: outcome "failed" with the exception's
    # class name and message stored internally.
    failed_rows = _audit_for(FAIL_ITEM)
    assert len(failed_rows) == 1
    failed_row = failed_rows[0]
    assert failed_row["outcome"] == "failed"
    assert failed_row["error"] == f"AcumaticaError: {FAIL_MESSAGE}"
    assert "SECRET-719" in failed_row["error"]
    assert failed_row["decision_id"] == "pd-719-c"
    assert failed_row["written_by"] == "dev-admin-1"
    assert failed_row["written_by_name"] == "admin"
    assert failed_row["requested_reorder_point"] == 95.0
    assert failed_row["requested_max_qty"] == 125.0
    assert failed_row["before_reorder_point"] is None
    assert failed_row["after_max_qty"] is None
    # item_sites is untouched for the failed pair; the next pair's
    # row holds the after values.
    assert _item_site(FAIL_ITEM) == (90.0, 140.0, 30.0)
    assert _item_site("ITEM-719-G") == (22.0, 32.0, 10.0)
    buckets = admin.get(SUMMARY_URL).json()
    assert buckets["applied"]["count"] == 3
    assert buckets["outstanding"]["count"] == 3

    # --- Sanitized errors in the public history route. ---------------
    response = planner.get(HISTORY_URL)
    assert response.status_code == 200, response.text
    history = response.json()
    assert len(history) == 4
    history_failed = next(row for row in history if row["item_code"] == FAIL_ITEM)
    # The stored detail is never exposed: the stable public reason
    # instead.
    assert history_failed["error"] == "writer_error"
    for row in history:
        assert row["error"] in (None, "writer_error")
        if row["error"] is not None:
            assert "AcumaticaError" not in row["error"]
            assert "SECRET-719" not in row["error"]
            assert "boom" not in row["error"]

    # --- Not writable: skipped with the blocked_reason. --------------
    ac_calls, oo_calls = len(FakeAcumaticaClient.calls), len(FakeOdooClient.calls)
    response = admin.post(
        CONFIRM_URL,
        json={
            "items": [
                {"item_code": "ITEM-719-E", "site_code": SITE},
                {"item_code": "ITEM-719-D", "site_code": SITE},
            ]
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert (
        payload["written"],
        payload["unchanged"],
        payload["failed"],
        payload["skipped"],
    ) == (0, 0, 0, 2)
    result_e, result_d = payload["results"]
    assert result_e["outcome"] == "skipped"
    assert result_e["reason"] == "no item_sites row"
    assert result_e["before"] is None
    assert result_e["after"] is None
    assert result_d["outcome"] == "skipped"
    assert result_d["reason"] == "unsupported source system"
    # Skipped pairs store no audit row and make no ERP call.
    assert len(_audit_rows()) == 4
    assert len(FakeAcumaticaClient.calls) == ac_calls
    assert len(FakeOdooClient.calls) == oo_calls

    # --- Refusals. ----------------------------------------------------
    # A planner (non-admin) token answers 403 with zero client calls,
    # no repository read and no audit row.
    repo_reads.clear()
    ac_calls, oo_calls = len(FakeAcumaticaClient.calls), len(FakeOdooClient.calls)
    response = planner.post(
        CONFIRM_URL,
        json={"items": [{"item_code": FAIL_ITEM, "site_code": SITE}]},
    )
    assert response.status_code == 403, response.text
    assert response.json() == {
        "detail": "Role 'admin' is required to perform this action."
    }
    assert repo_reads == []  # no repository read before the 403
    assert len(FakeAcumaticaClient.calls) == ac_calls
    assert len(FakeOdooClient.calls) == oo_calls
    assert len(_audit_rows()) == 4

    # No token answers 401.
    assert (
        anon.post(
            CONFIRM_URL,
            json={"items": [{"item_code": "ITEM-719-A", "site_code": SITE}]},
        ).status_code
        == 401
    )

    # A duplicate pair answers 422; so do a NUL byte, an empty list
    # and more than 200 items — never a 500, and nothing is stored.
    response = admin.post(
        CONFIRM_URL,
        json={
            "items": [
                {"item_code": "ITEM-719-G", "site_code": SITE},
                {"item_code": "ITEM-719-G", "site_code": SITE},
            ]
        },
    )
    assert response.status_code == 422, response.text
    for bad_body in (
        {"items": [{"item_code": "ITEM-719-G\x00", "site_code": SITE}]},
        {"items": [{"item_code": "ITEM-719-G", "site_code": SITE + "\x00"}]},
        {"items": []},
        {
            "items": [
                {"item_code": f"ITEM-719-PAD-{index:03d}", "site_code": SITE}
                for index in range(201)
            ]
        },
        {"items": [{"item_code": "ITEM-719-G" * 64, "site_code": SITE}]},
    ):
        response = admin.post(CONFIRM_URL, json=bad_body)
        assert response.status_code == 422, (bad_body, response.text)
    # The 200-item boundary is accepted.
    response = admin.post(
        CONFIRM_URL,
        json={
            "items": [
                {"item_code": f"ITEM-719-PAD-{index:03d}", "site_code": SITE}
                for index in range(200)
            ]
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["skipped"] == 200
    assert len(payload["results"]) == 200
    assert all(
        result["outcome"] == "skipped" and result["reason"] == "not outstanding"
        for result in payload["results"]
    )
    assert len(_audit_rows()) == 4
    assert len(FakeAcumaticaClient.calls) == ac_calls
    assert len(FakeOdooClient.calls) == oo_calls

    # --- The schema file: the path is listed. -------------------------
    schema = json.loads(_OPENAPI.read_text())
    confirm_path = schema["paths"]["/api/parameter-writeback/confirm"]
    assert "post" in confirm_path
    response_schema = confirm_path["post"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert response_schema["$ref"].endswith("ParameterWritebackConfirmResponse")
    for model in (
        "ParameterWritebackConfirmBody",
        "ParameterWritebackConfirmItem",
        "ParameterWritebackConfirmResult",
        "ParameterWritebackConfirmValues",
        "ParameterWritebackConfirmResponse",
    ):
        assert model in schema["components"]["schemas"]

    # --- Identity: the proof also runs as a NON-ADMIN (role "user"). --
    # Second pass after a full reload: the page boots the production
    # way (the token it would take from the URL hash as
    # #access_token=...), /api/auth/config answers publicly and
    # /api/auth/me resolves the viewer; the admin-only control is
    # refused 403 (nothing stored, no ERP call) while the page still
    # renders, and no token is still a 401.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]

    response = viewer.post(
        CONFIRM_URL,
        json={"items": [{"item_code": FAIL_ITEM, "site_code": SITE}]},
    )
    assert response.status_code == 403, response.text
    # Nothing stored, no ERP call.
    assert len(_audit_rows()) == 4
    assert len(FakeAcumaticaClient.calls) == ac_calls
    assert len(FakeOdooClient.calls) == oo_calls

    # The page still renders for the viewer: the preview the page
    # loads answers 200 with the same rows it renders — and the gate
    # is intact: unauthenticated /api traffic is still a 401.
    page = viewer.get(PREVIEW_URL)
    assert page.status_code == 200, page.text
    assert page.json()["count"] == 3
    assert page.json()["writable_count"] == 1
    assert anon_reloaded.get(PREVIEW_URL).status_code == 401
    assert (
        anon_reloaded.post(
            CONFIRM_URL,
            json={"items": [{"item_code": FAIL_ITEM, "site_code": SITE}]},
        ).status_code
        == 401
    )
    assert len(_audit_rows()) == 4
