"""Issue #853 proof: the transfer suggestions —
GET /api/supply-plan/transfers.

AC-1 — the route: the route module's sources are replaced the way the
issue 830 proof replaces its sources — ``recommendations_for_all`` with
a fake answering the rows of five items (item A: site 159 with reorder
point 400 and position 280 — a need of 120 — and site 154 with reorder
point 300, order quantity 100 and position 700 — a surplus of 300;
item B: a need of 120 against a surplus of 50 on the same source
system; item C: a need and a surplus on DIFFERENT source systems; item
D: a donor at exactly reorder point plus order quantity — no surplus —
against a need of 120; item E: a need and a surplus whose pairs have
no item_sites row) plus the rows the route must skip — and the
item_sites read with a counting wrapper over the real repository (each
pair's ``source_system``), the resolved as-of pinned.  Proved here:
item A yields exactly one suggestion — 154 → 159, quantity 120, its
arithmetic line the one line of text naming both numbers; the partial
cover of item B suggests 50 against a need of 120; item D's donor at
exactly reorder point plus order quantity gives nothing; item C's
different source systems never match; item E's unlisted pairs never
match; the two items order by value descending (nulls last, then
item_code); each source was read exactly once per call; no token
answers 401; the ERP client classes are patched to fail if
constructed; the schema file lists the route and its models.

Identity — the proof also runs as a NON-ADMIN (role "user"): a second
pass in the same test after a full reload, with the page booted the
production way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent as
its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer — sees the transfers page
still render (200 with the same body, the route open to any signed-in
user, so no admin-only control stands on it) while no token is still a
401.
"""

import json
import os
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.supply_plan as supply_plan
import app.feed.acumatica_client as acumatica_client_module
import app.feed.odoo_client as odoo_client_module
from app.main import app
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

URL = "/api/supply-plan/transfers"
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

# The resolved as-of the route reports (never null).
AS_OF = date(2026, 10, 31)


def _rec(
    item_code,
    site_code,
    *,
    status="ok",
    rop,
    on_hand,
    on_order,
    oq_units,
    unit_cost,
):
    """One row of the fake ``recommendations_for_all`` in the shape the
    real one answers: the position values, the reorder point in whole
    units and the order-quantity block (None when the item-site has no
    numeric recommended order quantity)."""
    return {
        "item_code": item_code,
        "site_code": site_code,
        "as_of": AS_OF,
        "status": status,
        "reorder_point": float(rop),
        "reorder_point_units": rop,
        "safety_stock_units": 50,
        "inputs": {
            "d": 10.0,
            "sd_d": 2.0,
            "lt": 7.0,
            "sd_lt": 1.0,
            "z": 1.6449,
            "service_level": 0.95,
        },
        "on_hand_units": on_hand,
        "on_order_units": on_order,
        "unit_cost": unit_cost,
        "suggested_order_quantity": 0,
        "order_quantity": (
            {
                "basis": "eoq",
                "clamped": None,
                "order_quantity": float(oq_units),
                "order_quantity_units": oq_units,
                "cover_days": 7.0,
                "annual_demand": 3650.0,
                "order_cost": 50.0,
                "holding_rate": 0.25,
                "unit_cost": unit_cost,
                "arithmetic": f"EOQ {oq_units}",
                "recommended_max_units": None,
                "current_order_qty": None,
                "current_max_qty": None,
            }
            if oq_units is not None
            else None
        ),
    }


