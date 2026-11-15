"""Issue #839 proof: the arithmetic names the input that set the raw
quantity.

The supply plan's arithmetic line labels the raw quantity with the
input that set it: when the suggested order quantity is strictly
greater than the order quantity's units, the label is "shortfall below
reorder point"; otherwise it stays the order quantity's basis label
("EOQ", "assumed 30 days" or "recommended"), exactly as before.
Proved the way the issue 830 proof proves the route: the route
module's ``recommendations_for_all`` is replaced with a fake answering
three "ok" rows, all due now — (a) shortfall 336 above its EOQ 253,
(b) EOQ 100 above its shortfall 40, (c) the tie, shortfall 120 and
EOQ 120 — and the resolved as-of pinned, with each pair seeded in
item_sites and no supplier terms anywhere.  The three lines read, in
full, "raw 336 (shortfall below reorder point): 336", "raw 100 (EOQ):
100" and "raw 120 (EOQ): 120" — the quantities, fields and ordering
untouched by the label.
"""

from datetime import date

import app.api.supply_plan as supply_plan
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

URL = "/api/supply-plan"

# The resolved as-of the route reports (never null).
AS_OF = date(2026, 10, 31)


class FakeRecommendations:
    """The fake for the route module's ``recommendations_for_all``: the
    three canned "ok" rows, all due now — (a) shortfall 336 above its
    EOQ 253, (b) EOQ 100 above its shortfall 40, (c) the tie, shortfall
    120 and EOQ 120."""

    calls = 0

    def __call__(self, as_of=None):
        FakeRecommendations.calls += 1
        return [
            {
                "item_code": "ITEM-839-A",
                "site_code": "SITE-839-A",
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 500.0,
                "reorder_point_units": 500,
                "safety_stock_units": 100,
                "inputs": {
                    "d": 10.0,
                    "sd_d": 2.0,
                    "lt": 7.0,
                    "sd_lt": 1.0,
                    "z": 1.6449,
                    "service_level": 0.95,
                },
                "on_hand_units": 200,
                "on_order_units": 100,
                "unit_cost": None,
                "suggested_order_quantity": 336,
                "order_quantity": {
                    "basis": "eoq",
                    "clamped": None,
                    "order_quantity": 252.9,
                    "order_quantity_units": 253,
                    "cover_days": 25.3,
                    "annual_demand": 3650.0,
                    "order_cost": 50.0,
                    "holding_rate": 0.25,
                    "unit_cost": None,
                    "arithmetic": "EOQ 252.9",
                    "recommended_max_units": 800,
                    "current_order_qty": None,
                    "current_max_qty": None,
                },
            },
            {
                "item_code": "ITEM-839-B",
                "site_code": "SITE-839-B",
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 400.0,
                "reorder_point_units": 400,
                "safety_stock_units": 80,
                "inputs": {
                    "d": 10.0,
                    "sd_d": 2.0,
                    "lt": 7.0,
                    "sd_lt": 1.0,
                    "z": 1.6449,
                    "service_level": 0.95,
                },
                "on_hand_units": 200,
                "on_order_units": 50,
                "unit_cost": None,
                "suggested_order_quantity": 40,
                "order_quantity": {
                    "basis": "eoq",
                    "clamped": None,
                    "order_quantity": 99.6,
                    "order_quantity_units": 100,
                    "cover_days": 10.0,
                    "annual_demand": 3650.0,
                    "order_cost": 50.0,
                    "holding_rate": 0.25,
                    "unit_cost": None,
                    "arithmetic": "EOQ 99.6",
                    "recommended_max_units": 600,
                    "current_order_qty": None,
                    "current_max_qty": None,
                },
            },
            {
                "item_code": "ITEM-839-C",
                "site_code": "SITE-839-C",
                "as_of": AS_OF,
                "status": "ok",
                "reorder_point": 300.0,
                "reorder_point_units": 300,
                "safety_stock_units": 60,
                "inputs": {
                    "d": 10.0,
                    "sd_d": 2.0,
                    "lt": 7.0,
                    "sd_lt": 1.0,
                    "z": 1.6449,
                    "service_level": 0.95,
                },
                "on_hand_units": 150,
                "on_order_units": 70,
                "unit_cost": None,
                "suggested_order_quantity": 120,
                "order_quantity": {
                    "basis": "eoq",
                    "clamped": None,
                    "order_quantity": 119.8,
                    "order_quantity_units": 120,
                    "cover_days": 12.0,
                    "annual_demand": 3650.0,
                    "order_cost": 50.0,
                    "holding_rate": 0.25,
                    "unit_cost": None,
                    "arithmetic": "EOQ 119.8",
                    "recommended_max_units": 450,
                    "current_order_qty": None,
                    "current_max_qty": None,
                },
            },
        ]


