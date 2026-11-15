r"""Issue #1125 proof: reading the published guidance behind the API.

Everything is exercised against the test database through the app's
test client with the two development stub users: the admin stub
(``dev-secret-token:admin``, sub ``dev-admin-1``, roles admin+user,
username ``admin``) publishes with ``PUT /api/guidance``, and the
non-admin stub (role ``user``, sub ``dev-user-1``, username
``developer``) reads the very same answer.

Proved here:

  * nothing published — ``GET /api/guidance`` answers 200 with
    exactly ``{"publication": null, "items": []}`` for the admin and
    for the non-admin, and 401 without a token;
  * the first publication — the admin's PUT of ``commit-1`` with six
    items (each with ``area`` ``stack``, ``area_title``
    ``Software stack``, ``status`` ``active``, ``decided_by``
    ``github:ceo``, ``decided_on`` ``2026-11-01``, no optional field,
    and ``scope`` the part of its key before its first hyphen), sent
    in this order: ``SHOP-D-10`` (design, the PostgreSQL 15
    statement, hash ``40ff75ae8d``), ``SHOP-D-2`` (design, the
    PostgreSQL statement, hash ``caddbcd58d``), ``SHOP-R-1``
    (roadmap, ``T``/``S.``, hash ``0282fa9675``), ``ACME-D-2``
    (design, the same title, statement and hash as ``SHOP-D-2``),
    ``SHOP-B-1`` (boundary, hash ``0223a4d736``) and ``SHOP-N-1``
    (direction, ``"Caf\xed"``/``"Na\xefve \u2264 5"``, hash
    ``6d418c8f93``), whose hashes the proof also works out itself by
    the hash rule — and, afterwards, ``GET /api/guidance`` answers
    200 with exactly the two keys ``publication`` and ``items``:
    ``publication`` holds the id the PUT answered, ``source``
    ``commit-1``, ``item_count`` 6, ``published_by`` the admin's
    username and a ``published_at`` that parses as a date and time
    with a UTC offset; and ``items`` holds the six versions with the
    keys, in this order, ``SHOP-N-1``, ``SHOP-B-1``, ``SHOP-R-1``,
    ``ACME-D-2``, ``SHOP-D-2``, ``SHOP-D-10`` — by type in the order
    direction, boundary, roadmap, design; within a type by the
    number at the end of the key compared as a number and ascending
    (``SHOP-D-2`` before ``SHOP-D-10``); and for the same number by
    the key in Unicode code point order (``ACME-D-2`` before
    ``SHOP-D-2``) — the route's own sort, never the database's
    collation.  Each item carries exactly the fixed field set:
    ``key``, ``hash``, ``stamp`` (the key, ``@`` and the hash,
    ``SHOP-D-10@40ff75ae8d`` for the last one), ``type``, ``scope``,
    ``area``, ``area_title``, ``title``, ``statement``, ``status``,
    ``decided_by``, ``decided_on`` — all exactly what was sent —
    ``decided_under``, ``decided_ref`` and ``authority`` all null,
    and ``since`` equal to ``publication.published_at``;
  * the same for everyone — the non-admin's answer equals the
    admin's, to the byte;
  * the second publication — the admin's PUT of ``commit-2`` that
    leaves out ``SHOP-R-1``, gives ``SHOP-D-10`` the statement
    ``Every service stores its data in PostgreSQL.`` and the hash
    ``caddbcd58d``, gives ``SHOP-B-1`` the ``decided_under``
    ``delegation``, the ``decided_ref`` ``SHOP#7`` and the
    ``authority`` ``executive``, and leaves the other three as they
    were — and, afterwards, ``publication`` holds the new id,
    ``source`` ``commit-2`` and ``item_count`` 5; the keys of
    ``items`` are, in this order, ``SHOP-N-1``, ``SHOP-B-1``,
    ``ACME-D-2``, ``SHOP-D-2``, ``SHOP-D-10``; ``SHOP-D-10`` shows
    the new statement and the stamp ``SHOP-D-10@caddbcd58d``;
    ``SHOP-B-1`` shows the three new optional fields; the ``since``
    of ``SHOP-D-10`` and of ``SHOP-B-1`` equals the second
    publication's ``published_at``; and the ``since`` of
    ``SHOP-N-1``, ``ACME-D-2`` and ``SHOP-D-2`` equals the first
    publication's ``published_at``, read from
    ``guidance_publications``, which is earlier than the second's;
  * read-only — the number of rows in ``guidance_publications`` and
    in ``guidance_versions`` is the same before and after three
    calls of the route;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role ``user``, asserted via ``GET /api/auth/me``) in a second
    pass after a full reload (a fresh ``TestClient`` booted the
    production way — the token the page would take from the URL hash
    as ``#access_token=...``, with ``GET /api/auth/config``
    answering publicly and ``GET /api/auth/me`` resolving the
    viewer): the admin-only control is absent — the viewer reads the
    very same guidance (it renders) — while a request without a
    token still gets 401.
"""

import hashlib
import os
from datetime import datetime

import psycopg2
import psycopg2.extras
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject
ADMIN_USERNAME = "admin"  # the admin stub user's username

