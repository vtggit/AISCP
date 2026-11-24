"""Issue #1587 proof: the parameter_writebacks.basis column, the
audit that stores it, and the record that counts by it.

Proves, against the live app and the migrated test database:

* the migration — the revision after the parent
  ``0056_short_order_releases`` is the only revision that follows
  it, the database has one head, equal to ``CURRENT_HEAD`` from
  ``tests/migration_head.py``, and is upgraded to it;
  ``parameter_writebacks.basis`` is String(16) and NOT NULL, and the
  check constraint ``ck_parameter_writebacks_basis`` refuses the
  value "other";
* the backfill — downgraded to the parent revision, one audit row
  with a ``decision_id`` and one without are inserted; upgraded back
  to the head, their ``basis`` is then "decision" and "ddmrp";
* the audit — the data is seeded the way test 762 seeds it (the
  item-sites, the decisions, the demand and lead times, the buffer
  designation), with the route module's two client classes replaced
  by fakes: confirming one decision row and one buffer row stores
  ``basis`` "decision" and "ddmrp", a writer that raises
  ``AcumaticaError`` stores a failed row with its row's basis, every
  audit row is read back field for field, the confirm passes the
  preview row's basis explicitly on every audit row it stores
  (written, unchanged and failed alike — never the repository
  default), the preview and the history answers keep their shapes,
  and a repository insert without a basis stores "decision" with a
  ``decision_id``, "ddmrp" without, and a given basis exactly as
  given;
* the record — a "decision" push and a "ddmrp" push, each read back
  unchanged, count once each, as before, and a row inserted with
  ``basis`` "excess" and a ``decision_id`` is counted in neither
  kind: the basis decides the kind, not the decision id.
"""

