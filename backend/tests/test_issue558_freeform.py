"""Issue #558 proof: release unit costs are rounded half-up to two decimals.

``app.aggregate.release.plan_release`` hands each release line the
planned unit cost rounded half-up to two decimal places (
``Decimal.quantize(Decimal("0.01"), ROUND_HALF_UP)`` — 0.005 becomes
0.01, never the banker's 0.00; a unit cost with no value is still
0.00), the line's ``value`` is the quantity at that rounded unit
cost, each group's ``value`` the sum of its lines and ``total_value``
the sum of the groups — all exact ``Decimal``.  The rounded unit cost
is what the release endpoint sends to Acumatica and what the
``plan_releases`` line snapshot and value record, so a released
group's recorded value equals the order's extended-cost total in the
ERP.  The #538 conflict check rounds the stored snapshot's line
``unit_cost`` and ``value`` the same way before comparing, so a
re-run of an already released month whose only difference is rounding
is not reported as a plan conflict while a real quantity change
still is.

Proved against the test PostgreSQL database with migration 0021
applied, with ``planned_orders``, the purchase-line read (
``OpenPurchaseLinePostgresRepository.list_latest``) and the two
Acumatica batch functions (
``AcumaticaClient.preview_draft_purchase_orders`` /
``create_draft_purchase_orders``) monkeypatched — no network — and
the suite's admin and non-admin tokens, using the four live lines of
PO003070 as fixtures: CONBOTTLE1 701 at 20.830624512, CONGRILL 125
at 139.99, CONGRILLT 128 at 19.135431193 and CONHEARTM1 2808 at
42.700720833.

Proved here, in order:

  * the plan: the four lines' unit costs are 20.83, 139.99, 19.14
    and 42.70 and their values 14601.83, 17498.75, 2449.92 and
    119901.60 — each line's value the quantity at its rounded unit
    cost, the group value and ``total_value`` 154452.10, matching
    Acumatica's order total for PO003070; a line with no unit cost
    gives 0.00 with value 0.00, and 0.005 rounds up to 0.01
    (half-up, not banker's);
  * the preview (non-admin) renders the rounded costs and values as
    exact decimal strings with the 154452.10 total;
  * the confirm (admin): the PUT payload the stubbed client receives
    carries the rounded costs, the response carries the rounded
    costs, values and the 154452.10 group value, and the audit row's
    line snapshot records the rounded costs with the exact value
    ``Decimal("154452.10")``;
  * a stored snapshot carrying the old unrounded costs for the same
    quantities does not raise the plan-conflict 409 on a confirm
    whose current plan differs from it only by rounding (the
    ``exists`` outcome is accepted), while a real quantity change
    still does (409, the group's row ``error``).
"""

import os
from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

import app.aggregate.release as release_module
from app.feed.acumatica_client import AcumaticaClient
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.plan_releases_postgres_repository import (
    PlanReleasePostgresRepository,
)

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

URL = "/api/aggregate/release"

# One PO003070: one site, one vendor, the four live lines.
SITE = "154"
SUPPLIER = "4851"
WAREHOUSE = "WHOLESALE"
VENDOR_CODE = "CONPERIPH"

# The four live lines of PO003070: item, quantity, planned unit cost.
PO003070 = [
    ("CONBOTTLE1", 701, "20.830624512"),
    ("CONGRILL", 125, "139.99"),
    ("CONGRILLT", 128, "19.135431193"),
    ("CONHEARTM1", 2808, "42.700720833"),
]

# The rounded unit costs and the line values at those rounded costs.
ROUNDED = {
    "CONBOTTLE1": (Decimal("20.83"), Decimal("14601.83")),
    "CONGRILL": (Decimal("139.99"), Decimal("17498.75")),
    "CONGRILLT": (Decimal("19.14"), Decimal("2449.92")),
    "CONHEARTM1": (Decimal("42.70"), Decimal("119901.60")),
}

GROUP_VALUE = Decimal("154452.10")  # Acumatica's order total for PO003070.

CURRENT_LINES = [
    {
        "item_code": item,
        "quantity": quantity,
        "unit_cost": str(ROUNDED[item][0]),
        "value": str(ROUNDED[item][1]),
    }
    for item, quantity, _cost in PO003070
]

# The same four lines as a pre-#558 confirm would have stored: the old
# unrounded unit costs, the same quantities and values — the current
# plan differs from this snapshot only by rounding.
OLD_LINES = [
    {
        "item_code": item,
        "quantity": quantity,
        "unit_cost": cost,
        "value": str(ROUNDED[item][1]),
    }
    for item, quantity, cost in PO003070
]


