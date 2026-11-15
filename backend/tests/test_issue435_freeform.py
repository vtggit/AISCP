"""Issue #435 proof: stored scenarios — named what-if lever sets, backend.

AC-1 — ``migrations/versions/0016_scenarios.py`` creates the
``scenarios`` table at head: ``id`` (text, primary key), ``name`` (text,
not null), ``service_level`` / ``demand_multiplier`` /
``lead_time_multiplier`` (DOUBLE PRECISION, nullable — a null lever
means "keep the baseline value", as in ``POST /api/scenarios/simulate``)
and ``created_at`` / ``updated_at`` (TIMESTAMPTZ, not null); the
downgrade to 0015_forecast_snapshot_effective drops the table and the
upgrade back to head restores it, cleanly.

AC-2 — ``app.models.scenarios`` holds ``ScenarioCreate`` (``name`` a
non-blank string of at most 200 characters; the three levers optional
floats), ``ScenarioUpdate`` (every field optional) and
``ScenarioResponse`` (``id``, ``name``, the three levers, ``created_at``
and ``updated_at`` as ISO-8601 strings);
``app.repositories.scenarios_postgres_repository.ScenarioPostgresRepository``
offers ``create``, ``list_all`` (newest ``created_at`` first),
``get_by_id``, ``update`` (only the given fields, bumping
``updated_at``) and ``delete``.

AC-3 — ``app.api.scenarios`` gains CRUD routes beside the existing
``POST /api/scenarios/simulate``, Bearer-authenticated by the mount-time
gate like the other routers: ``POST /api/scenarios`` (201 on create),
``GET /api/scenarios`` (newest first), ``GET /api/scenarios/{id}``,
``PUT /api/scenarios/{id}`` and ``DELETE /api/scenarios/{id}`` (204), a
404 for an unknown id on get/update/delete.  Any given lever is
validated by the engine's ``app.scenario.simulate._validate_levers`` —
a ``service_level`` not strictly between 0 and 1, or a non-positive or
non-finite multiplier, is a 422 — and a blank or explicitly null
``name`` is a 422.  The
simulate route and its behaviour are unchanged, and the OpenAPI
document (``backend/openapi.json``) describes the new routes and
models.

AC-4/5/6 — this proof runs against the test database and checks: the
migration upgrading and downgrading cleanly; ``POST /api/scenarios``
creating a scenario with levers and returning it with an ``id`` and
ISO-8601 timestamps (201 Created), and a create with no levers storing
all three null; a blank name (empty and whitespace-only) and an
out-of-range lever (``service_level`` 1.5, ``demand_multiplier`` 0, and
more) each a 422 with no row stored; ``GET /api/scenarios`` listing
newest ``created_at`` first; ``GET /api/scenarios/{id}`` returning one
scenario and 404 for an unknown id; ``PUT /api/scenarios/{id}``
changing only the given fields and bumping ``updated_at`` — a
documented partial update where an explicit null for a lever clears the
saved lever to baseline while omitted levers remain unchanged, and an
explicit null name is a 422 (a stored name is never null) — and 404
for an unknown id; ``DELETE /api/scenarios/{id}`` returning 204 with
the row then absent and 404 for an unknown id; every route answering
401 without a token; and a saved scenario storing only its levers —
the table has exactly the seven columns above, no result snapshot
columns.  Hostile inputs (NUL bytes, control characters, huge and
non-numeric values) never produce a 500.
"""

import json
import os
import time
from datetime import datetime
from pathlib import Path

import psycopg2
import psycopg2.extras
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASE_REVISION = "0015_forecast_snapshot_effective"
_HEAD_REVISION = "0022_lead_time_promised_on"

