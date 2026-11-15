"""Issue #780 proof: DDMRP status snapshots and the performance route.

AC-1 — against the live app and the migrated test database:
* the migration — ``0030_ddmrp_status_snapshots`` (revision after
  ``0029_ddmrp_adjustments``) is the only alembic head and is applied;
  it creates ``ddmrp_status_snapshots`` with exactly the documented
  columns (``snapshot_on`` Date and ``item_code``/``site_code``
  String(64), together the primary key; ``planning_zone`` and
  ``on_hand_status`` String(16); ``net_flow``, ``on_hand``,
  ``top_of_red``, ``top_of_yellow`` and ``top_of_green`` double
  precision; ``captured_at`` timestamptz defaulting to now(), every
  column not null) and an index on (``item_code``, ``site_code``,
  ``snapshot_on``).
* the capture — ``capture_ddmrp_snapshot`` builds the plan rows with
  the very same shared code ``GET /api/ddmrp/plan`` uses (``as_of``
  None: the plan's own as-of date), stores one row per plan row with
  the plan's zone, status and tops, and a second capture of the same
  date leaves one row per pair; with a named ``as_of`` date it stores
  under that date the rows of the plan built as of that date.
* the route — ``GET /api/ddmrp/performance?days=90`` (any signed-in
  user): with no snapshot rows it answers ``from``/``to`` null and
  ``rows`` empty; with snapshot rows for 10 days (4 red, 3 yellow, 2
  green and 1 over for one buffer) it answers ``days_observed`` 10 and
  the zone shares 40.0/30.0/20.0/10.0, each percentage rounded to one
  decimal, ordered by the red share descending, then item_code and
  site_code; the ``days`` window (7 to 730, default 90, anything else
  422) limits the days counted, ending on the latest ``snapshot_on``;
  and the route writes nothing.
* the schema file — ``backend/openapi.json`` lists the route (the
  ``days`` parameter 7 to 730, default 90) and its models.

AC-2 — the checks above, in this test: the head is
``0030_ddmrp_status_snapshots`` and the only head; the capture stores
one row per plan row and a second capture of the same date leaves one
row per pair; the route reports the 10-day buffer at 40.0/30.0/20.0/
10.0 with ``days_observed`` 10, ordered by the red share; the window
limits the days counted; ``days`` 3 answers 422 and no token answers
401.

AC-3 — the capture replaces all rows for its date: a buffer absent
from the plan on that date is removed from the snapshot, and
re-running the capture for the same date is idempotent.

Identity — the performance route is open to any signed-in user, so
the proof also runs as a NON-ADMIN (role "user"): a second pass in the
same test after a full reload, with the page booted the production way
(the token it would take from the URL hash as ``#access_token=...`` —
the development token, role "user" — sent as its Bearer token),
``GET /api/auth/config`` answering publicly and ``GET /api/auth/me``
resolving the viewer, sees the performance page still render (200 with
the same rows) while the admin-only controls (the ``PUT``/``DELETE``
of ``/api/ddmrp/buffers``) are absent for that caller (403), and no
token is still a 401.
"""

import json
import os
from datetime import date, timedelta
from pathlib import Path

import psycopg2
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.api.ddmrp import ddmrp_zone_rows
from app.db.connection import get_connection
from app.ddmrp.plan import plan_ddmrp
from app.ddmrp.snapshots import capture_ddmrp_snapshot
from app.main import app
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

PERF_URL = "/api/ddmrp/performance"
PLAN_URL = "/api/ddmrp/plan"
BUFFERS_URL = "/api/ddmrp/buffers"

HEAD = CURRENT_HEAD

SITE_A = "SITE-780-1"
SITE_B = "SITE-780-2"
SITE_C = "SITE-780-3"
ITEM_A = "ITEM-780A"  # on hand 10: "red" / "low" in the plan
ITEM_B = "ITEM-780B"  # on hand 5: "red" / "critical" in the plan
ITEM_C = "ITEM-780C"  # on hand 50: "green" / "ok" in the plan
DESIGNATED = (
    (ITEM_A, SITE_A),
    (ITEM_B, SITE_B),
    (ITEM_C, SITE_C),
)
ON_HAND = {
    (ITEM_A, SITE_A): 10.0,
    (ITEM_B, SITE_B): 5.0,
    (ITEM_C, SITE_C): 50.0,
}

