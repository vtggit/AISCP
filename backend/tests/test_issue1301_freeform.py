"""Issue #1301 proof: the standing-grant run leaves out the order it
cannot value.

Everything runs against the test database.  The plan the run reads is
seeded the way the issue 1198 proof seeds it — the builder's sources
replaced (``recommendations_for_all`` answering the two Acumatica
item-sites of site 154 of known, different values, one Odoo line and
one more due Acumatica line in the smaller group, the same site and
supplier, whose recommendation has no unit cost) and a demand event
seeded so the release's as-of is a fixed data date — and the
Acumatica client's preview and create are replaced with recording
fakes the same way.  The Acumatica environment is set and writes are
allowed.  The grants are created and revoked through the #1173 routes
(``POST /api/standing-grants`` and
``POST /api/standing-grants/{id}/revoke``).

Every time is relative to the fixed aware UTC ``NOW`` the proof
passes to the run, or to the grant's ``created_at``; the proof never
reads the wall clock, and the data date is the fixed ``AS_OF`` before
that ``NOW``.

Proved here:

  * left out, never sent — under a grant whose limits fit both
    groups the run row is ``released`` with ``documents`` 1 and the
    other group's value, ``left_out`` holds exactly one entry — the
    mixed group's site and supplier, the value of its line that has a
    cost and the reason ``no_value`` — the fake's create receives
    exactly the other group's order, never the mixed group or the
    line without a cost, and that order's ``plan_releases`` row has
    the actor ``standing-grant:<id>`` and the outcome ``created``;
  * a cost of 0 or below — with that line's unit cost 0, and again
    with -1, a run leaves the mixed group out with ``no_value`` and
    the same value and sends the other group;
  * the skipped reason — under a grant whose ``max_document_value``
    is below the other group's value a run sends nothing and records
    ``skipped`` with the reason "every due document is over a limit
    or has no value", the other group left out with
    ``document_limit`` and the mixed group with ``no_value``;
  * a person's preview is unchanged — ``POST /api/supply-plan/
    release`` naming the mixed group's supplier, without ``confirm``,
    answers the body today's code answers: the line without a unit
    cost reads ``unit_cost`` "0" and ``value`` "0", and the group's
    ``value`` and the plan's ``total_value`` are the value of its
    line that has a cost;
  * the runs list — ``GET /api/standing-grants/runs?process=
    order_plan_release`` answers 200 and the ``no_value`` entry reads
    ``site_code``, ``supplier_code``, ``value`` (a number) and
    ``reason``;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role "user", asserted via ``GET /api/auth/me``) in a second pass
    after a full reload (a fresh ``TestClient`` booted the production
    way — the token the page would take from the URL hash as
    ``#access_token=...``, ``GET /api/auth/config`` answering publicly
    and ``GET /api/auth/me`` resolving the viewer): the release
    confirm and the grant writes are refused with 403 while the
    release preview and the runs list answer 200, and nothing new is
    written.
"""

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.db.connection import get_connection, get_cursor
from app.feed.acumatica_client import AcumaticaClient, AcumaticaWritesDisabledError
from app.feed.status import FEED_TABLES
from app.main import app
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.supplier_terms_postgres_repository import (
    SupplierTermsPostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)
from app.supply import standing_release

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
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

# The latest transaction date in the seeded data — deliberately not
# the server date: the release's as-of, the date the origin tag and
# the month name, is this data date.
AS_OF = date(2026, 10, 15)
AS_OF_MONTH = "2026-10"
# The run's clock: a fixed aware UTC instant, never the server clock.
NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)

SUPPLIER_A = "S-1301-1"
SUPPLIER_B = "S-1301-2"
SUPPLIER_C = "S-1301-3"
ITEM_A = "ITEM-1301-A"
ITEM_B = "ITEM-1301-B"
ITEM_C = "ITEM-1301-C"
ITEM_D = "ITEM-1301-D"
SITE_A = "154"
SITE_C = "159"
ORIGIN_A = f"AIERP order plan {AS_OF.isoformat()} site {SITE_A} vendor {SUPPLIER_A}"
ORIGIN_B = f"AIERP order plan {AS_OF.isoformat()} site {SITE_A} vendor {SUPPLIER_B}"

