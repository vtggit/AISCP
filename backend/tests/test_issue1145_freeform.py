"""Issue #1145 proof: the top-ups of the order plan below its minimum.

AC-1 — the setting and the route: ``supply_plan.top_up_lookahead_days``
is a registered system setting (number, default 30, min 1, max 90) —
proven in ``tests/test_issue274_freeform.py`` and
``tests/test_issue502_freeform.py``, which list every registered
setting — and ``GET /api/supply-plan/top-ups?horizon_days=`` answers,
for any signed-in user (``horizon_days`` 1 to 60, default 7, exactly
as ``GET /api/supply-plan`` takes it; anything else 422), the plan's
``as_of``, ``horizon_days``, ``lookahead_days`` (the setting's
effective value: the stored value rounded down to a whole number of
days, else the default) and ``holding_rate`` (the effective
``replenishment.holding_rate`` the recommendations use), plus
``suppliers``: one entry per supplier group of the ``horizon_days``
order plan that has a stored minimum and a shortfall above 0, as
``GET /api/supply-plan`` reports them (#1133), in the plan's group
order.  An entry holds the group's ``supplier_code``,
``source_system``, ``vendor_code``, ``name``, ``minimum``,
``total_value`` and ``shortfall``, then ``top_ups``,
``top_up_value``, ``reaches_minimum`` and ``still_short``.  The
candidates are the lines of the order plan built for
``lookahead_days`` (``build_supply_plan(lookahead_days)`` with the
same as-of) of the same ``supplier_code`` and ``source_system`` whose
``due_in_days`` is above ``horizon_days``, whose ``value`` is not null
and whose (``item_code``, ``site_code``) is not on the excess list —
``excess_ranked`` read once per call, through the module's own name;
they are taken by ``due_in_days``, then ``item_code``, then
``site_code``, each added until the sum of the values taken is at
least the shortfall, the candidate that reaches it included and none
after it.  A top-up holds the look-ahead line's ``item_code``,
``site_code``, ``due_in_days``, ``order_by``, ``quantity``,
``unit_cost`` and ``value`` plus ``holding_cost`` (value ×
``holding_rate`` × ``due_in_days`` / 365, rounded to two places half
up); ``top_up_value`` is the top-ups' values summed (0 with none),
``reaches_minimum`` true when ``total_value`` plus ``top_up_value`` is
at least the minimum, and ``still_short`` the minimum minus the two
when that is above 0, else 0 — the money serialized the way the plan
serializes ``total_value``, rounded to two places half up.  The route
is read-only; every other route is unchanged — the existing proofs
lock that down.

AC-2 — this proof: the plan's sources seeded the way
``tests/test_issue1076_freeform.py`` seeds a plan (fixed dates, raw
INSERTs: 360 days of demand a day per item, three identical 50-day
lead-time observations, the on-hand that sets each line's due date,
the unit cost and one default terms row per item — every item-site in
``acumatica`` at site 159).  Supplier ``S-A`` has a stored minimum of
2500 and a 7-day plan line A worth 1800.00 — shortfall 700.00 — and
its lines beyond 7 days: X due in 10 days worth 400.00, Y due in 12
days worth 500.00 (on the excess list), Z due in 20 days worth
450.00, V due in 25 days without a unit cost and W due in 40 days
worth 300.00.  Supplier ``S-B`` has a minimum of 1000 and a plan line
B worth 200.00 — shortfall 800.00 — and one later line, U, due in 15
days worth 300.00.  Supplier ``S-C`` has a minimum of 100 and a plan
line C worth 400.00 (no shortfall); supplier ``S-D`` has no minimum.
The excess list is replaced through ``supply_plan.excess_ranked``
(counting its calls), the minimums are stored with
``SupplierOrderMinimumPostgresRepository().upsert(...)``, and the
route is read with a signed-in development token.  Proved: with the
defaults the answer's ``horizon_days`` is 7, ``lookahead_days`` 30 and
``holding_rate`` 0.25, and ``suppliers`` lists S-A then S-B and
nothing else — S-A's top-ups are X then Z (holding costs 2.74 and
6.16, each top-up's ``due_in_days``, ``order_by``, ``quantity``,
``unit_cost`` and ``value`` equal the look-ahead plan's line),
``top_up_value`` 850.00, ``reaches_minimum`` true and ``still_short``
0; S-B's top-ups are U alone (holding cost 3.08), ``top_up_value``
300.00, ``reaches_minimum`` false and ``still_short`` 500.00; Y never
comes up (excess), V never comes up (no value) and W never comes up
(beyond the 30-day look-ahead); ``excess_ranked`` is read exactly
once per call.  After the admin stores the setting as 15, S-A's
top-ups are X alone, ``reaches_minimum`` false and ``still_short``
300.00, and S-B's are still U.  ``horizon_days`` 0, 61 and ``abc``
each answer 422; without a token the route answers 401;
``GET /api/settings`` holds the setting with the default 30; the row
counts of ``supplier_order_minimums`` and ``settings`` are the same
before and after the calls that are not the admin's store.

Identity — the proof also runs as a NON-ADMIN (role "user"): a second
pass in the same test after a full reload, with the page booted the
production way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent as
its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer — sees the top-ups page
still render (200 with the same body, the route open to any signed-in
user, no admin-only control on it) while no token is still a 401.
"""

