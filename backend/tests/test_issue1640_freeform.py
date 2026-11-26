"""Issue #1640 proof: the standing-grant run records what went out.

Everything runs against the test database.  The order-plan run is
seeded the way the issue 1198 proof seeds it — the builder's sources
replaced (``recommendations_for_all`` answering three Acumatica
item-sites of site 154 at known, different values and one Odoo line)
and a demand event seeded so the release's as-of is a fixed data
date — and ``AcumaticaClient.create_draft_purchase_orders`` replaced
with a fake that answers per group.  The transfer run is seeded the
way the issue 1243 proof seeds it — the same replaced source
answering three Acumatica transfer pairs at known, different values
on three site pairs and one Odoo pair, an item-site read for the
same source systems and a demand event for the as-of — and
``AcumaticaClient.create_transfer_orders`` replaced with the same
kind of per-group fake.  The Acumatica environment is set and
writes are allowed.  The grants are created through the #1173 route
(``POST /api/standing-grants``) the way
``test_issue1198_freeform.py`` calls it.

Proved here, every field read strictly:

  * the order-plan run — a grant covering all three groups: with
    every group answered ``created`` the row is what it is today
    (``released``, ``documents`` 3, the three groups' summed value,
    no ``left_out``); with 2 of 3 groups ``created`` and the third
    ``error`` the row is ``released`` with ``documents`` 2, the two
    groups' summed value and the third group in ``left_out`` with
    the reason ``release_error``; with all 3 ``error`` the row is
    ``failed`` with the reason "every release group failed: <the
    first failed group's error>" and the chosen groups' count and
    value, as a failed send's are, the three failed groups in
    ``left_out`` with ``release_error``;
  * the transfer run — the same three results, the failed group
    looked up by its two sites, and a group answered ``exists``
    that carries the stale-lines error (a stored line snapshot of
    the same origin the planned lines no longer match) counted as
    failed while the error-free ``exists`` groups count as sent;
  * the exceptions inbox — after the partial order-plan run,
    ``GET /api/exceptions`` lists the standing-grant item for it,
    the run's ``left_out`` not empty being enough, with no change
    of the inbox's own.
"""

import os
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

import app.api.supply_plan as supply_plan
from app.db.connection import get_cursor
from app.feed.acumatica_client import AcumaticaClient
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
from app.repositories.transfer_releases_postgres_repository import (
    TransferReleasePostgresRepository,
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

# The latest transaction date in the seeded data — deliberately not
# the server date: the as-of of every origin tag the runs build.
AS_OF = date(2026, 10, 15)
# The runs' clock: fixed aware UTC instants, never the server clock.
NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)

# --- The order plan: three Acumatica groups of site 154, one Odoo
# line of site 159 that never goes out. -----------------------------
SITE = "154"
SITE_ODOO = "159"
ITEM_A = "ITEM-1640-A"
ITEM_B = "ITEM-1640-B"
ITEM_C = "ITEM-1640-C"
ITEM_D = "ITEM-1640-D"
SUPPLIER_A = "S-1640-1"
SUPPLIER_B = "S-1640-2"
SUPPLIER_C = "S-1640-3"
SUPPLIER_D = "S-1640-4"
ORIGIN_A = f"AIERP order plan {AS_OF.isoformat()} site {SITE} vendor {SUPPLIER_A}"
ORIGIN_B = f"AIERP order plan {AS_OF.isoformat()} site {SITE} vendor {SUPPLIER_B}"
ORIGIN_C = f"AIERP order plan {AS_OF.isoformat()} site {SITE} vendor {SUPPLIER_C}"

# The three groups' known, different values: 125 x 4.5, 100 x 10.01
# (10.005 rounded half-up to cents, as the release does) and
# 30 x 2.0.
VALUE_A = "562.5"
VALUE_B = "1001.00"
VALUE_C = "60.0"
TOTAL_ORDER = "1623.50"
SENT_ORDER = "1563.50"  # A and B: the partial run's sent value.

ORDER_ERROR_A = "the Acumatica create refused the order for site 154 vendor S-1640-1"
ORDER_ERROR_B = "the Acumatica create refused the order for site 154 vendor S-1640-2"
ORDER_ERROR_C = "the Acumatica create refused the order for site 154 vendor S-1640-3"

