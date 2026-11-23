"""Issue #1582 proof: the short-order release record.

AC-1 — the table: ``short_order_releases`` — ``id`` a bigint identity
primary key, ``as_of`` DATE not null, ``supplier_code`` and
``source_system`` TEXT not null, ``minimum`` and ``shortfall`` NUMERIC
not null, ``suggested`` TEXT not null with a CHECK limiting it to
``wait``, ``add_top_ups`` or ``order_anyway``, ``suggestion_reason``
TEXT not null, ``chosen`` TEXT not null with a CHECK limiting it to
``add_top_ups`` or ``order_anyway``, ``actor`` TEXT not null, and
``created_at`` TIMESTAMPTZ not null with a ``now()`` default; the
downgrade drops the table.  The database has one head, equal to
``CURRENT_HEAD`` imported from ``tests.migration_head.py`` (the
proof never compares that pin to a constant), the creating revision
is in the history — ``ScriptDirectory.walk_revisions()`` includes
it — and the database is upgraded to the head.

AC-3/AC-4 — the record, read through the app's test client the way
``tests/test_issue1191_freeform.py`` reads the release: the plan
builder stood in for as 1191 stands it in (recording each call's
horizon and as-of, one fixed plan per horizon), the excess list
answered nothing the way 1191's does, the release's data as-of
answered a fixed date never the server clock, and Acumatica's batch
mocked the way 1191 mocks it — preview and create fakes recording
their calls, the create's outcome switched to ``error`` for the one
confirm that must fail every group.  The minimums are stored the way
``tests/test_issue1145_freeform.py`` stores them (
``SupplierOrderMinimumPostgresRepository().upsert(...)``), and the
grant run is run the way ``tests/test_issue1198_freeform.py`` runs
it: the grant through the route, the feeds stamped fresh,
``run_order_plan_grant(now=NOW)`` and the run's own row read back.
Each scenario uses its own supplier:

* a short supplier released with one of its top-ups — one row,
  ``chosen`` ``add_top_ups``, its ``suggested``,
  ``suggestion_reason``, ``minimum``, ``shortfall`` and ``as_of``
  equal to the supplier's entry in
  ``GET /api/supply-plan/top-ups`` at the same data, its ``actor``
  the confirming admin's username;
* a short supplier released without its top-ups — one row,
  ``chosen`` ``order_anyway``;
* no row for a supplier that is not short, for a preview, or for a
  confirm whose Acumatica batch answers ``error`` for every group —
  the record then never reads the top-ups suggestions at all;
* a standing-grant run releasing a short supplier — one row, actor
  ``standing-grant:<id>``, ``chosen`` ``order_anyway``;
* a failure to record — with the insert made to raise, the confirm
  still answers 200 with the same body, the ``plan_releases`` rows
  are written and the failure is logged as a warning.

The record reads the suggestions once per confirm that creates a
group — the build_calls stand-in shows the two builds (the horizon
plan and the 30-day look-ahead, at the release's as-of) after each
recording confirm, and none after the all-error confirm.  Every row
field is read strictly (``row["chosen"]``, never ``.get`` with a
default), and the proof removes its rows, minimums and grant
afterwards.
"""

