"""Issue #842 proof: the order-plan release through the shared release body.

AC-1 — the shared release body: the preview and confirm body of
``POST /api/aggregate/release`` moved, unchanged, into
``app.api.aggregate.release_groups(plan, confirm, current_user)`` —
proved the way the month release is proved here: the month release
preview through ``POST /api/aggregate/release`` (the module's
``planned_orders`` and the purchase-line history replaced with fakes)
answers exactly as before — the current month's planned orders, one
group per (site, supplier) with the month's origin tag, the order whose
item-site has no supplier in its history as ``unreleasable``, the
preview outcome on top.

AC-1/AC-3 — the order-plan plan and the route:
``app.supply.release.order_plan_release`` builds a plan of the same
shape from the lines the ``GET /api/supply-plan`` builder produces —
the builder's sources replaced the way the issue 830 proof replaces
them (``recommendations_for_all`` with a fake answering three "ok"
rows all due now) and a demand event seeded so the latest transaction
date in the synced data — the release's as-of, the one the origin tag
and the month name — is a fixed date not equal to the server date —
seeded so the plan holds: two Acumatica suppliers at site 154 and one
Odoo line.  Proved here: a preview naming one Acumatica supplier
answers one group with its line at its fitted quantity (125, the lot
over the raw 120), the origin tag "AIERP order plan <as_of> site 154
vendor <code>" taken from that plan's as-of — the latest transaction
date in the data, never a date regenerated from the server clock — the
month as the as-of's YYYY-MM, and no Odoo line, writing nothing and
calling the recording Acumatica fake's preview once; a preview naming both
Acumatica suppliers answers one group per (site, supplier); a preview
naming the Odoo line's supplier lists it under ``unreleasable`` as
"only Acumatica lines are released here" with empty groups; a preview
naming only unknown supplier codes answers 200 with empty groups,
creates no Acumatica draft PO and is not a 422.

AC-2/AC-4 — confirm, audit and the refusals: an admin confirm — run
under a server clock moved to a different month, earlier than the
data's date, where any resolution that still consulted the server date
would have answered a different as-of — calls the fake's create
method once with that group, writes one ``plan_releases`` row with
outcome "created" (the origin tag still the preview's tag, the plan's
as-of, stable across preview and confirm and across the moved clock)
and the row appears in ``GET /api/aggregate/release/audit``; a planner confirm answers
403, an empty ``supplier_codes`` answers 422 (as do the other body
rule breakers), switched-off writes answer 409 and write nothing, and
no token answers 401.

Identity — the proof also runs as a NON-ADMIN (role "user"): a second
pass in the same test after a full reload, with the page booted the
production way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent as
its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer — sees the preview still
answer (200 with the same body, open to any signed-in user) while the
admin-only confirm is refused with 403 and no token is still a 401.
"""

import os
from datetime import date
from decimal import Decimal

import psycopg2
from fastapi.testclient import TestClient

import app.aggregate.release as aggregate_release
import app.api.supply_plan as supply_plan
import app.feed.acumatica_client as acumatica_client_module
import app.feed.odoo_client as odoo_client_module
from app.db.connection import get_connection_params, get_cursor
from app.feed.acumatica_client import (
    AcumaticaClient,
    AcumaticaWritesDisabledError,
)
from app.main import app
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.supplier_terms_postgres_repository import (
    SupplierTermsPostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)

URL = "/api/supply-plan/release"
MONTH_URL = "/api/aggregate/release"
AUDIT = "/api/aggregate/release/audit"

ACUMATICA_VARS = (
    "ACUMATICA_BASE_URL",
    "ACUMATICA_TENANT",
    "ACUMATICA_USERNAME",
    "ACUMATICA_PASSWORD",
    "ACUMATICA_ENDPOINT_VERSION",
    "ACUMATICA_CA_BUNDLE",
    "ACUMATICA_TIMEOUT_SECONDS",
    "ACUMATICA_ALLOW_INSECURE",
    "ACUMATICA_ALLOW_WRITES",
)

ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