# The exact shape of the scenarios table: name + the three levers +
# the two timestamps — no result snapshot columns.
_EXPECTED_COLUMNS = {
    "id": ("text", "NO"),
    "name": ("text", "NO"),
    "service_level": ("double precision", "YES"),
    "demand_multiplier": ("double precision", "YES"),
    "lead_time_multiplier": ("double precision", "YES"),
    "created_at": ("timestamp with time zone", "NO"),
    "updated_at": ("timestamp with time zone", "NO"),
}


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


def _table_exists(table: str) -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename = %s",
                (table,),
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _scenario_columns() -> dict:
    """{column: (data_type, is_nullable)} of the scenarios table."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'scenarios' "
                "ORDER BY ordinal_position"
            )
            return {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    finally:
        conn.close()


def _primary_key_columns(table: str) -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints AS tc "
                "JOIN information_schema.key_column_usage AS kcu "
                "  ON tc.constraint_name = kcu.constraint_name "
                " AND tc.table_schema = kcu.table_schema "
                "WHERE tc.constraint_type = 'PRIMARY KEY' "
                "  AND tc.table_schema = 'public' AND tc.table_name = %s "
                "ORDER BY kcu.ordinal_position",
                (table,),
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _scenario_count() -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM scenarios")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _stored_row(scenario_id: str):
    """The raw stored row (datetimes untouched), or None."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM scenarios WHERE id = %s", (scenario_id,))
            row = cur.fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def _iso(value: str) -> datetime:
    """Parse an ISO-8601 timestamp; raises on anything else."""
    parsed = datetime.fromisoformat(value)
    assert parsed.tzinfo is not None, f"timestamp not timezone-aware: {value}"
    return parsed


