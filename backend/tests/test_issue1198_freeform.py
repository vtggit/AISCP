"""Issue #1198 proof: the standing-grant run of the order plan.

Everything runs against the test database.  The plan the run reads is
seeded the way the issue 842 proof seeds it — the builder's sources
replaced (``recommendations_for_all`` answering two Acumatica
item-sites of site 154 at known, different values and one Odoo line)
and a demand event seeded so the release's as-of is a fixed data
date — and the Acumatica client's preview and create are replaced
with recording fakes the same way.  The Acumatica environment is set
and writes are allowed.  The grants are created and revoked through
the #1173 routes (``POST /api/standing-grants`` and
``POST /api/standing-grants/{id}/revoke``) the way
``test_issue1173_freeform.py`` calls them.

Proved here:

  * the table — ``standing_grant_runs`` exists with exactly the
    documented ten columns (bigint identity ``id`` primary key, text
    ``process`` and ``outcome`` not null, bigint ``grant_id`` not
    null, timestamptz ``started_at`` not null defaulting to now(),
    text ``reason`` null, integer ``documents`` not null defaulting
    to 0, numeric ``value`` not null defaulting to 0, jsonb
    ``left_out`` not null defaulting to an empty JSON array); the
    database's ``alembic_version`` equals the shared ``CURRENT_HEAD``
    pin; the revision that creates the table is found by walking the
    Alembic history (the proof never names it and never asserts that
    it is the head or the only head); the database itself refuses a
    direct insert for an unknown ``process``, an unknown ``outcome``,
    a negative ``documents``, a negative ``value`` and a
    ``grant_id`` no grant has;
  * the setting — ``get_setting("erp.standing_grants_horizon_days")``
    has system scope, type number, default 1, min 1, max 60 and the
    stated description, and ``GET /api/settings`` lists it;
  * nothing happens — with no grant the run answers
    ``{"outcome": "no_grant"}``, with a grant in force and the switch
    stored false it answers ``{"outcome": "off"}``, and with the
    run's lock held by another connection it answers
    ``{"outcome": "busy"}``; none of the three writes a run row or a
    ``plan_releases`` row or calls a fake;
  * stale data — with every Acumatica row's ``synced_at`` older than
    the grant allows the run writes a ``skipped`` row naming the
    grant's hours and makes no call, and with one feed table holding
    no Acumatica row it writes a ``skipped`` row naming that table
    and makes no call;
  * released within the limits — a grant whose
    ``max_document_value`` lies between the two groups' values sends
    only the smaller group: the fake's create receives exactly that
    one order, its ``plan_releases`` row has the actor
    ``standing-grant:<id>`` and the outcome ``created``, the run row
    is ``released`` with ``documents`` 1 and that group's value,
    ``left_out`` names the other group with ``document_limit`` and
    the Odoo line is never sent;
  * the day's total — a later run on the same UTC date, under a
    grant whose ``max_daily_value`` leaves less room than either
    group's value, sends nothing and records ``skipped`` with both
    groups in ``left_out`` as ``daily_limit``; on the next UTC date
    the allowance is whole again and the group within it is sent;
  * writes switched off — with Acumatica writes not allowed the run
    records ``failed`` with the reason "Acumatica writes are
    disabled", its ``documents`` and ``value`` those of the groups it
    tried to send;
  * nothing due — with no due line the run records ``nothing_due``
    and calls nothing;
  * no top-ups — with a stored minimum for one group's supplier above
    that group's value (the group a top-up candidate on the
    ``GET /api/supply-plan`` page), the order sent for that group
    holds exactly the plan's lines;
  * a person's release is unchanged — ``POST /api/supply-plan/release``
    with ``confirm`` true, by an admin, still writes
    ``plan_releases`` rows with the admin's username as the actor;
  * the identity requirement: the proof also runs as a NON-ADMIN
    (role "user", asserted via ``GET /api/auth/me``) in a second pass
    after a full reload (a fresh ``TestClient`` booted the production
    way — the token the page would take from the URL hash as
    ``#access_token=...``, ``GET /api/auth/config`` answering publicly
    and ``GET /api/auth/me`` resolving the viewer): the admin-only
    controls (the confirm, the grant writes) are refused with 403
    while the pages still render — the release preview and the
    grants list answer 200 — and nothing new is written.
"""

