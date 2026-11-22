"""Issue #1191 proof: chosen top-ups join the order-plan release.

AC-1 — the shared top-ups answer: the body of
``GET /api/supply-plan/top-ups`` moved, unchanged, into
``app.api.supply_plan.top_ups_by_supplier(horizon_days, as_of=None)``
— the route answers ``SupplyPlanTopUpsResponse(**top_ups_by_supplier(
horizon_days))``, builds the horizon plan with
``build_supply_plan(horizon_days, as_of)`` and the look-ahead plan at
the horizon plan's as-of, through the module's own
``build_supply_plan`` and ``excess_ranked`` names — locked by
``tests/test_issue1145_freeform.py`` passing unchanged: its
``excess_ranked`` seam still counts exactly one read per call and its
bodies are byte-identical.

AC-2 — the release body: ``OrderPlanReleaseIn`` gains ``top_ups``,
the (default empty) list of at most 200 chosen top-ups, each exactly
``supplier_code``, ``item_code`` and ``site_code`` — each 1 to 64
characters, no NUL bytes, repeated entries counted once — and the
refusals: any other field in an entry (422), more than 200 entries
(422), and a ``supplier_code`` that is not one of ``supplier_codes``
(422, naming that code).

AC-3 — the release: ``order_plan_release(horizon_days,
supplier_codes, top_ups=())`` adds each chosen top-up the
``top_ups_by_supplier(horizon_days, as_of)`` suggestions hold at the
release's as-of (the same ``supplier_code``, ``item_code`` and
``site_code`` in a supplier entry whose ``source_system`` is
``acumatica``) as a line of its (site, supplier) group — the top-up's
quantity in whole units, its unit cost rounded exactly as a due
line's, its value added to the group's and the plan's value, the
group created with the due lines' origin tag when the supplier has no
due line at that site, and the group's lines ordered by item code —
and lists a chosen top-up the suggestions do not hold in
``unreleasable`` with its item code, its site code and the reason
"not a suggested top-up"; ``top_ups_by_supplier`` is called only when
a top-up is named, so a release that names none computes nothing new
and answers exactly as before.

AC-4 — this proof, read through the app's test client: the
Acumatica client's preview and create replaced the way
``tests/test_issue842_freeform.py`` replaces them, ``excess_ranked``
in ``app.api.supply_plan`` answering nothing, ``_data_as_of`` in
``app.supply.release`` answering 2026-11-02, and
``build_supply_plan`` in ``app.api.supply_plan`` a stand-in recording
each call's horizon and answering one fixed plan per horizon: at 7,
supplier ``S-A`` of ``acumatica`` with a minimum of 1000.00 and a
shortfall of 600.00 and one due line D at site 159, 4 units at
100.00; at 30, the same plus S-A's lines T1 at 159 (due in 12 days,
3 units at 150.00) and T2 at 160 (due in 20 days, 2 units at
100.00).  Proved: a preview naming S-A and no top-up answers one
group, 159/S-A with line D, nothing unreleasable and total value
400.00, the stand-in asked for horizon 7 only; a preview naming T1
and T2 answers the 159/S-A group with D then T1 (3 units at 150.00,
value 450.00) and a new 160/S-A group with T2 (2 units at 100.00)
under the origin tag "AIERP order plan 2026-11-02 site 160 vendor
S-A", total value 1050.00 — 200 repeats of T1 plus one T2 counting
as the same two top-ups; a preview naming T1 and an unsuggested item
U at 159 answers T1 in its group and U in ``unreleasable`` with the
reason "not a suggested top-up"; the refusals above answer 422
without asking the stand-in anything; an admin confirm naming T1
writes one ``plan_releases`` row for 159/S-A whose lines are D and
T1.
"""

import os
from datetime import date
from decimal import Decimal

import psycopg2
from fastapi.testclient import TestClient

import app.api.supply_plan as supply_plan
import app.supply.release as release_module
from app.db.connection import get_connection_params
from app.feed.acumatica_client import (
    AcumaticaClient,
    AcumaticaWritesDisabledError,
)
from app.main import app

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

ADMIN_TOKEN = "dev-secret-token:admin"
ADMIN_DETAIL = "Role 'admin' is required to perform this action."

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
ITEM_U = "U"
ORIGIN_159 = f"AIERP order plan {AS_OF_STR} site {SITE_159} vendor {SUPPLIER}"
ORIGIN_160 = f"AIERP order plan {AS_OF_STR} site {SITE_160} vendor {SUPPLIER}"
NOT_SUGGESTED = "not a suggested top-up"

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