# --- The transfer: three valued Acumatica pairs, one Odoo pair. ----
ITEM_T1 = "ITEM-1640-T1"
ITEM_T2 = "ITEM-1640-T2"
ITEM_T3 = "ITEM-1640-T3"
ITEM_T4 = "ITEM-1640-T4"
# The pairs, in the suggestions' order (value descending): T3
# 161 to 162 at 600.00, T2 154 to 161 at 450.00, T1 154 to 159 at
# 300.00.
PAIR_T3 = ("161", "162")
PAIR_T2 = ("154", "161")
PAIR_T1 = ("154", "159")
ORIGIN_T3 = f"AIERP transfer {AS_OF.isoformat()} site {PAIR_T3[0]} to {PAIR_T3[1]}"
ORIGIN_T2 = f"AIERP transfer {AS_OF.isoformat()} site {PAIR_T2[0]} to {PAIR_T2[1]}"
ORIGIN_T1 = f"AIERP transfer {AS_OF.isoformat()} site {PAIR_T1[0]} to {PAIR_T1[1]}"

VALUE_T3 = "600.00"
VALUE_T2 = "450.00"
VALUE_T1 = "300.00"
TOTAL_TRANSFER = "1350.00"
SENT_TRANSFER = "750.00"  # T2 and T1: the partial run's sent value.

TRANSFER_ERROR_T3 = "the Acumatica transfer create refused the order from 161 to 162"
TRANSFER_ERROR_T2 = "the Acumatica transfer create refused the order from 154 to 161"
TRANSFER_ERROR_T1 = "the Acumatica transfer create refused the order from 154 to 159"
STALE_ERROR_T3 = (
    f"the existing transfer order for origin {ORIGIN_T3} "
    "no longer matches the planned lines; "
    "re-confirm is required"
)

# The wire orders the fakes receive, in the runs' send order.
ORDER_A_WIRE = {
    "site_id": SITE,
    "vendor_id": SUPPLIER_A,
    "origin": ORIGIN_A,
    "lines": [{"item_code": ITEM_A, "quantity": 125, "unit_cost": 4.5}],
}
ORDER_B_WIRE = {
    "site_id": SITE,
    "vendor_id": SUPPLIER_B,
    "origin": ORIGIN_B,
    "lines": [{"item_code": ITEM_B, "quantity": 100, "unit_cost": 10.01}],
}
ORDER_C_WIRE = {
    "site_id": SITE,
    "vendor_id": SUPPLIER_C,
    "origin": ORIGIN_C,
    "lines": [{"item_code": ITEM_C, "quantity": 30, "unit_cost": 2.0}],
}
T3_WIRE = {
    "from_site_id": PAIR_T3[0],
    "to_site_id": PAIR_T3[1],
    "origin": ORIGIN_T3,
    "lines": [{"item_code": ITEM_T3, "quantity": 100}],
}
T2_WIRE = {
    "from_site_id": PAIR_T2[0],
    "to_site_id": PAIR_T2[1],
    "origin": ORIGIN_T2,
    "lines": [{"item_code": ITEM_T2, "quantity": 150}],
}
T1_WIRE = {
    "from_site_id": PAIR_T1[0],
    "to_site_id": PAIR_T1[1],
    "origin": ORIGIN_T1,
    "lines": [{"item_code": ITEM_T1, "quantity": 120}],
}


def _fresh(t: datetime) -> datetime:
    """One hour before ``t`` — within the grants' 24 data-age hours."""
    return t - timedelta(hours=1)


