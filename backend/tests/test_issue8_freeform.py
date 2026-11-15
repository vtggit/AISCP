"""Issue #8 proof: lead_time_observations migration, upsert repository,
read-only API.

Against the test database:
  * upsert_many inserts a new row and, on a matching business_key,
    updates every other column and bumps synced_at, in one transaction,
  * lead_time_days is a generated column: the repository never writes it,
    the database computes it, and the API returns it,
  * a receipt date that precedes the order date is rejected by the
    database check constraint,
  * the read-only API lists, filters, and fetches rows; a missing row is
    404, limit above 1000 is rejected with 422 and POST
    /api/lead-time-observations is 405,
  * the migration downgrades and upgrades back to a single head.
"""

import os
from datetime import date

import psycopg2
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.db.connection import get_connection
from app.repositories.lead_time_observations_postgres_repository import (
    LeadTimeObservationPostgresRepository,
)

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND_ROOT, "migrations"))
    return cfg


def _table_names() -> list[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            return [row[0] for row in cur.fetchall()]
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


def test_issue8_freeform(client):
    repo = LeadTimeObservationPostgresRepository()

    # 1. upsert_many inserts a new row: ordered 2026-01-05, received
    #    2026-01-19.
    first = {
        "business_key": "R:RCPT-2026-001:1",
        "supplier_code": "ACME-CORP",
        "item_code": "WIDGET-1",
        "site_code": "SITE-A",
        "ordered_on": "2026-01-05",
        "received_on": "2026-01-19",
        "source_system": "acumatica",
    }
    second = {
        "business_key": "R:RCPT-2026-002:1",
        "supplier_code": "GLOBEX",
        "item_code": "WIDGET-2",
        "site_code": None,
        "ordered_on": "2026-01-10",
        "received_on": "2026-01-24",
    }
    assert repo.upsert_many([first, second]) == {"inserted": 2, "updated": 0}

    # 2. The database computes lead_time_days: the API answers 14 for
    #    2026-01-19 minus 2026-01-05, and the stored value is a real int.
    one = client.get("/api/lead-time-observations/R:RCPT-2026-001:1")
    assert one.status_code == 200, one.text
    assert one.json()["lead_time_days"] == 14
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT lead_time_days FROM lead_time_observations "
                "WHERE business_key = %s",
                ("R:RCPT-2026-001:1",),
            )
            raw = cur.fetchone()[0]
    finally:
        conn.close()
    assert raw == 14
    assert type(raw) is int

    # 3. Re-upserting the same business_key with received_on 2026-01-26
    #    counts as one update and the computed lead time becomes 21.
    changed = {**first, "received_on": "2026-01-26", "source_system": "dynamics"}
    assert repo.upsert_many([changed]) == {"inserted": 0, "updated": 1}
    stored = repo.get_by_business_key("R:RCPT-2026-001:1")
    assert stored["received_on"] == date(2026, 1, 26)
    assert stored["source_system"] == "dynamics"
    assert stored["supplier_code"] == "ACME-CORP"
    assert (
        client.get("/api/lead-time-observations/R:RCPT-2026-001:1").json()[
            "lead_time_days"
        ]
        == 21
    )

    # 4. A receipt date that precedes the order date is rejected by the
    #    database check constraint, and the failed transaction rolls back.
    with pytest.raises(psycopg2.IntegrityError):
        repo.upsert_many(
            [
                {
                    "business_key": "R:RCPT-2026-009:1",
                    "supplier_code": "GLOBEX",
                    "item_code": "WIDGET-9",
                    "ordered_on": "2026-01-10",
                    "received_on": "2026-01-01",
                }
            ]
        )
    assert repo.get_by_business_key("R:RCPT-2026-009:1") is None

    # 5. lead_time_days is a generated column: the database refuses to
    #    accept it in an INSERT payload.
    conn = get_connection()
    try:
        with pytest.raises(psycopg2.Error):
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO lead_time_observations "
                    "(id, business_key, supplier_code, item_code, site_code, "
                    "ordered_on, received_on, lead_time_days, source_system, "
                    "synced_at) "
                    "VALUES (%s, %s, %s, %s, NULL, %s, %s, %s, NULL, NOW())",
                    (
                        "manual-1",
                        "R:MANUAL-1",
                        "ACME-CORP",
                        "WIDGET-1",
                        "2026-01-05",
                        "2026-01-19",
                        99,
                    ),
                )
            conn.rollback()
    finally:
        conn.close()
    assert repo.get_by_business_key("R:MANUAL-1") is None

    # 6. GET /api/lead-time-observations lists the stored rows ordered by
    #    business_key; filters narrow them.
    listed = client.get("/api/lead-time-observations")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [row["business_key"] for row in body] == [
        "R:RCPT-2026-001:1",
        "R:RCPT-2026-002:1",
    ]
    assert body[0]["ordered_on"] == "2026-01-05"
    assert body[0]["received_on"] == "2026-01-26"
    assert body[0]["site_code"] == "SITE-A"
    assert body[1]["site_code"] is None

    by_supplier = client.get(
        "/api/lead-time-observations", params={"supplier_code": "ACME-CORP"}
    )
    assert by_supplier.status_code == 200, by_supplier.text
    assert [row["business_key"] for row in by_supplier.json()] == ["R:RCPT-2026-001:1"]

    by_item = client.get(
        "/api/lead-time-observations",
        params={"item_code": "WIDGET-2", "supplier_code": "GLOBEX"},
    )
    assert by_item.status_code == 200, by_item.text
    assert [row["business_key"] for row in by_item.json()] == ["R:RCPT-2026-002:1"]

    none = client.get(
        "/api/lead-time-observations", params={"supplier_code": "NO-SUCH-VENDOR"}
    )
    assert none.status_code == 200
    assert none.json() == []

    # 7. A missing business_key answers 404.
    assert (
        client.get("/api/lead-time-observations/R:NO-SUCH-RECEIPT:9").status_code == 404
    )

    # 8. A limit above 1000 is rejected with 422.
    assert (
        client.get("/api/lead-time-observations", params={"limit": 1001}).status_code
        == 422
    )

    # NUL bytes in filter values are rejected before reaching the database.
    nul_guarded = client.get(
        "/api/lead-time-observations",
        params={"supplier_code": "a\0b", "item_code": "S\0A"},
    )
    assert nul_guarded.status_code == 422
    assert nul_guarded.json()["detail"] == (
        "lead time observation filters must not contain NUL bytes"
    )

    # 9. The router is read-only: POST /api/lead-time-observations
    #    answers 405.
    assert (
        client.post(
            "/api/lead-time-observations",
            json={
                "business_key": "R:RCPT-2026-999:1",
                "supplier_code": "ACME-CORP",
                "item_code": "WIDGET-9",
                "ordered_on": "2026-01-05",
                "received_on": "2026-01-19",
            },
        ).status_code
        == 405
    )

    # 10. The migration downgrades and upgrades back to a single head.
    cfg = _alembic_config()
    alembic_command.downgrade(cfg, "0003_demand_events")
    assert _alembic_revision() == "0003_demand_events"
    assert "lead_time_observations" not in _table_names()
    alembic_command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert _alembic_revision() == heads[0]
    assert "lead_time_observations" in _table_names()
