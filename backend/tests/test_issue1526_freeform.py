"""Issue #1526 proof: the parameter writeback record — the measure of
the pushes AISCP made back to the ERPs and of what the ERPs kept.

AC-1 — the measure: ``app.recommend.push_record`` holds
``push_record()``, which writes nothing and calls no ERP.  An
item-site is one (item_code, site_code) pair, and for each item-site
only one ``parameter_writebacks`` row counts: the latest of its rows
whose outcome is "written" or "unchanged" and whose
``after_reorder_point`` and ``after_max_qty`` are both not null — the
latest the greatest ``written_at``, and among equal ``written_at``
the greatest ``id``.  A "failed" row never counts, and never hides an
earlier push.  The push is a reorder-point push when that row's
``decision_id`` is not null and a buffer push when it is null.  The
push is read back when the item-site's ``item_sites`` row exists and
its ``synced_at`` is later than the push's ``written_at``; a push
that is read back is unchanged when the ``item_sites`` row's
``reorder_point`` and ``max_qty`` are both not null and each differs
by no more than 0.000001 from the push's ``after_reorder_point`` and
``after_max_qty``, every other push that is read back being changed.
The answer is ``{reorder_points, buffers}``, each ``{pushed, read,
unchanged, rate}``: ``pushed`` the counted item-sites whose push is
of that kind, ``read`` how many of those are read back, ``unchanged``
how many of the read ones are unchanged, and ``rate``
``unchanged / read`` rounded to 4 places, null when ``read`` is 0.
The module's docstring states its limits: AISCP keeps no history of
the ERP's values between syncs, so a value changed and changed back
between two syncs counts as unchanged, and an item-site that no sync
lists any more is never read back.

AC-2 — the route: ``GET /api/parameter-writeback/record`` takes no
parameters, answers ``push_record()`` through response models
declared like the module's others, is open to any signed-in user
through the same ``require_any_authenticated_user`` dependency as
``GET /api/parameter-writeback/history``, and writes nothing.  The
preview, confirm and history routes are unchanged.  The contract:
``backend/openapi.json`` carries the route the way the API contract
check expects.

AC-3 — this proof, against the test database migrated to head.  The
proof first deletes every ``parameter_writebacks`` row and the
``item_sites`` rows of its own pairs.  Its item-sites are the pairs
P1 to P9, each with site S1; every NOT NULL column is filled and
every time is given explicitly: T1 2026-11-01T10:00:00Z, T2
2026-11-02T10:00:00Z, T3 2026-11-03T10:00:00Z and T4
2026-11-04T10:00:00Z.  Empty: with no ``parameter_writebacks`` row,
the route answers both kinds with 0, 0, 0 and a null rate.  The
pushes (written_at, outcome, with or without a decision_id, then the
after reorder point and max): P1 at T1 written with a decision 10
and 20, then at T3 written with a decision 12 and 24; P2 at T2
written with a decision 5 and 15; P3 at T2 unchanged with a decision
8 and 16; P4 at T1 written with a decision 3 and 9, then at T3
failed with a decision, both after values null; P5 at T2 written
without a decision 20 and 50; P6 at T2 written without a decision 30
and 60; P7 at T2 written without a decision 40 and 90; P8 at T2
failed with a decision, both after values null; P9 at T1 written
without a decision 7 and 14, then at T2 written with a decision 9
and 18.  The item-sites (synced_at, then the reorder point and max):
P1 at T4 12 and 24; P2 at T4 6 and 15; P3 at T1 8 and 16; P4 at T4
3.0000001 and 9; P5 at T4 20 and a null max; P6 without a row; P7 at
T4 40 and 90; P8 at T4 1 and 2; P9 at T4 9 and 18.  The answer:
``reorder_points`` pushed 5 (P1, P2, P3, P4 and P9), read 4,
unchanged 3 (P1, P4 and P9), rate 0.75; ``buffers`` pushed 3 (P5,
P6 and P7), read 2, unchanged 1 (P7), rate 0.5.  Access: the route
answers 401 without a token and 200 for the "user" role.  Nothing
written: the row counts of ``parameter_writebacks`` and
``item_sites`` are the same before and after the route's calls.
Clean-up: every row the proof inserted is deleted in a finally.

Identity — the proof also runs as a NON-ADMIN (role "user"): a
second pass in the same test after a full reload, with the page
booted the production way (the token it would take from the URL hash
as ``#access_token=...`` — the development token, role "user" — sent
as its Bearer token), ``GET /api/auth/config`` answering publicly
and ``GET /api/auth/me`` resolving the viewer, sees the admin-only
control absent (the record route answers 200 with the same body, no
403) while the page still renders, and no token is still a 401.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

URL = "/api/parameter-writeback/record"
PREVIEW_URL = "/api/parameter-writeback/preview"
CONFIRM_URL = "/api/parameter-writeback/confirm"
HISTORY_URL = "/api/parameter-writeback/history"
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

SITE = "S1"
WRITTEN_BY = "dev-user-1"

# Every time is given explicitly.
T1 = datetime(2026, 11, 1, 10, 0, 0, tzinfo=UTC)
T2 = datetime(2026, 11, 2, 10, 0, 0, tzinfo=UTC)
T3 = datetime(2026, 11, 3, 10, 0, 0, tzinfo=UTC)
T4 = datetime(2026, 11, 4, 10, 0, 0, tzinfo=UTC)

# The proof's item-sites: the pairs P1 to P9, each with site S1.
PAIRS = [(f"P{i}", SITE) for i in range(1, 10)]

# The pushes: (item_code, written_at, outcome, decision_id,
# after_reorder_point, after_max_qty, requested_reorder_point,
# requested_max_qty).  The two failed rows carry both after values
# null and the values their writeback requested.
PUSHS = [
    ("P1", T1, "written", "D-1526-P1", 10, 20, 10, 20),
    ("P1", T3, "written", "D-1526-P1", 12, 24, 12, 24),
    ("P2", T2, "written", "D-1526-P2", 5, 15, 5, 15),
    ("P3", T2, "unchanged", "D-1526-P3", 8, 16, 8, 16),
    ("P4", T1, "written", "D-1526-P4", 3, 9, 3, 9),
    ("P4", T3, "failed", "D-1526-P4", None, None, 12, 24),
    ("P5", T2, "written", None, 20, 50, 20, 50),
    ("P6", T2, "written", None, 30, 60, 30, 60),
    ("P7", T2, "written", None, 40, 90, 40, 90),
    ("P8", T2, "failed", "D-1526-P8", None, None, 1, 2),
    ("P9", T1, "written", None, 7, 14, 7, 14),
    ("P9", T2, "written", "D-1526-P9", 9, 18, 9, 18),
]

# The item-sites: (item_code, synced_at, reorder_point, max_qty).
# P6 has no item_sites row — no sync lists it any more.
ITEM_SITES = [
    ("P1", T4, 12, 24),
    ("P2", T4, 6, 15),
    ("P3", T1, 8, 16),
    ("P4", T4, 3.0000001, 9),
    ("P5", T4, 20, None),
    ("P7", T4, 40, 90),
    ("P8", T4, 1, 2),
    ("P9", T4, 9, 18),
]

# The empty answer: nothing pushed, nothing read, a null rate.
EMPTY_RECORD = {
    "reorder_points": {"pushed": 0, "read": 0, "unchanged": 0, "rate": None},
    "buffers": {"pushed": 0, "read": 0, "unchanged": 0, "rate": None},
}

# The answer of the seeded data: reorder_points — P1 (T3 decision
# push 12/24, read back at T4, kept exactly), P2 (T2 decision push
# 5/15, read back at T4 as 6/15 — changed), P3 (T2 decision push,
# its item-site synced at T1 — before the push, never read back), P4
# (the T3 failed row neither counts nor hides the T1 push 3/9, read
# back at T4 as 3.0000001/9 — within 0.000001, unchanged) and P9 (the
# latest counted row is the T2 decision push 9/18, read back at T4,
# unchanged); buffers — P5 (read back, a null max — changed), P6
# (no item_sites row — never read back) and P7 (read back, kept
# exactly — unchanged).
EXPECTED_RECORD = {
    "reorder_points": {"pushed": 5, "read": 4, "unchanged": 3, "rate": 0.75},
    "buffers": {"pushed": 3, "read": 2, "unchanged": 1, "rate": 0.5},
}


def _execute(sql: str, params: tuple = ()) -> list:
    """Run one statement and return its rows (no parameters for a DML)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description is not None else []
        conn.commit()
        return [tuple(row) for row in rows]
    finally:
        conn.close()


