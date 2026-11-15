"""Issue #199 proof: append-only decisions on stockout risk rows.

The proof covers, in one test:

* the migration shape — ``stockout_decisions`` exists with the
  documented columns, the decision CHECK constraint (only accepted /
  dismissed / snoozed), the action CHECK constraint (only expedite /
  raise_order / transfer, null allowed) and the (item_code, site_code,
  decided_at) index, and the 0009 head moved to 0010,
* POST /api/stockout/decisions behind the same auth gate as the
  stockout router: 401 without a token, 403 for a user without the
  admin role, 404 when the item-site has no current stockout row, and
  201 with the stored decision (new id, decided_at, the row's as_of /
  category / p_stockout / on_hand_units / open_supply_units /
  expected_units_short / money_at_risk snapshot, decided_by = the
  token subject) when it does,
* every business 422 (accepted without an action, action on a
  non-accepted decision, an unknown action, dismissed without a
  non-blank reason, reason over 1000 characters, snoozed without
  snooze_until, snooze_until not after today or more than 30 days out,
  snooze_until on a non-snoozed decision, one filter code without the
  other) in FastAPI's HTTPValidationError envelope, enforced at the
  HTTP boundary — the database CHECK constraints are proven separately
  and are not the proof of API validation,
* the accepted bounds: snooze_until up to today + 30 days and reason
  up to 1000 characters answer 201,
* GET /api/stockout/decisions open to any authenticated user: the
  item-site's full decision history with both codes, the current
  decision of every item-site that has one without them (bare JSON
  array, limit 1..1000 default 100, offset 0 or more, decided_at DESC,
  id DESC), and item_code/site_code/limit/offset as its only query
  parameters — no date filters,
* append-only semantics: the later dismissed decision becomes the
  current one while both appear, newest first, in the history and the
  earlier one is neither changed nor deleted,
* migration 0010's downgrade drops the table and its index (no orphan
  table) and the upgrade back to head re-creates it,
* the OpenAPI documents the POST 404, states the two GET modes and the
  422 for one code alone, and gates both routes on the Bearer security
  scheme.

The rows are seeded on fixed past dates so the ranked rows and their
snapshots are deterministic: 10/day demand from 2025-07-06 through
2026-06-30 for three item-sites (the latest demand event, so the
default as-of is 2026-06-30), pooled 8/10/12-day lead times (lt 10,
sd_lt 2, steady demand so sd_d 0) and on hand 60 / 40 / 30, so all
three are listed at risk — p_stockout 0.9772 (money at risk 80.34),
0.9987 (90.02) and 0.9998 (210.0) — with the third item-site
never decided on.

The identity requirement is met at the API boundary (this issue adds
no frontend page): after the admin pass, the test reloads as a
non-admin viewer (role "user", the development token's "T:user" form)
and asserts the admin-only control (POST) is denied with 403 while the
page's data (GET /api/stockout and GET /api/stockout/decisions, both
modes) still renders with 200, and a request without a token still
gets 401.
"""

import os
import time
from datetime import date, timedelta

import psycopg2
import psycopg2.errors
import psycopg2.extras
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOWN_REVISION = "0009_excess_decisions"
HEAD_REVISION = "0022_lead_time_promised_on"

ITEM_A = "ITEM-199-A"  # 60 on hand, unit cost 2.0 -> the accepted item-site
ITEM_B = "ITEM-199-B"  # 40 on hand, unit cost 1.5 -> the 1000-char reason
ITEM_C = "ITEM-199-C"  # 20 on hand, unit cost 3.0 -> listed, never decided
SITE_CODE = "SITE-199"
UNKNOWN_ITEM = "ITEM-199-NOPE"

ON_HAND_A = 60.0
ON_HAND_B = 40.0
ON_HAND_C = 30.0
UNIT_COST_A = 2.0
UNIT_COST_B = 1.5
UNIT_COST_C = 3.0