# The 10 snapshot days of the route phase, oldest first: day 10 is the
# latest snapshot_on.  Buffer A: 4 red, 3 yellow, 2 green and 1 over.
DAYS_A = (
    ("red", "stocked_out"),
    ("red", "stocked_out"),
    ("red", "critical"),
    ("red", "critical"),
    ("yellow", "critical"),
    ("yellow", "low"),
    ("yellow", "low"),
    ("green", "ok"),
    ("green", "ok"),
    ("over", "ok"),
)
# Buffer B: 1 red, 4 yellow, 4 green and 1 over — the same red share
# as buffer C, so the item_code tiebreak orders it before C.
DAYS_B = (
    ("red", "stocked_out"),
    ("yellow", "low"),
    ("yellow", "low"),
    ("yellow", "low"),
    ("green", "ok"),
    ("over", "ok"),
    ("yellow", "low"),
    ("green", "ok"),
    ("green", "ok"),
    ("green", "ok"),
)
# Buffer C: 1 red and 9 yellow.
DAYS_C = (("red", "critical"),) + (("yellow", "low"),) * 9

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
ON_HAND_SQL = (
    "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
    "as_of, source_system) VALUES (%s, %s, %s, %s, %s, 'test')"
)
SNAPSHOT_SQL = (
    "INSERT INTO ddmrp_status_snapshots "
    "(snapshot_on, item_code, site_code, planning_zone, on_hand_status, "
    "net_flow, on_hand, top_of_red, top_of_yellow, top_of_green) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
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


def _snapshot_columns() -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable FROM information_schema.columns "
                "WHERE table_name = 'ddmrp_status_snapshots' "
                "ORDER BY ordinal_position"
            )
            return {row[0]: (row[1], row[2], row[3]) for row in cur.fetchall()}
    finally:
        conn.close()


def _snapshot_pk() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT kcu.column_name "
                "FROM information_schema.table_constraints tc "
                "JOIN information_schema.key_column_usage kcu "
                "ON tc.constraint_name = kcu.constraint_name "
                "AND tc.table_catalog = kcu.table_catalog "
                "AND tc.table_schema = kcu.table_schema "
                "WHERE tc.table_name = 'ddmrp_status_snapshots' "
                "AND tc.constraint_type = 'PRIMARY KEY' "
                "ORDER BY kcu.ordinal_position"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _snapshot_indexes() -> list:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'ddmrp_status_snapshots'"
            )
            return [row[0] for row in cur.fetchall()]
    finally:
        conn.close()


def _stored_snapshots(snapshot_on: date) -> list:
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT item_code, site_code, planning_zone, on_hand_status, "
                "net_flow, on_hand, top_of_red, top_of_yellow, top_of_green "
                "FROM ddmrp_status_snapshots WHERE snapshot_on = %s "
                "ORDER BY item_code, site_code",
                (snapshot_on,),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _snapshot_count() -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM ddmrp_status_snapshots")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _seed(cur, item, site, as_of):
    """One designated item-site the way test 742 seeds its item-sites:
    the planning settings, smooth demand of 2.0 a day for 360 days
    ending on the as-of date, three 8/10/12-day lead-time
    observations and the pair's on-hand row."""
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, master_lead_time_days, "
        "source_system) VALUES (%s, %s, %s, 1.0, 25.0, 10.0, NULL, 'test')",
        (f"id-is-780-{item}", item, site),
    )
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-780-{item}-{offset}",
                f"de-780-{item}-{offset}",
                item,
                site,
                2.0,
                (as_of - timedelta(days=offset)).isoformat(),
                "shipment",
                "test",
            )
            for offset in range(360)
        ],
    )
    for i, lead in enumerate((8, 10, 12)):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-780-{item}-{i}",
                f"lt-780-{item}-{i}",
                f"SUP-780-{item}",
                item,
                (as_of - timedelta(days=30 + lead)).isoformat(),
                (as_of - timedelta(days=30)).isoformat(),
            ),
        )
    cur.execute(
        ON_HAND_SQL,
        (f"id-oh-780-{item}", item, site, ON_HAND[(item, site)], as_of),
    )