# The latest transaction date in the seeded data — deliberately not
# the server date, so the origin tag is provably the plan's as-of,
# never a date regenerated from the server clock at confirm time.
AS_OF = date(2026, 10, 15)
AS_OF_MONTH = "2026-10"

SUPPLIER_A = "S-842-1"
SUPPLIER_B = "S-842-2"
SUPPLIER_C = "S-842-3"
ITEM_A = "ITEM-842-A"
ITEM_B = "ITEM-842-B"
ITEM_C = "ITEM-842-C"
SITE_A = "154"
SITE_C = "159"
ORIGIN_A = f"AIERP order plan {AS_OF.isoformat()} site {SITE_A} vendor {SUPPLIER_A}"
ORIGIN_B = f"AIERP order plan {AS_OF.isoformat()} site {SITE_A} vendor {SUPPLIER_B}"
NOT_ACUMATICA_REASON = "only Acumatica lines are released here"
ADMIN_DETAIL = "Role 'admin' is required to perform this action."
WRITES_OFF_DETAIL = "Acumatica writes are disabled"


def _shift_month(delta: int) -> str:
    """The YYYY-MM of the current month shifted by ``delta`` months."""
    today = date.today()
    index = today.year * 12 + (today.month - 1) + delta
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


class FakeRecommendations:
    """The fake for the route module's ``recommendations_for_all``: the
    three canned "ok" rows, all due now — (a) and (b) the two
    Acumatica item-sites of site 154, (c) the Odoo line of site 159."""

    calls = 0

    def __call__(self, as_of=None):
        FakeRecommendations.calls += 1
        return [
            {
                "item_code": ITEM_A,
                "site_code": SITE_A,
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 100.0,
                "reorder_point_units": 100,
                "safety_stock_units": 25,
                "inputs": {
                    "d": 30.0,
                    "sd_d": 6.0,
                    "lt": 10.0,
                    "sd_lt": 3.0,
                    "z": 1.6449,
                    "service_level": 0.95,
                },
                "on_hand_units": 50,
                "on_order_units": 0,
                "unit_cost": 4.5,
                "suggested_order_quantity": 0,
                "order_quantity": {
                    "basis": "eoq",
                    "clamped": None,
                    "order_quantity": 119.4,
                    "order_quantity_units": 120,
                    "cover_days": 12.3,
                    "annual_demand": 10950.0,
                    "order_cost": 50.0,
                    "holding_rate": 0.25,
                    "unit_cost": 4.5,
                    "arithmetic": "EOQ 119.4",
                    "recommended_max_units": 400,
                    "current_order_qty": None,
                    "current_max_qty": None,
                },
            },
            {
                "item_code": ITEM_B,
                "site_code": SITE_A,
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 60.0,
                "reorder_point_units": 60,
                "safety_stock_units": 15,
                "inputs": {
                    "d": 20.0,
                    "sd_d": 4.0,
                    "lt": 7.0,
                    "sd_lt": 1.5,
                    "z": 1.6449,
                    "service_level": 0.95,
                },
                "on_hand_units": 40,
                "on_order_units": 0,
                "unit_cost": 10.005,
                "suggested_order_quantity": 0,
                "order_quantity": {
                    "basis": "eoq",
                    "clamped": None,
                    "order_quantity": 89.6,
                    "order_quantity_units": 90,
                    "cover_days": 9.2,
                    "annual_demand": 7300.0,
                    "order_cost": 50.0,
                    "holding_rate": 0.25,
                    "unit_cost": 10.005,
                    "arithmetic": "EOQ 89.6",
                    "recommended_max_units": 260,
                    "current_order_qty": None,
                    "current_max_qty": None,
                },
            },
            {
                "item_code": ITEM_C,
                "site_code": SITE_C,
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 40.0,
                "reorder_point_units": 40,
                "safety_stock_units": 10,
                "inputs": {
                    "d": 10.0,
                    "sd_d": 2.0,
                    "lt": 5.0,
                    "sd_lt": 1.0,
                    "z": 1.6449,
                    "service_level": 0.95,
                },
                "on_hand_units": 20,
                "on_order_units": 0,
                "unit_cost": 2.0,
                "suggested_order_quantity": 0,
                "order_quantity": {
                    "basis": "eoq",
                    "clamped": None,
                    "order_quantity": 29.7,
                    "order_quantity_units": 30,
                    "cover_days": 30.0,
                    "annual_demand": 3650.0,
                    "order_cost": 50.0,
                    "holding_rate": 0.25,
                    "unit_cost": 2.0,
                    "arithmetic": "EOQ 29.7",
                    "recommended_max_units": 160,
                    "current_order_qty": None,
                    "current_max_qty": None,
                },
            },
        ]


