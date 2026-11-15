"""Issue #7 proof: demand_events migration, upsert repository, read-only API.

Against the test database:
  * upsert_many inserts new rows and, on a matching business_key, updates
    every other column and bumps synced_at, in one transaction,
  * an event_type outside the allowed set and a zero quantity are each
    rejected by the database check constraints,
  * occurred_on is stored as a real DATE and round-trips as a date,
  * the occurred_from/occurred_to filters are inclusive at both ends,
  * the read-only API lists, filters, and fetches rows; a missing row is
    404, limit above 1000 is rejected with 422 and POST /api/demand-events
    is 405,
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
from app.repositories.demand_events_postgres_repository import (
    DemandEventPostgresRepository,
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


def test_issue7_freeform(client):
    repo = DemandEventPostgresRepository()

    # 1. upsert_many inserts two new rows in one transaction.
    first = {
        "business_key": "SO-1001-LINE-1",
        "item_code": "WIDGET-1",
        "site_code": "SITE-A",
        "quantity": 12.0,
        "occurred_on": date(2026, 9, 10),
        "event_type": "shipment",
        "source_system": "acumatica",
    }
    second = {
        "business_key": "SO-1002-LINE-1",
        "item_code": "WIDGET-2",
        "site_code": "SITE-B",
        "quantity": 4.5,
        "occurred_on": date(2026, 9, 12),
        "event_type": "production_issue",
    }
    assert repo.upsert_many([first, second]) == {"inserted": 2, "updated": 0}

    # 2. Re-upserting the existing business_key with a changed quantity
    #    counts as one update and rewrites every other column in place.
    changed = {**first, "quantity": 99.0, "source_system": "dynamics"}
    assert repo.upsert_many([changed]) == {"inserted": 0, "updated": 1}
    stored = repo.get_by_business_key("SO-1001-LINE-1")
    assert stored["quantity"] == 99.0
    assert stored["source_system"] == "dynamics"
    assert stored["event_type"] == "shipment"
    assert stored["item_code"] == "WIDGET-1"
    assert stored["site_code"] == "SITE-A"

    # 3a. An event_type outside the allowed set is rejected by the
    #     database check constraint.
    with pytest.raises(psycopg2.IntegrityError):
        repo.upsert_many(
            [{**first, "business_key": "SO-1001-LINE-9", "event_type": "transfer"}]
        )

    # 3b. A zero quantity is rejected by the database check constraint.
    with pytest.raises(psycopg2.IntegrityError):
        repo.upsert_many([{**second, "business_key": "SO-1002-LINE-9", "quantity": 0}])

    # The failed transactions rolled back: no partial rows were left behind.
    assert repo.get_by_business_key("SO-1001-LINE-9") is None
    assert repo.get_by_business_key("SO-1002-LINE-9") is None

    # 4. occurred_on round-trips as a date, not text: the repository hands
    #    back a datetime.date and the stored column value is a real DATE.
    assert stored["occurred_on"] == date(2026, 9, 10)
    assert type(stored["occurred_on"]) is date
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT occurred_on FROM demand_events WHERE business_key = %s",
                ("SO-1001-LINE-1",),
            )
            raw = cur.fetchone()[0]
    finally:
        conn.close()
    assert type(raw) is date

    # 5. occurred_from/occurred_to are inclusive at both ends: rows whose
    #    occurred_on equals a bound are returned.
    bounded = repo.list_rows(
        100, 0, occurred_from=date(2026, 9, 10), occurred_to=date(2026, 9, 12)
    )
    assert [row["business_key"] for row in bounded] == [
        "SO-1001-LINE-1",
        "SO-1002-LINE-1",
    ]
    left_bound = repo.list_rows(
        100, 0, occurred_from=date(2026, 9, 10), occurred_to=date(2026, 9, 10)
    )
    assert [row["business_key"] for row in left_bound] == ["SO-1001-LINE-1"]

    # 6. GET /api/demand-events lists the stored rows ordered by
    #    business_key; filters narrow them.
    listed = client.get("/api/demand-events")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [row["business_key"] for row in body] == [
        "SO-1001-LINE-1",
        "SO-1002-LINE-1",
    ]
    assert body[0]["occurred_on"] == "2026-09-10"
    assert date.fromisoformat(body[0]["occurred_on"]) == date(2026, 9, 10)

    filtered = client.get(
        "/api/demand-events", params={"item_code": "WIDGET-2", "site_code": "SITE-B"}
    )
    assert filtered.status_code == 200, filtered.text
    assert [row["business_key"] for row in filtered.json()] == ["SO-1002-LINE-1"]

    ranged = client.get(
        "/api/demand-events",
        params={"occurred_from": "2026-09-11", "occurred_to": "2026-09-12"},
    )
    assert ranged.status_code == 200, ranged.text
    assert [row["business_key"] for row in ranged.json()] == ["SO-1002-LINE-1"]

    none = client.get("/api/demand-events", params={"item_code": "GHOST"})
    assert none.status_code == 200
    assert none.json() == []

    one = client.get("/api/demand-events/SO-1002-LINE-1")
    assert one.status_code == 200, one.text
    assert one.json()["quantity"] == 4.5
    assert one.json()["event_type"] == "production_issue"
    assert one.json()["occurred_on"] == "2026-09-12"

    # 7. A missing business_key answers 404.
    assert client.get("/api/demand-events/NO-SUCH-KEY").status_code == 404

    # 8. A limit above 1000 is rejected with 422.
    assert client.get("/api/demand-events", params={"limit": 1001}).status_code == 422

    # NUL bytes in filter values are rejected before reaching the database.
    assert (
        client.get("/api/demand-events", params={"item_code": "a\0b"}).status_code
        == 422
    )
    assert (
        client.get("/api/demand-events", params={"site_code": "S\0A"}).status_code
        == 422
    )

    # Review-fix proof: a NUL byte in a filter value must reach the guard's
    # own HTTPException (422 with its detail message), not an unhandled
    # AttributeError from a missing status constant (which would be a 500).
    nul_guarded = client.get(
        "/api/demand-events",
        params={"item_code": "a\0b", "site_code": "S\0A"},
    )
    assert nul_guarded.status_code == 422
    assert nul_guarded.json()["detail"] == (
        "demand event filters must not contain NUL bytes"
    )

    # 9. The router is read-only: POST /api/demand-events answers 405.
    assert (
        client.post(
            "/api/demand-events",
            json={
                "business_key": "SO-9999-LINE-1",
                "item_code": "WIDGET-9",
                "site_code": "SITE-Z",
                "quantity": 1.0,
                "occurred_on": "2026-09-15",
                "event_type": "shipment",
            },
        ).status_code
        == 405
    )

    # 10. The migration downgrades and upgrades back to a single head.
    cfg = _alembic_config()
    alembic_command.downgrade(cfg, "0002_item_sites")
    assert _alembic_revision() == "0002_item_sites"
    assert "demand_events" not in _table_names()
    alembic_command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert _alembic_revision() == heads[0]
    assert "demand_events" in _table_names()