def _body(groups, unreleasable, total_value):
    return {
        "month": AS_OF_MONTH,
        "groups": groups,
        "unreleasable": unreleasable,
        "total_value": total_value,
        "acumatica_available": True,
    }


EXPECTED_NO_TOP_UPS = _body(
    [_group(SITE_159, ORIGIN_159, [dict(LINE_D)], "400.0")], [], "400.0"
)
EXPECTED_BOTH = _body(
    [
        _group(SITE_159, ORIGIN_159, [dict(LINE_D), dict(LINE_T1)], "850.0"),
        _group(SITE_160, ORIGIN_160, [dict(LINE_T2)], "200.0"),
    ],
    [],
    "1050.0",
)
EXPECTED_UNSUGGESTED = _body(
    [_group(SITE_159, ORIGIN_159, [dict(LINE_D), dict(LINE_T1)], "850.0")],
    [{"item_code": ITEM_U, "site_code": SITE_159, "reason": NOT_SUGGESTED}],
    "850.0",
)
EXPECTED_CONFIRM = _body(
    [
        _group(
            SITE_159,
            ORIGIN_159,
            [dict(LINE_D), dict(LINE_T1)],
            "850.0",
            outcome="created",
            order_nbr="PO-1191-001",
        )
    ],
    [],
    "850.0",
)


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
                "vendor_code": "V-1191-A",
                "name": "Acme Peripherals",
                "line_count": len(lines),
                "total_value": total_value,
                "minimum": Decimal("1000.00"),
                "shortfall": Decimal("600.00"),
                "lines": lines,
            }
        ],
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


