"""Issue #1567 proof: the suggestion on each top-up supplier entry.

AC-1 — the suggestion: each supplier entry of
``GET /api/supply-plan/top-ups`` gains ``suggestion`` and
``suggestion_reason``.  The threshold N is the
``exceptions.order_minimum_due_days`` system setting (system scope,
number, default 2, min 0, max 7), read with ``setting_value`` from the
stored system settings ``top_ups_by_supplier`` already reads, exactly
the way the exceptions inbox reads it.  A line of the entry's group in
the horizon plan must be ordered when its ``due_in_days`` is at most
N; the reason names the first such line in the group's order.
``"wait"`` when no line of the group must be ordered, the reason "No
line must be ordered within N days." — written "within 1 day" when N
is 1 and "today" when N is 0, a whole N written without decimals;
``"add_top_ups"`` when one must and ``reaches_minimum`` is true, the
reason "<item_code> at <site_code> must be ordered by <order_by>, and
the top-ups reach the minimum." with ``order_by`` read ``YYYY-MM-DD``;
``"order_anyway"`` when one must and ``reaches_minimum`` is false, the
reason "...and the top-ups cannot reach the minimum."  Nothing else
changes: the rest of the answer, the suppliers listed and their order,
and the release, which reads the top-ups for the named top-ups —
locked by ``tests/test_issue1191_freeform.py`` passing unchanged.

AC-2 — the model and the contract:
``SupplyPlanTopUpSupplier`` gains ``suggestion`` and
``suggestion_reason``, and ``backend/openapi.json`` carries both
fields (checked against the live schema).

AC-4 — this proof, read through the app's test client the way
``tests/test_issue1191_freeform.py`` reads its route:
``build_supply_plan`` and ``excess_ranked`` in ``app.api.supply_plan``
and ``build_supply_plan`` in ``app.api.exceptions`` (the inbox imports
the builder by name) stood in the way 1191 does, each answering one
fixed plan per horizon, as-of 2026-09-30: the 7-day plan's groups, all
of acumatica at site 159 — S-W with a minimum of 1000.00, a total of
400.00 and lines W1 due in 4 days (2026-10-04) and W2 due in 6 days
(2026-10-06), 2 units at 100.00 each; S-T the same minimum and total
with lines T1 due in 0 days (2026-09-30) and T2 due in 3 days
(2026-10-03); S-O the same minimum and total with one line O1 due in
1 day (2026-10-01), 4 units at 100.00; and S-N with a minimum of
100.00 and a total of 400.00, so no shortfall — one line N1 due in 0
days.  The 30-day look-ahead holds the same plus S-W's line W3 due in
12 days worth 700.00, S-T's line T3 due in 12 days worth 700.00 and
S-O's line O2 due in 12 days worth 100.00.  The minimums are stored
with ``SupplierOrderMinimumPostgresRepository().upsert(...)`` the way
#1145's proof stores them.  Proved: with the default N of 2 the
top-ups route lists S-W, S-T and S-O — not S-N — S-W "wait" with "No
line must be ordered within 2 days.", S-T "add_top_ups" naming its
line due in 0 days and S-O "order_anyway" naming its line due in 1
day; ``GET /api/exceptions`` lists an ``order_below_minimum`` row for
exactly S-T and S-O.  With N stored as 5 by the admin, S-W becomes
"add_top_ups" naming its line due in 4 days (and the inbox raises it
too — the same stored threshold).  With N stored as 0, S-T stays
"add_top_ups" and S-O becomes "wait" with "No line must be ordered
today."  The stored minimums and settings are removed after the
proof.
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
import app.api.supply_plan as supply_plan
from app.db.connection import get_connection
from app.main import app
from app.repositories.supplier_order_minimums_postgres_repository import (
    SupplierOrderMinimumPostgresRepository,
)

URL = "/api/supply-plan/top-ups"
EXCEPTIONS_URL = "/api/exceptions"
ADMIN_TOKEN = "dev-secret-token:admin"
SETTING_KEY = "exceptions.order_minimum_due_days"
SOURCE_SYSTEM = "acumatica"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
SITE = "159"
AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()

MINIMUMS = (
    ("S-W", 1000.0),
    ("S-T", 1000.0),
    ("S-O", 1000.0),
    ("S-N", 100.0),
)


def _line(item, due_in_days, order_by, quantity, unit_cost, value):
    """One line of the stand-in plan, every field the two consumers
    read (top_ups_by_supplier's and the inbox's) filled."""
    return {
        "item_code": item,
        "site_code": SITE,
        "position_units": 0,
        "reorder_point_units": 10,
        "daily_demand": 10.0,
        "due_in_days": due_in_days,
        "order_by": order_by,
        "lead_time_days": 10,
        "arrives_on": order_by,
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


# The 7-day plan's groups, one source system: S-W due in 4 and 6
# days, S-T due in 0 and 3 days, S-O due in 1 day and S-N — no
# shortfall — due in 0 days.
GROUPS = (
    (
        "S-W",
        "Whiskey Trading",
        "V-S-W",
        Decimal("1000.00"),
        (
            ("W1", 4, date(2026, 10, 4), 2, 100.0, Decimal("200.00")),
            ("W2", 6, date(2026, 10, 6), 2, 100.0, Decimal("200.00")),
        ),
    ),
    (
        "S-T",
        "Tango Trading",
        "V-S-T",
        Decimal("1000.00"),
        (
            ("T1", 0, date(2026, 9, 30), 2, 100.0, Decimal("200.00")),
            ("T2", 3, date(2026, 10, 3), 2, 100.0, Decimal("200.00")),
        ),
    ),
    (
        "S-O",
        "Oscar Trading",
        "V-S-O",
        Decimal("1000.00"),
        (("O1", 1, date(2026, 10, 1), 4, 100.0, Decimal("400.00")),),
    ),
    (
        "S-N",
        "November Trading",
        "V-S-N",
        Decimal("100.00"),
        (("N1", 0, date(2026, 9, 30), 4, 100.0, Decimal("400.00")),),
    ),
)

# The look-ahead lines the 30-day plan holds beyond the 7-day lines:
# S-W's and S-T's due in 12 days worth 700.00, S-O's due in 12 days
# worth 100.00.
LOOKAHEAD = {
    "S-W": ("W3", 12, date(2026, 10, 12), 7, 100.0, Decimal("700.00")),
    "S-T": ("T3", 12, date(2026, 10, 12), 7, 100.0, Decimal("700.00")),
    "S-O": ("O2", 12, date(2026, 10, 12), 1, 100.0, Decimal("100.00")),
}


def _stand_in_plan(horizon_days):
    """One fixed plan per horizon: the 7-day plan's four groups, and
    the 30-day look-ahead the same groups plus each look-ahead line."""
    suppliers = []
    for supplier_code, name, vendor_code, minimum, lines_spec in GROUPS:
        lines = [_line(*spec) for spec in lines_spec]
        if horizon_days >= 30 and supplier_code in LOOKAHEAD:
            lines.append(_line(*LOOKAHEAD[supplier_code]))
        total_value = sum((line["value"] for line in lines), Decimal(0))
        shortfall = minimum - total_value
        suppliers.append(
            {
                "supplier_code": supplier_code,
                "source_system": SOURCE_SYSTEM,
                "vendor_code": vendor_code,
                "name": name,
                "line_count": len(lines),
                "total_value": total_value,
                "minimum": minimum,
                "shortfall": shortfall if shortfall > 0 else Decimal("0.00"),
                "lines": lines,
            }
        )
    return {
        "as_of": AS_OF,
        "horizon_days": horizon_days,
        "line_count": sum(group["line_count"] for group in suppliers),
        "total_value": sum((group["total_value"] for group in suppliers), Decimal(0)),
        "suppliers": suppliers,
    }


class FakeExcess:
    """The stand-in for ``excess_ranked`` in ``app.api.supply_plan``:
    answers nothing, counted — the top-ups route must read the excess
    list exactly once per call."""

    calls = 0

    def __call__(self, as_of=None):
        FakeExcess.calls += 1
        return []


def _top_up(item, due, order_by, quantity, unit_cost, value, holding_cost):
    return {
        "item_code": item,
        "site_code": SITE,
        "due_in_days": due,
        "order_by": order_by,
        "quantity": quantity,
        "unit_cost": unit_cost,
        "value": value,
        "holding_cost": holding_cost,
    }


# 700.00 x 0.25 x 12 / 365 = 5.75; 100.00 x 0.25 x 12 / 365 = 0.82.
TOP_UP_W3 = _top_up("W3", 12, "2026-10-12", 7, 100.0, "700.00", "5.75")
TOP_UP_T3 = _top_up("T3", 12, "2026-10-12", 7, 100.0, "700.00", "5.75")
TOP_UP_O2 = _top_up("O2", 12, "2026-10-12", 1, 100.0, "100.00", "0.82")

# S-W: minimum 1000.00, total 400.00, shortfall 600.00; W3 (700.00)
# reaches it.  No line due within the default N of 2: wait.
SUPPLIER_W_DEFAULT = {
    "supplier_code": "S-W",
    "source_system": SOURCE_SYSTEM,
    "vendor_code": "V-S-W",
    "name": "Whiskey Trading",
    "minimum": "1000.00",
    "total_value": "400.00",
    "shortfall": "600.00",
    "top_ups": [TOP_UP_W3],
    "top_up_value": "700.00",
    "reaches_minimum": True,
    "still_short": "0.00",
    "suggestion": "wait",
    "suggestion_reason": "No line must be ordered within 2 days.",
}
# S-T: the same; T3 (700.00) reaches it and T1 is due in 0 days —
# within the default N: add_top_ups, naming T1.
SUPPLIER_T = {
    "supplier_code": "S-T",
    "source_system": SOURCE_SYSTEM,
    "vendor_code": "V-S-T",
    "name": "Tango Trading",
    "minimum": "1000.00",
    "total_value": "400.00",
    "shortfall": "600.00",
    "top_ups": [TOP_UP_T3],
    "top_up_value": "700.00",
    "reaches_minimum": True,
    "still_short": "0.00",
    "suggestion": "add_top_ups",
    "suggestion_reason": (
        "T1 at 159 must be ordered by 2026-09-30, and the top-ups " "reach the minimum."
    ),
}
# S-O: the same; O2 alone (100.00 < 600.00) — still short 500.00 —
# and O1 is due in 1 day, within the default N: order_anyway.
SUPPLIER_O_DEFAULT = {
    "supplier_code": "S-O",
    "source_system": SOURCE_SYSTEM,
    "vendor_code": "V-S-O",
    "name": "Oscar Trading",
    "minimum": "1000.00",
    "total_value": "400.00",
    "shortfall": "600.00",
    "top_ups": [TOP_UP_O2],
    "top_up_value": "100.00",
    "reaches_minimum": False,
    "still_short": "500.00",
    "suggestion": "order_anyway",
    "suggestion_reason": (
        "O1 at 159 must be ordered by 2026-10-01, and the top-ups "
        "cannot reach the minimum."
    ),
}


def _body(suppliers):
    return {
        "as_of": AS_OF_STR,
        "horizon_days": 7,
        "lookahead_days": 30,
        "holding_rate": 0.25,
        "suppliers": suppliers,
    }


DEFAULT_BODY = _body([SUPPLIER_W_DEFAULT, SUPPLIER_T, SUPPLIER_O_DEFAULT])
# N stored as 5: W1 is due in 4 days, within it — S-W add_top_ups.
FIVE_BODY = _body(
    [
        {
            **SUPPLIER_W_DEFAULT,
            "suggestion": "add_top_ups",
            "suggestion_reason": (
                "W1 at 159 must be ordered by 2026-10-04, and the "
                "top-ups reach the minimum."
            ),
        },
        SUPPLIER_T,
        SUPPLIER_O_DEFAULT,
    ]
)
# N stored as 0: only a line due in 0 days must be ordered — S-T
# stays add_top_ups, S-W and S-O wait for "today".
ZERO_BODY = _body(
    [
        {
            **SUPPLIER_W_DEFAULT,
            "suggestion_reason": "No line must be ordered today.",
        },
        SUPPLIER_T,
        {
            **SUPPLIER_O_DEFAULT,
            "suggestion": "wait",
            "suggestion_reason": "No line must be ordered today.",
        },
    ]
)

_ROW_FIELDS = (
    "kind",
    "severity",
    "key",
    "item_code",
    "site_code",
    "title",
    "detail",
    "view",
    "detected_on",
)


def _row(supplier_code, name, severity, item, order_by):
    return {
        "kind": "order_below_minimum",
        "severity": severity,
        "key": f"order_below_minimum:{SOURCE_SYSTEM}:{supplier_code}",
        "item_code": None,
        "site_code": None,
        "title": "Order below supplier minimum",
        "detail": (
            f"The order to {name} comes to $400.00, $600.00 below its "
            f"minimum of $1,000.00; {item} at {SITE} must be ordered "
            f"by {order_by}."
        ),
        "view": "#/orders",
        "detected_on": AS_OF_STR,
    }


def _below_minimum_rows(body):
    return [
        {field: row[field] for field in _ROW_FIELDS}
        for row in body["rows"]
        if row["kind"] == "order_below_minimum"
    ]


def test_issue1567_freeform(client, monkeypatch) -> None:
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # --- The stand-ins, the way test_issue1191_freeform stands them
    #     in: one fixed plan per horizon, the excess list nothing.
    build_calls: list[tuple] = []

    def fake_build_supply_plan(horizon_days, as_of=None):
        build_calls.append((horizon_days, as_of))
        return _stand_in_plan(horizon_days)

    FakeExcess.calls = 0
    monkeypatch.setattr(supply_plan, "excess_ranked", FakeExcess())
    monkeypatch.setattr(supply_plan, "build_supply_plan", fake_build_supply_plan)
    # The inbox imports the builder by name, so it gets the stand-in
    # there, too.
    monkeypatch.setattr(exceptions, "build_supply_plan", fake_build_supply_plan)

    # --- The minimums, stored the way #1145's proof stores them. ---
    for supplier_code, amount in MINIMUMS:
        SupplierOrderMinimumPostgresRepository().upsert(
            SOURCE_SYSTEM, supplier_code, amount, "test"
        )

    # --- AC-2: backend/openapi.json carries both fields. ----------
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    schema = spec["components"]["schemas"]["SupplyPlanTopUpSupplier"]
    suggestion = schema["properties"]["suggestion"]
    assert suggestion["type"] == "string"
    assert suggestion["enum"] == ["wait", "add_top_ups", "order_anyway"]
    assert schema["properties"]["suggestion_reason"]["type"] == "string"
    assert "suggestion" in schema["required"]
    assert "suggestion_reason" in schema["required"]

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # The threshold's registry entry: system, number, default 2,
    # min 0, max 7.
    settings = admin.get("/api/settings")
    assert settings.status_code == 200, settings.text
    entry = next(e for e in settings.json() if e["key"] == SETTING_KEY)
    assert entry["scope"] == "system"
    assert entry["type"] == "number"
    assert entry["allowed"] is None
    assert entry["default"] == 2
    assert entry["min"] == 0
    assert entry["max"] == 7

    # --- The default N (2): S-W, S-T and S-O — not S-N. -------------
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == DEFAULT_BODY
    assert [s["supplier_code"] for s in response.json()["suppliers"]] == [
        "S-W",
        "S-T",
        "S-O",
    ]
    assert FakeExcess.calls == 1, "the excess list is read once per call"
    # One top-ups call: the horizon build (the as-of unresolved), then
    # the look-ahead build at the horizon plan's as-of.
    assert build_calls == [(7, None), (30, AS_OF)]

    # --- The inbox: an order_below_minimum row for exactly S-T and
    #     S-O — S-W's lines are due in 4 and 6 days, beyond the
    #     default N, and S-N has no shortfall.
    exc = client.get(EXCEPTIONS_URL)
    assert exc.status_code == 200, exc.text
    assert "order_minimums" not in exc.json()["sources_failed"]
    assert _below_minimum_rows(exc.json()) == [
        _row("S-T", "Tango Trading", "high", "T1", "2026-09-30"),
        _row("S-O", "Oscar Trading", "medium", "O1", "2026-10-01"),
    ]
    assert build_calls[-1] == (7, None)

    # --- N stored as 5: W1 is due in 4 days, within it — S-W
    #     becomes add_top_ups, naming its line due in 4 days.
    store = admin.put(f"/api/settings/{SETTING_KEY}", json={"value": 5})
    assert store.status_code == 200, store.text
    assert store.json()["value"] == 5

    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == FIVE_BODY
    assert FakeExcess.calls == 2

    # The inbox reads the same stored threshold: S-W is raised too,
    # after S-O (high before medium, then the key).
    exc = client.get(EXCEPTIONS_URL)
    assert exc.status_code == 200, exc.text
    assert _below_minimum_rows(exc.json()) == [
        _row("S-T", "Tango Trading", "high", "T1", "2026-09-30"),
        _row("S-O", "Oscar Trading", "medium", "O1", "2026-10-01"),
        _row("S-W", "Whiskey Trading", "medium", "W1", "2026-10-04"),
    ]

    # --- N stored as 0: only a line due in 0 days must be ordered —
    #     S-T stays add_top_ups, S-O becomes wait, "today".
    store = admin.put(f"/api/settings/{SETTING_KEY}", json={"value": 0})
    assert store.status_code == 200, store.text
    assert store.json()["value"] == 0

    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == ZERO_BODY
    assert FakeExcess.calls == 3

    # --- Clean-up: the stored minimums and settings are removed. ----
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM supplier_order_minimums")
            cur.execute("DELETE FROM settings")
        conn.commit()
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM supplier_order_minimums")
            assert cur.fetchone()[0] == 0
            cur.execute("SELECT COUNT(*) FROM settings")
            assert cur.fetchone()[0] == 0
    finally:
        conn.close()
