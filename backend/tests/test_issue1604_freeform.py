"""Issue #1604 proof: the parameter writeback's pending audit row.

The confirm of ``POST /api/parameter-writeback/confirm`` now stores
its audit row before the ERP call: the row is written "pending"
(the before and after values and the error null) through the
repository's ``insert_pending``, and the writer's answer or error
then sets that same row's outcome, values and error after the call,
through ``update_outcome`` — so a confirmed pair has exactly one
audit row, a row still "pending" means the write's result is
unknown, and a failed write's row keeps its requested values with
its error.

Proved here, against the live app and the migrated test database,
seeded the way test 762 seeds it (the item-sites and the accepted
decisions directly in the test database, the route module's two
client classes replaced by fakes):

  * the order — a fake writer that, when called, reads the audit
    table sees exactly one row for its pair, with outcome "pending",
    the after values null and the requested values set;
  * written — after the confirm, that same row (the same id) has
    outcome "written" and the before and after values the writer
    answered, ``written_at`` untouched, and the table holds one row
    for the pair, every field read back strictly;
  * failed — with the writer raising ``AcumaticaError``, the same
    row has outcome "failed" and its error set, and the pair
    answers "writer_error";
  * no record, no write — with ``insert_pending`` made to raise,
    the writer is never called and the table holds no row for the
    pair;
  * lost update — with ``update_outcome`` made to raise after a
    successful write, the row stays "pending", the record of pushes
    does not count it, and the history lists it with outcome
    "pending", like any other row;
  * the constraint — a raw insert with outcome "pending" is
    accepted and one with outcome "other" is refused;
  * the downgrade — with one "pending" row stored, the downgrade to
    the parent revision ``PREV_REVISION`` rewrites that row to
    outcome "failed" and error "pending: result unknown" and the
    restored constraint refuses "pending" again; the upgrade back to
    the head (``CURRENT_HEAD`` from ``tests/migration_head.py``)
    restores it.
"""

import os
from datetime import date

import psycopg2
import psycopg2.errors
import psycopg2.extras
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
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

# The parent revision: the downgrade proof targets it.
PREV_REVISION = "0057_parameter_writeback_basis"

SITE = "SITE-1604"
ADMIN_TOKEN = "dev-secret-token:admin"
DECISION_AS_OF = date(2026, 11, 1)

PREVIEW_URL = "/api/parameter-writeback/preview"
CONFIRM_URL = "/api/parameter-writeback/confirm"
HISTORY_URL = "/api/parameter-writeback/history"
RECORD_URL = "/api/parameter-writeback/record"

# item_code -> (item_sites id, reorder_point, max_qty, order_qty,
# source_system): A is written, B's writer raises, C's pending row
# is never stored, D's outcome update raises.  Every pair's accepted
# decision recommends 55 against the seeded reorder point 25, so
# every pair is outstanding, with the proposed values 55 and 75
# (55 + the order quantity 20).
ITEM_A = "ITEM-1604-A"
ITEM_B = "ITEM-1604-B"
ITEM_C = "ITEM-1604-C"
ITEM_D = "ITEM-1604-D"
ITEMS = {
    ITEM_A: ("is-1604-a", 25.0, 60.0, 20.0, "acumatica"),
    ITEM_B: ("is-1604-b", 25.0, 60.0, 20.0, "acumatica"),
    ITEM_C: ("is-1604-c", 25.0, 60.0, 20.0, "acumatica"),
    ITEM_D: ("is-1604-d", 25.0, 60.0, 20.0, "acumatica"),
}
DECISIONS = {
    f"pd-1604-{suffix}": (item, "accepted", 55, 4000.0)
    for suffix, item in zip("abcd", (ITEM_A, ITEM_B, ITEM_C, ITEM_D))
}

# The writer's answer for a written pair: the seeded item_sites
# values as the ERP's before values, the requested values as the
# read-back.
BEFORE = (25.0, 60.0)
AFTER = (55.0, 75.0)