import os
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import psycopg2
import psycopg2.extras
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.parameter_writeback as parameter_writeback
from app.db.connection import get_connection
from app.feed.acumatica_client import AcumaticaError
from app.main import app
from app.repositories.parameter_writebacks_postgres_repository import (
    ParameterWritebacksPostgresRepository,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

# The parent revision: exactly one revision follows it.
PREV_REVISION = "0056_short_order_releases"

SITE = "SITE-1587"
ADMIN_TOKEN = "dev-secret-token:admin"
DECISION_AS_OF = date(2026, 11, 1)

PREVIEW_URL = "/api/parameter-writeback/preview"
CONFIRM_URL = "/api/parameter-writeback/confirm"
HISTORY_URL = "/api/parameter-writeback/history"
RECORD_URL = "/api/parameter-writeback/record"
ZONES_URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"

# item_code -> (item_sites id, reorder_point, max_qty, order_qty,
# source_system).  D and F are the two accepted decision rows (F's
# writer raises); B is the designated DDMRP buffer row; E carries
# only the directly inserted "excess" push.
ITEMS = {
    "ITEM-1587-D": ("is-1587-d", 25.0, 60.0, 20.0, "acumatica"),
    "ITEM-1587-F": ("is-1587-f", 25.0, 60.0, 20.0, "acumatica"),
    "ITEM-1587-B": ("is-1587-b", 25.0, 60.0, 20.0, "acumatica"),
    "ITEM-1587-E": ("is-1587-e", 25.0, 60.0, 20.0, "acumatica"),
}

# The two backfill rows of the migration proof: item_code -> the
# decision_id the row carries (None on the other one).  Both are
# outcome "failed", so they never count in the record.
BACKFILL_ROWS = {
    "BF-1587-1": "D-1587-BF1",
    "BF-1587-2": None,
}

# The excess push's after values and its explicit times (the
# item-site's synced_at is later than the push's written_at, so a
# misclassification would also show up in read and unchanged).
EXCESS_AFTER = (30.0, 70.0)
EXCESS_WRITTEN_AT = datetime(2026, 11, 10, 10, 0, tzinfo=UTC)
EXCESS_SYNCED_AT = datetime(2026, 11, 10, 11, 0, tzinfo=UTC)

# The exact key sets of the stored audit rows, the history answer
# (which gains no basis field) and the preview rows (unchanged).
AUDIT_KEYS = {
    "id",
    "item_code",
    "site_code",
    "source_system",
    "decision_id",
    "basis",
    "before_reorder_point",
    "before_max_qty",
    "after_reorder_point",
    "after_max_qty",
    "requested_reorder_point",
    "requested_max_qty",
    "outcome",
    "error",
    "written_by",
    "written_by_name",
    "written_at",
}
HISTORY_KEYS = AUDIT_KEYS - {"basis"}
PREVIEW_KEYS = {
    "item_code",
    "site_code",
    "decision_id",
    "decided_at",
    "money_at_stake",
    "source_system",
    "current_reorder_point",
    "current_max_qty",
    "current_order_qty",
    "proposed_reorder_point",
    "proposed_max_qty",
    "writable",
    "blocked_reason",
    "basis",
}

# The record once the decision push and the buffer push are read
# back unchanged — and the same answer after the excess push: the
# excess row is counted in neither kind.
EXPECTED_RECORD = {
    "reorder_points": {"pushed": 1, "read": 1, "unchanged": 1, "rate": 1.0},
    "buffers": {"pushed": 1, "read": 1, "unchanged": 1, "rate": 1.0},
}


class FakeAcumaticaClient:
    """The fake for the route module's AcumaticaClient: records every
    call, answers "written" with the seeded item_sites values as the
    ERP's before values and the requested values as the read-back,
    and raises ``AcumaticaError`` for the items of ``fail_for``."""

    calls: list[dict] = []
    fail_for: set[str] = set()

    def write_item_warehouse_parameters(
        self, *, item_code: str, site_code: str, reorder_point: float, max_qty: float
    ) -> dict:
        FakeAcumaticaClient.calls.append(
            {
                "item_code": item_code,
                "site_code": site_code,
                "reorder_point": reorder_point,
                "max_qty": max_qty,
            }
        )
        if item_code in FakeAcumaticaClient.fail_for:
            raise AcumaticaError("the ERP refused the write")
        reorder, max_value, _ = ITEMS[item_code][1:4]
        return {
            "outcome": "written",
            "before": {"reorder_point": reorder, "max_qty": max_value},
            "after": {"reorder_point": reorder_point, "max_qty": max_qty},
        }


class FakeOdooClient:
    """The fake for the route module's OdooClient: every call fails
    the proof (the proof confirms no Odoo pair)."""

    def write_reorder_rule(
        self, *, item_code: str, site_code: str, reorder_point: float, max_qty: float
    ) -> dict:
        raise AssertionError(f"the Odoo writer was called for {item_code}")


def _execute(sql: str, params: tuple = ()) -> list:
    """Run one statement and return its rows (no parameters for a DML)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall() if cur.description is not None else []
        conn.commit()
        return [tuple(row) for row in rows]
    finally:
        conn.close()


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND_ROOT, "migrations"))
    return cfg


def _alembic_revision() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _basis_column() -> tuple | None:
    """(data_type, max length, is_nullable) of parameter_writebacks.basis."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT data_type, character_maximum_length, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'parameter_writebacks' "
                "AND column_name = 'basis'"
            )
            row = cur.fetchone()
    finally:
        conn.close()
    return None if row is None else tuple(row)