import os
from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

import app.api.supply_plan as supply_plan
from app.db.connection import get_connection
from app.main import app
from app.repositories.supplier_order_minimums_postgres_repository import (
    SupplierOrderMinimumPostgresRepository,
)

URL = "/api/supply-plan/top-ups"
PLAN_URL = "/api/supply-plan"
ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

SOURCE_SYSTEM = "acumatica"
SITE = "159"
SETTING_KEY = "supply_plan.top_up_lookahead_days"
SETTING_DESCRIPTION = (
    "How many days ahead the order plan looks for items to top up an "
    "order that is below its supplier's minimum."
)

# The resolved as-of: the latest demand date, earlier than today.
AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)
SYNC_AS_OF = "2026-09-30T00:00:00+00:00"

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
LEAD_SQL = (
    "INSERT INTO lead_time_observations (id, business_key, supplier_code, "
    "item_code, ordered_on, received_on, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
ITEM_SITE_SQL = (
    "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
    "reorder_point, safety_stock, max_qty, order_qty, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
ON_HAND_SQL = (
    "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
    "as_of, source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)
TERMS_SQL = (
    "INSERT INTO supplier_terms (id, business_key, source_system, "
    "item_code, supplier_code, min_order_qty, max_order_qty, lot_size, "
    "lead_time_days, is_default) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
SUPPLIER_SQL = (
    "INSERT INTO suppliers (id, supplier_code, vendor_code, name, status, "
    "source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)

# The plan's items, all of the source system acumatica at site 159:
# (item, supplier, daily demand, reorder point, on hand, unit cost or
# None, terms).  The demand is 0.2 a day (reorder point 10, d x 50
# days) except Y at 0.25 a day (reorder point 13), so the on hand
# sets the due date: A 0, B 0, C 0, D 0 (within the 7-day horizon),
# X 10, Y 12, U 15, Z 20, V 25 and W 40.  The raw quantity is the EOQ
# at the unit cost (18 at 100, 25 at 50, 6 at no cost) fitted to the
# one terms row: A and D stand at 18, the rest capped at their
# maximum.
ITEMS = (
    # S-A: line A within the horizon (18 x 100.00 = 1800.00) and the
    # lines beyond it.
    ("A", "S-A", 0.2, 10.0, 0.0, 100.0, (10, None, None, 50, True)),
    ("X", "S-A", 0.2, 10.0, 12.0, 100.0, (None, 4, None, 50, True)),
    ("Y", "S-A", 0.25, 13.0, 16.0, 100.0, (None, 5, None, 50, True)),
    ("Z", "S-A", 0.2, 10.0, 14.0, 50.0, (None, 9, None, 50, True)),
    ("V", "S-A", 0.2, 10.0, 15.0, None, (None, None, None, 50, True)),
    ("W", "S-A", 0.2, 10.0, 18.0, 100.0, (None, 3, None, 50, True)),
    # S-B: line B within the horizon (2 x 100.00 = 200.00), then U.
    ("B", "S-B", 0.2, 10.0, 0.0, 100.0, (None, 2, None, 50, True)),
    ("U", "S-B", 0.2, 10.0, 13.0, 100.0, (None, 3, None, 50, True)),
    # S-C: line C within the horizon (4 x 100.00 = 400.00).
    ("C", "S-C", 0.2, 10.0, 0.0, 100.0, (None, 4, None, 50, True)),
    # S-D: line D within the horizon (18 x 100.00 = 1800.00), no
    # stored minimum.
    ("D", "S-D", 0.2, 10.0, 0.0, 100.0, (10, None, None, 50, True)),
)
SUPPLIERS = ("S-A", "S-B", "S-C", "S-D")

MINIMUMS = (
    ("S-A", 2500.0),  # 2500 - 1800.00 = 700.00 short.
    ("S-B", 1000.0),  # 1000 - 200.00 = 800.00 short.
    ("S-C", 100.0),  # 400.00 >= 100: no shortfall.
)


def _seed(cur) -> None:
    """The fixed-date rows, the way the issue 1076 proof seeds a plan:
    per item, 360 days of its demand a day, three identical 50-day
    lead-time observations (lead time 50, no variation), the on hand
    that sets the due date and the unit cost; one default terms row
    per item of the item's supplier."""
    for supplier in SUPPLIERS:
        cur.execute(
            SUPPLIER_SQL,
            (
                f"id-sp-1145-{supplier}",
                supplier,
                f"V-{supplier}",
                f"{supplier} Trading Co.",
                "active",
                SOURCE_SYSTEM,
            ),
        )
    for item, supplier, daily, reorder_point, on_hand, unit_cost, terms in ITEMS:
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-1145-{item}-{i}",
                    f"de-1145-{item}-{i}",
                    item,
                    SITE,
                    daily,
                    (FIRST_DAY + timedelta(days=i)).isoformat(),
                    "shipment",
                    SOURCE_SYSTEM,
                )
                for i in range(360)
            ],
        )
        base = date(2026, 7, 10)
        for i in range(3):
            ordered = base + timedelta(days=i)
            cur.execute(
                LEAD_SQL,
                (
                    f"id-lt-1145-{item}-{i}",
                    f"lt-1145-{item}-{i}",
                    supplier,
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=50)).isoformat(),
                    SOURCE_SYSTEM,
                ),
            )
        cur.execute(
            ITEM_SITE_SQL,
            (
                f"id-is-1145-{item}",
                item,
                SITE,
                unit_cost,
                reorder_point,
                0.0,
                None,
                None,
                SOURCE_SYSTEM,
            ),
        )
        cur.execute(
            ON_HAND_SQL,
            (f"id-oh-1145-{item}", item, SITE, on_hand, SYNC_AS_OF, SOURCE_SYSTEM),
        )
        cur.execute(
            TERMS_SQL,
            (
                f"id-st-1145-{item}",
                f"{item}:terms",
                SOURCE_SYSTEM,
                item,
                supplier,
                *terms,
            ),
        )


