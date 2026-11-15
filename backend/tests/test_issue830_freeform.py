"""Issue #830 proof: the supply plan — fit_to_terms and GET /api/supply-plan.

AC-1 — the quantity rule: ``fit_to_terms`` starts from ``ceil(raw)``; a set
minimum below the quantity raises it (``"raised_to_minimum"``), a set lot
size rounds up to the next whole multiple (``"rounded_to_lot"``, only when
it changes the quantity), a set maximum above it caps it
(``"capped_at_maximum"``), and ``short_of_need`` is true only when the
final quantity is below ``ceil(raw)`` — proved on the four pinned cases.

AC-1/AC-2 — the route: the route module's sources are replaced the way the
issue 795 proof replaces its sources — ``recommendations_for_all`` with a
fake answering three "ok" rows ((a) position 689 vs reorder point 945,
EOQ 246, suggested 256, d 30 — due now; (b) position 1000 vs 940, EOQ 100,
d 20 — due in 3 days; (c) position 5000 vs 900, d 10 — due in 410 days)
plus the rows the plan must skip — the item_sites read with a counting
wrapper over the real repository (each pair's ``source_system``), the
``supplier_terms`` repository and the suppliers table with counting
wrappers over the seeded rows, and the resolved as-of pinned.  Proved
here: (a) and (b) are in the 7-day plan and (c) is not; (a)'s raw
quantity is 256 (the larger of the EOQ and the suggested quantity),
fitted to its DEFAULT supplier's minimum and lot — the non-default
smaller-code row of the item and the other source system's row of (b)'s
item must not apply; the lines group under the supplier with its seeded
name and (b), with no terms for its source system, lands in the final
null group with a null ``source_system``; the arithmetic line shows the
steps; (a)'s value is quantity × unit cost to cents while (b), without a
unit cost, has a null value and contributes zero to the group and plan
totals; the groups are ordered by earliest due then total value, the
null group last; each source was read exactly once per call;
``horizon_days=2`` drops (b); ``horizon_days=0`` and 61 answer 422; no
token answers 401; the ERP client classes are patched to fail if
constructed; the schema file lists the route and its models.

Identity — the proof also runs as a NON-ADMIN (role "user"): a second
pass in the same test after a full reload, with the page booted the
production way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent as its
Bearer token), ``GET /api/auth/config`` answering publicly and ``GET
/api/auth/me`` resolving the viewer — sees the plan page still render
(200 with the same body, the route open to any signed-in user) while no
token is still a 401.
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
from app.repositories.supplier_terms_postgres_repository import (
    SupplierTermsPostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)
from app.supply.terms import fit_to_terms

URL = "/api/supply-plan"
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

# The resolved as-of the route reports (never null).
AS_OF = date(2026, 10, 31)


class FakeRecommendations:
    """The fake for the route module's ``recommendations_for_all``: the
    three canned "ok" rows — (a) due now, (b) due in 3 days, (c) due in
    410 days — plus the rows the plan must skip: a non-"ok" row and an
    "ok" row with no numeric recommended order quantity."""

    calls = 0

    def __call__(self, as_of=None):
        FakeRecommendations.calls += 1
        return [
            {
                "item_code": "ITEM-830-A",
                "site_code": "SITE-830-A",
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 944.6,
                "reorder_point_units": 945,
                "safety_stock_units": 250,
                "inputs": {
                    "d": 30.0,
                    "sd_d": 6.0,
                    "lt": 14.0,
                    "sd_lt": 3.0,
                    "z": 1.6449,
                    "service_level": 0.95,
                },
                "on_hand_units": 500,
                "on_order_units": 189,
                "unit_cost": 4.5,
                "suggested_order_quantity": 256,
                "order_quantity": {
                    "basis": "eoq",
                    "clamped": None,
                    "order_quantity": 245.7,
                    "order_quantity_units": 246,
                    "cover_days": 8.2,
                    "annual_demand": 10950.0,
                    "order_cost": 50.0,
                    "holding_rate": 0.25,
                    "unit_cost": 4.5,
                    "arithmetic": "EOQ 245.7",
                    "recommended_max_units": 1191,
                    "current_order_qty": None,
                    "current_max_qty": None,
                },
            },
            {
                "item_code": "ITEM-830-B",
                "site_code": "SITE-830-B",
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 940.0,
                "reorder_point_units": 940,
                "safety_stock_units": 200,
                "inputs": {
                    "d": 20.0,
                    "sd_d": 4.0,
                    "lt": 7.0,
                    "sd_lt": 1.5,
                    "z": 1.6449,
                    "service_level": 0.95,
                },
                "on_hand_units": 800,
                "on_order_units": 200,
                "unit_cost": None,
                "suggested_order_quantity": 0,
                "order_quantity": {
                    "basis": "eoq",
                    "clamped": None,
                    "order_quantity": 99.6,
                    "order_quantity_units": 100,
                    "cover_days": 5.0,
                    "annual_demand": 7300.0,
                    "order_cost": 50.0,
                    "holding_rate": 0.25,
                    "unit_cost": None,
                    "arithmetic": "EOQ 99.6",
                    "recommended_max_units": 1040,
                    "current_order_qty": None,
                    "current_max_qty": None,
                },
            },
            {
                "item_code": "ITEM-830-C",
                "site_code": "SITE-830-C",
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 900.0,
                "reorder_point_units": 900,
                "safety_stock_units": 150,
                "inputs": {
                    "d": 10.0,
                    "sd_d": 2.0,
                    "lt": 21.0,
                    "sd_lt": 4.0,
                    "z": 1.6449,
                    "service_level": 0.95,
                },
                "on_hand_units": 4000,
                "on_order_units": 1000,
                "unit_cost": 2.0,
                "suggested_order_quantity": 0,
                "order_quantity": {
                    "basis": "eoq",
                    "clamped": None,
                    "order_quantity": 199.1,
                    "order_quantity_units": 200,
                    "cover_days": 20.0,
                    "annual_demand": 3650.0,
                    "order_cost": 50.0,
                    "holding_rate": 0.25,
                    "unit_cost": 2.0,
                    "arithmetic": "EOQ 199.1",
                    "recommended_max_units": 1100,
                    "current_order_qty": None,
                    "current_max_qty": None,
                },
            },
            # Not status "ok": never in the plan.
            {
                "item_code": "ITEM-830-D",
                "site_code": "SITE-830-D",
                "as_of": AS_OF,
                "status": "no_demand",
                "reorder_point_units": None,
                "inputs": None,
                "on_hand_units": 12,
                "on_order_units": 0,
                "unit_cost": 9.0,
                "suggested_order_quantity": None,
                "order_quantity": None,
            },
            # Status "ok" but no numeric recommended order quantity:
            # never in the plan.
            {
                "item_code": "ITEM-830-E",
                "site_code": "SITE-830-E",
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 880.0,
                "reorder_point_units": 880,
                "safety_stock_units": 120,
                "inputs": {
                    "d": 5.0,
                    "sd_d": 1.0,
                    "lt": 9.0,
                    "sd_lt": 1.0,
                    "z": 1.6449,
                    "service_level": 0.95,
                },
                "on_hand_units": 100,
                "on_order_units": 0,
                "unit_cost": 3.0,
                "suggested_order_quantity": 780,
                "order_quantity": None,
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


class CountingSupplierTermsRepo:
    """The real supplier_terms repository wrapped to count its reads:
    the route must read it once per call."""

    calls = 0

    def __init__(self):
        self._inner = SupplierTermsPostgresRepository()

    def list(self, item_code=None, supplier_code=None):
        CountingSupplierTermsRepo.calls += 1
        return self._inner.list(item_code=item_code, supplier_code=supplier_code)


class CountingSupplierRepo:
    """The real suppliers repository wrapped to count its reads: the
    route must read the suppliers table once per call."""

    calls = 0

    def __init__(self):
        self._inner = SupplierPostgresRepository()

    def list_all(self):
        CountingSupplierRepo.calls += 1
        return self._inner.list_all()


def _line_a():
    """(a): due now, raw 256 fitted to the default supplier's minimum
    (300) and lot (25), value 300 × 4.50 to cents."""
    return {
        "item_code": "ITEM-830-A",
        "site_code": "SITE-830-A",
        "position_units": 689,
        "reorder_point_units": 945,
        "daily_demand": 30.0,
        "due_in_days": 0,
        "order_by": "2026-10-31",
        "lead_time_days": 10.0,
        "arrives_on": "2026-11-10",
        "raw_quantity": 256,
        "quantity": 300,
        "adjustments": ["raised_to_minimum"],
        "short_of_need": False,
        "terms": {"min_order_qty": 300.0, "lot_size": 25.0, "max_order_qty": None},
        "unit_cost": 4.5,
        # The monetary fields are exact decimals, rendered as decimal
        # strings in the JSON body, the way the release plan does it.
        "value": "1350.00",
        "arithmetic": "raw 256 (shortfall below reorder point) → raised to minimum 300 → rounded to lot 25: 300",
    }


def _line_b():
    """(b): due in 3 days, no terms for its source system — no
    supplier, null terms, lead time from the recommendation's inputs,
    no unit cost, null value."""
    return {
        "item_code": "ITEM-830-B",
        "site_code": "SITE-830-B",
        "position_units": 1000,
        "reorder_point_units": 940,
        "daily_demand": 20.0,
        "due_in_days": 3,
        "order_by": "2026-11-03",
        "lead_time_days": 7.0,
        "arrives_on": "2026-11-10",
        "raw_quantity": 100,
        "quantity": 100,
        "adjustments": [],
        "short_of_need": False,
        "terms": None,
        "unit_cost": None,
        "value": None,
        "arithmetic": "raw 100 (EOQ): 100",
    }


def _expected_body(horizon_days=7, with_b=True):
    """The full expected response for the default as-of."""
    suppliers = [
        {
            "supplier_code": "S-830-A2",
            "source_system": "acumatica",
            "vendor_code": "V-830-A2",
            "name": "Acme Widget Co.",
            "line_count": 1,
            "total_value": "1350.00",
            "lines": [_line_a()],
        }
    ]
    if with_b:
        # The no-supplier lines form the final group, its
        # supplier_code and source_system null.
        suppliers.append(
            {
                "supplier_code": None,
                "source_system": None,
                "vendor_code": None,
                "name": None,
                "line_count": 1,
                # 0, not null, when every line's value is null.
                "total_value": "0",
                "lines": [_line_b()],
            }
        )
    line_count = 1 + (1 if with_b else 0)
    return {
        "as_of": "2026-10-31",
        "horizon_days": horizon_days,
        "line_count": line_count,
        "total_value": "1350.00",
        "suppliers": suppliers,
    }


def test_issue830_freeform(monkeypatch, client):
    # --- AC-1: the quantity rule, on the pinned cases. --------------
    assert fit_to_terms(246, {"min_order_qty": 300, "lot_size": 25}) == {
        "quantity": 300,
        "adjustments": ["raised_to_minimum"],
        "short_of_need": False,
    }
    assert fit_to_terms(310, {"lot_size": 25}) == {
        "quantity": 325,
        "adjustments": ["rounded_to_lot"],
        "short_of_need": False,
    }
    assert fit_to_terms(900, {"max_order_qty": 500}) == {
        "quantity": 500,
        "adjustments": ["capped_at_maximum"],
        "short_of_need": True,
    }
    assert fit_to_terms(12.2, None) == {
        "quantity": 13,
        "adjustments": [],
        "short_of_need": False,
    }

    # --- Seed the tables the route reads once per call. -------------
    ItemSitePostgresRepository().upsert_many(
        [
            # (b)'s pair is acumatica: its odoo terms row must not
            # apply to it.
            {
                "item_code": "ITEM-830-A",
                "site_code": "SITE-830-A",
                "source_system": "acumatica",
            },
            {
                "item_code": "ITEM-830-B",
                "site_code": "SITE-830-B",
                "source_system": "acumatica",
            },
            {
                "item_code": "ITEM-830-C",
                "site_code": "SITE-830-C",
                "source_system": "acumatica",
            },
        ]
    )
    SupplierTermsPostgresRepository().upsert_many(
        [
            # (a)'s item: the smaller-code row is NOT the default —
            # the route must pick S-830-A2 by is_default, not by code.
            {
                "business_key": "acumatica:ITEM-830-A:S-830-A1",
                "source_system": "acumatica",
                "item_code": "ITEM-830-A",
                "supplier_code": "S-830-A1",
                "min_order_qty": 999,
                "max_order_qty": None,
                "lot_size": 500,
                "lead_time_days": 99,
                "is_default": False,
            },
            {
                "business_key": "acumatica:ITEM-830-A:S-830-A2",
                "source_system": "acumatica",
                "item_code": "ITEM-830-A",
                "supplier_code": "S-830-A2",
                "min_order_qty": 300,
                "max_order_qty": None,
                "lot_size": 25,
                "lead_time_days": 10,
                "is_default": True,
            },
            # (b)'s item: terms of the WRONG source system (the pair
            # is acumatica) — they must not apply, (b) stays in the
            # null group.
            {
                "business_key": "odoo:ITEM-830-B:S-830-B1",
                "source_system": "odoo",
                "item_code": "ITEM-830-B",
                "supplier_code": "S-830-B1",
                "min_order_qty": 50,
                "max_order_qty": None,
                "lot_size": 10,
                "lead_time_days": 4,
                "is_default": True,
            },
        ]
    )
    SupplierPostgresRepository().upsert_many(
        [
            {
                "supplier_code": "S-830-A1",
                "vendor_code": "V-830-A1",
                "name": "Bulk Bolt Bros.",
                "source_system": "acumatica",
            },
            {
                "supplier_code": "S-830-A2",
                "vendor_code": "V-830-A2",
                "name": "Acme Widget Co.",
                "source_system": "acumatica",
            },
        ]
    )

    # --- The route's sources, replaced the way the issue 795 proof
    # replaces its sources.  The fake recommendations answer the
    # canned rows; the reads are the real repositories wrapped to
    # count — the route must read each source exactly once per call.
    FakeRecommendations.calls = 0
    CountingItemSiteRepo.calls = 0
    CountingSupplierTermsRepo.calls = 0
    CountingSupplierRepo.calls = 0
    monkeypatch.setattr(supply_plan, "recommendations_for_all", FakeRecommendations())
    monkeypatch.setattr(supply_plan, "default_as_of", lambda cur: AS_OF)
    monkeypatch.setattr(supply_plan, "_item_site_repo", CountingItemSiteRepo())
    monkeypatch.setattr(
        supply_plan, "_supplier_terms_repo", CountingSupplierTermsRepo()
    )
    monkeypatch.setattr(supply_plan, "_supplier_repo", CountingSupplierRepo())

    # No ERP call: the client classes fail if constructed.
    def _no_client(self, *args, **kwargs):
        raise AssertionError("the supply plan route must not call an ERP")

    monkeypatch.setattr(acumatica_client_module.AcumaticaClient, "__init__", _no_client)
    monkeypatch.setattr(odoo_client_module.OdooClient, "__init__", _no_client)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    anon = TestClient(app)

    # --- Unauthenticated access: no token answers 401. --------------
    assert anon.get(URL).status_code == 401

    # --- The 7-day plan: (a) and (b) in, (c) (due in 410 days) out. -
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == _expected_body()
    assert "ITEM-830-C" not in json.dumps(body)
    assert "ITEM-830-D" not in json.dumps(body)
    assert "ITEM-830-E" not in json.dumps(body)
    # The non-default smaller-code row and the wrong source system's
    # row never applied: (a) carries S-830-A2's terms and lead time,
    # (b) carries none at all.
    assert "S-830-A1" not in json.dumps(body)
    assert "S-830-B1" not in json.dumps(body)

    # Each source was read exactly once per call.
    assert FakeRecommendations.calls == 1
    assert CountingItemSiteRepo.calls == 1
    assert CountingSupplierTermsRepo.calls == 1
    assert CountingSupplierRepo.calls == 1

    # A second call reads each source exactly once more.
    again = admin.get(URL)
    assert again.status_code == 200
    assert again.json() == body
    assert FakeRecommendations.calls == 2
    assert CountingItemSiteRepo.calls == 2
    assert CountingSupplierTermsRepo.calls == 2
    assert CountingSupplierRepo.calls == 2

    # --- The horizon: 2 days drops (b) (due in 3); 0 and 61 are
    # outside 1..60 and answer 422.
    short = admin.get(URL + "?horizon_days=2")
    assert short.status_code == 200, short.text
    assert short.json() == _expected_body(horizon_days=2, with_b=False)
    assert admin.get(URL + "?horizon_days=0").status_code == 422
    assert admin.get(URL + "?horizon_days=61").status_code == 422

    # --- The schema file lists the route and its models. -----------
    schema = json.loads(_OPENAPI.read_text())
    assert "/api/supply-plan" in schema["paths"]
    assert "get" in schema["paths"]["/api/supply-plan"]
    for model in (
        "SupplyPlanResponse",
        "SupplyPlanSupplierGroup",
        "SupplyPlanLine",
        "SupplyPlanTerms",
    ):
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

    # The plan page still renders for the viewer — the route is open
    # to any signed-in user, so no admin-only control stands on it —
    # with the same body, and no token is still a 401.
    viewer_response = viewer.get(URL)
    assert viewer_response.status_code == 200, viewer_response.text
    assert viewer_response.json() == body
    assert anon_reloaded.get(URL).status_code == 401