URL = "/api/guidance"
_PUBLICATIONS = "guidance_publications"
_VERSIONS = "guidance_versions"
_PUBLICATION_FIELDS = {
    "id",
    "source",
    "published_at",
    "published_by",
    "item_count",
}
_ITEM_FIELDS = {
    "key",
    "hash",
    "stamp",
    "type",
    "scope",
    "area",
    "area_title",
    "title",
    "statement",
    "status",
    "decided_by",
    "decided_on",
    "decided_under",
    "decided_ref",
    "authority",
    "since",
}


def _hash(type: str, title: str, statement: str) -> str:
    """The hash the rule works out: first 10 lowercase hex of SHA-256.

    The SHA-256 of the UTF-8 bytes of ``type + "\n" + title + "\n" +
    statement + "\n"``, as the proof must work it out itself.
    """
    words = f"{type}\n{title}\n{statement}\n"
    return hashlib.sha256(words.encode("utf-8")).hexdigest()[:10]


def _item(
    key: str,
    type: str,
    title: str,
    statement: str,
    item_hash: str | None = None,
    **overrides,
) -> dict:
    """One of the items used below (area stack, area_title Software
    stack, active, decided by github:ceo on 2026-11-01, no optional
    field, scope the part of the key before its first hyphen)."""
    item = {
        "key": key,
        "type": type,
        "scope": key.split("-", 1)[0],
        "area": "stack",
        "area_title": "Software stack",
        "title": title,
        "statement": statement,
        "status": "active",
        "decided_by": "github:ceo",
        "decided_on": "2026-11-01",
        "hash": item_hash if item_hash is not None else _hash(type, title, statement),
    }
    item.update(overrides)
    return item


SHOP_D_10 = _item(
    "SHOP-D-10",
    "design",
    "One database product",
    "Every service stores its data in PostgreSQL 15.",
    "40ff75ae8d",
)
SHOP_D_2 = _item(
    "SHOP-D-2",
    "design",
    "One database product",
    "Every service stores its data in PostgreSQL.",
    "caddbcd58d",
)
SHOP_R_1 = _item("SHOP-R-1", "roadmap", "T", "S.", "0282fa9675")
ACME_D_2 = _item(
    "ACME-D-2",
    "design",
    "One database product",
    "Every service stores its data in PostgreSQL.",
    "caddbcd58d",
)
SHOP_B_1 = _item(
    "SHOP-B-1",
    "boundary",
    "No shopper data leaves SHOP",
    "SHOP sends a shopper's details to no other system.",
    "0223a4d736",
)
SHOP_N_1 = _item(
    "SHOP-N-1", "direction", "Caf\u00e9", "Na\u00efve \u2264 5", "6d418c8f93"
)
FIRST_ITEMS = [SHOP_D_10, SHOP_D_2, SHOP_R_1, ACME_D_2, SHOP_B_1, SHOP_N_1]

FIRST_KEYS = ["SHOP-N-1", "SHOP-B-1", "SHOP-R-1", "ACME-D-2", "SHOP-D-2", "SHOP-D-10"]
SECOND_KEYS = ["SHOP-N-1", "SHOP-B-1", "ACME-D-2", "SHOP-D-2", "SHOP-D-10"]


def _rows(table: str) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT * FROM {table} ORDER BY id")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _row_count(table: str) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT count(*) FROM {table}")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _parse_utc(value: str) -> datetime:
    """Parse one ISO-8601 timestamp and assert it carries a UTC offset."""
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None and parsed.utcoffset() is not None, value
    assert parsed.utcoffset().total_seconds() == 0, value
    return parsed


def _expect_item(item: dict, sent: dict, since: str) -> None:
    """One current version: the fixed field set, exactly what was sent."""
    assert set(item) == _ITEM_FIELDS, item
    assert item["stamp"] == f"{sent['key']}@{sent['hash']}", item
    assert item["key"] == sent["key"]
    assert item["hash"] == sent["hash"]
    assert item["type"] == sent["type"]
    assert item["scope"] == sent["scope"]
    assert item["area"] == sent["area"]
    assert item["area_title"] == sent["area_title"]
    assert item["title"] == sent["title"]
    assert item["statement"] == sent["statement"]
    assert item["status"] == sent["status"]
    assert item["decided_by"] == sent["decided_by"]
    assert item["decided_on"] == sent["decided_on"]
    # An absent optional field is null in the answer.
    assert item["decided_under"] == sent.get("decided_under")
    assert item["decided_ref"] == sent.get("decided_ref")
    assert item["authority"] == sent.get("authority")
    assert item["since"] == since, item


