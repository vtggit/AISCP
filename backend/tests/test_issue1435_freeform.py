"""Issue #1435 proof: the stored sign-off proposals of the signed months.

The proof seeds the review the way ``test_issue1403_freeform`` seeds
it (importing the issue 928 seed and the direct sign-off insert of the
issue 935 proof, beside issue 1403's stored-figures helper, with the
same fixed dates) and signs in as admin and planner the way
``test_issue881_freeform`` does — development-mode tokens
``dev-secret-token:admin`` and ``dev-secret-token:user``.

AC-1 — the repository read ``latest_proposals_per_month`` answers,
for the latest sign-off of each month (``created_at`` newest first,
the ``id`` breaking ties), the month and the stored
``figures -> 'signoff_proposal'`` — null when the key is absent or
stored as null — one query that reads no other part of the figures,
which the seeded rows exercise through the route.

AC-2 — ``GET /api/sop/signoffs/proposals`` (declared before the
dynamic ``GET /api/sop/signoffs/{id}`` so it is reachable) answers
``{signoffs, proposed, not_proposed, without_proposal, months}`` to
any signed-in user and writes nothing.  Seeded rows, inserted straight
into ``sop_signoffs`` and deleted afterwards, each with a small
``figures`` object — 2026-08 without a ``signoff_proposal`` key,
2026-09 twice (an earlier ``{"proposed": false}``, a later
``{"proposed": true}``), 2026-10 ``{"proposed": false}`` and 2026-11
a null ``signoff_proposal`` — answer ``signoffs`` 4, ``proposed`` 1,
``not_proposed`` 1 and ``without_proposal`` 2; ``months`` is, newest
month first, 2026-11 null, 2026-10 false, 2026-09 true and 2026-08
null — the earlier 2026-09 sign-off is not the month's latest — and a
stored ``signoff_proposal`` object that is present but missing the
``proposed`` key counts as ``without_proposal`` with the month still
in ``signoffs``.  The route is listed in ``backend/openapi.json`` the
way the API contract check expects, and ``GET /api/sop/signoffs/{id}``
still answers as before, 404 for a missing id.

AC-3 — on the review seeded the way ``test_issue1403_freeform`` seeds
it, a sign-off made through ``POST /api/sop/signoffs`` after an
earlier sign-off stores ``figures.signoff_proposal`` and the route
counts its month by that proposal's ``proposed``; the route answers
401 without a token and 200 for the ``user`` role, and
``sop_signoffs`` carries the same rows before and after a call.

Identity — the proof also runs as a NON-ADMIN (role "user"): a second
pass in the same test after a full reload, with the page booted the
production way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent as
its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer — sees the page still
render (the route answers the same body) while the admin-only
sign-off control is absent (refused).
"""

from datetime import date

from fastapi.testclient import TestClient

import app.aggregate.projection as projection_module
import app.sop.review as review_module
from app.db.connection import get_connection, get_cursor
from app.main import app
from tests.test_issue928_freeform import (
    ADMIN_DETAIL,
    ADMIN_TOKEN,
    FIXED_TODAY,
    PLANNER_TOKEN,
    REVIEW,
    REVIEW_MONTH,
    _seed,
)
from tests.test_issue935_freeform import _insert_signoff
from tests.test_issue1403_freeform import _stored_figures

LIST = "/api/sop/signoffs"
PROPOSALS = "/api/sop/signoffs/proposals"
EARLIER_MONTH = "2026-09"  # the month before the review month

# The created_at stamps of the directly inserted rows.  The 2026-09
# pair is ordered by its stamps (the later one wins the month), the
# 2026-09 pair must outrank the earlier sign-off of the real
# sign-off pass, and the 2026-10 row must outrank the real sign-off
# row's server ``now()`` — a far future stamp, the way the other
# proofs mark a row newer than every real ``now()``.
EARLIER_SIGNOFF_CREATED_AT = "2026-09-01T00:00:00+00:00"
SEED_2026_08_CREATED_AT = "2026-08-31T00:00:00+00:00"
SEED_2026_09_EARLIER_CREATED_AT = "2026-09-15T00:00:00+00:00"
SEED_2026_09_LATER_CREATED_AT = "2026-09-30T00:00:00+00:00"
SEED_2026_10_CREATED_AT = "2030-01-01T00:00:00+00:00"
SEED_2026_11_CREATED_AT = "2026-11-15T00:00:00+00:00"
EDGE_2026_07_CREATED_AT = "2026-07-31T00:00:00+00:00"


