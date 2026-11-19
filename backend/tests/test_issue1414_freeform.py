"""Issue #1414 proof: the budget proposals of the projected spend.

AC-1 — the system setting ``sop.budget_proposal_margin`` of the
registry: number, default 0.05, bounds 0 to 1, the documented
description.

AC-2 — ``app.api.purchase_budgets.propose_budget(total_spend, margin)``
is ``total_spend * (1 + margin)`` rounded up to three significant
figures and 0 for a total of 0; a quotient float arithmetic leaves a
hair above a whole step never adds a step (the quotient is rounded to
9 decimals before the round-up).  Proven with the proof's examples:
(123456.78, 0.05) gives 130000, (100000, 0) gives 100000, (999.5, 0)
gives 1000, (1000, 0.05) gives 1050 and (0, 0.05) gives 0.

AC-3 — ``GET /api/purchase-budgets/proposals``: open to any signed-in
user like ``GET /api/purchase-budgets`` (401 without a valid token,
writing nothing), ``months`` from 1 to 12 defaulting to 6 (a value
outside the range answers 422), the spend read once through the
``spend_against_budget`` the module imports from
``app.aggregate.spend`` (replaced here by a stand-in answering two
months: 2026-12 committed 80000, planned 43456.78, total 123456.78,
budget 125000; 2027-01 all zeros and no budget, with
``item_sites_without_unit_cost`` 2) and the stored system settings
read once through ``app.sop.review._stored_system_settings``, the
margin from them through ``app.ddmrp.zones.setting_value``.  With no
margin stored the answer is margin 0.05 and
``item_sites_without_unit_cost`` 2, the 2026-12 entry
``proposed_budget`` 130000 and ``difference`` −5000 and the 2027-01
entry ``proposed_budget`` 0 and ``difference`` null; the stand-in is
called once with ``months=6`` and ``?months=2`` calls it with
``months=2``.  A stored margin of 0.2 — put through
``PUT /api/settings/sop.budget_proposal_margin`` as an admin and
removed afterwards — makes the 2026-12 proposal 149000.  ``?months=0``
and ``?months=13`` answer 422.  Without the stand-in, on the seeded
test database, the route answers 200 with 6 months and each
``proposed_budget`` is at least its ``total_spend``.  The rows of
``purchase_budgets`` and ``settings`` are the same before and after
the reads — the route writes nothing.  ``backend/openapi.json``
carries the path and its schemas.

The identity requirement: the proof also runs as a NON-ADMIN (role
``user``, asserted via ``GET /api/auth/me``) in a second pass after a
full reload (a fresh ``TestClient`` booted the production way — the
token the page would take from the URL hash as
``#access_token=...``, with ``GET /api/auth/config`` answering
publicly and ``GET /api/auth/me`` resolving the viewer): the
admin-only controls are refused with 403 (nothing stored or deleted)
while the page still renders — the budgets and the proposals it loads
answer 200 — and a request without a token still gets 401.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import purchase_budgets as purchase_budgets_module
from app.api.purchase_budgets import propose_budget
from app.db.connection import get_connection
from app.main import app
from app.settings.registry import get_setting

URL = "/api/purchase-budgets/proposals"
LIST_URL = "/api/purchase-budgets"
KEY = "sop.budget_proposal_margin"
SETTING_URL = f"/api/settings/{KEY}"
ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_DETAIL = "Role 'admin' is required to perform this action."


class _SpendStandIn:
    """A stand-in for ``app.aggregate.spend.spend_against_budget``.

    Answers the same shape — ``{"months": [...],
    "item_sites_without_unit_cost": n}`` — with the two months of the
    proof, and records every call so the route's one read of the
    spend, and the ``months`` it passes, can be asserted.
    """

    MONTHS = (
        {
            "month": "2026-12",
            "committed_spend": 80000,
            "planned_spend": 43456.78,
            "total_spend": 123456.78,
            "budget": 125000,
            "remaining": 1543.22,
            "over_budget": False,
        },
        {
            "month": "2027-01",
            "committed_spend": 0,
            "planned_spend": 0,
            "total_spend": 0,
            "budget": None,
            "remaining": None,
            "over_budget": False,
        },
    )

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "months": [dict(row) for row in self.MONTHS],
            "item_sites_without_unit_cost": 2,
        }


def _rows(table: str) -> list[tuple]:
    """The table's rows in stable order, as the database holds them."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if table == "purchase_budgets":
                cur.execute(
                    "SELECT month, amount, updated_at, updated_by "
                    "FROM purchase_budgets ORDER BY month"
                )
            else:
                cur.execute(
                    "SELECT scope, owner, key, value, updated_by, updated_at "
                    "FROM settings ORDER BY scope, owner, key"
                )
            return [tuple(row) for row in cur.fetchall()]
    finally:
        conn.close()


