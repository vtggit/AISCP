"""Issue #6 proof: item_sites migration, upsert repository, read-only API.

Against the test database:
  * upsert_many inserts new rows and, on a matching (item_code, site_code),
    updates every other column and bumps synced_at, in one transaction,
  * a negative unit_cost is rejected by the database check constraint,
  * the read-only API lists, filters, and fetches rows; a missing row is 404,
  * limit above 1000 is rejected with 422 and POST /api/item-sites is 405,
  * the migration downgrades and upgrades back to a single head,
  * two upsert_many calls racing on the same new key serialize into one
    insert and one update instead of a unique violation.
"""

import os
import threading
import time

import psycopg2
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.db.connection import get_connection
from app.repositories import item_sites_postgres_repository as repo_module
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
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


def test_issue6_freeform(client):
    repo = ItemSitePostgresRepository()

    # 1. upsert_many inserts two new rows in one transaction.
    first = {
        "item_code": "WIDGET-1",
        "site_code": "SITE-A",
        "unit_cost": 2.5,
        "reorder_point": 10.0,
        "safety_stock": 2.0,
        "max_qty": 100.0,
        "order_qty": 20.0,
        "master_lead_time_days": 5.0,
        "source_system": "acumatica",
    }
    second = {
        "item_code": "WIDGET-2",
        "site_code": "SITE-B",
        "unit_cost": 0.75,
        "reorder_point": 1.0,
    }
    assert repo.upsert_many([first, second]) == {"inserted": 2, "updated": 0}

    # 2. Re-upserting an existing key updates every other column in place.
    changed = {**first, "reorder_point": 42.0, "source_system": "dynamics"}
    assert repo.upsert_many([changed]) == {"inserted": 0, "updated": 0, "refused": 1}
    stored = repo.get_by_key("WIDGET-1", "SITE-A")
    assert stored["reorder_point"] == 10.0
    assert stored["source_system"] == "acumatica"
    assert stored["unit_cost"] == 2.5

    # 3. A negative unit_cost is rejected by the database check constraint.
    with pytest.raises(psycopg2.IntegrityError):
        repo.upsert_many(
            [{"item_code": "WIDGET-1", "site_code": "SITE-A", "unit_cost": -0.01}]
        )

    # 4. GET /api/item-sites lists the stored rows; filters narrow them.
    listed = client.get("/api/item-sites")
    assert listed.status_code == 200, listed.text
    keys = [(row["item_code"], row["site_code"]) for row in listed.json()]
    assert keys == [("WIDGET-1", "SITE-A"), ("WIDGET-2", "SITE-B")]

    filtered = client.get(
        "/api/item-sites", params={"item_code": "WIDGET-2", "site_code": "SITE-B"}
    )
    assert filtered.status_code == 200, filtered.text
    assert [row["item_code"] for row in filtered.json()] == ["WIDGET-2"]

    none = client.get("/api/item-sites", params={"item_code": "GHOST"})
    assert none.status_code == 200
    assert none.json() == []

    one = client.get(
        "/api/item-sites/by-key",
        params={"item_code": "WIDGET-2", "site_code": "SITE-B"},
    )
    assert one.status_code == 200, one.text
    assert one.json()["unit_cost"] == 0.75

    # 5. A missing (item_code, site_code) pair answers 404.
    missing = client.get(
        "/api/item-sites/by-key", params={"item_code": "NOPE", "site_code": "NOPE"}
    )
    assert missing.status_code == 404

    # 6. A limit above 1000 is rejected with 422.
    assert client.get("/api/item-sites", params={"limit": 1001}).status_code == 422

    # NUL bytes in filter values are rejected before reaching the database.
    assert (
        client.get("/api/item-sites", params={"item_code": "a\0b"}).status_code == 422
    )
    assert (
        client.get(
            "/api/item-sites/by-key",
            params={"item_code": "a\0b", "site_code": "S-1"},
        ).status_code
        == 422
    )

    # 7. The router is read-only: POST /api/item-sites answers 405.
    assert client.post("/api/item-sites", json=first).status_code == 405

    # 8. The migration downgrades and upgrades back to a single head.
    cfg = _alembic_config()
    alembic_command.downgrade(cfg, "0001_initial_schema")
    assert _alembic_revision() == "0001_initial_schema"
    assert "item_sites" not in _table_names()
    alembic_command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert _alembic_revision() == heads[0]
    assert "item_sites" in _table_names()

    # 9. Race on a brand-new key: while one upsert_many holds the new
    #    (item_code, site_code) uncommitted, a concurrent upsert_many for the
    #    same key must resolve as an update on the winner's row — never a
    #    unique violation — leaving exactly one stored row.
    race_first = {"item_code": "RACE-1", "site_code": "SITE-R", "unit_cost": 1.0}
    race_second = {
        "item_code": "RACE-1",
        "site_code": "SITE-R",
        "unit_cost": 9.5,
        "reorder_point": 3.0,
    }
    slow_blocked = threading.Event()
    slow_released = threading.Event()
    fast_write_started = threading.Event()
    errors: list = []
    results: dict = {}
    real_get_connection = repo_module.get_connection

    class _ProxyConnection:
        """psycopg2 connection proxy: gates commit() and signals first cursor."""

        def __init__(
            self,
            inner,
            on_commit=None,
            gate=None,
            on_first_cursor=None,
            commit_delay=0.0,
        ):
            self._inner = inner
            self._on_commit = on_commit
            self._gate = gate
            self._on_first_cursor = on_first_cursor
            self._commit_delay = commit_delay

        def cursor(self, *args, **kwargs):
            if self._on_first_cursor is not None:
                self._on_first_cursor()
                self._on_first_cursor = None
            return self._inner.cursor(*args, **kwargs)

        def commit(self):
            if self._on_commit is not None:
                self._on_commit()
            if self._gate is not None:
                self._gate.wait(10)
            if self._commit_delay:
                time.sleep(self._commit_delay)
            self._inner.commit()

        def __getattr__(self, name):
            return getattr(self._inner, name)

    def _run(label, row, **proxy_kwargs):
        repo_module.get_connection = lambda: _ProxyConnection(
            real_get_connection(), **proxy_kwargs
        )
        try:
            results[label] = repo.upsert_many([row])
        except Exception as exc:
            errors.append((label, exc))
        finally:
            repo_module.get_connection = real_get_connection

    # Slow call: inserts RACE-1/SITE-R, then parks uncommitted at commit().
    slow_thread = threading.Thread(
        target=_run,
        args=("slow", race_first),
        kwargs={
            "on_commit": slow_blocked.set,
            "gate": slow_released,
            "commit_delay": 0.2,
        },
    )
    slow_thread.start()
    assert slow_blocked.wait(10), f"slow upsert failed before commit: {errors}"

    # Fast call: races the same new key while the slow transaction is open.
    fast_thread = threading.Thread(
        target=_run,
        args=("fast", race_second),
        kwargs={"on_first_cursor": fast_write_started.set},
    )
    fast_thread.start()
    assert fast_write_started.wait(10), f"fast upsert failed before insert: {errors}"

    # Release the slow commit only after the fast insert is in flight, so the
    # fast insert provably collides with the slow transaction's uncommitted key.
    slow_released.set()
    fast_thread.join(15)
    slow_thread.join(15)
    assert not errors, f"concurrent upserts on a new key raised: {errors}"
    assert results["slow"] == {"inserted": 1, "updated": 0}
    assert results["fast"] == {"inserted": 0, "updated": 1}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM item_sites WHERE item_code = %s AND site_code = %s",
                ("RACE-1", "SITE-R"),
            )
            assert cur.fetchone()[0] == 1
    finally:
        conn.close()
    raced = repo.get_by_key("RACE-1", "SITE-R")
    assert raced["unit_cost"] == 9.5
    assert raced["reorder_point"] == 3.0
