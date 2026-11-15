"""Issue #779 proof: the ddmrp_adjustments table, its API and its
effect on the zones.

Seeds two item-sites with known demand and designates two buffers
the way ``test_issue740_freeform`` does (360 days of steady daily
demand ending the day before today, three 8/10/12-day observed lead
times, ``PUT /api/ddmrp/buffers``), and proves, against the live app
and the migrated test database:

* the migration — ``0030_ddmrp_status_snapshots`` (revision after
  ``0028_open_sales_lines``) is the only alembic head and is applied;
  it creates ``ddmrp_adjustments`` with exactly the documented
  columns (String(36) ``id`` the primary key, String(64)
  ``item_code``/``site_code`` nullable, Date ``start_on``/``end_on``
  not null, Float ``factor`` not null, text ``note`` nullable,
  String(255) ``created_by`` not null, String(255)
  ``created_by_name`` nullable, timestamptz ``created_at`` not null
  defaulting to now()), owned by the connecting user with no
  explicit grants and no foreign keys; an insert violating each of
  the three CHECK constraints — one code without the other, ``end_on``
  before ``start_on``, a factor outside (0, 10] — is rejected by the
  database; the downgrade drops the table and the upgrade back to
  head re-creates it cleanly;
* the routes — an admin ``POST /api/ddmrp/adjustments`` of an
  all-buffer factor 1.5 covering the as-of date answers 201 with the
  stored row (created_by the admin token's subject, created_by_name
  its display name); a second all-buffer window that shares a
  boundary date with the stored one answers 409 with a stable
  machine-readable detail (a fixed structure naming the conflicting
  adjustment, no database constraint text); an item-site factor 0.5
  for the first buffer answers 201 (a different scope, no conflict);
  a planner ``POST`` answers 403; factor 0, factor 11, a reversed
  window, one code without the other, an empty code, a NUL byte,
  and a factor that is not a decimal number each answer 422; an
  unknown pair answers 404; a factor of exactly 10 (the inclusive
  bound) answers 201; and a burst of eight concurrent same-scope
  POSTs of one shared window stores exactly one row (one 201, the
  rest 409) — the overlap check and the store hold one transaction,
  serialized per scope, so the check never races the insert;
  ``GET /api/ddmrp/adjustments`` (any signed-in user) lists them
  ordered by start_on, with the all-buffer rows first on equal
  dates; ``DELETE /api/ddmrp/adjustments/{id}`` (admin only) answers
  204 and a second ``DELETE`` answers 404;
* the effect — the first buffer's zone row reads ``adjustment_factor``
  0.5 and ``adu`` = ``adu_base`` x 0.5 (the item-site factor wins
  over the all-buffer one) with its zone tops the ``buffer_zones`` of
  the adjusted ADU; the second buffer's reads 1.5 (the all-buffer
  factor) the same way; the plan rows inherit ``adu_base`` and
  ``adjustment_factor`` through the shared zone rows, without
  omission; an empty-string item-site scope stored straight in the
  database never collides with the all-buffer scope (the zone rows
  key the stored values apart); after the ``DELETE`` of the item-site
  adjustment the
  first buffer reads 1.5, and an adjustment whose window ends before
  the as-of date has no effect;
* no adjustments — with none stored, every zone row carries
  ``adu_base`` = ``adu`` and ``adjustment_factor`` 1.0, and every
  number is the unadjusted one (the first buffer reads exactly the
  numbers ``test_issue740_freeform``'s "ok" row reads);
* the schema file — ``backend/openapi.json`` lists the
  ``/api/ddmrp/adjustments`` path with its operations and the
  ``DdmrpAdjustment``/``DdmrpAdjustmentBody`` models, documents the
  factor, in both models, as a decimal number with its explicit
  decimal precision (``format`` decimal, precision 12, scale 6) and,
  in the body model, its explicit bounds (strictly above 0, at most
  10), and carries ``adu_base`` and ``adjustment_factor`` on the
  zone and the plan row models;
* the identity requirement: the proof also runs as a NON-ADMIN (role
  ``user``) — a second pass in the same test after a full reload,
  with the page booted the production way (the token it would take
  from the URL hash as ``#access_token=...`` — the development
  token, role "user" — sent as its Bearer token), ``GET
  /api/auth/config`` answering publicly and ``GET /api/auth/me``
  resolving the viewer, sees the admin-only controls (the
  ``POST``/``DELETE`` of ``/api/ddmrp/adjustments``) absent for that
  caller (403) while the page still renders (200), and no token is
  still a 401.
"""