def _plan_release_rows() -> list[dict]:
    """Every stored plan_releases row, oldest first, every column."""
    from app.db.connection import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM plan_releases ORDER BY id")
            columns = [c[0] for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _transfer_release_rows() -> list[dict]:
    """Every stored transfer_releases row, oldest first, every column."""
    from app.db.connection import get_connection

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM transfer_releases ORDER BY id")
            columns = [c[0] for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _order_rec(item_code, *, rop, on_hand, unit_cost, oq_units):
    """One order-plan recommendation row of the fake source: the
    item-site at site 154, due now, at the given cost and order
    quantity."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "as_of": AS_OF,
        "status": "ok",
        "reorder_point": float(rop),
        "reorder_point_units": rop,
        "safety_stock_units": 25,
        "inputs": {
            "d": 30.0,
            "sd_d": 6.0,
            "lt": 10.0,
            "sd_lt": 3.0,
            "z": 1.6449,
            "service_level": 0.95,
        },
        "on_hand_units": on_hand,
        "on_order_units": 0,
        "unit_cost": unit_cost,
        "suggested_order_quantity": 0,
        "order_quantity": {
            "basis": "eoq",
            "clamped": None,
            "order_quantity": float(oq_units),
            "order_quantity_units": oq_units,
            "cover_days": 12.0,
            "annual_demand": 10950.0,
            "order_cost": 50.0,
            "holding_rate": 0.25,
            "unit_cost": unit_cost,
            "arithmetic": f"EOQ {oq_units}",
            "recommended_max_units": 400,
            "current_order_qty": None,
            "current_max_qty": None,
        },
    }


def _order_recommendations() -> list[dict]:
    """The canned order-plan rows: three due Acumatica item-sites of
    site 154 (one supplier each) and the Odoo line of site 159."""
    rows = [
        # 125 x 4.5: raw 120 raised to the lot of 25.
        _order_rec(ITEM_A, rop=100, on_hand=50, unit_cost=4.5, oq_units=120),
        # 100 x 10.01: raw 90 raised to the minimum of 100.
        _order_rec(ITEM_B, rop=60, on_hand=40, unit_cost=10.005, oq_units=90),
        # 30 x 2.0: raw 30, a multiple of the lot of 10.
        _order_rec(ITEM_C, rop=40, on_hand=20, unit_cost=2.0, oq_units=30),
    ]
    # The Odoo line: site 159, unreleasable, never sent.
    odoo = _order_rec(ITEM_D, rop=40, on_hand=20, unit_cost=2.0, oq_units=30)
    odoo["site_code"] = SITE_ODOO
    rows.append(odoo)
    return rows


def _seed_order_builder() -> None:
    """The tables the order plan reads: the demand event (the
    as-of), the item-sites, the supplier terms and the suppliers."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO demand_events (id, business_key, item_code, "
            "site_code, quantity, occurred_on, event_type, source_system, "
            "synced_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 'acumatica', %s)",
            (
                "1640-demand-1",
                "1640-demand-1",
                ITEM_A,
                SITE,
                30.0,
                AS_OF,
                "shipment",
                _fresh(NOW),
            ),
        )
    ItemSitePostgresRepository().upsert_many(
        [
            {"item_code": ITEM_A, "site_code": SITE, "source_system": "acumatica"},
            {"item_code": ITEM_B, "site_code": SITE, "source_system": "acumatica"},
            {"item_code": ITEM_C, "site_code": SITE, "source_system": "acumatica"},
            {"item_code": ITEM_D, "site_code": SITE_ODOO, "source_system": "odoo"},
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
                "business_key": f"acumatica:{ITEM_C}:{SUPPLIER_C}",
                "source_system": "acumatica",
                "item_code": ITEM_C,
                "supplier_code": SUPPLIER_C,
                "min_order_qty": 20,
                "max_order_qty": None,
                "lot_size": 10,
                "lead_time_days": 10,
                "is_default": True,
            },
            {
                "business_key": f"odoo:{ITEM_D}:{SUPPLIER_D}",
                "source_system": "odoo",
                "item_code": ITEM_D,
                "supplier_code": SUPPLIER_D,
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
                "vendor_code": "V-1640-1",
                "name": "Acme Fasteners",
                "source_system": "acumatica",
            },
            {
                "supplier_code": SUPPLIER_B,
                "vendor_code": "V-1640-2",
                "name": "Bolt & Nut Works",
                "source_system": "acumatica",
            },
            {
                "supplier_code": SUPPLIER_C,
                "vendor_code": "V-1640-3",
                "name": "Cog & Pinion Co",
                "source_system": "acumatica",
            },
            {
                "supplier_code": SUPPLIER_D,
                "vendor_code": "V-1640-4",
                "name": "Odoo Widgets",
                "source_system": "odoo",
            },
        ]
    )


