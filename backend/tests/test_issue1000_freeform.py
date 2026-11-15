"""Issue #1000 proof: the recommendation carries the item-site's source system.

AC-1 — each row of ``GET /api/recommendations`` and the answer of
``GET /api/recommendations/by-key`` gains ``source_system``, the
item-site's stored ``item_sites.source_system`` (a string or null): it
is read from the item-site row the recommendation already reads, so
neither route issues a new SQL statement. ``GET /api/recommendations``
gains the optional ``source_system`` query parameter, at most 64
characters, applied the same way as the existing ``status`` filter and
in the same place — before ``limit`` and ``offset``: an unknown value
answers an empty list, not an error. ``RecommendationResponse`` gains
``source_system: str | None``, described in its docstring, and
``backend/openapi.json`` documents the field as a nullable string and
the parameter as optional, and nothing else.

AC-2 — with the item-sites seeded through the real repositories, the
way ``test_issue682_freeform.py`` seeds its data: ``ITEM-A`` at ``S1``
under ``acumatica``, ``ITEM-B`` at ``S2`` under ``odoo`` and ``ITEM-C``
at ``S3`` with a null ``source_system`` (360 days of 2.0 a day each;
``ITEM-A`` and ``ITEM-B`` with a usable pooled lead time, ``ITEM-C``
without one):

  * each list row reads its own source (``acumatica``, ``odoo``) and
    ``ITEM-C`` reads null; ``by-key`` for ``ITEM-B`` at ``S2`` reads
    ``odoo`` and equals the list's row for the pair;
  * ``source_system=odoo`` answers only ``ITEM-B``,
    ``source_system=acumatica`` only ``ITEM-A``, and
    ``source_system=sap`` an empty list with status 200;
  * a 65-character value answers 422 (and a NUL byte, an empty value,
    a control character and a huge value never answer 500);
  * the filter works together with ``status`` and with ``limit`` (it
    runs before ``offset``);
  * ``backend/openapi.json`` documents the field as a nullable string
    and the parameter as optional;
  * counted through the normal connection path (the way
    ``test_issue682_freeform.py`` counts), one call of each route runs
    the same number of SQL statements as before this slice: the list
    route exactly ``FIXED_STATEMENTS`` of
    ``test_issue682_freeform.py`` and the by-key route its own fixed
    budget of 17 statements — the field adds no statement to either.
"""

import json
from datetime import date, timedelta
from pathlib import Path

import psycopg2

from app.db.connection import get_connection
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from tests.test_issue682_freeform import FIXED_STATEMENTS

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)

ITEM_A, SITE_A, SOURCE_A = "ITEM-A", "S1", "acumatica"
ITEM_B, SITE_B, SOURCE_B = "ITEM-B", "S2", "odoo"
ITEM_C, SITE_C = "ITEM-C", "S3"

# The fixed statement budget of GET /api/recommendations/by-key for an
# explicit as_of, counted through the normal connection path: the
# route's one item_sites existence read, the call's one settings read,
# the population's demand statistics (2), lead-time statistics,
# item_sites, 30-day history and counted overrides, the pair's demand
# statistics (2), lead-time statistics, on_hand row and open purchase
# lines, and the pair's counted overrides, counted signals and 30-day
# history (2).  This slice adds no statement to either route, so one
# call of the route still runs exactly these.
BY_KEY_STATEMENTS = 17

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


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

    def counting_connect(*args, **kwargs):
        return _CountingConnection(real_connect(*args, **kwargs), counter, queries)

    psycopg2.connect = counting_connect
    try:
        result = fn()
    finally:
        psycopg2.connect = real_connect
    return result, counter[0], queries