# d 10, sd_d 0 (steady 10/day), lt 10, sd_lt 2 -> mu 100, sigma
# 20, service level 0.95: the deterministic ranked snapshot of
# each seeded row.
SNAPSHOT_A = {
    "category": "likely",
    "p_stockout": 0.9772,
    "expected_units_short": 40.17,
    "money_at_risk": 80.34,
}
SNAPSHOT_B = {
    "category": "likely",
    "p_stockout": 0.9987,
    "expected_units_short": 60.01,
    "money_at_risk": 90.02,
}
SNAPSHOT_C = {
    "category": "likely",
    "p_stockout": 0.9998,
    "expected_units_short": 70.0,
    "money_at_risk": 210.0,
}

FIRST_DAY = date(2025, 7, 6)
DEMAND_DAYS = 360  # 10/day through 2026-06-30
AS_OF = "2026-06-30"  # the latest seeded demand event
AS_OF_STAMP = "2026-06-30 00:00:00+00"
ADMIN_SUB = "dev-admin-1"

ALL_COLUMNS = {
    "id",
    "item_code",
    "site_code",
    "decision",
    "action",
    "reason",
    "snooze_until",
    "as_of",
    "category",
    "p_stockout",
    "on_hand_units",
    "open_supply_units",
    "expected_units_short",
    "money_at_risk",
    "decided_by",
    "decided_by_name",
    "decided_at",
}


def _seed() -> None:
    """The fixed-date rows: three listed-at-risk item-sites.

    10/day demand for every item from 2025-07-06 through 2026-06-30,
    so the default as-of (the latest demand event) is 2026-06-30;
    pooled 8/10/12-day lead times (mean 10, sample std 2); on hand 60
    / 40 / 30, so every position is far below the mean lead-time
    demand of 100 and every item-site is listed at risk.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        f"id-de-199-{item}-{day_index}",
                        f"de-199-{item}-{day_index}",
                        item,
                        SITE_CODE,
                        10.0,
                        (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for item in (ITEM_A, ITEM_B, ITEM_C)
                    for day_index in range(DEMAND_DAYS)
                ],
            )
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    # 8, 10 and 12 days -> mean 10, sample std 2, for
                    # every item.
                    (
                        f"id-lt-199-{suffix}{day_index}",
                        f"lt-199-{suffix}{day_index}",
                        f"SUP-199-{suffix}",
                        item,
                        ordered,
                        received,
                        "test",
                    )
                    for suffix, item in (("a", ITEM_A), ("b", ITEM_B), ("c", ITEM_C))
                    for day_index, (ordered, received) in enumerate(
                        (
                            ("2026-02-01", "2026-02-09"),
                            ("2026-02-05", "2026-02-15"),
                            ("2026-02-08", "2026-02-20"),
                        ),
                        start=1,
                    )
                ],
            )
            cur.executemany(
                "INSERT INTO on_hand "
                "(id, item_code, site_code, quantity_on_hand, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    ("id-oh-199-a", ITEM_A, SITE_CODE, ON_HAND_A, AS_OF_STAMP, "test"),
                    ("id-oh-199-b", ITEM_B, SITE_CODE, ON_HAND_B, AS_OF_STAMP, "test"),
                    ("id-oh-199-c", ITEM_C, SITE_CODE, ON_HAND_C, AS_OF_STAMP, "test"),
                ],
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, source_system) "
                "VALUES (%s, %s, %s, %s, %s)",
                [
                    ("id-is-199-a", ITEM_A, SITE_CODE, UNIT_COST_A, "test"),
                    ("id-is-199-b", ITEM_B, SITE_CODE, UNIT_COST_B, "test"),
                    ("id-is-199-c", ITEM_C, SITE_CODE, UNIT_COST_C, "test"),
                ],
            )
        conn.commit()
    finally:
        conn.close()


def _count_decisions() -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM stockout_decisions")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _stored_decision(decision_id: str) -> dict:
    """The raw stockout_decisions row of one id, as the API stores it."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM stockout_decisions WHERE id = %s", (decision_id,)
            )
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, f"decision {decision_id} disappeared"
    return dict(row)


