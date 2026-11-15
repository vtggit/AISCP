"""Issue #657 proof: the ABC classification of the item-sites.

AC-1 — ``app.recommend.abc.classify`` is a pure function over
``{item_code, site_code, annual_value}`` dicts with the share
constants ``A_SHARE = 0.80`` and ``B_SHARE = 0.95``: the valued
item-sites are ranked by ``annual_value`` descending (ties by
``item_code``, then ``site_code``) and cut at the cumulative share
*before* each item — below ``A_SHARE`` an ``"A"``, below ``B_SHARE`` a
``"B"``, else a ``"C"``.  On five items with annual values 500, 300,
100, 60 and 40 (total 1000) the ``share_before`` values are exactly
0.0, 0.5, 0.8, 0.9 and 0.96 and the classes exactly A, A, B, B, C; an
item with annual value 0 and one with null are unclassified — listed
after the classified ones ordered by ``item_code`` then ``site_code``
— with ``rank``, ``share_before`` and ``abc_class`` null and
``annual_value`` null.  Every row carries exactly the six keys
``item_code``, ``site_code``, ``annual_value``, ``rank``,
``share_before`` and ``abc_class``.  ``app.recommend.abc.abc_for_all``
builds the items from ONE ``recommendations_for_all(as_of)`` call,
pricing an ``"ok"`` recommendation at ``365 * inputs["d"] *
unit_cost`` when both are positive and leaving every other
recommendation unclassified.

``GET /api/abc`` (``app.api.abc``, mounted in ``app.main`` behind the
same auth gate as the other routes) returns
``{"as_of", "total_annual_value", "counts", "rows"}`` with the rows as
``AbcRow`` and the counts as an ``AbcCounts`` object with integer
fields ``A``, ``B``, ``C`` and ``unclassified`` (AC-4).  Through the
test client, with the planner token and ``recommendations_for_all``
monkeypatched (as test_issue453_freeform.py monkeypatches the ranking
inputs) to three "ok" recommendations and one not ok, it returns the
expected classes, counts and total, and ``recommendations_for_all``
is called exactly once, with the supplied ``as_of`` passed through.

AC-3 — ``as_of`` is never null: the supplied query value when present,
else the shared default as-of date (the earlier of today and the
latest synced data date, per ``app.stats.demand.default_as_of``) when
omitted — proven against the test database with one demand event
(occurred_on 2026-09-30) and a frozen "today" (2026-10-02 gives the
data date, 2026-09-01 gives today), including when no rows are
returned.  An anonymous call gets 401, a malformed ``as_of`` gets 422
(not 500), and ``backend/openapi.json`` carries the route with its
optional ``as_of`` query parameter and its three models.

Identity — the proof also runs as a NON-ADMIN (role ``user``) in a
second pass after a full reload: the page boots the production way
(the token it would take from the URL hash as ``#access_token=...`` —
the bare development token — with ``/api/auth/config`` answering
publicly and ``/api/auth/me`` resolving the viewer for that user,
dev-mode auth standing in for the IdP) — the route carries no
admin-only control, so the non-admin viewer reads the ABC table and
the page still renders, while unauthenticated /api traffic is still
401.
"""

import inspect
import json
import math
import os
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.abc as abc_api
import app.recommend.abc as abc_module
import app.stats.demand as demand_module
from app.api.abc import AbcCounts, AbcRow
from app.db.connection import get_connection
from app.main import app
from app.recommend.abc import A_SHARE, B_SHARE, abc_for_all, classify

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

AS_OF = "2026-10-25"
AS_OF_DATE = date(2026, 10, 25)
SITE = "SITE-657"
DEMAND_DATE = date(2026, 9, 30)  # the latest synced data date
TODAY_AFTER = date(2026, 10, 2)  # today later than the data date
TODAY_BEFORE = date(2026, 9, 1)  # today earlier than the data date

ROW_KEYS = {
    "item_code",
    "site_code",
    "annual_value",
    "rank",
    "share_before",
    "abc_class",
}


