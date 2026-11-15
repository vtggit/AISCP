"""Issue #116 proof: CHECK constraints on open_purchase_lines.

Migration 0007_open_purchase_lines_checks (down_revision
0006_open_purchase_lines) adds two named check constraints:
  * ck_open_purchase_lines_open_units_nonnegative (open_units >= 0),
  * ck_open_purchase_lines_order_type_nonempty (length(order_type) > 0),
and its downgrade drops both.

Against the test database:
  * with head applied, the database itself rejects a row whose
    open_units is -1 with an integrity error naming
    ck_open_purchase_lines_open_units_nonnegative and a row whose
    order_type is empty with one naming
    ck_open_purchase_lines_order_type_nonempty, while a valid row
    inserts,
  * the migration validates the existing rows before adding the
    constraints: with violating rows present the upgrade to head
    aborts, the database stays at 0006_open_purchase_lines, and the
    rows are left untouched; once the rows are repaired, the upgrade
    succeeds and the repaired rows survive under the new constraints,
  * downgrading to 0006_open_purchase_lines removes both constraints,
    after which a violating row inserts again, and the upgrade back to
    head leaves the database at the single head.
"""

import os

import psycopg2
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.db.connection import get_connection
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASE_REVISION = "0006_open_purchase_lines"
_NEGATIVE_UNITS_CHECK = "ck_open_purchase_lines_open_units_nonnegative"
_NONEMPTY_ORDER_TYPE_CHECK = "ck_open_purchase_lines_order_type_nonempty"


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


def _check_constraints() -> list[str]:
    """Named CHECK constraints on open_purchase_lines, by name."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT con.conname FROM pg_constraint con "
                "JOIN pg_class rel ON rel.oid = con.conrelid "
                "WHERE rel.relname = 'open_purchase_lines' "
                "AND con.contype = 'c' ORDER BY con.conname"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _insert_raw(business_key: str, order_type: str, open_units: float) -> None:
    """Insert one raw row and commit, bypassing the repository."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO open_purchase_lines "
                "(id, business_key, item_code, site_code, supplier_code, "
                "order_type, open_units, promised_on, ordered_on, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    f"id-116-{business_key}",
                    business_key,
                    "W-116",
                    "SITE-1",
                    "SUP-116",
                    order_type,
                    open_units,
                    "2026-07-05",
                    "2026-06-20",
                    "2026-06-30 00:00:00+00",
                    "test",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _stored(business_key: str) -> dict | None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT order_type, open_units FROM open_purchase_lines "
                "WHERE business_key = %s",
                (business_key,),
            )
            row = cur.fetchone()
            return dict(zip(("order_type", "open_units"), row)) if row else None
    finally:
        conn.close()


def _repair(business_key: str, order_type: str, open_units: float) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE open_purchase_lines SET order_type = %s, open_units = %s "
                "WHERE business_key = %s",
                (order_type, open_units, business_key),
            )
        conn.commit()
    finally:
        conn.close()


def test_issue116_freeform(client):
    repo = OpenPurchaseLinePostgresRepository()

    # 1. Head is applied and both named checks exist on the table.
    assert _alembic_revision() == CURRENT_HEAD
    assert _check_constraints() == [
        _NEGATIVE_UNITS_CHECK,
        _NONEMPTY_ORDER_TYPE_CHECK,
    ]

    # 2. A negative open_units is rejected by the database with an
    #    integrity error naming the nonnegative check.
    with pytest.raises(psycopg2.IntegrityError) as negative:
        repo.upsert_many(
            [
                {
                    "business_key": "op-116-neg",
                    "item_code": "W-116",
                    "site_code": "SITE-1",
                    "order_type": "RO",
                    "open_units": -1.0,
                    "as_of": "2026-06-30 00:00:00+00",
                }
            ]
        )
    assert _NEGATIVE_UNITS_CHECK in str(negative.value)
    assert _stored("op-116-neg") is None

    # 3. An empty order_type is rejected by the database with an
    #    integrity error naming the nonempty check.
    with pytest.raises(psycopg2.IntegrityError) as empty:
        repo.upsert_many(
            [
                {
                    "business_key": "op-116-empty",
                    "item_code": "W-116",
                    "site_code": "SITE-1",
                    "order_type": "",
                    "open_units": 5.0,
                    "as_of": "2026-06-30 00:00:00+00",
                }
            ]
        )
    assert _NONEMPTY_ORDER_TYPE_CHECK in str(empty.value)
    assert _stored("op-116-empty") is None

    # 4. A valid row inserts.
    assert repo.upsert_many(
        [
            {
                "business_key": "op-116-valid",
                "item_code": "W-116",
                "site_code": "SITE-1",
                "order_type": "RO",
                "open_units": 5.0,
                "as_of": "2026-06-30 00:00:00+00",
            }
        ]
    ) == {"inserted": 1, "updated": 0}
    assert _stored("op-116-valid") == {"order_type": "RO", "open_units": 5.0}

    # 5. The migration validates the existing rows before adding the
    #    constraints: at 0006, violating rows exist, and the upgrade to
    #    head must abort instead of running over them.
    cfg = _alembic_config()
    alembic_command.downgrade(cfg, _BASE_REVISION)
    assert _alembic_revision() == _BASE_REVISION
    _insert_raw("op-116-bad-units", "RO", -5.0)
    _insert_raw("op-116-bad-type", "", 7.0)
    with pytest.raises(RuntimeError, match="repair them before applying"):
        alembic_command.upgrade(cfg, "head")
    # The abort left the database at 0006 with the rows untouched:
    # validation refuses, it does not backfill.
    assert _alembic_revision() == _BASE_REVISION
    assert _stored("op-116-bad-units") == {"order_type": "RO", "open_units": -5.0}
    assert _stored("op-116-bad-type") == {"order_type": "", "open_units": 7.0}
    assert _check_constraints() == []

    # 6. Once the rows are repaired, the upgrade succeeds and the
    #    repaired rows survive under the new constraints.
    _repair("op-116-bad-units", "RO", 5.0)
    _repair("op-116-bad-type", "RO", 7.0)
    alembic_command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert _alembic_revision() == heads[0]
    assert _check_constraints() == [
        _NEGATIVE_UNITS_CHECK,
        _NONEMPTY_ORDER_TYPE_CHECK,
    ]
    assert _stored("op-116-bad-units") == {"order_type": "RO", "open_units": 5.0}
    assert _stored("op-116-bad-type") == {"order_type": "RO", "open_units": 7.0}
    assert _stored("op-116-valid") == {"order_type": "RO", "open_units": 5.0}

    # 7. Downgrading to 0006 removes both constraints: neither name is
    #    left in the catalog and a violating row inserts again.
    alembic_command.downgrade(cfg, _BASE_REVISION)
    assert _alembic_revision() == _BASE_REVISION
    assert _check_constraints() == []
    _insert_raw("op-116-after-downgrade", "", -1.0)
    assert _stored("op-116-after-downgrade") == {
        "order_type": "",
        "open_units": -1.0,
    }

    # 8. The upgrade back to head re-adds both constraints, leaving the
    #    database at the single head.
    _repair("op-116-after-downgrade", "RO", 1.0)
    alembic_command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert _alembic_revision() == heads[0]
    assert _check_constraints() == [
        _NEGATIVE_UNITS_CHECK,
        _NONEMPTY_ORDER_TYPE_CHECK,
    ]