import asyncio
import json
import os
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import httpx
import psycopg2
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.ddmrp.zones import ZONE_FIELDS, buffer_zones
from app.main import app
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

URL = "/api/ddmrp/adjustments"
ZONES_URL = "/api/ddmrp/zones"
PLAN_URL = "/api/ddmrp/plan"
BUFFERS_URL = "/api/ddmrp/buffers"

HEAD = CURRENT_HEAD
PREV_HEAD = "0028_open_sales_lines"

ADMIN_TOKEN = "dev-secret-token:admin"
PLANNER_TOKEN = "dev-secret-token:user"
VIEWER_TOKEN = "dev-secret-token"  # the exact development token: role "user"

ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
ADMIN_NAME = "admin"  # display_name of the admin stub user
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

ITEM_A = "ITEM-779A"  # designated: 2.0/day smooth, 10-day observed lead time
SITE_A = "SITE-779-1"
ITEM_B = "ITEM-779B"  # designated: 3.0/day smooth, 10-day observed lead time
SITE_B = "SITE-779-2"

# column -> (data_type, character_maximum_length, is_nullable)
EXPECTED_SHAPE = {
    "id": ("character varying", 36, "NO"),
    "item_code": ("character varying", 64, "YES"),
    "site_code": ("character varying", 64, "YES"),
    "start_on": ("date", None, "NO"),
    "end_on": ("date", None, "NO"),
    "factor": ("double precision", None, "NO"),
    "note": ("text", None, "YES"),
    "created_by": ("character varying", 255, "NO"),
    "created_by_name": ("character varying", 255, "YES"),
    "created_at": ("timestamp with time zone", None, "NO"),
}

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


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


def _table_columns() -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'ddmrp_adjustments' "
                "ORDER BY column_name"
            )
            return {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}
    finally:
        conn.close()


def _table_exists() -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_name = 'ddmrp_adjustments'"
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _table_meta() -> tuple:
    """(owner, explicit grants, check constraint names) of the table."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_get_userbyid(c.relowner), c.relacl IS NULL "
                "FROM pg_class c WHERE c.relname = 'ddmrp_adjustments'"
            )
            owner, no_grants = cur.fetchone()
            cur.execute(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'ddmrp_adjustments'::regclass "
                "AND contype = 'c' ORDER BY conname"
            )
            checks = [row[0] for row in cur.fetchall()]
            return owner, no_grants, checks
    finally:
        conn.close()


def _current_user() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _primary_key_columns() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT a.attname FROM pg_index i "
                "JOIN pg_attribute a ON a.attrelid = i.indrelid "
                "AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = 'ddmrp_adjustments'::regclass "
                "AND i.indisprimary "
                "ORDER BY a.attname"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _stored_rows() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM ddmrp_adjustments "
                "ORDER BY (item_code IS NOT NULL), start_on, end_on, id"
            )
            return [tuple(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _settings_map() -> dict:
    """The stored system settings, the same read the zone rows use."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT key, value FROM settings "
                "WHERE scope = 'system' AND owner = ''"
            )
            return {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()


def _direct_insert(**overrides) -> None:
    """Store one row in ddmrp_adjustments straight in the database."""
    row = {
        "id": str(uuid4()),
        "item_code": None,
        "site_code": None,
        "start_on": date(2026, 1, 1),
        "end_on": date(2026, 1, 31),
        "factor": 2.0,
        "note": None,
        "created_by": "direct-insert",
        "created_by_name": None,
    }
    row.update(overrides)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO ddmrp_adjustments "
                "(id, item_code, site_code, start_on, end_on, factor, "
                "note, created_by, created_by_name) "
                "VALUES (%(id)s, %(item_code)s, %(site_code)s, "
                "%(start_on)s, %(end_on)s, %(factor)s, %(note)s, "
                "%(created_by)s, %(created_by_name)s)",
                row,
            )
        conn.commit()
    finally:
        conn.close()