def _ok(item_code: str, d: float, unit_cost: float) -> dict:
    """One "ok" recommendation shaped like the live result."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "as_of": AS_OF,
        "status": "ok",
        "demand_class": "smooth",
        "safety_stock": 0.0,
        "reorder_point": d * 10.0,
        "safety_stock_units": 0,
        "reorder_point_units": int(d * 10.0),
        "inputs": {"d": d, "sd_d": 0.0, "lt": 10.0, "sd_lt": 0.0},
        "arithmetic": "reorder_point = lt * d + z * sigma_l",
        "demand_basis": "history",
        "other_basis": None,
        "unit_cost": unit_cost,
    }


class _FrozenDate(date):
    """A date whose ``today()`` is the proof's frozen clock."""

    _today: date = TODAY_AFTER

    @classmethod
    def today(cls) -> date:
        return cls._today


def _freeze_today(monkeypatch, day: date) -> None:
    """Freeze ``date.today`` for ``app.stats.demand`` (the shared
    default as-of date's notion of today)."""
    _FrozenDate._today = day
    monkeypatch.setattr(demand_module, "date", _FrozenDate)


def test_issue657_freeform(monkeypatch, client):
    # --- AC-1: classify on five items (values 500, 300, 100, 60, 40,
    # total 1000) plus a zero and a null — the input order is shuffled
    # on purpose; the output is in rank order.
    assert A_SHARE == 0.80
    assert B_SHARE == 0.95
    items = [
        {"item_code": "I040", "site_code": SITE, "annual_value": 40},
        {"item_code": "I100", "site_code": SITE, "annual_value": 100},
        {"item_code": "INULL", "site_code": SITE, "annual_value": None},
        {"item_code": "I500", "site_code": SITE, "annual_value": 500},
        {"item_code": "I000", "site_code": SITE, "annual_value": 0},
        {"item_code": "I060", "site_code": SITE, "annual_value": 60},
        {"item_code": "I300", "site_code": SITE, "annual_value": 300},
    ]
    rows = classify(items)
    assert [(r["rank"], r["share_before"], r["abc_class"]) for r in rows] == [
        (1, 0.0, "A"),
        (2, 0.5, "A"),
        (3, 0.8, "B"),
        (4, 0.9, "B"),
        (5, 0.96, "C"),
        # the zero and the null item: unclassified, no rank/share/class
        (None, None, None),
        (None, None, None),
    ]
    assert [r["item_code"] for r in rows] == [
        "I500",
        "I300",
        "I100",
        "I060",
        "I040",
        "I000",
        "INULL",
    ]
    assert [r["annual_value"] for r in rows] == [
        500.0,
        300.0,
        100.0,
        60.0,
        40.0,
        None,
        None,
    ]
    for row in rows:
        assert set(row.keys()) == ROW_KEYS
    # The zero and the null are unclassified, listed after the
    # classified ones, with rank (and share and class) null.
    for row in rows[5:]:
        assert row["rank"] is None
        assert row["share_before"] is None
        assert row["abc_class"] is None
        assert row["annual_value"] is None
    # Hostile values never raise: they are simply unclassified.
    hostile = classify(
        [
            {"item_code": "H1", "site_code": SITE, "annual_value": float("nan")},
            {"item_code": "H2", "site_code": SITE, "annual_value": float("inf")},
            {"item_code": "H3", "site_code": SITE, "annual_value": -5},
            {"item_code": "H4", "site_code": SITE, "annual_value": True},
        ]
    )
    assert all(r["abc_class"] is None and r["annual_value"] is None for r in hostile)
    assert [r["item_code"] for r in hostile] == ["H1", "H2", "H3", "H4"]
    assert not math.isnan(sum(r["share_before"] or 0.0 for r in hostile))

    # Ties are ranked by item_code, then site_code.
    tied = classify(
        [
            {"item_code": "B", "site_code": "S2", "annual_value": 100},
            {"item_code": "C", "site_code": "S1", "annual_value": 200},
            {"item_code": "A", "site_code": "S9", "annual_value": 100},
            {"item_code": "B", "site_code": "S1", "annual_value": 100},
        ]
    )
    assert [(r["item_code"], r["site_code"], r["rank"]) for r in tied] == [
        ("C", "S1", 1),
        ("A", "S9", 2),
        ("B", "S1", 3),
        ("B", "S2", 4),
    ]

    # --- AC-1: the route, with the planner token and
    # recommendations_for_all monkeypatched to three "ok"
    # recommendations and one not ok.
    calls: list[date | None] = []

    def fake_recommendations_for_all(as_of=None):
        calls.append(as_of)
        return [
            _ok("I657-1", 10.0, 2.0),  # 365 * 10 * 2 = 7300
            _ok("I657-2", 5.0, 2.0),  # 365 * 5 * 2 = 3650
            _ok("I657-3", 1.0, 1.0),  # 365 * 1 * 1 = 365
            {  # not ok: unclassified even though it carries a unit cost
                "item_code": "I657-4",
                "site_code": SITE,
                "as_of": AS_OF,
                "status": "not_recommendable_demand",
                "demand_class": "intermittent",
                "safety_stock": None,
                "reorder_point": None,
                "safety_stock_units": None,
                "reorder_point_units": None,
                "arithmetic": "",
                "demand_basis": "history",
                "other_basis": None,
                "unit_cost": 3.0,
            },
        ]

    monkeypatch.setattr(
        abc_module, "recommendations_for_all", fake_recommendations_for_all
    )

    total = 7300.0 + 3650.0 + 365.0  # 11315.0
    response = client.get("/api/abc", params={"as_of": AS_OF})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == AS_OF
    assert body["total_annual_value"] == total
    assert body["counts"] == {"A": 2, "B": 0, "C": 1, "unclassified": 1}
    assert [(r["item_code"], r["rank"], r["abc_class"]) for r in body["rows"]] == [
        ("I657-1", 1, "A"),
        ("I657-2", 2, "A"),
        ("I657-3", 3, "C"),
        ("I657-4", None, None),
    ]
    assert [r["annual_value"] for r in body["rows"]] == [7300.0, 3650.0, 365.0, None]
    assert body["rows"][0]["share_before"] == 0.0
    assert body["rows"][1]["share_before"] == 7300.0 / total
    assert body["rows"][2]["share_before"] == (7300.0 + 3650.0) / total
    assert body["rows"][3] == {
        "item_code": "I657-4",
        "site_code": SITE,
        "annual_value": None,
        "rank": None,
        "share_before": None,
        "abc_class": None,
    }
    for row in body["rows"]:
        assert set(row.keys()) == ROW_KEYS
    # recommendations_for_all was called exactly once, and the supplied
    # as_of was passed through to it.
    assert calls == [AS_OF_DATE]

    # --- AC-4: AbcCounts has exactly the integer fields A, B, C and
    # unclassified, and the body carries true ints.
    assert set(AbcCounts.model_fields) == {"A", "B", "C", "unclassified"}
    for field in AbcCounts.model_fields.values():
        assert field.annotation is int
    assert AbcCounts(A=1, B=2, C=3, unclassified=4).model_dump() == {
        "A": 1,
        "B": 2,
        "C": 3,
        "unclassified": 4,
    }
    for name in ("A", "B", "C", "unclassified"):
        assert type(body["counts"][name]) is int
    assert set(AbcRow.model_fields) == ROW_KEYS

    # --- AC-3: as_of is never null.  Seed one demand event (the latest
    # synced data date) and freeze today on both sides of it.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO demand_events (id, business_key, item_code, "
                "site_code, quantity, occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    "id-de-657-1",
                    "de-657-1",
                    "I657-1",
                    SITE,
                    10.0,
                    DEMAND_DATE.isoformat(),
                    "shipment",
                    "test",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    calls.clear()
    _freeze_today(monkeypatch, TODAY_AFTER)
    response = client.get("/api/abc")
    assert response.status_code == 200, response.text
    # Today (2026-10-02) is later than the data date: the default is
    # the data date, non-nullable, passed through to
    # recommendations_for_all.
    assert response.json()["as_of"] == DEMAND_DATE.isoformat()
    assert calls == [DEMAND_DATE]

    calls.clear()
    _freeze_today(monkeypatch, TODAY_BEFORE)
    response = client.get("/api/abc")
    assert response.status_code == 200, response.text
    # Today (2026-09-01) is earlier than the data date: the default is
    # today.
    assert response.json()["as_of"] == TODAY_BEFORE.isoformat()
    assert calls == [TODAY_BEFORE]

    # ...including when no rows are returned.
    _freeze_today(monkeypatch, TODAY_AFTER)

    def empty_recommendations_for_all(as_of=None):
        calls.append(as_of)
        return []

    monkeypatch.setattr(
        abc_module, "recommendations_for_all", empty_recommendations_for_all
    )
    response = client.get("/api/abc")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == DEMAND_DATE.isoformat()
    assert body["rows"] == []
    assert body["total_annual_value"] == 0.0
    assert body["counts"] == {"A": 0, "B": 0, "C": 0, "unclassified": 0}

    # abc_for_all itself: one recommendations_for_all call per call.
    calls.clear()
    monkeypatch.setattr(
        abc_module, "recommendations_for_all", fake_recommendations_for_all
    )
    result = abc_for_all(AS_OF_DATE)
    assert calls == [AS_OF_DATE]
    assert [(r["item_code"], r["abc_class"]) for r in result] == [
        ("I657-1", "A"),
        ("I657-2", "A"),
        ("I657-3", "C"),
        ("I657-4", None),
    ]

    # --- Hostile as_of values answer 422, never 500.
    for bad in ("not-a-date", "2026-10-25\x00", ""):
        response = client.get("/api/abc", params={"as_of": bad})
        assert response.status_code == 422, (bad, response.text)

    # --- AC-1: auth — an anonymous call gets 401.
    anon = TestClient(app)
    response = anon.get("/api/abc")
    assert response.status_code == 401

    # --- AC-1: openapi.json carries the route, its optional as_of
    # query parameter and its models.
    spec = json.loads(OPENAPI.read_text())
    operation = spec["paths"]["/api/abc"]["get"]
    parameters = {p["name"]: p for p in operation["parameters"]}
    assert parameters["as_of"]["in"] == "query"
    assert parameters["as_of"]["required"] is False
    schema_ref = operation["responses"]["200"]["content"]["application/json"]["schema"][
        "$ref"
    ]
    assert schema_ref.endswith("#/components/schemas/AbcResponse")
    schemas = spec["components"]["schemas"]
    assert set(schemas["AbcRow"]["properties"]) == ROW_KEYS
    assert set(schemas["AbcCounts"]["properties"]) == {"A", "B", "C", "unclassified"}
    for name in ("A", "B", "C", "unclassified"):
        assert schemas["AbcCounts"]["properties"][name]["type"] == "integer"

    # --- Identity: the proof also runs as a NON-ADMIN (role user).
    # Second pass after a full reload: the page boots the production
    # way — the token it would take from the URL hash as
    # #access_token=... (the bare development token), with
    # /api/auth/config answering publicly and /api/auth/me resolving
    # the non-admin viewer for that user.
    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    viewer = TestClient(app, headers={"Authorization": f"Bearer {dev_token}"})
    assert anon.get("/api/auth/config").status_code == 200  # public, pre-token
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == "dev-user-1"
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin-only control is absent: the route module references no
    # admin dependency and the route stays in the schema, so the
    # non-admin viewer reads the ABC table and the page still renders.
    assert "require_admin" not in inspect.getsource(abc_api)
    assert "/api/abc" in app.openapi()["paths"]
    response = viewer.get("/api/abc", params={"as_of": AS_OF})
    assert response.status_code == 200, response.text
    assert response.json()["counts"] == {"A": 2, "B": 0, "C": 1, "unclassified": 1}
    # The gate is intact: unauthenticated /api traffic is still 401.
    assert anon.get("/api/abc").status_code == 401