class FakeRecommendations:
    """The fake for the route module's ``recommendations_for_all``: the
    rows of the five items plus the rows the route must skip — a
    non-"ok" row, an "ok" row with no numeric recommended order
    quantity, and "ok" rows whose position the route cannot hold
    (a non-numeric on hand, an order-quantity block that is not a
    mapping): stored data the route cannot hold is skipped, never a
    500."""

    calls = 0

    def __call__(self, as_of=None):
        FakeRecommendations.calls += 1
        return [
            # Item A: site 159 needs 120 (ROP 400, position 280);
            # site 154 holds 300 beyond ROP + order quantity (ROP 300,
            # order quantity 100, position 700).
            _rec(
                "ITEM-853-A",
                "159",
                rop=400,
                on_hand=200,
                on_order=80,
                oq_units=100,
                unit_cost=2.5,
            ),
            _rec(
                "ITEM-853-A",
                "154",
                rop=300,
                on_hand=700,
                on_order=0,
                oq_units=100,
                unit_cost=2.5,
            ),
            # Item B: a need of 120 against a surplus of 50 — the
            # partial cover: the suggestion is 50, not 120.
            _rec(
                "ITEM-853-B",
                "201",
                rop=400,
                on_hand=280,
                on_order=0,
                oq_units=100,
                unit_cost=10.0,
            ),
            _rec(
                "ITEM-853-B",
                "202",
                rop=300,
                on_hand=450,
                on_order=0,
                oq_units=100,
                unit_cost=10.0,
            ),
            # Item C: a need and a surplus on DIFFERENT source systems
            # (acumatica / odoo in item_sites): they never match.
            _rec(
                "ITEM-853-C",
                "301",
                rop=400,
                on_hand=280,
                on_order=0,
                oq_units=100,
                unit_cost=1.0,
            ),
            _rec(
                "ITEM-853-C",
                "302",
                rop=300,
                on_hand=700,
                on_order=0,
                oq_units=100,
                unit_cost=1.0,
            ),
            # Item D: the donor sits at exactly ROP + order quantity
            # (position 400, ROP 300, order quantity 100): it holds
            # nothing beyond them and gives nothing to the need of
            # 120.
            _rec(
                "ITEM-853-D",
                "401",
                rop=300,
                on_hand=400,
                on_order=0,
                oq_units=100,
                unit_cost=1.0,
            ),
            _rec(
                "ITEM-853-D",
                "402",
                rop=400,
                on_hand=280,
                on_order=0,
                oq_units=100,
                unit_cost=1.0,
            ),
            # Item E: a need and a surplus whose pairs have no
            # item_sites row: no source system, no match.
            _rec(
                "ITEM-853-E",
                "501",
                rop=400,
                on_hand=280,
                on_order=0,
                oq_units=100,
                unit_cost=1.0,
            ),
            _rec(
                "ITEM-853-E",
                "502",
                rop=300,
                on_hand=700,
                on_order=0,
                oq_units=100,
                unit_cost=1.0,
            ),
            # Not status "ok": never a transfer, whatever the position.
            _rec(
                "ITEM-853-F",
                "601",
                status="no_demand",
                rop=400,
                on_hand=280,
                on_order=0,
                oq_units=100,
                unit_cost=1.0,
            ),
            _rec(
                "ITEM-853-F",
                "602",
                rop=300,
                on_hand=700,
                on_order=0,
                oq_units=100,
                unit_cost=1.0,
            ),
            # Status "ok" but no numeric recommended order quantity:
            # never a transfer.
            _rec(
                "ITEM-853-G",
                "701",
                rop=400,
                on_hand=280,
                on_order=0,
                oq_units=None,
                unit_cost=1.0,
            ),
            _rec(
                "ITEM-853-G",
                "702",
                rop=300,
                on_hand=700,
                on_order=0,
                oq_units=None,
                unit_cost=1.0,
            ),
            # Status "ok" with a position the route cannot hold — a
            # non-numeric on hand and an order-quantity block that is
            # not a mapping: skipped, never a 500.
            _rec(
                "ITEM-853-H",
                "801",
                rop=400,
                on_hand="lots",
                on_order=0,
                oq_units=100,
                unit_cost=1.0,
            ),
            {
                "item_code": "ITEM-853-H",
                "site_code": "802",
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 300.0,
                "reorder_point_units": 300,
                "on_hand_units": 700,
                "on_order_units": 0,
                "unit_cost": 1.0,
                "order_quantity": "eoq",
            },
        ]


class CountingItemSiteRepo:
    """The real item_sites repository wrapped to count its reads: the
    route must read the table once per call, for each pair's
    ``source_system``."""

    calls = 0

    def __init__(self):
        self._inner = ItemSitePostgresRepository()

    def list_rows(self, limit, offset, **filters):
        CountingItemSiteRepo.calls += 1
        return self._inner.list_rows(limit, offset, **filters)


def _suggestion_b():
    """Item B's partial cover: a surplus of 50 against a need of 120
    suggests 50, value 50 x 10.00 to cents — the larger value, so the
    first of the ordered suggestions."""
    return {
        "item_code": "ITEM-853-B",
        "source_system": "acumatica",
        "from_site": "202",
        "to_site": "201",
        "quantity": 50,
        "unit_cost": 10.0,
        "value": "500.00",
        "to_site_need": 120,
        "from_site_surplus": 50,
        "arithmetic": "site 201 needs 120 (ROP 400 \u2212 position 280); site 202 holds 50 beyond ROP + order quantity; move 50",
    }


def _suggestion_a():
    """Item A's one transfer: 154 gives 159 the 120 of its need, value
    120 x 2.50 to cents."""
    return {
        "item_code": "ITEM-853-A",
        "source_system": "acumatica",
        "from_site": "154",
        "to_site": "159",
        "quantity": 120,
        "unit_cost": 2.5,
        "value": "300.00",
        "to_site_need": 120,
        "from_site_surplus": 300,
        "arithmetic": "site 159 needs 120 (ROP 400 \u2212 position 280); site 154 holds 300 beyond ROP + order quantity; move 120",
    }


def _expected_body():
    """The full expected response: the two items ordered by value
    descending (500.00 before 300.00), total 800.00 to cents."""
    return {
        "as_of": "2026-10-31",
        "suggestion_count": 2,
        "total_value": "800.00",
        "suggestions": [_suggestion_b(), _suggestion_a()],
    }