import ast
import io
import os
import tokenize
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import psycopg2
import psycopg2.errors
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection, get_cursor
from app.feed.acumatica_client import AcumaticaClient, AcumaticaWritesDisabledError
from app.feed.status import FEED_TABLES
from app.main import app
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.standing_grant_runs_postgres_repository import GRANT_RUN_LOCK_KEY
from app.repositories.supplier_order_minimums_postgres_repository import (
    SupplierOrderMinimumPostgresRepository,
)
from app.repositories.supplier_terms_postgres_repository import (
    SupplierTermsPostgresRepository,
)
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)
from app.settings.registry import get_setting
from app.supply import standing_release
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

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

SUPPLIER_A = "S-1198-1"
SUPPLIER_B = "S-1198-2"
SUPPLIER_C = "S-1198-3"
ITEM_A = "ITEM-1198-A"
ITEM_B = "ITEM-1198-B"
ITEM_C = "ITEM-1198-C"
SITE_A = "154"
SITE_C = "159"
ORIGIN_A = f"AIERP order plan {AS_OF.isoformat()} site {SITE_A} vendor {SUPPLIER_A}"
ORIGIN_B = f"AIERP order plan {AS_OF.isoformat()} site {SITE_A} vendor {SUPPLIER_B}"

# The two Acumatica groups' known, different values: 125 x 4.5 and
# 100 x 10.01 (10.005 rounded half-up to cents, as the release does).
VALUE_A = "562.5"
VALUE_B = "1001.00"

_TABLE = "standing_grant_runs"
_COLUMNS = {
    "id": ("bigint", None, "NO", None, "YES"),
    "process": ("text", None, "NO", None, "NO"),
    "grant_id": ("bigint", None, "NO", None, "NO"),
    "started_at": ("timestamp with time zone", None, "NO", "now()", "NO"),
    "outcome": ("text", None, "NO", None, "NO"),
    "reason": ("text", None, "YES", None, "NO"),
    "documents": ("integer", None, "NO", "0", "NO"),
    "value": ("numeric", None, "NO", "0", "NO"),
    "left_out": ("jsonb", None, "NO", "'[]'::jsonb", "NO"),
}
SETTINGS_KEY = "erp.standing_grants_horizon_days"
SETTINGS_DESCRIPTION = (
    "How many days ahead a standing-grant run of the order plan looks "
    "for due lines; 1 sends each line on the day it is due."
)
WRITES_OFF_REASON = "Acumatica writes are disabled"
ADMIN_DETAIL = "Role 'admin' is required to perform this action."


def _fresh(t: datetime) -> datetime:
    """One hour before ``t`` — within every grant's data-age hours."""
    return t - timedelta(hours=1)


def _old(t: datetime) -> datetime:
    """Two days before ``t`` — beyond the grants' 24 data-age hours."""
    return t - timedelta(hours=48)