def _table_exists() -> bool:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_name = 'stockout_decisions'"
            )
            return cur.fetchone() is not None
    finally:
        conn.close()


def _index_names() -> list[str]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'stockout_decisions'"
            )
            return [row[0] for row in cur.fetchall()]
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


def _assert_validation_envelope(resp) -> list[dict]:
    """A business 422 in FastAPI's HTTPValidationError envelope."""
    assert resp.status_code == 422, (resp.status_code, resp.text)
    body = resp.json()
    detail = body.get("detail")
    assert isinstance(detail, list) and detail, body
    for error in detail:
        assert isinstance(error, dict), body
        for key in ("type", "loc", "msg"):
            assert key in error, (key, body)
    return detail


def _assert_full_columns(rows: list[dict]) -> None:
    for row in rows:
        assert set(row.keys()) == ALL_COLUMNS, row


def _post_decision(client: TestClient, body: dict):
    time.sleep(0.02)  # distinct decided_at per call (ordering proof)
    return client.post("/api/stockout/decisions", json=body)


def _base_body(item_code: str = ITEM_A) -> dict:
    return {"item_code": item_code, "site_code": SITE_CODE}


def test_issue199_freeform(client, admin_token, user_token):
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    user_client = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_client = TestClient(app)

    _seed()

    # -- The seeded item-sites are the listed-at-risk rows. --------------
    stockout_resp = admin_client.get("/api/stockout")
    assert stockout_resp.status_code == 200, stockout_resp.text
    stockout_rows = stockout_resp.json()
    assert sorted((row["item_code"], row["site_code"]) for row in stockout_rows) == [
        (ITEM_A, SITE_CODE),
        (ITEM_B, SITE_CODE),
        (ITEM_C, SITE_CODE),
    ], stockout_rows
    row_a = next(row for row in stockout_rows if row["item_code"] == ITEM_A)
    row_b = next(row for row in stockout_rows if row["item_code"] == ITEM_B)
    row_c = next(row for row in stockout_rows if row["item_code"] == ITEM_C)
    for row, snapshot, on_hand in (
        (row_a, SNAPSHOT_A, ON_HAND_A),
        (row_b, SNAPSHOT_B, ON_HAND_B),
        (row_c, SNAPSHOT_C, ON_HAND_C),
    ):
        assert row["as_of"] == AS_OF
        assert row["category"] == snapshot["category"]
        assert row["p_stockout"] == snapshot["p_stockout"]
        assert row["on_hand_units"] == on_hand
        assert row["open_supply_units"] == 0.0
        assert row["expected_units_short"] == snapshot["expected_units_short"]
        assert row["money_at_risk"] == snapshot["money_at_risk"]

    # -- The table shape: columns, CHECK constraints, index. -------------
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable, "
                "character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'stockout_decisions' ORDER BY ordinal_position"
            )
            columns = {
                row["column_name"]: (
                    row["data_type"],
                    row["is_nullable"],
                    row["character_maximum_length"],
                )
                for row in cur.fetchall()
            }
        assert columns == {
            "id": ("character varying", "NO", 64),
            "item_code": ("character varying", "NO", 64),
            "site_code": ("character varying", "NO", 64),
            "decision": ("character varying", "NO", 16),
            "action": ("character varying", "YES", 32),
            "reason": ("text", "YES", None),
            "snooze_until": ("date", "YES", None),
            "as_of": ("date", "NO", None),
            "category": ("character varying", "NO", 16),
            "p_stockout": ("double precision", "NO", None),
            "on_hand_units": ("double precision", "NO", None),
            "open_supply_units": ("double precision", "NO", None),
            "expected_units_short": ("double precision", "NO", None),
            "money_at_risk": ("double precision", "YES", None),
            "decided_by": ("character varying", "NO", 255),
            "decided_by_name": ("character varying", "YES", 255),
            "decided_at": ("timestamp with time zone", "NO", None),
        }, columns
        with conn.cursor() as cur:
            cur.execute(
                "SELECT conname, pg_get_constraintdef(oid) "
                "FROM pg_constraint WHERE conrelid = 'stockout_decisions'::regclass "
                "AND contype = 'c' ORDER BY conname"
            )
            checks = {row[0]: row[1] for row in cur.fetchall()}
        decision_check = checks["ck_stockout_decisions_decision"]
        for value in ("accepted", "dismissed", "snoozed"):
            assert f"'{value}'" in decision_check, decision_check
        action_check = checks["ck_stockout_decisions_action"]
        for value in ("expedite", "raise_order", "transfer"):
            assert f"'{value}'" in action_check, action_check
        assert any(
            "item_code, site_code, decided_at" in index for index in _index_names()
        ), _index_names()
        # The CHECK constraints themselves refuse unknown values.
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO stockout_decisions "
                "(id, item_code, site_code, decision, as_of, category, "
                "p_stockout, on_hand_units, open_supply_units, "
                "expected_units_short, decided_by) "
                "VALUES ('id-199-check', %s, %s, 'approved', %s, 'likely', "
                "0.5, 1, 1, 1, 'seeder')",
                (ITEM_A, SITE_CODE, AS_OF),
            )
            conn.commit()
            raise AssertionError("decision CHECK constraint did not fire")
        except psycopg2.errors.CheckViolation:
            conn.rollback()
        finally:
            cur.close()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO stockout_decisions "
                "(id, item_code, site_code, decision, action, as_of, category, "
                "p_stockout, on_hand_units, open_supply_units, "
                "expected_units_short, decided_by) "
                "VALUES ('id-199-check', %s, %s, 'accepted', 'sell', %s, 'likely', "
                "0.5, 1, 1, 1, 'seeder')",
                (ITEM_A, SITE_CODE, AS_OF),
            )
            conn.commit()
            raise AssertionError("action CHECK constraint did not fire")
        except psycopg2.errors.CheckViolation:
            conn.rollback()
        finally:
            cur.close()
    finally:
        conn.close()
    assert _count_decisions() == 0

    # -- Auth boundary: 401 without a token, 403 without admin. ----------
    anon_resp = anon_client.get("/api/stockout/decisions")
    assert anon_resp.status_code == 401
    assert (
        anon_resp.json()["detail"]
        == "Authentication required. Provide a valid Bearer token."
    )
    anon_post = anon_client.post(
        "/api/stockout/decisions",
        json={**_base_body(), "decision": "accepted", "action": "expedite"},
    )
    assert anon_post.status_code == 401
    forbidden = user_client.post(
        "/api/stockout/decisions",
        json={**_base_body(), "decision": "accepted", "action": "expedite"},
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["detail"] == (
        "Role 'admin' is required to perform this action."
    )

    # -- AC-4: the accepted decision snapshots the ranked row. -----------
    accepted = _post_decision(
        admin_client,
        {
            **_base_body(),
            "decision": "accepted",
            "action": "expedite",
            "reason": "expedite the open order before the projected stockout",
        },
    )
    assert accepted.status_code == 201, accepted.text
    accepted = accepted.json()
    assert set(accepted.keys()) == ALL_COLUMNS
    assert accepted["id"]
    assert accepted["decided_at"]
    assert accepted["item_code"] == ITEM_A
    assert accepted["site_code"] == SITE_CODE
    assert accepted["decision"] == "accepted"
    assert accepted["action"] == "expedite"
    assert accepted["reason"] == "expedite the open order before the projected stockout"
    assert accepted["snooze_until"] is None
    assert accepted["as_of"] == row_a["as_of"]
    assert accepted["category"] == row_a["category"]
    assert accepted["p_stockout"] == row_a["p_stockout"]
    assert accepted["on_hand_units"] == row_a["on_hand_units"]
    assert accepted["open_supply_units"] == row_a["open_supply_units"]
    assert accepted["expected_units_short"] == row_a["expected_units_short"]
    assert accepted["money_at_risk"] == row_a["money_at_risk"]
    assert accepted["decided_by"] == ADMIN_SUB
    assert _count_decisions() == 1

    # -- AC-4: the dismissed decision becomes the current one. -----------
    dismissed = _post_decision(
        admin_client,
        {
            **_base_body(),
            "decision": "dismissed",
            "reason": "demand forecast was wrong; coverage is fine",
        },
    )
    assert dismissed.status_code == 201, dismissed.text
    dismissed = dismissed.json()
    assert dismissed["id"] != accepted["id"]
    assert dismissed["decision"] == "dismissed"
    assert dismissed["action"] is None
    assert dismissed["reason"] == "demand forecast was wrong; coverage is fine"
    assert dismissed["p_stockout"] == row_a["p_stockout"]
    assert dismissed["decided_by"] == ADMIN_SUB
    # The unfiltered list now carries the dismissed decision as the
    # item-site's current one.
    current_resp = user_client.get("/api/stockout/decisions")
    assert current_resp.status_code == 200, current_resp.text
    current_rows = current_resp.json()
    assert isinstance(current_rows, list)
    assert [row["id"] for row in current_rows] == [dismissed["id"]]
    assert current_rows[0]["decision"] == "dismissed"
    _assert_full_columns(current_rows)
    # The item-site's history carries both, newest first.
    history_resp = user_client.get(
        "/api/stockout/decisions",
        params={"item_code": ITEM_A, "site_code": SITE_CODE},
    )
    assert history_resp.status_code == 200, history_resp.text
    history_rows = history_resp.json()
    assert isinstance(history_rows, list)
    assert [row["id"] for row in history_rows] == [dismissed["id"], accepted["id"]]
    assert [row["decision"] for row in history_rows] == ["dismissed", "accepted"]
    _assert_full_columns(history_rows)

    # -- AC-2: snooze_until up to today + 30 days is accepted. -----------
    snoozed = _post_decision(
        admin_client,
        {
            **_base_body(),
            "decision": "snoozed",
            "snooze_until": (date.today() + timedelta(days=30)).isoformat(),
        },
    )
    assert snoozed.status_code == 201, snoozed.text
    snoozed = snoozed.json()
    assert snoozed["decision"] == "snoozed"
    assert snoozed["action"] is None
    assert snoozed["snooze_until"] == (date.today() + timedelta(days=30)).isoformat()

    # -- AC-2: reason up to 1000 characters is accepted. -----------------
    long_reason = "x" * 1000
    assert len(long_reason) == 1000
    dismissed_b = _post_decision(
        admin_client,
        {**_base_body(ITEM_B), "decision": "dismissed", "reason": long_reason},
    )
    assert dismissed_b.status_code == 201, dismissed_b.text
    dismissed_b = dismissed_b.json()
    assert dismissed_b["item_code"] == ITEM_B
    assert dismissed_b["reason"] == long_reason
    assert dismissed_b["p_stockout"] == row_b["p_stockout"]
    assert dismissed_b["money_at_risk"] == row_b["money_at_risk"]
    assert _count_decisions() == 4

    # -- AC-2: every business 422, all in the validation envelope. -------
    _assert_validation_envelope(
        _post_decision(admin_client, {**_base_body(), "decision": "accepted"})
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                **_base_body(),
                "decision": "dismissed",
                "reason": "still at risk",
                "action": "expedite",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {**_base_body(), "decision": "accepted", "action": "scrub"},
        )
    )
    _assert_validation_envelope(
        _post_decision(admin_client, {**_base_body(), "decision": "dismissed"})
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {**_base_body(), "decision": "dismissed", "reason": "   "},
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {**_base_body(), "decision": "dismissed", "reason": "x" * 1001},
        )
    )
    _assert_validation_envelope(
        _post_decision(admin_client, {**_base_body(), "decision": "snoozed"})
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                **_base_body(),
                "decision": "snoozed",
                "snooze_until": date.today().isoformat(),
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                **_base_body(),
                "decision": "snoozed",
                "snooze_until": (date.today() - timedelta(days=1)).isoformat(),
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                **_base_body(),
                "decision": "snoozed",
                "snooze_until": (date.today() + timedelta(days=31)).isoformat(),
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                **_base_body(),
                "decision": "accepted",
                "action": "raise_order",
                "snooze_until": (date.today() + timedelta(days=7)).isoformat(),
            },
        )
    )
    assert _count_decisions() == 4

    # -- AC-4: an unknown item-site has no current ranked row. -----------
    not_found = _post_decision(
        admin_client,
        {
            **_base_body(UNKNOWN_ITEM),
            "decision": "accepted",
            "action": "expedite",
        },
    )
    assert not_found.status_code == 404, not_found.text
    assert not_found.json()["detail"] == "no current stockout row for item site"
    assert _count_decisions() == 4

    # -- Append-only: the earlier decisions were neither changed nor
    #    deleted. ---------------------------------------------------------
    accepted_snapshot = _stored_decision(accepted["id"])
    assert accepted_snapshot["decision"] == "accepted"
    assert accepted_snapshot["action"] == "expedite"
    assert accepted_snapshot["reason"] == (
        "expedite the open order before the projected stockout"
    )
    dismissed_snapshot = _stored_decision(dismissed["id"])
    assert dismissed_snapshot["decision"] == "dismissed"
    assert dismissed_snapshot["reason"] == "demand forecast was wrong; coverage is fine"

    # -- AC-6: no filters -> only the latest decision per item-site. -----
    current_resp = user_client.get("/api/stockout/decisions")
    assert current_resp.status_code == 200, current_resp.text
    current_rows = current_resp.json()
    assert isinstance(current_rows, list)
    # Four decisions were stored across two item-sites; the bare list
    # carries exactly one row per item-site, the latest of each, newest
    # first (ITEM_B's decision is the newest overall).
    assert [row["id"] for row in current_rows] == [
        dismissed_b["id"],
        snoozed["id"],
    ]
    assert {row["item_code"] for row in current_rows} == {ITEM_A, ITEM_B}
    _assert_full_columns(current_rows)

    # -- AC-6: both codes -> every decision of that item-site. -----------
    history_resp = user_client.get(
        "/api/stockout/decisions",
        params={"item_code": ITEM_A, "site_code": SITE_CODE},
    )
    assert history_resp.status_code == 200
    history_rows = history_resp.json()
    history_ids = [row["id"] for row in history_rows]
    assert history_ids == [snoozed["id"], dismissed["id"], accepted["id"]], history_ids
    # decided_at is non-increasing along the history, id decides ties.
    for earlier, later in zip(history_rows, history_rows[1:]):
        assert earlier["decided_at"] >= later["decided_at"]
        if earlier["decided_at"] == later["decided_at"]:
            assert earlier["id"] > later["id"]
    history_b = user_client.get(
        "/api/stockout/decisions",
        params={"item_code": ITEM_B, "site_code": SITE_CODE},
    )
    assert history_b.status_code == 200
    assert [row["id"] for row in history_b.json()] == [dismissed_b["id"]]

    # -- AC-3: 200 (an empty array) for an item-site without decisions,
    #    listed at risk or not; never 404. --------------------------------
    for item in (ITEM_C, UNKNOWN_ITEM):
        empty = user_client.get(
            "/api/stockout/decisions",
            params={"item_code": item, "site_code": SITE_CODE},
        )
        assert empty.status_code == 200, empty.text
        assert empty.json() == []

    # -- AC-3: one code without the other and the paging bounds. ---------
    _assert_validation_envelope(
        user_client.get("/api/stockout/decisions", params={"item_code": ITEM_A})
    )
    _assert_validation_envelope(
        user_client.get("/api/stockout/decisions", params={"site_code": SITE_CODE})
    )
    _assert_validation_envelope(
        user_client.get("/api/stockout/decisions", params={"limit": 0})
    )
    _assert_validation_envelope(
        user_client.get("/api/stockout/decisions", params={"limit": 1001})
    )
    _assert_validation_envelope(
        user_client.get("/api/stockout/decisions", params={"offset": -1})
    )
    # Paging walks the same order in both modes.
    page_one = user_client.get("/api/stockout/decisions", params={"limit": 1})
    assert page_one.status_code == 200
    assert [row["id"] for row in page_one.json()] == [dismissed_b["id"]]
    page_two = user_client.get(
        "/api/stockout/decisions", params={"limit": 1, "offset": 1}
    )
    assert [row["id"] for row in page_two.json()] == [snoozed["id"]]
    page_history = user_client.get(
        "/api/stockout/decisions",
        params={
            "item_code": ITEM_A,
            "site_code": SITE_CODE,
            "limit": 2,
        },
    )
    assert [row["id"] for row in page_history.json()] == [
        snoozed["id"],
        dismissed["id"],
    ]
    page_past = user_client.get(
        "/api/stockout/decisions",
        params={
            "item_code": ITEM_A,
            "site_code": SITE_CODE,
            "limit": 1,
            "offset": 3,
        },
    )
    assert page_past.status_code == 200
    assert page_past.json() == []

    # -- OpenAPI: the POST documents the 404; the GET states both modes
    #    and the 422 for one code alone; both routes are gated; the GET
    #    takes no date filter parameters. --------------------------------
    schema = app.openapi()
    post = schema["paths"]["/api/stockout/decisions"]["post"]
    assert post["responses"]["404"]["description"] == (
        "No current stockout row exists for the item-site."
    )
    assert post["security"] == [{"bearerAuth": []}]
    get = schema["paths"]["/api/stockout/decisions"]["get"]
    assert get["security"] == [{"bearerAuth": []}]
    query_params = {
        parameter["name"]
        for parameter in get["parameters"]
        if parameter["in"] == "query"
    }
    assert query_params == {"item_code", "site_code", "limit", "offset"}
    description = get["description"]
    assert "full decision history of that item-site" in description, description
    assert "current decision" in description, description
    assert "every item-site that has one" in description, description
    assert "Giving only one of the two codes answers 422" in description, description

    # -- Identity, viewer pass: the admin-only control is absent for a
    #    role "user" while the page's data still renders. ----------------
    viewer_stockout = user_client.get("/api/stockout")
    assert viewer_stockout.status_code == 200
    assert sorted(
        (row["item_code"], row["site_code"]) for row in viewer_stockout.json()
    ) == [(ITEM_C, SITE_CODE)]
    viewer_current = user_client.get("/api/stockout/decisions")
    assert viewer_current.status_code == 200
    viewer_current_rows = viewer_current.json()
    assert isinstance(viewer_current_rows, list)
    assert [row["id"] for row in viewer_current_rows] == [
        dismissed_b["id"],
        snoozed["id"],
    ]
    viewer_history = user_client.get(
        "/api/stockout/decisions",
        params={"item_code": ITEM_A, "site_code": SITE_CODE},
    )
    assert viewer_history.status_code == 200
    assert [row["id"] for row in viewer_history.json()] == history_ids
    viewer_post = user_client.post(
        "/api/stockout/decisions",
        json={**_base_body(), "decision": "accepted", "action": "expedite"},
    )
    assert viewer_post.status_code == 403
    assert viewer_post.json()["detail"] == (
        "Role 'admin' is required to perform this action."
    )
    assert _count_decisions() == 4
    assert anon_client.get("/api/stockout/decisions").status_code == 401

    # -- AC-5: the downgrade drops the table and its index, and the
    #    upgrade back to head re-creates it. ------------------------------
    cfg = _alembic_config()
    alembic_command.downgrade(cfg, DOWN_REVISION)
    assert _alembic_revision() == DOWN_REVISION
    assert not _table_exists()
    assert _index_names() == []
    alembic_command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert heads[0] == CURRENT_HEAD
    assert _alembic_revision() == CURRENT_HEAD
    assert _table_exists()
    assert any("item_code, site_code, decided_at" in index for index in _index_names())
    # The API is back on the re-created table (empty after the drop).
    recheck = user_client.get("/api/stockout/decisions")
    assert recheck.status_code == 200
    assert recheck.json() == []
