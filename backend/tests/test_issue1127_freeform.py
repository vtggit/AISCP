r"""Issue #1127 proof: the guidance publication from the deployment's command.

``python -m app.guidance_publish`` reads one JSON document from
standard input and publishes it exactly the way ``PUT /api/guidance``
does — the same checks, the same comparison with what is current, the
same rows, in one transaction — with the only difference that
``published_by`` is the string ``deployment``: the command opens no
HTTP connection and reads no token.  The command's ``main`` is called
here with text streams of its own (``io.StringIO``) and the two
tables are read directly; the route is called through the app's test
client with the admin development token, the way the #1124 proof
does.

Proved here:

  * the three items — ``SHOP-D-1`` (type ``design``, title ``One
    database product``, statement ``Every service stores its data in
    PostgreSQL.``, the hash the rule works out from the item's own
    words, ``caddbcd58d``), ``SHOP-R-1`` (type ``roadmap``, title
    ``T``, statement ``S.``, hash ``0282fa9675``) and ``SHOP-N-1``
    (type ``direction``, title ``"Café"``, statement ``"Naïve ≤ 5"``,
    hash ``6d418c8f93``), each with ``scope`` ``SHOP``, ``area``
    ``stack``, ``area_title`` ``Software stack``, ``status``
    ``active``, ``decided_by`` ``github:ceo``, ``decided_on``
    ``2026-11-01`` and no optional field;
  * the first publication — ``main`` given the body with ``source``
    ``commit-1`` and the three items returns 0; standard output is
    one line that parses as a JSON object with ``changed`` true,
    ``items`` 3, ``new_versions`` 3, ``withdrawn`` 0 and an integer
    ``publication_id``; standard error is empty;
    ``guidance_publications`` holds one row with that id, ``source``
    ``commit-1``, ``item_count`` 3 and ``published_by``
    ``deployment``; and ``guidance_versions`` holds three rows with
    both publication ids equal to that id;
  * the same again changes nothing — the same body returns 0 with
    ``changed`` false and the same ``publication_id``, and the tables
    hold the same rows;
  * the route sees what the command stored — the admin's
    ``PUT /api/guidance`` with the same three items answers 200 with
    ``changed`` false and that ``publication_id``;
  * the command sees what the route stored — after the admin's
    ``PUT`` in which ``SHOP-D-1`` has the statement ``Every service
    stores its data in PostgreSQL 15.`` and the hash the rule works
    out from it, ``40ff75ae8d``, ``main`` given that same body
    returns 0 with ``changed`` false, and ``main`` given the first
    body again returns 0 with ``changed`` true and ``new_versions``
    1, the new row's publication having ``published_by``
    ``deployment``;
  * the refused — each returning 2 with empty standard output, a
    standard error line starting with ``refused:`` and both tables
    unchanged: standard input that is not JSON (``{``), an empty
    standard input, a JSON list, the three items where the last one's
    hash is ``0000000000``, a body whose ``items`` is an empty list
    and a body with an extra key whose value is ``NaN``;
  * the failed — with the function that stores a publication replaced
    by one that raises ``RuntimeError("down")``, ``main`` given a
    valid body returns 1 with empty standard output and a standard
    error line that starts with ``failed:`` and holds no line
    starting with ``Traceback``;
  * running the module — the module's source ends by calling ``main``
    under ``if __name__ == "__main__":`` and exiting with its result.
"""

import hashlib
import io
import json
from pathlib import Path

import psycopg2
import psycopg2.extras
from fastapi.testclient import TestClient

import app.guidance_publish as guidance_publish
from app.db.connection import get_connection
from app.main import app

ADMIN_TOKEN = "dev-secret-token:admin"
URL = "/api/guidance"
_PUBLICATIONS = "guidance_publications"
_VERSIONS = "guidance_versions"


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
    item_hash: str,
) -> dict:
    """One of the items used below (scope SHOP, area stack, active,
    decided by github:ceo on 2026-11-01, no optional field)."""
    return {
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
        "hash": item_hash,
    }


ITEM_DESIGN = _item(
    "SHOP-D-1",
    "design",
    "One database product",
    "Every service stores its data in PostgreSQL.",
    "caddbcd58d",
)
ITEM_ROADMAP = _item("SHOP-R-1", "roadmap", "T", "S.", "0282fa9675")
ITEM_DIRECTION = _item("SHOP-N-1", "direction", "Café", "Naïve ≤ 5", "6d418c8f93")
FIRST_ITEMS = [ITEM_DESIGN, ITEM_ROADMAP, ITEM_DIRECTION]
FIRST_BODY = {"source": "commit-1", "items": FIRST_ITEMS}