def test_issue435_freeform(client):
    # ------------------------------------------------------------------
    # AC-1: the migration upgrades and downgrades cleanly.
    # ------------------------------------------------------------------
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_exists("scenarios")
    assert _scenario_columns() == _EXPECTED_COLUMNS
    assert _primary_key_columns("scenarios") == ["id"]

    # A stored row, so the downgrade visibly drops its data too.
    response = client.post(
        "/api/scenarios",
        json={
            "name": "Migration row",
            "service_level": 0.97,
            "demand_multiplier": 1.2,
        },
    )
    assert response.status_code == 201, response.text
    mig_id = response.json()["id"]
    assert _scenario_count() == 1

    cfg = _alembic_config()
    try:
        alembic_command.downgrade(cfg, _BASE_REVISION)
        assert _alembic_revision() == _BASE_REVISION
        # The downgrade drops the table and its rows, nothing else.
        assert not _table_exists("scenarios")
    finally:
        alembic_command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert _alembic_revision() == CURRENT_HEAD
    assert _scenario_columns() == _EXPECTED_COLUMNS
    # The dropped row is gone with the table; the table is back, empty.
    assert _stored_row(mig_id) is None
    assert _scenario_count() == 0

    # ------------------------------------------------------------------
    # AC-2 + AC-5: POST creates a scenario with levers — 201 Created,
    # returned with its id and ISO-8601 timestamps, stored in the DB.
    # ------------------------------------------------------------------
    response = client.post(
        "/api/scenarios",
        json={
            "name": "Peak season",
            "service_level": 0.99,
            "demand_multiplier": 1.5,
            "lead_time_multiplier": 2.0,
        },
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert set(created) == {
        "id",
        "name",
        "service_level",
        "demand_multiplier",
        "lead_time_multiplier",
        "created_at",
        "updated_at",
    }
    assert isinstance(created["id"], str) and created["id"]
    assert created["name"] == "Peak season"
    assert created["service_level"] == 0.99
    assert created["demand_multiplier"] == 1.5
    assert created["lead_time_multiplier"] == 2.0
    created_at = _iso(created["created_at"])
    updated_at = _iso(created["updated_at"])
    assert created_at <= updated_at
    row = _stored_row(created["id"])
    assert row is not None
    # Only the levers are stored: the row has exactly the seven columns.
    assert set(row) == set(_EXPECTED_COLUMNS)
    assert row["name"] == "Peak season"
    assert row["service_level"] == 0.99
    assert row["demand_multiplier"] == 1.5
    assert row["lead_time_multiplier"] == 2.0
    assert isinstance(row["created_at"], datetime)
    assert isinstance(row["updated_at"], datetime)

    # A create with no levers stores all three null (baseline).
    response = client.post("/api/scenarios", json={"name": "Baseline"})
    assert response.status_code == 201, response.text
    baseline = response.json()
    assert baseline["service_level"] is None
    assert baseline["demand_multiplier"] is None
    assert baseline["lead_time_multiplier"] is None
    row = _stored_row(baseline["id"])
    assert row["service_level"] is None
    assert row["demand_multiplier"] is None
    assert row["lead_time_multiplier"] is None
    assert _scenario_count() == 2

    # ------------------------------------------------------------------
    # AC-3: 422s — blank names and out-of-range levers store nothing.
    # ------------------------------------------------------------------
    for body in (
        {"name": ""},
        {"name": "   "},
        {"name": "n" * 201},  # over the 200-character limit
        {"name": "Bad SL high", "service_level": 1.5},
        {"name": "Bad SL low", "service_level": 0.0},
        {"name": "Bad SL edge", "service_level": 1.0},
        {"name": "Bad demand zero", "demand_multiplier": 0},
        {"name": "Bad demand neg", "demand_multiplier": -1.0},
        {"name": "Bad type", "demand_multiplier": "abc"},
        {"name": "a\x00b"},  # NUL byte: 422, never a 500
    ):
        response = client.post("/api/scenarios", json=body)
        assert response.status_code == 422, (body, response.status_code, response.text)
    # A non-finite multiplier (JSON "Infinity"): 422, never a 500.
    response = client.post(
        "/api/scenarios",
        content=b'{"name": "Bad lead inf", "lead_time_multiplier": Infinity}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert _scenario_count() == 2

    # The 200-character boundary name is accepted.
    response = client.post("/api/scenarios", json={"name": "n" * 200})
    assert response.status_code == 201, response.text
    response.json()["id"]
    assert _scenario_count() == 3

    # ------------------------------------------------------------------
    # AC-2: GET lists newest created_at first.
    # ------------------------------------------------------------------
    time.sleep(0.02)
    response = client.post("/api/scenarios", json={"name": "Old one"})
    assert response.status_code == 201
    old_id = response.json()["id"]
    time.sleep(0.02)
    response = client.post("/api/scenarios", json={"name": "New one"})
    assert response.status_code == 201
    new_id = response.json()["id"]

    response = client.get("/api/scenarios")
    assert response.status_code == 200, response.text
    listed = response.json()
    assert [row["name"] for row in listed] == [
        "New one",
        "Old one",
        "n" * 200,
        "Baseline",
        "Peak season",
    ]
    for row in listed:
        assert set(row) == set(_EXPECTED_COLUMNS)
        _iso(row["created_at"])
        _iso(row["updated_at"])
    by_id = {row["id"]: row for row in listed}
    assert by_id[new_id]["name"] == "New one"
    assert by_id[old_id]["name"] == "Old one"

    # ------------------------------------------------------------------
    # AC-2: GET by id returns one; 404 for an unknown id; hostile ids
    # never 500.
    # ------------------------------------------------------------------
    response = client.get(f"/api/scenarios/{created['id']}")
    assert response.status_code == 200, response.text
    assert response.json() == created

    response = client.get("/api/scenarios/no-such-scenario")
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "scenario not found"}
    # A NUL byte in the id (percent-encoded on the wire): 422, never 500.
    response = client.get("/api/scenarios/a%00b")
    assert response.status_code == 422, response.text
    response = client.get(f"/api/scenarios/{'x' * 60000}")  # huge: 404
    assert response.status_code == 404, response.text

    # ------------------------------------------------------------------
    # AC-6: PUT is a documented partial update — only the given fields
    # change and updated_at is bumped; 404 for an unknown id.
    # ------------------------------------------------------------------
    time.sleep(0.02)
    response = client.put(
        f"/api/scenarios/{created['id']}", json={"demand_multiplier": 3.0}
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    assert updated["demand_multiplier"] == 3.0
    # The omitted fields remain unchanged...
    assert updated["name"] == "Peak season"
    assert updated["service_level"] == 0.99
    assert updated["lead_time_multiplier"] == 2.0
    assert updated["created_at"] == created["created_at"]
    # ...and updated_at was bumped.
    assert _iso(updated["updated_at"]) > _iso(created["updated_at"])
    first_update_at = updated["updated_at"]

    # An explicit null for a lever clears it to baseline (null), while
    # the omitted levers remain unchanged.
    time.sleep(0.02)
    response = client.put(
        f"/api/scenarios/{created['id']}", json={"service_level": None}
    )
    assert response.status_code == 200, response.text
    cleared = response.json()
    assert cleared["service_level"] is None
    assert cleared["demand_multiplier"] == 3.0
    assert cleared["lead_time_multiplier"] == 2.0
    assert cleared["name"] == "Peak season"
    assert cleared["created_at"] == created["created_at"]
    assert _iso(cleared["updated_at"]) > _iso(first_update_at)
    row = _stored_row(created["id"])
    assert row["service_level"] is None
    assert row["demand_multiplier"] == 3.0
    assert row["lead_time_multiplier"] == 2.0

    time.sleep(0.02)
    response = client.put(
        f"/api/scenarios/{created['id']}", json={"name": "Peak season v2"}
    )
    assert response.status_code == 200, response.text
    renamed = response.json()
    assert renamed["name"] == "Peak season v2"
    assert renamed["service_level"] is None
    assert renamed["demand_multiplier"] == 3.0
    assert renamed["lead_time_multiplier"] == 2.0
    assert _iso(renamed["updated_at"]) > _iso(first_update_at)

    response = client.put("/api/scenarios/no-such-scenario", json={"name": "x"})
    assert response.status_code == 404, response.text
    assert response.json() == {"detail": "scenario not found"}
    # Out-of-range levers, blank names and an explicit null name are
    # 422 on update too — never a 500.
    for body in (
        {"service_level": 1.5},
        {"demand_multiplier": 0},
        {"name": "   "},
        {"name": "a\x00b"},
        {"name": None},  # explicit null name: 422, never a 500
    ):
        response = client.put(f"/api/scenarios/{created['id']}", json=body)
        assert response.status_code == 422, (body, response.text)
    response = client.put(
        f"/api/scenarios/{created['id']}",
        content=b'{"lead_time_multiplier": Infinity}',
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422, response.text
    # None of the rejected updates touched the row — in particular the
    # explicit null name left the stored name intact (no 500, no NULL).
    row = _stored_row(created["id"])
    assert row["name"] == "Peak season v2"
    assert row["service_level"] is None
    assert row["demand_multiplier"] == 3.0
    assert row["lead_time_multiplier"] == 2.0

    # ------------------------------------------------------------------
    # AC-2: DELETE returns 204 and the row is then absent; 404 for an
    # unknown id.
    # ------------------------------------------------------------------
    response = client.delete(f"/api/scenarios/{baseline['id']}")
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert _stored_row(baseline["id"]) is None
    assert _scenario_count() == 4  # 5 created, 1 deleted
    response = client.get(f"/api/scenarios/{baseline['id']}")
    assert response.status_code == 404, response.text
    response = client.delete(f"/api/scenarios/{baseline['id']}")
    assert response.status_code == 404, response.text
    response = client.delete("/api/scenarios/no-such-scenario")
    assert response.status_code == 404, response.text

    # A hostile name (control characters) is stored, not a 500.
    response = client.post("/api/scenarios", json={"name": "ctrl \x01\x07 char"})
    assert response.status_code == 201, response.text
    ctrl_id = response.json()["id"]
    assert _stored_row(ctrl_id)["name"] == "ctrl \x01\x07 char"
    assert client.delete(f"/api/scenarios/{ctrl_id}").status_code == 204

    # ------------------------------------------------------------------
    # AC-3: every route answers 401 without a token.
    # ------------------------------------------------------------------
    with TestClient(app) as anonymous:
        responses = [
            anonymous.post("/api/scenarios", json={"name": "x"}),
            anonymous.get("/api/scenarios"),
            anonymous.get(f"/api/scenarios/{created['id']}"),
            anonymous.put(f"/api/scenarios/{created['id']}", json={"name": "x"}),
            anonymous.delete(f"/api/scenarios/{created['id']}"),
            anonymous.post("/api/scenarios/simulate", json={}),
        ]
    for response in responses:
        assert response.status_code == 401, (response.request, response.text)

    # ------------------------------------------------------------------
    # AC-3: the OpenAPI document describes the new routes and models,
    # and the simulate route is unchanged.
    # ------------------------------------------------------------------
    spec = json.loads(
        (Path(__file__).resolve().parents[1] / "openapi.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(spec["paths"]["/api/scenarios"]) == {"post", "get"}
    assert set(spec["paths"]["/api/scenarios/{scenario_id}"]) == {
        "get",
        "put",
        "delete",
    }
    create_op = spec["paths"]["/api/scenarios"]["post"]
    assert (
        create_op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ScenarioCreate"
    )
    assert (
        create_op["responses"]["201"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ScenarioResponse"
    )
    list_op = spec["paths"]["/api/scenarios"]["get"]
    assert (
        list_op["responses"]["200"]["content"]["application/json"]["schema"]["items"][
            "$ref"
        ]
        == "#/components/schemas/ScenarioResponse"
    )
    detail = spec["paths"]["/api/scenarios/{scenario_id}"]
    for method in ("get", "put"):
        assert (
            detail[method]["responses"]["200"]["content"]["application/json"]["schema"][
                "$ref"
            ]
            == "#/components/schemas/ScenarioResponse"
        )
        assert "404" in detail[method]["responses"]
    assert (
        detail["put"]["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/ScenarioUpdate"
    )
    assert "204" in detail["delete"]["responses"]
    assert "404" in detail["delete"]["responses"]
    for model in ("ScenarioCreate", "ScenarioUpdate", "ScenarioResponse"):
        assert model in spec["components"]["schemas"], model
    # The simulate route and its models are described exactly as before.
    simulate_op = spec["paths"]["/api/scenarios/simulate"]["post"]
    assert (
        simulate_op["requestBody"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/SimulateBody"
    )
    assert (
        simulate_op["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/SimulateResponse"
    )
    for model in (
        "SimulateBody",
        "SimulateResponse",
        "SimulateSummary",
        "SimulateItemSite",
        "SimulateBasis",
        "SimulateLevers",
    ):
        assert model in spec["components"]["schemas"], model

    # ------------------------------------------------------------------
    # AC-3: the simulate route and its behaviour are unchanged.
    # ------------------------------------------------------------------
    response = client.post("/api/scenarios/simulate", json={})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert set(payload) == {"summary", "item_sites"}
    assert payload["item_sites"] == []
    assert payload["summary"]["item_sites"] == 0
    assert payload["summary"]["reorder_point_units_delta_total"] == 0
    assert payload["summary"]["safety_stock_units_delta_total"] == 0
    assert payload["summary"]["dollar_impact"] == 0.0
    assert payload["summary"]["levers"] == {
        "service_level": None,
        "demand_multiplier": None,
        "lead_time_multiplier": None,
    }
    # An out-of-range lever is still a 422, as before.
    response = client.post("/api/scenarios/simulate", json={"service_level": 1.5})
    assert response.status_code == 422, response.text
