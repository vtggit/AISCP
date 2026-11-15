"""Issue #1002 proof: GET /api/erp-sources reads the item-sites' sources.

Everything is exercised against the test database through the app's
test client: the item-sites are seeded through
``ItemSitePostgresRepository.upsert_many`` — two under ``acumatica``,
one under ``odoo`` and one with a null ``source_system`` — and the
whole proof runs as a NON-ADMIN (role ``user``, asserted via
``GET /api/auth/me``).

Proved here:

  * empty — with no item-sites the answer is ``{"sources": [],
    "item_sites": []}`` with status 200;
  * sources — ``sources`` reads ``acumatica`` with 2, then ``odoo``
    with 1, then null with 1 (count descending, then source_system
    with null last), each with its newest ``synced_at``;
  * item-sites — ``item_sites`` holds all four rows in
    (item_code, site_code) order, each with its source system;
  * who may read — a non-admin signed-in user gets 200 and no token
    gets 401 (the router is mounted behind the app's authentication
    gate the same way as the erp-mappings router);
  * one statement — one call of the route runs one SQL statement for
    the read, counted through the normal connection path (the
    production code does not swap, patch or rebind
    ``psycopg2.connect``);
  * the schema — ``backend/openapi.json`` lists the route with its
    response models.
"""

import inspect
import json
import time
from pathlib import Path

import psycopg2
from fastapi.testclient import TestClient

import app.db.connection as connection_module
from app.db.connection import get_cursor
from app.main import app
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

URL = "/api/erp-sources"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

# The seed: two item-sites under acumatica, one under odoo and one
# with a null source_system (item codes chosen so the item_code order
# is the seed order).
ACU_ROW_1 = {
    "item_code": "ITEM-1002-A",
    "site_code": "SITE-1002-1",
    "source_system": "acumatica",
}
ACU_ROW_2 = {
    "item_code": "ITEM-1002-B",
    "site_code": "SITE-1002-2",
    "source_system": "acumatica",
}
ODOO_ROW = {
    "item_code": "ITEM-1002-C",
    "site_code": "SITE-1002-3",
    "source_system": "odoo",
}
NULL_ROW = {
    "item_code": "ITEM-1002-D",
    "site_code": "SITE-1002-4",
    "source_system": None,
}