def _table_columns() -> dict:
    """The table's columns as (data_type, max_length, nullability, default, identity)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable, column_default, is_identity "
                "FROM information_schema.columns "
                "WHERE table_name = %s ORDER BY ordinal_position",
                (_TABLE,),
            )
            return {r[0]: (r[1], r[2], r[3], r[4], r[5]) for r in cur.fetchall()}
    finally:
        conn.close()


def _index_defs() -> list[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = %s ORDER BY indexname",
                (_TABLE,),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _fk_constraints() -> list[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_get_constraintdef(con.oid) "
                "FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "WHERE rel.relname = %s AND con.contype = 'f'",
                (_TABLE,),
            )
            return [r[0] for r in cur.fetchall()]
    finally:
        conn.close()


def _alembic_revision() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _creates_table(source: str, table: str) -> bool:
    """True only when ``source`` holds a real ``op.create_table`` call.

    The call's first argument must be the string literal naming
    ``table`` — single- or double-quoted, any white space (line
    breaks included) allowed between the parenthesis and the string.
    A name that merely appears in a comment, a docstring, a
    ``down_revision`` string, a foreign key, a differently named
    table or another call (such as ``op.drop_table``) never matches,
    nor does an expression that merely begins with the literal
    (a concatenation or an operation building a different name).
    """
    tokens: list = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            tokens.append(token)
    except (
        tokenize.TokenError,
        IndentationError,
        SyntaxError,
        ValueError,
        UnicodeDecodeError,
    ):
        pass
    skip = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
    }
    for index, token in enumerate(tokens):
        if token.type != tokenize.NAME or token.string != "op":
            continue
        # ``x.op.create_table`` is a call on ``x.op``, not on ``op``.
        if index and tokens[index - 1].string == ".":
            continue
        window = tokens[index : index + 4]
        if [(t.type, t.string) for t in window] != [
            (tokenize.NAME, "op"),
            (tokenize.OP, "."),
            (tokenize.NAME, "create_table"),
            (tokenize.OP, "("),
        ]:
            continue
        literals: list[str] = []
        ends_argument = False
        position = index + 4
        while position < len(tokens):
            argument = tokens[position]
            position += 1
            if argument.type in skip:
                continue
            if argument.type == tokenize.STRING:
                literals.append(argument.string)
                continue
            ends_argument = argument.type == tokenize.OP and argument.string in (
                ",",
                ")",
            )
            break
        if not literals or not ends_argument:
            continue
        try:
            value = ast.literal_eval(" ".join(literals))
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
        if isinstance(value, str) and value == table:
            return True
    return False


def _creating_revision():
    """The revision that creates the table, found by walking the history.

    The proof never names the revision: the Alembic history is walked
    and the one revision whose module creates the table is the one
    this proof is about.
    """
    script = ScriptDirectory(_MIGRATIONS_DIR)
    for rev in script.walk_revisions():
        path = getattr(rev.module, "__file__", None)
        if not path:
            continue
        with open(path, "rb") as fh:
            source = fh.read().decode("utf-8", errors="replace")
        if _creates_table(source, _TABLE):
            return rev
    raise AssertionError(f"no revision creates {_TABLE}")


def _run_rows() -> list[dict]:
    """Every standing_grant_runs row, oldest first, every column."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM {_TABLE} ORDER BY id")
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


def _expect_db_rejection(
    conn,
    sql: str,
    params: tuple,
    expected: type[psycopg2.Error],
    label: str,
) -> None:
    """Run one statement in a savepoint; it must fail with ``expected``.

    The savepoint keeps the surrounding (rolled-back) transaction
    usable after each violation, and nothing here is ever committed.
    """
    with conn.cursor() as cur:
        cur.execute("SAVEPOINT issue1198")
        try:
            cur.execute(sql, params)
        except psycopg2.Error as exc:
            assert type(exc) is expected, f"{label}: {type(exc).__name__}"
            cur.execute("ROLLBACK TO SAVEPOINT issue1198")
        else:
            raise AssertionError(f"{label}: the database accepted the row")
        finally:
            cur.execute("RELEASE SAVEPOINT issue1198")


def _run_insert_sql() -> str:
    return (
        "INSERT INTO standing_grant_runs "
        "(process, grant_id, started_at, outcome, reason, documents, value, "
        "left_out) VALUES (%s, %s, NOW(), %s, NULL, %s, %s, '[]'::jsonb)"
    )