class FakeExcess:
    """The stand-in for ``excess_ranked`` in ``app.api.supply_plan``:
    the fixed ranked rows (each (item_code, site_code) pair, with its
    1-based rank), counted — the route must read the excess list
    exactly once per call."""

    calls = 0

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def __call__(self, as_of=None):
        FakeExcess.calls += 1
        return [
            {"item_code": item, "site_code": site, "rank": rank}
            for rank, (item, site) in enumerate(self._pairs, start=1)
        ]


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


# X: 400.00 x 0.25 x 10 / 365 = 2.74; Z: 450.00 x 0.25 x 20 / 365 =
# 6.16; U: 300.00 x 0.25 x 15 / 365 = 3.08.
TOP_UP_X = _top_up("X", 10, "2026-10-10", 4, 100.0, "400.00", "2.74")
TOP_UP_Z = _top_up("Z", 20, "2026-10-20", 9, 50.0, "450.00", "6.16")
TOP_UP_U = _top_up("U", 15, "2026-10-15", 3, 100.0, "300.00", "3.08")

# S-A: minimum 2500.00, total 1800.00, shortfall 700.00; X (400.00)
# then Z (450.00) reach 850.00 >= 700.00.
SUPPLIER_A_DEFAULT = {
    "supplier_code": "S-A",
    "source_system": SOURCE_SYSTEM,
    "vendor_code": "V-S-A",
    "name": "S-A Trading Co.",
    "minimum": "2500.00",
    "total_value": "1800.00",
    "shortfall": "700.00",
    "top_ups": [TOP_UP_X, TOP_UP_Z],
    "top_up_value": "850.00",
    "reaches_minimum": True,
    "still_short": "0.00",
}
# S-A with the 15-day look-ahead: X alone (400.00 < 700.00).
SUPPLIER_A_LOOKAHEAD_15 = {
    **SUPPLIER_A_DEFAULT,
    "top_ups": [TOP_UP_X],
    "top_up_value": "400.00",
    "reaches_minimum": False,
    "still_short": "300.00",
}
# S-B: minimum 1000.00, total 200.00, shortfall 800.00; U alone
# (300.00 < 800.00) — still short 500.00 in either look-ahead.
SUPPLIER_B = {
    "supplier_code": "S-B",
    "source_system": SOURCE_SYSTEM,
    "vendor_code": "V-S-B",
    "name": "S-B Trading Co.",
    "minimum": "1000.00",
    "total_value": "200.00",
    "shortfall": "800.00",
    "top_ups": [TOP_UP_U],
    "top_up_value": "300.00",
    "reaches_minimum": False,
    "still_short": "500.00",
}