def _demand(cur, item, site, first, days, quantity):
    """``days`` of ``quantity`` units a day for one item, from ``first``."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-1000-{item}-{i}",
                f"de-1000-{item}-{i}",
                item,
                site,
                quantity,
                (first + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(days)
        ],
    )


def _lead_times(cur, item):
    """Three lead-time observations of 8, 10 and 12 days: mean 10,
    sample std 2, not low confidence."""
    for i, (ordered, received) in enumerate(
        (
            ("2026-08-22", "2026-08-30"),
            ("2026-08-20", "2026-08-30"),
            ("2026-08-18", "2026-08-30"),
        )
    ):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                f"id-lt-1000-{item}-{i}",
                f"lt-1000-{item}-{i}",
                f"SUP-1000-{item}",
                item,
                ordered,
                received,
                "test",
            ),
        )


def _seed_item_sites() -> None:
    """The three item-sites through the real item-site repository."""
    ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": ITEM_A,
                "site_code": SITE_A,
                "unit_cost": 1.0,
                "reorder_point": 25.0,
                "safety_stock": 10.0,
                "source_system": SOURCE_A,
            },
            {
                "item_code": ITEM_B,
                "site_code": SITE_B,
                "unit_cost": 1.0,
                "reorder_point": 25.0,
                "safety_stock": 10.0,
                "source_system": SOURCE_B,
            },
            {
                "item_code": ITEM_C,
                "site_code": SITE_C,
                "unit_cost": 1.0,
                "reorder_point": 25.0,
                "safety_stock": 10.0,
                "source_system": None,
            },
        ]
    )


def _list(client, **params) -> list[dict]:
    response = client.get("/api/recommendations", params={"as_of": AS_OF_STR, **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_issue1000_freeform(client, monkeypatch):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # --- seed the three item-sites the way test_issue682 does ---------
    # ITEM-A at S1 under acumatica, ITEM-B at S2 under odoo, ITEM-C at
    # S3 with a null source_system: 360 days of 2.0 a day each, ITEM-A
    # and ITEM-B with a usable pooled lead time (mean 10 days) and
    # ITEM-C without one (status insufficient_lead_time).
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand(cur, ITEM_A, SITE_A, FIRST_DAY, 360, 2.0)
            _lead_times(cur, ITEM_A)
            _demand(cur, ITEM_B, SITE_B, FIRST_DAY, 360, 2.0)
            _lead_times(cur, ITEM_B)
            _demand(cur, ITEM_C, SITE_C, FIRST_DAY, 360, 2.0)
        conn.commit()
    finally:
        conn.close()
    _seed_item_sites()

    # --- AC-1: the field on every list row, null for ITEM-C ------------
    rows = _list(client)
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (ITEM_A, SITE_A),
        (ITEM_B, SITE_B),
        (ITEM_C, SITE_C),
    ]
    api_rows = {(row["item_code"], row["site_code"]): row for row in rows}
    assert api_rows[(ITEM_A, SITE_A)]["source_system"] == SOURCE_A
    assert api_rows[(ITEM_B, SITE_B)]["source_system"] == SOURCE_B
    assert api_rows[(ITEM_C, SITE_C)]["source_system"] is None
    # ITEM-C's row is otherwise a normal (not ok) recommendation.
    assert api_rows[(ITEM_C, SITE_C)]["status"] == "insufficient_lead_time"
    assert api_rows[(ITEM_A, SITE_A)]["status"] == "ok"
    assert api_rows[(ITEM_B, SITE_B)]["status"] == "ok"

    # The by-key answer for ITEM-B at S2 reads odoo and equals the
    # list's row for the pair.
    by_key = client.get(
        "/api/recommendations/by-key",
        params={"item_code": ITEM_B, "site_code": SITE_B, "as_of": AS_OF_STR},
    )
    assert by_key.status_code == 200, by_key.text
    assert by_key.json()["source_system"] == SOURCE_B
    assert by_key.json() == api_rows[(ITEM_B, SITE_B)]

    # --- AC-1: the source_system filter --------------------------------
    # Each stored source answers exactly its own item-site.
    assert [
        (row["item_code"], row["site_code"])
        for row in _list(client, source_system=SOURCE_B)
    ] == [(ITEM_B, SITE_B)]
    assert [
        (row["item_code"], row["site_code"])
        for row in _list(client, source_system=SOURCE_A)
    ] == [(ITEM_A, SITE_A)]
    # An unknown value answers an empty list, not an error.
    unknown = client.get(
        "/api/recommendations",
        params={"as_of": AS_OF_STR, "source_system": "sap"},
    )
    assert unknown.status_code == 200
    assert unknown.json() == []

    # At most 64 characters: 65 answers 422, like the status filter.
    too_long = client.get(
        "/api/recommendations",
        params={"as_of": AS_OF_STR, "source_system": "a" * 65},
    )
    assert too_long.status_code == 422
    # Hostile values never answer 500: a NUL byte is refused by the
    # filter guard, an empty value and a control character match
    # nothing and a huge value is too long.
    nul = client.get(
        "/api/recommendations",
        params={"as_of": AS_OF_STR, "source_system": "odoo\x00"},
    )
    assert nul.status_code == 422
    assert nul.json()["detail"] == "recommendation filters must not contain NUL bytes"
    control = client.get(
        "/api/recommendations",
        params={"as_of": AS_OF_STR, "source_system": "odoo\x01"},
    )
    assert control.status_code == 200
    assert control.json() == []
    empty = client.get(
        "/api/recommendations",
        params={"as_of": AS_OF_STR, "source_system": ""},
    )
    assert empty.status_code == 200
    assert empty.json() == []
    huge = client.get(
        "/api/recommendations",
        params={"as_of": AS_OF_STR, "source_system": "a" * 10_000},
    )
    assert huge.status_code == 422

    # The filter composes with status: the intersection, applied the
    # same way as the status filter.
    assert [
        (row["item_code"], row["site_code"])
        for row in _list(client, source_system=SOURCE_B, status="ok")
    ] == [(ITEM_B, SITE_B)]
    assert _list(client, source_system=SOURCE_A, status="insufficient_lead_time") == []
    # ...and the status filter alone still reaches the null-source row.
    by_status = _list(client, status="insufficient_lead_time")
    assert [(row["item_code"], row["site_code"]) for row in by_status] == [
        (ITEM_C, SITE_C),
    ]
    assert by_status[0]["source_system"] is None

    # The filter composes with limit and offset: it runs on the whole
    # list before limit and offset, the same place as status.
    assert [(row["item_code"], row["site_code"]) for row in _list(client, limit=1)] == [
        (ITEM_A, SITE_A)
    ]
    assert [
        (row["item_code"], row["site_code"])
        for row in _list(client, source_system=SOURCE_B, limit=1)
    ] == [(ITEM_B, SITE_B)]
    assert _list(client, source_system=SOURCE_A, offset=1) == []

    # --- AC-2: the schema documents the field and the parameter --------
    openapi = json.loads(OPENAPI.read_text(encoding="utf-8"))
    field = openapi["components"]["schemas"]["RecommendationResponse"]["properties"][
        "source_system"
    ]
    assert field == {
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "title": "Source System",
    }
    parameters = openapi["paths"]["/api/recommendations"]["get"]["parameters"]
    parameter = next(p for p in parameters if p["name"] == "source_system")
    assert parameter["in"] == "query"
    assert parameter["required"] is False
    assert parameter["schema"] == {
        "anyOf": [{"type": "string", "maxLength": 64}, {"type": "null"}],
        "title": "Source System",
    }

    # --- AC-2: no new statement in either route -------------------------
    # Counted through the normal connection path, the way
    # test_issue682_freeform.py counts: one call of the list route runs
    # exactly the fixed budget that test pins, and one call of the
    # by-key route its own fixed budget — the field adds no statement.
    response, count_list, _ = _measure(
        lambda: client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    )
    assert response.status_code == 200
    assert count_list == FIXED_STATEMENTS
    response, count_by_key, _ = _measure(
        lambda: client.get(
            "/api/recommendations/by-key",
            params={"item_code": ITEM_B, "site_code": SITE_B, "as_of": AS_OF_STR},
        )
    )
    assert response.status_code == 200
    assert count_by_key == BY_KEY_STATEMENTS
