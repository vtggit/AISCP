"""Issue #1302 proof: the no_value limit of the standing-grant run
of the transfer release — a suggestion valued at 0 or below.

Everything runs against the test database.  The suggestions the run
reads are seeded the way the issue 1243 proof seeds them — the route
module's ``recommendations_for_all`` replaced with a fake that counts
its calls and the item_sites read seeded for the same source systems
— so the builder answers documents of known values: the 600.00
witness (item I: 167 to 168), two Acumatica transfer orders of known,
different values on two site pairs (item B: 154 to 161 at 450.00,
item A: 154 to 159 at 300.00), one document beside a valued
suggestion holding a suggestion valued at 0 (item E: 163 to 164 at
0.00 beside item F: 163 to 164 at 100.00) and one holding a
suggestion valued at -1 (item G: 165 to 166 at -1.00 beside item H:
165 to 166 at 100.00), one Odoo suggestion (item C: 154 to 159) and
one Acumatica suggestion whose receiving item-site has no unit cost
(item D: 161 to 162, no value) — and a demand event is seeded so the
as-of is the fixed data date.  The Acumatica client's transfer
preview and create (``AcumaticaClient.preview_transfer_orders``,
``AcumaticaClient.create_transfer_orders``) are replaced with
recording fakes the same way, the create consulting the write switch
before its empty-batch shortcut, as the real client does.  The
Acumatica environment is set and writes are allowed.  The grants are
created and revoked through the #1173 routes
(``POST /api/standing-grants`` and ``POST /api/standing-grants/{id}/
revoke``).

Proved here:

  * a value of 0 or below — a document holding a suggestion valued
    at 0, and again one valued at -1, each beside a suggestion valued
    above 0, is left out with ``no_value`` and never sent, its
    left-out value the summed value of its suggestions that are above
    0 (the 0 and the -1 excluded from the sum); in the same run the
    other document within the limits is sent, and a later run on the
    same UTC date under a grant whose ``max_daily_value`` holds the
    witness shows the day's allowance reduced by the sent document's
    value only — not by the left-out documents' values;
  * only transfer runs count toward the day — an
    ``order_plan_release`` run recorded on the same UTC date as a
    later transfer run through
    ``StandingGrantRunPostgresRepository.record``, of a value large
    enough that, counted, it would push the later run's document
    past ``max_daily_value``, leaves the transfer allowance whole:
    the document is sent;
  * the day turns at UTC midnight — under a grant whose
    ``max_daily_value`` fits one of the two documents but not two, a
    run at 23:59 UTC sends one document and a run at 00:01 UTC on
    the next date sends the other — a rolling 24-hour window would
    refuse it;
  * a person's confirm keeps the display name —
    ``POST /api/supply-plan/transfers/release`` with ``confirm``
    true, by an admin whose identity carries a display name, writes
    ``transfer_releases`` rows whose ``actor_name`` is that display
    name — the grant's runs above carry a null one.
"""

import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