def _release_body(month, groups, unreleasable, total_value):
    """One expected order-plan release answer (the preview/confirm
    both answer with Acumatica available in this test)."""
    return {
        "month": month,
        "groups": groups,
        "unreleasable": unreleasable,
        "total_value": total_value,
        "acumatica_available": True,
    }


def _group_a(outcome, order_nbr):
    return {
        "site_code": SITE_A,
        "supplier_code": SUPPLIER_A,
        "origin": ORIGIN_A,
        "lines": [
            # The fitted quantity (125, the lot over the raw 120) at
            # the unit cost — 4.50, no rounding needed — as exact
            # decimal strings.
            {"item_code": ITEM_A, "quantity": 125, "unit_cost": "4.5", "value": "562.5"}
        ],
        "value": "562.5",
        "outcome": outcome,
        "warehouse_code": "WHOLESALE",
        "vendor_code": "CONPERIPH",
        "order_nbr": order_nbr,
        "error": None,
    }


def _group_b(outcome, order_nbr):
    return {
        "site_code": SITE_A,
        "supplier_code": SUPPLIER_B,
        "origin": ORIGIN_B,
        "lines": [
            # Raised to the minimum 100; the unit cost 10.005 rounded
            # half-up to cents exactly as plan_release rounds it.
            {
                "item_code": ITEM_B,
                "quantity": 100,
                "unit_cost": "10.01",
                "value": "1001.00",
            }
        ],
        "value": "1001.00",
        "outcome": outcome,
        "warehouse_code": "WHOLESALE",
        "vendor_code": "CONPERIPH",
        "order_nbr": order_nbr,
        "error": None,
    }


def _order_a():
    """(a) in the wire format the release body sends to Acumatica."""
    return {
        "site_id": SITE_A,
        "vendor_id": SUPPLIER_A,
        "origin": ORIGIN_A,
        "lines": [{"item_code": ITEM_A, "quantity": 125, "unit_cost": 4.5}],
    }


def _order_b():
    return {
        "site_id": SITE_A,
        "vendor_id": SUPPLIER_B,
        "origin": ORIGIN_B,
        "lines": [{"item_code": ITEM_B, "quantity": 100, "unit_cost": 10.01}],
    }


EXPECTED_PREVIEW_ONE = _release_body(
    AS_OF_MONTH, [_group_a("would_create", None)], [], "562.5"
)
EXPECTED_PREVIEW_BOTH = _release_body(
    AS_OF_MONTH,
    [_group_a("would_create", None), _group_b("would_create", None)],
    [],
    "1563.50",
)
EXPECTED_ODOO = _release_body(
    AS_OF_MONTH,
    [],
    [{"item_code": ITEM_C, "site_code": SITE_C, "reason": NOT_ACUMATICA_REASON}],
    "0",
)
EXPECTED_UNKNOWN = _release_body(AS_OF_MONTH, [], [], "0")
EXPECTED_CONFIRM_ONE = _release_body(
    AS_OF_MONTH, [_group_a("created", "PO-842-001")], [], "562.5"
)


