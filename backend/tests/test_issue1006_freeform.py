"""Issue #1006 proof: item-mapping suggestions, read-only, for any
signed-in user.

Everything is exercised against the test database through the app's
test client with the two development stub users: the admin stub
(``dev-secret-token:admin``, sub ``dev-admin-1``, roles admin+user)
stores the mappings that make codes "already mapped", and the non-admin
stub (role ``user``, sub ``dev-user-1``) reads the suggestions and is
refused the mutations.  The seeds go through the real repositories —
``item_sites`` and ``inventory_items`` through their
``upsert_many``, the mappings through ``PUT /api/erp-mappings``.

Proved here:

  * by code — ``SAME-1`` held in ``item_sites`` at ``154`` under
    ``acumatica`` and at ``WH`` under ``odoo`` answers one suggestion
    with ``source_code`` and ``target_code`` both ``SAME-1`` and
    ``matched_on`` ``code``;
  * by description — ``ACU-1`` named "Oak Desk " under ``acumatica``
    and ``ODO-1`` named "oak desk" under ``odoo`` answer one
    suggestion with ``matched_on`` ``description``, with both
    descriptions as stored;
  * not a match — "Oak Desk" against "Oak Desks" answers nothing,
    two null descriptions and two blank ones answer nothing, and two
    items under the same source answer nothing: no similarity score,
    no partial match, no match on the unit of measure;
  * ambiguous — ``ACU-2`` named "Chair" against ``ODO-2`` and
    ``ODO-3``, both named "chair", answers two suggestions, both
    with ``ambiguous`` true (and ``ambiguous`` false on the rest);
  * already mapped — after ``PUT /api/erp-mappings`` maps ``ACU-1``
    to ``ODO-1`` that pair is gone, and a code that is another
    mapping's target is left out too;
  * the answer — ordered by ``matched_on`` with ``code`` first, then
    ``source_code``, then ``target_code``; the descriptions are null
    when absent; ``limit=1`` answers one; ``limit=0`` and
    ``limit=1001`` (and other hostile limits) answer 422, never 500;
  * nothing written — the three tables hold the same rows after the
    call as before it;
  * the route's place — ``GET /api/erp-mappings`` still answers its
    own stored list; a non-admin signed-in user gets 200 and no token
    gets 401;
  * the identity requirement: the proof also runs as a NON-ADMIN (role
    ``user``, asserted via ``GET /api/auth/me``) in a second pass
    after a full reload (a fresh ``TestClient`` booted the production
    way — the token the page would take from the URL hash as
    ``#access_token=...``, with ``GET /api/auth/config`` answering
    publicly and ``GET /api/auth/me`` resolving the viewer): the
    admin-only controls are refused with 403 (nothing stored or
    deleted) while the page still renders — the suggestions it lists
    answer 200 — and a request without a token still gets 401.
"""

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_DETAIL = "Role 'admin' is required to perform this action."

SUGGESTIONS_URL = "/api/erp-mappings/suggestions"
MAPPINGS_URL = "/api/erp-mappings"
_TABLES = ("item_sites", "inventory_items", "erp_code_mappings")

_CODE_SAME = {
    "source_code": "SAME-1",
    "target_code": "SAME-1",
    "matched_on": "code",
    "source_description": None,
    "target_description": None,
    "ambiguous": False,
}
_DESC_ACU1 = {
    "source_code": "ACU-1",
    "target_code": "ODO-1",
    "matched_on": "description",
    "source_description": "Oak Desk ",
    "target_description": "oak desk",
    "ambiguous": False,
}
_DESC_ACU2_OD2 = {
    "source_code": "ACU-2",
    "target_code": "ODO-2",
    "matched_on": "description",
    "source_description": "Chair",
    "target_description": "chair",
    "ambiguous": True,
}
_DESC_ACU2_OD3 = {
    "source_code": "ACU-2",
    "target_code": "ODO-3",
    "matched_on": "description",
    "source_description": "Chair",
    "target_description": "chair",
    "ambiguous": True,
}

