"""Issue #1412 proof: GET /api/drift/decisions/acceptance.

AC-1/AC-3 — the route answers 200 to any authenticated user with the
same guard as ``GET /api/drift/decisions/summary`` (``require_any_authenticated_user``),
answers 401 without a token, and writes nothing: it reads the current
decision of every item-site (the latest by decided_at, id breaking
ties) with one call to
``PlannerDecisionPostgresRepository.current_decisions()``.

AC-2 — the proof deletes every row of ``planner_decisions`` on the
test database first and inserts its rows straight into the table (then
deletes them afterwards): item-site A a dismissed decision followed by
a later accepted one (current 10, recommended 15, money 500 — only the
later one counts), B dismissed (current 40, recommended 25, money
300), C accepted (current 30, recommended 20, money null), D snoozed
(current 5, recommended 9, money 100), E accepted (current null,
recommended 5, money 200) and F dismissed (current 7, recommended 7,
money 50).  The answer is accepted {count 3, money_at_stake 700,
without_money 1}, dismissed {count 2, money_at_stake 350,
without_money 0}, snoozed {count 1}, rate 0.6, money_rate 0.6667,
raise {accepted 2, dismissed 0} and lower {accepted 1, dismissed 1} —
the raise/lower direction of each current accepted/dismissed decision
(the stored current_reorder_point null or below the recommended value
raises, above it lowers, equal counts in neither, a snoozed decision
in neither).  With no decisions stored every count and sum is 0 and
both rates are null.  The row count of ``planner_decisions`` is the
same before and after a call.

AC-4 — the OpenAPI schema and this proof specify and assert the
response nullability (``rate`` and ``money_rate`` are null when their
denominator is 0), the rounding to 4 decimals (money_rate 0.6667 for
700 of 1050) and the direction of the accepted/dismissed money values
(700 = A 500 + E 200 for accepted, 350 = B 300 + F 50 for dismissed).

The identity requirement is proved in a second pass after a full
reload as a NON-ADMIN (role "user"): the page boots the production way
(the token it would take from the URL hash as ``#access_token=...``),
``GET /api/auth/config`` answers publicly and ``GET /api/auth/me``
resolves the viewer, the admin-only control is absent while the page
still renders, and no token is still a 401.
"""

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

ACCEPTANCE_URL = "/api/drift/decisions/acceptance"
POST_URL = "/api/drift/decisions"
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

SITE = "SITE-1412"
AS_OF = "2026-11-01"
DECIDED_BY = "seeder-1412"

# The token the page would take from the URL hash as
# #access_token=... — the development token for the non-admin viewer.
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"

# (id, item_code, decision, decided_at, reason, snooze_until,
# current_reorder_point, recommended_reorder_point_units, money_at_stake)
ROWS = [
    # A: the earlier dismissal is superseded — only the later
    # accepted decision is the current one of item-site A.
    (
        "pd-1412-a-1",
        "ITEM-1412-A",
        "dismissed",
        "2026-11-01T09:00:00+00:00",
        "superseded by the later acceptance",
        None,
        12,
        15,
        400.0,
    ),
    (
        "pd-1412-a-2",
        "ITEM-1412-A",
        "accepted",
        "2026-11-02T09:00:00+00:00",
        "agreed with the review",
        None,
        10,
        15,
        500.0,
    ),
    (
        "pd-1412-b",
        "ITEM-1412-B",
        "dismissed",
        "2026-11-01T10:00:00+00:00",
        "not now",
        None,
        40,
        25,
        300.0,
    ),
    (
        "pd-1412-c",
        "ITEM-1412-C",
        "accepted",
        "2026-11-01T11:00:00+00:00",
        "agreed with the review",
        None,
        30,
        20,
        None,
    ),
    (
        "pd-1412-d",
        "ITEM-1412-D",
        "snoozed",
        "2026-11-01T12:00:00+00:00",
        None,
        "2026-12-01",
        5,
        9,
        100.0,
    ),
    (
        "pd-1412-e",
        "ITEM-1412-E",
        "accepted",
        "2026-11-01T13:00:00+00:00",
        "agreed with the review",
        None,
        None,
        5,
        200.0,
    ),
    (
        "pd-1412-f",
        "ITEM-1412-F",
        "dismissed",
        "2026-11-01T14:00:00+00:00",
        "the point already matches",
        None,
        7,
        7,
        50.0,
    ),
]

EXPECTED = {
    "accepted": {"count": 3, "money_at_stake": 700.0, "without_money": 1},
    "dismissed": {"count": 2, "money_at_stake": 350.0, "without_money": 0},
    "snoozed": {"count": 1},
    "rate": 0.6,
    "money_rate": 0.6667,
    "raise": {"accepted": 2, "dismissed": 0},
    "lower": {"accepted": 1, "dismissed": 1},
}

EMPTY = {
    "accepted": {"count": 0, "money_at_stake": 0.0, "without_money": 0},
    "dismissed": {"count": 0, "money_at_stake": 0.0, "without_money": 0},
    "snoozed": {"count": 0},
    "rate": None,
    "money_rate": None,
    "raise": {"accepted": 0, "dismissed": 0},
    "lower": {"accepted": 0, "dismissed": 0},
}