def _body(lookahead_days, suppliers):
    return {
        "as_of": AS_OF_STR,
        "horizon_days": 7,
        "lookahead_days": lookahead_days,
        "holding_rate": 0.25,
        "suppliers": suppliers,
    }


DEFAULT_BODY = _body(30, [SUPPLIER_A_DEFAULT, SUPPLIER_B])
LOOKAHEAD_15_BODY = _body(15, [SUPPLIER_A_LOOKAHEAD_15, SUPPLIER_B])


def _plan_line_by_item(admin, horizon_days):
    """The plan's lines of ``horizon_days`` keyed by item_code."""
    response = admin.get(PLAN_URL, params={"horizon_days": horizon_days})
    assert response.status_code == 200, response.text
    return {
        line["item_code"]: line
        for group in response.json()["suppliers"]
        for line in group["lines"]
    }


def _row_counts():
    """The row counts of the two tables the route must not write."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM supplier_order_minimums")
            minimums = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM settings")
            settings = cur.fetchone()[0]
    finally:
        conn.close()
    return minimums, settings


def _assert_matches_lookahead_plan(top_ups, lines):
    """Each top-up's due_in_days, order_by, quantity, unit_cost and
    value equal the look-ahead plan's line for it."""
    for top_up in top_ups:
        line = lines[top_up["item_code"]]
        assert top_up["site_code"] == line["site_code"]
        assert top_up["due_in_days"] == line["due_in_days"]
        assert top_up["order_by"] == line["order_by"]
        assert top_up["quantity"] == line["quantity"]
        assert top_up["unit_cost"] == line["unit_cost"]
        assert top_up["value"] == line["value"]


