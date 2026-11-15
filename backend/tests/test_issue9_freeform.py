"""Issue #9 proof: on_hand migration, upsert repository, read-only API.

Against the test database:
  * upsert_many inserts new rows and, on a matching (item_code, site_code),
    updates every other column and bumps synced_at, in one transaction,
  * last-fresh-wins by as_of: a re-upsert with a later as_of stores both
    new values, an older snapshot is ignored, and re-sending the current
    snapshot is idempotent,
  * a negative quantity_on_hand is stored as the ERP reported it,
  * the read-only API lists, filters, and fetches rows; a missing row is
    404, limit above 1000 is rejected with 422 and POST /api/on-hand is
    405,
  * the migration downgrades and upgrades back to a single head.
"""

import os
from datetime import UTC, datetime

from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory

from app.db.connection import get_connection
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository

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


def test_issue9_freeform(client):
    repo = OnHandPostgresRepository()

    # 1. upsert_many inserts two rows in one call.
    assert repo.upsert_many(
        [
            {
                "item_code": "WIDGET-1",
                "site_code": "SITE-A",
                "quantity_on_hand": 120.0,
                "as_of": "2026-01-15T08:00:00Z",
                "source_system": "dynamics",
            },
            {
                "item_code": "WIDGET-2",
                "site_code": "SITE-A",
                "quantity_on_hand": -5.0,
                "as_of": "2026-01-15T08:00:00Z",
            },
        ]
    ) == {"inserted": 2, "updated": 0}

    # 2. A negative quantity IS stored: ERPs can report negative stock
    #    and the read model must not hide it.
    stored = repo.get_row("WIDGET-2", "SITE-A")
    assert stored["quantity_on_hand"] == -5.0
    one = client.get(
        "/api/on-hand/by-key", params={"item_code": "WIDGET-2", "site_code": "SITE-A"}
    )
    assert one.status_code == 200, one.text
    assert one.json()["quantity_on_hand"] == -5.0

    # 3. Re-upserting with a new quantity_on_hand and a later as_of counts
    #    as one update and stores both new values.
    assert repo.upsert_many(
        [
            {
                "item_code": "WIDGET-1",
                "site_code": "SITE-A",
                "quantity_on_hand": 90.0,
                "as_of": "2026-01-16T08:00:00Z",
                "source_system": "dynamics",
            }
        ]
    ) == {"inserted": 0, "updated": 1}
    stored = repo.get_row("WIDGET-1", "SITE-A")
    assert stored["quantity_on_hand"] == 90.0
    assert stored["as_of"] == datetime(2026, 1, 16, 8, 0, tzinfo=UTC).isoformat()

    # 4. Last-fresh-wins by as_of: an older snapshot cannot overwrite the
    #    newer current row, while a genuinely new key in the same batch is
    #    still inserted.
    assert repo.upsert_many(
        [
            {
                "item_code": "WIDGET-1",
                "site_code": "SITE-A",
                "quantity_on_hand": 12.0,
                "as_of": "2026-01-14T08:00:00Z",
            },
            {
                "item_code": "WIDGET-1",
                "site_code": "SITE-B",
                "quantity_on_hand": 7.0,
                "as_of": "2026-01-16T08:00:00Z",
            },
        ]
    ) == {"inserted": 1, "updated": 0}
    stored = repo.get_row("WIDGET-1", "SITE-A")
    assert stored["quantity_on_hand"] == 90.0
    assert stored["as_of"] == datetime(2026, 1, 16, 8, 0, tzinfo=UTC).isoformat()
    assert repo.get_row("WIDGET-1", "SITE-B")["quantity_on_hand"] == 7.0

    # 5. Idempotent: re-sending the current snapshot keeps every stored
    #    value.
    assert repo.upsert_many(
        [
            {
                "item_code": "WIDGET-1",
                "site_code": "SITE-A",
                "quantity_on_hand": 90.0,
                "as_of": "2026-01-16T08:00:00Z",
                "source_system": "dynamics",
            }
        ]
    ) == {"inserted": 0, "updated": 1}
    stored = repo.get_row("WIDGET-1", "SITE-A")
    assert stored["quantity_on_hand"] == 90.0
    assert stored["as_of"] == datetime(2026, 1, 16, 8, 0, tzinfo=UTC).isoformat()
    assert stored["source_system"] == "dynamics"

    # 6. GET /api/on-hand lists the stored rows ordered by
    #    (item_code, site_code); filters narrow them.
    listed = client.get("/api/on-hand")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert [(row["item_code"], row["site_code"]) for row in body] == [
        ("WIDGET-1", "SITE-A"),
        ("WIDGET-1", "SITE-B"),
        ("WIDGET-2", "SITE-A"),
    ]
    assert body[0]["quantity_on_hand"] == 90.0
    assert body[2]["quantity_on_hand"] == -5.0
    assert body[0]["id"]
    assert body[0]["synced_at"]
    assert body[0]["as_of"] == datetime(2026, 1, 16, 8, 0, tzinfo=UTC).isoformat()

    by_item = client.get("/api/on-hand", params={"item_code": "WIDGET-2"})
    assert by_item.status_code == 200, by_item.text
    assert [(row["item_code"], row["site_code"]) for row in by_item.json()] == [
        ("WIDGET-2", "SITE-A")
    ]

    by_both = client.get(
        "/api/on-hand", params={"item_code": "WIDGET-1", "site_code": "SITE-B"}
    )
    assert by_both.status_code == 200, by_both.text
    assert [row["site_code"] for row in by_both.json()] == ["SITE-B"]

    none = client.get("/api/on-hand", params={"item_code": "NO-SUCH-ITEM"})
    assert none.status_code == 200
    assert none.json() == []

    # 7. A missing (item_code, site_code) answers 404.
    assert (
        client.get(
            "/api/on-hand/by-key",
            params={"item_code": "NO-SUCH-ITEM", "site_code": "SITE-Z"},
        ).status_code
        == 404
    )

    # 8. A limit above 1000 is rejected with 422.
    assert client.get("/api/on-hand", params={"limit": 1001}).status_code == 422

    # NUL bytes in filter values are rejected before reaching the database.
    nul_guarded = client.get(
        "/api/on-hand", params={"item_code": "a\0b", "site_code": "S\0A"}
    )
    assert nul_guarded.status_code == 422
    assert nul_guarded.json()["detail"] == (
        "on-hand filters must not contain NUL bytes"
    )

    # 9. The router is read-only: POST /api/on-hand answers 405, as do the
    #    other write methods.
    assert (
        client.post(
            "/api/on-hand",
            json={
                "item_code": "WIDGET-9",
                "site_code": "SITE-A",
                "quantity_on_hand": 1.0,
                "as_of": "2026-01-17T08:00:00Z",
            },
        ).status_code
        == 405
    )
    for method in ("PUT", "PATCH", "DELETE"):
        assert client.request(method, "/api/on-hand").status_code == 405

    # 10. The migration downgrades and upgrades back to a single head.
    cfg = _alembic_config()
    alembic_command.downgrade(cfg, "0004_lead_time_observations")
    assert _alembic_revision() == "0004_lead_time_observations"
    assert "on_hand" not in _table_names()
    alembic_command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert _alembic_revision() == heads[0]
    assert "on_hand" in _table_names()