# The exact key sets of the stored audit rows and of the history
# answer (which carries no basis field).
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


class FakeAcumaticaClient:
    """The fake for the route module's AcumaticaClient: records every
    call and, when called, reads the audit table — the row the
    confirm stored before the ERP call (the way test 762's fake
    answers the write)."""

    calls: list[dict] = []
    observations: dict[tuple[str, str], list[dict]] = {}
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
        FakeAcumaticaClient.observations[(item_code, site_code)] = _audit_rows(
            item_code, site_code
        )
        if item_code in FakeAcumaticaClient.fail_for:
            raise AcumaticaError("the fake ERP refused the write")
        return {
            "outcome": "written",
            "before": {"reorder_point": BEFORE[0], "max_qty": BEFORE[1]},
            "after": {"reorder_point": reorder_point, "max_qty": max_qty},
        }


class FakeOdooClient:
    """The fake for the route module's OdooClient: records every call
    (the proof confirms no Odoo pair, so it must stay empty)."""

    calls: list[dict] = []

    def write_reorder_rule(
        self, *, item_code: str, site_code: str, reorder_point: float, max_qty: float
    ) -> dict:
        FakeOdooClient.calls.append(
            {
                "item_code": item_code,
                "site_code": site_code,
                "reorder_point": reorder_point,
                "max_qty": max_qty,
            }
        )
        return {
            "outcome": "written",
            "before": {"reorder_point": BEFORE[0], "max_qty": BEFORE[1]},
            "after": {"reorder_point": reorder_point, "max_qty": max_qty},
        }


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
            for decision_id, (item_code, decision, units, money) in DECISIONS.items():
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
                        decision,
                        None,
                        None,
                        DECISION_AS_OF,
                        units,
                        25.0,
                        money,
                        "seeder",
                        "2026-11-01T09:00:00+00",
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _audit_rows(
    item_code: str | None = None, site_code: str | None = None
) -> list[dict]:
    """The stored audit rows, strictly, oldest first (narrowed to one
    pair when given)."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if item_code is None:
                cur.execute(
                    "SELECT * FROM parameter_writebacks ORDER BY written_at, id"
                )
            else:
                cur.execute(
                    "SELECT * FROM parameter_writebacks "
                    "WHERE item_code = %s AND site_code = %s "
                    "ORDER BY written_at, id",
                    (item_code, site_code),
                )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _assert_audit_row(
    row: dict,
    *,
    item_code: str,
    decision_id: str | None,
    requested: tuple[float, float],
    outcome: str,
    before: tuple[float | None, float | None],
    after: tuple[float | None, float | None],
    error: str | None,
) -> None:
    """One stored audit row, field for field."""
    assert set(row.keys()) == AUDIT_KEYS
    assert row["item_code"] == item_code
    assert row["site_code"] == SITE
    assert row["source_system"] == "acumatica"
    assert row["decision_id"] == decision_id
    assert row["basis"] == "decision"
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


def _raw_insert(outcome: str, row_id: str) -> None:
    """One raw audit row with the given outcome — the constraint
    proof's rows, inserted outside the repository."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO parameter_writebacks "
                "(id, item_code, site_code, source_system, basis, "
                " requested_reorder_point, requested_max_qty, outcome, written_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    row_id,
                    "ITEM-1604-X",
                    SITE,
                    "acumatica",
                    "decision",
                    1.0,
                    2.0,
                    outcome,
                    "nobody",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _assert_outcome_refused(outcome: str) -> None:
    """The check constraint refuses the given outcome value."""
    try:
        _raw_insert(outcome, f"row-1604-{outcome}")
    except psycopg2.Error as exc:
        assert isinstance(exc, psycopg2.errors.CheckViolation)
        assert "ck_parameter_writebacks_outcome" in str(exc)
    else:
        raise AssertionError(f"outcome {outcome!r} was accepted")