def _clear_setup() -> None:
    """The proof's setup: delete every parameter_writebacks row and
    the item_sites rows of its own pairs, so the proof starts from
    its own picture of the audit and the item-sites."""
    _execute("DELETE FROM parameter_writebacks")
    _execute(
        "DELETE FROM item_sites WHERE (item_code, site_code) IN "
        "(SELECT * FROM UNNEST(%s::text[], %s::text[]))",
        (
            [item_code for item_code, _ in PAIRS],
            [site_code for _, site_code in PAIRS],
        ),
    )


def _insert_push(
    item_code: str,
    written_at: datetime,
    outcome: str,
    decision_id: str | None,
    after_reorder_point: float | None,
    after_max_qty: float | None,
    requested_reorder_point: float,
    requested_max_qty: float,
) -> str:
    """Store one audit row with every NOT NULL column filled and its
    written_at given explicitly — basis "decision" when the row's
    decision_id is not None, "ddmrp" otherwise; returns its id for
    the clean-up."""
    row_id = str(uuid4())
    basis = "decision" if decision_id is not None else "ddmrp"
    _execute(
        "INSERT INTO parameter_writebacks "
        "(id, item_code, site_code, source_system, decision_id, basis, "
        "before_reorder_point, before_max_qty, after_reorder_point, "
        "after_max_qty, requested_reorder_point, requested_max_qty, "
        "outcome, error, written_by, written_by_name, written_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s, %s)",
        (
            row_id,
            item_code,
            SITE,
            "acumatica",
            decision_id,
            basis,
            None,
            None,
            after_reorder_point,
            after_max_qty,
            requested_reorder_point,
            requested_max_qty,
            outcome,
            None,
            WRITTEN_BY,
            None,
            written_at,
        ),
    )
    return row_id


