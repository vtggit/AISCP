"""Issue #914 proof: the one-page summary of the 7-day supply plan —
GET /api/supply-plan/summary.

AC-1 — the route: open to any signed-in user behind the same
auth_gate as the rest of the supply-plan routes, answering
``{"as_of", "horizon_days": 7, "line_count", "total_value",
"supplier_count", "transfer_count", "transfer_value",
"transfers_in_flight", "transfers_awaiting_release"}``.  Proved
here: ``line_count``, ``total_value`` and ``supplier_count`` equal
the 7-day order plan's over the same seeded data (the same builder
``GET /api/supply-plan`` uses), ``transfer_count`` and
``transfer_value`` equal the transfer suggestions' (the same
builder ``GET /api/supply-plan/transfers`` uses),
``transfers_in_flight`` the ``list_in_flight`` rows whose
``erp_status`` is null or none of "Released", "Completed" and
"Canceled", and ``transfers_awaiting_release`` how many of those
are at least the ``exceptions.release_approval_days`` setting (3)
old by ``updated_at`` — with one "created" row "On Hold" five days
old, one "created" row with a null status one day old and one
"Released" row: 2 in flight, 1 awaiting.  One request runs the
recommendations computation exactly once (the answer is built
inside one ``recommendations_memo``), no token answers 401, and the
schema file lists the route and its model.

AC-2 — this proof seeds plan, transfer and ``transfer_releases``
data the way ``tests/test_issue897_freeform.py`` does (fixed dates,
raw INSERTs: demand, lead times, item-sites, on-hand and open
purchase lines for item A at the donor site 154 and the receiver
site 159, one supplier-terms row naming SUP-914, the
``transfer_releases`` rows with explicit ``updated_at`` stamps).

Identity — the proof also runs as a NON-ADMIN (role "user"): a
second pass in the same test after a full reload, with the page
booted the production way (the token it would take from the URL
hash as ``#access_token=...`` — the development token, role
"user" — sent as its Bearer token), ``GET /api/auth/config``
answering publicly and ``GET /api/auth/me`` resolving the viewer —
sees the summary page still render (200 with the same body, the
route open to any signed-in user, so no admin-only control stands
on it) while no token is still a 401.
"""

import json
import os
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.recommend import reorder

URL = "/api/supply-plan/summary"
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