def _expected_month_body(month):
    """The month release preview answer, exactly as before the shared
    body moved: the current month's planned orders grouped by
    (site, supplier), M3 (no supplier in history) unreleasable, M4
    (next month) not in the release."""
    return {
        "month": month,
        "groups": [
            {
                "site_code": SITE_A,
                "supplier_code": "4851",
                "origin": f"AIERP plan {month} site {SITE_A} vendor 4851",
                "lines": [
                    {
                        "item_code": "M1",
                        "quantity": 10,
                        "unit_cost": "2.0",
                        "value": "20.0",
                    }
                ],
                "value": "20.0",
                "outcome": "would_create",
                "warehouse_code": "WHOLESALE",
                "vendor_code": "CONPERIPH",
                "order_nbr": None,
                "error": None,
            },
            {
                "site_code": SITE_A,
                "supplier_code": "4870",
                "origin": f"AIERP plan {month} site {SITE_A} vendor 4870",
                "lines": [
                    {
                        "item_code": "M2",
                        "quantity": 5,
                        "unit_cost": "10.0",
                        "value": "50.0",
                    }
                ],
                "value": "50.0",
                "outcome": "would_create",
                "warehouse_code": "WHOLESALE",
                "vendor_code": "CONPERIPH",
                "order_nbr": None,
                "error": None,
            },
        ],
        "unreleasable": [
            {
                "item_code": "M3",
                "site_code": SITE_A,
                "reason": "no supplier in purchase history",
            }
        ],
        "total_value": "70.0",
        "acumatica_available": True,
    }