class FakeRecommendations:
    """The fake for the route module's ``recommendations_for_all``:
    the canned rows, all due now — (a) and (b) the two Acumatica
    item-sites of site 154 at known, different values, (c) the Odoo
    line of site 159.  ``rows`` is mutable: the nothing-due pass
    empties it."""

    def __init__(self, rows: list) -> None:
        self.rows = rows

    def __call__(self, as_of=None):
        return self.rows


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
                "1198-demand-1",
                "1198-demand-1",
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
            # Two Acumatica item-sites at site 154, one Odoo line.
            {"item_code": ITEM_A, "site_code": SITE_A, "source_system": "acumatica"},
            {"item_code": ITEM_B, "site_code": SITE_A, "source_system": "acumatica"},
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
                "vendor_code": "V-1198-1",
                "name": "Acme Fasteners",
                "source_system": "acumatica",
            },
            {
                "supplier_code": SUPPLIER_B,
                "vendor_code": "V-1198-2",
                "name": "Bolt & Nut Works",
                "source_system": "acumatica",
            },
            {
                "supplier_code": SUPPLIER_C,
                "vendor_code": "V-1198-3",
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
            ("1198-onhand-1", ITEM_A, SITE_A, 50.0, NOW, synced_at),
        )
    if table == "open_purchase_lines":
        return (
            "INSERT INTO open_purchase_lines (id, business_key, item_code, "
            "site_code, supplier_code, order_type, open_units, promised_on, "
            "ordered_on, as_of, source_system, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, 'purchase', %s, %s, %s, %s, "
            "'acumatica', %s)",
            (
                "1198-opl-1",
                "1198-opl-1",
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
            "1198-lto-1",
            "1198-lto-1",
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


def _feed_freshness(synced_at: datetime, missing: tuple = ()) -> None:
    """One Acumatica row at ``synced_at`` in every feed table.

    Every Acumatica row of each table is stamped ``synced_at`` — the
    run's freshness check reads each table's newest such stamp — a
    table that holds no Acumatica row gets its one back, and a table
    in ``missing`` holds no Acumatica row at all.
    """
    with get_cursor() as cur:
        for table in FEED_TABLES:
            if table in missing:
                cur.execute(f"DELETE FROM {table} WHERE source_system = 'acumatica'")
                continue
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


def _order_a():
    """(a) in the wire format the release body sends to Acumatica."""
    return {
        "site_id": SITE_A,
        "vendor_id": SUPPLIER_A,
        "origin": ORIGIN_A,
        "lines": [{"item_code": ITEM_A, "quantity": 125, "unit_cost": 4.5}],
    }


def _order_b():
    return {
        "site_id": SITE_A,
        "vendor_id": SUPPLIER_B,
        "origin": ORIGIN_B,
        "lines": [{"item_code": ITEM_B, "quantity": 100, "unit_cost": 10.01}],
    }


def _group_body(supplier, origin, line, value, outcome, order_nbr):
    return {
        "site_code": SITE_A,
        "supplier_code": supplier,
        "origin": origin,
        "lines": [line],
        "value": value,
        "outcome": outcome,
        "warehouse_code": "WHOLESALE",
        "vendor_code": "CONPERIPH",
        "order_nbr": order_nbr,
        "error": None,
    }


EXPECTED_PREVIEW_A = {
    "month": AS_OF_MONTH,
    "groups": [
        _group_body(
            SUPPLIER_A,
            ORIGIN_A,
            {
                "item_code": ITEM_A,
                "quantity": 125,
                "unit_cost": "4.5",
                "value": "562.5",
            },
            VALUE_A,
            "would_create",
            None,
        )
    ],
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


def test_issue1198_freeform(monkeypatch, client, user_token) -> None:
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
                "order_nbr": f"PO-1198-{len(create_calls):03d}",
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

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # -- The table: exactly the ten documented columns, the database
    #    on the shared pin, the creating revision found by walking the
    #    history (never named here; the proof never asserts that it is
    #    the head or the only head). -------------------------------
    assert _table_columns() == _COLUMNS
    assert _alembic_revision() == CURRENT_HEAD
    creating = _creating_revision()
    script = ScriptDirectory(_MIGRATIONS_DIR)
    assert creating.down_revision is not None
    assert script.get_revision(creating.down_revision) is not None
    # The index over (process, started_at) serves the day's total;
    # the foreign key keeps grant_id a grant's id.
    assert any("USING btree (process, started_at)" in d for d in _index_defs())
    assert any(
        "erp_standing_grants(id)" in definition for definition in _fk_constraints()
    )

    # -- Nothing happens: no grant in force answers no_grant, and
    #    nothing is read beyond that or written. --------------------
    assert standing_release.run_order_plan_grant(now=NOW) == {"outcome": "no_grant"}
    assert _run_rows() == []
    assert _plan_release_rows() == []
    assert preview_calls == []
    assert create_calls == []

    # -- The setting. ------------------------------------------------
    setting = get_setting(SETTINGS_KEY)
    assert setting is not None
    assert setting.scope == "system"
    assert setting.type == "number"
    assert setting.default == 1
    assert setting.min == 1
    assert setting.max == 60
    assert setting.description == SETTINGS_DESCRIPTION
    body = admin.get("/api/settings").json()
    entry = next(e for e in body if e["key"] == SETTINGS_KEY)
    assert entry["scope"] == "system"
    assert entry["type"] == "number"
    assert entry["default"] == 1
    assert entry["min"] == 1
    assert entry["max"] == 60
    assert entry["description"] == SETTINGS_DESCRIPTION
    assert entry["value"] == 1
    assert entry["source"] == "default"

    # -- The grant in force, through the #1173 routes. ---------------
    # max_document_value lies between the two groups' values: the
    # smaller group is covered, the larger is not.
    created = admin.post("/api/standing-grants", json=_grant_body(800, 100000))
    assert created.status_code == 201, created.text
    grant_1 = created.json()
    assert grant_1["process"] == "order_plan_release"
    assert grant_1["in_force"] is True
    grant_1_id = grant_1["id"]

    # The database refuses the direct inserts.
    conn = get_connection()
    try:
        sql = _run_insert_sql()
        _expect_db_rejection(
            conn,
            sql,
            ("bogus_process", grant_1_id, "released", 0, 0),
            psycopg2.errors.CheckViolation,
            "unknown process",
        )
        _expect_db_rejection(
            conn,
            sql,
            ("order_plan_release", grant_1_id, "weird", 0, 0),
            psycopg2.errors.CheckViolation,
            "unknown outcome",
        )
        _expect_db_rejection(
            conn,
            sql,
            ("order_plan_release", grant_1_id, "released", -1, 0),
            psycopg2.errors.CheckViolation,
            "negative documents",
        )
        _expect_db_rejection(
            conn,
            sql,
            ("order_plan_release", grant_1_id, "released", 0, -1),
            psycopg2.errors.CheckViolation,
            "negative value",
        )
        _expect_db_rejection(
            conn,
            sql,
            ("order_plan_release", 999999999, "released", 0, 0),
            psycopg2.errors.ForeignKeyViolation,
            "unknown grant",
        )
    finally:
        conn.close()
    assert _run_rows() == [], "refused inserts store nothing"

    # -- Nothing happens: the switch stored false answers off. -------
    off = admin.put("/api/settings/erp.standing_grants_enabled", json={"value": False})
    assert off.status_code == 200, off.text
    assert standing_release.run_order_plan_grant(now=NOW) == {"outcome": "off"}
    assert _run_rows() == []
    assert _plan_release_rows() == []
    assert preview_calls == []
    assert create_calls == []
    reset = admin.delete("/api/settings/erp.standing_grants_enabled")
    assert reset.status_code == 200, reset.text

    # -- Nothing happens: another session holds the run's lock. ------
    lock_conn = get_connection()
    try:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (GRANT_RUN_LOCK_KEY,))
            assert bool(cur.fetchone()[0]), "the proof holds the run's lock"
        assert standing_release.run_order_plan_grant(now=NOW) == {"outcome": "busy"}
        assert _run_rows() == []
        assert _plan_release_rows() == []
        assert preview_calls == []
        assert create_calls == []
    finally:
        with lock_conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(%s)", (GRANT_RUN_LOCK_KEY,))
        lock_conn.close()

    # -- Stale data: every Acumatica row older than the grant allows.
    _feed_freshness(_old(NOW))
    row = standing_release.run_order_plan_grant(now=NOW)
    assert row["process"] == "order_plan_release"
    assert row["grant_id"] == grant_1_id
    assert row["started_at"] == NOW.isoformat()
    assert row["outcome"] == "skipped"
    assert row["reason"] == "the synced Acumatica data is older than 24 hours"
    assert row["documents"] == 0
    assert row["value"] == "0"
    assert row["left_out"] == []
    rows = _run_rows()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "skipped"
    assert rows[0]["reason"] == "the synced Acumatica data is older than 24 hours"
    assert _plan_release_rows() == []
    assert preview_calls == []
    assert create_calls == []

    # -- Stale data: one feed table holds no Acumatica row. ----------
    _feed_freshness(_fresh(NOW), missing=("on_hand",))
    row = standing_release.run_order_plan_grant(now=NOW)
    assert row["outcome"] == "skipped"
    assert row["reason"] == "no synced Acumatica data in on_hand"
    assert row["documents"] == 0
    assert row["value"] == "0"
    assert row["left_out"] == []
    assert len(_run_rows()) == 2
    assert _plan_release_rows() == []
    assert preview_calls == []
    assert create_calls == []

    # -- Released within the limits: only the smaller group. --------
    _feed_freshness(_fresh(NOW))
    row = standing_release.run_order_plan_grant(now=NOW)
    assert row["id"] is not None
    assert row["process"] == "order_plan_release"
    assert row["grant_id"] == grant_1_id
    assert row["started_at"] == NOW.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 1
    assert row["value"] == VALUE_A
    assert row["left_out"] == [
        {
            "site_code": SITE_A,
            "supplier_code": SUPPLIER_B,
            "value": VALUE_B,
            "reason": "document_limit",
        }
    ]
    # The fake's create receives exactly the smaller group's order —
    # and never the Odoo line's.
    assert create_calls == [[_order_a()]]
    assert ITEM_C not in str(create_calls)
    # The run's plan_releases row: the grant as actor, created.
    releases = _plan_release_rows()
    assert len(releases) == 1
    release_row = releases[0]
    assert release_row["actor"] == f"standing-grant:{grant_1_id}"
    assert release_row["outcome"] == "created"
    assert release_row["supplier_code"] == SUPPLIER_A
    assert release_row["origin"] == ORIGIN_A
    assert release_row["value"] == Decimal(VALUE_A)
    assert release_row["lines"] == [
        {"item_code": ITEM_A, "quantity": 125, "unit_cost": "4.5", "value": "562.5"}
    ]
    assert len(_run_rows()) == 3

    # -- The day's total: same UTC date, a tighter daily grant. -----
    # Revoked through the route; the new grant covers both documents
    # but leaves the day 1100 - 562.5 = 537.5 — less than either
    # group's value.
    revoked = admin.post(f"/api/standing-grants/{grant_1_id}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["in_force"] is False
    created = admin.post("/api/standing-grants", json=_grant_body(1001, 1100))
    assert created.status_code == 201, created.text
    grant_2_id = created.json()["id"]
    row = standing_release.run_order_plan_grant(now=NOW)
    assert row["grant_id"] == grant_2_id
    assert row["outcome"] == "skipped"
    assert row["reason"] == "every due document is over a limit"
    assert row["documents"] == 0
    assert row["value"] == "0"
    assert row["left_out"] == [
        {
            "site_code": SITE_A,
            "supplier_code": SUPPLIER_A,
            "value": VALUE_A,
            "reason": "daily_limit",
        },
        {
            "site_code": SITE_A,
            "supplier_code": SUPPLIER_B,
            "value": VALUE_B,
            "reason": "daily_limit",
        },
    ]
    assert len(_run_rows()) == 4
    assert _plan_release_rows() == releases, "nothing sent on the tight day"
    assert len(create_calls) == 1

    # -- The next UTC date: the allowance is whole again. -----------
    next_day = NOW + timedelta(days=1)
    _feed_freshness(_fresh(next_day))
    row = standing_release.run_order_plan_grant(now=next_day)
    assert row["started_at"] == next_day.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 1
    assert row["value"] == VALUE_A
    assert row["left_out"] == [
        {
            "site_code": SITE_A,
            "supplier_code": SUPPLIER_B,
            "value": VALUE_B,
            "reason": "daily_limit",
        }
    ]
    assert len(create_calls) == 2
    assert create_calls[-1] == [_order_a()]
    assert len(_run_rows()) == 5
    releases = _plan_release_rows()
    assert len(releases) == 2

    # -- Writes switched off: the run records the refusal. ----------
    revoked = admin.post(f"/api/standing-grants/{grant_2_id}/revoke")
    assert revoked.status_code == 200, revoked.text
    created = admin.post("/api/standing-grants", json=_grant_body(100000, 1000000))
    assert created.status_code == 201, created.text
    grant_3_id = created.json()["id"]
    third_day = NOW + timedelta(days=2)
    _feed_freshness(_fresh(third_day))
    monkeypatch.delenv("ACUMATICA_ALLOW_WRITES")
    row = standing_release.run_order_plan_grant(now=third_day)
    assert row["grant_id"] == grant_3_id
    assert row["outcome"] == "failed"
    assert row["reason"] == WRITES_OFF_REASON
    # The documents and the value are those of the groups the run
    # tried to send — both fit the grant and the whole day's allowance.
    assert row["documents"] == 2
    assert row["value"] == "1563.50"
    assert row["left_out"] == []
    assert len(_run_rows()) == 6
    assert len(_plan_release_rows()) == 2, "the refusal writes no release row"
    assert len(create_calls) == 2
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # -- Nothing due: no due line, nothing sent, nothing called. ----
    fake_recommendations.rows = []
    try:
        row = standing_release.run_order_plan_grant(now=third_day)
        assert row["outcome"] == "nothing_due"
        assert row["reason"] is None
        assert row["documents"] == 0
        assert row["value"] == "0"
        assert row["left_out"] == []
        assert len(_run_rows()) == 7
        assert _plan_release_rows() == releases
        assert len(create_calls) == 2
        assert len(preview_calls) == 0
    finally:
        fake_recommendations.rows = _recommendations()

    # -- No top-ups: the group below its stored minimum is a top-up
    #    candidate on the plan page, yet the order sent for it holds
    #    exactly the plan's lines.
    SupplierOrderMinimumPostgresRepository().upsert(
        "acumatica", SUPPLIER_B, 5000.0, "admin"
    )
    plan_page = admin.get("/api/supply-plan")
    assert plan_page.status_code == 200, plan_page.text
    plan_groups = {g["supplier_code"]: g for g in plan_page.json()["suppliers"]}
    assert float(plan_groups[SUPPLIER_B]["minimum"]) == 5000.0
    # 5000 - 1000.50: the plan page values the line at its raw unit
    # cost (10.005 x 100), the release at its rounded 10.01.
    assert float(plan_groups[SUPPLIER_B]["shortfall"]) == 3999.5
    fourth_day = NOW + timedelta(days=3)
    _feed_freshness(_fresh(fourth_day))
    row = standing_release.run_order_plan_grant(now=fourth_day)
    assert row["grant_id"] == grant_3_id
    assert row["outcome"] == "released"
    assert row["documents"] == 2
    assert row["value"] == "1563.50"
    assert row["left_out"] == []
    assert len(create_calls) == 3
    assert create_calls[-1] == [_order_a(), _order_b()]
    # The group's order is its plan line alone — no top-up line.
    assert create_calls[-1][1]["lines"] == [
        {"item_code": ITEM_B, "quantity": 100, "unit_cost": 10.01}
    ]
    # One plan_releases row per sent group.
    assert len(_plan_release_rows()) == 4
    assert len(_run_rows()) == 8

    # -- A person's release is unchanged: the admin's confirm still
    #    writes plan_releases rows with the admin's username as the
    #    actor.
    confirm = admin.post(
        "/api/supply-plan/release",
        json={"supplier_codes": [SUPPLIER_A], "confirm": True},
    )
    assert confirm.status_code == 200, confirm.text
    assert confirm.json()["groups"][0]["outcome"] == "created"
    releases = _plan_release_rows()
    assert len(releases) == 5
    person_row = releases[-1]
    assert person_row["actor"] == "admin"
    assert person_row["outcome"] == "created"
    assert person_row["supplier_code"] == SUPPLIER_A
    assert person_row["lines"] == [
        {"item_code": ITEM_A, "quantity": 125, "unit_cost": "4.5", "value": "562.5"}
    ]
    assert create_calls[-1] == [_order_a()]

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

    # The release page still renders for the viewer — the preview is
    # open to any signed-in user and answers with the same body —
    # while the admin-only confirm is absent (refused, nothing sent).
    preview = viewer.post(
        "/api/supply-plan/release", json={"supplier_codes": [SUPPLIER_A]}
    )
    assert preview.status_code == 200, preview.text
    assert preview.json() == EXPECTED_PREVIEW_A
    refused = viewer.post(
        "/api/supply-plan/release",
        json={"supplier_codes": [SUPPLIER_A], "confirm": True},
    )
    assert refused.status_code == 403
    assert refused.json()["detail"] == ADMIN_DETAIL

    # The grants page still renders — the list answers 200 — while
    # the admin-only grant writes are absent (refused).
    grants = viewer.get("/api/standing-grants")
    assert grants.status_code == 200
    assert len(grants.json()) == 3
    refused_grant = viewer.post("/api/standing-grants", json=_grant_body(800, 100000))
    assert refused_grant.status_code == 403

    # Still nothing new written, no new call.
    assert len(_plan_release_rows()) == 5
    assert len(_run_rows()) == 8
    assert len(create_calls) == 4
    assert preview_calls == [[_order_a()]]
