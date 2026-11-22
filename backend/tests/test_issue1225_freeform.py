"""Issue #1225 proof: the top-ups cap is on the entries as sent.

The release body's ``top_ups`` holds at most 200 entries *as sent* —
``Field(max_length=200)`` on the ``OrderPlanReleaseIn`` list — so a
body with 201 or more entries is 422 by request validation, before
the release is built, even when the repeats would leave 200 or fewer
distinct top-ups; below the cap nothing changes, repeated entries
still counting once.  Read through the app's test client with the
stand-ins of ``tests/test_issue1191_freeform.py``: the Acumatica
client's preview and create replaced the way
``tests/test_issue842_freeform.py`` replaces them, ``excess_ranked``
in ``app.api.supply_plan`` answering nothing, ``_data_as_of`` in
``app.supply.release`` answering 2026-11-02, and
``build_supply_plan`` in ``app.api.supply_plan`` a stand-in recording
each call's horizon and answering one fixed plan per horizon: at 7,
supplier ``S-A`` of ``acumatica`` with a minimum of 1000.00 and a
shortfall of 600.00 and one due line D at site 159, 4 units at
100.00; at 30, the same plus S-A's lines T1 at 159 (due in 12 days,
3 units at 150.00) and T2 at 160 (due in 20 days, 2 units at
100.00).  Proved: 200 repeats of T1 plus one T2 — 201 entries as
sent — is 422, and 201 distinct entries is 422, neither asking the
stand-in anything; 199 repeats of T1 plus one T2 — 200 entries —
answers 200 with T1 and T2 each added once, the same answer as naming
each once; and ``OrderPlanReleaseIn``'s ``top_ups`` carries
``maxItems`` 200, both in ``backend/openapi.json`` and in
``app.openapi()``.
"""

import json
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import app.api.supply_plan as supply_plan
import app.supply.release as release_module
from app.feed.acumatica_client import (
    AcumaticaClient,
    AcumaticaWritesDisabledError,
)
from app.main import app

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

URL = "/api/supply-plan/release"

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

# The release's as-of: the fixed answer of the release module's
# _data_as_of seam — never the server date.
AS_OF = date(2026, 11, 2)
AS_OF_STR = AS_OF.isoformat()
AS_OF_MONTH = "2026-11"

SUPPLIER = "S-A"
SITE_159 = "159"
SITE_160 = "160"
ITEM_D = "D"
ITEM_T1 = "T1"
ITEM_T2 = "T2"
ORIGIN_159 = f"AIERP order plan {AS_OF_STR} site {SITE_159} vendor {SUPPLIER}"
ORIGIN_160 = f"AIERP order plan {AS_OF_STR} site {SITE_160} vendor {SUPPLIER}"

LINE_D = {
    "item_code": ITEM_D,
    "quantity": 4,
    "unit_cost": "100.0",
    "value": "400.0",
    "source": "reorder_point",
}
LINE_T1 = {
    "item_code": ITEM_T1,
    "quantity": 3,
    "unit_cost": "150.0",
    "value": "450.0",
    "source": "top_up",
}
LINE_T2 = {
    "item_code": ITEM_T2,
    "quantity": 2,
    "unit_cost": "100.0",
    "value": "200.0",
    "source": "top_up",
}


def _top_up(item_code, site_code, supplier_code=SUPPLIER):
    return {
        "supplier_code": supplier_code,
        "item_code": item_code,
        "site_code": site_code,
    }


def _group(site_code, origin, lines, value, outcome="would_create", order_nbr=None):
    return {
        "site_code": site_code,
        "supplier_code": SUPPLIER,
        "origin": origin,
        "lines": lines,
        "value": value,
        "outcome": outcome,
        "warehouse_code": "WHOLESALE",
        "vendor_code": "CONPERIPH",
        "order_nbr": order_nbr,
        "error": None,
    }


# T1 in the 159/S-A group after D (the lines ordered by item code),
# T2 in the 160/S-A group — the answer of a preview naming T1 and T2.
EXPECTED_BOTH = {
    "month": AS_OF_MONTH,
    "groups": [
        _group(SITE_159, ORIGIN_159, [dict(LINE_D), dict(LINE_T1)], "850.0"),
        _group(SITE_160, ORIGIN_160, [dict(LINE_T2)], "200.0"),
    ],
    "unreleasable": [],
    "total_value": "1050.0",
    "acumatica_available": True,
}


def _stand_in_line(item_code, site_code, due_in_days, quantity, unit_cost, value):
    """One line of the stand-in plan, every field the two consumers
    read (the release's and top_ups_by_supplier's) filled."""
    return {
        "item_code": item_code,
        "site_code": site_code,
        "position_units": 0,
        "reorder_point_units": 10,
        "daily_demand": 10.0,
        "due_in_days": due_in_days,
        "order_by": AS_OF,
        "lead_time_days": 10,
        "arrives_on": AS_OF,
        "raw_quantity": quantity,
        "quantity": quantity,
        "adjustments": None,
        "short_of_need": None,
        "terms": None,
        "unit_cost": unit_cost,
        "value": value,
        "arithmetic": None,
        "supplier_reason": None,
        "source": "reorder_point",
    }