# The resolved as-of: the latest demand date, earlier than today.
AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)
ITEM = "A914"
FROM_SITE = "154"
TO_SITE = "159"
SOURCE_SYSTEM = "test"
SUPPLIER = "SUP-914"
SYNC_AS_OF = "2026-09-30T00:00:00+00:00"
TRANSFER_ORIGIN = f"AIERP transfer {AS_OF_STR} site {FROM_SITE} to {TO_SITE}"

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
OPEN_LINE_SQL = (
    "INSERT INTO open_purchase_lines (id, business_key, item_code, "
    "site_code, supplier_code, order_type, open_units, promised_on, "
    "ordered_on, as_of, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
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
TRANSFER_SQL = (
    "INSERT INTO transfer_releases (origin, from_site_code, to_site_code, "
    "reference_nbr, outcome, line_count, lines, actor, erp_status, "
    "updated_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)"
)


def _seed(cur) -> None:
    """The fixed-date rows: demand, lead times, item-sites, position.

    Item A at site 154 (the donor): 30.0 a day for 360 days (d 30,
    sd_d 0), a 50-day lead time (three identical observations) -> ROP
    1500 units; 4000 on hand and 50 open -> position 4050, far above
    the point.  Item A at site 159 (the receiver): 2.0 a day for 360
    days (d 2, sd_d 0), the same 50-day lead time -> ROP 100 units;
    0 on hand, nothing open — short by 100.  One supplier-terms row
    names SUP-914 as the item's default supplier of the "test" source
    system (no minimum, lot or maximum, no lead time), and the
    suppliers table stores the supplier's name.
    """
    for site, daily in ((FROM_SITE, 30.0), (TO_SITE, 2.0)):
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-914-{site}-{i}",
                    f"de-914-{site}-{i}",
                    ITEM,
                    site,
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
                f"id-lt-914-{i}",
                f"lt-914-{i}",
                SUPPLIER,
                ITEM,
                ordered.isoformat(),
                (ordered + timedelta(days=50)).isoformat(),
                SOURCE_SYSTEM,
            ),
        )
    cur.execute(
        ITEM_SITE_SQL,
        ("id-is-914-154", ITEM, FROM_SITE, 2.0, 1500.0, 0.0, None, None, SOURCE_SYSTEM),
    )
    cur.execute(
        ITEM_SITE_SQL,
        ("id-is-914-159", ITEM, TO_SITE, 2.0, 100.0, 0.0, None, None, SOURCE_SYSTEM),
    )
    cur.execute(
        ON_HAND_SQL,
        ("id-oh-914-154", ITEM, FROM_SITE, 4000.0, SYNC_AS_OF, SOURCE_SYSTEM),
    )
    cur.execute(
        ON_HAND_SQL,
        ("id-oh-914-159", ITEM, TO_SITE, 0.0, SYNC_AS_OF, SOURCE_SYSTEM),
    )
    cur.execute(
        OPEN_LINE_SQL,
        (
            "id-ol-914",
            "po:PO-914-1:1",
            ITEM,
            FROM_SITE,
            SUPPLIER,
            "po",
            50.0,
            "2026-11-15",
            "2026-09-20",
            SYNC_AS_OF,
            SOURCE_SYSTEM,
        ),
    )
    cur.execute(
        TERMS_SQL,
        (
            "id-st-914",
            f"{ITEM}:terms",
            SOURCE_SYSTEM,
            ITEM,
            SUPPLIER,
            None,
            None,
            None,
            None,
            True,
        ),
    )
    cur.execute(
        SUPPLIER_SQL,
        ("id-sp-914", SUPPLIER, "VEND-914", "Supplier 914", "active", SOURCE_SYSTEM),
    )


def _insert_transfer(
    cur,
    reference_nbr: str,
    outcome: str,
    erp_status: str | None,
    updated_at: datetime,
    line: dict,
) -> None:
    """One transfer_releases row of item A from 154 to 159, its
    ``updated_at`` the given stamp (the summary's awaiting window
    reads against it)."""
    cur.execute(
        TRANSFER_SQL,
        (
            TRANSFER_ORIGIN,
            FROM_SITE,
            TO_SITE,
            reference_nbr,
            outcome,
            1,
            json.dumps([line]),
            "test",
            erp_status,
            updated_at,
        ),
    )


def _supplier_count(plan: dict) -> int:
    """The plan's supplier groups with a non-null supplier_code."""
    return sum(1 for group in plan["suppliers"] if group["supplier_code"] is not None)