import logging
import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg2
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.supply_plan as supply_plan
import app.supply.release as release_module
import app.supply.short_orders as short_orders
import app.supply.standing_release as standing_release
from app.db.connection import get_connection, get_connection_params
from app.feed.acumatica_client import (
    AcumaticaClient,
    AcumaticaWritesDisabledError,
)
from app.feed.status import FEED_TABLES
from app.main import app
from app.repositories.supplier_order_minimums_postgres_repository import (
    SupplierOrderMinimumPostgresRepository,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

# The creating revision, named so the proof can show it is in the
# history: the double-quoted head literal's one owner is
# tests/test_issue803_freeform.py, which exempts this proof from its
# literal scan.
CREATING_REVISION = "0056_short_order_releases"

URL = "/api/supply-plan/release"
TOP_UPS_URL = "/api/supply-plan/top-ups"
GRANTS_URL = "/api/standing-grants"

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

# The release's as-of: the fixed answer of the release module's
# _data_as_of seam — never the server date.
AS_OF = date(2026, 11, 20)
AS_OF_STR = AS_OF.isoformat()
AS_OF_MONTH = "2026-11"
# The standing-grant run's now: an aware UTC instant, the feeds
# stamped one hour before it — within the grant's data-age hours.
NOW = datetime(2026, 11, 23, 12, 0, 0, tzinfo=UTC)

SITE = "154"

# The scenario's suppliers — each scenario uses its own.
SUPPLIER_TOP = "S-1582-TOP"  # released with one of its top-ups
SUPPLIER_BARE = "S-1582-BARE"  # released without its top-ups
SUPPLIER_FULL = "S-1582-FULL"  # not short: no stored minimum
SUPPLIER_PREVIEW = "S-1582-PREVIEW"  # previewed, never confirmed
SUPPLIER_ERROR = "S-1582-ERR"  # confirmed, the batch answers error
SUPPLIER_GRANT = "S-1582-GRANT"  # released by the standing-grant run
SUPPLIER_RAISE = "S-1582-RAISE"  # confirmed, the record's insert raises
SCENARIO_SUPPLIERS = (
    SUPPLIER_TOP,
    SUPPLIER_BARE,
    SUPPLIER_FULL,
    SUPPLIER_PREVIEW,
    SUPPLIER_ERROR,
    SUPPLIER_GRANT,
    SUPPLIER_RAISE,
)

# Every line of the stand-in plan: (item, supplier, due_in_days,
# quantity, unit cost).  The due lines (due in at most 2 days) sit in
# every horizon plan; the top-up candidates (due beyond the 7-day
# horizon) sit only in the 30-day look-ahead plan, the way 1191's
# stand-in holds its chosen top-ups.
MASTER_LINES = (
    ("A1", SUPPLIER_TOP, 0, 4, 100.0),
    ("A2", SUPPLIER_TOP, 12, 3, 150.0),
    ("B1", SUPPLIER_BARE, 0, 3, 100.0),
    ("B2", SUPPLIER_BARE, 15, 2, 100.0),
    ("C1", SUPPLIER_FULL, 0, 2, 100.0),
    ("D1", SUPPLIER_PREVIEW, 1, 2, 100.0),
    ("D2", SUPPLIER_PREVIEW, 18, 8, 100.0),
    ("E1", SUPPLIER_ERROR, 2, 3, 100.0),
    ("E2", SUPPLIER_ERROR, 20, 1, 100.0),
    ("F1", SUPPLIER_GRANT, 0, 1, 150.0),
    ("F2", SUPPLIER_GRANT, 25, 1, 100.0),
    ("G1", SUPPLIER_RAISE, 0, 2, 125.0),
    ("G2", SUPPLIER_RAISE, 16, 2, 125.0),
)

# The stored order minimums, keyed by supplier — the full supplier's
# has none (the not-short scenario).  Each minimum sits above the
# supplier's 7-day plan value, so each other supplier is short.
MINIMUMS = {
    SUPPLIER_TOP: Decimal("800.00"),  # 800.00 - 400.00 = 400.00 short
    SUPPLIER_BARE: Decimal("1000.00"),  # 1000.00 - 300.00 = 700.00 short
    SUPPLIER_PREVIEW: Decimal("900.00"),  # 900.00 - 200.00 = 700.00 short
    SUPPLIER_ERROR: Decimal("900.00"),  # 900.00 - 300.00 = 600.00 short
    SUPPLIER_GRANT: Decimal("700.00"),  # 700.00 - 150.00 = 550.00 short
    SUPPLIER_RAISE: Decimal("500.00"),  # 500.00 - 250.00 = 250.00 short
}

TOP_UP_A2 = {
    "supplier_code": SUPPLIER_TOP,
    "item_code": "A2",
    "site_code": SITE,
}
TOP_UP_D2 = {
    "supplier_code": SUPPLIER_PREVIEW,
    "item_code": "D2",
    "site_code": SITE,
}


def _stand_in_line(item_code, supplier_code, due_in_days, quantity, unit_cost):
    """One line of the stand-in plan, every field the two consumers
    read (the release's and top_ups_by_supplier's) filled."""
    value = (Decimal(quantity) * Decimal(str(unit_cost))).quantize(Decimal("0.01"))
    return {
        "item_code": item_code,
        "site_code": SITE,
        "position_units": 0,
        "reorder_point_units": 10,
        "daily_demand": 10.0,
        "due_in_days": due_in_days,
        "order_by": AS_OF + timedelta(days=due_in_days),
        "lead_time_days": 10,
        "arrives_on": AS_OF + timedelta(days=due_in_days + 10),
        "raw_quantity": quantity,
        "quantity": quantity,
        "adjustments": None,
        "short_of_need": None,
        "terms": None,
        "unit_cost": unit_cost,
        "value": value,
        "arithmetic": None,
        "supplier_code": supplier_code,
        "source_system": "acumatica",
        "supplier_reason": None,
        "source": "reorder_point",
    }


def _stand_in_plan(horizon_days):
    """One fixed plan per horizon: every master line due within
    ``horizon_days``, one acumatica supplier group per supplier — the
    group's stored minimum (none for the full supplier) and its
    shortfall against the horizon's total, the way the builder's
    supplier group holds them."""
    lines = [
        _stand_in_line(item, supplier, due, quantity, unit_cost)
        for item, supplier, due, quantity, unit_cost in MASTER_LINES
        if due <= horizon_days
    ]
    by_supplier: dict[str, list[dict]] = {}
    for line in lines:
        by_supplier.setdefault(line["supplier_code"], []).append(line)
    suppliers = []
    for supplier in SCENARIO_SUPPLIERS:
        supplier_lines = by_supplier.get(supplier)
        if not supplier_lines:
            continue
        total_value = sum(line["value"] for line in supplier_lines)
        minimum = MINIMUMS.get(supplier)
        shortfall = (
            (minimum - total_value if total_value < minimum else Decimal(0))
            if minimum is not None
            else None
        )
        suppliers.append(
            {
                "supplier_code": supplier,
                "source_system": "acumatica",
                "vendor_code": f"V-{supplier}",
                "name": f"{supplier} Trading Co.",
                "line_count": len(supplier_lines),
                "total_value": total_value,
                "minimum": minimum,
                "shortfall": shortfall,
                "lines": supplier_lines,
            }
        )
    total_value = sum(line["value"] for line in lines)
    return {
        "as_of": AS_OF,
        "horizon_days": horizon_days,
        "line_count": len(lines),
        "total_value": total_value,
        "suppliers": suppliers,
    }


def _release_line(item_code, quantity, unit_cost, value, source):
    return {
        "item_code": item_code,
        "quantity": quantity,
        "unit_cost": unit_cost,
        "value": value,
        "source": source,
    }


def _group_body(supplier, lines, value, outcome, order_nbr, error=None):
    return {
        "site_code": SITE,
        "supplier_code": supplier,
        "origin": f"AIERP order plan {AS_OF_STR} site {SITE} vendor {supplier}",
        "lines": lines,
        "value": value,
        "outcome": outcome,
        "warehouse_code": "WHOLESALE",
        "vendor_code": "CONPERIPH",
        "order_nbr": order_nbr,
        "error": error,
    }


def _body(groups, total_value):
    return {
        "month": AS_OF_MONTH,
        "groups": groups,
        "unreleasable": [],
        "total_value": total_value,
        "acumatica_available": True,
    }


EXPECTED_PREVIEW_TOP = _body(
    [
        _group_body(
            SUPPLIER_TOP,
            [
                _release_line("A1", 4, "100.0", "400.0", "reorder_point"),
                _release_line("A2", 3, "150.0", "450.0", "top_up"),
            ],
            "850.0",
            "would_create",
            None,
        )
    ],
    "850.0",
)
EXPECTED_CONFIRM_TOP = _body(
    [
        _group_body(
            SUPPLIER_TOP,
            [
                _release_line("A1", 4, "100.0", "400.0", "reorder_point"),
                _release_line("A2", 3, "150.0", "450.0", "top_up"),
            ],
            "850.0",
            "created",
            "PO-1582-001",
        )
    ],
    "850.0",
)
EXPECTED_CONFIRM_BARE = _body(
    [
        _group_body(
            SUPPLIER_BARE,
            [_release_line("B1", 3, "100.0", "300.0", "reorder_point")],
            "300.0",
            "created",
            "PO-1582-002",
        )
    ],
    "300.0",
)
EXPECTED_CONFIRM_FULL = _body(
    [
        _group_body(
            SUPPLIER_FULL,
            [_release_line("C1", 2, "100.0", "200.0", "reorder_point")],
            "200.0",
            "created",
            "PO-1582-003",
        )
    ],
    "200.0",
)
EXPECTED_PREVIEW_PREVIEW = _body(
    [
        _group_body(
            SUPPLIER_PREVIEW,
            [
                _release_line("D1", 2, "100.0", "200.0", "reorder_point"),
                _release_line("D2", 8, "100.0", "800.0", "top_up"),
            ],
            "1000.0",
            "would_create",
            None,
        )
    ],
    "1000.0",
)
EXPECTED_CONFIRM_ERROR = _body(
    [
        _group_body(
            SUPPLIER_ERROR,
            [_release_line("E1", 3, "100.0", "300.0", "reorder_point")],
            "300.0",
            "error",
            None,
            error="the fake batch answers error",
        )
    ],
    "300.0",
)
EXPECTED_CONFIRM_GRANT = _body(
    [
        _group_body(
            SUPPLIER_GRANT,
            [_release_line("F1", 1, "150.0", "150.0", "reorder_point")],
            "150.0",
            "created",
            "PO-1582-005",
        )
    ],
    "150.0",
)
EXPECTED_CONFIRM_RAISE = _body(
    [
        _group_body(
            SUPPLIER_RAISE,
            [_release_line("G1", 2, "125.0", "250.0", "reorder_point")],
            "250.0",
            "created",
            "PO-1582-006",
        )
    ],
    "250.0",
)


def _short_rows() -> list[dict]:
    """Every stored short_order_releases row, oldest first, every
    column of it."""
    conn = psycopg2.connect(**get_connection_params())
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM short_order_releases ORDER BY id")
            columns = [column[0] for column in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


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


def _table_columns() -> dict:
    """The table's columns as (data_type, max_length, nullability,
    default, identity)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable, column_default, is_identity "
                "FROM information_schema.columns "
                "WHERE table_name = 'short_order_releases' "
                "ORDER BY ordinal_position"
            )
            return {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in cur.fetchall()}
    finally:
        conn.close()


def _check_defs() -> list[str]:
    """The table's check constraints, as their definitions."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "WHERE rel.relname = 'short_order_releases' "
                "AND con.contype = 'c' ORDER BY con.conname"
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _db_head() -> str:
    """The revision the test database is on."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _seed_feeds() -> None:
    """One Acumatica row in every feed table the run checks for
    freshness, each stamped one hour before NOW — the plan's builder
    is stood in for, so only the stamps stand to the run."""
    fresh = NOW - timedelta(hours=1)
    conn = psycopg2.connect(**get_connection_params())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO demand_events (id, business_key, item_code, "
                "site_code, quantity, occurred_on, event_type, "
                "source_system, synced_at) VALUES (%s, %s, %s, %s, %s, %s, "
                "'shipment', 'acumatica', %s)",
                (
                    "1582-demand-1",
                    "1582-demand-1",
                    "F1",
                    SITE,
                    30.0,
                    AS_OF,
                    fresh,
                ),
            )
            cur.execute(
                "INSERT INTO item_sites (id, item_code, site_code, "
                "unit_cost, reorder_point, safety_stock, max_qty, "
                "order_qty, source_system, synced_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'acumatica', %s)",
                (
                    "1582-item-site-1",
                    "F1",
                    SITE,
                    150.0,
                    10.0,
                    0.0,
                    None,
                    None,
                    fresh,
                ),
            )
            cur.execute(
                "INSERT INTO on_hand (id, item_code, site_code, "
                "quantity_on_hand, as_of, source_system, synced_at) "
                "VALUES (%s, %s, %s, %s, %s, 'acumatica', %s)",
                ("1582-onhand-1", "F1", SITE, 50.0, AS_OF, fresh),
            )
            cur.execute(
                "INSERT INTO open_purchase_lines (id, business_key, "
                "item_code, site_code, supplier_code, order_type, "
                "open_units, promised_on, ordered_on, as_of, "
                "source_system, synced_at) "
                "VALUES (%s, %s, %s, %s, %s, 'purchase', %s, %s, %s, %s, "
                "'acumatica', %s)",
                (
                    "1582-opl-1",
                    "1582-opl-1",
                    "F1",
                    SITE,
                    SUPPLIER_GRANT,
                    0.0,
                    AS_OF + timedelta(days=30),
                    AS_OF,
                    fresh,
                    fresh,
                ),
            )
            cur.execute(
                "INSERT INTO lead_time_observations (id, business_key, "
                "supplier_code, item_code, site_code, ordered_on, "
                "received_on, source_system, synced_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'acumatica', %s)",
                (
                    "1582-lto-1",
                    "1582-lto-1",
                    SUPPLIER_GRANT,
                    "F1",
                    SITE,
                    AS_OF - timedelta(days=20),
                    AS_OF - timedelta(days=10),
                    fresh,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _cleanup(grant_ids: list[int]) -> None:
    """The proof's own rows, minimums and grant, removed."""
    conn = psycopg2.connect(**get_connection_params())
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM short_order_releases " "WHERE supplier_code = ANY(%s)",
                (list(SCENARIO_SUPPLIERS),),
            )
            cur.execute(
                "DELETE FROM supplier_order_minimums " "WHERE supplier_code = ANY(%s)",
                (list(SCENARIO_SUPPLIERS),),
            )
            cur.execute(
                "DELETE FROM plan_releases " "WHERE supplier_code = ANY(%s)",
                (list(SCENARIO_SUPPLIERS),),
            )
            if grant_ids:
                cur.execute(
                    "DELETE FROM standing_grant_runs " "WHERE grant_id = ANY(%s)",
                    (grant_ids,),
                )
                cur.execute(
                    "DELETE FROM erp_standing_grants WHERE id = ANY(%s)",
                    (grant_ids,),
                )
            for table in FEED_TABLES:
                cur.execute(
                    f"DELETE FROM {table} WHERE id LIKE '1582-%%'",
                )
        conn.commit()
    finally:
        conn.close()


def test_issue1582_freeform(monkeypatch, client, caplog) -> None:
    # Isolate the Acumatica environment, complete configuration,
    # writes on — the way 1191 and 538 switch the writes on.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # The shared session fixture runs ``alembic upgrade``, and
    # migrations/env.py calls ``fileConfig(alembic.ini)`` whose
    # disable_existing_loggers default turns off every logger that
    # already exists — including this one's.  Re-enable it so the
    # warning assertion below observes what the code actually logs.
    monkeypatch.setattr(logging.getLogger("app.supply.short_orders"), "disabled", False)

    grant_ids: list[int] = []
    try:
        # -- The table: exactly the ten documented columns, the
        #    checks, the one head equal to the shared pin, the
        #    creating revision in the history, the database on the
        #    head.  The proof never compares CURRENT_HEAD to a
        #    constant. -------------------------------------------------
        assert _table_columns() == {
            "id": ("bigint", None, "NO", None, "YES"),
            "as_of": ("date", None, "NO", None, "NO"),
            "supplier_code": ("text", None, "NO", None, "NO"),
            "source_system": ("text", None, "NO", None, "NO"),
            "minimum": ("numeric", None, "NO", None, "NO"),
            "shortfall": ("numeric", None, "NO", None, "NO"),
            "suggested": ("text", None, "NO", None, "NO"),
            "suggestion_reason": ("text", None, "NO", None, "NO"),
            "chosen": ("text", None, "NO", None, "NO"),
            "actor": ("text", None, "NO", None, "NO"),
            "created_at": ("timestamp with time zone", None, "NO", "now()", "NO"),
        }
        checks = _check_defs()
        assert any(
            "suggested = ANY" in definition
            and "'wait'::text" in definition
            and "'add_top_ups'::text" in definition
            and "'order_anyway'::text" in definition
            for definition in checks
        ), "the suggested check is missing"
        assert any(
            "chosen = ANY" in definition
            and "'add_top_ups'::text" in definition
            and "'order_anyway'::text" in definition
            and "'wait'::text" not in definition
            for definition in checks
        ), "the chosen check is missing"
        # One head, equal to the shared pin.
        heads = ScriptDirectory(_MIGRATIONS_DIR).get_heads()
        assert list(heads) == [CURRENT_HEAD]
        # The creating revision is in the history.
        assert CREATING_REVISION in {
            script.revision
            for script in ScriptDirectory(_MIGRATIONS_DIR).walk_revisions()
        }
        # The database is upgraded to the head.
        assert _db_head() == CURRENT_HEAD

        # -- The minimums, stored the way the admin stores them
        #    (issue 1145's way); the full supplier's has none. -------
        minimums = SupplierOrderMinimumPostgresRepository()
        for supplier, amount in MINIMUMS.items():
            minimums.upsert("acumatica", supplier, float(amount), "test")

        # -- Acumatica's batch, mocked the way 1191 mocks it: preview
        #    and create fakes recording their calls; the create's
        #    outcome switches to error for the one confirm that must
        #    fail every group. ----------------------------------------
        preview_calls: list[list] = []
        create_calls: list[list] = []
        create_outcome = ["created"]

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
            created = create_outcome[0] == "created"
            return [
                {
                    "origin": entry["origin"],
                    "outcome": create_outcome[0],
                    "order_nbr": (
                        f"PO-1582-{len(create_calls):03d}" if created else None
                    ),
                    "status": "On Hold" if created else None,
                    "warehouse_code": "WHOLESALE",
                    "vendor_code": "CONPERIPH",
                    "line_count": len(entry["lines"]),
                    "error": None if created else "the fake batch answers error",
                }
                for entry in orders
            ]

        monkeypatch.setattr(
            AcumaticaClient, "preview_draft_purchase_orders", fake_preview
        )
        monkeypatch.setattr(
            AcumaticaClient, "create_draft_purchase_orders", fake_create
        )

        # -- The seams, the names 1191 stands in for: the excess list
        #    answers nothing; the release's data as-of answers a fixed
        #    date; the plan builder records each call's horizon (and
        #    as-of) and answers one fixed plan per horizon.
        build_calls: list[tuple] = []

        def fake_build_supply_plan(horizon_days, as_of=None):
            build_calls.append((horizon_days, as_of))
            return _stand_in_plan(horizon_days)

        monkeypatch.setattr(supply_plan, "excess_ranked", lambda as_of=None: [])
        monkeypatch.setattr(release_module, "_data_as_of", lambda: AS_OF)
        monkeypatch.setattr(supply_plan, "build_supply_plan", fake_build_supply_plan)

        admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

        # -- A short supplier released with one of its top-ups: one
        #    row, chosen add_top_ups, its fields equal to the
        #    supplier's entry in the top-ups at the same data. -------
        preview = admin.post(
            URL, json={"supplier_codes": [SUPPLIER_TOP], "top_ups": [TOP_UP_A2]}
        )
        assert preview.status_code == 200, preview.text
        assert preview.json() == EXPECTED_PREVIEW_TOP
        # The preview: the release's own horizon build, then
        # top_ups_by_supplier's horizon build at the release's as-of
        # and its look-ahead build.  A preview records nothing.
        assert build_calls == [(7, AS_OF), (7, AS_OF), (30, AS_OF)]
        assert preview_calls == [
            [
                {
                    "site_id": SITE,
                    "vendor_id": SUPPLIER_TOP,
                    "origin": EXPECTED_PREVIEW_TOP["groups"][0]["origin"],
                    "lines": [
                        {"item_code": "A1", "quantity": 4, "unit_cost": 100.0},
                        {"item_code": "A2", "quantity": 3, "unit_cost": 150.0},
                    ],
                }
            ]
        ]
        assert _short_rows() == []

        confirm = admin.post(
            URL,
            json={
                "supplier_codes": [SUPPLIER_TOP],
                "top_ups": [TOP_UP_A2],
                "confirm": True,
            },
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json() == EXPECTED_CONFIRM_TOP
        # The confirm: the same three builds, then the record's own
        # read of the suggestions — the 7-day plan and its 30-day
        # look-ahead, at the release's as-of.
        assert build_calls == (
            [(7, AS_OF), (7, AS_OF), (30, AS_OF)] * 2 + [(7, AS_OF), (30, AS_OF)]
        )
        assert create_calls == [
            [
                {
                    "site_id": SITE,
                    "vendor_id": SUPPLIER_TOP,
                    "origin": EXPECTED_PREVIEW_TOP["groups"][0]["origin"],
                    "lines": [
                        {"item_code": "A1", "quantity": 4, "unit_cost": 100.0},
                        {"item_code": "A2", "quantity": 3, "unit_cost": 150.0},
                    ],
                }
            ]
        ]
        rows = _short_rows()
        assert len(rows) == 1, "one row per short supplier released"
        row = rows[0]
        # Every field, read strictly.
        assert isinstance(row["id"], int) and row["id"] > 0
        assert row["as_of"] == AS_OF
        assert row["supplier_code"] == SUPPLIER_TOP
        assert row["source_system"] == "acumatica"
        assert row["minimum"] == Decimal("800.00")
        assert row["shortfall"] == Decimal("400.00")
        assert row["suggested"] == "add_top_ups"
        assert row["suggestion_reason"] == (
            f"A1 at {SITE} must be ordered by {AS_OF_STR}, and the "
            "top-ups reach the minimum."
        )
        assert row["chosen"] == "add_top_ups"
        assert row["actor"] == "admin", "the confirmer's username"
        assert row["created_at"] is not None
        # The plan_releases row records the same actor.
        release_rows = _plan_release_rows()
        assert len(release_rows) == 1
        assert release_rows[0]["actor"] == "admin"
        assert release_rows[0]["outcome"] == "created"
        assert release_rows[0]["supplier_code"] == SUPPLIER_TOP
        # Its suggested, suggestion_reason, minimum, shortfall and
        # as_of equal the supplier's entry in the top-ups at the same
        # data.
        before = len(build_calls)
        top_ups = admin.get(TOP_UPS_URL)
        assert top_ups.status_code == 200, top_ups.text
        top_body = top_ups.json()
        assert build_calls[before:] == [(7, None), (30, AS_OF)]
        entry = next(
            s for s in top_body["suppliers"] if s["supplier_code"] == SUPPLIER_TOP
        )
        assert entry["suggestion"] == "add_top_ups"
        assert row["suggested"] == entry["suggestion"]
        assert row["suggestion_reason"] == entry["suggestion_reason"]
        assert row["minimum"] == Decimal(entry["minimum"])
        assert row["shortfall"] == Decimal(entry["shortfall"])
        assert row["as_of"] == date.fromisoformat(top_body["as_of"])
        assert row["supplier_code"] == entry["supplier_code"]
        assert row["source_system"] == entry["source_system"]

        # -- A short supplier released without its top-ups: one row,
        #    chosen order_anyway. -------------------------------------
        before = len(build_calls)
        bare = admin.post(
            URL, json={"supplier_codes": [SUPPLIER_BARE], "confirm": True}
        )
        assert bare.status_code == 200, bare.text
        assert bare.json() == EXPECTED_CONFIRM_BARE
        # No top-ups named: the release reads the horizon plan only,
        # then the record reads the suggestions once.
        assert build_calls[before:] == [(7, AS_OF), (7, AS_OF), (30, AS_OF)]
        rows = _short_rows()
        assert [r["supplier_code"] for r in rows] == [
            SUPPLIER_TOP,
            SUPPLIER_BARE,
        ]
        bare_row = rows[1]
        assert bare_row["chosen"] == "order_anyway"
        assert bare_row["suggested"] == "order_anyway"
        assert bare_row["minimum"] == Decimal("1000.00")
        assert bare_row["shortfall"] == Decimal("700.00")
        assert bare_row["suggestion_reason"] == (
            f"B1 at {SITE} must be ordered by {AS_OF_STR}, and the "
            "top-ups cannot reach the minimum."
        )
        assert bare_row["as_of"] == AS_OF
        assert bare_row["actor"] == "admin"
        assert bare_row["created_at"] is not None

        # -- No row for a supplier that is not short. -----------------
        before = len(build_calls)
        full = admin.post(
            URL, json={"supplier_codes": [SUPPLIER_FULL], "confirm": True}
        )
        assert full.status_code == 200, full.text
        assert full.json() == EXPECTED_CONFIRM_FULL
        # The record still reads the suggestions (a group was
        # created) — but the full supplier holds no entry, so no row.
        assert build_calls[before:] == [(7, AS_OF), (7, AS_OF), (30, AS_OF)]
        assert [r["supplier_code"] for r in _short_rows()] == [
            SUPPLIER_TOP,
            SUPPLIER_BARE,
        ]

        # -- No row for a preview. ------------------------------------
        before = len(build_calls)
        previewed = admin.post(
            URL,
            json={"supplier_codes": [SUPPLIER_PREVIEW], "top_ups": [TOP_UP_D2]},
        )
        assert previewed.status_code == 200, previewed.text
        assert previewed.json() == EXPECTED_PREVIEW_PREVIEW
        assert build_calls[before:] == [(7, AS_OF), (7, AS_OF), (30, AS_OF)]
        assert [r["supplier_code"] for r in _short_rows()] == [
            SUPPLIER_TOP,
            SUPPLIER_BARE,
        ]

        # -- No row for a confirm whose batch answers error for every
        #    group: the record never reads the suggestions at all. ----
        create_outcome[0] = "error"
        before = len(build_calls)
        errored = admin.post(
            URL, json={"supplier_codes": [SUPPLIER_ERROR], "confirm": True}
        )
        assert errored.status_code == 200, errored.text
        assert errored.json() == EXPECTED_CONFIRM_ERROR
        # Only the release's own horizon build — the record held its
        # read: no group of the answer was created.
        assert build_calls[before:] == [(7, AS_OF)]
        assert [r["supplier_code"] for r in _short_rows()] == [
            SUPPLIER_TOP,
            SUPPLIER_BARE,
        ]
        # The error outcome still wrote its plan_releases row.
        error_rows = [
            r for r in _plan_release_rows() if r["supplier_code"] == SUPPLIER_ERROR
        ]
        assert len(error_rows) == 1
        assert error_rows[0]["outcome"] == "error"
        create_outcome[0] = "created"

        # -- A standing-grant run releasing a short supplier: one row,
        #    actor standing-grant:<id>, chosen order_anyway — the run
        #    the way 1198 runs it. ------------------------------------
        _seed_feeds()
        created = admin.post(
            GRANTS_URL,
            json={
                "process": "order_plan_release",
                "max_document_value": 160,
                "max_daily_value": 100000,
                "max_data_age_hours": 24,
            },
        )
        assert created.status_code == 201, created.text
        grant = created.json()
        grant_id = grant["id"]
        grant_ids.append(grant_id)
        assert grant["process"] == "order_plan_release"
        assert grant["in_force"] is True
        before = len(build_calls)
        row = standing_release.run_order_plan_grant(now=NOW)
        assert row["process"] == "order_plan_release"
        assert row["grant_id"] == grant_id
        assert row["started_at"] == NOW.isoformat()
        assert row["outcome"] == "released"
        assert row["reason"] is None
        assert row["documents"] == 1
        assert row["value"] == "150.0"
        # Every other group lies over the document limit — the plan
        # order is by (site, supplier).
        assert row["left_out"] == [
            {
                "site_code": SITE,
                "supplier_code": SUPPLIER_BARE,
                "value": "300.0",
                "reason": "document_limit",
            },
            {
                "site_code": SITE,
                "supplier_code": SUPPLIER_FULL,
                "value": "200.0",
                "reason": "document_limit",
            },
            {
                "site_code": SITE,
                "supplier_code": SUPPLIER_PREVIEW,
                "value": "200.0",
                "reason": "document_limit",
            },
            {
                "site_code": SITE,
                "supplier_code": SUPPLIER_RAISE,
                "value": "250.0",
                "reason": "document_limit",
            },
            {
                "site_code": SITE,
                "supplier_code": SUPPLIER_TOP,
                "value": "400.0",
                "reason": "document_limit",
            },
        ]
        # The run: the plan at the grant's horizon (the default 1),
        # then the record's read at the same horizon and as-of.
        assert build_calls[before:] == [(1, AS_OF), (1, AS_OF), (30, AS_OF)]
        # The run's plan_releases row: the grant as actor, created.
        grant_releases = [
            r for r in _plan_release_rows() if r["supplier_code"] == SUPPLIER_GRANT
        ]
        assert len(grant_releases) == 1
        assert grant_releases[0]["actor"] == f"standing-grant:{grant_id}"
        assert grant_releases[0]["outcome"] == "created"
        # One short-order row for the released short supplier, the
        # grant as actor, chosen order_anyway: the grant's group holds
        # no top-up line.
        rows = _short_rows()
        assert [r["supplier_code"] for r in rows] == [
            SUPPLIER_TOP,
            SUPPLIER_BARE,
            SUPPLIER_GRANT,
        ]
        grant_row = rows[2]
        assert grant_row["as_of"] == AS_OF
        assert grant_row["supplier_code"] == SUPPLIER_GRANT
        assert grant_row["source_system"] == "acumatica"
        assert grant_row["minimum"] == Decimal("700.00")
        assert grant_row["shortfall"] == Decimal("550.00")
        assert grant_row["suggested"] == "order_anyway"
        assert grant_row["suggestion_reason"] == (
            f"F1 at {SITE} must be ordered by {AS_OF_STR}, and the "
            "top-ups cannot reach the minimum."
        )
        assert grant_row["chosen"] == "order_anyway"
        assert grant_row["actor"] == f"standing-grant:{grant_id}"
        assert grant_row["created_at"] is not None

        # -- A failure to record: with the insert made to raise, the
        #    confirm still answers 200 with the same body, the
        #    plan_releases rows are written, the failure is logged as
        #    a warning and no row is stored. ---------------------------
        def _raising_insert(rows_):
            raise RuntimeError("the record's insert is made to raise")

        monkeypatch.setattr(short_orders, "_insert_rows", _raising_insert)
        with caplog.at_level(logging.WARNING, logger="app.supply.short_orders"):
            before = len(build_calls)
            raised = admin.post(
                URL, json={"supplier_codes": [SUPPLIER_RAISE], "confirm": True}
            )
        assert raised.status_code == 200, raised.text
        assert raised.json() == EXPECTED_CONFIRM_RAISE
        # The record read the suggestions before its insert failed.
        assert build_calls[before:] == [(7, AS_OF), (7, AS_OF), (30, AS_OF)]
        # The failure is logged as a warning.
        assert any(
            record.levelno == logging.WARNING
            and "failed to record the short-order release" in record.getMessage()
            for record in caplog.records
        ), "the failure to record is logged as a warning"
        # The plan_releases rows are written; no short-order row.
        raise_releases = [
            r for r in _plan_release_rows() if r["supplier_code"] == SUPPLIER_RAISE
        ]
        assert len(raise_releases) == 1
        assert raise_releases[0]["outcome"] == "created"
        assert raise_releases[0]["actor"] == "admin"
        assert [r["supplier_code"] for r in _short_rows()] == [
            SUPPLIER_TOP,
            SUPPLIER_BARE,
            SUPPLIER_GRANT,
        ]
    finally:
        _cleanup(grant_ids)