def _stand_in_plan(horizon_days):
    """One fixed plan per horizon: at 7, S-A of acumatica with a
    minimum of 1000.00 and a shortfall of 600.00 and one due line D
    at site 159, 4 units at 100.00; at 30, the same plus S-A's lines
    T1 at 159 (due in 12 days, 3 units at 150.00) and T2 at 160 (due
    in 20 days, 2 units at 100.00)."""
    lines = [_stand_in_line(ITEM_D, SITE_159, 0, 4, 100.0, Decimal("400.00"))]
    if horizon_days == 30:
        lines += [
            _stand_in_line(ITEM_T1, SITE_159, 12, 3, 150.0, Decimal("450.00")),
            _stand_in_line(ITEM_T2, SITE_160, 20, 2, 100.0, Decimal("200.00")),
        ]
    total_value = sum(line["value"] for line in lines)
    return {
        "as_of": AS_OF,
        "horizon_days": horizon_days,
        "line_count": len(lines),
        "total_value": total_value,
        "suppliers": [
            {
                "supplier_code": SUPPLIER,
                "source_system": "acumatica",
                "vendor_code": "V-1225-A",
                "name": "Acme Peripherals",
                "line_count": len(lines),
                "total_value": total_value,
                "minimum": Decimal("1000.00"),
                "shortfall": Decimal("600.00"),
                "lines": lines,
            }
        ],
    }


def test_issue1225_freeform(monkeypatch, client) -> None:
    # Isolate the Acumatica environment, complete configuration,
    # writes on.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- The Acumatica client, replaced with fakes recording their
    # calls, the way test_issue1191_freeform replaces it.
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
                "order_nbr": f"PO-1225-{len(create_calls):03d}",
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

    # --- The seams.  The excess list answers nothing; the release's
    # data as-of answers a fixed date; the plan builder is a stand-in
    # recording each call's horizon (and as-of) and answering one
    # fixed plan per horizon.
    build_calls: list[tuple] = []
    # One preview that names top-ups asks the stand-in for the
    # release's own horizon build, then top_ups_by_supplier's horizon
    # build at the release's as-of and its look-ahead build (the
    # default 30) at the horizon plan's as-of.
    top_up_preview_calls = [(7, AS_OF), (7, AS_OF), (30, AS_OF)]

    def fake_build_supply_plan(horizon_days, as_of=None):
        build_calls.append((horizon_days, as_of))
        return _stand_in_plan(horizon_days)

    monkeypatch.setattr(supply_plan, "excess_ranked", lambda as_of=None: [])
    monkeypatch.setattr(release_module, "_data_as_of", lambda: AS_OF)
    monkeypatch.setattr(supply_plan, "build_supply_plan", fake_build_supply_plan)

    # --- The cap is on the entries as sent: 200 repeats of T1 plus
    # one T2 (201 entries) is 422 by request validation, even though
    # the repeats would count once — and the stand-in is not asked.
    over_as_sent = [_top_up(ITEM_T1, SITE_159) for _ in range(200)] + [
        _top_up(ITEM_T2, SITE_160)
    ]
    assert len(over_as_sent) == 201
    over_response = client.post(
        URL, json={"supplier_codes": [SUPPLIER], "top_ups": over_as_sent}
    )
    assert over_response.status_code == 422

    # 201 distinct entries: 422 the same way, still nothing built.
    over_distinct = client.post(
        URL,
        json={
            "supplier_codes": [SUPPLIER],
            "top_ups": [_top_up(f"E{i:03d}", SITE_159) for i in range(201)],
        },
    )
    assert over_distinct.status_code == 422

    # Neither refusal asked the stand-in anything.
    assert build_calls == []

    # --- At the cap: 199 repeats of T1 plus one T2 — 200 entries —
    # is accepted, T1 and T2 each added once, and the answer is the
    # same as naming each once.
    at_cap = [_top_up(ITEM_T1, SITE_159) for _ in range(199)] + [
        _top_up(ITEM_T2, SITE_160)
    ]
    assert len(at_cap) == 200
    at_cap_response = client.post(
        URL, json={"supplier_codes": [SUPPLIER], "top_ups": at_cap}
    )
    assert at_cap_response.status_code == 200, at_cap_response.text
    assert at_cap_response.json() == EXPECTED_BOTH

    # T1 and T2 each added once: 159/S-A holds D then T1, 160/S-A
    # holds T2, nothing else.
    groups = at_cap_response.json()["groups"]
    assert len(groups) == 2
    assert [line["item_code"] for line in groups[0]["lines"]] == [ITEM_D, ITEM_T1]
    assert [line["item_code"] for line in groups[1]["lines"]] == [ITEM_T2]
    assert build_calls == top_up_preview_calls

    # Naming each once answers exactly the same.
    once = client.post(
        URL,
        json={
            "supplier_codes": [SUPPLIER],
            "top_ups": [_top_up(ITEM_T1, SITE_159), _top_up(ITEM_T2, SITE_160)],
        },
    )
    assert once.status_code == 200, once.text
    assert once.json() == at_cap_response.json() == EXPECTED_BOTH
    assert build_calls == top_up_preview_calls * 2

    # --- The schema: top_ups carries maxItems 200, both in the
    # committed openapi.json and in app.openapi().
    committed_top_ups = json.loads(OPENAPI.read_text(encoding="utf-8"))["components"][
        "schemas"
    ]["OrderPlanReleaseIn"]["properties"]["top_ups"]
    assert committed_top_ups["maxItems"] == 200
    live_top_ups = app.openapi()["components"]["schemas"]["OrderPlanReleaseIn"][
        "properties"
    ]["top_ups"]
    assert live_top_ups["maxItems"] == 200