def test_issue914_freeform(client, monkeypatch) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # Count the underlying recommendations computations by wrapping
    # the module's internal builder; each entry is the computed as-of.
    computed: list[date] = []
    real_builder = reorder._compute_recommendations

    def counting_builder(resolved_as_of: date) -> list[dict]:
        computed.append(resolved_as_of)
        return real_builder(resolved_as_of)

    monkeypatch.setattr(reorder, "_compute_recommendations", counting_builder)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    anon = TestClient(app)

    # --- Unauthenticated access: no token answers 401 (status only —
    # auth_gate defines no canonical 401 body).
    assert anon.get(URL).status_code == 401

    # --- The summary's totals over the baseline data: the same
    # builders the two GET routes use, so the same answers. ---------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    summary = response.json()

    plan_response = admin.get("/api/supply-plan", params={"horizon_days": 7})
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()

    transfers_response = admin.get("/api/supply-plan/transfers")
    assert transfers_response.status_code == 200, transfers_response.text
    transfers = transfers_response.json()

    assert summary["as_of"] == plan["as_of"] == AS_OF_STR
    assert summary["horizon_days"] == 7
    # Totals: the summary's plan fields equal the 7-day order plan's
    # over the same data, its supplier_count the plan's supplier
    # groups with a non-null supplier_code, and its transfer fields
    # the transfer suggestions'.
    assert summary["line_count"] == plan["line_count"]
    assert summary["total_value"] == plan["total_value"]
    assert summary["supplier_count"] == _supplier_count(plan)
    assert summary["transfer_count"] == transfers["suggestion_count"]
    assert summary["transfer_value"] == transfers["total_value"]

    # The seeded shape, concretely: the receiver short by 100 is the
    # plan's one line (EOQ 383 units x 2.00 = 766.00, supplier group
    # SUP-914 the only non-null supplier group), and the donor gives
    # the receiver its need of 100 (100 x 2.00 = 200.00).  Nothing is
    # released yet: no in-flight transfer row.
    assert plan["line_count"] == 1
    assert summary["line_count"] == 1
    assert summary["total_value"] == "766.00"
    assert summary["supplier_count"] == 1
    assert summary["transfer_count"] == 1
    assert summary["transfer_value"] == "200.00"
    assert summary["transfers_in_flight"] == 0
    assert summary["transfers_awaiting_release"] == 0

    # --- One computation: one request runs the recommendations
    # computation exactly once (the plan and the suggestions read
    # their recommendations through the one memo).
    computed.clear()
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == summary
    assert computed == [AS_OF], computed

    # And the memo does not leak past the request: the next request
    # computes again, exactly once.
    computed.clear()
    again = admin.get(URL)
    assert again.status_code == 200
    assert again.json() == summary
    assert computed == [AS_OF], computed

    # --- In flight: one "created" row "On Hold" five days old, one
    # "created" row with a null status one day old, one "Released"
    # row.  The "Released" row stops counting: 2 in flight.  Only the
    # five-day-old row is at least the 3-day release-approval window
    # old by updated_at: 1 awaiting.  The two counted rows' 120 units
    # lift the receiver's position above its reorder point, so the
    # plan and the suggestions empty — and the summary's totals keep
    # equaling the two GET routes over the same (new) data.
    now = datetime.now(UTC)
    line_120 = {"item_code": ITEM, "quantity": 120}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _insert_transfer(
                cur, "000911", "created", "On Hold", now - timedelta(days=5), line_120
            )
            _insert_transfer(
                cur, "000912", "created", None, now - timedelta(days=1), line_120
            )
            _insert_transfer(
                cur, "000913", "created", "Released", now - timedelta(days=10), line_120
            )
        conn.commit()
    finally:
        conn.close()

    response = admin.get(URL)
    assert response.status_code == 200, response.text
    summary = response.json()
    assert summary["transfers_in_flight"] == 2
    assert summary["transfers_awaiting_release"] == 1

    plan_response = admin.get("/api/supply-plan", params={"horizon_days": 7})
    assert plan_response.status_code == 200, plan_response.text
    plan = plan_response.json()
    transfers_response = admin.get("/api/supply-plan/transfers")
    assert transfers_response.status_code == 200, transfers_response.text
    transfers = transfers_response.json()

    assert summary["line_count"] == plan["line_count"] == 0
    assert summary["total_value"] == plan["total_value"] == "0"
    assert summary["supplier_count"] == _supplier_count(plan) == 0
    assert summary["transfer_count"] == transfers["suggestion_count"] == 0
    assert summary["transfer_value"] == transfers["total_value"] == "0"

    # --- The schema file lists the route and its model. -----------
    schema = json.loads(_OPENAPI.read_text())
    assert "/api/supply-plan/summary" in schema["paths"]
    assert "get" in schema["paths"]["/api/supply-plan/summary"]
    assert "SupplyPlanSummaryResponse" in schema["components"]["schemas"]

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

    # The summary page still renders for the viewer — the route is
    # open to any signed-in user, so no admin-only control stands on
    # it — with the same body, and no token is still a 401.
    viewer_response = viewer.get(URL)
    assert viewer_response.status_code == 200, viewer_response.text
    assert viewer_response.json() == summary
    assert anon_reloaded.get(URL).status_code == 401
