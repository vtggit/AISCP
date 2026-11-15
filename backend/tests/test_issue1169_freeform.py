"""Issue #1169 proof: the orders below their supplier minimum on the
triage list.

``GET /api/exceptions`` gains the order minimums as its fourteenth
source, listed "order_minimums" when it fails: the source reads the
stored ``supplier_order_minimums`` once per call, through the
module attribute ``_supplier_order_minimum_repo`` (a
``SupplierOrderMinimumPostgresRepository``) — when none is stored it
adds no row and builds no plan — and otherwise builds the order plan
once, ``build_supply_plan(7)`` through the module's own name (7 the
default horizon of ``GET /api/supply-plan`` and of the Order plan
screen).  One row per supplier group of the plan that carries a
``minimum`` and a ``shortfall`` above 0 and holds a line whose
``due_in_days`` is at most the
``exceptions.order_minimum_due_days`` system setting (system scope,
number, default 2, min 0, max 7 — applied with ``setting_value`` on
the stored system settings the list already reads once per call, the
way ``exceptions.release_approval_days`` is applied, so a missing or
out-of-range stored value, or a failed settings read, reads 2): kind
"order_below_minimum", severity "high" when one of the group's lines
has a ``due_in_days`` of 0, else "medium", the key
"order_below_minimum:<source_system>:<supplier_code>",
``item_code`` and ``site_code`` null, title "Order below supplier
minimum", the detail "The order to <name> comes to <total_value>,
<shortfall> below its minimum of <minimum>; <item_code> at
<site_code> must be ordered by <order_by>." (the name the group's
``name``, else its ``supplier_code``; the line named the group's
first — its lines are ordered by ``due_in_days``, then item, then
site — the amounts dollars and cents with thousands separators,
``order_by`` read ``YYYY-MM-DD``), view "#/orders" and
``detected_on`` the plan's ``as_of`` (the plan's ``as_of`` is the
rows' ``detected_on`` only, never the response's ``as_of``).  The
group without a supplier has no minimum, so it never qualifies.  The
kind stands directly after "sole_source_short" in ``_KIND_ORDER`` —
the last kind.  A failure of the minimums read or of the plan build
lists "order_minimums" in ``sources_failed`` with the other sources
still answering, status 200, and the route never answers 500.
Every other kind, source and field is unchanged.

Everything is read through the app's test client the way
``test_issue1089_freeform`` reads the triage list, with
``build_supply_plan`` in ``app.api.exceptions`` replaced by a
counting stand-in that records each call's horizon and answers one
fixed plan: ``as_of`` the date 2026-11-02, ``horizon_days`` 7, and
these ``suppliers``, each group a dict with ``supplier_code``,
``source_system``, ``name``, ``total_value``, ``minimum`` and
``shortfall`` (Decimal or null) and ``lines``, each line a dict with
``item_code``, ``site_code``, ``due_in_days`` and ``order_by`` (a
date):

* S-A (acumatica, "Alpha Supply") — total 1800.00, minimum 2500.00,
  shortfall 700.00; lines X at 159 due in 0 days (order by
  2026-11-02), then Z at 159 due in 5 days (2026-11-07);
* S-D (acumatica, "Delta") — total 400.00, minimum 100.00, shortfall
  0.00; line W at 159 due in 0 days;
* S-E (acumatica, "Echo") — total 50.00, minimum and shortfall
  null; line Y at 159 due in 0 days;
* S-B (acumatica, no name) — total 200.00, minimum 1000.00, shortfall
  800.00; line U at 159 due in 2 days (2026-11-04);
* S-C (acumatica, "Gamma") — total 300.00, minimum 1000.00, shortfall
  700.00; line V at 159 due in 3 days (2026-11-05);
* the group without a supplier — total 10.00, minimum and shortfall
  null; line Q at 159 due in 0 days.

Proved: with no row in ``supplier_order_minimums`` the answer is 200
and holds no ``order_below_minimum`` row, ``order_minimums`` is not
in ``sources_failed`` and the stand-in is never called; after
``SupplierOrderMinimumPostgresRepository().upsert("acumatica", "S-A",
2500, "test")`` (the stored row opens the source; the stand-in's
groups carry the amounts) each call calls the stand-in once, with
horizon 7, and the answer holds exactly two rows, in this order —
S-A: severity "high", the key, null ``item_code`` and ``site_code``,
the title, the view, ``detected_on`` 2026-11-02 and the detail
"The order to Alpha Supply comes to $1,800.00, $700.00 below its
minimum of $2,500.00; X at 159 must be ordered by 2026-11-02.", and
S-B: severity "medium" and the detail "The order to S-B comes to
$200.00, $800.00 below its minimum of $1,000.00; U at 159 must be
ordered by 2026-11-04." — no row for S-C (its line is due in 3
days), S-D (shortfall 0), S-E (no minimum) or the group without a
supplier.  After the admin stores
``exceptions.order_minimum_due_days`` 3 with
``PUT /api/settings/exceptions.order_minimum_due_days``, the way
``test_issue1145_freeform`` stores its setting, S-C is raised too —
severity "medium" and the detail "The order to Gamma comes to
$300.00, $700.00 below its minimum of $1,000.00; V at 159 must be
ordered by 2026-11-05." — and with 0 stored only S-A is raised; the
registry entry holds the scope, type, default, bounds and
description stated above.  With the stand-in raising,
"order_minimums" is in ``sources_failed`` and the answer is 200;
with ``_supplier_order_minimum_repo`` replaced by a stand-in whose
``list`` raises, "order_minimums" is in ``sources_failed``, the
answer is 200 and the plan stand-in is never called.
"""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
from app.main import app
from app.repositories.supplier_order_minimums_postgres_repository import (
    SupplierOrderMinimumPostgresRepository,
)