# The reworded design item: new statement, the hash the rule works out
# from the item's own words.
REWORDED_DESIGN = _item(
    "SHOP-D-1",
    "design",
    "One database product",
    "Every service stores its data in PostgreSQL 15.",
    "40ff75ae8d",
)
REWORDED_BODY = {
    "source": "commit-2",
    "items": [REWORDED_DESIGN, ITEM_ROADMAP, ITEM_DIRECTION],
}


def _rows(table: str) -> list[dict]:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(f"SELECT * FROM {table} ORDER BY id")
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _run(raw: str) -> tuple[int, str, str]:
    """``main`` on text streams of its own; the (code, out, err) it leaves."""
    out = io.StringIO()
    err = io.StringIO()
    code = guidance_publish.main(stdin=io.StringIO(raw), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_issue1127_freeform(client, monkeypatch):
    # -- The three items' hashes are the ones the rule works out. ------
    assert (
        _hash(
            "design",
            "One database product",
            "Every service stores its data in PostgreSQL.",
        )
        == "caddbcd58d"
    )
    assert _hash("roadmap", "T", "S.") == "0282fa9675"
    assert _hash("direction", "Café", "Naïve ≤ 5") == "6d418c8f93"
    assert (
        _hash(
            "design",
            "One database product",
            "Every service stores its data in PostgreSQL 15.",
        )
        == "40ff75ae8d"
    )
    # Both tables hold no row at first.
    assert _rows(_PUBLICATIONS) == []
    assert _rows(_VERSIONS) == []

    # -- The first publication, through the command. -------------------
    code, out, err = _run(json.dumps(FIRST_BODY))
    assert code == 0
    lines = out.splitlines()
    assert len(lines) == 1
    answer = json.loads(lines[0])
    assert set(answer) == {
        "changed",
        "publication_id",
        "items",
        "new_versions",
        "withdrawn",
    }
    assert answer["changed"] is True
    assert answer["items"] == 3
    assert answer["new_versions"] == 3
    assert answer["withdrawn"] == 0
    assert isinstance(answer["publication_id"], int)
    assert err == ""
    publications = _rows(_PUBLICATIONS)
    assert len(publications) == 1
    assert publications[0]["id"] == answer["publication_id"]
    assert publications[0]["source"] == "commit-1"
    assert publications[0]["item_count"] == 3
    assert publications[0]["published_by"] == "deployment"
    versions = _rows(_VERSIONS)
    assert len(versions) == 3
    assert {row["key"] for row in versions} == {
        "SHOP-D-1",
        "SHOP-R-1",
        "SHOP-N-1",
    }
    for row in versions:
        assert row["first_publication_id"] == answer["publication_id"]
        assert row["last_publication_id"] == answer["publication_id"]
    # The stored text is exactly the string that was sent.
    direction = next(row for row in versions if row["key"] == "SHOP-N-1")
    assert direction["title"] == "Café"
    assert direction["statement"] == "Naïve ≤ 5"
    first_publication_id = answer["publication_id"]
    before_rerun = (publications, versions)

    # -- The same again changes nothing. --------------------------------
    code, out, err = _run(json.dumps(FIRST_BODY))
    assert code == 0
    answer = json.loads(out.splitlines()[0])
    assert answer["changed"] is False
    assert answer["publication_id"] == first_publication_id
    assert err == ""
    assert (
        _rows(_PUBLICATIONS),
        _rows(_VERSIONS),
    ) == before_rerun

    # -- The route sees what the command stored. ------------------------
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    response = admin.put(URL, json=FIRST_BODY)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is False
    assert body["publication_id"] == first_publication_id
    assert body["items"] == 3
    assert body["new_versions"] == 0
    assert body["withdrawn"] == 0

    # -- The command sees what the route stored. ------------------------
    response = admin.put(URL, json=REWORDED_BODY)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["new_versions"] == 1
    assert body["withdrawn"] == 0
    second_publication_id = body["publication_id"]
    assert second_publication_id > first_publication_id
    # The command, given that same body: nothing changes.
    code, out, err = _run(json.dumps(REWORDED_BODY))
    assert code == 0
    answer = json.loads(out.splitlines()[0])
    assert answer["changed"] is False
    assert answer["publication_id"] == second_publication_id
    assert err == ""
    # The command, given the first body again: one new version row.
    code, out, err = _run(json.dumps(FIRST_BODY))
    assert code == 0
    answer = json.loads(out.splitlines()[0])
    assert answer["changed"] is True
    assert answer["new_versions"] == 1
    assert answer["withdrawn"] == 0
    third_publication_id = answer["publication_id"]
    assert third_publication_id > second_publication_id
    publications = _rows(_PUBLICATIONS)
    assert len(publications) == 3
    third = next(row for row in publications if row["id"] == third_publication_id)
    assert third["published_by"] == "deployment"
    assert third["item_count"] == 3
    versions = _rows(_VERSIONS)
    assert len(versions) == 5
    design_rows = [row for row in versions if row["key"] == "SHOP-D-1"]
    assert len(design_rows) == 3
    # No row is ever deleted: the first publication's row is still the
    # first row, and the new row is the newest.
    first_design = min(design_rows, key=lambda row: row["id"])
    assert first_design["statement"] == "Every service stores its data in PostgreSQL."
    assert first_design["first_publication_id"] == first_publication_id
    assert first_design["last_publication_id"] == first_publication_id
    reworded_design = next(row for row in design_rows if row["hash"] == "40ff75ae8d")
    assert (
        reworded_design["statement"]
        == "Every service stores its data in PostgreSQL 15."
    )
    assert reworded_design["first_publication_id"] == second_publication_id
    # The reworded row is no longer current and is left as it is.
    assert reworded_design["last_publication_id"] == second_publication_id
    new_design = max(design_rows, key=lambda row: row["id"])
    assert new_design["statement"] == "Every service stores its data in PostgreSQL."
    assert new_design["first_publication_id"] == third_publication_id
    assert new_design["last_publication_id"] == third_publication_id

    # -- The refused: nothing on stdout, one ``refused:`` line on
    #    stderr, exit code 2, both tables unchanged. --------------------
    before_refusals = (_rows(_PUBLICATIONS), _rows(_VERSIONS))
    valid_raw = json.dumps(FIRST_BODY)
    broken_hash_items = [
        dict(ITEM_DESIGN),
        dict(ITEM_ROADMAP),
        dict(ITEM_DIRECTION, hash="0000000000"),
    ]
    refusals = [
        ("standard input not JSON", "{"),
        ("empty standard input", ""),
        ("a JSON list", json.dumps(FIRST_ITEMS)),
        (
            "the last item's hash 0000000000",
            json.dumps({"source": "commit-1", "items": broken_hash_items}),
        ),
        ("an empty items list", json.dumps({"source": "commit-1", "items": []})),
        ("an extra key whose value is NaN", valid_raw[:-1] + ', "extra": NaN}'),
    ]
    for label, raw in refusals:
        code, out, err = _run(raw)
        assert code == 2, f"{label}: code {code}, err {err!r}"
        assert out == "", f"{label}: {out!r}"
        assert err.startswith("refused:"), f"{label}: {err!r}"
        assert len(err.splitlines()) == 1, f"{label}: {err!r}"
    assert (
        _rows(_PUBLICATIONS),
        _rows(_VERSIONS),
    ) == before_refusals

    # -- The failed: the store replaced by one that raises. -------------
    before_failed = (_rows(_PUBLICATIONS), _rows(_VERSIONS))

    def _down(*args, **kwargs):
        raise RuntimeError("down")

    monkeypatch.setattr(guidance_publish._repo, "publish", _down)
    code, out, err = _run(valid_raw)
    assert code == 1
    assert out == ""
    assert err.startswith("failed:"), f"err {err!r}"
    assert not any(
        line.startswith("Traceback") for line in err.splitlines()
    ), f"err {err!r}"
    assert (
        _rows(_PUBLICATIONS),
        _rows(_VERSIONS),
    ) == before_failed

    # -- Running the module: the source ends by calling main under the
    #    ``__main__`` guard and exiting with its result. -----------------
    source = Path(guidance_publish.__file__).read_text(encoding="utf-8")
    assert source.rstrip().endswith('if __name__ == "__main__":\n    sys.exit(main())')