def _shift_month(delta: int) -> str:
    """The YYYY-MM of the current month shifted by ``delta`` months."""
    today = date.today()
    index = today.year * 12 + (today.month - 1) + delta
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def _order(item: str, month: str, quantity: int, unit_cost) -> dict:
    """One planned order of the release month, as the database hands it."""
    cost = None if unit_cost is None else Decimal(unit_cost)
    return {
        "item_code": item,
        "site_code": SITE,
        "placed_month": month,
        "arrival_month": month,
        "quantity": quantity,
        "unit_cost": cost,
        "value": quantity * (cost if cost is not None else 0),
        "d": 1.0,
        "month_end_on_hand": 0,
    }


def _result(origin: str, outcome: str, order_nbr, line_count: int) -> dict:
    """One canned per-order batch result, as the client would return it."""
    return {
        "origin": origin,
        "outcome": outcome,
        "order_nbr": order_nbr,
        "status": "On Hold",
        "warehouse_code": WAREHOUSE,
        "vendor_code": VENDOR_CODE,
        "line_count": line_count,
        "error": None,
    }


def test_issue558_freeform(monkeypatch, client, admin_token) -> None:
    # Isolate the Acumatica environment, complete configuration.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")

    month = _shift_month(0)
    ORIGIN = f"AIERP plan {month} site {SITE} vendor {SUPPLIER}"

    # -- the four monkeypatched seams -----------------------------------
    orders_variant = {"orders": None}

    def fake_planned_orders(
        as_of=None,
        months=6,
        service_level=None,
        demand_multiplier=None,
        lead_time_multiplier=None,
    ):
        return [
            _order(item, month, quantity, cost)
            for item, quantity, cost in orders_variant["orders"]
        ]

    monkeypatch.setattr(release_module, "planned_orders", fake_planned_orders)

    def fake_list_latest(self):
        return [
            {
                "item_code": item,
                "site_code": SITE,
                "supplier_code": SUPPLIER,
                "ordered_on": date(2026, 5, 1),
                "business_key": f"{item}-1",
            }
            for item in (
                "CONBOTTLE1",
                "CONGRILL",
                "CONGRILLT",
                "CONHEARTM1",
                "CONNOPRICE",
                "CONHALFUP",
            )
        ]

    monkeypatch.setattr(
        OpenPurchaseLinePostgresRepository, "list_latest", fake_list_latest
    )

    captured = {"orders": None}
    create_results = {"value": None}

    def fake_preview(self, orders):
        if not orders:
            return []
        return [
            {
                "origin": entry["origin"],
                "outcome": "would_create",
                "order_nbr": None,
                "status": None,
                "warehouse_code": WAREHOUSE,
                "vendor_code": VENDOR_CODE,
                "line_count": len(entry["lines"]),
                "error": None,
            }
            for entry in orders
        ]

    def fake_create(self, orders):
        if not orders:
            return []
        captured["orders"] = orders
        return create_results["value"]

    monkeypatch.setattr(AcumaticaClient, "preview_draft_purchase_orders", fake_preview)
    monkeypatch.setattr(AcumaticaClient, "create_draft_purchase_orders", fake_create)

    from app.main import app

    # The suite's admin token for the confirms.
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    repo = PlanReleasePostgresRepository()

    # -- 1. the plan: PO003070's four lines round to the ERP total ------
    orders_variant["orders"] = PO003070
    plan = release_module.plan_release(month)
    assert plan["month"] == month
    assert plan["unreleasable"] == []
    assert len(plan["groups"]) == 1
    group = plan["groups"][0]
    assert group["site_code"] == SITE
    assert group["supplier_code"] == SUPPLIER
    assert group["origin"] == ORIGIN
    assert [line["item_code"] for line in group["lines"]] == [
        item for item, _quantity, _cost in PO003070
    ]
    for line in group["lines"]:
        unit, value = ROUNDED[line["item_code"]]
        assert line["unit_cost"] == unit
        assert isinstance(line["unit_cost"], Decimal)
        assert line["value"] == value
        assert isinstance(line["value"], Decimal)
        # The value is the quantity at the rounded unit cost, exactly.
        assert line["value"] == Decimal(line["quantity"]) * line["unit_cost"]
    # The exact strings the API and the audit snapshot render.
    assert [str(line["unit_cost"]) for line in group["lines"]] == [
        "20.83",
        "139.99",
        "19.14",
        "42.70",
    ]
    assert [str(line["value"]) for line in group["lines"]] == [
        "14601.83",
        "17498.75",
        "2449.92",
        "119901.60",
    ]
    # Group value the sum of its lines, total the sum of the groups —
    # 154452.10, Acumatica's order total for PO003070.
    assert group["value"] == sum(line["value"] for line in group["lines"])
    assert group["value"] == GROUP_VALUE
    assert plan["total_value"] == GROUP_VALUE
    assert isinstance(plan["total_value"], Decimal)
    assert str(group["value"]) == "154452.10"

    # -- 2. no unit cost is 0.00; 0.005 rounds up, not banker's --------
    assert release_module.round_half_up_cents(Decimal("0.005")) == Decimal("0.01")
    orders_variant["orders"] = [("CONNOPRICE", 10, None), ("CONHALFUP", 1, "0.005")]
    plan = release_module.plan_release(month)
    lines = {line["item_code"]: line for line in plan["groups"][0]["lines"]}
    assert lines["CONNOPRICE"]["unit_cost"] == Decimal("0.00")
    assert lines["CONNOPRICE"]["value"] == Decimal("0.00")
    assert isinstance(lines["CONNOPRICE"]["unit_cost"], Decimal)
    # Half-up: 0.005 becomes 0.01 — banker's rounding would give 0.00.
    assert lines["CONHALFUP"]["unit_cost"] == Decimal("0.01")
    assert lines["CONHALFUP"]["value"] == Decimal("0.01")
    assert plan["groups"][0]["value"] == Decimal("0.01")

    # -- 3. the preview (non-admin) renders the rounded plan ------------
    orders_variant["orders"] = PO003070
    response = client.post(URL, json={"month": month})
    assert response.status_code == 200, response.text
    preview = response.json()
    assert preview["acumatica_available"] is True
    assert preview["total_value"] == "154452.10"
    assert preview["groups"][0]["value"] == "154452.10"
    assert preview["groups"][0]["outcome"] == "would_create"
    assert preview["groups"][0]["lines"] == CURRENT_LINES

    # -- 4. the confirm: the PUT payload carries the rounded costs ------
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")
    create_results["value"] = [_result(ORIGIN, "created", "PO003070", 4)]
    response = admin.post(URL, json={"month": month, "confirm": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total_value"] == "154452.10"
    assert body["groups"][0]["value"] == "154452.10"
    assert body["groups"][0]["outcome"] == "created"
    assert body["groups"][0]["order_nbr"] == "PO003070"
    assert body["groups"][0]["lines"] == CURRENT_LINES
    # The PUT payload the stubbed client received: the rounded costs.
    assert captured["orders"] == [
        {
            "site_id": SITE,
            "vendor_id": SUPPLIER,
            "origin": ORIGIN,
            "lines": [
                {"item_code": "CONBOTTLE1", "quantity": 701, "unit_cost": 20.83},
                {"item_code": "CONGRILL", "quantity": 125, "unit_cost": 139.99},
                {"item_code": "CONGRILLT", "quantity": 128, "unit_cost": 19.14},
                {"item_code": "CONHEARTM1", "quantity": 2808, "unit_cost": 42.70},
            ],
        }
    ]
    # The audit row records the rounded snapshot and the exact value —
    # the order's extended-cost total in the ERP.
    rows = repo.list_newest()
    assert len(rows) == 1
    row = rows[0]
    assert row["origin"] == ORIGIN
    assert row["outcome"] == "created"
    assert row["order_nbr"] == "PO003070"
    assert isinstance(row["value"], Decimal)
    assert row["value"] == GROUP_VALUE
    assert row["lines"] == CURRENT_LINES

    # -- 5. old unrounded snapshot: a rounding-only difference is not a
    #        plan conflict ----------------------------------------------
    old_id = repo.insert_pending(
        month=month,
        site_code=SITE,
        supplier_code=SUPPLIER,
        origin=ORIGIN,
        lines=[
            {
                **line,
                "unit_cost": Decimal(line["unit_cost"]),
                "value": Decimal(line["value"]),
            }
            for line in OLD_LINES
        ],
        value=GROUP_VALUE,
        actor="pre-558",
    )
    repo.update_outcome(
        old_id,
        outcome="created",
        order_nbr="PO003070",
        warehouse_code=WAREHOUSE,
        vendor_code=VENDOR_CODE,
    )
    # Acumatica still holds the PO under the origin: the client answers
    # exists.
    create_results["value"] = [_result(ORIGIN, "exists", "PO003070", 4)]
    response = admin.post(URL, json={"month": month, "confirm": True})
    assert response.status_code == 200, response.text
    assert response.json()["groups"][0]["outcome"] == "exists"
    assert response.json()["groups"][0]["order_nbr"] == "PO003070"

    # -- 6. a real quantity change is still a plan conflict --------------
    orders_variant["orders"] = [
        ("CONBOTTLE1", 701, "20.830624512"),
        ("CONGRILL", 126, "139.99"),  # 125 -> 126: a real change
        ("CONGRILLT", 128, "19.135431193"),
        ("CONHEARTM1", 2808, "42.700720833"),
    ]
    response = admin.post(URL, json={"month": month, "confirm": True})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert ORIGIN in detail
    assert "re-confirmation" in detail
    rows = repo.list_newest()
    # The newest row is this confirm's: error (never exists), with the
    # changed line in its snapshot.
    assert rows[0]["origin"] == ORIGIN
    assert rows[0]["outcome"] == "error"
    assert rows[0]["order_nbr"] is None
    assert rows[0]["lines"][1]["quantity"] == 126