URL = "/api/exceptions"
ADMIN_TOKEN = "dev-secret-token:admin"
SETTING_KEY = "exceptions.order_minimum_due_days"
SETTING_DESCRIPTION = (
    "Days within which a line of an order below its supplier's "
    "minimum must be ordered for the order to appear in the "
    "exceptions inbox."
)
SOURCE_SYSTEM = "acumatica"
SITE = "159"


def _line(item_code, due_in_days, order_by):
    """One fixed plan line, in the shape the plan's group holds."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "due_in_days": due_in_days,
        "order_by": order_by,
    }


def _group(supplier_code, name, total_value, minimum, shortfall, lines):
    """One fixed supplier group of the plan's answer."""
    return {
        "supplier_code": supplier_code,
        "source_system": SOURCE_SYSTEM,
        "name": name,
        "total_value": total_value,
        "minimum": minimum,
        "shortfall": shortfall,
        "lines": lines,
    }


# The stand-in's fixed plan: as_of 2026-11-02, horizon 7.
PLAN = {
    "as_of": date(2026, 11, 2),
    "horizon_days": 7,
    "suppliers": [
        _group(
            "S-A",
            "Alpha Supply",
            Decimal("1800.00"),
            Decimal("2500.00"),
            Decimal("700.00"),
            [
                _line("X", 0, date(2026, 11, 2)),
                _line("Z", 5, date(2026, 11, 7)),
            ],
        ),
        _group(
            "S-D",
            "Delta",
            Decimal("400.00"),
            Decimal("100.00"),
            Decimal("0.00"),
            [_line("W", 0, date(2026, 11, 2))],
        ),
        _group(
            "S-E",
            "Echo",
            Decimal("50.00"),
            None,
            None,
            [_line("Y", 0, date(2026, 11, 2))],
        ),
        _group(
            "S-B",
            None,
            Decimal("200.00"),
            Decimal("1000.00"),
            Decimal("800.00"),
            [_line("U", 2, date(2026, 11, 4))],
        ),
        _group(
            "S-C",
            "Gamma",
            Decimal("300.00"),
            Decimal("1000.00"),
            Decimal("700.00"),
            [_line("V", 3, date(2026, 11, 5))],
        ),
        _group(
            None,
            None,
            Decimal("10.00"),
            None,
            None,
            [_line("Q", 0, date(2026, 11, 2))],
        ),
    ],
}


class _PlanStandIn:
    """The stand-in for build_supply_plan: each call's horizon is
    recorded, the fixed plan is handed back unchanged."""

    def __init__(self, plan):
        self.horizons = []
        self._plan = plan

    def __call__(self, horizon_days, as_of=None):
        self.horizons.append(horizon_days)
        return self._plan


def _answer_rows(body):
    return [row for row in body["rows"] if row["kind"] == "order_below_minimum"]