def _plan_release_rows() -> list[dict]:
    """Every stored plan_releases row, oldest first, every column."""
    conn = psycopg2.connect(**get_connection_params())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM plan_releases ORDER BY id")
            columns = [column[0] for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def test_issue842_freeform(monkeypatch, client, user_token) -> None:
    # Isolate the Acumatica environment, complete configuration,
    # writes on.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- Seed the tables the builder reads once per call. ------------
    # The demand event is the latest transaction in the synced data:
    # the release's as-of — the date the origin tag and the month name
    # — is this data date, never the server date.
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO demand_events (id, business_key, item_code, "
            "site_code, quantity, occurred_on, event_type) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("842-demand-1", "842-demand-1", ITEM_A, SITE_A, 30.0, AS_OF, "shipment"),
        )
    ItemSitePostgresRepository().upsert_many(
        [
            # Two Acumatica item-sites at site 154, one Odoo line.
            {"item_code": ITEM_A, "site_code": SITE_A, "source_system": "acumatica"},
            {"item_code": ITEM_B, "site_code": SITE_A, "source_system": "acumatica"},
            {"item_code": ITEM_C, "site_code": SITE_C, "source_system": "odoo"},
        ]
    )
    # The supplier terms that make the plan hold: two Acumatica
    # suppliers at site 154 and the Odoo line's supplier.
    SupplierTermsPostgresRepository().upsert_many(
        [
            {
                "business_key": f"acumatica:{ITEM_A}:{SUPPLIER_A}",
                "source_system": "acumatica",
                "item_code": ITEM_A,
                "supplier_code": SUPPLIER_A,
                "min_order_qty": 100,
                "max_order_qty": None,
                "lot_size": 25,
                "lead_time_days": 10,
                "is_default": True,
            },
            {
                "business_key": f"acumatica:{ITEM_B}:{SUPPLIER_B}",
                "source_system": "acumatica",
                "item_code": ITEM_B,
                "supplier_code": SUPPLIER_B,
                "min_order_qty": 100,
                "max_order_qty": None,
                "lot_size": 10,
                "lead_time_days": 10,
                "is_default": True,
            },
            {
                "business_key": f"odoo:{ITEM_C}:{SUPPLIER_C}",
                "source_system": "odoo",
                "item_code": ITEM_C,
                "supplier_code": SUPPLIER_C,
                "min_order_qty": 20,
                "max_order_qty": None,
                "lot_size": 10,
                "lead_time_days": 5,
                "is_default": True,
            },
        ]
    )
    SupplierPostgresRepository().upsert_many(
        [
            {
                "supplier_code": SUPPLIER_A,
                "vendor_code": "V-842-1",
                "name": "Acme Fasteners",
                "source_system": "acumatica",
            },
            {
                "supplier_code": SUPPLIER_B,
                "vendor_code": "V-842-2",
                "name": "Bolt & Nut Works",
                "source_system": "acumatica",
            },
            {
                "supplier_code": SUPPLIER_C,
                "vendor_code": "V-842-3",
                "name": "Odoo Widgets",
                "source_system": "odoo",
            },
        ]
    )

    # --- The Acumatica client, replaced with a fake recording its
    # calls.  The write switch is consulted the way the real client
    # consults it: before anything else, before the empty-batch
    # shortcut.
    preview_calls: list[list] = []
    create_calls: list[list] = []

    def _writes_enabled() -> bool:
        return os.environ.get("ACUMATICA_ALLOW_WRITES", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def fake_preview(self, orders):
        preview_calls.append([dict(entry) for entry in orders])
        if not orders:
            return []
        return [
            {
                "origin": entry["origin"],
                "outcome": "would_create",
                "order_nbr": None,
                "status": None,
                "warehouse_code": "WHOLESALE",
                "vendor_code": "CONPERIPH",
                "line_count": len(entry["lines"]),
                "error": None,
            }
            for entry in orders
        ]

    def fake_create(self, orders):
        if not _writes_enabled():
            raise AcumaticaWritesDisabledError(
                "Acumatica writes are disabled; set ACUMATICA_ALLOW_WRITES "
                "to a truthy value to enable purchase order creation"
            )
        if not orders:
            return []
        create_calls.append([dict(entry) for entry in orders])
        return [
            {
                "origin": entry["origin"],
                "outcome": "created",
                "order_nbr": f"PO-842-{len(create_calls):03d}",
                "status": "On Hold",
                "warehouse_code": "WHOLESALE",
                "vendor_code": "CONPERIPH",
                "line_count": len(entry["lines"]),
                "error": None,
            }
            for entry in orders
        ]

    monkeypatch.setattr(AcumaticaClient, "preview_draft_purchase_orders", fake_preview)
    monkeypatch.setattr(AcumaticaClient, "create_draft_purchase_orders", fake_create)

    # --- The builder's sources, replaced the way the issue 830 proof
    # replaces its sources: the fake recommendations answer the canned
    # rows.  The release's as-of is not pinned: it is the latest
    # transaction date in the seeded data (the demand event above),
    # which the release reads from the data, never from the server
    # clock or the resolver's clamp to today.
    FakeRecommendations.calls = 0
    monkeypatch.setattr(supply_plan, "recommendations_for_all", FakeRecommendations())

    # --- The month release's seams (the "unchanged" check below). ----
    def planned(item, site, placed, quantity, unit_cost):
        return {
            "item_code": item,
            "site_code": site,
            "placed_month": placed,
            "arrival_month": placed,
            "quantity": quantity,
            "unit_cost": unit_cost,
            "value": quantity * (unit_cost if unit_cost is not None else 0),
        }

    def fake_planned_orders(as_of=None, months=6, **kwargs):
        month = _shift_month(0)
        return [
            planned("M1", SITE_A, month, 10, Decimal("2.0")),
            planned("M2", SITE_A, month, 5, Decimal("10.0")),
            planned("M3", SITE_A, month, 7, Decimal("3.0")),
            # Placed in the next month: not part of this release.
            planned("M4", SITE_A, _shift_month(1), 2, Decimal("1.0")),
        ]

    history = [
        {
            "item_code": "M1",
            "site_code": SITE_A,
            "supplier_code": "4851",
            "ordered_on": date(2026, 7, 15),
            "business_key": "M1-1",
        },
        {
            "item_code": "M2",
            "site_code": SITE_A,
            "supplier_code": "4870",
            "ordered_on": date(2026, 7, 20),
            "business_key": "M2-1",
        },
    ]

    def fake_list_latest(self):
        return [dict(row) for row in history]

    monkeypatch.setattr(aggregate_release, "planned_orders", fake_planned_orders)
    monkeypatch.setattr(
        OpenPurchaseLinePostgresRepository, "list_latest", fake_list_latest
    )

    # The Odoo client must never be constructed: the release is
    # Acumatica-only.
    def _no_odoo_client(self, *args, **kwargs):
        raise AssertionError("the order-plan release must not call Odoo")

    monkeypatch.setattr(odoo_client_module.OdooClient, "__init__", _no_odoo_client)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    planner = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon = TestClient(app)

    # --- Unauthenticated access: no token answers 401. ---------------
    assert anon.post(URL, json={"supplier_codes": [SUPPLIER_A]}).status_code == 401

    # --- Preview: one supplier — one group, the fitted quantity, the
    # origin tag, no Odoo line, and nothing written.
    response = admin.post(URL, json={"supplier_codes": [SUPPLIER_A]})
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_PREVIEW_ONE
    assert ORIGIN_A in response.text
    assert ITEM_C not in response.text
    assert SUPPLIER_C not in response.text
    assert _plan_release_rows() == []
    assert preview_calls == [[_order_a()]]
    assert create_calls == []
    assert FakeRecommendations.calls == 1

    # --- Preview: both Acumatica suppliers — one group per
    # (site, supplier).
    both = admin.post(URL, json={"supplier_codes": [SUPPLIER_A, SUPPLIER_B]})
    assert both.status_code == 200, both.text
    assert both.json() == EXPECTED_PREVIEW_BOTH
    assert preview_calls == [[_order_a()], [_order_a(), _order_b()]]
    assert _plan_release_rows() == []

    # --- Odoo: a preview naming the Odoo line's supplier lists it
    # under unreleasable with the stated reason and empty groups.
    odoo = admin.post(URL, json={"supplier_codes": [SUPPLIER_C]})
    assert odoo.status_code == 200, odoo.text
    assert odoo.json() == EXPECTED_ODOO

    # --- Unknown supplier codes: 200 with empty groups, no Acumatica
    # draft PO, and not a 422.
    unknown = admin.post(URL, json={"supplier_codes": ["S-NOPE", "S-NOPE-2"]})
    assert unknown.status_code == 200, unknown.text
    assert unknown.json() == EXPECTED_UNKNOWN
    assert create_calls == []

    # --- Body rule breakers: 422. ------------------------------------
    assert admin.post(URL, json={"supplier_codes": []}).status_code == 422
    assert admin.post(URL, json={}).status_code == 422
    assert admin.post(URL).status_code == 422
    assert (
        admin.post(
            URL, json={"supplier_codes": [SUPPLIER_A], "horizon_days": 0}
        ).status_code
        == 422
    )
    assert (
        admin.post(
            URL, json={"supplier_codes": [SUPPLIER_A], "horizon_days": 61}
        ).status_code
        == 422
    )
    assert admin.post(URL, json={"supplier_codes": ["S-0"] * 51}).status_code == 422
    assert admin.post(URL, json={"supplier_codes": ["x" * 65]}).status_code == 422
    assert admin.post(URL, json={"supplier_codes": ["S-\x00"]}).status_code == 422
    assert (
        admin.post(URL, json={"supplier_codes": [SUPPLIER_A], "extra": 1}).status_code
        == 422
    )
    # A preview is still possible: the refusals wrote nothing.
    assert _plan_release_rows() == []

    # --- Confirm: the admin confirms that group — under a moved
    # server clock.  The clock as the as-of resolver sees it moves to
    # 2026-09-15: a different month, and earlier than the data's date,
    # so any resolution that still consulted the server date (the
    # min(today, latest) clamp) would have rebuilt the origin tag and
    # the month from 2026-09-15 at confirm time.  The confirm instead
    # carries the preview's tag — the plan's as-of, a function of the
    # data, stable for the release.
    class _FakeDate(date):
        @classmethod
        def today(cls):
            return date(2026, 9, 15)

    monkeypatch.setattr("app.stats.demand.date", _FakeDate)
    confirm = admin.post(URL, json={"supplier_codes": [SUPPLIER_A], "confirm": True})
    assert confirm.status_code == 200, confirm.text
    assert confirm.json() == EXPECTED_CONFIRM_ONE
    # The fake's create method called once, with that group.
    assert create_calls == [[_order_a()]]

    # One plan_releases row, outcome created, the exact lines.
    rows = _plan_release_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["month"] == AS_OF_MONTH
    assert row["site_code"] == SITE_A
    assert row["supplier_code"] == SUPPLIER_A
    assert row["origin"] == ORIGIN_A
    assert row["outcome"] == "created"
    assert row["order_nbr"] == "PO-842-001"
    assert row["warehouse_code"] == "WHOLESALE"
    assert row["vendor_code"] == "CONPERIPH"
    assert row["line_count"] == 1
    assert row["value"] == Decimal("562.5")
    assert row["lines"] == [
        {"item_code": ITEM_A, "quantity": 125, "unit_cost": "4.5", "value": "562.5"}
    ]
    assert row["actor"] == "admin"
    assert row["error"] is None

    # The row appears in the audit — and the audit stays admin only.
    audit = admin.get(AUDIT)
    assert audit.status_code == 200
    audit_rows = audit.json()
    assert len(audit_rows) == 1
    assert audit_rows[0]["origin"] == ORIGIN_A
    assert audit_rows[0]["month"] == AS_OF_MONTH
    assert audit_rows[0]["site_code"] == SITE_A
    assert audit_rows[0]["supplier_code"] == SUPPLIER_A
    assert audit_rows[0]["outcome"] == "created"
    assert audit_rows[0]["order_nbr"] == "PO-842-001"
    assert audit_rows[0]["value"] == "562.5"
    assert audit_rows[0]["lines"] == [
        {"item_code": ITEM_A, "quantity": 125, "unit_cost": "4.5", "value": "562.5"}
    ]
    assert audit_rows[0]["actor"] == "admin"
    assert planner.get(AUDIT).status_code == 403

    # --- Refusals. -----------------------------------------------------
    # A planner confirm answers 403 and writes nothing.
    refused = planner.post(URL, json={"supplier_codes": [SUPPLIER_A], "confirm": True})
    assert refused.status_code == 403
    assert refused.json()["detail"] == ADMIN_DETAIL
    assert len(_plan_release_rows()) == 1

    # Switched-off writes answer 409 and write nothing.
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES")
    off = admin.post(URL, json={"supplier_codes": [SUPPLIER_A], "confirm": True})
    assert off.status_code == 409
    assert off.json()["detail"] == WRITES_OFF_DETAIL
    assert len(_plan_release_rows()) == 1
    assert len(create_calls) == 1
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- Month release unchanged: the month release preview through
    # POST /api/aggregate/release answers exactly as before.
    month = _shift_month(0)
    month_response = admin.post(MONTH_URL, json={"month": month})
    assert month_response.status_code == 200, month_response.text
    assert month_response.json() == _expected_month_body(month)
    assert preview_calls[-1] == [
        {
            "site_id": SITE_A,
            "vendor_id": "4851",
            "origin": f"AIERP plan {month} site {SITE_A} vendor 4851",
            "lines": [{"item_code": "M1", "quantity": 10, "unit_cost": 2.0}],
        },
        {
            "site_id": SITE_A,
            "vendor_id": "4870",
            "origin": f"AIERP plan {month} site {SITE_A} vendor 4870",
            "lines": [{"item_code": "M2", "quantity": 5, "unit_cost": 10.0}],
        },
    ]
    assert len(_plan_release_rows()) == 1

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

    # The preview still answers for the viewer — the route is open to
    # any signed-in user, so no admin-only control stands on it —
    # with the same body; the admin-only confirm is refused; no token
    # is still a 401.
    viewer_preview = viewer.post(URL, json={"supplier_codes": [SUPPLIER_A]})
    assert viewer_preview.status_code == 200, viewer_preview.text
    assert viewer_preview.json() == EXPECTED_PREVIEW_ONE
    viewer_confirm = viewer.post(
        URL, json={"supplier_codes": [SUPPLIER_A], "confirm": True}
    )
    assert viewer_confirm.status_code == 403
    assert (
        anon_reloaded.post(URL, json={"supplier_codes": [SUPPLIER_A]}).status_code
        == 401
    )
    # Still nothing new written.
    assert len(_plan_release_rows()) == 1
