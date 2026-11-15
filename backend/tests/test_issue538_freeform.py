"""Issue #538 proof: releasing a month's planned orders as draft POs.

``app.aggregate.release.plan_release(month)`` plans the release of the
baseline plan's planned orders of ``month`` (no levers, over enough
months to include it, filtered on ``placed_month == month``): the
supplier of each item-site is the latest ``ordered_on`` line of its
purchase-line history (ties to the larger ``business_key``), orders
without a supplier go to ``unreleasable``, the rest are grouped one PO
per (site, supplier) — groups by site then supplier, lines by item
code — each group tagged with its origin and value.  Proven against
the test PostgreSQL database with migration 0021 applied, with
``planned_orders``, the purchase-line read (
``OpenPurchaseLinePostgresRepository.list_latest``) and the two
Acumatica batch functions (
``AcumaticaClient.preview_draft_purchase_orders`` /
``create_draft_purchase_orders``) monkeypatched — no network — and the
suite's admin and non-admin tokens.

The planned orders placed in the tested month are A@154 (10 units at
2.0), B@154 (5 at 10.0), C@159 (4 at no cost) and D@154 (1 at 3.0),
plus one order placed in the next month that must be ignored; the
purchase-line history gives A and B supplier 4851 (B with two lines,
the later one 4851, an earlier one 4870), C supplier 4870 and D none.

Proved here, in order:

  * the plan: two groups — ``154/4851`` with lines A (value 20.0) then
    B (value 50.0) and ``159/4870`` with line C (cost 0, value 0.0),
    both origins as specified, D unreleasable with the stated reason
    and ``total_value`` 70.0;
  * preview as the non-admin with the client preview returning
    ``would_create`` for both groups gives 200 with those outcomes and
    writes no audit row; with incomplete configuration it gives
    ``acumatica_available`` false and makes no client call;
  * confirm as the non-admin gives 403 with no client call; with
    writes off it gives 409 and records nothing; as the admin with the
    client returning ``created`` (PO003100) and ``exists`` (PO000777)
    it gives 200 with those outcomes and leaves exactly two audit rows
    with the right order numbers, outcomes, values, line snapshots and
    actor, each written as ``pending`` before the client call (the
    stubbed client checks this); a second confirm made while the
    advisory lock is held (taken by the test on its own connection)
    gives 409; a client auth failure gives 502 and leaves both of that
    release's audit rows ``aborted``;
  * AC-3: when the current plan's line snapshot for a PO differs from
    the stored snapshot of the same origin, the confirm must not
    return exists — it fails with a conflict (the group's row is
    ``error``, not ``exists``), re-confirming the same changed plan
    fails again, and once the plan matches the stored snapshot again
    the ``exists`` outcome is accepted;
  * AC-4: the ``value`` column of ``plan_releases`` is the exact
    ``NUMERIC`` type (same data type and precision as the other
    monetary column, ``purchase_budgets.amount``), no floating-point
    type anywhere in the table, and the ``outcome`` CHECK constraint
    refuses any outcome outside the five allowed;
  * the review fixes: the plan's monetary fields are exact decimals
    and never binary floats — the fake planned orders carry the
    ``Decimal`` unit costs the database hands back, ``plan_release``
    returns ``Decimal`` values, the confirm writes its audit rows
    without crashing on those Decimals (the jsonb line snapshot keeps
    its monetary fields as exact decimal strings, never
    floating-point numbers), the repository hands the stored value
    back as a ``Decimal``, and the API renders every monetary value
    as an exact decimal string;
  * validation: a month 13 months ahead, a past month and ``2026-13``
    each give 422, a body with an extra field gives 422 and a missing
    body gives 422 — none writes an audit row;
  * the audit: ``GET /api/aggregate/release/audit`` as the admin lists
    the rows newest first (every column), as the non-admin gives 403;
  * the identity requirement: the proof also runs as a NON-ADMIN (role
    ``user``) in a second pass after a full reload — the page boots
    the production way (the token it would take from the URL hash as
    ``#access_token=...``, the bare development token, with
    ``/api/auth/config`` answering publicly and ``/api/auth/me``
    resolving the viewer for that user), the admin-only controls are
    absent for the viewer (confirm and audit refused 403, nothing
    written) while the page still renders (its aggregate data and the
    preview the viewer may run answer 200, unauthenticated /api traffic
    still 401).
"""