def test_issue1169_freeform(client, monkeypatch):
    standin = _PlanStandIn(PLAN)
    monkeypatch.setattr(exceptions, "build_supply_plan", standin)

    # -- None stored: no row in supplier_order_minimums — no
    #    order_below_minimum row, the source is not failed and the
    #    plan is never built. ----------------------------------------
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert _answer_rows(body) == []
    assert "order_minimums" not in body["sources_failed"]
    assert standin.horizons == [], "no stored minimum: the plan is never built"

    # -- Raised: the stored minimum opens the source; each call
    #    builds the plan once, with horizon 7. -----------------------
    SupplierOrderMinimumPostgresRepository().upsert(SOURCE_SYSTEM, "S-A", 2500, "test")
    response = client.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "order_minimums" not in body["sources_failed"]
    assert standin.horizons == [
        7
    ], "build_supply_plan is called once per call, with horizon 7"
    rows = _answer_rows(body)
    assert [row["key"] for row in rows] == [
        "order_below_minimum:acumatica:S-A",
        "order_below_minimum:acumatica:S-B",
    ], "S-C (due in 3 days), S-D (shortfall 0), S-E (no minimum) and "
    "the group without a supplier are never raised"
    row = rows[0]
    assert row["kind"] == "order_below_minimum"
    assert row["severity"] == "high"
    assert row["item_code"] is None
    assert row["site_code"] is None
    assert row["title"] == "Order below supplier minimum"
    assert row["view"] == "#/orders"
    assert row["detected_on"] == "2026-11-02"
    assert row["detail"] == (
        "The order to Alpha Supply comes to $1,800.00, $700.00 below "
        "its minimum of $2,500.00; X at 159 must be ordered by "
        "2026-11-02."
    )
    row = rows[1]
    assert row["severity"] == "medium"
    assert row["item_code"] is None
    assert row["site_code"] is None
    assert row["title"] == "Order below supplier minimum"
    assert row["view"] == "#/orders"
    assert row["detected_on"] == "2026-11-02"
    assert row["detail"] == (
        "The order to S-B comes to $200.00, $800.00 below its minimum "
        "of $1,000.00; U at 159 must be ordered by 2026-11-04."
    )
    # A second call builds the plan once more, with the same answer.
    again = client.get(URL)
    assert again.status_code == 200
    assert standin.horizons == [7, 7], "one plan per call"
    assert [r["key"] for r in _answer_rows(again.json())] == [
        "order_below_minimum:acumatica:S-A",
        "order_below_minimum:acumatica:S-B",
    ]

    # -- The setting: the admin stores 3 — S-C is raised too. -------
    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    store = admin.put(f"/api/settings/{SETTING_KEY}", json={"value": 3})
    assert store.status_code == 200, store.text
    assert store.json()["value"] == 3

    # The registry entry: the scope, type, default, bounds and
    # description stated above.
    settings = admin.get("/api/settings")
    assert settings.status_code == 200, settings.text
    entry = next(e for e in settings.json() if e["key"] == SETTING_KEY)
    assert entry["scope"] == "system"
    assert entry["type"] == "number"
    assert entry["allowed"] is None
    assert entry["default"] == 2
    assert entry["min"] == 0
    assert entry["max"] == 7
    assert entry["description"] == SETTING_DESCRIPTION

    body = client.get(URL).json()
    rows = _answer_rows(body)
    assert [row["key"] for row in rows] == [
        "order_below_minimum:acumatica:S-A",
        "order_below_minimum:acumatica:S-B",
        "order_below_minimum:acumatica:S-C",
    ]
    s_c = rows[2]
    assert s_c["severity"] == "medium"
    assert s_c["detail"] == (
        "The order to Gamma comes to $300.00, $700.00 below its "
        "minimum of $1,000.00; V at 159 must be ordered by "
        "2026-11-05."
    )

    # -- 0 stored: only the line due in 0 days qualifies — S-A alone.
    store = admin.put(f"/api/settings/{SETTING_KEY}", json={"value": 0})
    assert store.status_code == 200, store.text
    body = client.get(URL).json()
    assert [row["key"] for row in _answer_rows(body)] == [
        "order_below_minimum:acumatica:S-A",
    ]

    # -- A failure: the plan stand-in raises — status 200,
    #    "order_minimums" in sources_failed, no row of the kind. ----
    def _raising_plan(*args, **kwargs):
        raise RuntimeError("the order plan is down")

    monkeypatch.setattr(exceptions, "build_supply_plan", _raising_plan)
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert "order_minimums" in body["sources_failed"]
    assert _answer_rows(body) == []

    # -- A failure: the minimums read raises — status 200,
    #    "order_minimums" in sources_failed and the plan is never
    #    built. -------------------------------------------------------
    standin.horizons = []
    monkeypatch.setattr(exceptions, "build_supply_plan", standin)

    class _BrokenMinimums:
        def list(self):
            raise RuntimeError("the minimums are down")

    monkeypatch.setattr(exceptions, "_supplier_order_minimum_repo", _BrokenMinimums())
    response = client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert "order_minimums" in body["sources_failed"]
    assert _answer_rows(body) == []
    assert (
        standin.horizons == []
    ), "the plan is never built when the minimums read fails"

    # -- The order: the kind stands directly after sole_source_short
    #    — the last kind. --------------------------------------------
    assert (
        exceptions._KIND_ORDER["order_below_minimum"]
        == exceptions._KIND_ORDER["sole_source_short"] + 1
    )