def _seed_transfer_builder() -> None:
    """The tables the transfer suggestions read: one more demand
    event (the as-of) and the item-sites the builder maps to source
    systems."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO demand_events (id, business_key, item_code, "
            "site_code, quantity, occurred_on, event_type, source_system, "
            "synced_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 'acumatica', %s)",
            (
                "1640-demand-2",
                "1640-demand-2",
                ITEM_T1,
                PAIR_T1[0],
                30.0,
                AS_OF,
                "shipment",
                _fresh(NOW + timedelta(days=3)),
            ),
        )
    ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": ITEM_T1,
                "site_code": PAIR_T1[0],
                "source_system": "acumatica",
            },
            {
                "item_code": ITEM_T1,
                "site_code": PAIR_T1[1],
                "source_system": "acumatica",
            },
            {
                "item_code": ITEM_T2,
                "site_code": PAIR_T2[0],
                "source_system": "acumatica",
            },
            {
                "item_code": ITEM_T2,
                "site_code": PAIR_T2[1],
                "source_system": "acumatica",
            },
            {
                "item_code": ITEM_T3,
                "site_code": PAIR_T3[0],
                "source_system": "acumatica",
            },
            {
                "item_code": ITEM_T3,
                "site_code": PAIR_T3[1],
                "source_system": "acumatica",
            },
            {"item_code": ITEM_T4, "site_code": PAIR_T1[0], "source_system": "odoo"},
            {"item_code": ITEM_T4, "site_code": PAIR_T1[1], "source_system": "odoo"},
        ]
    )


def _feed_row(table: str, synced_at: datetime) -> tuple[str, tuple]:
    """The one Acumatica row of each of the other feed tables."""
    if table == "on_hand":
        return (
            "INSERT INTO on_hand (id, item_code, site_code, "
            "quantity_on_hand, as_of, source_system, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, 'acumatica', %s)",
            ("1640-onhand-1", ITEM_A, SITE, 50.0, NOW, synced_at),
        )
    if table == "open_purchase_lines":
        return (
            "INSERT INTO open_purchase_lines (id, business_key, item_code, "
            "site_code, supplier_code, order_type, open_units, promised_on, "
            "ordered_on, as_of, source_system, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, 'purchase', %s, %s, %s, %s, "
            "'acumatica', %s)",
            (
                "1640-opl-1",
                "1640-opl-1",
                ITEM_B,
                SITE,
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
            "1640-lto-1",
            "1640-lto-1",
            SUPPLIER_A,
            ITEM_A,
            SITE,
            AS_OF - timedelta(days=20),
            AS_OF - timedelta(days=10),
            synced_at,
        ),
    )


def _seed_other_feeds() -> None:
    """One Acumatica row in each of the other feed tables the runs
    check for freshness."""
    with get_cursor() as cur:
        for table in FEED_TABLES:
            if table in ("item_sites", "demand_events"):
                continue  # the builder seeds hold their rows
            sql, params = _feed_row(table, _fresh(NOW))
            cur.execute(sql, params)


def _feed_freshness(synced_at: datetime) -> None:
    """Every Acumatica row of every feed table stamped ``synced_at``
    — the runs' freshness check reads each table's newest such
    stamp."""
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


class FakeRecommendations:
    """The fake for the route module's ``recommendations_for_all``:
    the canned rows.  ``rows`` is mutable: the order-plan pass and
    the transfer pass hold their own."""

    def __init__(self, rows: list) -> None:
        self.rows = rows

    def __call__(self, as_of=None):
        return self.rows


def _transfer_rec(item_code, site_code, *, rop, on_hand, on_order, oq_units, unit_cost):
    """One row of the fake ``recommendations_for_all`` in the shape
    the real one answers: the position values, the reorder point in
    whole units and the order-quantity block."""
    return {
        "item_code": item_code,
        "site_code": site_code,
        "as_of": AS_OF,
        "status": "ok",
        "reorder_point": float(rop),
        "reorder_point_units": rop,
        "safety_stock_units": 10,
        "inputs": {
            "d": 10.0,
            "sd_d": 2.0,
            "lt": 7.0,
            "sd_lt": 1.0,
            "z": 1.6449,
            "service_level": 0.95,
        },
        "on_hand_units": on_hand,
        "on_order_units": on_order,
        "unit_cost": unit_cost,
        "suggested_order_quantity": 0,
        "order_quantity": {
            "basis": "eoq",
            "clamped": None,
            "order_quantity": float(oq_units),
            "order_quantity_units": oq_units,
            "cover_days": 7.0,
            "annual_demand": 3650.0,
            "order_cost": 50.0,
            "holding_rate": 0.25,
            "unit_cost": unit_cost,
            "arithmetic": f"EOQ {oq_units}",
            "recommended_max_units": None,
            "current_order_qty": None,
            "current_max_qty": None,
        },
    }


def _transfer_recommendations() -> list[dict]:
    """The canned transfer rows: the three valued Acumatica pairs
    and the Odoo pair, never released here."""
    return [
        # T3 (acumatica): site 162 needs 100 against a surplus of
        # 250 at site 161 — the move of 100 at 6.0, value 600.00.
        _transfer_rec(
            ITEM_T3,
            PAIR_T3[1],
            rop=300,
            on_hand=200,
            on_order=0,
            oq_units=50,
            unit_cost=6.0,
        ),
        _transfer_rec(
            ITEM_T3,
            PAIR_T3[0],
            rop=100,
            on_hand=400,
            on_order=0,
            oq_units=50,
            unit_cost=6.0,
        ),
        # T2 (acumatica): site 161 needs 150 against a surplus of
        # 250 at site 154 — the move of 150 at 3.0, value 450.00.
        _transfer_rec(
            ITEM_T2,
            PAIR_T2[1],
            rop=250,
            on_hand=100,
            on_order=0,
            oq_units=50,
            unit_cost=3.0,
        ),
        _transfer_rec(
            ITEM_T2,
            PAIR_T2[0],
            rop=100,
            on_hand=400,
            on_order=0,
            oq_units=50,
            unit_cost=3.0,
        ),
        # T1 (acumatica): site 159 needs 120 against a surplus of
        # 300 at site 154 — the move of 120 at 2.5, value 300.00.
        _transfer_rec(
            ITEM_T1,
            PAIR_T1[1],
            rop=400,
            on_hand=200,
            on_order=80,
            oq_units=100,
            unit_cost=2.5,
        ),
        _transfer_rec(
            ITEM_T1,
            PAIR_T1[0],
            rop=300,
            on_hand=700,
            on_order=0,
            oq_units=100,
            unit_cost=2.5,
        ),
        # T4 (odoo): a need of 30 against a surplus of 50 — the
        # Odoo suggestion, never released here.
        _transfer_rec(
            ITEM_T4,
            PAIR_T1[1],
            rop=50,
            on_hand=20,
            on_order=0,
            oq_units=10,
            unit_cost=2.0,
        ),
        _transfer_rec(
            ITEM_T4,
            PAIR_T1[0],
            rop=20,
            on_hand=80,
            on_order=0,
            oq_units=10,
            unit_cost=2.0,
        ),
    ]


def _grant_body(process: str) -> dict:
    """A grant wide enough for every document and every day here:
    the limits never bind, only the releases' outcomes do."""
    return {
        "process": process,
        "max_document_value": 2000,
        "max_daily_value": 100000,
        "max_data_age_hours": 24,
    }