def _assert_constraint_refuses_other() -> None:
    """The check constraint refuses a basis other than the three."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO parameter_writebacks "
                "(id, item_code, site_code, source_system, basis, "
                " requested_reorder_point, requested_max_qty, outcome, written_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    str(uuid4()),
                    "ITEM-1587-X",
                    SITE,
                    "acumatica",
                    "other",
                    1.0,
                    2.0,
                    "written",
                    "nobody",
                ),
            )
    except psycopg2.Error as exc:
        assert isinstance(exc, psycopg2.errors.CheckViolation)
        assert "ck_parameter_writebacks_basis" in str(exc)
    else:
        raise AssertionError("basis 'other' was accepted")
    finally:
        conn.rollback()
        conn.close()


def _insert_backfill_row(item_code: str, decision_id: str | None) -> None:
    """One audit row of the backfill proof, inserted raw — the basis
    column the proof proves does not exist yet at the parent
    revision."""
    _execute(
        "INSERT INTO parameter_writebacks "
        "(id, item_code, site_code, source_system, decision_id, "
        " before_reorder_point, before_max_qty, after_reorder_point, "
        " after_max_qty, requested_reorder_point, requested_max_qty, "
        " outcome, error, written_by, written_by_name, written_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s)",
        (
            str(uuid4()),
            item_code,
            SITE,
            "acumatica",
            decision_id,
            None,
            None,
            None,
            None,
            1.0,
            2.0,
            "failed",
            "backfill proof",
            "seeder",
            None,
            datetime(2026, 11, 5, 0, 0, tzinfo=UTC),
        ),
    )


def _audit_rows(item_code: str | None = None) -> list[dict]:
    """The stored audit rows, strictly, oldest first (narrowed to one
    item_code when given)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if item_code is None:
                cur.execute(
                    "SELECT * FROM parameter_writebacks ORDER BY written_at, id"
                )
            else:
                cur.execute(
                    "SELECT * FROM parameter_writebacks WHERE item_code = %s "
                    "ORDER BY written_at, id",
                    (item_code,),
                )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _assert_audit_row(
    row: dict,
    *,
    decision_id: str | None,
    basis: str,
    before: tuple[float | None, float | None],
    after: tuple[float | None, float | None],
    requested: tuple[float, float],
    outcome: str,
    error: str | None,
) -> None:
    """Every field of one stored audit row, read strictly."""
    assert set(row.keys()) == AUDIT_KEYS
    UUID(row["id"])  # a uuid id
    assert isinstance(row["written_at"], datetime)
    assert row["written_at"].tzinfo is not None
    assert row["site_code"] == SITE
    assert row["source_system"] == "acumatica"
    assert row["decision_id"] == decision_id
    assert row["basis"] == basis
    assert row["before_reorder_point"] == before[0]
    assert row["before_max_qty"] == before[1]
    assert row["after_reorder_point"] == after[0]
    assert row["after_max_qty"] == after[1]
    assert row["requested_reorder_point"] == requested[0]
    assert row["requested_max_qty"] == requested[1]
    assert row["outcome"] == outcome
    assert row["error"] == error
    assert row["written_by"] == "dev-admin-1"
    assert row["written_by_name"] == "admin"


def _clear_setup() -> None:
    """The proof's setup: delete the proof's own item-sites,
    decisions and buffer rows, so it starts from its own picture."""
    _execute(
        "DELETE FROM item_sites WHERE (item_code, site_code) IN "
        "(SELECT * FROM UNNEST(%s::text[], %s::text[]))",
        (list(ITEMS), [SITE] * len(ITEMS)),
    )
    _execute(
        "DELETE FROM planner_decisions WHERE id IN (%s, %s)",
        ("pd-1587-d", "pd-1587-f"),
    )
    _execute("DELETE FROM ddmrp_buffers WHERE item_code = %s", ("ITEM-1587-B",))
    _execute("DELETE FROM demand_events WHERE item_code = %s", ("ITEM-1587-B",))
    _execute(
        "DELETE FROM lead_time_observations WHERE item_code = %s",
        ("ITEM-1587-B",),
    )


