"""Issue #1438 proof: one recommendations computation for the top-ups.

AC-1 — the blocks: ``top_ups_by_supplier(horizon_days, as_of=None)``
runs its body inside one ``with`` statement — one
``app.recommend.reorder.recommendations_memo`` block, one
``app.db.read_cache.read_once`` block and one
``app.db.connection.one_connection`` block — so its two order plans'
``recommendations_for_all`` calls and its excess ranking's own read
share the one memo, the recommendations are computed once per as-of,
each read the cached readers serve is read once, and the call's
cursors ride the one connection; a caller already inside any of the
blocks shares the outer one (the nested use each already allows), and
the answer — for the route ``GET /api/supply-plan/top-ups`` and for
the release (``app.supply.release``) — is the same as without the
blocks.  The function's and the module's docstrings say so; nothing
else in the module changes.

AC-2 — this proof: the plan's sources seeded the way
``tests/test_issue1145_freeform.py`` seeds them (fixed dates, raw
INSERTs: 360 days of demand a day per item, three identical 50-day
lead-time observations, the on-hand that sets each line's due date,
the unit cost and one default terms row per item — every item-site in
``acumatica`` at site 159), with the supplier groups' stored
minimums: supplier ``S-A`` a minimum of 2500 against a 7-day line A
worth 1800.00 (shortfall 700.00) topped up by its later lines X
(400.00) and Z (450.00), supplier ``S-B`` a minimum of 1000 against
line B worth 200.00 (shortfall 800.00) topped up by its later line U
(300.00), ``S-C`` no shortfall and ``S-D`` no minimum.  The calls of
``app.recommend.reorder._compute_recommendations`` are counted with a
wrapper around the module's own name.  Proved:
``top_ups_by_supplier(7)`` computes the recommendations exactly once
(the horizon plan's build, the look-ahead plan's build and the excess
ranking all share the one memo per as-of); calling the three pieces
outside any block — ``build_supply_plan(7)``, ``build_supply_plan``
for the look-ahead days (the default 30) and ``excess_ranked()`` —
computes them more than once; the function's answer equals
``GET /api/supply-plan/top-ups``'s answer (with a signed-in token),
which also computes exactly once per call; and the function's answer
equals the answer it gives when the three blocks are replaced by
no-op context managers.  Every existing test passes.

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
from contextlib import contextmanager
from datetime import date, timedelta

from fastapi.testclient import TestClient

import app.api.supply_plan as supply_plan
import app.recommend.reorder as reorder
from app.api.supply_plan import SupplyPlanTopUpsResponse, top_ups_by_supplier
from app.db.connection import get_connection
from app.main import app
from app.recommend.excess import excess_ranked
from app.repositories.supplier_order_minimums_postgres_repository import (
    SupplierOrderMinimumPostgresRepository,
)

URL = "/api/supply-plan/top-ups"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

SOURCE_SYSTEM = "acumatica"
SITE = "159"

# The resolved as-of: the latest demand date, earlier than today.
AS_OF = date(2026, 9, 30)
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
# None, terms).  The demand is 0.2 a day (reorder point 10) except Y
# at 0.25 a day (reorder point 13), so the on hand sets the due date:
# A 0, B 0, C 0, D 0 (within the 7-day horizon), X 10, Y 12, U 15,
# Z 20, V 25 and W 40.
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
    """The fixed-date rows, the way the issue 1145 proof seeds a plan:
    per item, 360 days of its demand a day, three identical 50-day
    lead-time observations (lead time 50, no variation), the on hand
    that sets the due date and the unit cost; one default terms row
    per item of the item's supplier."""
    for supplier in SUPPLIERS:
        cur.execute(
            SUPPLIER_SQL,
            (
                f"id-sp-1438-{supplier}",
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
                    f"id-de-1438-{item}-{i}",
                    f"de-1438-{item}-{i}",
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
                    f"id-lt-1438-{item}-{i}",
                    f"lt-1438-{item}-{i}",
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
                f"id-is-1438-{item}",
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
            (f"id-oh-1438-{item}", item, SITE, on_hand, SYNC_AS_OF, SOURCE_SYSTEM),
        )
        cur.execute(
            TERMS_SQL,
            (
                f"id-st-1438-{item}",
                f"{item}:terms",
                SOURCE_SYSTEM,
                item,
                supplier,
                *terms,
            ),
        )


def test_issue1438_freeform(client, monkeypatch) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # --- Seed the plan's sources the way the issue 1145 proof does. -
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

    # The shared computation, counted: every call of
    # _compute_recommendations — whatever the caller, the route or a
    # direct call — is the recommendations computed for one resolved
    # as-of.
    compute_calls: list[date] = []
    real_compute = reorder._compute_recommendations

    def counting_compute(resolved_as_of):
        compute_calls.append(resolved_as_of)
        return real_compute(resolved_as_of)

    monkeypatch.setattr(reorder, "_compute_recommendations", counting_compute)

    # --- One call: the recommendations are computed exactly once. ---
    answer = top_ups_by_supplier(7)
    assert compute_calls == [AS_OF], (
        "the two order plans and the excess ranking share one "
        "recommendations computation per as-of"
    )

    # --- The route's answer is the function's answer. --------------
    before = len(compute_calls)
    response = client.get(URL, params={"horizon_days": 7})
    assert response.status_code == 200, response.text
    route_body = response.json()
    assert route_body == SupplyPlanTopUpsResponse(**answer).model_dump(mode="json")
    assert (
        len(compute_calls) - before == 1
    ), "the route's call computes the recommendations exactly once"

    # --- The three pieces outside any block: more than once. -------
    before_pieces = len(compute_calls)
    supply_plan.build_supply_plan(7)
    supply_plan.build_supply_plan(30)  # the look-ahead days, default
    excess_ranked()
    pieces = len(compute_calls) - before_pieces
    assert pieces > 1, "outside any block the pieces recompute"

    # --- Identity: a second pass after a full reload, as the
    #     NON-ADMIN (role "user").  The page is booted the production
    #     way: the token it would take from the URL hash as
    #     #access_token=... is the development token (role "user")
    #     sent as its Bearer token; GET /api/auth/config answers
    #     publicly and GET /api/auth/me resolves the viewer.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)
    assert anon.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["authenticated"] is True
    assert "user" in me.json()["user"]["roles"]
    assert "admin" not in me.json()["user"]["roles"]

    # The top-ups page still renders for the viewer — the route is
    # open to any signed-in user, so no admin-only control stands on
    # it — with the same body, and no token is still a 401.
    viewer_response = viewer.get(URL, params={"horizon_days": 7})
    assert viewer_response.status_code == 200, viewer_response.text
    assert viewer_response.json() == route_body
    assert anon.get(URL).status_code == 401

    # --- The no-op blocks: the answer is unchanged. ----------------
    @contextmanager
    def no_op():
        yield

    monkeypatch.setattr(supply_plan, "recommendations_memo", no_op)
    monkeypatch.setattr(supply_plan, "read_once", no_op)
    monkeypatch.setattr(supply_plan, "one_connection", no_op)
    assert top_ups_by_supplier(7) == answer