# The two Acumatica groups' known, different values: 125 x 4.5 and
# 100 x 10.01 (10.005 rounded half-up to cents, as the release does).
# The mixed group is the smaller one's — site 154 and
# SUPPLIER_A, holding (a) plus the extra due line (d) whose
# recommendation has no unit cost.
VALUE_A = "562.5"
VALUE_B = "1001.00"

# The skipped run's reason: every group left out, one of them for
# holding a line without a value.
SKIPPED_REASON = "every due document is over a limit or has no value"
ADMIN_DETAIL = "Role 'admin' is required to perform this action."


def _fresh(t: datetime) -> datetime:
    """One hour before ``t`` — within every grant's data-age hours."""
    return t - timedelta(hours=1)


def _run_rows() -> list[dict]:
    """Every standing_grant_runs row, oldest first, every column."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM standing_grant_runs ORDER BY id")
            columns = [c[0] for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _plan_release_rows() -> list[dict]:
    """Every stored plan_releases row, oldest first, every column."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM plan_releases ORDER BY id")
            columns = [c[0] for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


class FakeRecommendations:
    """The fake for the route module's ``recommendations_for_all``:
    the canned rows, all due now — (a) and (d) the two Acumatica
    lines of the smaller group of site 154, (b) the other Acumatica
    item-site of site 154 and (c) the Odoo line of site 159.
    (d)'s ``unit_cost`` is mutable: the proof prices it at 0 and at
    -1, and deletes the key for the no-unit-cost passes."""

    def __init__(self, rows: list) -> None:
        self.rows = rows

    def __call__(self, as_of=None):
        return self.rows


def _recommendation_d() -> dict:
    """(d): the extra due line of the smaller group — the builder is
    given no unit cost for it (no ``unit_cost`` key), which
    ``order_plan_release`` writes as ``Decimal(0)``."""
    return {
        "item_code": ITEM_D,
        "site_code": SITE_A,
        "as_of": AS_OF,
        "status": "ok",
        "reorder_point": 20.0,
        "reorder_point_units": 20,
        "safety_stock_units": 5,
        "inputs": {
            "d": 15.0,
            "sd_d": 3.0,
            "lt": 10.0,
            "sd_lt": 2.0,
            "z": 1.6449,
            "service_level": 0.95,
        },
        "on_hand_units": 10,
        "on_order_units": 0,
        "suggested_order_quantity": 0,
        "order_quantity": {
            "basis": "eoq",
            "clamped": None,
            "order_quantity": 40.0,
            "order_quantity_units": 40,
            "cover_days": 2.7,
            "annual_demand": 5475.0,
            "order_cost": 50.0,
            "holding_rate": 0.25,
            "arithmetic": "EOQ 40.0",
            "recommended_max_units": 400,
            "current_order_qty": None,
            "current_max_qty": None,
        },
    }


def _recommendations() -> list[dict]:
    return [
        {
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "as_of": AS_OF,
            "status": "ok",
            "reorder_point": 100.0,
            "reorder_point_units": 100,
            "safety_stock_units": 25,
            "inputs": {
                "d": 30.0,
                "sd_d": 6.0,
                "lt": 10.0,
                "sd_lt": 3.0,
                "z": 1.6449,
                "service_level": 0.95,
            },
            "on_hand_units": 50,
            "on_order_units": 0,
            "unit_cost": 4.5,
            "suggested_order_quantity": 0,
            "order_quantity": {
                "basis": "eoq",
                "clamped": None,
                "order_quantity": 119.4,
                "order_quantity_units": 120,
                "cover_days": 12.3,
                "annual_demand": 10950.0,
                "order_cost": 50.0,
                "holding_rate": 0.25,
                "unit_cost": 4.5,
                "arithmetic": "EOQ 119.4",
                "recommended_max_units": 400,
                "current_order_qty": None,
                "current_max_qty": None,
            },
        },
        {
            "item_code": ITEM_B,
            "site_code": SITE_A,
            "as_of": AS_OF,
            "status": "ok",
            "reorder_point": 60.0,
            "reorder_point_units": 60,
            "safety_stock_units": 15,
            "inputs": {
                "d": 20.0,
                "sd_d": 4.0,
                "lt": 7.0,
                "sd_lt": 1.5,
                "z": 1.6449,
                "service_level": 0.95,
            },
            "on_hand_units": 40,
            "on_order_units": 0,
            "unit_cost": 10.005,
            "suggested_order_quantity": 0,
            "order_quantity": {
                "basis": "eoq",
                "clamped": None,
                "order_quantity": 89.6,
                "order_quantity_units": 90,
                "cover_days": 9.2,
                "annual_demand": 7300.0,
                "order_cost": 50.0,
                "holding_rate": 0.25,
                "unit_cost": 10.005,
                "arithmetic": "EOQ 89.6",
                "recommended_max_units": 260,
                "current_order_qty": None,
                "current_max_qty": None,
            },
        },
        _recommendation_d(),
        {
            "item_code": ITEM_C,
            "site_code": SITE_C,
            "as_of": AS_OF,
            "status": "ok",
            "reorder_point": 40.0,
            "reorder_point_units": 40,
            "safety_stock_units": 10,
            "inputs": {
                "d": 10.0,
                "sd_d": 2.0,
                "lt": 5.0,
                "sd_lt": 1.0,
                "z": 1.6449,
                "service_level": 0.95,
            },
            "on_hand_units": 20,
            "on_order_units": 0,
            "unit_cost": 2.0,
            "suggested_order_quantity": 0,
            "order_quantity": {
                "basis": "eoq",
                "clamped": None,
                "order_quantity": 29.7,
                "order_quantity_units": 30,
                "cover_days": 30.0,
                "annual_demand": 3650.0,
                "order_cost": 50.0,
                "holding_rate": 0.25,
                "unit_cost": 2.0,
                "arithmetic": "EOQ 29.7",
                "recommended_max_units": 160,
                "current_order_qty": None,
                "current_max_qty": None,
            },
        },
    ]


def _seed_builder() -> None:
    """The tables the plan reads: the demand event (the as-of), the
    item-sites, the supplier terms and the suppliers."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO demand_events (id, business_key, item_code, "
            "site_code, quantity, occurred_on, event_type, source_system, "
            "synced_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 'acumatica', %s)",
            (
                "1301-demand-1",
                "1301-demand-1",
                ITEM_A,
                SITE_A,
                30.0,
                AS_OF,
                "shipment",
                _fresh(NOW),
            ),
        )
    ItemSitePostgresRepository().upsert_many(
        [
            # Two Acumatica item-sites at site 154 — (a) and the
            # extra due line (d) of the smaller group, (b) the other
            # group — and one Odoo line.
            {"item_code": ITEM_A, "site_code": SITE_A, "source_system": "acumatica"},
            {"item_code": ITEM_B, "site_code": SITE_A, "source_system": "acumatica"},
            {"item_code": ITEM_D, "site_code": SITE_A, "source_system": "acumatica"},
            {"item_code": ITEM_C, "site_code": SITE_C, "source_system": "odoo"},
        ]
    )
    SupplierTermsPostgresRepository().upsert_many(
        [
            {
                "business_key": f"acumatica:{ITEM_A}:{SUPPLIER_A}",
                "source_system": "acumatica",
                "item_code": ITEM_A,
                "supplier_code": SUPPLIER_A,
                "min_order_qty": 100,
                "max_order_qty": None,
                "lot_size": 25,
                "lead_time_days": 10,
                "is_default": True,
            },
            {
                "business_key": f"acumatica:{ITEM_B}:{SUPPLIER_B}",
                "source_system": "acumatica",
                "item_code": ITEM_B,
                "supplier_code": SUPPLIER_B,
                "min_order_qty": 100,
                "max_order_qty": None,
                "lot_size": 10,
                "lead_time_days": 10,
                "is_default": True,
            },
            {
                "business_key": f"acumatica:{ITEM_D}:{SUPPLIER_A}",
                "source_system": "acumatica",
                "item_code": ITEM_D,
                "supplier_code": SUPPLIER_A,
                "min_order_qty": 50,
                "max_order_qty": None,
                "lot_size": 10,
                "lead_time_days": 10,
                "is_default": True,
            },
            {
                "business_key": f"odoo:{ITEM_C}:{SUPPLIER_C}",
                "source_system": "odoo",
                "item_code": ITEM_C,
                "supplier_code": SUPPLIER_C,
                "min_order_qty": 20,
                "max_order_qty": None,
                "lot_size": 10,
                "lead_time_days": 5,
                "is_default": True,
            },
        ]
    )
    SupplierPostgresRepository().upsert_many(
        [
            {
                "supplier_code": SUPPLIER_A,
                "vendor_code": "V-1301-1",
                "name": "Acme Fasteners",
                "source_system": "acumatica",
            },
            {
                "supplier_code": SUPPLIER_B,
                "vendor_code": "V-1301-2",
                "name": "Bolt & Nut Works",
                "source_system": "acumatica",
            },
            {
                "supplier_code": SUPPLIER_C,
                "vendor_code": "V-1301-3",
                "name": "Odoo Widgets",
                "source_system": "odoo",
            },
        ]
    )


def _feed_row(table: str, synced_at: datetime) -> tuple[str, tuple]:
    """The one Acumatica row of each of the other feed tables."""
    if table == "on_hand":
        return (
            "INSERT INTO on_hand (id, item_code, site_code, "
            "quantity_on_hand, as_of, source_system, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, 'acumatica', %s)",
            ("1301-onhand-1", ITEM_A, SITE_A, 50.0, NOW, synced_at),
        )
    if table == "open_purchase_lines":
        return (
            "INSERT INTO open_purchase_lines (id, business_key, item_code, "
            "site_code, supplier_code, order_type, open_units, promised_on, "
            "ordered_on, as_of, source_system, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, 'purchase', %s, %s, %s, %s, "
            "'acumatica', %s)",
            (
                "1301-opl-1",
                "1301-opl-1",
                ITEM_B,
                SITE_A,
                SUPPLIER_B,
                0.0,
                AS_OF + timedelta(days=30),
                AS_OF,
                NOW,
                synced_at,
            ),
        )
    return (
        "INSERT INTO lead_time_observations (id, business_key, "
        "supplier_code, item_code, site_code, ordered_on, received_on, "
        "source_system, synced_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, 'acumatica', %s)",
        (
            "1301-lto-1",
            "1301-lto-1",
            SUPPLIER_A,
            ITEM_A,
            SITE_A,
            AS_OF - timedelta(days=20),
            AS_OF - timedelta(days=10),
            synced_at,
        ),
    )


def _seed_other_feeds() -> None:
    """One Acumatica row in each of the other feed tables the run
    checks for freshness."""
    with get_cursor() as cur:
        for table in FEED_TABLES:
            if table in ("item_sites", "demand_events"):
                continue  # _seed_builder holds their rows
            sql, params = _feed_row(table, _fresh(NOW))
            cur.execute(sql, params)


def _feed_freshness(synced_at: datetime) -> None:
    """One Acumatica row at ``synced_at`` in every feed table.

    Every Acumatica row of each table is stamped ``synced_at`` — the
    run's freshness check reads each table's newest such stamp — and
    a table that holds no Acumatica row gets its one back.
    """
    with get_cursor() as cur:
        for table in FEED_TABLES:
            cur.execute(
                f"UPDATE {table} SET synced_at = %s "
                "WHERE source_system = 'acumatica'",
                (synced_at,),
            )
            cur.execute(
                f"SELECT count(*) AS n FROM {table} "
                "WHERE source_system = 'acumatica'"
            )
            if cur.fetchone()["n"] == 0:
                if table in ("item_sites", "demand_events"):
                    raise AssertionError(f"the seed lost its {table} row")
                sql, params = _feed_row(table, synced_at)
                cur.execute(sql, params)


def _order_b():
    """(b) in the wire format the release body sends to Acumatica —
    the other group's order, the one the runs send."""
    return {
        "site_id": SITE_A,
        "vendor_id": SUPPLIER_B,
        "origin": ORIGIN_B,
        "lines": [{"item_code": ITEM_B, "quantity": 100, "unit_cost": 10.01}],
    }


def _mixed_order():
    """The mixed group in the wire format — (a) at its cost and (d)
    at the 0 the release writes for the missing unit cost.  The run
    never sends it; only the person's preview does."""
    return {
        "site_id": SITE_A,
        "vendor_id": SUPPLIER_A,
        "origin": ORIGIN_A,
        "lines": [
            {"item_code": ITEM_A, "quantity": 125, "unit_cost": 4.5},
            {"item_code": ITEM_D, "quantity": 50, "unit_cost": 0.0},
        ],
    }


def _mixed_group_body(outcome: str, order_nbr):
    return {
        "site_code": SITE_A,
        "supplier_code": SUPPLIER_A,
        "origin": ORIGIN_A,
        "lines": [
            {
                "item_code": ITEM_A,
                "quantity": 125,
                "unit_cost": "4.5",
                "value": VALUE_A,
                "source": "reorder_point",
            },
            # The line without a unit cost: 0 and 0, exactly as
            # today's code answers it.
            {
                "item_code": ITEM_D,
                "quantity": 50,
                "unit_cost": "0",
                "value": "0",
                "source": "reorder_point",
            },
        ],
        "value": VALUE_A,
        "outcome": outcome,
        "warehouse_code": "WHOLESALE",
        "vendor_code": "CONPERIPH",
        "order_nbr": order_nbr,
        "error": None,
    }


EXPECTED_PREVIEW_MIXED = {
    "month": AS_OF_MONTH,
    "groups": [_mixed_group_body("would_create", None)],
    "unreleasable": [],
    "total_value": VALUE_A,
    "acumatica_available": True,
}


def _grant_body(max_document_value, max_daily_value) -> dict:
    return {
        "process": "order_plan_release",
        "max_document_value": max_document_value,
        "max_daily_value": max_daily_value,
        "max_data_age_hours": 24,
    }


def _no_value_entry() -> dict:
    """The mixed group's left_out entry in the run's answer: its
    site and supplier, the value of its line that has a cost and the
    reason no_value."""
    return {
        "site_code": SITE_A,
        "supplier_code": SUPPLIER_A,
        "value": VALUE_A,
        "reason": "no_value",
    }


def _assert_other_group_sent(
    row: dict, grant_id: int, started_at: datetime, create_calls: list[list]
) -> None:
    """One run: released, documents 1, the other group's value, the
    mixed group left out with no_value, exactly the other group's
    order sent."""
    assert row["process"] == "order_plan_release"
    assert row["grant_id"] == grant_id
    assert row["started_at"] == started_at.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 1
    assert row["value"] == VALUE_B
    assert row["left_out"] == [_no_value_entry()]
    assert create_calls[-1] == [_order_b()]
    # Never the mixed group, never the line without a cost.
    assert ITEM_A not in str(create_calls)
    assert ITEM_D not in str(create_calls)
    releases = _plan_release_rows()
    assert releases[-1]["actor"] == f"standing-grant:{grant_id}"
    assert releases[-1]["outcome"] == "created"
    assert releases[-1]["supplier_code"] == SUPPLIER_B
    assert releases[-1]["origin"] == ORIGIN_B
    assert releases[-1]["value"] == Decimal(VALUE_B)
    assert releases[-1]["lines"] == [
        {
            "item_code": ITEM_B,
            "quantity": 100,
            "unit_cost": "10.01",
            "value": VALUE_B,
            "source": "reorder_point",
        }
    ]


def test_issue1301_freeform(monkeypatch, client) -> None:
    # Isolate the Acumatica environment, complete configuration,
    # writes on.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- Seed the tables the plan reads, plus the other feed tables
    # the run checks for freshness.
    _seed_builder()
    _seed_other_feeds()

    # --- The Acumatica client, replaced with fakes recording their
    # calls.  The write switch is consulted the way the real client
    # consults it: before anything else, before the empty-batch
    # shortcut.
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
                "order_nbr": f"PO-1301-{len(create_calls):03d}",
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

    # --- The builder's source, replaced: the canned rows, all due
    # now.  The run's as-of is the demand event's data date.
    import app.api.supply_plan as supply_plan

    fake_recommendations = FakeRecommendations(_recommendations())
    monkeypatch.setattr(supply_plan, "recommendations_for_all", fake_recommendations)
    d_row = next(row for row in fake_recommendations.rows if row["item_code"] == ITEM_D)
    assert "unit_cost" not in d_row

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # -- The grant in force, through the #1173 routes: the limits
    #    fit both groups.
    created = admin.post("/api/standing-grants", json=_grant_body(100000, 1000000))
    assert created.status_code == 201, created.text
    grant_1 = created.json()
    assert grant_1["process"] == "order_plan_release"
    assert grant_1["in_force"] is True
    grant_1_id = grant_1["id"]

    # -- Left out, never sent: the line without a unit cost. -------
    # The plan holds the two Acumatica groups of known, different
    # values and the Odoo line, plus the extra due Acumatica line in
    # the smaller group whose recommendation has no unit cost: the
    # smaller group is mixed — one line with a cost, one without —
    # and is left out with the reason no_value, reducing no
    # allowance, before either limit is consulted.
    row = standing_release.run_order_plan_grant(now=NOW)
    assert row["id"] is not None
    _assert_other_group_sent(row, grant_1_id, NOW, create_calls)
    assert create_calls == [[_order_b()]]
    assert len(_plan_release_rows()) == 1
    assert len(_run_rows()) == 1

    # -- A cost of 0 or below: the same left-out, the same value. --
    d_row["unit_cost"] = 0.0
    second_day = NOW + timedelta(days=1)
    _feed_freshness(_fresh(second_day))
    row = standing_release.run_order_plan_grant(now=second_day)
    _assert_other_group_sent(row, grant_1_id, second_day, create_calls)
    assert len(create_calls) == 2
    assert len(_plan_release_rows()) == 2
    assert len(_run_rows()) == 2

    d_row["unit_cost"] = -1.0
    third_day = NOW + timedelta(days=2)
    _feed_freshness(_fresh(third_day))
    row = standing_release.run_order_plan_grant(now=third_day)
    _assert_other_group_sent(row, grant_1_id, third_day, create_calls)
    assert len(create_calls) == 3
    assert len(_plan_release_rows()) == 3
    assert len(_run_rows()) == 3

    # -- The skipped reason: the document ceiling below the other
    #    group's value, the line back to no unit cost. -------------
    # Nothing is sent: the mixed group is left out for no_value and
    # the other group for document_limit, so the run records
    # skipped with the reason that names both.
    del d_row["unit_cost"]
    fourth_day = NOW + timedelta(days=3)
    _feed_freshness(_fresh(fourth_day))
    revoked = admin.post(f"/api/standing-grants/{grant_1_id}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["in_force"] is False
    created = admin.post("/api/standing-grants", json=_grant_body(800, 100000))
    assert created.status_code == 201, created.text
    grant_2_id = created.json()["id"]
    row = standing_release.run_order_plan_grant(now=fourth_day)
    assert row["grant_id"] == grant_2_id
    assert row["started_at"] == fourth_day.isoformat()
    assert row["outcome"] == "skipped"
    assert row["reason"] == SKIPPED_REASON
    assert row["documents"] == 0
    assert row["value"] == "0"
    assert row["left_out"] == [
        _no_value_entry(),
        {
            "site_code": SITE_A,
            "supplier_code": SUPPLIER_B,
            "value": VALUE_B,
            "reason": "document_limit",
        },
    ]
    assert len(_run_rows()) == 4
    assert len(_plan_release_rows()) == 3, "nothing sent on the skipped run"
    assert len(create_calls) == 3

    # -- The runs list: the no_value entry reads like the others. --
    runs_body = admin.get("/api/standing-grants/runs?process=order_plan_release")
    assert runs_body.status_code == 200, runs_body.text
    runs = runs_body.json()
    assert len(runs) == 4
    assert all(run["process"] == "order_plan_release" for run in runs)
    released_runs = [run for run in runs if run["outcome"] == "released"]
    assert len(released_runs) == 3
    for released_run in released_runs:
        assert released_run["documents"] == 1
        assert released_run["value"] == 1001.0
        assert released_run["left_out"] == [
            {
                "site_code": SITE_A,
                "supplier_code": SUPPLIER_A,
                "value": 562.5,
                "reason": "no_value",
            }
        ]
    skipped_run = runs[0]
    assert skipped_run["outcome"] == "skipped"
    assert skipped_run["reason"] == SKIPPED_REASON
    assert skipped_run["documents"] == 0
    assert skipped_run["value"] == 0.0
    no_value_entry = skipped_run["left_out"][0]
    assert no_value_entry["site_code"] == SITE_A
    assert no_value_entry["supplier_code"] == SUPPLIER_A
    assert isinstance(no_value_entry["value"], float)
    assert no_value_entry["value"] == 562.5
    assert no_value_entry["reason"] == "no_value"
    assert skipped_run["left_out"] == [
        no_value_entry,
        {
            "site_code": SITE_A,
            "supplier_code": SUPPLIER_B,
            "value": 1001.0,
            "reason": "document_limit",
        },
    ]

    # -- Identity: a second pass after a full reload, as the
    # NON-ADMIN (role "user").  The page is booted the production
    # way: the token it would take from the URL hash as
    # #access_token=... is the development token (role "user") sent
    # as its Bearer token; GET /api/auth/config answers publicly and
    # GET /api/auth/me resolves the viewer.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    assert viewer.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is True
    assert "user" in me.json()["user"]["roles"]
    assert "admin" not in me.json()["user"]["roles"]

    # The release page still renders for the viewer — the preview of
    # the mixed group's supplier, without confirm, answers the body
    # today's code answers: the line without a unit cost reads "0"
    # and "0", and the group's value and the plan's total_value are
    # the value of its line that has a cost — while the admin-only
    # confirm is absent (refused, nothing sent).
    preview = viewer.post(
        "/api/supply-plan/release", json={"supplier_codes": [SUPPLIER_A]}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json() == EXPECTED_PREVIEW_MIXED
    refused = viewer.post(
        "/api/supply-plan/release",
        json={"supplier_codes": [SUPPLIER_A], "confirm": True},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == ADMIN_DETAIL

    # The runs list still answers 200 for the viewer, while the
    # admin-only grant writes are absent (refused).
    runs = viewer.get("/api/standing-grants/runs?process=order_plan_release")
    assert runs.status_code == 200
    assert len(runs.json()) == 4
    refused_grant = viewer.post("/api/standing-grants", json=_grant_body(800, 100000))
    assert refused_grant.status_code == 403

    # Still nothing new written, no new call.
    assert len(_plan_release_rows()) == 3
    assert len(_run_rows()) == 4
    assert create_calls == [[_order_b()]] * 3
    assert preview_calls == [[_mixed_order()]]