def _item_site(cur, item: str, site: str) -> None:
    """The item-site's current planning settings (the way test 740
    seeds its item-sites): unit cost 1.0, no master lead time (the
    observed lead time decides)."""
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, master_lead_time_days, "
        "source_system) VALUES (%s, %s, %s, 1.0, 25.0, 10.0, NULL, 'test')",
        (f"id-is-779-{item}", item, site),
    )


def _demand_events(cur, item: str, site: str, daily: dict) -> None:
    """One demand event per day of ``daily`` (the way test 740 seeds
    its demand events)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-779-{item}-{day.isoformat()}",
                f"de-779-{item}-{day.isoformat()}",
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
    10 (the way test 740 seeds its lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-779-{item}-{i}",
                f"lt-779-{item}-{i}",
                f"SUP-779-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def _zones_rows() -> list:
    reader = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    return reader.get(ZONES_URL).json()["rows"]


def _concurrent_admin_posts(payload: dict, times: int) -> list:
    """The status codes of ``times`` concurrent admin POSTs of
    ``payload`` against the live app.

    Each request runs on its own connection, and the app's sync
    routes run in worker threads, so the stores truly race the way
    several API workers would: the overlap check and the insert hold
    one transaction per scope, so a shared window of one scope
    stores exactly once.
    """
    headers = {"Authorization": f"Bearer {ADMIN_TOKEN}"}

    async def _one() -> int:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as call:
            response = await call.post(URL, json=payload, headers=headers)
            return response.status_code

    async def _burst() -> list:
        return await asyncio.gather(*(_one() for _ in range(times)))

    return asyncio.run(_burst())


def test_issue779_freeform(client, admin_token, user_token, monkeypatch):
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    planner = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    # The recommendation inputs of the zone rows: the registry
    # defaults, never ambient environment values.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # --- 1. The migration: the only head, applied; the documented
    #     shape; each CHECK constraint rejects a violating insert;
    #     the downgrade drops the table, the upgrade re-creates it.
    cfg = _alembic_config()
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert heads[0] == HEAD
    assert _alembic_revision() == HEAD
    assert _table_columns() == EXPECTED_SHAPE
    assert _primary_key_columns() == ["id"]
    owner, no_grants, checks = _table_meta()
    assert owner == _current_user(), "the migration changes no ownership"
    assert no_grants, "the migration grants nothing"
    assert checks == [
        "ck_ddmrp_adjustments_factor",
        "ck_ddmrp_adjustments_scope",
        "ck_ddmrp_adjustments_window",
    ]

    # A well-formed row stores (created_at takes the server default).
    _direct_insert()
    assert len(_stored_rows()) == 1

    # The three CHECK constraints each reject a violating insert.
    for bad in (
        {"item_code": "ONLY-ITEM", "site_code": None},  # scope
        {"item_code": None, "site_code": "ONLY-SITE"},  # scope
        {"start_on": date(2026, 1, 15), "end_on": date(2026, 1, 14)},  # window
        {"factor": 0.0},  # factor at the lower bound
        {"factor": 10.5},  # factor above the upper bound
    ):
        with pytest.raises(psycopg2.IntegrityError):
            _direct_insert(**bad)
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ddmrp_adjustments")
        conn.commit()
    finally:
        conn.close()
    assert _stored_rows() == []

    try:
        alembic_command.downgrade(cfg, PREV_HEAD)
        assert _alembic_revision() == PREV_HEAD
        assert not _table_exists()
    finally:
        alembic_command.upgrade(cfg, "head")
    assert _alembic_revision() == HEAD
    assert _table_columns() == EXPECTED_SHAPE
    assert _primary_key_columns() == ["id"]

    # --- 2. Two designated buffers with known zones (the way
    #     test 740 does): the as-of date is the day before today,
    #     the day the latest demand event is seeded.
    as_of = date.today() - timedelta(days=1)
    daily_a = {as_of - timedelta(days=offset): 2.0 for offset in range(360)}
    daily_b = {as_of - timedelta(days=offset): 3.0 for offset in range(360)}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _item_site(cur, ITEM_A, SITE_A)
            _item_site(cur, ITEM_B, SITE_B)
            _demand_events(cur, ITEM_A, SITE_A, daily_a)
            _demand_events(cur, ITEM_B, SITE_B, daily_b)
            _lead_times(cur, ITEM_A, as_of - timedelta(days=30))
            _lead_times(cur, ITEM_B, as_of - timedelta(days=30))
        conn.commit()
    finally:
        conn.close()

    response = admin.put(
        BUFFERS_URL, json={"item_code": ITEM_A, "site_code": SITE_A, "note": "buffer"}
    )
    assert response.status_code == 200, response.text
    response = admin.put(
        BUFFERS_URL, json={"item_code": ITEM_B, "site_code": SITE_B, "note": "buffer"}
    )
    assert response.status_code == 200, response.text

    # --- 3. No adjustments: every zone row equals the unadjusted
    #     output — adu_base = adu, adjustment_factor 1.0, and the
    #     first buffer's numbers are exactly test 740's "ok" row's;
    #     the plan rows already carry the two new fields.
    response = client.get(ZONES_URL)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["as_of"] == as_of.isoformat()
    rows = data["rows"]
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (ITEM_A, SITE_A),
        (ITEM_B, SITE_B),
    ]
    row_a, row_b = rows
    assert row_a["status"] == "ok"
    assert row_a["adu_base"] == 2.0
    assert row_a["adjustment_factor"] == 1.0
    assert row_a["adu"] == 2.0
    assert row_a["dlt_days"] == 10.0
    assert row_a["dlt_source"] == "observed"
    assert row_a["demand_class"] == "smooth"
    assert row_a["cv"] == 0.0
    # test 740's "ok" row, unchanged: adu 2.0, dlt 10 (short, 0.8),
    # low variability (0.25), the EOQ order cycle 541/2.0 days.
    assert row_a["lead_time_factor"] == 0.8
    assert row_a["variability_factor"] == 0.25
    assert row_a["red_base"] == 16.0
    assert row_a["red_safety"] == 4.0
    assert row_a["red"] == 20.0
    assert row_a["yellow"] == 20.0
    assert row_a["order_cycle_days"] == 541.0 / 2.0
    assert row_a["green"] == 541.0
    assert row_a["top_of_red"] == 20
    assert row_a["top_of_yellow"] == 40
    assert row_a["top_of_green"] == 581
    assert row_a["arithmetic"]
    assert "\n" not in row_a["arithmetic"]
    assert row_b["status"] == "ok"
    assert row_b["adu_base"] == 3.0
    assert row_b["adjustment_factor"] == 1.0
    assert row_b["adu"] == 3.0
    assert row_b["dlt_days"] == 10.0
    assert row_b["demand_class"] == "smooth"
    for field in ZONE_FIELDS:
        assert (
            row_b[field]
            == buffer_zones(
                3.0, 10.0, row_b["order_cycle_days"], "smooth", 0.0, _settings_map()
            )[field]
        ), field
    cycle_a, cycle_b = row_a["order_cycle_days"], row_b["order_cycle_days"]

    # The plan rows inherit the two fields through the shared zone
    # rows (with no adjustment stored: 1.0 and adu_base = adu).
    response = client.get(PLAN_URL)
    assert response.status_code == 200, response.text
    plan_rows = {row["item_code"]: row for row in response.json()["rows"]}
    assert set(plan_rows) == {ITEM_A, ITEM_B}
    assert plan_rows[ITEM_A]["adu_base"] == 2.0
    assert plan_rows[ITEM_A]["adjustment_factor"] == 1.0
    assert plan_rows[ITEM_A]["adu"] == 2.0
    assert plan_rows[ITEM_B]["adu_base"] == 3.0
    assert plan_rows[ITEM_B]["adjustment_factor"] == 1.0
    assert plan_rows[ITEM_B]["adu"] == 3.0

    # --- 4. The routes. ------------------------------------------------
    # An admin POST of an all-buffer factor 1.5 covering the as-of
    # date answers 201 with the stored row.
    response = admin.post(
        URL,
        json={
            "start_on": (as_of - timedelta(days=30)).isoformat(),
            "end_on": (as_of + timedelta(days=30)).isoformat(),
            "factor": 1.5,
            "note": "every buffer",
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    all_buffer_id = body["id"]
    assert len(all_buffer_id) == 36
    assert body["item_code"] is None
    assert body["site_code"] is None
    assert body["start_on"] == (as_of - timedelta(days=30)).isoformat()
    assert body["end_on"] == (as_of + timedelta(days=30)).isoformat()
    assert body["factor"] == 1.5
    assert body["note"] == "every buffer"
    assert body["created_by"] == ADMIN_SUB
    assert body["created_by_name"] == ADMIN_NAME
    assert body["created_at"]

    # Any signed-in user lists the stored adjustment.
    response = planner.get(URL)
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [all_buffer_id]

    # A second all-buffer window sharing a boundary date overlaps:
    # 409 with a stable machine-readable detail, no database
    # constraint text, nothing stored.
    response = admin.post(
        URL,
        json={
            "start_on": (as_of + timedelta(days=30)).isoformat(),
            "end_on": (as_of + timedelta(days=60)).isoformat(),
            "factor": 2.0,
        },
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert set(detail) == {
        "code",
        "message",
        "scope",
        "item_code",
        "site_code",
        "overlapping_id",
        "overlapping_start_on",
        "overlapping_end_on",
    }
    assert detail["code"] == "ddmrp_adjustment_overlap"
    assert detail["scope"] == "all_buffers"
    assert detail["item_code"] is None
    assert detail["site_code"] is None
    assert detail["overlapping_id"] == all_buffer_id
    assert detail["overlapping_start_on"] == (as_of - timedelta(days=30)).isoformat()
    assert detail["overlapping_end_on"] == (as_of + timedelta(days=30)).isoformat()
    assert "ck_ddmrp" not in response.text
    assert "constraint" not in response.text.lower()
    assert "violates" not in response.text.lower()
    assert len(_stored_rows()) == 1

    # An item-site factor 0.5 for the first buffer answers 201: a
    # different scope never overlaps the all-buffer one.
    response = admin.post(
        URL,
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "start_on": (as_of - timedelta(days=10)).isoformat(),
            "end_on": (as_of + timedelta(days=10)).isoformat(),
            "factor": 0.5,
        },
    )
    assert response.status_code == 201, response.text
    body_a = response.json()
    item_a_id = body_a["id"]
    assert body_a["item_code"] == ITEM_A
    assert body_a["site_code"] == SITE_A
    assert body_a["factor"] == 0.5

    # A second item-site window for the same pair, sharing the stored
    # window's end date, overlaps within the pair's scope: 409 with
    # the stable detail naming the scope and the conflicting row.
    response = admin.post(
        URL,
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "start_on": (as_of + timedelta(days=10)).isoformat(),
            "end_on": (as_of + timedelta(days=20)).isoformat(),
            "factor": 0.25,
        },
    )
    assert response.status_code == 409, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "ddmrp_adjustment_overlap"
    assert detail["scope"] == "item_site"
    assert detail["item_code"] == ITEM_A
    assert detail["site_code"] == SITE_A
    assert detail["overlapping_id"] == item_a_id
    assert detail["overlapping_end_on"] == (as_of + timedelta(days=10)).isoformat()

    # A planner POST answers 403 (nothing stored).
    response = planner.post(
        URL,
        json={
            "start_on": (as_of + timedelta(days=100)).isoformat(),
            "end_on": (as_of + timedelta(days=110)).isoformat(),
            "factor": 1.25,
        },
    )
    assert response.status_code == 403, response.text
    assert len(_stored_rows()) == 2

    # The 422s: a factor outside (0, 10], a reversed window, one
    # code without the other, a NUL byte, and a factor that is not a
    # decimal number.
    far_start = (as_of + timedelta(days=100)).isoformat()
    far_end = (as_of + timedelta(days=110)).isoformat()
    for bad in (
        {"start_on": far_start, "end_on": far_end, "factor": 0},
        {"start_on": far_start, "end_on": far_end, "factor": 11},
        {"start_on": far_end, "end_on": far_start, "factor": 1.5},
        {"item_code": ITEM_A, "start_on": far_start, "end_on": far_end, "factor": 1.5},
        {"site_code": SITE_A, "start_on": far_start, "end_on": far_end, "factor": 1.5},
        {"start_on": far_start, "end_on": far_end, "factor": 1.5, "note": "a\x00b"},
        {
            "item_code": ITEM_A + "\x00",
            "site_code": SITE_A,
            "start_on": far_start,
            "end_on": far_end,
            "factor": 1.5,
        },
        {"start_on": far_start, "end_on": far_end, "factor": "1.5"},
        {"start_on": far_start, "end_on": far_end, "factor": True},
        {"start_on": far_start, "end_on": far_end, "factor": None},
        # An empty-string code answers 422, never 404: it must not
        # masquerade as the all-buffer scope (an empty pair would
        # share the all-buffer scope key).
        {
            "item_code": "",
            "site_code": "",
            "start_on": far_start,
            "end_on": far_end,
            "factor": 1.5,
        },
        {
            "item_code": "",
            "site_code": SITE_A,
            "start_on": far_start,
            "end_on": far_end,
            "factor": 1.5,
        },
    ):
        response = admin.post(URL, json=bad)
        assert response.status_code == 422, (bad, response.text)
    assert len(_stored_rows()) == 2

    # An unknown pair answers 404.
    response = admin.post(
        URL,
        json={
            "item_code": "GHOST-779",
            "site_code": "GHOST-SITE-779",
            "start_on": far_start,
            "end_on": far_end,
            "factor": 1.5,
        },
    )
    assert response.status_code == 404, response.text
    assert len(_stored_rows()) == 2

    # The inclusive upper bound: a factor of exactly 10 answers 201.
    response = admin.post(
        URL,
        json={"start_on": far_start, "end_on": far_end, "factor": 10},
    )
    assert response.status_code == 201, response.text
    factor10_id = response.json()["id"]
    assert response.json()["factor"] == 10.0

    # The overlap check is atomic: a burst of concurrent same-scope
    # POSTs of one shared window stores exactly one row — one 201,
    # the rest 409 — so the check can never race the insert.
    burst_start = (as_of + timedelta(days=150)).isoformat()
    burst_end = (as_of + timedelta(days=160)).isoformat()
    codes = _concurrent_admin_posts(
        {"start_on": burst_start, "end_on": burst_end, "factor": 2.0}, 8
    )
    assert codes.count(201) == 1, codes
    assert codes.count(409) == len(codes) - 1, codes
    burst_rows = [
        row for row in _stored_rows() if row[3] == as_of + timedelta(days=150)
    ]
    assert len(burst_rows) == 1, "one shared window stores exactly once"
    response = admin.delete(f"{URL}/{burst_rows[0][0]}")
    assert response.status_code == 204, response.text

    # --- 5. The effect: each designated pair's active factor on the
    #     as-of date scales its ADU — the item-site factor wins over
    #     the all-buffer one — through the existing buffer_zones.
    rows = _zones_rows()
    row_a, row_b = rows
    assert row_a["adu_base"] == 2.0
    assert row_a["adjustment_factor"] == 0.5
    assert row_a["adu"] == row_a["adu_base"] * 0.5
    assert row_a["adu"] == 1.0
    assert row_a["order_cycle_days"] == cycle_a, "the order cycle is unadjusted"
    for field in ZONE_FIELDS:
        assert (
            row_a[field]
            == buffer_zones(1.0, 10.0, cycle_a, "smooth", 0.0, _settings_map())[field]
        ), field
    assert row_b["adu_base"] == 3.0
    assert row_b["adjustment_factor"] == 1.5
    assert row_b["adu"] == row_b["adu_base"] * 1.5
    assert row_b["adu"] == 4.5
    assert row_b["order_cycle_days"] == cycle_b, "the order cycle is unadjusted"
    for field in ZONE_FIELDS:
        assert (
            row_b[field]
            == buffer_zones(4.5, 10.0, cycle_b, "smooth", 0.0, _settings_map())[field]
        ), field

    # An empty-string item-site scope stored straight in the database
    # never collides with the all-buffer scope: the zone rows key the
    # stored values apart, so neither buffer reads its factor.
    _direct_insert(
        item_code="",
        site_code="",
        start_on=as_of - timedelta(days=1),
        end_on=as_of + timedelta(days=1),
        factor=7.0,
    )
    rows = _zones_rows()
    row_a, row_b = rows
    assert row_a["adjustment_factor"] == 0.5
    assert (
        row_b["adjustment_factor"] == 1.5
    ), "the empty-string scope is not the all-buffer scope"
    assert row_b["adu"] == 4.5
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ddmrp_adjustments WHERE item_code = ''")
        conn.commit()
    finally:
        conn.close()

    # The plan rows carry the factor fields without omission.
    plan_rows = {row["item_code"]: row for row in client.get(PLAN_URL).json()["rows"]}
    assert plan_rows[ITEM_A]["adu_base"] == 2.0
    assert plan_rows[ITEM_A]["adjustment_factor"] == 0.5
    assert plan_rows[ITEM_A]["adu"] == 1.0
    assert plan_rows[ITEM_B]["adu_base"] == 3.0
    assert plan_rows[ITEM_B]["adjustment_factor"] == 1.5
    assert plan_rows[ITEM_B]["adu"] == 4.5

    # The listing is ordered by start_on, with the all-buffer rows
    # first on equal dates.
    listed = client.get(URL).json()
    assert [(row["item_code"], row["start_on"]) for row in listed] == [
        (None, (as_of - timedelta(days=30)).isoformat()),
        (ITEM_A, (as_of - timedelta(days=10)).isoformat()),
        (None, far_start),
    ]

    # --- 6. Delete and windows. -----------------------------------------
    # After the DELETE of the item-site adjustment, the first buffer
    # falls back to the all-buffer factor 1.5; a second DELETE answers
    # 404.
    response = admin.delete(f"{URL}/{item_a_id}")
    assert response.status_code == 204, response.text
    response = admin.delete(f"{URL}/{item_a_id}")
    assert response.status_code == 404, response.text
    rows = _zones_rows()
    row_a = next(row for row in rows if row["item_code"] == ITEM_A)
    assert row_a["adjustment_factor"] == 1.5
    assert row_a["adu"] == row_a["adu_base"] * 1.5
    assert row_a["adu"] == 3.0

    # An adjustment whose window ends before the as-of date has no
    # effect: the second buffer still reads the all-buffer 1.5.
    response = admin.post(
        URL,
        json={
            "item_code": ITEM_B,
            "site_code": SITE_B,
            "start_on": (as_of - timedelta(days=30)).isoformat(),
            "end_on": (as_of - timedelta(days=1)).isoformat(),
            "factor": 0.5,
        },
    )
    assert response.status_code == 201, response.text
    expired_id = response.json()["id"]
    rows = _zones_rows()
    row_b = next(row for row in rows if row["item_code"] == ITEM_B)
    assert row_b["adjustment_factor"] == 1.5, "the expired window has no effect"
    assert row_b["adu"] == 4.5

    # On the shared start date the all-buffer row lists first.
    listed = client.get(URL).json()
    assert [(row["item_code"], row["start_on"]) for row in listed[:2]] == [
        (None, (as_of - timedelta(days=30)).isoformat()),
        (ITEM_B, (as_of - timedelta(days=30)).isoformat()),
    ]

    # Remove every stored adjustment again.
    for adjustment_id in (all_buffer_id, factor10_id, expired_id):
        response = admin.delete(f"{URL}/{adjustment_id}")
        assert response.status_code == 204, response.text
    assert _stored_rows() == []
    assert client.get(URL).json() == []

    # --- 7. No adjustments again: every number is the unadjusted one.
    rows = _zones_rows()
    row_a, row_b = rows
    assert row_a["adu_base"] == row_a["adu"] == 2.0
    assert row_a["adjustment_factor"] == 1.0
    assert row_a["red_base"] == 16.0
    assert row_a["red"] == 20.0
    assert row_a["yellow"] == 20.0
    assert row_a["top_of_red"] == 20
    assert row_a["top_of_yellow"] == 40
    assert row_a["top_of_green"] == 581
    assert row_b["adu_base"] == row_b["adu"] == 3.0
    assert row_b["adjustment_factor"] == 1.0
    for field in ZONE_FIELDS:
        assert (
            row_b[field]
            == buffer_zones(3.0, 10.0, cycle_b, "smooth", 0.0, _settings_map())[field]
        ), field

    # --- 8. The schema file lists the routes, the models and the new
    #     fields; the factor is documented as a decimal with its
    #     explicit bounds.
    spec = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    assert set(spec["paths"][URL]) == {"get", "post"}
    assert set(spec["paths"][f"{URL}/{{adjustment_id}}"]) == {"delete"}
    assert (
        spec["paths"][URL]["post"]["requestBody"]["content"]["application/json"][
            "schema"
        ]["$ref"]
        == "#/components/schemas/DdmrpAdjustmentBody"
    )
    assert (
        spec["paths"][URL]["get"]["responses"]["200"]["content"]["application/json"][
            "schema"
        ]["items"]["$ref"]
        == "#/components/schemas/DdmrpAdjustment"
    )
    schemas = spec["components"]["schemas"]
    assert set(schemas["DdmrpAdjustment"]["properties"]) == {
        "id",
        "item_code",
        "site_code",
        "start_on",
        "end_on",
        "factor",
        "note",
        "created_by",
        "created_by_name",
        "created_at",
    }
    # The factor is documented, in both the row model and the body
    # model, as a decimal number with an explicit precision and
    # scale; the body model carries its explicit bounds on top.
    for model_name in ("DdmrpAdjustment", "DdmrpAdjustmentBody"):
        model_factor = schemas[model_name]["properties"]["factor"]
        assert model_factor["type"] == "number"
        assert model_factor["format"] == "decimal"
        assert model_factor["precision"] == 12
        assert model_factor["scale"] == 6
    factor_schema = schemas["DdmrpAdjustmentBody"]["properties"]["factor"]
    assert factor_schema["exclusiveMinimum"] == 0.0
    assert factor_schema["maximum"] == 10.0
    assert "decimal" in factor_schema.get("description", "").lower()
    assert schemas["DdmrpZoneRow"]["properties"]["adu_base"]["type"] == "number"
    assert (
        schemas["DdmrpZoneRow"]["properties"]["adjustment_factor"]["type"] == "number"
    )
    # The plan's response model carries the fields on every row.
    plan_ref = spec["paths"][PLAN_URL]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    plan_model = schemas[plan_ref.rsplit("/", 1)[-1]]
    plan_row = schemas[
        plan_model["properties"]["rows"]["items"]["$ref"].rsplit("/", 1)[-1]
    ]
    assert plan_row["properties"]["adu_base"]["type"] == "number"
    assert plan_row["properties"]["adjustment_factor"]["type"] == "number"
    assert "adu_base" in plan_row["required"]
    assert "adjustment_factor" in plan_row["required"]

    # --- 9. Identity: the proof also runs as a NON-ADMIN (role "user").
    #     Second pass in the same test after a full reload: the page
    #     boots the production way (the token it would take from the
    #     URL hash as #access_token=... is the development token, role
    #     "user", sent as its Bearer token); /api/auth/config answers
    #     publicly and /api/auth/me resolves the viewer; the
    #     admin-only controls are absent for that caller (403) while
    #     the page still renders, and no token is still a 401.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["user"]["sub"] == VIEWER_SUB
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]
    assert "admin" not in me_body["user"]["groups"]

    response = viewer.post(
        URL,
        json={"start_on": far_start, "end_on": far_end, "factor": 1.25},
    )
    assert response.status_code == 403, response.text
    response = viewer.delete(f"{URL}/{uuid4()}")
    assert response.status_code == 403, response.text
    assert _stored_rows() == []
    # The adjustments page still renders for the viewer (empty — the
    # viewer wrote nothing).
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == []
    response = anon_reloaded.get(URL)
    assert response.status_code == 401, response.text
    assert response.headers.get("www-authenticate") == "Bearer"