def test_issue1125_freeform(client):
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    # The proof's six items carry the hashes the proof works out
    # itself by the hash rule.
    for item in FIRST_ITEMS:
        assert item["hash"] == _hash(item["type"], item["title"], item["statement"])

    # -- Nothing published: 200 with exactly the empty answer, for the
    #    admin and for the non-admin; 401 without a token. --------------
    for who in (admin, viewer):
        response = who.get(URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"publication", "items"}, body
        assert body == {"publication": None, "items": []}, body
    assert anon.get(URL).status_code == 401

    # -- The first publication: commit-1, the six items in the
    #    proof's own order. ----------------------------------------------
    response = admin.put(URL, json={"source": "commit-1", "items": FIRST_ITEMS})
    assert response.status_code == 200, response.text
    first_body = response.json()
    assert first_body["changed"] is True
    assert first_body["items"] == 6
    assert first_body["new_versions"] == 6
    assert first_body["withdrawn"] == 0
    first_id = first_body["publication_id"]
    assert isinstance(first_id, int)

    # -- After the first publication. -------------------------------------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"publication", "items"}, body
    publication = body["publication"]
    assert set(publication) == _PUBLICATION_FIELDS, publication
    assert publication["id"] == first_id
    assert publication["source"] == "commit-1"
    assert publication["item_count"] == 6
    assert publication["published_by"] == ADMIN_USERNAME
    first_published_at = _parse_utc(publication["published_at"])
    items = body["items"]
    assert [item["key"] for item in items] == FIRST_KEYS
    sent = {item["key"]: item for item in FIRST_ITEMS}
    for item in items:
        _expect_item(item, sent[item["key"]], publication["published_at"])
        assert _parse_utc(item["since"]) == first_published_at

    # -- The same for everyone: the non-admin's answer equals the
    #    admin's. ----------------------------------------------------------
    other = viewer.get(URL)
    assert other.status_code == 200, other.text
    assert other.json() == body

    # -- The second publication: commit-2, five items — SHOP-R-1 left
    #    out, SHOP-D-10 reworded (the PostgreSQL statement, hash
    #    caddbcd58d), SHOP-B-1 given its decision record, the other
    #    three as they were. -------------------------------------------------
    second_items = [
        SHOP_N_1,
        _item(
            "SHOP-B-1",
            "boundary",
            "No shopper data leaves SHOP",
            "SHOP sends a shopper's details to no other system.",
            decided_under="delegation",
            decided_ref="SHOP#7",
            authority="executive",
        ),
        ACME_D_2,
        SHOP_D_2,
        _item(
            "SHOP-D-10",
            "design",
            "One database product",
            "Every service stores its data in PostgreSQL.",
        ),
    ]
    response = admin.put(URL, json={"source": "commit-2", "items": second_items})
    assert response.status_code == 200, response.text
    second_body = response.json()
    assert second_body["changed"] is True
    assert second_body["items"] == 5
    assert second_body["new_versions"] == 2
    assert second_body["withdrawn"] == 1
    second_id = second_body["publication_id"]
    assert isinstance(second_id, int)
    assert second_id != first_id

    # -- After the second publication. --------------------------------------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    publication = body["publication"]
    assert set(publication) == _PUBLICATION_FIELDS, publication
    assert publication["id"] == second_id
    assert publication["source"] == "commit-2"
    assert publication["item_count"] == 5
    assert publication["published_by"] == ADMIN_USERNAME
    second_published_at = _parse_utc(publication["published_at"])
    items = body["items"]
    assert [item["key"] for item in items] == SECOND_KEYS
    by_key = {item["key"]: item for item in items}
    # The fixed field set, exactly what was sent — the reworded and the
    # enriched item included.
    second_sent = {item["key"]: item for item in second_items}
    for item in items:
        _expect_item(item, second_sent[item["key"]], item["since"])
    d10 = by_key["SHOP-D-10"]
    assert d10["statement"] == "Every service stores its data in PostgreSQL."
    assert d10["hash"] == "caddbcd58d"
    assert d10["stamp"] == "SHOP-D-10@caddbcd58d"
    b1 = by_key["SHOP-B-1"]
    assert b1["decided_under"] == "delegation"
    assert b1["decided_ref"] == "SHOP#7"
    assert b1["authority"] == "executive"
    # Since: the rows the second publication changed were first held
    # by it; the rows it left as they were were first held by the
    # first publication — read from guidance_publications, earlier
    # than the second's.
    first_row = next(row for row in _rows(_PUBLICATIONS) if row["id"] == first_id)
    first_since = first_row["published_at"]
    assert first_since < second_published_at
    for key in ("SHOP-N-1", "ACME-D-2", "SHOP-D-2"):
        assert datetime.fromisoformat(by_key[key]["since"]) == first_since
    for key in ("SHOP-D-10", "SHOP-B-1"):
        assert datetime.fromisoformat(by_key[key]["since"]) == second_published_at

    # -- Read-only: three calls of the route store nothing. ------------------
    before_counts = (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    )
    for _ in range(3):
        response = admin.get(URL)
        assert response.status_code == 200, response.text
        assert response.json() == body
    assert (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    ) == before_counts

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only control is
    #    absent — the viewer reads the very same guidance (it
    #    renders) — and a request without a token still gets 401. --
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
    assert admin_me.json()["user"]["username"] == ADMIN_USERNAME
    # The admin-only control is absent for the viewer: the route
    # answers 200, not 403, and the answer is the very same one the
    # admin gets — the page renders.
    page = viewer_reloaded.get(URL)
    assert page.status_code == 200, page.text
    assert page.json() == body
    # ...and a request without a token still gets 401.
    assert anon.get(URL).status_code == 401