def test_issue1414_freeform(client, monkeypatch):
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # -- AC-1: the registered system setting. ---------------------------
    setting = get_setting(KEY)
    assert setting is not None, "the setting must be registered"
    assert setting.scope == "system"
    assert setting.type == "number"
    assert setting.default == 0.05
    assert setting.min == 0
    assert setting.max == 1
    assert setting.description == (
        "The share added to a month's projected purchase spend, "
        "committed plus planned, to propose that month's budget "
        "(0.05 = 5%)."
    )

    # -- AC-2: the arithmetic, the proof's examples. --------------------
    assert propose_budget(123456.78, 0.05) == 130000
    assert propose_budget(100000, 0) == 100000
    assert propose_budget(999.5, 0) == 1000
    assert propose_budget(1000, 0.05) == 1050
    assert propose_budget(0, 0.05) == 0
    # A hair above a whole step never adds a step: the quotient is
    # rounded to 9 decimals before the round-up.
    assert propose_budget(1000.0000000000002 / 1.05, 0.05) == 1000
    assert propose_budget(0.30000000000000004, 0) == 0.3

    # Nothing is written: the reads see the tables as they stand.
    budgets_before = _rows("purchase_budgets")
    settings_before = _rows("settings")

    # -- AC-3: the route with a stand-in spend. -------------------------
    standin = _SpendStandIn()
    monkeypatch.setattr(purchase_budgets_module, "spend_against_budget", standin)

    # No margin stored: the registry default 0.05 applies.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"margin", "item_sites_without_unit_cost", "months"}
    assert body["margin"] == 0.05
    assert body["item_sites_without_unit_cost"] == 2
    assert [month["month"] for month in body["months"]] == [
        "2026-12",
        "2027-01",
    ]
    december = body["months"][0]
    assert set(december) == {
        "month",
        "committed_spend",
        "planned_spend",
        "total_spend",
        "proposed_budget",
        "budget",
        "difference",
    }
    assert december["committed_spend"] == 80000
    assert december["planned_spend"] == 43456.78
    assert december["total_spend"] == 123456.78
    assert december["budget"] == 125000
    assert december["proposed_budget"] == 130000
    assert december["difference"] == -5000
    january = body["months"][1]
    assert january["committed_spend"] == 0
    assert january["planned_spend"] == 0
    assert january["total_spend"] == 0
    assert january["budget"] is None
    assert january["proposed_budget"] == 0
    assert january["difference"] is None
    # The stand-in was called exactly once, with the default months=6.
    assert standin.calls == [{"months": 6}]
    # The reads write nothing so far.
    assert _rows("purchase_budgets") == budgets_before
    assert _rows("settings") == settings_before

    # ?months=2 reaches the spend with months=2.
    response = client.get(f"{URL}?months=2")
    assert response.status_code == 200, response.text
    assert standin.calls == [{"months": 6}, {"months": 2}]

    # A stored margin of 0.2, as an admin, removed afterwards.
    response = admin.put(SETTING_URL, json={"value": 0.2})
    assert response.status_code == 200, response.text
    assert response.json()["value"] == 0.2
    assert response.json()["source"] == "system"
    # One row stored: the system row for the key, changed by the admin.
    settings_after_put = _rows("settings")
    assert len(settings_after_put) == len(settings_before) + 1
    (row,) = [
        stored
        for stored in settings_after_put
        if stored[0] == "system" and stored[1] == "" and stored[2] == KEY
    ]
    assert row[3] == 0.2
    assert row[4] == ADMIN_SUB
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["margin"] == 0.2
    assert body["months"][0]["proposed_budget"] == 149000
    assert body["months"][0]["difference"] == -24000
    assert body["months"][1]["proposed_budget"] == 0
    response = admin.delete(SETTING_URL)
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "default"
    # Removed: the default applies again and the settings row is gone.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json()["margin"] == 0.05
    assert response.json()["months"][0]["proposed_budget"] == 130000
    assert _rows("purchase_budgets") == budgets_before
    assert _rows("settings") == settings_before

    # -- AC-3: errors and access. ----------------------------------------
    calls_before_errors = list(standin.calls)
    for query in ("months=0", "months=13"):
        response = client.get(f"{URL}?{query}")
        assert response.status_code == 422, (query, response.text)
    # Validation precedes the read: the stand-in was not called again.
    assert standin.calls == calls_before_errors
    # Open to any signed-in user: 401 without a token, 200 for the
    # user role (the default client is a non-admin).
    assert TestClient(app).get(URL).status_code == 401
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json()["margin"] == 0.05
    assert _rows("purchase_budgets") == budgets_before
    assert _rows("settings") == settings_before

    # -- AC-3: without the stand-in, on the seeded test database. --------
    monkeypatch.undo()
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["months"]) == 6
    for month in body["months"]:
        assert month["proposed_budget"] >= month["total_spend"], month
    # The reads write nothing, with the real spend either.
    assert _rows("purchase_budgets") == budgets_before
    assert _rows("settings") == settings_before

    # -- The OpenAPI document carries the path and its schemas. ----------
    spec = json.loads(
        (Path(__file__).resolve().parents[1] / "openapi.json").read_text(
            encoding="utf-8"
        )
    )
    operation = spec["paths"]["/api/purchase-budgets/proposals"]["get"]
    months_param = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "months"
    )
    assert months_param["schema"]["minimum"] == 1
    assert months_param["schema"]["maximum"] == 12
    assert months_param["schema"]["default"] == 6
    assert (
        operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/BudgetProposals"
    )
    month_schema = spec["components"]["schemas"]["ProposalMonth"]
    assert set(month_schema["properties"]) == {
        "month",
        "committed_spend",
        "planned_spend",
        "total_spend",
        "proposed_budget",
        "budget",
        "difference",
    }
    # The {month} mutations are untouched: no collision.
    assert set(spec["paths"]["/api/purchase-budgets/{month}"]) == {
        "put",
        "delete",
    }

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only controls are
    #    refused 403 while the page still renders for the viewer. -------
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    assert TestClient(app).get("/api/auth/config").status_code == 200
    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin stub's identity is the other one: sub and username.
    admin_me = admin.get("/api/auth/me")
    assert admin_me.status_code == 200, admin_me.text
    assert admin_me.json()["user"]["sub"] == ADMIN_SUB
    assert admin_me.json()["user"]["username"] == "admin"
    # The admin-only controls are absent for the viewer: 403, nothing
    # stored or deleted.
    response = viewer_reloaded.put(f"{LIST_URL}/2026-12", json={"amount": 1})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer_reloaded.delete(f"{LIST_URL}/2026-12")
    assert response.status_code == 403, response.text
    response = viewer_reloaded.put(SETTING_URL, json={"value": 0.1})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    assert _rows("purchase_budgets") == budgets_before
    assert _rows("settings") == settings_before
    # The page still renders: the budgets and the proposals it loads
    # answer 200 for the viewer.
    assert viewer_reloaded.get(LIST_URL).status_code == 200
    response = viewer_reloaded.get(URL)
    assert response.status_code == 200, response.text
    assert set(response.json()) == {
        "margin",
        "item_sites_without_unit_cost",
        "months",
    }
    # And the gate is intact: a request without a token still gets 401.
    assert TestClient(app).get(URL).status_code == 401
    assert TestClient(app).get(LIST_URL).status_code == 401
