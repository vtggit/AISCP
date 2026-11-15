"""Issue #1133 proof: the order minimum and the shortfall on the
supply plan's supplier groups.

AC-1 — the minimum and the shortfall: ``app.api.supply_plan`` holds
``_supplier_order_minimum_repo`` (a
``SupplierOrderMinimumPostgresRepository``) beside its other
repositories, and ``build_supply_plan`` lists the stored minimums
exactly once per call, keyed by (``source_system``,
``supplier_code``) compared exactly as stored.  ``SupplyPlanSupplierGroup``
gains ``minimum`` and ``shortfall``: for a group whose pair has a
stored minimum, ``minimum`` is the stored amount and ``shortfall`` the
minimum minus the group's ``total_value`` when the total is below the
minimum and 0 when it is not — both rounded to two decimal places
half-up, the way the lines' values are, and serialized the way the
group's ``total_value`` is; every other group, the group without a
supplier included, carries neither key in the response, exactly as a
line leaves out a null ``supplier_reason``.  A minimum whose pair has
no group in the plan changes nothing, and a raised minimums read
fails ``GET /api/supply-plan`` with a 500 — it never degrades to the
absent keys, which are reserved for a supplier with no stored
minimum.

AC-2 — this proof: the plan's sources seeded the way
``tests/test_issue1076_freeform.py`` seeds a plan (fixed dates, raw
INSERTs: 360 days of 0.2 a day, three identical 50-day lead-time
observations, 0 on hand — due now — per item) for four items of the
source system ``acumatica``: ``I-1133-A`` at unit cost 100 under
``S-A`` (raw 18, value 1800.00), ``I-1133-B`` at 50 under ``S-B``
(raw 25 capped at the maximum 5, value 250.00), ``I-1133-C`` at 40
under ``S-C`` (raw 28 capped at the maximum 10, value 400.00) and
``I-1133-D`` without a unit cost or terms — the single final group
without a supplier.  The minimums are stored with
``SupplierOrderMinimumPostgresRepository().upsert(...)``.  Proved
through ``GET /api/supply-plan`` with a signed-in development token:
with nothing stored, no group's dictionary carries either key and
the whole answer equals the answer of the same call made before any
minimum exists; a minimum of 2500 for (``acumatica``, S-A) gives S-A
``minimum`` 2500.00 and ``shortfall`` 700.00, read as decimals from
the JSON, while the S-B, S-C and supplier-less groups carry neither
key; a minimum of 250 for (``acumatica``, S-B) gives ``minimum``
250.00 and ``shortfall`` 0; one of 100 for (``acumatica``, S-C)
gives ``minimum`` 100.00 and ``shortfall`` 0; one of 1800.005 for
S-A rounds to ``minimum`` 1800.01 and ``shortfall`` 0.01; a minimum
stored for (``odoo``, S-A) only gives the ``acumatica`` S-A group
neither key; one stored for (``acumatica``, ``s-a``) gives the S-A
group neither key; one for (``acumatica``, S-Z) — a pair without a
group in the plan — changes the answer in nothing; with minimums
stored, the lines, the groups' order and the ``line_count`` and
``total_value`` of the plan and of each group equal those of the
answer without minimums; with
``supply_plan._supplier_order_minimum_repo`` replaced by a counting
wrapper, the way ``tests/test_issue830_freeform.py`` counts its
repository reads, one call of ``GET /api/supply-plan`` lists the
minimums exactly once; and with the read raising, the route answers
500 with the minimum and shortfall keys never absent.

Identity — the proof also runs as a NON-ADMIN (role "user"): a
second pass in the same test after a full reload, with the page
booted the production way (the token it would take from the URL hash
as ``#access_token=...`` — the development token, role "user" — sent
as its Bearer token), ``GET /api/auth/config`` answering publicly
and ``GET /api/auth/me`` resolving the viewer — sees the plan page
still render (200 with the same body, the route open to any
signed-in user, no admin-only control on it) while no token is still
a 401.
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

URL = "/api/supply-plan"
ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

# The plan's one source system and its three suppliers.
SOURCE_SYSTEM = "acumatica"
SUPPLIER_A = "S-A"
SUPPLIER_B = "S-B"
SUPPLIER_C = "S-C"

# The resolved as-of: the latest demand date, earlier than today.
AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
ARRIVES_ON = (AS_OF + timedelta(days=50)).isoformat()  # 0 due days + 50
FIRST_DAY = AS_OF - timedelta(days=359)
SITE = "159"
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

# The plan's four items of the source system: the three supplier
# groups (S-A 1800.00, S-B 250.00, S-C 400.00) and the item without
# a unit cost or terms — the final group without a supplier.
ITEMS = (
    ("A", "I-1133-A", SUPPLIER_A, 100.0),
    ("B", "I-1133-B", SUPPLIER_B, 50.0),
    ("C", "I-1133-C", SUPPLIER_C, 40.0),
    ("D", "I-1133-D", None, None),
)


def _seed(cur) -> None:
    """The fixed-date rows, the way the issue 1076 proof seeds a
    plan: per item, 360 days of 0.2 a day (d 0.2, sd_d 0), three
    identical 50-day lead-time observations (lt 50, sd_lt 0) ->
    reorder point 10, 0 on hand -> due now.  The raw quantity is the
    EOQ for the item's unit cost (18 at 100, 25 at 50, 28 at 40),
    else 30 days of demand (6 without a cost); fitted: A stays 18
    (1800.00), B is capped at its maximum 5 (250.00), C at its
    maximum 10 (400.00) and D, with no terms row, lands in the final
    group without a supplier (no unit cost, null value)."""
    for key, item, supplier, unit_cost in ITEMS:
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-1133-{key}-{i}",
                    f"de-1133-{key}-{i}",
                    item,
                    SITE,
                    0.2,
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
                    f"id-lt-1133-{key}-{i}",
                    f"lt-1133-{key}-{i}",
                    supplier or "S-NOVENDOR",
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=50)).isoformat(),
                    SOURCE_SYSTEM,
                ),
            )
        cur.execute(
            ITEM_SITE_SQL,
            (
                f"id-is-1133-{key}",
                item,
                SITE,
                unit_cost,
                10.0,
                0.0,
                None,
                None,
                SOURCE_SYSTEM,
            ),
        )
        cur.execute(
            ON_HAND_SQL,
            (f"id-oh-1133-{key}", item, SITE, 0.0, SYNC_AS_OF, SOURCE_SYSTEM),
        )
        if supplier is not None:
            cur.execute(
                SUPPLIER_SQL,
                (
                    f"id-sp-1133-{key}",
                    supplier,
                    f"V-{supplier}",
                    f"{supplier} Trading Co.",
                    "active",
                    SOURCE_SYSTEM,
                ),
            )
            if key == "A":
                # The default pick, min order 10: the raw 18 stands.
                terms = (10, None, None, 50, True)
            elif key == "B":
                # The raw 25 is capped at the maximum 5.
                terms = (None, 5, None, 50, True)
            else:
                # The raw 28 is capped at the maximum 10.
                terms = (None, 10, None, 50, True)
            cur.execute(
                TERMS_SQL,
                (
                    f"id-st-1133-{key}",
                    f"{item}:terms-{key}",
                    SOURCE_SYSTEM,
                    item,
                    supplier,
                    *terms,
                ),
            )


def _get(client) -> dict:
    response = client.get(URL, params={"horizon_days": 7})
    assert response.status_code == 200, response.text
    return response.json()


def _minimums(body) -> dict:
    """Each group's (minimum, shortfall) as decimals read from the
    JSON, None for the keys the group leaves out."""
    pairs = {}
    for group in body["suppliers"]:
        minimum = group.get("minimum")
        shortfall = group.get("shortfall")
        pairs[group["supplier_code"]] = (
            Decimal(minimum) if minimum is not None else None,
            Decimal(shortfall) if shortfall is not None else None,
        )
    return pairs


def _without_minimums(body) -> dict:
    """The answer with the groups' minimum keys removed: the shape
    of the pre-minimum answer, for the 'unchanged around it'
    checks."""
    return {
        **body,
        "suppliers": [
            {
                field: group[field]
                for field in group
                if field not in ("minimum", "shortfall")
            }
            for group in body["suppliers"]
        ],
    }


def _store(source_system: str, supplier_code: str, amount) -> None:
    """Store one minimum the way the admin stores it."""
    SupplierOrderMinimumPostgresRepository().upsert(
        source_system, supplier_code, amount, "test"
    )


def _clear_minimums() -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM supplier_order_minimums")
        conn.commit()
    finally:
        conn.close()


class CountingMinimumRepo:
    """The real supplier_order_minimums repository wrapped to count
    its reads, the way the issue 830 proof counts its repository
    reads: the builder must list the minimums once per call."""

    calls = 0

    def __init__(self):
        self._inner = SupplierOrderMinimumPostgresRepository()

    def list(self):
        CountingMinimumRepo.calls += 1
        return self._inner.list()


class FailingMinimumRepo:
    """The repository whose read fails: the route must answer 500,
    never a plan whose groups carry no minimum."""

    def list(self):
        raise RuntimeError("the minimums read failed")


def test_issue1133_freeform(client, monkeypatch) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    _clear_minimums()

    # --- Nothing stored: no group's dictionary carries either key,
    #     and the whole answer equals the answer of the same call
    #     made before any minimum exists. ----------------------------
    pre = _get(admin)
    again = _get(admin)
    assert again == pre
    for group in pre["suppliers"]:
        assert "minimum" not in group
        assert "shortfall" not in group

    # The plan the minimums answer against: the three supplier
    # groups of the source system in order of their value, then the
    # supplier-less group last.
    assert pre["as_of"] == AS_OF_STR
    assert pre["horizon_days"] == 7
    assert pre["line_count"] == 4
    assert Decimal(pre["total_value"]) == Decimal("2450.00")
    assert [group["supplier_code"] for group in pre["suppliers"]] == [
        SUPPLIER_A,
        SUPPLIER_C,
        SUPPLIER_B,
        None,
    ]
    assert [group["source_system"] for group in pre["suppliers"]] == [
        SOURCE_SYSTEM,
        SOURCE_SYSTEM,
        SOURCE_SYSTEM,
        None,
    ]
    assert {
        group["supplier_code"]: Decimal(group["total_value"])
        for group in pre["suppliers"]
    } == {
        SUPPLIER_A: Decimal("1800.00"),
        SUPPLIER_B: Decimal("250.00"),
        SUPPLIER_C: Decimal("400.00"),
        None: Decimal("0"),
    }
    assert [group["line_count"] for group in pre["suppliers"]] == [1, 1, 1, 1]

    # --- A short order: 2500 for (acumatica, S-A). ------------------
    _store(SOURCE_SYSTEM, SUPPLIER_A, 2500)
    body = _get(admin)
    assert _minimums(body) == {
        SUPPLIER_A: (Decimal("2500.00"), Decimal("700.00")),
        SUPPLIER_B: (None, None),
        SUPPLIER_C: (None, None),
        None: (None, None),
    }
    # Unchanged around it: the lines, the groups' order, the
    # line_count and the total_value of the plan and of each group
    # equal the answer without minimums.
    assert body["line_count"] == pre["line_count"]
    assert Decimal(body["total_value"]) == Decimal(pre["total_value"])
    assert [group["supplier_code"] for group in body["suppliers"]] == [
        group["supplier_code"] for group in pre["suppliers"]
    ]
    for kept, fresh in zip(pre["suppliers"], body["suppliers"]):
        assert fresh["lines"] == kept["lines"]
        assert fresh["line_count"] == kept["line_count"]
        assert fresh["total_value"] == kept["total_value"]
    assert _without_minimums(body) == pre

    # --- An order that meets its minimum: 250 for (acumatica, S-B). -
    _clear_minimums()
    _store(SOURCE_SYSTEM, SUPPLIER_B, 250)
    body = _get(admin)
    assert _minimums(body) == {
        SUPPLIER_A: (None, None),
        SUPPLIER_B: (Decimal("250.00"), Decimal("0.00")),
        SUPPLIER_C: (None, None),
        None: (None, None),
    }
    assert _without_minimums(body) == pre

    # --- Above it: 100 for (acumatica, S-C). -------------------------
    _clear_minimums()
    _store(SOURCE_SYSTEM, SUPPLIER_C, 100)
    body = _get(admin)
    assert _minimums(body) == {
        SUPPLIER_A: (None, None),
        SUPPLIER_B: (None, None),
        SUPPLIER_C: (Decimal("100.00"), Decimal("0.00")),
        None: (None, None),
    }
    assert _without_minimums(body) == pre

    # --- Rounding: 1800.005 for S-A rounds to 1800.01, the shortfall
    #     1800.01 - 1800.00. ------------------------------------------
    _clear_minimums()
    _store(SOURCE_SYSTEM, SUPPLIER_A, 1800.005)
    body = _get(admin)
    assert _minimums(body) == {
        SUPPLIER_A: (Decimal("1800.01"), Decimal("0.01")),
        SUPPLIER_B: (None, None),
        SUPPLIER_C: (None, None),
        None: (None, None),
    }
    assert _without_minimums(body) == pre

    # --- Another source system is another supplier: (odoo, S-A) only.
    _clear_minimums()
    _store("odoo", SUPPLIER_A, 2500)
    body = _get(admin)
    assert _minimums(body) == {
        SUPPLIER_A: (None, None),
        SUPPLIER_B: (None, None),
        SUPPLIER_C: (None, None),
        None: (None, None),
    }
    assert body == pre

    # --- Exact codes: (acumatica, 's-a') is not S-A. -----------------
    _clear_minimums()
    _store(SOURCE_SYSTEM, "s-a", 2500)
    body = _get(admin)
    assert _minimums(body) == {
        SUPPLIER_A: (None, None),
        SUPPLIER_B: (None, None),
        SUPPLIER_C: (None, None),
        None: (None, None),
    }
    assert body == pre

    # --- A pair without a group: (acumatica, S-Z) changes the answer
    #     in nothing. ---------------------------------------------------
    _clear_minimums()
    _store(SOURCE_SYSTEM, "S-Z", 2500)
    body = _get(admin)
    assert body == pre

    # --- One read: one call lists the minimums exactly once, counted
    #     the way the issue 830 proof counts its repository reads.
    _clear_minimums()
    CountingMinimumRepo.calls = 0
    monkeypatch.setattr(
        supply_plan, "_supplier_order_minimum_repo", CountingMinimumRepo()
    )
    _store(SOURCE_SYSTEM, SUPPLIER_A, 2500)
    body = _get(admin)
    assert CountingMinimumRepo.calls == 1, "the minimums are listed once per call"
    assert body["suppliers"][0]["minimum"] == "2500.00"
    assert body["suppliers"][0]["shortfall"] == "700.00"
    again = _get(admin)
    assert CountingMinimumRepo.calls == 2
    assert again == body

    # --- A failed read: the route answers 500, never a plan whose
    #     groups carry no minimum. -------------------------------------
    monkeypatch.setattr(
        supply_plan, "_supplier_order_minimum_repo", FailingMinimumRepo()
    )
    failing = TestClient(
        app,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        raise_server_exceptions=False,
    )
    response = failing.get(URL, params={"horizon_days": 7})
    assert response.status_code == 500, response.text

    # The read stands again: a working repository for the viewer's
    # pass (the stored 2500 for S-A still answers its group).
    monkeypatch.setattr(
        supply_plan,
        "_supplier_order_minimum_repo",
        SupplierOrderMinimumPostgresRepository(),
    )

    # --- Identity: a second pass after a full reload, as the
    #     NON-ADMIN (role "user").  The page is booted the production
    #     way: the token it would take from the URL hash as
    #     #access_token=... is the development token (role "user")
    #     sent as its Bearer token; GET /api/auth/config answers
    #     publicly and GET /api/auth/me resolves the viewer.
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
    viewer_response = viewer.get(URL, params={"horizon_days": 7})
    assert viewer_response.status_code == 200, viewer_response.text
    assert viewer_response.json() == body
    assert anon_reloaded.get(URL).status_code == 401