def _signoff_rows() -> list[dict]:
    """Every stored row, with its figures — the table must answer the
    same rows before and after a read."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM sop_signoffs ORDER BY id")
        return [dict(row) for row in cur.fetchall()]


def _delete_signoffs(ids: list[int]) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM sop_signoffs WHERE id = ANY(%s)", (ids,))
        conn.commit()
    finally:
        conn.close()


def test_issue1435_freeform(client, monkeypatch, user_token) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    # The review resolves its own "current server date", the
    # projection resolves its default as_of against it, and the shared
    # default as-of date (recommendations, realised accuracy) is
    # pinned to the same server date — the way
    # test_issue1403_freeform pins it.
    monkeypatch.setattr(review_module, "date", _FixedDate)
    monkeypatch.setattr(projection_module, "date", _FixedDate)
    monkeypatch.setattr("app.aggregate.spend.date", _FixedDate)
    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    # --- Seed the review the way test_issue1403_freeform seeds it. --
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

    seeded_ids: list[int] = []
    try:
        # No sign-off at all: nothing is signed off yet.
        assert admin.get(PROPOSALS).json() == {
            "signoffs": 0,
            "proposed": 0,
            "not_proposed": 0,
            "without_proposal": 0,
            "months": [],
        }

        # --- A real sign-off: an earlier sign-off, then a sign-off
        #     of the review month made through the route. ------------
        # The earlier sign-off stores the current review's months with
        # every value field divided by 1.02, the way
        # test_issue1403_freeform stores it: the proposal is true.
        _insert_signoff(
            EARLIER_MONTH,
            _stored_figures(admin.get(REVIEW).json()["months"]),
            "admin",
            "Admin",
            EARLIER_SIGNOFF_CREATED_AT,
        )
        proposal = admin.get(REVIEW).json()["signoff_proposal"]
        assert proposal is not None
        assert proposal["proposed"] is True, "0.02/1.02 is at most 0.05"

        response = admin.post(
            LIST, json={"month": REVIEW_MONTH, "note": "October figures agreed"}
        )
        assert response.status_code == 201, response.text
        stored = response.json()
        assert stored["month"] == REVIEW_MONTH
        assert stored["figures"]["signoff_proposal"] == proposal
        assert stored["figures"]["signoff_proposal"]["proposed"] is True

        # The route counts the month by that stored proposal's
        # proposed; the earlier sign-off's stored figures carry no
        # signoff_proposal key, so its month is without a proposal.
        body = admin.get(PROPOSALS)
        assert body.status_code == 200, body.text
        assert body.json() == {
            "signoffs": 2,
            "proposed": 1,
            "not_proposed": 0,
            "without_proposal": 1,
            "months": [
                {"month": REVIEW_MONTH, "proposed": True},
                {"month": EARLIER_MONTH, "proposed": None},
            ],
        }

        # --- Seeded rows, inserted straight into sop_signoffs, each
        #     with a small figures object, deleted afterwards. -------
        seeded_ids.append(
            _insert_signoff(
                "2026-08",
                {"as_of": "2026-08-31"},
                "admin",
                "Admin",
                SEED_2026_08_CREATED_AT,
            )
        )
        seeded_ids.append(
            _insert_signoff(
                "2026-09",
                {"signoff_proposal": {"proposed": False}},
                "admin",
                "Admin",
                SEED_2026_09_EARLIER_CREATED_AT,
            )
        )
        seeded_ids.append(
            _insert_signoff(
                "2026-09",
                {"signoff_proposal": {"proposed": True}},
                "admin",
                "Admin",
                SEED_2026_09_LATER_CREATED_AT,
            )
        )
        seeded_ids.append(
            _insert_signoff(
                "2026-10",
                {"signoff_proposal": {"proposed": False}},
                "admin",
                "Admin",
                SEED_2026_10_CREATED_AT,
            )
        )
        seeded_ids.append(
            _insert_signoff(
                "2026-11",
                {"signoff_proposal": None},
                "admin",
                "Admin",
                SEED_2026_11_CREATED_AT,
            )
        )
        body = admin.get(PROPOSALS).json()
        assert body["signoffs"] == 4, "the four signed months"
        assert body["proposed"] == 1
        assert body["not_proposed"] == 1
        assert body["without_proposal"] == 2
        assert body["months"] == [
            {"month": "2026-11", "proposed": None},
            {"month": "2026-10", "proposed": False},
            {"month": "2026-09", "proposed": True},
            {"month": "2026-08", "proposed": None},
        ]

        # A stored signoff_proposal that is present but missing the
        # proposed key counts as without_proposal; the month remains
        # in signoffs.
        edge_id = _insert_signoff(
            "2026-07",
            {"signoff_proposal": {"largest_change": 0.1, "since_month": "2026-06"}},
            "admin",
            "Admin",
            EDGE_2026_07_CREATED_AT,
        )
        try:
            body = admin.get(PROPOSALS).json()
            assert body["signoffs"] == 5, "the month stays counted"
            assert body["proposed"] == 1
            assert body["not_proposed"] == 1
            assert body["without_proposal"] == 3
            assert {"month": "2026-07", "proposed": None} in body["months"]
        finally:
            _delete_signoffs([edge_id])

        # --- The neighbouring route answers as before, 404 for a
        #     missing id. --------------------------------------------
        response = admin.get(f"{LIST}/{stored['id']}")
        assert response.status_code == 200, response.text
        assert response.json()["figures"]["signoff_proposal"]["proposed"] is True
        row = admin.get(f"{LIST}/{seeded_ids[-1]}")
        assert row.status_code == 200, row.text
        assert row.json()["month"] == "2026-11"
        assert row.json()["figures"] == {"signoff_proposal": None}
        missing = admin.get(f"{LIST}/999999999")
        assert missing.status_code == 404
        assert missing.json()["detail"] == "sign-off not found"
        assert admin.get(f"{LIST}/not-an-id").status_code == 422

        # --- Access and writes: 401 without a token, 200 for the
        #     user role, and the route writes nothing. ---------------
        assert anon.get(PROPOSALS).status_code == 401
        viewer_body = viewer.get(PROPOSALS)
        assert viewer_body.status_code == 200, viewer_body.text
        assert viewer_body.json() == admin.get(PROPOSALS).json()
        rows_before = _signoff_rows()
        assert admin.get(PROPOSALS).status_code == 200
        assert viewer.get(PROPOSALS).status_code == 200
        assert anon.get(PROPOSALS).status_code == 401
        assert _signoff_rows() == rows_before, "the route writes no row"

        # --- Identity: a second pass after a full reload, as the
        #     NON-ADMIN (role "user").  The page is booted the
        #     production way: the token it would take from the URL
        #     hash as #access_token=... is the development token
        #     (role "user") sent as its Bearer token;
        #     GET /api/auth/config answers publicly and
        #     GET /api/auth/me resolves the viewer.
        viewer_reloaded = TestClient(
            app, headers={"Authorization": f"Bearer {user_token}"}
        )
        anon_reloaded = TestClient(app)
        config = anon_reloaded.get("/api/auth/config")
        assert config.status_code == 200
        assert config.json()["authEnabled"] is True
        me = viewer_reloaded.get("/api/auth/me")
        assert me.status_code == 200, me.text
        me_body = me.json()
        assert me_body["authenticated"] is True
        assert me_body["user"]["roles"] == ["user"]
        assert "admin" not in me_body["user"]["roles"]
        # The page still renders for the viewer — the route answers
        # the same body as the admin's — while the admin-only
        # sign-off control is absent (refused) and no token is still
        # a 401.
        response = viewer_reloaded.get(PROPOSALS)
        assert response.status_code == 200, response.text
        assert response.json() == admin.get(PROPOSALS).json()
        refused = viewer_reloaded.post(
            LIST, json={"month": REVIEW_MONTH, "note": "nope"}
        )
        assert refused.status_code == 403, refused.text
        assert refused.json()["detail"] == ADMIN_DETAIL
        assert anon_reloaded.get(PROPOSALS).status_code == 401
        assert _signoff_rows() == rows_before, "nothing new was written"
    finally:
        _delete_signoffs(seeded_ids)