def _plan_rows_for(as_of_date: date) -> list:
    """The plan rows of the very same shared code the plan route uses,
    as of the named date."""
    _, zone_rows = ddmrp_zone_rows(as_of_date)
    return plan_ddmrp(as_of_date, zone_rows)["rows"]


def _snapshot_matches_plan(stored: list, plan_rows: list) -> bool:
    if len(stored) != len(plan_rows):
        return False
    expected = [
        {
            "item_code": row["item_code"],
            "site_code": row["site_code"],
            "planning_zone": row["planning_zone"],
            "on_hand_status": row["on_hand_status"],
            "net_flow": float(row["net_flow"]),
            "on_hand": float(row["on_hand"]),
            "top_of_red": float(row["top_of_red"]),
            "top_of_yellow": float(row["top_of_yellow"]),
            "top_of_green": float(row["top_of_green"]),
        }
        for row in plan_rows
    ]
    expected.sort(key=lambda row: (row["item_code"], row["site_code"]))
    return stored == expected


def test_issue780_freeform(client, admin_token, user_token):
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    anon = TestClient(app)

    as_of = date.today() - timedelta(days=1)

    # --- AC-1: the migration ------------------------------------------
    # The head is 0030_ddmrp_status_snapshots and the only head.
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    assert list(heads) == [HEAD], "the snapshot migration is the only head"
    assert _alembic_revision() == HEAD
    assert _snapshot_columns() == {
        "snapshot_on": ("date", None, "NO"),
        "item_code": ("character varying", 64, "NO"),
        "site_code": ("character varying", 64, "NO"),
        "planning_zone": ("character varying", 16, "NO"),
        "on_hand_status": ("character varying", 16, "NO"),
        "net_flow": ("double precision", None, "NO"),
        "on_hand": ("double precision", None, "NO"),
        "top_of_red": ("double precision", None, "NO"),
        "top_of_yellow": ("double precision", None, "NO"),
        "top_of_green": ("double precision", None, "NO"),
        "captured_at": ("timestamp with time zone", None, "NO"),
    }
    assert _snapshot_pk() == ["snapshot_on", "item_code", "site_code"]
    index_defs = _snapshot_indexes()
    assert any(
        "CREATE INDEX" in indexdef and "item_code, site_code, snapshot_on" in indexdef
        for indexdef in index_defs
    ), "the index on (item_code, site_code, snapshot_on) exists"

    # --- AC-1: the route with no snapshot rows -------------------------
    resp = client.get(PERF_URL)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"from": None, "to": None, "rows": []}

    # --- Seed: the designated buffers and their on-hand ----------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item, site in DESIGNATED:
                _seed(cur, item, site, as_of)
        conn.commit()
    finally:
        conn.close()

    # Designate the item-sites the way test 742 designates them.
    for item, site in DESIGNATED:
        resp = admin.put(
            BUFFERS_URL, json={"item_code": item, "site_code": site, "note": "buffer"}
        )
        assert resp.status_code == 200, resp.text

    # The plan the capture must mirror.
    resp = client.get(PLAN_URL)
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    as_of_date = date.fromisoformat(plan["as_of"])
    assert as_of_date == as_of
    assert len(plan["rows"]) == 3
    plan_by_key = {(row["item_code"], row["site_code"]): row for row in plan["rows"]}
    assert plan_by_key[(ITEM_A, SITE_A)]["planning_zone"] == "red"
    assert plan_by_key[(ITEM_A, SITE_A)]["on_hand_status"] == "low"
    assert plan_by_key[(ITEM_B, SITE_B)]["planning_zone"] == "red"
    assert plan_by_key[(ITEM_B, SITE_B)]["on_hand_status"] == "critical"
    assert plan_by_key[(ITEM_C, SITE_C)]["planning_zone"] == "green"
    assert plan_by_key[(ITEM_C, SITE_C)]["on_hand_status"] == "ok"

    # --- AC-1/AC-2: the capture -----------------------------------------
    result = capture_ddmrp_snapshot()
    assert result["snapshot_on"] == as_of_date
    assert len(result["rows"]) == 3
    stored = _stored_snapshots(as_of_date)
    assert len(stored) == 3, "one row per plan row"
    assert _snapshot_matches_plan(
        stored, plan["rows"]
    ), "the stored rows carry the plan's zone, status and tops"

    # A second capture of the same date: one row per pair, no
    # duplicates.
    result = capture_ddmrp_snapshot()
    assert result["snapshot_on"] == as_of_date
    stored_again = _stored_snapshots(as_of_date)
    assert len(stored_again) == 3
    assert stored_again == stored

    # With a named as-of date the capture stores under that date the
    # rows of the plan built as of that date.
    explicit = as_of_date - timedelta(days=1)
    result = capture_ddmrp_snapshot(explicit)
    assert result["snapshot_on"] == explicit
    assert _snapshot_matches_plan(_stored_snapshots(explicit), _plan_rows_for(explicit))
    assert _snapshot_count() == 6, "the two dates hold their rows side by side"

    # --- AC-3: the capture replaces all rows for its date ---------------
    # Buffer B loses its designation, so it is absent from the plan and
    # must leave the snapshot of the date.
    resp = admin.delete(BUFFERS_URL, params={"item_code": ITEM_B, "site_code": SITE_B})
    assert resp.status_code == 204, resp.text
    result = capture_ddmrp_snapshot()
    assert result["snapshot_on"] == as_of_date
    stored = _stored_snapshots(as_of_date)
    assert [(row["item_code"], row["site_code"]) for row in stored] == [
        (ITEM_A, SITE_A),
        (ITEM_C, SITE_C),
    ], "a buffer absent from the plan is removed from the date"
    # Re-running the capture for the same date is idempotent.
    capture_ddmrp_snapshot()
    assert _stored_snapshots(as_of_date) == stored

    # --- AC-1/AC-2: the route -------------------------------------------
    # Ten snapshot days ending on the latest snapshot_on: 4 red, 3
    # yellow, 2 green and 1 over for buffer A, 1 red for B and C.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ddmrp_status_snapshots")
            for item, site, days in (
                (ITEM_A, SITE_A, DAYS_A),
                (ITEM_B, SITE_B, DAYS_B),
                (ITEM_C, SITE_C, DAYS_C),
            ):
                for i, (zone, status) in enumerate(days, start=1):
                    cur.execute(
                        SNAPSHOT_SQL,
                        (
                            (as_of - timedelta(days=10 - i)).isoformat(),
                            item,
                            site,
                            zone,
                            status,
                            1.5,
                            2.5,
                            20.0,
                            40.0,
                            581.0,
                        ),
                    )
        conn.commit()
    finally:
        conn.close()

    counts_before = _snapshot_count()

    # The default window: the 90 days ending on the latest
    # snapshot_on.  Buffer A's shares are 40.0/30.0/20.0/10.0 and the
    # rows are ordered by the red share (B and C tie at 10.0, then
    # item_code).
    resp = client.get(PERF_URL)
    assert resp.status_code == 200, resp.text
    perf = resp.json()
    assert perf["from"] == (as_of - timedelta(days=89)).isoformat()
    assert perf["to"] == as_of.isoformat()
    assert [(row["item_code"], row["site_code"]) for row in perf["rows"]] == [
        (ITEM_A, SITE_A),
        (ITEM_B, SITE_B),
        (ITEM_C, SITE_C),
    ]
    row_a = perf["rows"][0]
    assert row_a["days_observed"] == 10
    assert (
        row_a["red_pct"],
        row_a["yellow_pct"],
        row_a["green_pct"],
        row_a["over_pct"],
    ) == (40.0, 30.0, 20.0, 10.0)
    assert (row_a["stocked_out_pct"], row_a["critical_pct"]) == (20.0, 30.0)
    row_b, row_c = perf["rows"][1], perf["rows"][2]
    assert row_b["days_observed"] == 10
    assert (
        row_b["red_pct"],
        row_b["yellow_pct"],
        row_b["green_pct"],
        row_b["over_pct"],
    ) == (10.0, 40.0, 40.0, 10.0)
    assert (row_b["stocked_out_pct"], row_b["critical_pct"]) == (10.0, 0.0)
    assert (
        row_c["red_pct"],
        row_c["yellow_pct"],
        row_c["green_pct"],
        row_c["over_pct"],
    ) == (10.0, 90.0, 0.0, 0.0)
    assert (row_c["stocked_out_pct"], row_c["critical_pct"]) == (0.0, 10.0)
    defaults_body = perf

    # The days window limits the days counted: the 7 days ending on
    # the latest snapshot_on.
    resp = client.get(PERF_URL, params={"days": 7})
    assert resp.status_code == 200, resp.text
    perf = resp.json()
    assert perf["from"] == (as_of - timedelta(days=6)).isoformat()
    assert perf["to"] == as_of.isoformat()
    row_a = perf["rows"][0]
    assert row_a["days_observed"] == 7
    assert (
        row_a["red_pct"],
        row_a["yellow_pct"],
        row_a["green_pct"],
        row_a["over_pct"],
    ) == (14.3, 42.9, 28.6, 14.3)
    assert (row_a["stocked_out_pct"], row_a["critical_pct"]) == (0.0, 28.6)
    row_b, row_c = perf["rows"][1], perf["rows"][2]
    assert (
        row_b["red_pct"],
        row_b["yellow_pct"],
        row_b["green_pct"],
        row_b["over_pct"],
    ) == (0.0, 28.6, 57.1, 14.3)
    assert (
        row_c["red_pct"],
        row_c["yellow_pct"],
        row_c["green_pct"],
        row_c["over_pct"],
    ) == (0.0, 100.0, 0.0, 0.0)

    # The window bounds: days 7 and 730 answer, anything else 422.
    assert client.get(PERF_URL, params={"days": 730}).status_code == 200
    assert client.get(PERF_URL, params={"days": 3}).status_code == 422
    assert client.get(PERF_URL, params={"days": 0}).status_code == 422
    assert client.get(PERF_URL, params={"days": 731}).status_code == 422
    assert client.get(PERF_URL, params={"days": "abc"}).status_code == 422
    assert client.get(PERF_URL, params={"days": "1e3"}).status_code == 422
    assert client.get(PERF_URL, params={"days": "90\x00"}).status_code == 422

    # No token answers 401.
    assert anon.get(PERF_URL).status_code == 401

    # The route writes nothing.
    assert _snapshot_count() == counts_before

    # --- AC-1: the schema file ------------------------------------------
    spec = json.loads(_OPENAPI.read_text(encoding="utf-8"))
    operation = spec["paths"][PERF_URL]["get"]
    days_param = next(
        parameter
        for parameter in operation["parameters"]
        if parameter["name"] == "days"
    )
    assert (
        days_param["schema"]["minimum"],
        days_param["schema"]["maximum"],
        days_param["schema"]["default"],
    ) == (7, 730, 90)
    assert (
        operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"]
        == "#/components/schemas/DdmrpPerformanceResponse"
    )
    schemas = spec["components"]["schemas"]
    assert set(schemas["DdmrpPerformanceResponse"]["properties"]) == {
        "from",
        "to",
        "rows",
    }
    assert set(schemas["DdmrpPerformanceRow"]["properties"]) == {
        "item_code",
        "site_code",
        "days_observed",
        "red_pct",
        "yellow_pct",
        "green_pct",
        "over_pct",
        "stocked_out_pct",
        "critical_pct",
    }

    # --- Identity: a second pass after a full reload, as the
    #     non-admin (role "user"). ---------------------------------------
    # The page boots the production way: the token it would take from
    # the URL hash as #access_token=... is the development token (role
    # "user") sent as its Bearer token; GET /api/auth/config answers
    # publicly and GET /api/auth/me resolves the viewer.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    me_body = me.json()
    assert me_body["user"]["sub"] == "dev-user-1"
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]

    # The admin-only controls are absent for that caller...
    resp = viewer.put(
        BUFFERS_URL,
        json={"item_code": ITEM_A, "site_code": SITE_A, "note": "viewer"},
    )
    assert resp.status_code == 403, resp.text
    resp = viewer.delete(BUFFERS_URL, params={"item_code": ITEM_A, "site_code": SITE_A})
    assert resp.status_code == 403, resp.text

    # ...while the performance page still renders for it, and no token
    # is still a 401.
    resp = viewer.get(PERF_URL)
    assert resp.status_code == 200, resp.text
    assert resp.json() == defaults_body
    assert anon_reloaded.get(PERF_URL).status_code == 401