def test_issue1145_freeform(client, monkeypatch) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # --- Seed the plan's sources the way the issue 1076 proof does. -
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # The minimums, stored the way the admin stores them: S-A 2500,
    # S-B 1000, S-C 100; S-D has none.
    for supplier, amount in MINIMUMS:
        SupplierOrderMinimumPostgresRepository().upsert(
            SOURCE_SYSTEM, supplier, amount, "test"
        )
    assert _row_counts() == (len(MINIMUMS), 0)

    # The excess list, replaced through the module's own name: Y's
    # pair, counted once per call.
    FakeExcess.calls = 0
    monkeypatch.setattr(supply_plan, "excess_ranked", FakeExcess([("Y", SITE)]))

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    anon = TestClient(app)

    # --- The setting is listed, with the default 30. ----------------
    settings = admin.get("/api/settings")
    assert settings.status_code == 200, settings.text
    entry = next(e for e in settings.json() if e["key"] == SETTING_KEY)
    assert entry["scope"] == "system"
    assert entry["type"] == "number"
    assert entry["allowed"] is None
    assert entry["min"] == 1
    assert entry["max"] == 90
    assert entry["default"] == 30
    assert entry["description"] == SETTING_DESCRIPTION
    assert entry["value"] == 30
    assert entry["source"] == "default"

    # --- Validation: outside 1..60 or non-numeric answers 422; no
    #     token answers 401.
    assert admin.get(URL, params={"horizon_days": 0}).status_code == 422
    assert admin.get(URL, params={"horizon_days": 61}).status_code == 422
    assert admin.get(URL, params={"horizon_days": "abc"}).status_code == 422
    assert anon.get(URL).status_code == 401

    # --- The default answer: horizon 7, look-ahead 30, rate 0.25. ---
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == DEFAULT_BODY
    # S-A then S-B, in the plan's group order, and nothing else: S-C
    # has no shortfall and S-D has no stored minimum.
    assert [s["supplier_code"] for s in body["suppliers"]] == ["S-A", "S-B"]
    assert FakeExcess.calls == 1, "excess_ranked is read once per call"

    # Each top-up is the look-ahead plan's line for it.
    lines_30 = _plan_line_by_item(admin, 30)
    _assert_matches_lookahead_plan(SUPPLIER_A_DEFAULT["top_ups"], lines_30)
    _assert_matches_lookahead_plan(SUPPLIER_B["top_ups"], lines_30)
    # Y never comes up (on the excess list), V never comes up (no
    # value) and W never comes up (due in 40 days, beyond the 30-day
    # look-ahead — it is not in the look-ahead plan at all).
    for excluded in ("Y", "V", "W"):
        assert excluded not in [
            t["item_code"] for s in body["suppliers"] for t in s["top_ups"]
        ], excluded
    assert "W" not in lines_30

    # A second call reads the excess list exactly once more; the
    # answer is unchanged.
    again = admin.get(URL, params={"horizon_days": 7})
    assert again.status_code == 200
    assert again.json() == body
    assert FakeExcess.calls == 2

    # --- Read-only so far: nothing was written. ---------------------
    assert _row_counts() == (len(MINIMUMS), 0)

    # --- The shorter look-ahead: the admin stores the setting as 15.
    store = admin.put(f"/api/settings/{SETTING_KEY}", json={"value": 15})
    assert store.status_code == 200, store.text
    assert store.json()["value"] == 15
    assert store.json()["source"] == "system"
    assert _row_counts() == (len(MINIMUMS), 1)

    response = admin.get(URL)
    assert response.status_code == 200, response.text
    short_body = response.json()
    assert short_body == LOOKAHEAD_15_BODY
    # S-A's top-ups are X alone: Z is due in 20 days, beyond the 15
    # look-ahead — still short 2500 - 1800 - 400 = 300.00 — while
    # S-B's are still U (due in 15 days, within it).
    assert [t["item_code"] for t in short_body["suppliers"][0]["top_ups"]] == ["X"]
    assert short_body["suppliers"][1]["top_ups"] == [TOP_UP_U]
    lines_15 = _plan_line_by_item(admin, 15)
    _assert_matches_lookahead_plan(short_body["suppliers"][0]["top_ups"], lines_15)
    _assert_matches_lookahead_plan(short_body["suppliers"][1]["top_ups"], lines_15)
    assert "Z" not in lines_15
    assert FakeExcess.calls == 3

    # --- Read-only: the calls that are not the admin's store wrote
    #     nothing.
    assert _row_counts() == (len(MINIMUMS), 1)

    # --- Identity: a second pass after a full reload, as the
    # NON-ADMIN (role "user").  The page is booted the production
    # way: the token it would take from the URL hash as
    # #access_token=... is the development token (role "user") sent
    # as its Bearer token; GET /api/auth/config answers publicly and
    # GET /api/auth/me resolves the viewer.
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is True
    assert "user" in me.json()["user"]["roles"]
    assert "admin" not in me.json()["user"]["roles"]

    # The top-ups page still renders for the viewer — the route is
    # open to any signed-in user, so no admin-only control stands on
    # it — with the same body, and no token is still a 401.
    viewer_response = client.get(URL)
    assert viewer_response.status_code == 200, viewer_response.text
    assert viewer_response.json() == short_body
    assert anon_reloaded.get(URL).status_code == 401