def _seed_item_sites_and_decisions() -> None:
    """Seed item_sites and planner_decisions directly (the way test
    762 seeds them)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item_code, (
                site_id,
                reorder,
                max_qty,
                order_qty,
                source,
            ) in ITEMS.items():
                cur.execute(
                    "INSERT INTO item_sites "
                    "(id, item_code, site_code, unit_cost, reorder_point, "
                    " max_qty, order_qty, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        site_id,
                        item_code,
                        SITE,
                        1.0,
                        reorder,
                        max_qty,
                        order_qty,
                        source,
                    ),
                )
            for decision_id, item_code, money in (
                ("pd-1587-d", "ITEM-1587-D", 2000.0),
                ("pd-1587-f", "ITEM-1587-F", 1000.0),
            ):
                cur.execute(
                    "INSERT INTO planner_decisions "
                    "(id, item_code, site_code, decision, reason, snooze_until, "
                    " as_of, recommended_reorder_point_units, current_reorder_point, "
                    " money_at_stake, decided_by, decided_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        decision_id,
                        item_code,
                        SITE,
                        "accepted",
                        None,
                        None,
                        DECISION_AS_OF,
                        55,
                        25.0,
                        money,
                        "seeder",
                        "2026-11-01T09:00:00+00",
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _demand_events(cur, item: str, site: str, daily: dict[date, float]) -> None:
    """One demand event per day of ``daily`` (the way test 762 seeds
    its demand events)."""
    cur.executemany(
        "INSERT INTO demand_events (id, business_key, item_code, site_code, "
        "quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                f"id-de-1587-{item}-{day.isoformat()}",
                f"de-1587-{item}-{day.isoformat()}",
                item,
                site,
                quantity,
                day.isoformat(),
                "shipment",
                "test",
            )
            for day, quantity in sorted(daily.items(), reverse=True)
        ],
    )


def _lead_times(cur, item: str, received: date) -> None:
    """Three lead-time observations of 8, 10 and 12 days: pooled mean
    10 (the way test 762 seeds its lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-1587-{item}-{i}",
                f"lt-1587-{item}-{i}",
                f"SUP-1587-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def test_issue1587_freeform(client, monkeypatch):
    # The proof starts from its own picture of the audit.
    _execute("DELETE FROM parameter_writebacks")

    # === The migration: one head, one follower of the parent, the
    #     column and its constraint. ====================================
    script = ScriptDirectory(_MIGRATIONS_DIR)
    heads = script.get_heads()
    assert len(heads) == 1
    assert list(heads) == [CURRENT_HEAD]
    # Exactly one revision follows the parent revision.
    followers = [
        revision
        for revision in script.walk_revisions()
        if revision.down_revision == PREV_REVISION
    ]
    assert len(followers) == 1

    cfg = _alembic_config()
    alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD

    # The column: String(16), NOT NULL.
    assert _basis_column() == ("character varying", 16, "NO")
    # The constraint refuses the value "other".
    _assert_constraint_refuses_other()

    # The backfill: downgraded to the parent revision the column is
    # gone; one row with a decision_id and one without are inserted;
    # the upgrade back to the head backfills their basis.
    try:
        alembic_command.downgrade(cfg, PREV_REVISION)
        assert _alembic_revision() == PREV_REVISION
        assert _basis_column() is None  # the downgrade dropped the column
        for item_code, decision_id in BACKFILL_ROWS.items():
            _insert_backfill_row(item_code, decision_id)
    finally:
        alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == CURRENT_HEAD
    assert {
        row["item_code"]: row["basis"]
        for row in _audit_rows()
        if row["item_code"] in BACKFILL_ROWS
    } == {
        "BF-1587-1": "decision",
        "BF-1587-2": "ddmrp",
    }

    # === The audit and the record: seeded the way test 762 seeds it,
    #     the route module's two client classes replaced by fakes. =====
    FakeAcumaticaClient.calls = []
    FakeAcumaticaClient.fail_for = {"ITEM-1587-F"}
    monkeypatch.setattr(parameter_writeback, "AcumaticaClient", FakeAcumaticaClient)
    monkeypatch.setattr(parameter_writeback, "OdooClient", FakeOdooClient)
    # The recommendation inputs: the registry defaults, never ambient
    # environment values (the way test 762 pins them).
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    _clear_setup()
    _seed_item_sites_and_decisions()

    # The as-of date: the demand statistics' default as-of date is the
    # earlier of today and the latest demand event, so the latest
    # event seeded on that day is the as-of date the routes answer
    # as of.
    as_of = date.today()
    daily = {as_of - timedelta(days=offset): 2.0 for offset in range(360)}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand_events(cur, "ITEM-1587-B", SITE, daily)
            # Three 8/10/12-day observations, received 30 days
            # before the as-of date (the way test 762 seeds its
            # lead times).
            _lead_times(cur, "ITEM-1587-B", as_of - timedelta(days=30))
        conn.commit()
    finally:
        conn.close()

    # Designate the buffer the way test 762 designates it (an admin
    # PUT).
    response = admin.put(
        BUFFERS_URL,
        json={"item_code": "ITEM-1587-B", "site_code": SITE, "note": "buffer"},
    )
    assert response.status_code == 200, response.text

    # The known zone of the designated pair, as of the default as-of
    # date (the way test 762 reads it).
    response = client.get(ZONES_URL)
    assert response.status_code == 200, response.text
    zones = {row["item_code"]: row for row in response.json()["rows"]}
    zone = zones["ITEM-1587-B"]
    assert zone["status"] == "ok"
    top_of_yellow = float(zone["top_of_yellow"])
    top_of_green = float(zone["top_of_green"])

    # The preview: the two decision rows and the buffer row, in the
    # preview's unchanged shape.
    response = client.get(PREVIEW_URL)
    assert response.status_code == 200, response.text
    rows = response.json()["rows"]
    for row in rows:
        assert set(row.keys()) == PREVIEW_KEYS
    by_pair = {(row["item_code"], row["site_code"]): row for row in rows}
    assert set(by_pair) == {
        ("ITEM-1587-D", SITE),
        ("ITEM-1587-F", SITE),
        ("ITEM-1587-B", SITE),
    }
    preview_basis = {(row["item_code"], row["site_code"]): row["basis"] for row in rows}
    assert preview_basis == {
        ("ITEM-1587-D", SITE): "decision",
        ("ITEM-1587-F", SITE): "decision",
        ("ITEM-1587-B", SITE): "ddmrp",
    }
    assert by_pair[("ITEM-1587-D", SITE)]["decision_id"] == "pd-1587-d"
    assert by_pair[("ITEM-1587-B", SITE)]["decision_id"] is None

    # The confirm: the preview row's basis is passed explicitly on
    # every audit row the route stores — written, unchanged and
    # failed alike.
    insert_calls: list[dict] = []
    real_insert = parameter_writeback._writebacks.insert

    def recording_insert(**fields):
        insert_calls.append(fields)
        return real_insert(**fields)

    monkeypatch.setattr(parameter_writeback._writebacks, "insert", recording_insert)

    response = admin.post(
        CONFIRM_URL,
        json={
            "items": [
                {"item_code": "ITEM-1587-D", "site_code": SITE},
                {"item_code": "ITEM-1587-B", "site_code": SITE},
                {"item_code": "ITEM-1587-F", "site_code": SITE},
            ]
        },
    )
    assert response.status_code == 200, response.text
    confirm = response.json()
    assert confirm["written"] == 2
    assert confirm["unchanged"] == 0
    assert confirm["failed"] == 1
    assert confirm["skipped"] == 0
    results = {(r["item_code"], r["site_code"]): r for r in confirm["results"]}
    assert results[("ITEM-1587-D", SITE)] == {
        "item_code": "ITEM-1587-D",
        "site_code": SITE,
        "outcome": "written",
        "reason": None,
        "before": {"reorder_point": 25.0, "max_qty": 60.0, "order_qty": 20.0},
        "after": {"reorder_point": 55.0, "max_qty": 75.0, "order_qty": 20.0},
    }
    assert results[("ITEM-1587-B", SITE)] == {
        "item_code": "ITEM-1587-B",
        "site_code": SITE,
        "outcome": "written",
        "reason": None,
        "before": {"reorder_point": 25.0, "max_qty": 60.0, "order_qty": 20.0},
        "after": {
            "reorder_point": top_of_yellow,
            "max_qty": top_of_green,
            "order_qty": top_of_green - top_of_yellow,
        },
    }
    # The failed row answers the stable public reason, and the writer
    # was called for the three pairs in request order.
    assert results[("ITEM-1587-F", SITE)] == {
        "item_code": "ITEM-1587-F",
        "site_code": SITE,
        "outcome": "failed",
        "reason": "writer_error",
        "before": None,
        "after": None,
    }
    assert [
        (call["item_code"], call["reorder_point"], call["max_qty"])
        for call in FakeAcumaticaClient.calls
    ] == [
        ("ITEM-1587-D", 55.0, 75.0),
        ("ITEM-1587-B", top_of_yellow, top_of_green),
        ("ITEM-1587-F", 55.0, 75.0),
    ]

    # Every audit row the confirm stored carries the preview row's
    # basis, passed explicitly — never left to the repository
    # default.
    assert len(insert_calls) == 3
    for call in insert_calls:
        assert "basis" in call  # passed explicitly on every audit row
    assert [(call["item_code"], call["site_code"]) for call in insert_calls] == [
        ("ITEM-1587-D", SITE),
        ("ITEM-1587-B", SITE),
        ("ITEM-1587-F", SITE),
    ]
    assert [call["basis"] for call in insert_calls] == [
        "decision",
        "ddmrp",
        "decision",
    ]
    for call in insert_calls:
        assert call["basis"] == preview_basis[(call["item_code"], call["site_code"])]

    # The stored rows, field for field.
    (audit_d,) = _audit_rows("ITEM-1587-D")
    _assert_audit_row(
        audit_d,
        decision_id="pd-1587-d",
        basis="decision",
        before=(25.0, 60.0),
        after=(55.0, 75.0),
        requested=(55.0, 75.0),
        outcome="written",
        error=None,
    )
    (audit_b,) = _audit_rows("ITEM-1587-B")
    _assert_audit_row(
        audit_b,
        decision_id=None,
        basis="ddmrp",
        before=(25.0, 60.0),
        after=(top_of_yellow, top_of_green),
        requested=(top_of_yellow, top_of_green),
        outcome="written",
        error=None,
    )
    (audit_f,) = _audit_rows("ITEM-1587-F")
    _assert_audit_row(
        audit_f,
        decision_id="pd-1587-f",
        basis="decision",
        before=(None, None),
        after=(None, None),
        requested=(55.0, 75.0),
        outcome="failed",
        error="AcumaticaError: the ERP refused the write",
    )

    # The history answer keeps its shape: no basis field.
    response = client.get(HISTORY_URL, params={"item_code": "ITEM-1587-D"})
    assert response.status_code == 200, response.text
    (history_row,) = response.json()
    assert set(history_row.keys()) == HISTORY_KEYS
    assert history_row["decision_id"] == "pd-1587-d"
    assert history_row["outcome"] == "written"
    assert history_row["error"] is None
    assert history_row["written_by"] == "dev-admin-1"
    assert history_row["written_by_name"] == "admin"

    # The repository default: an insert without a basis stores
    # "decision" with a decision_id and "ddmrp" without; a given
    # basis is stored exactly as given, even when it differs from
    # what the decision_id would imply.
    repo = ParameterWritebacksPostgresRepository()
    with_decision = repo.insert(
        item_code="ITEM-1587-R1",
        site_code=SITE,
        source_system="acumatica",
        decision_id="D-1587-R1",
        before_reorder_point=None,
        before_max_qty=None,
        after_reorder_point=None,
        after_max_qty=None,
        requested_reorder_point=1.0,
        requested_max_qty=2.0,
        outcome="failed",
        error="the repository default proof",
        written_by="dev-user-1",
        written_by_name=None,
        written_at=datetime(2026, 11, 6, 0, 0, tzinfo=UTC),
    )
    assert with_decision["basis"] == "decision"
    without_decision = repo.insert(
        item_code="ITEM-1587-R2",
        site_code=SITE,
        source_system="acumatica",
        decision_id=None,
        before_reorder_point=None,
        before_max_qty=None,
        after_reorder_point=None,
        after_max_qty=None,
        requested_reorder_point=1.0,
        requested_max_qty=2.0,
        outcome="failed",
        error="the repository default proof",
        written_by="dev-user-1",
        written_by_name=None,
        written_at=datetime(2026, 11, 7, 0, 0, tzinfo=UTC),
    )
    assert without_decision["basis"] == "ddmrp"
    explicit = repo.insert(
        item_code="ITEM-1587-R3",
        site_code=SITE,
        source_system="acumatica",
        decision_id="D-1587-R3",
        basis="excess",
        before_reorder_point=None,
        before_max_qty=None,
        after_reorder_point=None,
        after_max_qty=None,
        requested_reorder_point=1.0,
        requested_max_qty=2.0,
        outcome="failed",
        error="the explicit basis proof",
        written_by="dev-user-1",
        written_by_name=None,
        written_at=datetime(2026, 11, 8, 0, 0, tzinfo=UTC),
    )
    assert explicit["basis"] == "excess"

    # The record: the decision push and the ddmrp push, each read
    # back unchanged, count once each, as before.  The item-sites
    # hold the after values and a sync after the pushes.
    _execute(
        "UPDATE item_sites SET synced_at = now() + interval '1 second' "
        "WHERE item_code IN ('ITEM-1587-D', 'ITEM-1587-B') "
        "AND site_code = %s",
        (SITE,),
    )
    response = client.get(RECORD_URL)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_RECORD

    # A push of any other basis is counted in neither kind: the row
    # carries a decision_id (which the old decision_id rule would
    # have counted as a reorder-point push) and its item-site reads
    # it back unchanged — yet the record is unchanged.
    _execute(
        "INSERT INTO parameter_writebacks "
        "(id, item_code, site_code, source_system, decision_id, basis, "
        " before_reorder_point, before_max_qty, after_reorder_point, "
        " after_max_qty, requested_reorder_point, requested_max_qty, "
        " outcome, error, written_by, written_by_name, written_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
        "%s, %s, %s, %s)",
        (
            str(uuid4()),
            "ITEM-1587-E",
            SITE,
            "acumatica",
            "D-1587-E",
            "excess",
            None,
            None,
            EXCESS_AFTER[0],
            EXCESS_AFTER[1],
            EXCESS_AFTER[0],
            EXCESS_AFTER[1],
            "written",
            None,
            "seeder",
            None,
            EXCESS_WRITTEN_AT,
        ),
    )
    _execute(
        "UPDATE item_sites SET reorder_point = %s, max_qty = %s, "
        "synced_at = %s WHERE item_code = 'ITEM-1587-E' AND site_code = %s",
        (EXCESS_AFTER[0], EXCESS_AFTER[1], EXCESS_SYNCED_AT, SITE),
    )

    # The excess row, read back strictly.
    (excess_row,) = _audit_rows("ITEM-1587-E")
    assert set(excess_row.keys()) == AUDIT_KEYS
    assert excess_row["basis"] == "excess"
    assert excess_row["decision_id"] == "D-1587-E"
    assert excess_row["outcome"] == "written"
    assert excess_row["after_reorder_point"] == EXCESS_AFTER[0]
    assert excess_row["after_max_qty"] == EXCESS_AFTER[1]
    assert excess_row["error"] is None

    # The record is unchanged: the excess push is counted in neither
    # kind — it neither replaces nor joins a counted push.
    response = client.get(RECORD_URL)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_RECORD