# The full answer after the seeds, in order: code first, then the
# description pairs by source_code, then target_code.
EXPECTED_BASE = [_CODE_SAME, _DESC_ACU1, _DESC_ACU2_OD2, _DESC_ACU2_OD3]
# After ACU-1 -> ODO-1 is stored: that pair is gone.
EXPECTED_AFTER_ACU1 = [_CODE_SAME, _DESC_ACU2_OD2, _DESC_ACU2_OD3]
# After ACU-9 -> ODO-2 is stored: ODO-2 is another mapping's target.
EXPECTED_AFTER_TARGET = [_CODE_SAME, _DESC_ACU2_OD3]


def _seed() -> None:
    """The rows through the real repositories (no API writes)."""
    ItemSitePostgresRepository().upsert_many(
        [
            {"item_code": "SAME-1", "site_code": "154", "source_system": "acumatica"},
            {"item_code": "SAME-1", "site_code": "WH", "source_system": "odoo"},
        ]
    )
    InventoryItemPostgresRepository().upsert_many(
        [
            # By description: equal after trimming and ignoring case.
            {
                "item_code": "ACU-1",
                "description": "Oak Desk ",
                "source_system": "acumatica",
            },
            {"item_code": "ODO-1", "description": "oak desk", "source_system": "odoo"},
            # Ambiguous: one source against two targets.
            {
                "item_code": "ACU-2",
                "description": "Chair",
                "source_system": "acumatica",
            },
            {"item_code": "ODO-2", "description": "chair", "source_system": "odoo"},
            {"item_code": "ODO-3", "description": "chair", "source_system": "odoo"},
            # Null and blank descriptions never match.
            {"item_code": "ACU-N", "description": None, "source_system": "acumatica"},
            {"item_code": "ODO-N", "description": None, "source_system": "odoo"},
            {"item_code": "ACU-B", "description": "   ", "source_system": "acumatica"},
            {"item_code": "ODO-B", "description": " ", "source_system": "odoo"},
            # Two items under the same source: no pair, nothing to match.
            {
                "item_code": "ACU-L1",
                "description": "Lamp",
                "source_system": "acumatica",
            },
            {
                "item_code": "ACU-L2",
                "description": "LAMP",
                "source_system": "acumatica",
            },
        ]
    )


def _table_snapshot() -> dict[str, tuple]:
    """Every row of the three tables, as sorted tuples for equality."""
    conn = get_connection()
    try:
        snapshot = {}
        with conn.cursor() as cur:
            for table in _TABLES:
                cur.execute(f"SELECT * FROM {table}")
                snapshot[table] = tuple(sorted(tuple(row) for row in cur.fetchall()))
        return snapshot
    finally:
        conn.close()