def test_issue1191_freeform(monkeypatch, client) -> None:
    # Isolate the Acumatica environment, complete configuration,
    # writes on.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- The Acumatica client, replaced with a fake recording its
    # calls, the way test_issue842_freeform replaces it.
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
                "order_nbr": f"PO-1191-{len(create_calls):03d}",
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

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # --- No top-ups: the answer is exactly as before, and the
    # stand-in is asked for horizon 7 only — nothing new computed.
    no_top_ups = client.post(URL, json={"supplier_codes": [SUPPLIER]})
    assert no_top_ups.status_code == 200, no_top_ups.text
    assert no_top_ups.json() == EXPECTED_NO_TOP_UPS
    assert build_calls == [(7, AS_OF)]
    assert preview_calls[-1] == [
        {
            "site_id": SITE_159,
            "vendor_id": SUPPLIER,
            "origin": ORIGIN_159,
            "lines": [{"item_code": ITEM_D, "quantity": 4, "unit_cost": 100.0}],
        }
    ]

    # --- Both top-ups: T1 joins the 159/S-A group after D (the lines
    # ordered by item code), T2 creates the 160/S-A group with the
    # due lines' origin tag, and the values add to the groups' and
    # the plan's.
    both = client.post(
        URL,
        json={
            "supplier_codes": [SUPPLIER],
            "top_ups": [_top_up(ITEM_T1, SITE_159), _top_up(ITEM_T2, SITE_160)],
        },
    )
    assert both.status_code == 200, both.text
    assert both.json() == EXPECTED_BOTH
    assert build_calls == [(7, AS_OF)] + top_up_preview_calls
    assert preview_calls[-1] == [
        {
            "site_id": SITE_159,
            "vendor_id": SUPPLIER,
            "origin": ORIGIN_159,
            "lines": [
                {"item_code": ITEM_D, "quantity": 4, "unit_cost": 100.0},
                {"item_code": ITEM_T1, "quantity": 3, "unit_cost": 150.0},
            ],
        },
        {
            "site_id": SITE_160,
            "vendor_id": SUPPLIER,
            "origin": ORIGIN_160,
            "lines": [{"item_code": ITEM_T2, "quantity": 2, "unit_cost": 100.0}],
        },
    ]

    # Repeated entries count once: 200 repeats of T1 plus one T2 are
    # the same two top-ups — accepted (at most 200 top-ups), each
    # added once.
    repeated = [_top_up(ITEM_T1, SITE_159) for _ in range(199)] + [
        _top_up(ITEM_T2, SITE_160)
    ]
    assert len(repeated) == 200
    repeated_response = client.post(
        URL, json={"supplier_codes": [SUPPLIER], "top_ups": repeated}
    )
    assert repeated_response.status_code == 200, repeated_response.text
    assert repeated_response.json() == EXPECTED_BOTH
    assert build_calls == [(7, AS_OF)] + top_up_preview_calls * 2

    # --- A top-up not suggested: T1 in its group, U in unreleasable
    # with the reason "not a suggested top-up" — and nothing else.
    unsuggested = client.post(
        URL,
        json={
            "supplier_codes": [SUPPLIER],
            "top_ups": [_top_up(ITEM_T1, SITE_159), _top_up(ITEM_U, SITE_159)],
        },
    )
    assert unsuggested.status_code == 200, unsuggested.text
    assert unsuggested.json() == EXPECTED_UNSUGGESTED
    assert build_calls == [(7, AS_OF)] + top_up_preview_calls * 3

    # --- The refusals: 422, without asking the stand-in anything.
    calls_before = list(build_calls)

    extra_field = client.post(
        URL,
        json={
            "supplier_codes": [SUPPLIER],
            "top_ups": [
                {
                    "supplier_code": SUPPLIER,
                    "item_code": ITEM_T1,
                    "site_code": SITE_159,
                    "quantity": 3,
                }
            ],
        },
    )
    assert extra_field.status_code == 422

    too_many = client.post(
        URL,
        json={
            "supplier_codes": [SUPPLIER],
            "top_ups": [_top_up(f"E{i:03d}", SITE_159) for i in range(201)],
        },
    )
    assert too_many.status_code == 422

    # The error names the code that is not one of supplier_codes.
    unknown_supplier = client.post(
        URL,
        json={
            "supplier_codes": [SUPPLIER],
            "top_ups": [_top_up(ITEM_T1, SITE_159, supplier_code="S-X")],
        },
    )
    assert unknown_supplier.status_code == 422
    detail = unknown_supplier.json()["detail"]
    assert isinstance(detail, list) and detail
    assert any("S-X" in entry["msg"] for entry in detail)

    # A NUL byte in any of the entry's fields is refused the same
    # way the supplier codes are.
    nul = client.post(
        URL,
        json={
            "supplier_codes": [SUPPLIER],
            "top_ups": [
                {
                    "supplier_code": SUPPLIER,
                    "item_code": f"{ITEM_T1}\x00",
                    "site_code": SITE_159,
                }
            ],
        },
    )
    assert nul.status_code == 422

    assert build_calls == calls_before

    # --- The confirm: an admin confirm naming T1 writes one
    # plan_releases row for 159/S-A whose lines are D and T1.
    confirm = admin.post(
        URL,
        json={
            "supplier_codes": [SUPPLIER],
            "top_ups": [_top_up(ITEM_T1, SITE_159)],
            "confirm": True,
        },
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json() == EXPECTED_CONFIRM
    # The fake's create method called once, with that group.
    assert create_calls == [
        [
            {
                "site_id": SITE_159,
                "vendor_id": SUPPLIER,
                "origin": ORIGIN_159,
                "lines": [
                    {"item_code": ITEM_D, "quantity": 4, "unit_cost": 100.0},
                    {"item_code": ITEM_T1, "quantity": 3, "unit_cost": 150.0},
                ],
            }
        ]
    ]

    # One plan_releases row, outcome created, the exact lines.
    rows = _plan_release_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["month"] == AS_OF_MONTH
    assert row["site_code"] == SITE_159
    assert row["supplier_code"] == SUPPLIER
    assert row["origin"] == ORIGIN_159
    assert row["outcome"] == "created"
    assert row["order_nbr"] == "PO-1191-001"
    assert row["warehouse_code"] == "WHOLESALE"
    assert row["vendor_code"] == "CONPERIPH"
    assert row["line_count"] == 2
    assert row["value"] == Decimal("850.0")
    assert row["lines"] == [dict(LINE_D), dict(LINE_T1)]
    assert row["actor"] == "admin"
    assert row["error"] is None

    # The confirm is admin only: a non-admin confirm answers 403 and
    # writes nothing.
    refused = client.post(
        URL,
        json={
            "supplier_codes": [SUPPLIER],
            "top_ups": [_top_up(ITEM_T1, SITE_159)],
            "confirm": True,
        },
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == ADMIN_DETAIL
    assert len(_plan_release_rows()) == 1