def test_issue1604_freeform(client, monkeypatch):
    # The proof starts from its own picture of the audit.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM parameter_writebacks")
        conn.commit()
    finally:
        conn.close()

    # The two client classes in the route module are replaced with
    # fakes that record their calls (the way test 762 replaces them).
    FakeAcumaticaClient.calls = []
    FakeAcumaticaClient.observations = {}
    FakeAcumaticaClient.fail_for = set()
    FakeOdooClient.calls = []
    monkeypatch.setattr(parameter_writeback, "AcumaticaClient", FakeAcumaticaClient)
    monkeypatch.setattr(parameter_writeback, "OdooClient", FakeOdooClient)
    # The recommendation inputs: the registry defaults, never ambient
    # environment values (the way test 762 pins them).
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    _seed_item_sites_and_decisions()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    # The same client, without raising server exceptions: the proof's
    # two "the exception propagates" scenarios answer 500 instead of
    # crashing the test.
    admin_lenient = TestClient(
        app,
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}"},
        raise_server_exceptions=False,
    )

    # --- The order: the fake writer reads the audit table when it is
    #     called — the row is pending before the ERP call. ----------
    response = admin.post(
        CONFIRM_URL, json={"items": [{"item_code": ITEM_A, "site_code": SITE}]}
    )
    assert response.status_code == 200, response.text
    confirm = response.json()
    assert confirm["written"] == 1
    assert confirm["unchanged"] == 0
    assert confirm["failed"] == 0
    assert confirm["skipped"] == 0
    (result,) = confirm["results"]
    assert result == {
        "item_code": ITEM_A,
        "site_code": SITE,
        "outcome": "written",
        "reason": None,
        "before": {"reorder_point": 25.0, "max_qty": 60.0, "order_qty": 20.0},
        "after": {"reorder_point": 55.0, "max_qty": 75.0, "order_qty": 20.0},
    }
    assert len(FakeAcumaticaClient.calls) == 1
    assert FakeOdooClient.calls == []
    # When the writer was called, the audit table held exactly one
    # row for the pair, pending, the before and after values null,
    # the requested values set.
    observed_rows = FakeAcumaticaClient.observations[(ITEM_A, SITE)]
    assert len(observed_rows) == 1
    (observed,) = observed_rows
    _assert_audit_row(
        observed,
        item_code=ITEM_A,
        decision_id="pd-1604-a",
        requested=AFTER,
        outcome="pending",
        before=(None, None),
        after=(None, None),
        error=None,
    )

    # --- Written: that same row (the same id) is written, the before
    #     and after values the writer answered, written_at untouched;
    #     the table holds one row for the pair. ----------------------
    rows = _audit_rows(ITEM_A, SITE)
    assert len(rows) == 1
    (row,) = rows
    assert row["id"] == observed["id"]
    _assert_audit_row(
        row,
        item_code=ITEM_A,
        decision_id="pd-1604-a",
        requested=AFTER,
        outcome="written",
        before=BEFORE,
        after=AFTER,
        error=None,
    )
    # The outcome update touched the same row: written_at is not
    # changed.
    assert row["written_at"] == observed["written_at"]

    # --- Failed: the writer raises AcumaticaError — the same row
    #     (the pair's pending row) is failed, its error set, and the
    #     pair answers the stable public reason. ---------------------
    FakeAcumaticaClient.fail_for = {ITEM_B}
    response = admin.post(
        CONFIRM_URL, json={"items": [{"item_code": ITEM_B, "site_code": SITE}]}
    )
    assert response.status_code == 200, response.text
    confirm = response.json()
    assert confirm["written"] == 0
    assert confirm["unchanged"] == 0
    assert confirm["failed"] == 1
    assert confirm["skipped"] == 0
    (result,) = confirm["results"]
    assert result == {
        "item_code": ITEM_B,
        "site_code": SITE,
        "outcome": "failed",
        "reason": "writer_error",
        "before": None,
        "after": None,
    }
    # The failing writer read the audit table too: the pair's row was
    # pending when the ERP call was made.
    observed_b = FakeAcumaticaClient.observations[(ITEM_B, SITE)]
    assert len(observed_b) == 1
    (observed,) = observed_b
    _assert_audit_row(
        observed,
        item_code=ITEM_B,
        decision_id="pd-1604-b",
        requested=AFTER,
        outcome="pending",
        before=(None, None),
        after=(None, None),
        error=None,
    )
    rows = _audit_rows(ITEM_B, SITE)
    assert len(rows) == 1
    (row,) = rows
    assert row["id"] == observed["id"]
    _assert_audit_row(
        row,
        item_code=ITEM_B,
        decision_id="pd-1604-b",
        requested=AFTER,
        outcome="failed",
        before=(None, None),
        after=(None, None),
        error="AcumaticaError: the fake ERP refused the write",
    )
    assert row["written_at"] == observed["written_at"]
    # The failed write left the item-site untouched.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT reorder_point, max_qty, order_qty FROM item_sites "
                "WHERE item_code = %s AND site_code = %s",
                (ITEM_B, SITE),
            )
            assert cur.fetchone() == (25.0, 60.0, 20.0)
    finally:
        conn.close()

    # --- No record, no write: insert_pending raises — the ERP is not
    #     called and the table holds no row for the pair. -----------
    real_insert_pending = parameter_writeback._writebacks.insert_pending

    def raising_insert_pending(**fields):
        raise RuntimeError("insert_pending failed")

    monkeypatch.setattr(
        parameter_writeback._writebacks, "insert_pending", raising_insert_pending
    )
    response = admin_lenient.post(
        CONFIRM_URL, json={"items": [{"item_code": ITEM_C, "site_code": SITE}]}
    )
    assert response.status_code == 500  # the exception propagates
    assert [
        call for call in FakeAcumaticaClient.calls if call["item_code"] == ITEM_C
    ] == []
    assert _audit_rows(ITEM_C, SITE) == []
    monkeypatch.setattr(
        parameter_writeback._writebacks, "insert_pending", real_insert_pending
    )

    # --- Lost update: update_outcome raises after a successful write
    #     — the row stays pending, the record does not count it, and
    #     the history lists it with outcome "pending". --------------
    def raising_update_outcome(writeback_id, **fields):
        raise RuntimeError("update_outcome failed")

    monkeypatch.setattr(
        parameter_writeback._writebacks, "update_outcome", raising_update_outcome
    )
    response = admin_lenient.post(
        CONFIRM_URL, json={"items": [{"item_code": ITEM_D, "site_code": SITE}]}
    )
    assert response.status_code == 500  # the exception propagates
    rows = _audit_rows(ITEM_D, SITE)
    assert len(rows) == 1
    (row,) = rows
    _assert_audit_row(
        row,
        item_code=ITEM_D,
        decision_id="pd-1604-d",
        requested=AFTER,
        outcome="pending",
        before=(None, None),
        after=(None, None),
        error=None,
    )
    # The record of pushes counts only the written row of the first
    # pair — the pending row of the last one counts nowhere.
    response = admin.get(RECORD_URL)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "reorder_points": {"pushed": 1, "read": 0, "unchanged": 0, "rate": None},
        "buffers": {"pushed": 0, "read": 0, "unchanged": 0, "rate": None},
        "excess": {"pushed": 0, "read": 0, "unchanged": 0, "rate": None},
    }
    # The history lists the pending row like any other, with its
    # outcome "pending"; the shape of the answer is unchanged.
    response = admin.get(HISTORY_URL)
    assert response.status_code == 200, response.text
    history = response.json()
    assert len(history) == 3
    for row in history:
        assert set(row.keys()) == HISTORY_KEYS
    by_pair = {(row["item_code"], row["site_code"]): row for row in history}
    assert set(by_pair) == {
        (ITEM_A, SITE),
        (ITEM_B, SITE),
        (ITEM_D, SITE),
    }
    assert by_pair[(ITEM_A, SITE)]["outcome"] == "written"
    assert by_pair[(ITEM_B, SITE)]["outcome"] == "failed"
    assert by_pair[(ITEM_B, SITE)]["error"] == "writer_error"
    pending_history = by_pair[(ITEM_D, SITE)]
    assert pending_history["outcome"] == "pending"
    assert pending_history["error"] is None
    assert pending_history["before_reorder_point"] is None
    assert pending_history["before_max_qty"] is None
    assert pending_history["after_reorder_point"] is None
    assert pending_history["after_max_qty"] is None
    # The parameter values come back as exact decimals: the stored
    # floats rendered through their shortest round-trip
    # representation.
    assert pending_history["requested_reorder_point"] == "55.0"
    assert pending_history["requested_max_qty"] == "75.0"
    assert pending_history["decision_id"] == "pd-1604-d"
    assert pending_history["written_by"] == "dev-admin-1"
    assert pending_history["written_by_name"] == "admin"

    # --- The constraint: "pending" is admitted, "other" is refused. -
    _raw_insert("pending", "row-1604-pending")
    (row,) = _audit_rows("ITEM-1604-X", SITE)
    assert row["id"] == "row-1604-pending"
    assert row["outcome"] == "pending"
    _assert_outcome_refused("other")
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM parameter_writebacks WHERE item_code = %s",
                ("ITEM-1604-X",),
            )
        conn.commit()
    finally:
        conn.close()

    # --- The downgrade: one pending row stored, the downgrade to the
    #     parent rewrites it to failed and "pending: result unknown",
    #     and the upgrade back to the head restores the constraint. --
    pending_row = ParameterWritebacksPostgresRepository().insert_pending(
        item_code="ITEM-1604-X",
        site_code=SITE,
        source_system="acumatica",
        decision_id=None,
        basis="ddmrp",
        requested_reorder_point=1.0,
        requested_max_qty=2.0,
        written_by="seeder",
        written_by_name=None,
    )
    cfg = _alembic_config()
    try:
        alembic_command.downgrade(cfg, PREV_REVISION)
        assert _alembic_revision() == PREV_REVISION
        (row,) = _audit_rows("ITEM-1604-X", SITE)
        assert row["id"] == pending_row["id"]
        assert row["outcome"] == "failed"
        assert row["error"] == "pending: result unknown"
        # Every other value of the row survives the downgrade.
        assert row["basis"] == "ddmrp"
        assert row["decision_id"] is None
        assert row["before_reorder_point"] is None
        assert row["before_max_qty"] is None
        assert row["after_reorder_point"] is None
        assert row["after_max_qty"] is None
        assert row["requested_reorder_point"] == 1.0
        assert row["requested_max_qty"] == 2.0
        assert row["written_at"] == pending_row["written_at"]
        # The restored constraint refuses "pending" again.
        _assert_outcome_refused("pending")
    finally:
        head = str(CURRENT_HEAD)
        alembic_command.upgrade(cfg, head)
    assert _alembic_revision() == CURRENT_HEAD
    # The rewritten row is still the one failed row at the head.
    (row,) = _audit_rows("ITEM-1604-X", SITE)
    assert row["id"] == pending_row["id"]
    assert row["outcome"] == "failed"
    assert row["error"] == "pending: result unknown"
    # And "pending" is admitted by the restored constraint, the
    # rewritten row still beside it.
    _raw_insert("pending", "row-1604-head")
    assert {row["id"]: row["outcome"] for row in _audit_rows("ITEM-1604-X", SITE)} == {
        pending_row["id"]: "failed",
        "row-1604-head": "pending",
    }