import app.api.supply_plan as supply_plan
from app.db.connection import get_connection, get_cursor
from app.feed.acumatica_client import AcumaticaClient, AcumaticaWritesDisabledError
from app.feed.status import FEED_TABLES
from app.main import app
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)
from app.repositories.standing_grant_runs_postgres_repository import (
    StandingGrantRunPostgresRepository,
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
# The display name the admin's identity carries: the development
# admin's username, the one the person's confirm stores beside the
# actor.
ADMIN_DISPLAY_NAME = "admin"

# The latest transaction date in the seeded data — deliberately not
# the server date: the as-of of every origin tag the run and the
# person's confirm build.
AS_OF = date(2026, 10, 15)
# The run's clock: fixed aware UTC instants, never the server clock.
NOW = datetime(2026, 11, 1, 12, 0, 0, tzinfo=UTC)
# The day the order-plan run is recorded on: the same UTC date as
# the later transfer run, earlier on it.
ORDER_PLAN_RUN_AT = datetime(2026, 11, 2, 9, 0, 0, tzinfo=UTC)
DAY2 = datetime(2026, 11, 2, 12, 0, 0, tzinfo=UTC)
# The midnight turn: 23:59 UTC one date, 00:01 UTC the next — two
# minutes apart, which a rolling 24-hour window would hold together.
DAY3 = datetime(2026, 11, 3, 23, 59, 0, tzinfo=UTC)
DAY4 = datetime(2026, 11, 4, 0, 1, 0, tzinfo=UTC)

ITEM_A = "ITEM-1302-A"
ITEM_B = "ITEM-1302-B"
ITEM_C = "ITEM-1302-C"
ITEM_D = "ITEM-1302-D"
ITEM_E = "ITEM-1302-E"
ITEM_F = "ITEM-1302-F"
ITEM_G = "ITEM-1302-G"
ITEM_H = "ITEM-1302-H"
ITEM_I = "ITEM-1302-I"
SITE_A_FROM = "154"
SITE_A_TO = "159"
SITE_B_FROM = "154"
SITE_B_TO = "161"
SITE_D_FROM = "161"
SITE_D_TO = "162"
SITE_E_FROM = "163"
SITE_E_TO = "164"
SITE_G_FROM = "165"
SITE_G_TO = "166"
SITE_I_FROM = "167"
SITE_I_TO = "168"
ORIGIN_A = f"AIERP transfer {AS_OF.isoformat()} site {SITE_A_FROM} to {SITE_A_TO}"
ORIGIN_B = f"AIERP transfer {AS_OF.isoformat()} site {SITE_B_FROM} to {SITE_B_TO}"
ORIGIN_I = f"AIERP transfer {AS_OF.isoformat()} site {SITE_I_FROM} to {SITE_I_TO}"

# The documents' known values: A 120 x 2.5 = 300.00, B 150 x 3.0 =
# 450.00, the witness I 100 x 6.0 = 600.00, E 10 x 0.0 = 0.00,
# F 20 x 5.0 = 100.00, G 1 x -1.0 = -1.00, H 50 x 2.0 = 100.00.
VALUE_A = "300.00"
VALUE_B = "450.00"
VALUE_F = "100.00"
VALUE_H = "100.00"
VALUE_I = "600.00"


def _fresh(t: datetime) -> datetime:
    """One hour before ``t`` — within every grant's data-age hours."""
    return t - timedelta(hours=1)


def _entry(item_code: str) -> dict:
    """One requested transfer release entry: the item and the pair
    it suggests on — never a quantity."""
    return {
        "item_code": item_code,
        "from_site": SITE_A_FROM,
        "to_site": SITE_A_TO,
    }


def _wire_order(
    item_code: str, from_site: str, to_site: str, origin: str, quantity: int
) -> dict:
    """One transfer order in the wire format the confirm sends to
    Acumatica."""
    return {
        "from_site_id": from_site,
        "to_site_id": to_site,
        "origin": origin,
        "lines": [{"item_code": item_code, "quantity": quantity}],
    }


ORDER_A = _wire_order(ITEM_A, SITE_A_FROM, SITE_A_TO, ORIGIN_A, 120)
ORDER_B = _wire_order(ITEM_B, SITE_B_FROM, SITE_B_TO, ORIGIN_B, 150)
ORDER_I = _wire_order(ITEM_I, SITE_I_FROM, SITE_I_TO, ORIGIN_I, 100)


def _rec(
    item_code,
    site_code,
    *,
    rop,
    on_hand,
    on_order,
    oq_units,
    unit_cost,
):
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


def _recommendations(items: tuple[str, ...] | None = None) -> list[dict]:
    """The canned rows: every document the run reads — narrowed to
    ``items`` when a later pass holds only part of the data (the
    surplus of a document already moved out of it)."""
    rows = [
        # Item A (acumatica): site 159 needs 120 (ROP 400, position
        # 280) against a surplus of 300 at site 154 (position 700
        # beyond ROP 300 + order quantity 100) — the move of 120 at
        # 2.5, value 300.00.
        _rec(
            ITEM_A,
            SITE_A_TO,
            rop=400,
            on_hand=200,
            on_order=80,
            oq_units=100,
            unit_cost=2.5,
        ),
        _rec(
            ITEM_A,
            SITE_A_FROM,
            rop=300,
            on_hand=700,
            on_order=0,
            oq_units=100,
            unit_cost=2.5,
        ),
        # Item B (acumatica): site 161 needs 150 (ROP 250, position
        # 100) against a surplus of 250 at site 154 (position 400
        # beyond ROP 100 + order quantity 50) — the move of 150 at
        # 3.0, value 450.00.
        _rec(
            ITEM_B,
            SITE_B_TO,
            rop=250,
            on_hand=100,
            on_order=0,
            oq_units=50,
            unit_cost=3.0,
        ),
        _rec(
            ITEM_B,
            SITE_B_FROM,
            rop=100,
            on_hand=400,
            on_order=0,
            oq_units=50,
            unit_cost=3.0,
        ),
        # Item C (odoo): a need of 30 against a surplus of 50 — the
        # Odoo suggestion, never released here.
        _rec(
            ITEM_C,
            SITE_A_TO,
            rop=50,
            on_hand=20,
            on_order=0,
            oq_units=10,
            unit_cost=2.0,
        ),
        _rec(
            ITEM_C,
            SITE_A_FROM,
            rop=20,
            on_hand=80,
            on_order=0,
            oq_units=10,
            unit_cost=2.0,
        ),
        # Item D (acumatica): a need of 40 against a surplus of 40,
        # the receiving site without a unit cost — the valueless
        # suggestion.
        _rec(
            ITEM_D,
            SITE_D_TO,
            rop=40,
            on_hand=0,
            on_order=0,
            oq_units=20,
            unit_cost=None,
        ),
        _rec(
            ITEM_D,
            SITE_D_FROM,
            rop=10,
            on_hand=70,
            on_order=0,
            oq_units=20,
            unit_cost=None,
        ),
        # Item E (acumatica): a need of 30 against a surplus of 10 —
        # the 0-valued suggestion of the document that also holds F
        # (the move of 10 at 0.0, value 0.00).
        _rec(
            ITEM_E,
            SITE_E_TO,
            rop=30,
            on_hand=0,
            on_order=0,
            oq_units=10,
            unit_cost=0.0,
        ),
        _rec(
            ITEM_E,
            SITE_E_FROM,
            rop=40,
            on_hand=70,
            on_order=0,
            oq_units=20,
            unit_cost=0.0,
        ),
        # Item F (acumatica): a need of 100 against a surplus of 20
        # — the suggestion beside E (the move of 20 at 5.0, value
        # 100.00).
        _rec(
            ITEM_F,
            SITE_E_TO,
            rop=100,
            on_hand=0,
            on_order=0,
            oq_units=20,
            unit_cost=5.0,
        ),
        _rec(
            ITEM_F,
            SITE_E_FROM,
            rop=50,
            on_hand=90,
            on_order=0,
            oq_units=20,
            unit_cost=5.0,
        ),
        # Item G (acumatica): a need of 5 against a surplus of 1 —
        # the -1-valued suggestion of the document that also holds H
        # (the move of 1 at -1.0, value -1.00).
        _rec(
            ITEM_G,
            SITE_G_TO,
            rop=5,
            on_hand=0,
            on_order=0,
            oq_units=20,
            unit_cost=-1.0,
        ),
        _rec(
            ITEM_G,
            SITE_G_FROM,
            rop=10,
            on_hand=31,
            on_order=0,
            oq_units=20,
            unit_cost=-1.0,
        ),
        # Item H (acumatica): a need of 80 against a surplus of 50 —
        # the suggestion beside G (the move of 50 at 2.0, value
        # 100.00).
        _rec(
            ITEM_H,
            SITE_G_TO,
            rop=80,
            on_hand=0,
            on_order=0,
            oq_units=50,
            unit_cost=2.0,
        ),
        _rec(
            ITEM_H,
            SITE_G_FROM,
            rop=20,
            on_hand=120,
            on_order=0,
            oq_units=50,
            unit_cost=2.0,
        ),
        # Item I (acumatica): a need of 100 against a surplus of 100
        # — the witness document (the move of 100 at 6.0, value
        # 600.00).
        _rec(
            ITEM_I,
            SITE_I_TO,
            rop=100,
            on_hand=0,
            on_order=0,
            oq_units=20,
            unit_cost=6.0,
        ),
        _rec(
            ITEM_I,
            SITE_I_FROM,
            rop=30,
            on_hand=150,
            on_order=0,
            oq_units=20,
            unit_cost=6.0,
        ),
    ]
    if items is None:
        return rows
    return [row for row in rows if row["item_code"] in items]


class FakeRecommendations:
    """The fake for the route module's ``recommendations_for_all``:
    the canned rows.  ``calls`` counts the reads (the run reads the
    suggestions exactly once per plan) and ``rows`` is mutable (the
    later passes hold only the part of the data their day holds)."""

    def __init__(self, rows: list) -> None:
        self.rows = rows
        self.calls = 0
        self.raise_error = None

    def __call__(self, as_of=None):
        self.calls += 1
        if self.raise_error is not None:
            raise self.raise_error
        return self.rows


def _seed_builder() -> None:
    """The tables the suggestions read: the demand event (the
    as-of) and the item-sites the builder maps to source systems."""
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO demand_events (id, business_key, item_code, "
            "site_code, quantity, occurred_on, event_type, source_system, "
            "synced_at) VALUES (%s, %s, %s, %s, %s, %s, %s, 'acumatica', %s)",
            (
                "1302-demand-1",
                "1302-demand-1",
                ITEM_A,
                SITE_A_FROM,
                30.0,
                AS_OF,
                "shipment",
                _fresh(NOW),
            ),
        )
    ItemSitePostgresRepository().upsert_many(
        [
            # The two valued Acumatica pairs, the Odoo pair, the
            # valueless Acumatica pair, the 0-valued and the
            # -1-valued documents and the witness.
            {
                "item_code": ITEM_A,
                "site_code": SITE_A_FROM,
                "source_system": "acumatica",
            },
            {"item_code": ITEM_A, "site_code": SITE_A_TO, "source_system": "acumatica"},
            {
                "item_code": ITEM_B,
                "site_code": SITE_B_FROM,
                "source_system": "acumatica",
            },
            {"item_code": ITEM_B, "site_code": SITE_B_TO, "source_system": "acumatica"},
            {"item_code": ITEM_C, "site_code": SITE_A_FROM, "source_system": "odoo"},
            {"item_code": ITEM_C, "site_code": SITE_A_TO, "source_system": "odoo"},
            {
                "item_code": ITEM_D,
                "site_code": SITE_D_FROM,
                "source_system": "acumatica",
            },
            {"item_code": ITEM_D, "site_code": SITE_D_TO, "source_system": "acumatica"},
            {
                "item_code": ITEM_E,
                "site_code": SITE_E_FROM,
                "source_system": "acumatica",
            },
            {"item_code": ITEM_E, "site_code": SITE_E_TO, "source_system": "acumatica"},
            {
                "item_code": ITEM_F,
                "site_code": SITE_E_FROM,
                "source_system": "acumatica",
            },
            {"item_code": ITEM_F, "site_code": SITE_E_TO, "source_system": "acumatica"},
            {
                "item_code": ITEM_G,
                "site_code": SITE_G_FROM,
                "source_system": "acumatica",
            },
            {"item_code": ITEM_G, "site_code": SITE_G_TO, "source_system": "acumatica"},
            {
                "item_code": ITEM_H,
                "site_code": SITE_G_FROM,
                "source_system": "acumatica",
            },
            {"item_code": ITEM_H, "site_code": SITE_G_TO, "source_system": "acumatica"},
            {
                "item_code": ITEM_I,
                "site_code": SITE_I_FROM,
                "source_system": "acumatica",
            },
            {"item_code": ITEM_I, "site_code": SITE_I_TO, "source_system": "acumatica"},
        ]
    )


def _feed_row(table: str, synced_at: datetime) -> tuple[str, tuple]:
    """The one Acumatica row of each of the other feed tables."""
    if table == "on_hand":
        return (
            "INSERT INTO on_hand (id, item_code, site_code, "
            "quantity_on_hand, as_of, source_system, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, 'acumatica', %s)",
            ("1302-onhand-1", ITEM_A, SITE_A_FROM, 50.0, NOW, synced_at),
        )
    if table == "open_purchase_lines":
        return (
            "INSERT INTO open_purchase_lines (id, business_key, item_code, "
            "site_code, supplier_code, order_type, open_units, promised_on, "
            "ordered_on, as_of, source_system, synced_at) "
            "VALUES (%s, %s, %s, %s, %s, 'purchase', %s, %s, %s, %s, "
            "'acumatica', %s)",
            (
                "1302-opl-1",
                "1302-opl-1",
                ITEM_B,
                SITE_B_FROM,
                "S-1302-1",
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
            "1302-lto-1",
            "1302-lto-1",
            "S-1302-1",
            ITEM_A,
            SITE_A_FROM,
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


def _transfer_release_rows() -> list[dict]:
    """Every stored transfer_releases row, oldest first, every column."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM transfer_releases ORDER BY id")
            columns = [c[0] for c in cur.description]
            return [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _grant_body(process: str, max_document_value, max_daily_value) -> dict:
    return {
        "process": process,
        "max_document_value": max_document_value,
        "max_daily_value": max_daily_value,
        "max_data_age_hours": 24,
    }


def test_issue1302_freeform(monkeypatch, client) -> None:
    # Isolate the Acumatica environment, complete configuration,
    # writes on.
    for var in ACUMATICA_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("ACUMATICA_BASE_URL", "https://acumatica.example.com/Instance")
    monkeypatch.setenv("ACUMATICA_TENANT", "AIERP")
    monkeypatch.setenv("ACUMATICA_USERNAME", "svc-aierp")
    monkeypatch.setenv("ACUMATICA_PASSWORD", "s3cret-password!")
    monkeypatch.setenv("ACUMATICA_ALLOW_WRITES", "1")

    # --- Seed the tables the suggestions read, plus the other feed
    # tables the run checks for freshness.
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

    def fake_preview(self, transfers):
        preview_calls.append([dict(entry) for entry in transfers])
        if not transfers:
            return []
        return [
            {
                "origin": entry["origin"],
                "outcome": "would_create",
                "reference_nbr": None,
                "status": None,
                "from_warehouse_code": "WHOLESALE",
                "to_warehouse_code": "DISTRIBUTION",
                "line_count": len(entry["lines"]),
                "error": None,
            }
            for entry in transfers
        ]

    def fake_create(self, transfers):
        if not _writes_enabled():
            raise AcumaticaWritesDisabledError(
                "Acumatica writes are disabled; set ACUMATICA_ALLOW_WRITES "
                "to a truthy value to enable transfer order creation"
            )
        if not transfers:
            return []
        create_calls.append([dict(entry) for entry in transfers])
        return [
            {
                "origin": entry["origin"],
                "outcome": "created",
                "reference_nbr": f"TR-1302-{len(create_calls):03d}",
                "status": "On Hold",
                "from_warehouse_code": "WHOLESALE",
                "to_warehouse_code": "DISTRIBUTION",
                "line_count": len(entry["lines"]),
                "error": None,
            }
            for entry in transfers
        ]

    monkeypatch.setattr(AcumaticaClient, "preview_transfer_orders", fake_preview)
    monkeypatch.setattr(AcumaticaClient, "create_transfer_orders", fake_create)

    # --- The builder's source, replaced: the canned rows, the calls
    # counted.  The run's as-of is the demand event's data date, the
    # one a person's confirm resolves.
    fake_recommendations = FakeRecommendations(_recommendations())
    monkeypatch.setattr(supply_plan, "recommendations_for_all", fake_recommendations)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    runs_repo = StandingGrantRunPostgresRepository()

    # -- Nothing happens: no grant in force answers no_grant, and
    #    nothing is read beyond that or written. --------------------
    assert standing_release.run_transfer_grant(now=NOW) == {"outcome": "no_grant"}
    assert _run_rows() == []
    assert _transfer_release_rows() == []
    assert preview_calls == []
    assert create_calls == []
    assert fake_recommendations.calls == 0

    # The grants, through the #1173 routes: the order-plan grant the
    # day's-allowance check records its run under, and the transfer's
    # — one at a time, a new one revoking the old.
    created = admin.post(
        "/api/standing-grants",
        json=_grant_body("order_plan_release", 800, 100000),
    )
    assert created.status_code == 201, created.text
    order_plan_grant_id = created.json()["id"]
    assert created.json()["in_force"] is True
    created = admin.post(
        "/api/standing-grants", json=_grant_body("transfer_release", 400, 1000)
    )
    assert created.status_code == 201, created.text
    grant_1_id = created.json()["id"]
    assert created.json()["in_force"] is True

    # -- A value of 0 or below: left out with no_value, never sent.
    # The document limit lies between the witness and the two valued
    # documents, so the sent document is the 300.00 one alone.
    _feed_freshness(_fresh(NOW))
    row = standing_release.run_transfer_grant(now=NOW)
    assert row["id"] is not None
    assert row["process"] == "transfer_release"
    assert row["grant_id"] == grant_1_id
    assert row["started_at"] == NOW.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 1
    assert row["value"] == VALUE_A
    assert row["left_out"] == [
        {
            "from_site": SITE_I_FROM,
            "to_site": SITE_I_TO,
            "value": VALUE_I,
            "reason": "document_limit",
        },
        {
            "from_site": SITE_B_FROM,
            "to_site": SITE_B_TO,
            "value": VALUE_B,
            "reason": "document_limit",
        },
        {
            "from_site": SITE_E_FROM,
            "to_site": SITE_E_TO,
            "value": VALUE_F,
            "reason": "no_value",
        },
        {
            "from_site": SITE_G_FROM,
            "to_site": SITE_G_TO,
            "value": VALUE_H,
            "reason": "no_value",
        },
        {
            "from_site": SITE_D_FROM,
            "to_site": SITE_D_TO,
            "value": "0",
            "reason": "no_value",
        },
    ]
    # The 0-valued document's left-out value is the summed value of
    # its suggestions that are above 0 — the 0 itself excluded from
    # the sum — and the -1-valued document's too, the -1 excluded.
    zero_entry = next(e for e in row["left_out"] if e["from_site"] == SITE_E_FROM)
    assert zero_entry["to_site"] == SITE_E_TO
    assert zero_entry["reason"] == "no_value"
    assert zero_entry["value"] == VALUE_F
    negative_entry = next(e for e in row["left_out"] if e["from_site"] == SITE_G_FROM)
    assert negative_entry["to_site"] == SITE_G_TO
    assert negative_entry["reason"] == "no_value"
    assert negative_entry["value"] == VALUE_H
    # The sent document only: the 0-valued, the -1-valued, the
    # valueless and the Odoo suggestion are never sent.
    assert create_calls == [[ORDER_A]]
    assert ITEM_E not in str(create_calls)
    assert ITEM_G not in str(create_calls)
    assert ITEM_D not in str(create_calls)
    assert ITEM_C not in str(create_calls)
    # The run row: the sent document's value, and its
    # transfer_releases row: the grant as actor, a null actor name.
    rows = _run_rows()
    assert len(rows) == 1
    assert rows[0]["value"] == Decimal(VALUE_A)
    releases = _transfer_release_rows()
    assert len(releases) == 1
    release_row = releases[0]
    assert release_row["actor"] == f"standing-grant:{grant_1_id}"
    assert release_row["actor_name"] is None
    assert release_row["outcome"] == "created"
    assert release_row["origin"] == ORIGIN_A
    assert release_row["from_site_code"] == SITE_A_FROM
    assert release_row["to_site_code"] == SITE_A_TO
    assert release_row["lines"] == [{"item_code": ITEM_A, "quantity": 120}]

    # -- A value of 0 or below: the day's allowance after the run is
    # reduced by the sent document's value only.  The witness
    # document, 600.00, fits what the day still holds — 1000 - 300
    # = 700 — but not the 500 the two left-out documents' 100.00
    # each would leave if their values counted.
    revoked = admin.post(f"/api/standing-grants/{grant_1_id}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["in_force"] is False
    created = admin.post(
        "/api/standing-grants", json=_grant_body("transfer_release", 1000, 1000)
    )
    assert created.status_code == 201, created.text
    grant_2_id = created.json()["id"]
    assert created.json()["in_force"] is True
    fake_recommendations.rows = _recommendations((ITEM_I,))
    row = standing_release.run_transfer_grant(now=NOW)
    assert row["grant_id"] == grant_2_id
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 1
    assert row["value"] == VALUE_I
    assert row["left_out"] == []
    assert create_calls[-1] == [ORDER_I]
    assert len(_run_rows()) == 2
    assert len(_transfer_release_rows()) == 2

    # -- Only transfer runs count toward the day: an
    #    order_plan_release run recorded on the same UTC date as the
    #    later transfer run, of a value large enough that, counted,
    #    it would leave the day 1000 - 99999.99 — past which no
    #    document fits.
    runs_repo.record(
        process="order_plan_release",
        grant_id=order_plan_grant_id,
        started_at=ORDER_PLAN_RUN_AT,
        outcome="released",
        reason=None,
        documents=1,
        value=Decimal("99999.99"),
        left_out=[
            {
                "site_code": SITE_A_FROM,
                "supplier_code": "S-1302-1",
                "value": "1234.56",
                "reason": "daily_limit",
            }
        ],
    )
    fake_recommendations.rows = _recommendations((ITEM_B,))
    _feed_freshness(_fresh(DAY2))
    row = standing_release.run_transfer_grant(now=DAY2)
    assert row["grant_id"] == grant_2_id
    assert row["started_at"] == DAY2.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 1
    assert row["value"] == VALUE_B
    assert row["left_out"] == []
    assert create_calls[-1] == [ORDER_B]
    assert len(_run_rows()) == 4  # the recorded order-plan run among them
    assert len(_transfer_release_rows()) == 3
    assert len(create_calls) == 3

    # -- The day turns at UTC midnight: the grant fits one of the
    #    two documents but not two — 700 holds 450.00 or 300.00, not
    #    the 750.00 of both.  The 23:59 UTC run sends the larger.
    revoked = admin.post(f"/api/standing-grants/{grant_2_id}/revoke")
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["in_force"] is False
    created = admin.post(
        "/api/standing-grants", json=_grant_body("transfer_release", 700, 700)
    )
    assert created.status_code == 201, created.text
    grant_3_id = created.json()["id"]
    assert created.json()["in_force"] is True
    fake_recommendations.rows = _recommendations((ITEM_A, ITEM_B))
    _feed_freshness(_fresh(DAY3))
    row = standing_release.run_transfer_grant(now=DAY3)
    assert row["grant_id"] == grant_3_id
    assert row["started_at"] == DAY3.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 1
    assert row["value"] == VALUE_B
    assert row["left_out"] == [
        {
            "from_site": SITE_A_FROM,
            "to_site": SITE_A_TO,
            "value": VALUE_A,
            "reason": "daily_limit",
        },
    ]
    assert create_calls[-1] == [ORDER_B]

    # The other document at 00:01 UTC the next date: the day turns
    # at UTC midnight.  A rolling 24-hour window from the 23:59 run
    # would hold both documents' 750.00 against the day's 700 and
    # refuse this one two minutes later — the new UTC date starts
    # the allowance whole instead.  The data no longer suggests the
    # moved document: its surplus went with the order just created.
    fake_recommendations.rows = _recommendations((ITEM_A,))
    _feed_freshness(_fresh(DAY4))
    row = standing_release.run_transfer_grant(now=DAY4)
    assert row["grant_id"] == grant_3_id
    assert row["started_at"] == DAY4.isoformat()
    assert row["outcome"] == "released"
    assert row["reason"] is None
    assert row["documents"] == 1
    assert row["value"] == VALUE_A
    assert row["left_out"] == []
    assert create_calls[-1] == [ORDER_A]
    assert len(_run_rows()) == 6
    assert len(_transfer_release_rows()) == 5

    # -- A person's confirm keeps the display name: the admin's
    #    confirm writes transfer_releases rows whose actor_name is
    # the identity's display name.
    confirm = admin.post(
        "/api/supply-plan/transfers/release",
        json={"transfers": [_entry(ITEM_A)], "confirm": True},
    )
    assert confirm.status_code == 200, confirm.text
    plan_body = confirm.json()
    assert plan_body["as_of"] == AS_OF.isoformat()
    assert plan_body["groups"][0]["origin"] == ORIGIN_A
    assert plan_body["groups"][0]["outcome"] == "created"
    assert plan_body["groups"][0]["lines"] == [{"item_code": ITEM_A, "quantity": 120}]
    assert plan_body["unreleasable"] == []
    releases = _transfer_release_rows()
    assert len(releases) == 6
    person_row = releases[-1]
    assert person_row["actor"] == ADMIN_DISPLAY_NAME
    # The display name the identity carries — the grant's runs above
    # wrote a null actor_name, the person's confirm its identity's.
    assert person_row["actor_name"] == ADMIN_DISPLAY_NAME
    assert person_row["outcome"] == "created"
    assert person_row["origin"] == ORIGIN_A
    assert person_row["from_site_code"] == SITE_A_FROM
    assert person_row["to_site_code"] == SITE_A_TO
    assert person_row["lines"] == [{"item_code": ITEM_A, "quantity": 120}]
    assert create_calls[-1] == [ORDER_A]

    # Every send, in order — the witness only in its own run, the
    # 0-valued, the -1-valued, the valueless and the Odoo
    # suggestions never.
    assert create_calls == [
        [ORDER_A],
        [ORDER_I],
        [ORDER_B],
        [ORDER_B],
        [ORDER_A],
        [ORDER_A],
    ]
    for never in (ITEM_E, ITEM_G, ITEM_D, ITEM_C):
        assert never not in str(create_calls)
    assert preview_calls == []
    assert len(_run_rows()) == 6
    assert fake_recommendations.calls == 6