import os
from datetime import date
from decimal import Decimal

import psycopg2
import psycopg2.errors
from fastapi.testclient import TestClient

import app.aggregate.release as release_module
from app.db.connection import get_connection_params
from app.feed.acumatica_client import (
    AcumaticaAuthError,
    AcumaticaClient,
    AcumaticaWritesDisabledError,
)
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.plan_releases_postgres_repository import (
    RELEASE_LOCK_KEY,
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
AUDIT = "/api/aggregate/release/audit"
NO_SUPPLIER_REASON = "no supplier in purchase history"
ADMIN_DETAIL = "Role 'admin' is required to perform this action."


def _shift_month(delta: int) -> str:
    """The YYYY-MM of the current month shifted by ``delta`` months."""
    today = date.today()
    index = today.year * 12 + (today.month - 1) + delta
    year, month = divmod(index, 12)
    return f"{year:04d}-{month + 1:02d}"


def _result(origin: str, outcome: str, order_nbr, site: str, lines_count: int) -> dict:
    """One canned per-order batch result, as the client would return it."""
    return {
        "origin": origin,
        "outcome": outcome,
        "order_nbr": order_nbr,
        "status": "On Hold",
        "warehouse_code": "WHOLESALE" if site == "154" else "RETAIL",
        "vendor_code": "CONPERIPH",
        "line_count": lines_count,
        "error": None,
    }


def test_issue538_freeform(monkeypatch, client, admin_token, user_token) -> None:
    # Isolate the Acumatica environment, complete configuration.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")

    month = _shift_month(0)
    ORIGIN_154 = f"AIERP plan {month} site 154 vendor 4851"
    ORIGIN_159 = f"AIERP plan {month} site 159 vendor 4870"
    LINES_154 = [
        {"item_code": "A", "quantity": 10, "unit_cost": "2.0", "value": "20.0"},
        {"item_code": "B", "quantity": 5, "unit_cost": "10.0", "value": "50.0"},
    ]
    LINES_159 = [
        {"item_code": "C", "quantity": 4, "unit_cost": "0", "value": "0"},
    ]
    LINES_154_STALE = [
        {"item_code": "A", "quantity": 10, "unit_cost": "2.0", "value": "20.0"},
        {"item_code": "B", "quantity": 6, "unit_cost": "10.0", "value": "60.0"},
    ]

    # -- the four monkeypatched seams -----------------------------------
    planned_calls: list[dict] = []
    plan_variant = {"b_quantity": 5}

    def planned(item, site, placed, quantity, unit_cost):
        return {
            "item_code": item,
            "site_code": site,
            "placed_month": placed,
            "arrival_month": placed,
            "quantity": quantity,
            "unit_cost": unit_cost,
            "value": quantity * (unit_cost if unit_cost is not None else 0),
            "d": 1.0,
            "month_end_on_hand": 0,
        }

    def fake_planned_orders(
        as_of=None,
        months=6,
        service_level=None,
        demand_multiplier=None,
        lead_time_multiplier=None,
    ):
        planned_calls.append(
            {
                "as_of": as_of,
                "months": months,
                "service_level": service_level,
                "demand_multiplier": demand_multiplier,
                "lead_time_multiplier": lead_time_multiplier,
            }
        )
        return [
            # The unit costs are the exact Decimals the database hands
            # back for a NUMERIC column — the confirm must store and
            # return them without crashing or losing exactness.
            planned("A", "154", month, 10, Decimal("2.0")),
            planned("B", "154", month, plan_variant["b_quantity"], Decimal("10.0")),
            planned("C", "159", month, 4, None),
            planned("D", "154", month, 1, Decimal("3.0")),
            # Placed in the next month: not part of this release.
            planned("E", "154", _shift_month(1), 2, Decimal("1.0")),
        ]

    monkeypatch.setattr(release_module, "planned_orders", fake_planned_orders)

    as_of = date(2026, 10, 1).isoformat() + "T00:00:00+00:00"
    history = [
        {
            "id": "1",
            "item_code": "A",
            "site_code": "154",
            "supplier_code": "4851",
            "order_type": "STOCK",
            "open_units": 0.0,
            "promised_on": None,
            "ordered_on": date(2026, 7, 15),
            "business_key": "A-1",
            "as_of": as_of,
            "source_system": "acumatica",
            "synced_at": as_of,
        },
        {
            "id": "2",
            "item_code": "B",
            "site_code": "154",
            "supplier_code": "4870",
            "order_type": "STOCK",
            "open_units": 0.0,
            "promised_on": None,
            "ordered_on": date(2026, 7, 20),
            "business_key": "B-1",
            "as_of": as_of,
            "source_system": "acumatica",
            "synced_at": as_of,
        },
        {
            "id": "3",
            "item_code": "B",
            "site_code": "154",
            "supplier_code": "4851",
            "order_type": "STOCK",
            "open_units": 0.0,
            "promised_on": None,
            "ordered_on": date(2026, 8, 1),
            "business_key": "B-2",
            "as_of": as_of,
            "source_system": "acumatica",
            "synced_at": as_of,
        },
        {
            "id": "4",
            "item_code": "C",
            "site_code": "159",
            "supplier_code": "4870",
            "order_type": "STOCK",
            "open_units": 0.0,
            "promised_on": None,
            "ordered_on": date(2026, 6, 30),
            "business_key": "C-1",
            "as_of": as_of,
            "source_system": "acumatica",
            "synced_at": as_of,
        },
    ]
    history_reads = []

    def fake_list_latest(self):
        history_reads.append(True)
        return [dict(row) for row in history]

    monkeypatch.setattr(
        OpenPurchaseLinePostgresRepository, "list_latest", fake_list_latest
    )

    calls: list[tuple[str, list]] = []
    create_results = {"value": None}
    create_exc = {"value": None}

    def writes_enabled() -> bool:
        return os.environ.get("ACUMATICA_ALLOW_WRITES", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def newest_outcomes(limit: int) -> list:
        conn = psycopg2.connect(**get_connection_params())
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT outcome FROM plan_releases " "ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
                return [row[0] for row in cur.fetchall()]
        finally:
            conn.close()

    def fake_preview(self, orders):
        if not orders:
            return []
        calls.append(("preview", orders))
        return [
            {
                "origin": entry["origin"],
                "outcome": "would_create",
                "order_nbr": None,
                "status": None,
                "warehouse_code": (
                    "WHOLESALE" if entry["site_id"] == "154" else "RETAIL"
                ),
                "vendor_code": "CONPERIPH",
                "line_count": len(entry["lines"]),
                "error": None,
            }
            for entry in orders
        ]

    def fake_create(self, orders):
        # The write switch, consulted exactly as the client consults
        # it: before anything else, and before any request.
        if not writes_enabled():
            raise AcumaticaWritesDisabledError(
                "Acumatica writes are disabled; set ACUMATICA_ALLOW_WRITES "
                "to a truthy value to enable purchase order creation"
            )
        if not orders:
            return []
        calls.append(("create", orders))
        # Each row of this release is written pending BEFORE the
        # client work: the newest len(orders) rows must be pending.
        assert newest_outcomes(len(orders)) == ["pending"] * len(orders)
        if create_exc["value"] is not None:
            raise create_exc["value"]
        return create_results["value"]

    monkeypatch.setattr(AcumaticaClient, "preview_draft_purchase_orders", fake_preview)
    monkeypatch.setattr(AcumaticaClient, "create_draft_purchase_orders", fake_create)

    from app.main import app

    # The suite's admin and non-admin tokens.
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    repo = PlanReleasePostgresRepository()

    # -- 1. the plan: preview as the non-admin ---------------------------
    response = viewer.post(URL, json={"month": month})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["month"] == month
    assert plan["acumatica_available"] is True
    # The total is the exact decimal string, not a binary float.
    assert plan["total_value"] == "70.0"
    assert plan["unreleasable"] == [
        {"item_code": "D", "site_code": "154", "reason": NO_SUPPLIER_REASON}
    ]
    assert [
        (group["site_code"], group["supplier_code"]) for group in plan["groups"]
    ] == [("154", "4851"), ("159", "4870")]
    group_154, group_159 = plan["groups"]
    assert group_154["origin"] == ORIGIN_154
    assert group_154["lines"] == LINES_154
    assert group_154["value"] == "70.0"
    assert group_154["outcome"] == "would_create"
    assert group_154["warehouse_code"] == "WHOLESALE"
    assert group_154["vendor_code"] == "CONPERIPH"
    assert group_154["order_nbr"] is None
    assert group_154["error"] is None
    assert group_159["origin"] == ORIGIN_159
    assert group_159["lines"] == LINES_159
    assert group_159["value"] == "0"
    assert group_159["outcome"] == "would_create"
    assert group_159["warehouse_code"] == "RETAIL"
    assert group_159["vendor_code"] == "CONPERIPH"
    # The baseline plan, no levers, over exactly enough months to
    # include the month; the purchase-line history read once.
    assert planned_calls == [
        {
            "as_of": None,
            "months": 1,
            "service_level": None,
            "demand_multiplier": None,
            "lead_time_multiplier": None,
        }
    ]
    assert history_reads == [True]
    # The function itself hands back exact Decimals (review fix:
    # monetary values are computed and returned as exact numerics,
    # never binary floats).
    direct = release_module.plan_release(month)
    assert isinstance(direct["total_value"], Decimal)
    assert direct["total_value"] == Decimal("70.0")
    assert direct["groups"][0]["value"] == Decimal("70.0")
    assert isinstance(direct["groups"][0]["value"], Decimal)
    line_a = direct["groups"][0]["lines"][0]
    assert line_a["unit_cost"] == Decimal("2.0")
    assert line_a["value"] == Decimal("20.0")
    assert isinstance(line_a["unit_cost"], Decimal)
    assert isinstance(line_a["value"], Decimal)
    # The preview is called exactly once with the two groups' orders —
    # lines exactly {item_code, quantity, unit_cost}.
    assert [name for name, _ in calls] == ["preview"]
    assert calls[0][1] == [
        {
            "site_id": "154",
            "vendor_id": "4851",
            "origin": ORIGIN_154,
            "lines": [
                {"item_code": "A", "quantity": 10, "unit_cost": 2.0},
                {"item_code": "B", "quantity": 5, "unit_cost": 10.0},
            ],
        },
        {
            "site_id": "159",
            "vendor_id": "4870",
            "origin": ORIGIN_159,
            "lines": [{"item_code": "C", "quantity": 4, "unit_cost": 0}],
        },
    ]
    # A preview writes no audit row.
    assert repo.list_newest() == []

    # -- 2. preview with incomplete configuration ------------------------
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    calls.clear()
    response = viewer.post(URL, json={"month": month})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["acumatica_available"] is False
    for group in plan["groups"]:
        assert group["outcome"] is None
        assert group["warehouse_code"] is None
        assert group["vendor_code"] is None
        assert group["order_nbr"] is None
        assert group["error"] is None
    assert calls == []  # no client call
    assert repo.list_newest() == []
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")

    # -- 3. confirm as the non-admin: 403, no client call ----------------
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")
    calls.clear()
    response = viewer.post(URL, json={"month": month, "confirm": True})
    assert response.status_code == 403
    assert response.json() == {"detail": ADMIN_DETAIL}
    assert calls == []
    assert repo.list_newest() == []

    # -- 4. confirm with writes off: 409, nothing recorded ---------------
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES", raising=False)
    calls.clear()
    response = admin.post(URL, json={"month": month, "confirm": True})
    assert response.status_code == 409
    assert "writes are disabled" in response.json()["detail"]
    assert calls == []
    assert repo.list_newest() == []  # records nothing

    # -- 5. confirm as the admin: created + exists -----------------------
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "true")
    create_results["value"] = [
        _result(ORIGIN_154, "created", "PO003100", "154", 2),
        _result(ORIGIN_159, "exists", "PO000777", "159", 1),
    ]
    calls.clear()
    response = admin.post(URL, json={"month": month, "confirm": True})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["acumatica_available"] is True
    assert [group["outcome"] for group in plan["groups"]] == ["created", "exists"]
    assert plan["groups"][0]["order_nbr"] == "PO003100"
    assert plan["groups"][1]["order_nbr"] == "PO000777"
    assert plan["groups"][1]["warehouse_code"] == "RETAIL"
    # The confirm batch was called exactly once, with every group.
    assert [name for name, _ in calls] == ["create"]
    assert len(calls[0][1]) == 2
    # Exactly two audit rows, with the right order numbers, outcomes,
    # values, line snapshots and actor; each was written pending before
    # the client call (fake_create asserts that).
    rows = repo.list_newest()
    assert len(rows) == 2
    by_origin = {row["origin"]: row for row in rows}
    row_154 = by_origin[ORIGIN_154]
    assert row_154["outcome"] == "created"
    assert row_154["order_nbr"] == "PO003100"
    assert row_154["warehouse_code"] == "WHOLESALE"
    assert row_154["vendor_code"] == "CONPERIPH"
    # The review fixes: the stored value comes back as the exact
    # Decimal, not a binary float, and the confirm that stored the
    # Decimal unit costs did not crash.
    assert isinstance(row_154["value"], Decimal)
    assert row_154["value"] == Decimal("70.0")
    assert row_154["line_count"] == 2
    assert row_154["lines"] == LINES_154
    assert row_154["actor"] == "admin"
    assert row_154["month"] == month
    assert row_154["site_code"] == "154"
    assert row_154["supplier_code"] == "4851"
    assert row_154["error"] is None
    row_159 = by_origin[ORIGIN_159]
    assert row_159["outcome"] == "exists"
    assert row_159["order_nbr"] == "PO000777"
    assert isinstance(row_159["value"], Decimal)
    assert row_159["value"] == Decimal(0)
    assert row_159["line_count"] == 1
    assert row_159["lines"] == LINES_159
    assert row_159["actor"] == "admin"
    # The exact line snapshot in jsonb keeps its monetary fields as
    # decimal strings (no float8 number anywhere in the snapshot).
    conn = psycopg2.connect(**get_connection_params())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT jsonb_typeof(lines->0->'unit_cost'), "
                "jsonb_typeof(lines->0->'value') "
                "FROM plan_releases WHERE origin = %s AND outcome = 'created'",
                (ORIGIN_154,),
            )
            snapshot_types = cur.fetchone()
    finally:
        conn.close()
    assert snapshot_types == ("string", "string")

    # -- 6. a second confirm while the advisory lock is held: 409 --------
    lock_conn = psycopg2.connect(**get_connection_params())
    try:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (RELEASE_LOCK_KEY,))
            assert cur.fetchone()[0] is True
        calls.clear()
        response = admin.post(URL, json={"month": month, "confirm": True})
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"]
        assert calls == []  # nothing is written to Acumatica
        assert len(repo.list_newest()) == 2  # nothing is recorded
    finally:
        lock_conn.close()

    # -- 7. a client auth failure: 502, the release's rows aborted -------
    create_exc["value"] = AcumaticaAuthError(
        "the Acumatica login was rejected with HTTP 401"
    )
    calls.clear()
    response = admin.post(URL, json={"month": month, "confirm": True})
    assert response.status_code == 502
    assert "401" in response.json()["detail"]
    rows = repo.list_newest()
    assert len(rows) == 4
    # Both rows of that release — the two newest — are aborted.
    assert [row["outcome"] for row in rows[:2]] == ["aborted", "aborted"]
    assert [row["origin"] for row in rows[:2]] == [ORIGIN_159, ORIGIN_154]
    assert all(row["error"] and "401" in row["error"] for row in rows[:2])
    create_exc["value"] = None

    # -- 8. AC-3: a changed snapshot must not be reported as exists ------
    # B's quantity changes (5 -> 6): same origin, different lines.
    # Acumatica still holds the PO under the origin, so the client
    # answers exists — but the stored snapshot differs.
    plan_variant["b_quantity"] = 6
    create_results["value"] = [
        _result(ORIGIN_154, "exists", "PO003100", "154", 2),
        _result(ORIGIN_159, "exists", "PO000777", "159", 1),
    ]
    calls.clear()
    response = admin.post(URL, json={"month": month, "confirm": True})
    # The confirm fails with a conflict: it does not return exists.
    assert response.status_code == 409
    assert ORIGIN_154 in response.json()["detail"]
    assert "re-confirmation" in response.json()["detail"]
    rows = repo.list_newest()
    assert len(rows) == 6
    # The stale group's row is error (never exists) with the changed
    # line snapshot; the unchanged group's row is exists.
    stale_row = rows[1]
    assert stale_row["origin"] == ORIGIN_154
    assert stale_row["outcome"] == "error"
    assert stale_row["order_nbr"] is None
    assert stale_row["lines"] == LINES_154_STALE
    assert isinstance(stale_row["value"], Decimal)
    assert stale_row["value"] == Decimal("80.0")
    assert rows[0]["origin"] == ORIGIN_159
    assert rows[0]["outcome"] == "exists"
    assert rows[0]["order_nbr"] == "PO000777"
    # Re-confirming the same changed plan fails the same way: the
    # conflict stands until the plan matches the existing PO again.
    calls.clear()
    response = admin.post(URL, json={"month": month, "confirm": True})
    assert response.status_code == 409
    rows = repo.list_newest()
    assert len(rows) == 8
    assert rows[1]["origin"] == ORIGIN_154
    assert rows[1]["outcome"] == "error"

    # The plan back to the stored snapshot: exists is accepted again.
    plan_variant["b_quantity"] = 5
    calls.clear()
    response = admin.post(URL, json={"month": month, "confirm": True})
    assert response.status_code == 200, response.text
    plan = response.json()
    assert [group["outcome"] for group in plan["groups"]] == ["exists", "exists"]
    assert plan["groups"][0]["order_nbr"] == "PO003100"
    assert plan["groups"][1]["order_nbr"] == "PO000777"
    rows = repo.list_newest()
    assert len(rows) == 10

    # -- 9. validation: 422s, nothing written ----------------------------
    for body in (
        {"month": _shift_month(13)},  # 13 months ahead
        {"month": _shift_month(-1)},  # a past month
        {"month": "2026-13"},  # not a real month
        {"month": month, "confirm": False, "extra": 1},  # extra field
    ):
        response = client.post(URL, json=body)
        assert response.status_code == 422, (body, response.text)
    response = client.post(URL)  # a missing body
    assert response.status_code == 422
    assert len(repo.list_newest()) == 10

    # -- 10. AC-4: exact numeric money, no floating-point types ----------
    conn = psycopg2.connect(**get_connection_params())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_type, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_name = 'plan_releases' AND column_name = 'value'"
            )
            value_type = cur.fetchone()
            cur.execute(
                "SELECT data_type, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_name = 'purchase_budgets' AND column_name = 'amount'"
            )
            budget_type = cur.fetchone()
            cur.execute(
                "SELECT DISTINCT data_type FROM information_schema.columns "
                "WHERE table_name = 'plan_releases'"
            )
            data_types = {row[0] for row in cur.fetchall()}
    finally:
        conn.close()
    # The outcome CHECK refuses any other outcome.
    conn = psycopg2.connect(**get_connection_params())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO plan_releases "
                "(month, site_code, supplier_code, origin, outcome, "
                " line_count, value, lines, actor) "
                "VALUES ('2026-10', '1', '1', 'x', 'weird', 1, 0, '[]', 't')"
            )
        conn.commit()
        raised = False
    except psycopg2.errors.CheckViolation:
        conn.rollback()
        raised = True
    finally:
        conn.close()
    assert value_type[0] == "numeric"
    assert value_type == budget_type  # same type and precision as the budget amount
    assert not data_types & {"double precision", "real", "float4", "float8"}
    assert raised

    # -- 11. the audit: newest first for the admin, 403 for the rest -----
    response = admin.get(AUDIT)
    assert response.status_code == 200, response.text
    audit = response.json()
    assert len(audit) == 10
    # Newest first: created_at DESC, id DESC.
    assert [row["id"] for row in audit] == sorted(
        (row["id"] for row in audit), reverse=True
    )
    assert [row["outcome"] for row in audit] == [
        "exists",  # 159, snapshot matched again
        "exists",  # 154, snapshot matched again
        "exists",  # 159, second stale confirm
        "error",  # 154, second stale confirm
        "exists",  # 159, first stale confirm
        "error",  # 154, first stale confirm
        "aborted",  # 159, auth failure
        "aborted",  # 154, auth failure
        "exists",  # 159, first confirm
        "created",  # 154, first confirm
    ]
    for row in audit:
        assert set(row) == {
            "id",
            "month",
            "site_code",
            "supplier_code",
            "origin",
            "warehouse_code",
            "vendor_code",
            "order_nbr",
            "outcome",
            "error",
            "line_count",
            "value",
            "lines",
            "actor",
            "created_at",
            "updated_at",
        }
    response = viewer.get(AUDIT)
    assert response.status_code == 403
    assert response.json() == {"detail": ADMIN_DETAIL}

    # -- 12. Identity: the proof also runs as a NON-ADMIN ----------------
    # Second pass after a full reload: the page boots the production
    # way — the token it would take from the URL hash as
    # #access_token=... (the bare development token), with
    # /api/auth/config answering publicly and /api/auth/me resolving
    # (dev-mode auth standing in for the IdP) the non-admin viewer for
    # that user.
    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    viewer_reloaded = TestClient(app, headers={"Authorization": f"Bearer {dev_token}"})
    anon = TestClient(app)
    assert anon.get("/api/auth/config").status_code == 200  # public, pre-token
    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == "dev-user-1"
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin-only controls are absent for the viewer: confirm and
    # audit are refused 403, nothing is written.
    before = len(repo.list_newest())
    response = viewer_reloaded.post(URL, json={"month": month, "confirm": True})
    assert response.status_code == 403, response.text
    assert response.json() == {"detail": ADMIN_DETAIL}
    response = viewer_reloaded.get(AUDIT)
    assert response.status_code == 403, response.text
    assert len(repo.list_newest()) == before
    # The page still renders for the viewer: the aggregate data it
    # loads answers 200, the preview the viewer may run answers 200,
    # and unauthenticated /api traffic is still 401.
    assert viewer_reloaded.get("/api/aggregate/projection").status_code == 200
    response = viewer_reloaded.post(URL, json={"month": month})
    assert response.status_code == 200, response.text
    assert response.json()["month"] == month
    assert anon.post(URL, json={"month": month}).status_code == 401