def _stored_rows() -> list[dict]:
    """The item_sites rows as (item_code, site_code, source_system,
    synced_at), ordered by (item_code, site_code)."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT item_code, site_code, source_system, synced_at "
            "FROM item_sites ORDER BY item_code, site_code"
        )
        return [dict(row) for row in cur.fetchall()]


# ----------------------------------------------------------------------
# Counting the SQL statements of a call, through the normal
# connection path (the same wrapper the #980 proof uses).
# ----------------------------------------------------------------------


class _CountingCursor:
    """A cursor that counts its execute calls, delegating the rest."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def execute(self, query, *args, **kwargs):
        self._counter[0] += 1
        self._queries.append(query)
        return self._real.execute(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._real.close()
        return False


class _CountingConnection:
    """A connection whose cursors count their execute calls."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def cursor(self, *args, **kwargs):
        return _CountingCursor(
            self._real.cursor(*args, **kwargs), self._counter, self._queries
        )

    def __getattr__(self, name):
        return getattr(self._real, name)


def _measure(fn):
    """Run fn with psycopg2.connect wrapped; (result, statements, SQL)."""
    counter = [0]
    queries: list[str] = []
    real_connect = psycopg2.connect

    def counting_connect(**params):
        return _CountingConnection(real_connect(**params), counter, queries)

    psycopg2.connect = counting_connect
    try:
        result = fn()
    finally:
        psycopg2.connect = real_connect
    return result, counter[0], queries


def test_issue1002_freeform(client, user_headers):
    repo = ItemSitePostgresRepository()

    # ------------------------------------------------------------------
    # Empty: with no item-sites, both lists are empty, status 200.
    # ------------------------------------------------------------------
    with get_cursor() as cur:
        cur.execute("DELETE FROM item_sites")
    viewer = TestClient(app, headers=user_headers)
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == {"sources": [], "item_sites": []}

    # ------------------------------------------------------------------
    # The seed, through upsert_many: two under acumatica, one under
    # odoo and one with a null source_system.  A second, later upsert
    # of one acumatica row gives that source a strictly newer
    # synced_at than the rest of the first write.
    # ------------------------------------------------------------------
    result = repo.upsert_many([ACU_ROW_1, ACU_ROW_2, ODOO_ROW, NULL_ROW])
    assert result == {"inserted": 4, "updated": 0}, result
    time.sleep(0.05)
    result = repo.upsert_many([ACU_ROW_1])
    assert result == {"inserted": 0, "updated": 1}, result

    rows = _stored_rows()
    assert len(rows) == 4, rows
    stored = {(row["item_code"], row["site_code"]): row for row in rows}
    acu_latest = max(
        stored[("ITEM-1002-A", "SITE-1002-1")]["synced_at"],
        stored[("ITEM-1002-B", "SITE-1002-2")]["synced_at"],
    )
    # The second upsert really is newer, so "newest" is not a tie.
    assert acu_latest > stored[("ITEM-1002-B", "SITE-1002-2")]["synced_at"]
    assert acu_latest > stored[("ITEM-1002-C", "SITE-1002-3")]["synced_at"]
    assert acu_latest > stored[("ITEM-1002-D", "SITE-1002-4")]["synced_at"]

    # ------------------------------------------------------------------
    # Who may read: a non-admin signed-in user gets 200 (the whole
    # proof below runs as the viewer).
    # ------------------------------------------------------------------
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()

    # ------------------------------------------------------------------
    # Sources: acumatica with 2, then odoo with 1, then null with 1 —
    # count descending, then source_system with null last — each with
    # its newest synced_at.
    # ------------------------------------------------------------------
    assert body["sources"] == [
        {
            "source_system": "acumatica",
            "item_sites": 2,
            "last_synced_at": acu_latest.isoformat(),
        },
        {
            "source_system": "odoo",
            "item_sites": 1,
            "last_synced_at": stored[("ITEM-1002-C", "SITE-1002-3")][
                "synced_at"
            ].isoformat(),
        },
        {
            "source_system": None,
            "item_sites": 1,
            "last_synced_at": stored[("ITEM-1002-D", "SITE-1002-4")][
                "synced_at"
            ].isoformat(),
        },
    ]

    # ------------------------------------------------------------------
    # Item-sites: all four rows in (item_code, site_code) order, each
    # with its source system.
    # ------------------------------------------------------------------
    assert body["item_sites"] == [
        {
            "item_code": "ITEM-1002-A",
            "site_code": "SITE-1002-1",
            "source_system": "acumatica",
        },
        {
            "item_code": "ITEM-1002-B",
            "site_code": "SITE-1002-2",
            "source_system": "acumatica",
        },
        {
            "item_code": "ITEM-1002-C",
            "site_code": "SITE-1002-3",
            "source_system": "odoo",
        },
        {
            "item_code": "ITEM-1002-D",
            "site_code": "SITE-1002-4",
            "source_system": None,
        },
    ]

    # ------------------------------------------------------------------
    # No token at all gets 401 (the app's auth gate, as on
    # erp-mappings).
    # ------------------------------------------------------------------
    anonymous = TestClient(app)
    response = anonymous.get(URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"

    # ------------------------------------------------------------------
    # One statement: one call of the route runs one SQL statement for
    # the read, counted through the normal connection path; the
    # production code does not swap, patch or rebind psycopg2.connect.
    # ------------------------------------------------------------------
    response, statements, queries = _measure(lambda: viewer.get(URL))
    assert response.status_code == 200, response.text
    assert statements == 1, (statements, queries)
    assert response.json() == body
    connection_source = inspect.getsource(connection_module)
    assert "psycopg2.connect" in connection_source
    assert "psycopg2.connect =" not in connection_source

    # ------------------------------------------------------------------
    # The schema: backend/openapi.json lists the route with its
    # response models.
    # ------------------------------------------------------------------
    openapi = json.loads(OPENAPI.read_text(encoding="utf-8"))
    get_spec = openapi["paths"]["/api/erp-sources"]["get"]
    assert get_spec["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErpSourcesResponse"
    }
    schemas = openapi["components"]["schemas"]
    assert set(schemas["ErpSourcesResponse"]["properties"]) == {
        "sources",
        "item_sites",
    }
    assert (
        schemas["ErpSourcesResponse"]["properties"]["sources"]["items"]["$ref"]
        == "#/components/schemas/ErpSource"
    )
    assert (
        schemas["ErpSourcesResponse"]["properties"]["item_sites"]["items"]["$ref"]
        == "#/components/schemas/ErpSourceItemSite"
    )
    assert set(schemas["ErpSource"]["properties"]) == {
        "source_system",
        "item_sites",
        "last_synced_at",
    }
    assert schemas["ErpSource"]["required"] == [
        "source_system",
        "item_sites",
        "last_synced_at",
    ]
    assert set(schemas["ErpSourceItemSite"]["properties"]) == {
        "item_code",
        "site_code",
        "source_system",
    }
    assert schemas["ErpSourceItemSite"]["required"] == [
        "item_code",
        "site_code",
        "source_system",
    ]

    # ------------------------------------------------------------------
    # Identity, second pass after a full reload: the page boots the
    # production way (the token it would take from the URL hash as
    # #access_token=...), /api/auth/config answers publicly and
    # /api/auth/me resolves the viewer (role "user", no admin); the
    # read is not an admin-only control — it answers 200 for the
    # viewer and the page still renders, while no token gets 401.
    # ------------------------------------------------------------------
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
    response = viewer_reloaded.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == body
    assert TestClient(app).get(URL).status_code == 401