def test_issue839_freeform(monkeypatch, client):
    # --- Seed the pairs the route reads once per call. -------------
    ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": "ITEM-839-A",
                "site_code": "SITE-839-A",
                "source_system": "acumatica",
            },
            {
                "item_code": "ITEM-839-B",
                "site_code": "SITE-839-B",
                "source_system": "acumatica",
            },
            {
                "item_code": "ITEM-839-C",
                "site_code": "SITE-839-C",
                "source_system": "acumatica",
            },
        ]
    )

    # --- The route's sources, replaced the way the issue 830 proof
    # replaces its sources.  The fake recommendations answer the
    # canned rows; the resolved as-of is pinned.
    FakeRecommendations.calls = 0
    monkeypatch.setattr(supply_plan, "recommendations_for_all", FakeRecommendations())
    monkeypatch.setattr(supply_plan, "default_as_of", lambda cur: AS_OF)

    # --- The 7-day plan: all three rows due now, no supplier. ------
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["as_of"] == "2026-10-31"
    assert body["horizon_days"] == 7
    assert body["line_count"] == 3

    groups = body["suppliers"]
    assert len(groups) == 1
    group = groups[0]
    # No supplier terms anywhere: the single final group, its
    # supplier_code and source_system null.
    assert group["supplier_code"] is None
    assert group["source_system"] is None
    assert group["vendor_code"] is None
    assert group["name"] is None
    assert group["line_count"] == 3
    assert group["total_value"] == "0"
    assert [line["item_code"] for line in group["lines"]] == [
        "ITEM-839-A",
        "ITEM-839-B",
        "ITEM-839-C",
    ]

    by_item = {line["item_code"]: line for line in group["lines"]}
    # (a) shortfall 336 above its EOQ 253: the raw quantity is the
    # suggested one, named as the shortfall below the reorder point.
    assert by_item["ITEM-839-A"]["arithmetic"] == (
        "raw 336 (shortfall below reorder point): 336"
    )
    assert by_item["ITEM-839-A"]["raw_quantity"] == 336
    assert by_item["ITEM-839-A"]["quantity"] == 336
    # (b) EOQ 100 above its shortfall 40: the raw quantity is the
    # order quantity, named by its basis label as before.
    assert by_item["ITEM-839-B"]["arithmetic"] == "raw 100 (EOQ): 100"
    assert by_item["ITEM-839-B"]["raw_quantity"] == 100
    assert by_item["ITEM-839-B"]["quantity"] == 100
    # (c) the tie, shortfall 120 and EOQ 120: not strictly greater —
    # the basis label stands.
    assert by_item["ITEM-839-C"]["arithmetic"] == "raw 120 (EOQ): 120"
    assert by_item["ITEM-839-C"]["raw_quantity"] == 120
    assert by_item["ITEM-839-C"]["quantity"] == 120

    # All three due now, the fitting steps absent without terms.
    for line in group["lines"]:
        assert line["due_in_days"] == 0
        assert line["terms"] is None
        assert line["adjustments"] == []
        assert line["short_of_need"] is False

    # The fake was read exactly once per call.
    assert FakeRecommendations.calls == 1