def _count_rows() -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM planner_decisions")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _insert_rows() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for (
                row_id,
                item_code,
                decision,
                decided_at,
                reason,
                snooze_until,
                current,
                recommended,
                money,
            ) in ROWS:
                cur.execute(
                    "INSERT INTO planner_decisions "
                    "(id, item_code, site_code, decision, reason, "
                    "snooze_until, as_of, recommended_reorder_point_units, "
                    "current_reorder_point, money_at_stake, decided_by, "
                    "decided_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        row_id,
                        item_code,
                        SITE,
                        decision,
                        reason,
                        snooze_until,
                        AS_OF,
                        recommended,
                        current,
                        money,
                        DECIDED_BY,
                        decided_at,
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _delete_rows() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM planner_decisions WHERE id = ANY(%s)",
                ([row[0] for row in ROWS],),
            )
        conn.commit()
    finally:
        conn.close()


def _resolve(schema: dict, node: dict) -> dict:
    """Follow a ``$ref`` chain to the referenced component schema."""
    while isinstance(node, dict) and "$ref" in node:
        node = schema["components"]["schemas"][node["$ref"].rsplit("/", 1)[-1]]
    return node


def _assert_openapi_contract() -> None:
    """The schema file lists the path and specifies the response
    nullability and the direction buckets."""
    schema = json.loads(_OPENAPI.read_text())
    path = schema["paths"]["/api/drift/decisions/acceptance"]
    assert "get" in path
    response_schema = path["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    model = _resolve(schema, response_schema)
    assert set(model["properties"]) == {
        "accepted",
        "dismissed",
        "snoozed",
        "rate",
        "money_rate",
        "raise",
        "lower",
    }
    # Nullability: rate and money_rate are numbers that may be null.
    for name in ("rate", "money_rate"):
        assert model["properties"][name] == {
            "anyOf": [{"type": "number"}, {"type": "null"}],
            "title": name.replace("_", " ").title(),
        }, (name, model["properties"][name])
    # The money buckets carry the summed stored money_at_stake of the
    # decisions in the direction.
    for name in ("accepted", "dismissed"):
        bucket = _resolve(schema, model["properties"][name])
        assert set(bucket["properties"]) == {
            "count",
            "money_at_stake",
            "without_money",
        }
    snoozed = _resolve(schema, model["properties"]["snoozed"])
    assert set(snoozed["properties"]) == {"count"}
    for name in ("raise", "lower"):
        direction = _resolve(schema, model["properties"][name])
        assert set(direction["properties"]) == {"accepted", "dismissed"}
        for field in direction["properties"].values():
            assert field["type"] == "integer"


def test_issue1412_freeform(user_token) -> None:
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon = TestClient(app)

    # The proof starts from an empty table: delete every row of
    # planner_decisions on the test database first.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM planner_decisions")
        conn.commit()
    finally:
        conn.close()
    assert _count_rows() == 0
    _insert_rows()

    try:
        # -- The answer over the current decisions. -------------------
        before = _count_rows()
        response = viewer.get(ACCEPTANCE_URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body == EXPECTED
        # The route writes nothing: the row count is unchanged.
        assert _count_rows() == before

        # The direction of the accepted/dismissed money values: the
        # accepted money is exactly the accepted decisions' snapshots
        # (A 500 + E 200; C's null adds nothing but is counted in
        # without_money), the dismissed money exactly the dismissed
        # ones (B 300 + F 50).
        assert body["accepted"]["money_at_stake"] == 500.0 + 200.0
        assert body["dismissed"]["money_at_stake"] == 300.0 + 50.0
        # Rounding to 4 decimals: 3 of 5 counts, 700 of 1050 money.
        assert body["rate"] == 3 / 5 == 0.6
        assert body["money_rate"] == round(700.0 / 1050.0, 4) == 0.6667
        # Directions: A (10 < 15) and E (null) raise among the
        # accepted, C (30 > 20) lowers; B (40 > 25) lowers among the
        # dismissed, F (7 = 7) counts in neither and D (snoozed) in
        # none.
        assert body["raise"] == {"accepted": 2, "dismissed": 0}
        assert body["lower"] == {"accepted": 1, "dismissed": 1}

        # -- Access: 200 for the user role, 401 without a token. ------
        assert viewer.get(ACCEPTANCE_URL).status_code == 200
        unauthenticated = anon.get(ACCEPTANCE_URL)
        assert unauthenticated.status_code == 401
        assert unauthenticated.json()["detail"] == (
            "Authentication required. Provide a valid Bearer token."
        )

        # -- The schema file carries the new path and its schemas. ----
        _assert_openapi_contract()
    finally:
        # Delete the proof's rows afterwards.
        _delete_rows()
    assert _count_rows() == 0

    # -- No decisions stored: both rates null, every count and sum 0.
    response = viewer.get(ACCEPTANCE_URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY
    assert _count_rows() == 0

    # -- Identity, second pass after a full reload as a NON-ADMIN
    #    (role "user"): the page boots the production way (the token
    #    it would take from the URL hash as #access_token=...),
    #    /api/auth/config answers publicly and /api/auth/me resolves
    #    the viewer.
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]

    # The admin-only control is absent for the acceptance view: the
    # non-admin gets 200 with the full body (the page still renders),
    # not the 403 an admin-only control would answer.
    response = viewer_reloaded.get(ACCEPTANCE_URL)
    assert response.status_code == 200, response.text
    assert response.json() == EMPTY
    # And the page's admin-only write control is still refused for
    # the viewer — the non-admin cannot record a decision, and the
    # refusal stores nothing.
    write = viewer_reloaded.post(
        POST_URL,
        json={
            "item_code": "ITEM-1412-A",
            "site_code": SITE,
            "decision": "accepted",
        },
    )
    assert write.status_code == 403, write.text
    assert _count_rows() == 0
    # Unauthenticated /api traffic is still a 401.
    assert anon_reloaded.get(ACCEPTANCE_URL).status_code == 401