def test_issue853_freeform(monkeypatch, client):
    # --- Seed the item_sites rows the route reads once per call:
    # every pair's source system.  Item E's pairs are deliberately
    # absent (no source system), and item C's two sites sit on
    # different systems.
    ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": "ITEM-853-A",
                "site_code": "159",
                "source_system": "acumatica",
            },
            {
                "item_code": "ITEM-853-A",
                "site_code": "154",
                "source_system": "acumatica",
            },
            {
                "item_code": "ITEM-853-B",
                "site_code": "201",
                "source_system": "acumatica",
            },
            {
                "item_code": "ITEM-853-B",
                "site_code": "202",
                "source_system": "acumatica",
            },
            {
                "item_code": "ITEM-853-C",
                "site_code": "301",
                "source_system": "acumatica",
            },
            {
                "item_code": "ITEM-853-C",
                "site_code": "302",
                "source_system": "odoo",
            },
            {
                "item_code": "ITEM-853-D",
                "site_code": "401",
                "source_system": "acumatica",
            },
            {
                "item_code": "ITEM-853-D",
                "site_code": "402",
                "source_system": "acumatica",
            },
        ]
    )

    # --- The route's sources, replaced the way the issue 830 proof
    # replaces its sources: the fake recommendations answer the
    # canned rows; the item_sites read is the real repository wrapped
    # to count — the route must read it exactly once per call — and
    # the resolved as-of is pinned.
    FakeRecommendations.calls = 0
    CountingItemSiteRepo.calls = 0
    monkeypatch.setattr(supply_plan, "recommendations_for_all", FakeRecommendations())
    monkeypatch.setattr(supply_plan, "default_as_of", lambda cur: AS_OF)
    monkeypatch.setattr(supply_plan, "_item_site_repo", CountingItemSiteRepo())

    # No ERP call: the client classes fail if constructed.
    def _no_client(self, *args, **kwargs):
        raise AssertionError("the transfer suggestions route must not call an ERP")

    monkeypatch.setattr(acumatica_client_module.AcumaticaClient, "__init__", _no_client)
    monkeypatch.setattr(odoo_client_module.OdooClient, "__init__", _no_client)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    anon = TestClient(app)

    # --- Unauthenticated access: no token answers 401 (status only —
    # auth_gate defines no canonical 401 body).
    assert anon.get(URL).status_code == 401

    # --- The transfer suggestions. ----------------------------------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == _expected_body()

    # Item A: exactly one suggestion — 154 → 159, quantity 120, the
    # arithmetic line the one line of text naming both numbers (the
    # need of 120 and the surplus of 300).
    a = [s for s in body["suggestions"] if s["item_code"] == "ITEM-853-A"]
    assert len(a) == 1
    assert (a[0]["from_site"], a[0]["to_site"], a[0]["quantity"]) == (
        "154",
        "159",
        120,
    )
    assert a[0]["arithmetic"] == (
        "site 159 needs 120 (ROP 400 \u2212 position 280); "
        "site 154 holds 300 beyond ROP + order quantity; move 120"
    )

    # Item B: the partial cover — a surplus of 50 against a need of
    # 120 suggests 50: the donor's remainder is reduced to zero and
    # the need is left short, never rounded up.
    b = [s for s in body["suggestions"] if s["item_code"] == "ITEM-853-B"]
    assert len(b) == 1
    assert b[0]["quantity"] == 50
    assert b[0]["to_site_need"] == 120
    assert b[0]["from_site_surplus"] == 50

    # Item C's different source systems never match; item D's donor at
    # exactly ROP + order quantity gives nothing; item E's unlisted
    # pairs never match; the skipped rows never surface.
    for absent in (
        "ITEM-853-C",
        "ITEM-853-D",
        "ITEM-853-E",
        "ITEM-853-F",
        "ITEM-853-G",
        "ITEM-853-H",
    ):
        assert absent not in json.dumps(body)

    # The two items order by value descending (500.00 before 300.00).
    assert [s["item_code"] for s in body["suggestions"]] == [
        "ITEM-853-B",
        "ITEM-853-A",
    ]

    # Each source was read exactly once per call.
    assert FakeRecommendations.calls == 1
    assert CountingItemSiteRepo.calls == 1

    # A second call reads each source exactly once more.
    again = admin.get(URL)
    assert again.status_code == 200
    assert again.json() == body
    assert FakeRecommendations.calls == 2
    assert CountingItemSiteRepo.calls == 2

    # --- The schema file lists the route and its models. -----------
    schema = json.loads(_OPENAPI.read_text())
    assert "/api/supply-plan/transfers" in schema["paths"]
    assert "get" in schema["paths"]["/api/supply-plan/transfers"]
    for model in ("TransferSuggestionsResponse", "TransferSuggestion"):
        assert model in schema["components"]["schemas"]

    # --- Identity: a second pass after a full reload, as the
    # NON-ADMIN (role "user").  The page is booted the production
    # way: the token it would take from the URL hash as
    # #access_token=... is the development token (role "user") sent
    # as its Bearer token; GET /api/auth/config answers publicly and
    # GET /api/auth/me resolves the viewer.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is True
    assert "user" in me.json()["user"]["roles"]
    assert "admin" not in me.json()["user"]["roles"]

    # The transfers page still renders for the viewer — the route is
    # open to any signed-in user, so no admin-only control stands on
    # it — with the same body, and no token is still a 401.
    viewer_response = viewer.get(URL)
    assert viewer_response.status_code == 200, viewer_response.text
    assert viewer_response.json() == body
    assert anon_reloaded.get(URL).status_code == 401
