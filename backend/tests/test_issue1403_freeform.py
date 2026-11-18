"""Issue #1403 proof: the S&OP review's proposal to sign the month off.

The proof seeds the projection's inputs, pins the dates and inserts
sign-offs the way ``test_issue935_freeform`` does (importing its
``_insert_signoff`` and the issue 928 seed) and reads
``GET /api/sop/review`` as the admin — development-mode token
``dev-secret-token:admin``.

AC-1 — the setting ``sop.signoff_proposal_max_change`` (system,
number) answers its default 0.05, its bounds 0.001 to 1 and its
description through ``get_setting``.

AC-2 — the review answers ``signoff_proposal``: null without an
earlier sign-off; an object once a sign-off of the month before the
review month is stored, its stored figures the current review's
``months`` with every value field divided by 1.02 — ``proposed`` true
with ``max_change`` 0.05, ``largest_change`` 0.02/1.02,
``compared_values`` the number of non-null value fields over the
shared months and ``since_month`` that earlier month.  A newer
sign-off for the same earlier month, the same but with the second
month's ``supply_value`` divided by 1.10, is ``proposed`` false with
``largest_change`` 0.10/1.10 in ``supply_value`` of that month; with
the setting stored at 0.15, ``proposed`` is true again with
``max_change`` 0.15.  After a sign-off of the review month itself,
``signoff_proposal`` is null again, and the sign-off stored the
review answer as it already does, the proposal included.  The route
stays read-only: reading the review writes nothing and the
``sop_signoffs`` row count is unchanged by it.  The ``user`` role
reads the same proposal, and no token is a 401.

Identity — the proof also runs as a NON-ADMIN (role "user"): a second
pass in the same test after a full reload, with the page booted the
production way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent as
its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer — sees the page still
render (the review answers the same proposal) while the admin-only
sign-off is refused.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.aggregate.projection as projection_module
import app.sop.review as review_module
from app.db.connection import get_connection
from app.main import app
from app.settings.registry import get_setting
from tests.test_issue928_freeform import (
    ADMIN_DETAIL,
    ADMIN_TOKEN,
    FIXED_TODAY,
    PLANNER_TOKEN,
    REVIEW,
    REVIEW_MONTH,
    _seed,
    _signoff_count,
)
from tests.test_issue935_freeform import _insert_signoff

KEY = "sop.signoff_proposal_max_change"
LIST = "/api/sop/signoffs"
PREV_MONTH = "2026-09"  # the month before the review month
SECOND_MONTH = "2026-11"  # the second month of the horizon
VALUE_FIELDS = (
    "demand_value",
    "supply_value",
    "planned_receipts_value",
    "total_spend",
    "budget",
)
# Future created_at stamps, each later than the one before: every
# directly inserted row becomes the newest row overall when inserted.
CREATED_AT_1 = "2027-01-01T00:00:00+00:00"
CREATED_AT_2 = "2027-01-01T00:00:01+00:00"


def _stored_figures(
    months: list[dict],
    wobble: float = 1.02,
    broken: tuple[str, str, float] | None = None,
) -> dict:
    """The stored figures of an earlier sign-off: the current review's
    ``months`` with every value field divided by ``wobble`` — or by
    ``broken``'s third value for that month and field when ``broken``
    is given."""
    stored_months = []
    for entry in months:
        stored = dict(entry)
        for field in VALUE_FIELDS:
            if entry[field] is None:
                continue
            divisor = wobble
            if (
                broken is not None
                and entry["month"] == broken[0]
                and field == broken[1]
            ):
                divisor = broken[2]
            stored[field] = entry[field] / divisor
        stored_months.append(stored)
    return {"as_of": "2026-09-30", "months": stored_months}


def test_issue1403_freeform(client, monkeypatch, user_token) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    # The review resolves its own "current server date", the
    # projection resolves its default as_of against it, and the shared
    # default as-of date (recommendations, realised accuracy) is
    # pinned to the same server date.
    monkeypatch.setattr(review_module, "date", _FixedDate)
    monkeypatch.setattr(projection_module, "date", _FixedDate)
    monkeypatch.setattr("app.aggregate.spend.date", _FixedDate)
    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    # --- The setting: default, bounds and description. ----------------
    setting = get_setting(KEY)
    assert setting is not None
    assert setting.scope == "system"
    assert setting.type == "number"
    assert setting.default == 0.05
    assert setting.min == 0.001
    assert setting.max == 1
    assert setting.description == (
        "The largest relative change of any value figure of the S&OP "
        "review since the last sign-off (0.05 = 5%) at which the review "
        "proposes signing the month off."
    )

    # --- Seed ---------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {PLANNER_TOKEN}"})
    anon = TestClient(app)

    # --- No earlier sign-off: the proposal is null. -------------------
    response = admin.get(REVIEW, params={"months": 3})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == FIXED_TODAY.isoformat()
    assert body["review_month"] == REVIEW_MONTH
    assert body["latest_signoff"] is None
    assert body["since_signoff"] is None
    assert body["signoff_proposal"] is None
    months = body["months"]
    assert [m["month"] for m in months] == ["2026-10", "2026-11", "2026-12"]
    assert _signoff_count() == 0

    # --- Almost nothing moved: a sign-off of the month before the
    #     review month, its stored figures the current review's months
    #     with every value field divided by 1.02. ----------------------
    _insert_signoff(
        PREV_MONTH,
        _stored_figures(months),
        "admin",
        "Admin",
        CREATED_AT_1,
    )
    body = admin.get(REVIEW, params={"months": 3}).json()
    assert body["latest_signoff"] is None, "2026-09 is not the review month"
    assert body["since_signoff"]["signed_month"] == PREV_MONTH
    proposal = body["signoff_proposal"]
    assert proposal is not None
    assert proposal["proposed"] is True, "0.02/1.02 is at most 0.05"
    assert proposal["max_change"] == 0.05, "the registry default"
    assert proposal["largest_change"] == pytest.approx(0.02 / 1.02)
    assert proposal["compared_values"] == sum(
        1 for m in months for field in VALUE_FIELDS if m[field] is not None
    )
    assert proposal["since_month"] == PREV_MONTH

    # --- One figure moved: a newer sign-off for the same earlier month,
    #     the same but with the second month's supply_value divided by
    #     1.10. ---------------------------------------------------------
    _insert_signoff(
        PREV_MONTH,
        _stored_figures(months, broken=(SECOND_MONTH, "supply_value", 1.10)),
        "admin",
        "Admin",
        CREATED_AT_2,
    )
    body = admin.get(REVIEW, params={"months": 3}).json()
    proposal = body["signoff_proposal"]
    assert proposal is not None
    assert proposal["proposed"] is False, "0.10/1.10 is above 0.05"
    assert proposal["max_change"] == 0.05
    assert proposal["largest_change"] == pytest.approx(0.10 / 1.10)
    assert proposal["largest_field"] == "supply_value"
    assert proposal["largest_month"] == SECOND_MONTH
    assert proposal["compared_values"] == sum(
        1 for m in months for field in VALUE_FIELDS if m[field] is not None
    )
    assert proposal["since_month"] == PREV_MONTH
    # The user role reads the same proposal; no token is a 401.
    viewer_body = viewer.get(REVIEW, params={"months": 3}).json()
    assert viewer_body["signoff_proposal"] == proposal
    assert anon.get(REVIEW, params={"months": 3}).status_code == 401

    # With the setting stored at 0.15, proposed is true again.
    response = admin.put(f"/api/settings/{KEY}", json={"value": 0.15})
    assert response.status_code == 200, response.text
    assert response.json()["source"] == "system"
    proposal = admin.get(REVIEW, params={"months": 3}).json()["signoff_proposal"]
    assert proposal["proposed"] is True, "0.10/1.10 is at most 0.15"
    assert proposal["max_change"] == 0.15
    assert proposal["largest_change"] == pytest.approx(0.10 / 1.10)
    assert proposal["largest_field"] == "supply_value"
    assert proposal["largest_month"] == SECOND_MONTH
    assert proposal["since_month"] == PREV_MONTH

    # --- The route stays read-only: reading the review writes nothing.
    signoffs_before = _signoff_count()
    assert admin.get(REVIEW, params={"months": 3}).status_code == 200
    assert viewer.get(REVIEW, params={"months": 3}).status_code == 200
    assert _signoff_count() == signoffs_before, "the review writes no row"

    # --- Signed off: a sign-off of the review month itself. -----------
    # It stores the review answer as it already does, the proposal
    # included.
    response = admin.post(
        LIST, json={"month": REVIEW_MONTH, "note": "October figures agreed"}
    )
    assert response.status_code == 201, response.text
    stored = response.json()
    assert stored["figures"]["signoff_proposal"] == proposal
    assert _signoff_count() == signoffs_before + 1
    body = admin.get(REVIEW, params={"months": 3}).json()
    assert body["latest_signoff"]["id"] == stored["id"]
    assert body["since_signoff"] is not None
    assert body["signoff_proposal"] is None, "the review month is signed off"

    # --- Identity: a second pass after a full reload, as the
    #     NON-ADMIN (role "user").  The page is booted the production
    #     way: the token it would take from the URL hash as
    #     #access_token=... is the development token (role "user") sent
    #     as its Bearer token; GET /api/auth/config answers publicly
    #     and GET /api/auth/me resolves the viewer.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_reloaded = TestClient(app)
    config = anon_reloaded.get("/api/auth/config")
    assert config.status_code == 200
    assert config.json()["authEnabled"] is True
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["authenticated"] is True
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]
    # The page still renders for the viewer — the review answers the
    # same proposal as the admin's — while the admin-only sign-off is
    # absent (refused) and no token is still a 401.
    response = viewer.get(REVIEW, params={"months": 3})
    assert response.status_code == 200, response.text
    assert (
        response.json()["signoff_proposal"]
        == admin.get(REVIEW, params={"months": 3}).json()["signoff_proposal"]
    )
    refused = viewer.post(LIST, json={"month": REVIEW_MONTH, "note": "nope"})
    assert refused.status_code == 403, refused.text
    assert refused.json()["detail"] == ADMIN_DETAIL
    assert anon_reloaded.get(REVIEW, params={"months": 3}).status_code == 401
    assert _signoff_count() == signoffs_before + 1, "nothing new was written"