def _insert_item_site(
    item_code: str, synced_at: datetime, reorder_point, max_qty
) -> str:
    """Store one item_sites row with every NOT NULL column filled and
    its synced_at given explicitly; returns its id for the clean-up."""
    row_id = str(uuid4())
    _execute(
        "INSERT INTO item_sites "
        "(id, item_code, site_code, unit_cost, reorder_point, "
        "safety_stock, max_qty, order_qty, master_lead_time_days, "
        "source_system, synced_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            row_id,
            item_code,
            SITE,
            None,
            reorder_point,
            None,
            max_qty,
            None,
            None,
            "acumatica",
            synced_at,
        ),
    )
    return row_id


def _delete_rows(push_ids: list, site_ids: list) -> None:
    """The clean-up: delete every row the proof inserted, by id."""
    if push_ids:
        _execute(
            "DELETE FROM parameter_writebacks WHERE id = ANY(%s)",
            (push_ids,),
        )
    if site_ids:
        _execute("DELETE FROM item_sites WHERE id = ANY(%s)", (site_ids,))


def _row_counts() -> dict:
    """The row counts of the two tables the route must not write."""
    rows = _execute(
        "SELECT 'parameter_writebacks', COUNT(*) FROM parameter_writebacks "
        "UNION ALL SELECT 'item_sites', COUNT(*) FROM item_sites"
    )
    return {name: count for name, count in rows}


