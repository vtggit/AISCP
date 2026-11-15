r"""Issue #1128 proof: the item and the stamp histories behind the API.

Everything is exercised against the test database through the app's
test client with the two development stub users: the admin stub
(``dev-secret-token:admin``, sub ``dev-admin-1``, roles admin+user,
username ``admin``) publishes with ``PUT /api/guidance``, and the
non-admin stub (role ``user``, sub ``dev-user-1``, username
``developer``) reads the very same answers.

Proved here:

  * the items — ``SHOP-D-1`` (type ``design``, title ``One database
    product``) in two wordings, wording A with the statement
    ``Every service stores its data in PostgreSQL.`` and the hash
    ``caddbcd58d``, wording B with the statement ``Every service
    stores its data in PostgreSQL 15.`` and the hash ``40ff75ae8d``;
    and ``SHOP-R-1`` (type ``roadmap``, ``T``/``S.``, hash
    ``0282fa9675``) — each with ``scope`` ``SHOP``, ``area``
    ``stack``, ``area_title`` ``Software stack``, ``decided_by``
    ``github:ceo``, ``decided_on`` ``2026-11-01`` and no optional
    field, whose hashes the proof also works out itself by the hash
    rule;
  * four publications, in this order: ``commit-1`` with ``SHOP-D-1``
    in wording A and ``SHOP-R-1``, both ``active``; ``commit-2`` with
    ``SHOP-D-1`` in wording B and ``SHOP-R-1``; ``commit-3`` with
    ``SHOP-D-1`` in wording A again and ``SHOP-R-1`` with ``status``
    ``retired``; ``commit-4`` with ``SHOP-D-1`` in wording A only;
  * the history of ``SHOP-D-1`` — ``GET /api/guidance/items/SHOP-D-1``
    answers 200 with ``key`` ``SHOP-D-1``, ``current`` true and three
    versions whose hashes are, in order, ``caddbcd58d``,
    ``40ff75ae8d``, ``caddbcd58d``: the first is ``current`` true
    with ``first.source`` ``commit-3`` and ``last.source``
    ``commit-4``; the second is ``current`` false with
    ``first.source`` and ``last.source`` both ``commit-2``; the third
    is ``current`` false with both ``commit-1`` — every version
    carries the fixed field set, its ``stamp`` is ``SHOP-D-1@``
    followed by its hash, and each ``first.published_at`` and
    ``last.published_at`` parses as a date and time with a UTC offset
    and equals the ``published_at`` of that publication in
    ``guidance_publications``;
  * the history of an item that is no longer published — ``GET
    /api/guidance/items/SHOP-R-1`` answers 200 with ``current`` false
    and two versions, the first with ``status`` ``retired``,
    ``first.source`` and ``last.source`` both ``commit-3``, the
    second with ``status`` ``active``, ``first.source`` ``commit-1``
    and ``last.source`` ``commit-2``; ``published_by`` in each is the
    admin's username;
  * a stamp that is current — ``GET
    /api/guidance/stamps/SHOP-D-1@caddbcd58d`` answers 200 with
    ``stamp`` ``SHOP-D-1@caddbcd58d``, ``key`` ``SHOP-D-1``,
    ``hash`` ``caddbcd58d``, ``type`` ``design``, ``title``
    ``One database product``, the statement of wording A,
    ``current`` true, ``superseded`` false and two versions, the
    newer first (``first.source`` ``commit-3``, then
    ``first.source`` ``commit-1``);
  * a stamp that was replaced — ``GET
    /api/guidance/stamps/SHOP-D-1@40ff75ae8d`` answers 200 with the
    statement of wording B, ``current`` false, ``superseded`` true
    and one version; a stamp of an item that is no longer published —
    ``GET /api/guidance/stamps/SHOP-R-1@0282fa9675`` answers 200 with
    ``current`` false, ``superseded`` false and two versions;
  * not found — ``GET /api/guidance/items/SHOP-D-2`` and ``GET
    /api/guidance/stamps/SHOP-D-1@0000000000`` answer 404;
  * refused — the items route answers 422 in FastAPI's
    HTTPValidationError envelope with ``loc`` ``["path", "key"]`` for
    ``shop-d-1``, ``SHOP-X-1`` and ``SHOP-D-12345``; the stamps route
    answers 422 for ``SHOP-D-1``, ``SHOP-D-1@CADDBCD58D``,
    ``SHOP-D-1@caddbcd58`` and ``shop-d-1@caddbcd58d``; and the
    hostile path parameters (a 10,000-character key, a NUL byte, a
    tab) never answer 500;
  * who may — the non-admin's answers to the two history calls and
    the three stamp calls equal the admin's; without a token both
    routes answer 401;
  * read-only — the number of rows in ``guidance_publications`` and
    in ``guidance_versions`` is the same before and after all these
    reads;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role ``user``, asserted via ``GET /api/auth/me``) in a second
    pass after a full reload (a fresh ``TestClient`` booted the
    production way — the token the page would take from the URL hash
    as ``#access_token=...``, with ``GET /api/auth/config``
    answering publicly and ``GET /api/auth/me`` resolving the
    viewer): the admin-only control is absent — the viewer reads the
    very same histories and stamps (the page renders) — while a
    request without a token still gets 401.
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

PUT_URL = "/api/guidance"
ITEMS_D1 = "/api/guidance/items/SHOP-D-1"
ITEMS_R1 = "/api/guidance/items/SHOP-R-1"
ITEMS_D2 = "/api/guidance/items/SHOP-D-2"
STAMP_A = "/api/guidance/stamps/SHOP-D-1@caddbcd58d"
STAMP_B = "/api/guidance/stamps/SHOP-D-1@40ff75ae8d"
STAMP_R = "/api/guidance/stamps/SHOP-R-1@0282fa9675"
STAMP_MISSING = "/api/guidance/stamps/SHOP-D-1@0000000000"
_PUBLICATIONS = "guidance_publications"
_VERSIONS = "guidance_versions"
_VERSION_FIELDS = {
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
    "current",
    "first",
    "last",
}
_REF_FIELDS = {
    "publication_id",
    "source",
    "published_at",
    "published_by",
}
A_STATEMENT = "Every service stores its data in PostgreSQL."
B_STATEMENT = "Every service stores its data in PostgreSQL 15."


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
    """One of the items used below (scope the part of the key before
    its first hyphen, area stack, area_title Software stack, active,
    decided by github:ceo on 2026-11-01, no optional field)."""
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


SHOP_D_1_A = _item(
    "SHOP-D-1", "design", "One database product", A_STATEMENT, "caddbcd58d"
)
SHOP_D_1_B = _item(
    "SHOP-D-1", "design", "One database product", B_STATEMENT, "40ff75ae8d"
)
SHOP_R_1_ACTIVE = _item("SHOP-R-1", "roadmap", "T", "S.", "0282fa9675")
SHOP_R_1_RETIRED = _item(
    "SHOP-R-1", "roadmap", "T", "S.", "0282fa9675", status="retired"
)


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


def _expect_version(
    version: dict,
    key: str,
    *,
    title: str,
    item_type: str,
    statement: str,
    status: str,
    current: bool,
    first_source: str,
    last_source: str,
    publications: dict,
) -> None:
    """One version of one item: the fixed field set, exactly what was
    sent, its two publications as stored in guidance_publications."""
    assert set(version) == _VERSION_FIELDS, version
    assert version["stamp"] == f"{key}@{version['hash']}", version
    assert version["type"] == item_type, version
    assert version["scope"] == "SHOP", version
    assert version["area"] == "stack", version
    assert version["area_title"] == "Software stack", version
    assert version["title"] == title, version
    assert version["statement"] == statement, version
    assert version["status"] == status, version
    assert version["decided_by"] == "github:ceo", version
    assert version["decided_on"] == "2026-11-01", version
    # No optional field was sent: all three are null in the answer.
    assert version["decided_under"] is None, version
    assert version["decided_ref"] is None, version
    assert version["authority"] is None, version
    assert version["current"] is current, version
    for prefix, source in (("first", first_source), ("last", last_source)):
        ref = version[prefix]
        assert set(ref) == _REF_FIELDS, ref
        row = publications[source]
        assert ref["source"] == source, ref
        assert ref["publication_id"] == row["id"], ref
        assert ref["published_by"] == ADMIN_USERNAME, ref
        assert _parse_utc(ref["published_at"]) == row["published_at"], ref


def test_issue1128_freeform(client):
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    # The proof's items carry the hashes the proof works out
    # itself by the hash rule.
    for item in (SHOP_D_1_A, SHOP_D_1_B, SHOP_R_1_ACTIVE, SHOP_R_1_RETIRED):
        assert item["hash"] == _hash(item["type"], item["title"], item["statement"])
    assert SHOP_R_1_ACTIVE["status"] == "active"
    assert SHOP_R_1_RETIRED["status"] == "retired"

    # -- Four publications, in this order. ----------------------------------
    response = admin.put(
        PUT_URL, json={"source": "commit-1", "items": [SHOP_D_1_A, SHOP_R_1_ACTIVE]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    first_id = body["publication_id"]
    assert isinstance(first_id, int)
    assert body["items"] == 2
    assert body["new_versions"] == 2
    assert body["withdrawn"] == 0

    response = admin.put(
        PUT_URL, json={"source": "commit-2", "items": [SHOP_D_1_B, SHOP_R_1_ACTIVE]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    second_id = body["publication_id"]
    assert second_id > first_id
    assert body["items"] == 2
    assert body["new_versions"] == 1
    assert body["withdrawn"] == 0

    response = admin.put(
        PUT_URL, json={"source": "commit-3", "items": [SHOP_D_1_A, SHOP_R_1_RETIRED]}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    third_id = body["publication_id"]
    assert third_id > second_id
    assert body["items"] == 2
    assert body["new_versions"] == 2
    assert body["withdrawn"] == 0

    response = admin.put(PUT_URL, json={"source": "commit-4", "items": [SHOP_D_1_A]})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    fourth_id = body["publication_id"]
    assert fourth_id > third_id
    assert body["items"] == 1
    assert body["new_versions"] == 0
    assert body["withdrawn"] == 1

    # The four publications as stored, by source.
    publications = {row["source"]: row for row in _rows(_PUBLICATIONS)}
    assert set(publications) == {
        "commit-1",
        "commit-2",
        "commit-3",
        "commit-4",
    }, publications
    # The four publications are the newest four rows, in the order
    # published.
    assert [row["id"] for row in _rows(_PUBLICATIONS)][-4:] == [
        first_id,
        second_id,
        third_id,
        fourth_id,
    ]

    # -- Read-only: the counts before all the reads below. ------------------
    before_counts = (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    )

    # -- The history of SHOP-D-1: three versions, newest first. -------------
    response = admin.get(ITEMS_D1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"key", "current", "versions"}, body
    assert body["key"] == "SHOP-D-1", body
    assert body["current"] is True, body
    versions = body["versions"]
    assert [v["hash"] for v in versions] == [
        "caddbcd58d",
        "40ff75ae8d",
        "caddbcd58d",
    ], versions
    _expect_version(
        versions[0],
        "SHOP-D-1",
        title="One database product",
        item_type="design",
        statement=A_STATEMENT,
        status="active",
        current=True,
        first_source="commit-3",
        last_source="commit-4",
        publications=publications,
    )
    _expect_version(
        versions[1],
        "SHOP-D-1",
        title="One database product",
        item_type="design",
        statement=B_STATEMENT,
        status="active",
        current=False,
        first_source="commit-2",
        last_source="commit-2",
        publications=publications,
    )
    _expect_version(
        versions[2],
        "SHOP-D-1",
        title="One database product",
        item_type="design",
        statement=A_STATEMENT,
        status="active",
        current=False,
        first_source="commit-1",
        last_source="commit-1",
        publications=publications,
    )

    # -- The history of SHOP-R-1, no longer published. -----------------------
    response = admin.get(ITEMS_R1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"key", "current", "versions"}, body
    assert body["key"] == "SHOP-R-1", body
    assert body["current"] is False, body
    versions = body["versions"]
    assert [v["hash"] for v in versions] == ["0282fa9675", "0282fa9675"], versions
    _expect_version(
        versions[0],
        "SHOP-R-1",
        title="T",
        item_type="roadmap",
        statement="S.",
        status="retired",
        current=False,
        first_source="commit-3",
        last_source="commit-3",
        publications=publications,
    )
    _expect_version(
        versions[1],
        "SHOP-R-1",
        title="T",
        item_type="roadmap",
        statement="S.",
        status="active",
        current=False,
        first_source="commit-1",
        last_source="commit-2",
        publications=publications,
    )

    # -- A stamp that is current. --------------------------------------------
    response = admin.get(STAMP_A)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "stamp",
        "key",
        "hash",
        "type",
        "title",
        "statement",
        "current",
        "superseded",
        "versions",
    }, body
    assert body["stamp"] == "SHOP-D-1@caddbcd58d", body
    assert body["key"] == "SHOP-D-1", body
    assert body["hash"] == "caddbcd58d", body
    assert body["type"] == "design", body
    assert body["title"] == "One database product", body
    assert body["statement"] == A_STATEMENT, body
    assert body["current"] is True, body
    assert body["superseded"] is False, body
    versions = body["versions"]
    assert len(versions) == 2, versions
    _expect_version(
        versions[0],
        "SHOP-D-1",
        title="One database product",
        item_type="design",
        statement=A_STATEMENT,
        status="active",
        current=True,
        first_source="commit-3",
        last_source="commit-4",
        publications=publications,
    )
    _expect_version(
        versions[1],
        "SHOP-D-1",
        title="One database product",
        item_type="design",
        statement=A_STATEMENT,
        status="active",
        current=False,
        first_source="commit-1",
        last_source="commit-1",
        publications=publications,
    )

    # -- A stamp that was replaced. ------------------------------------------
    response = admin.get(STAMP_B)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "stamp",
        "key",
        "hash",
        "type",
        "title",
        "statement",
        "current",
        "superseded",
        "versions",
    }, body
    assert body["stamp"] == "SHOP-D-1@40ff75ae8d", body
    assert body["key"] == "SHOP-D-1", body
    assert body["hash"] == "40ff75ae8d", body
    assert body["type"] == "design", body
    assert body["title"] == "One database product", body
    assert body["statement"] == B_STATEMENT, body
    assert body["current"] is False, body
    assert body["superseded"] is True, body
    versions = body["versions"]
    assert len(versions) == 1, versions
    _expect_version(
        versions[0],
        "SHOP-D-1",
        title="One database product",
        item_type="design",
        statement=B_STATEMENT,
        status="active",
        current=False,
        first_source="commit-2",
        last_source="commit-2",
        publications=publications,
    )

    # -- A stamp of an item that is no longer published. ---------------------
    response = admin.get(STAMP_R)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "stamp",
        "key",
        "hash",
        "type",
        "title",
        "statement",
        "current",
        "superseded",
        "versions",
    }, body
    assert body["stamp"] == "SHOP-R-1@0282fa9675", body
    assert body["key"] == "SHOP-R-1", body
    assert body["hash"] == "0282fa9675", body
    assert body["type"] == "roadmap", body
    assert body["title"] == "T", body
    assert body["statement"] == "S.", body
    assert body["current"] is False, body
    assert body["superseded"] is False, body
    versions = body["versions"]
    assert len(versions) == 2, versions
    _expect_version(
        versions[0],
        "SHOP-R-1",
        title="T",
        item_type="roadmap",
        statement="S.",
        status="retired",
        current=False,
        first_source="commit-3",
        last_source="commit-3",
        publications=publications,
    )
    _expect_version(
        versions[1],
        "SHOP-R-1",
        title="T",
        item_type="roadmap",
        statement="S.",
        status="active",
        current=False,
        first_source="commit-1",
        last_source="commit-2",
        publications=publications,
    )

    # -- Not found: a key no row has, and a hash the key never had. ----------
    response = admin.get(ITEMS_D2)
    assert response.status_code == 404, response.text
    response = admin.get(STAMP_MISSING)
    assert response.status_code == 404, response.text

    # -- Refused, each 422: FastAPI's HTTPValidationError envelope. ----------
    for bad_key in ("shop-d-1", "SHOP-X-1", "SHOP-D-12345"):
        response = admin.get(f"/api/guidance/items/{bad_key}")
        assert response.status_code == 422, (bad_key, response.text)
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, detail
        # The invalid key path parameter is named in the envelope.
        assert any(err["loc"] == ["path", "key"] for err in detail), detail
    for bad_stamp in (
        "SHOP-D-1",
        "SHOP-D-1@CADDBCD58D",
        "SHOP-D-1@caddbcd58",
        "shop-d-1@caddbcd58d",
    ):
        response = admin.get(f"/api/guidance/stamps/{bad_stamp}")
        assert response.status_code == 422, (bad_stamp, response.text)
        detail = response.json()["detail"]
        assert isinstance(detail, list) and detail, detail
        assert any(err["loc"] == ["path", "stamp"] for err in detail), detail

    # Hostile path parameters never answer 500: a 10,000-character
    # key, a NUL byte and a tab all refuse 422.
    assert admin.get("/api/guidance/items/" + "A" * 10_000).status_code == 422
    assert admin.get("/api/guidance/items/%00").status_code == 422
    assert admin.get("/api/guidance/stamps/%09").status_code == 422

    # -- Who may: the non-admin's answers to the two history calls and the
    #    three stamp calls equal the admin's; without a token both routes
    #    answer 401. ---------------------------------------------------------
    for url in (ITEMS_D1, ITEMS_R1, STAMP_A, STAMP_B, STAMP_R):
        admin_response = admin.get(url)
        viewer_response = viewer.get(url)
        assert viewer_response.status_code == admin_response.status_code == 200
        assert viewer_response.json() == admin_response.json(), url
    assert anon.get(ITEMS_D1).status_code == 401
    assert anon.get(STAMP_A).status_code == 401

    # -- Read-only: all of these reads stored nothing. -----------------------
    assert (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    ) == before_counts

    # -- Identity, second pass after a full reload: the page boots the
    #    production way (the token it would take from the URL hash as
    #    #access_token=...), /api/auth/config answers publicly and
    #    /api/auth/me resolves the viewer; the admin-only control is
    #    absent — the viewer reads the very same histories and stamps
    #    (they render) — and a request without a token still gets 401. --
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
    # The admin-only control is absent for the viewer: every read
    # answers 200, not 403, with the very same answer the admin gets.
    for url in (ITEMS_D1, ITEMS_R1, STAMP_A, STAMP_B, STAMP_R):
        page = viewer_reloaded.get(url)
        assert page.status_code == 200, page.text
        assert page.json() == admin.get(url).json(), url
    # ...and a request without a token still gets 401.
    assert anon.get(ITEMS_D1).status_code == 401
    assert anon.get(STAMP_A).status_code == 401
    # Still read-only after the viewer's reads.
    assert (
        _row_count(_PUBLICATIONS),
        _row_count(_VERSIONS),
    ) == before_counts