def test_issue1006_freeform(client):
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    _seed()

    # -- By code, by description, ambiguous, not-a-match: the full
    #    answer, in order, with the stored descriptions (null when the
    #    code has no inventory_items row under its side). -------------
    response = admin.get(SUGGESTIONS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_BASE

    by_code = [s for s in EXPECTED_BASE if s["matched_on"] == "code"]
    by_description = [s for s in EXPECTED_BASE if s["matched_on"] == "description"]
    assert by_code == [
        {
            "source_code": "SAME-1",
            "target_code": "SAME-1",
            "matched_on": "code",
            "source_description": None,
            "target_description": None,
            "ambiguous": False,
        }
    ]
    assert _DESC_ACU1 in by_description  # descriptions as stored
    assert all(s["ambiguous"] for s in by_description if s["source_code"] == "ACU-2")
    assert not _DESC_ACU1["ambiguous"]
    # Nothing matched on a partial or similar description, on the unit
    # of measure, or on null/blank/same-source descriptions.
    for code in ("ACU-N", "ODO-N", "ACU-B", "ODO-B", "ACU-L1", "ACU-L2"):
        assert not any(
            s["source_code"] == code or s["target_code"] == code for s in EXPECTED_BASE
        )

    # -- Nothing written: the three tables hold the same rows after the
    #    call as before it. --------------------------------------------
    before = _table_snapshot()
    assert admin.get(SUGGESTIONS_URL).status_code == 200
    assert _table_snapshot() == before

    # -- The answer's limit: default 200, 1 to 1000, out of range 422.
    response = admin.get(SUGGESTIONS_URL, params={"limit": 1})
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_BASE[:1]
    for bad_limit in (0, 1001, -1, "abc", "1.5", "99999999999999999999", "\x00"):
        response = admin.get(SUGGESTIONS_URL, params={"limit": bad_limit})
        assert response.status_code == 422, f"limit={bad_limit!r}: {response.text}"

    # -- Already mapped: a stored item mapping to odoo by source code,
    #    and any mapping's target code, are left out. ------------------
    response = admin.put(
        MAPPINGS_URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": "ACU-1",
            "target_code": "ODO-1",
        },
    )
    assert response.status_code == 200, response.text
    assert admin.get(SUGGESTIONS_URL).json() == EXPECTED_AFTER_ACU1

    response = admin.put(
        MAPPINGS_URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": "ACU-9",
            "target_code": "ODO-2",
        },
    )
    assert response.status_code == 200, response.text
    assert admin.get(SUGGESTIONS_URL).json() == EXPECTED_AFTER_TARGET

    # -- Not a match: "Oak Desk" against "Oak Desks" answers nothing
    #    (the pair never appears, even though the stored rows now make
    #    "Oak Desk" a code on the source side). -------------------------
    InventoryItemPostgresRepository().upsert_many(
        [
            {
                "item_code": "ACU-X",
                "description": "Oak Desk",
                "source_system": "acumatica",
            },
            {"item_code": "ODO-X", "description": "Oak Desks", "source_system": "odoo"},
        ]
    )
    response = admin.get(SUGGESTIONS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_AFTER_TARGET
    assert not any(s["target_code"] == "ODO-X" for s in response.json())
    assert not any(
        (s["source_code"], s["target_code"]) == ("ACU-X", "ODO-X")
        for s in response.json()
    )

    # -- The route's place: GET /api/erp-mappings still answers its own
    #    stored list (kind then source code), not the suggestions. -----
    response = admin.get(MAPPINGS_URL)
    assert response.status_code == 200, response.text
    stored = response.json()
    assert [(m["source_code"], m["target_code"]) for m in stored] == [
        ("ACU-1", "ODO-1"),
        ("ACU-9", "ODO-2"),
    ]
    for row in stored:
        assert set(row) == {
            "id",
            "kind",
            "target_system",
            "source_code",
            "target_code",
            "created_at",
            "updated_at",
        }
        assert row["kind"] == "item" and row["target_system"] == "odoo"

    # -- Access: a non-admin signed-in user gets 200; no token, 401. ----
    response = viewer.get(SUGGESTIONS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_AFTER_TARGET
    assert viewer.get(MAPPINGS_URL).status_code == 200
    assert anon.get(SUGGESTIONS_URL).status_code == 401
    assert anon.get(MAPPINGS_URL).status_code == 401

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only controls are
    #    refused 403 while the page still renders for the viewer. ------
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
    # The admin-only controls are absent for the viewer: 403, nothing
    # stored or deleted.
    response = viewer_reloaded.put(
        MAPPINGS_URL,
        json={
            "kind": "item",
            "target_system": "odoo",
            "source_code": "ACU-2",
            "target_code": "ODO-3",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    mapping_id = admin.get(MAPPINGS_URL).json()[0]["id"]
    response = viewer_reloaded.delete(f"{MAPPINGS_URL}/{mapping_id}")
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    assert [
        (m["source_code"], m["target_code"]) for m in admin.get(MAPPINGS_URL).json()
    ] == [("ACU-1", "ODO-1"), ("ACU-9", "ODO-2")]
    # The page still renders: the suggestions it lists answer 200 for
    # the viewer, and a request without a token still gets 401.
    response = viewer_reloaded.get(SUGGESTIONS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_AFTER_TARGET
    assert viewer_reloaded.get(MAPPINGS_URL).status_code == 200
    assert TestClient(app).get(SUGGESTIONS_URL).status_code == 401