def _assert_openapi_contract() -> None:
    """The contract: backend/openapi.json carries the route the way
    the API contract check expects — the committed entry equals the
    live schema's, the 200 answer is the record model, and both
    models are present with their fields."""
    committed = json.loads(_OPENAPI.read_text())
    live = app.openapi()
    committed_path = committed["paths"]["/api/parameter-writeback/record"]
    assert committed_path == live["paths"]["/api/parameter-writeback/record"]
    operation = committed_path["get"]
    # The route takes no parameters of its own: no query parameter,
    # only the app-wide authorization header the gate adds to every
    # operation.
    assert all(
        parameter["in"] == "header" and parameter["name"] == "authorization"
        for parameter in operation["parameters"]
    )
    schema = operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert schema["$ref"] == "#/components/schemas/ParameterWritebackPushRecord"
    record = committed["components"]["schemas"]["ParameterWritebackPushRecord"]
    assert set(record["properties"]) == {"reorder_points", "buffers"}
    for name in ("reorder_points", "buffers"):
        assert record["properties"][name]["$ref"] == (
            "#/components/schemas/ParameterWritebackPushRecordKind"
        )
    kind = committed["components"]["schemas"]["ParameterWritebackPushRecordKind"]
    assert set(kind["properties"]) == {"pushed", "read", "unchanged", "rate"}
    for name in ("pushed", "read", "unchanged"):
        assert kind["properties"][name] == {"type": "integer", "title": name.title()}
    assert kind["properties"]["rate"] == {
        "anyOf": [{"type": "number"}, {"type": "null"}],
        "title": "Rate",
    }


def test_issue1526_freeform(client) -> None:
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    # --- Setup: the proof's own pairs start clean. ----------------
    _clear_setup()

    # --- Empty: with no parameter_writebacks row the route answers
    #     both kinds with 0, 0, 0 and a null rate. ------------------
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY_RECORD

    inserted_push_ids: list = []
    inserted_site_ids: list = []
    try:
        # --- The pushes and the item-sites, every time explicit. ---
        for push in PUSHS:
            inserted_push_ids.append(_insert_push(*push))
        for site in ITEM_SITES:
            inserted_site_ids.append(_insert_item_site(*site))

        before = _row_counts()

        # --- The answer. -------------------------------------------
        response = viewer.get(URL)
        assert response.status_code == 200, response.text
        assert response.json() == EXPECTED_RECORD

        # --- Access: 401 without a token, 200 for the "user" role. -
        assert anon.get(URL).status_code == 401
        assert viewer.get(URL).status_code == 200

        # --- The contract: openapi.json carries the route. ---------
        _assert_openapi_contract()

        # --- Unchanged: the preview, confirm and history routes. ---
        preview = viewer.get(PREVIEW_URL)
        assert preview.status_code == 200, preview.text
        assert preview.json() == {"rows": [], "count": 0, "writable_count": 0}
        history = viewer.get(HISTORY_URL)
        assert history.status_code == 200, history.text
        history_rows = history.json()
        # The history still lists the audit rows — the proof's 12
        # pushes, newest first (written_at DESC, id DESC).
        assert len(history_rows) == len(PUSHS)
        assert sorted(
            (row["item_code"], row["site_code"], row["outcome"]) for row in history_rows
        ) == sorted((item_code, SITE, outcome) for item_code, _, outcome, *_ in PUSHS)
        assert all(
            history_rows[i]["written_at"] >= history_rows[i + 1]["written_at"]
            for i in range(len(history_rows) - 1)
        )
        confirm = viewer.post(
            CONFIRM_URL,
            json={"items": [{"item_code": "P1", "site_code": SITE}]},
        )
        assert confirm.status_code == 403, confirm.text

        # --- Identity: a second pass after a full reload, as the
        #     NON-ADMIN (role "user").  The page is booted the
        #     production way: the token it would take from the URL
        #     hash as #access_token=... is the development token
        #     (role "user") sent as its Bearer token; GET
        #     /api/auth/config answers publicly and GET /api/auth/me
        #     resolves the viewer.
        viewer_reloaded = TestClient(
            app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
        )
        anon_reloaded = TestClient(app)
        assert anon_reloaded.get("/api/auth/config").status_code == 200
        me = viewer_reloaded.get("/api/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["authenticated"] is True
        assert "user" in me.json()["user"]["roles"]
        assert "admin" not in me.json()["user"]["roles"]

        # The record answers the viewer with the same body — no
        # admin-only control stands on the route — and no token is
        # still a 401.
        reloaded = viewer_reloaded.get(URL)
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json() == EXPECTED_RECORD
        assert anon_reloaded.get(URL).status_code == 401

        # --- Nothing written: the row counts are the same before
        #     and after the route's calls. --------------------------
        assert _row_counts() == before
    finally:
        _delete_rows(inserted_push_ids, inserted_site_ids)