def test_issue1640_freeform(monkeypatch, client) -> None:
    # Isolate the Acumatica environment, complete configuration,
    # writes on.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- Seed the order plan, plus the other feed tables the runs
    # check for freshness.
    _seed_order_builder()
    _seed_other_feeds()

    # --- The Acumatica clients, replaced with fakes answering per
    # group: the write switch is consulted the way the real client
    # consults it, before the empty-batch shortcut.
    order_create_calls: list[list] = []
    transfer_create_calls: list[list] = []
    order_answers: list[dict] = []
    transfer_answers: list[dict] = []

    def _writes_enabled() -> bool:
        return os.environ.get("ACUMATICA_ALLOW_WRITES", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )

    def fake_order_create(self, orders):
        if not _writes_enabled():
            raise RuntimeError("the fake answers no writes that are switched off")
        if not orders:
            return []
        order_create_calls.append([dict(entry) for entry in orders])
        return [
            {
                "origin": entry["origin"],
                "outcome": answer["outcome"],
                "order_nbr": (
                    f"PO-1640-{len(order_create_calls):03d}"
                    if answer["outcome"] in ("created", "exists")
                    else None
                ),
                "status": "On Hold",
                "warehouse_code": "WHOLESALE",
                "vendor_code": "CONPERIPH",
                "line_count": len(entry["lines"]),
                "error": answer.get("error"),
            }
            for entry, answer in zip(orders, order_answers)
        ]

    def fake_transfer_create(self, transfers):
        if not _writes_enabled():
            raise RuntimeError("the fake answers no writes that are switched off")
        if not transfers:
            return []
        transfer_create_calls.append([dict(entry) for entry in transfers])
        return [
            {
                "origin": entry["origin"],
                "outcome": answer["outcome"],
                "reference_nbr": (
                    f"TR-1640-{len(transfer_create_calls):03d}"
                    if answer["outcome"] in ("created", "exists")
                    else None
                ),
                "status": "On Hold",
                "from_warehouse_code": "WHOLESALE",
                "to_warehouse_code": "DISTRIBUTION",
                "line_count": len(entry["lines"]),
                "error": answer.get("error"),
            }
            for entry, answer in zip(transfers, transfer_answers)
        ]

    monkeypatch.setattr(
        AcumaticaClient, "create_draft_purchase_orders", fake_order_create
    )
    monkeypatch.setattr(AcumaticaClient, "create_transfer_orders", fake_transfer_create)

    # --- The builder's source, replaced: the canned rows.  The
    # runs' as-of is the demand events' data date.
    fake_recommendations = FakeRecommendations(_order_recommendations())
    monkeypatch.setattr(supply_plan, "recommendations_for_all", fake_recommendations)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # -- The order-plan grant in force, through the #1173 route. ----
    created = admin.post("/api/standing-grants", json=_grant_body("order_plan_release"))
    assert created.status_code == 201, created.text
    grant_order_id = created.json()["id"]
    assert created.json()["in_force"] is True

    # -- All three groups created: the row is what it is today. ----
    order_answers[:] = [
        {"outcome": "created"},
        {"outcome": "created"},
        {"outcome": "created"},
    ]
    _feed_freshness(_fresh(NOW))
    row = standing_release.run_order_plan_grant(now=NOW)
    assert row["id"] is not None
    assert row["process"] == "order_plan_release"
    assert row["grant_id"] == grant_order_id
    assert row["started_at"] == NOW.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 3
    assert row["value"] == TOTAL_ORDER
    assert row["left_out"] == []
    # The fake's create receives exactly the three groups' orders —
    # and never the Odoo line's.
    assert order_create_calls == [[ORDER_A_WIRE, ORDER_B_WIRE, ORDER_C_WIRE]]
    assert ITEM_D not in str(order_create_calls)
    # The run's plan_releases rows: the grant as actor, all created.
    releases = _plan_release_rows()
    assert len(releases) == 3
    for release_row in releases:
        assert release_row["actor"] == f"standing-grant:{grant_order_id}"
        assert release_row["outcome"] == "created"
        assert release_row["error"] is None
    assert releases[0]["supplier_code"] == SUPPLIER_A
    assert releases[0]["origin"] == ORIGIN_A
    assert releases[1]["supplier_code"] == SUPPLIER_B
    assert releases[2]["supplier_code"] == SUPPLIER_C

    # -- All three groups error: the run is failed, the first
    #    failure's error the reason, the chosen groups' count and
    #    value, the failed groups left out.
    day_fail = NOW + timedelta(days=1)
    order_answers[:] = [
        {"outcome": "error", "error": ORDER_ERROR_A},
        {"outcome": "error", "error": ORDER_ERROR_B},
        {"outcome": "error", "error": ORDER_ERROR_C},
    ]
    _feed_freshness(_fresh(day_fail))
    row = standing_release.run_order_plan_grant(now=day_fail)
    assert row["started_at"] == day_fail.isoformat()
    assert row["outcome"] == "failed"
    assert row["reason"] == f"every release group failed: {ORDER_ERROR_A}"
    assert row["documents"] == 3
    assert row["value"] == TOTAL_ORDER
    assert row["left_out"] == [
        {
            "site_code": SITE,
            "supplier_code": SUPPLIER_A,
            "value": VALUE_A,
            "reason": "release_error",
        },
        {
            "site_code": SITE,
            "supplier_code": SUPPLIER_B,
            "value": VALUE_B,
            "reason": "release_error",
        },
        {
            "site_code": SITE,
            "supplier_code": SUPPLIER_C,
            "value": VALUE_C,
            "reason": "release_error",
        },
    ]
    # The failed groups' plan_releases rows keep the errors.
    releases = _plan_release_rows()
    assert len(releases) == 6
    for release_row, error in zip(
        releases[3:], (ORDER_ERROR_A, ORDER_ERROR_B, ORDER_ERROR_C)
    ):
        assert release_row["outcome"] == "error"
        assert release_row["error"] == error

    # -- 2 of 3 groups created, the third errors: the run is
    #    released, the sent groups' count and value, the third left
    #    out with release_error.
    day_partial = NOW + timedelta(days=2)
    order_answers[:] = [
        {"outcome": "created"},
        {"outcome": "created"},
        {"outcome": "error", "error": ORDER_ERROR_C},
    ]
    _feed_freshness(_fresh(day_partial))
    row = standing_release.run_order_plan_grant(now=day_partial)
    assert row["started_at"] == day_partial.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 2
    assert row["value"] == SENT_ORDER
    assert row["left_out"] == [
        {
            "site_code": SITE,
            "supplier_code": SUPPLIER_C,
            "value": VALUE_C,
            "reason": "release_error",
        },
    ]
    partial_run_id = row["id"]
    releases = _plan_release_rows()
    assert len(releases) == 9
    assert releases[6]["supplier_code"] == SUPPLIER_A
    assert releases[6]["outcome"] == "created"
    assert releases[7]["supplier_code"] == SUPPLIER_B
    assert releases[7]["outcome"] == "created"
    assert releases[8]["supplier_code"] == SUPPLIER_C
    assert releases[8]["outcome"] == "error"
    assert releases[8]["error"] == ORDER_ERROR_C

    # -- The exceptions inbox: the standing-grant item for the
    #    partial run, its left_out not empty being enough. ----------
    body = admin.get("/api/exceptions").json()
    assert body["counts"] is not None
    items = [
        r for r in body["rows"] if r["key"] == f"standing_grant_run:{partial_run_id}"
    ]
    assert len(items) == 1, body["rows"]
    item = items[0]
    assert item["kind"] == "standing_grant_run"
    assert item["severity"] == "medium"
    assert item["title"] == "Standing grant run needs attention"
    assert item["detail"] == (
        f"The standing grant for the order plan sent 2 document(s) "
        f"at {day_partial:%Y-%m-%d %H:%M} UTC and held 1 back over its limits."
    )
    assert item["view"] == "#/releases"
    assert item["item_code"] is None
    assert item["site_code"] is None
    assert item["detected_on"] == day_partial.date().isoformat()

    # ================= The transfer run. ===========================

    # --- The transfer seed: the source's rows swapped, the
    # item-sites and the demand event seeded.
    _seed_transfer_builder()
    fake_recommendations.rows = _transfer_recommendations()

    created = admin.post("/api/standing-grants", json=_grant_body("transfer_release"))
    assert created.status_code == 201, created.text
    grant_transfer_id = created.json()["id"]
    assert created.json()["in_force"] is True

    # -- All three groups created: the row is what it is today. ----
    transfer_answers[:] = [
        {"outcome": "created"},
        {"outcome": "created"},
        {"outcome": "created"},
    ]
    day_t1 = NOW + timedelta(days=3)
    _feed_freshness(_fresh(day_t1))
    row = standing_release.run_transfer_grant(now=day_t1)
    assert row["id"] is not None
    assert row["process"] == "transfer_release"
    assert row["grant_id"] == grant_transfer_id
    assert row["started_at"] == day_t1.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 3
    assert row["value"] == TOTAL_TRANSFER
    assert row["left_out"] == []
    # The fake's create receives exactly the three pairs' orders, in
    # the suggestions' order — and never the Odoo pair's.
    assert transfer_create_calls == [[T3_WIRE, T2_WIRE, T1_WIRE]]
    assert ITEM_T4 not in str(transfer_create_calls)
    # The run's transfer_releases rows: the grant as actor, no actor
    # name, all created.
    transfers = _transfer_release_rows()
    assert len(transfers) == 3
    for transfer_row in transfers:
        assert transfer_row["actor"] == f"standing-grant:{grant_transfer_id}"
        assert transfer_row["actor_name"] is None
        assert transfer_row["outcome"] == "created"
    assert transfers[0]["origin"] == ORIGIN_T3
    assert transfers[1]["origin"] == ORIGIN_T2
    assert transfers[2]["origin"] == ORIGIN_T1

    # -- 2 of 3 groups created, the third errors: the run is
    #    released, the sent groups' count and value, the third left
    #    out with release_error.
    day_t2 = NOW + timedelta(days=4)
    transfer_answers[:] = [
        {"outcome": "error", "error": TRANSFER_ERROR_T3},
        {"outcome": "created"},
        {"outcome": "created"},
    ]
    _feed_freshness(_fresh(day_t2))
    row = standing_release.run_transfer_grant(now=day_t2)
    assert row["started_at"] == day_t2.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 2
    assert row["value"] == SENT_TRANSFER
    assert row["left_out"] == [
        {
            "from_site": PAIR_T3[0],
            "to_site": PAIR_T3[1],
            "value": VALUE_T3,
            "reason": "release_error",
        },
    ]

    # -- All three groups error: the run is failed, the first
    #    failure's error the reason, the chosen groups' count and
    #    value, the failed groups left out.
    day_t3 = NOW + timedelta(days=5)
    transfer_answers[:] = [
        {"outcome": "error", "error": TRANSFER_ERROR_T3},
        {"outcome": "error", "error": TRANSFER_ERROR_T2},
        {"outcome": "error", "error": TRANSFER_ERROR_T1},
    ]
    _feed_freshness(_fresh(day_t3))
    row = standing_release.run_transfer_grant(now=day_t3)
    assert row["started_at"] == day_t3.isoformat()
    assert row["outcome"] == "failed"
    assert row["reason"] == f"every release group failed: {TRANSFER_ERROR_T3}"
    assert row["documents"] == 3
    assert row["value"] == TOTAL_TRANSFER
    assert row["left_out"] == [
        {
            "from_site": PAIR_T3[0],
            "to_site": PAIR_T3[1],
            "value": VALUE_T3,
            "reason": "release_error",
        },
        {
            "from_site": PAIR_T2[0],
            "to_site": PAIR_T2[1],
            "value": VALUE_T2,
            "reason": "release_error",
        },
        {
            "from_site": PAIR_T1[0],
            "to_site": PAIR_T1[1],
            "value": VALUE_T1,
            "reason": "release_error",
        },
    ]

    # -- An exists that carries the stale-lines error counts as
    #    failed: a stored line snapshot of the first pair's origin
    #    the planned lines no longer match, the fake answering
    #    exists for every group.
    releases_repo = TransferReleasePostgresRepository()
    seed_id = releases_repo.insert_pending(
        origin=ORIGIN_T3,
        from_site_code=PAIR_T3[0],
        to_site_code=PAIR_T3[1],
        lines=[{"item_code": ITEM_T3, "quantity": 999}],
        actor="seed",
    )
    assert releases_repo.update_outcome(seed_id, outcome="created")
    day_t4 = NOW + timedelta(days=6)
    transfer_answers[:] = [
        {"outcome": "exists"},
        {"outcome": "exists"},
        {"outcome": "exists"},
    ]
    _feed_freshness(_fresh(day_t4))
    row = standing_release.run_transfer_grant(now=day_t4)
    assert row["started_at"] == day_t4.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 2
    assert row["value"] == SENT_TRANSFER
    assert row["left_out"] == [
        {
            "from_site": PAIR_T3[0],
            "to_site": PAIR_T3[1],
            "value": VALUE_T3,
            "reason": "release_error",
        },
    ]
    # The run's rows: the stale group exists without a reference
    # number and with the stale error, the error-free exists groups
    # kept the reference number.
    transfers = _transfer_release_rows()
    stale_row = next(
        t
        for t in transfers
        if t["origin"] == ORIGIN_T3
        and t["outcome"] == "exists"
        and t["error"] is not None
    )
    assert stale_row["actor"] == f"standing-grant:{grant_transfer_id}"
    assert stale_row["reference_nbr"] is None
    assert stale_row["error"] == STALE_ERROR_T3
    for origin in (ORIGIN_T2, ORIGIN_T1):
        exists_rows = [
            t
            for t in transfers
            if t["origin"] == origin
            and t["outcome"] == "exists"
            and t["error"] is None
            and t["actor"] == f"standing-grant:{grant_transfer_id}"
        ]
        assert exists_rows, origin
        assert exists_rows[-1]["reference_nbr"] == "TR-1640-004"
