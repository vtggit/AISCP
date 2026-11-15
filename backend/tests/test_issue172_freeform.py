"""Issue #172 proof: append-only planner decisions on drift rows.

The proof covers, in one test:

* the migration shape — ``planner_decisions`` exists with the documented
  columns, the decision CHECK constraint (only accepted / dismissed /
  snoozed) and the (item_code, site_code, decided_at) index,
* POST /api/drift/decisions behind the same auth gate as the drift
  router: 401 without a token, 403 for a user without the admin role, 404
  when the item-site has no current drift row, and 201 with the stored
  decision (id, decided_at, the row's as_of / reorder_point_units /
  current_reorder_point / money_at_stake snapshot, decided_by = the
  token subject) when it does,
* every business 422 (unknown decision, dismissed without a non-blank
  reason, reason over 1000 characters, snoozed without snooze_until,
  snooze_until not after today or more than 90 days out, snooze_until
  on a non-snoozed decision, one filter code without the other) in
  FastAPI's HTTPValidationError envelope,
* GET /api/drift/decisions open to any authenticated user: the
  item-site's full decision history with both codes, the current
  decision of every item-site that has one without them (bare JSON
  array, limit 1..1000 default 100, offset 0 or more), and results
  ordered by decided_at DESC, id DESC in both current and history
  modes (the id tiebreak is proven with rows sharing one decided_at),
* append-only semantics: earlier decisions are never changed or
  deleted, and duplicate POSTs are accepted as separate entries
  without an Idempotency-Key header,
* the OpenAPI of POST /api/drift/decisions documenting the 404 as
  "no current drift row exists" and the duplicates as append-only
  without an Idempotency-Key requirement.

The drift row is seeded on fixed past calendar dates so the snapshot
values are deterministic: 10/day demand from 2025-09-01 through
2026-08-31 (the latest demand event, so the default as-of is
2026-08-31), pooled 9/10/10-day lead times, and an item-site reorder
point of 120 against a recommended point of about 106 units.

The identity requirement is met at the API boundary (this issue adds
no frontend page): after the admin pass, the test reloads as a
non-admin viewer (role "user", the development token's "T:user" form)
and asserts the admin-only control (POST) is denied with 403 while the
page's data (GET, both modes) still renders with 200, and a request
without a token still gets 401.
"""

import time
from datetime import date, timedelta

import psycopg2
import psycopg2.errors
import psycopg2.extras
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

ITEM_CODE = "ITEM-172"
SITE_CODE = "SITE-172"
UNIT_COST = 2.5
CURRENT_REORDER_POINT = 120.0
ORDER_QTY = 80.0
FIRST_DAY = date(2025, 9, 1)
DEMAND_DAYS = 365  # 10/day through 2026-08-31
AS_OF = "2026-08-31"  # the latest seeded demand event
ADMIN_SUB = "dev-admin-1"

ALL_COLUMNS = {
    "id",
    "item_code",
    "site_code",
    "decision",
    "reason",
    "snooze_until",
    "as_of",
    "recommended_reorder_point_units",
    "current_reorder_point",
    "money_at_stake",
    "decided_by",
    "decided_by_name",
    "decided_at",
}


def _seed() -> None:
    """The fixed-date rows: one item-site with a drift row.

    10/day demand from 2025-09-01 through 2026-08-31 and pooled
    9/10/10-day lead times, so the default as-of (the latest demand
    event) is 2026-08-31 and the recommendation is "ok"; the item-site
    carries a reorder point of 120, above the recommended ~106 units.
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
                        f"id-de-172-{day_index}",
                        f"de-172-{day_index}",
                        ITEM_CODE,
                        SITE_CODE,
                        10.0,
                        (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for day_index in range(DEMAND_DAYS)
                ],
            )
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    (
                        "id-lt-172-1",
                        "lt-172-1",
                        "SUP-172",
                        ITEM_CODE,
                        "2026-05-01",
                        "2026-05-10",
                        "test",
                    ),
                    (
                        "id-lt-172-2",
                        "lt-172-2",
                        "SUP-172",
                        ITEM_CODE,
                        "2026-05-05",
                        "2026-05-15",
                        "test",
                    ),
                    (
                        "id-lt-172-3",
                        "lt-172-3",
                        "SUP-172",
                        ITEM_CODE,
                        "2026-05-09",
                        "2026-05-19",
                        "test",
                    ),
                ),
            )
            cur.execute(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "order_qty, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (
                    "id-is-172",
                    ITEM_CODE,
                    SITE_CODE,
                    UNIT_COST,
                    CURRENT_REORDER_POINT,
                    ORDER_QTY,
                    "test",
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_decision(
    decision_id: str,
    item_code: str,
    site_code: str,
    decision: str,
    decided_at: str,
    reason: str | None = None,
    snooze_until: str | None = None,
    units: int = 107,
    current: float | None = 120.0,
    money: float | None = 34.5,
    decided_by: str = "seeder",
) -> None:
    """One planner_decisions row with a fixed decided_at (ordering proof)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO planner_decisions "
                "(id, item_code, site_code, decision, reason, snooze_until, "
                "as_of, recommended_reorder_point_units, current_reorder_point, "
                "money_at_stake, decided_by, decided_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    decision_id,
                    item_code,
                    site_code,
                    decision,
                    reason,
                    snooze_until,
                    AS_OF,
                    units,
                    current,
                    money,
                    decided_by,
                    decided_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _stored_decision(decision_id: str) -> dict:
    """The raw planner_decisions row of one id, as the API stores it."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM planner_decisions WHERE id = %s", (decision_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, f"decision {decision_id} disappeared"
    return dict(row)


def _count_decisions(item_code: str | None = None) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if item_code is None:
                cur.execute("SELECT COUNT(*) FROM planner_decisions")
            else:
                cur.execute(
                    "SELECT COUNT(*) FROM planner_decisions WHERE item_code = %s",
                    (item_code,),
                )
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
    # Deliberately no Idempotency-Key header: duplicates must be
    # accepted as append-only entries without one.
    time.sleep(0.02)  # distinct decided_at per call (ordering proof)
    return client.post("/api/drift/decisions", json=body)


def test_issue172_freeform(client, admin_token, user_token):
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    user_client = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_client = TestClient(app)

    _seed()

    # -- The seeded item-site has exactly one current drift row. --------
    drift_resp = admin_client.get(
        "/api/drift/by-key",
        params={"item_code": ITEM_CODE, "site_code": SITE_CODE},
    )
    assert drift_resp.status_code == 200, drift_resp.text
    drift_row = drift_resp.json()
    assert drift_row["as_of"] == AS_OF
    assert drift_row["category"] == "drift"
    assert drift_row["current_reorder_point"] == CURRENT_REORDER_POINT
    assert isinstance(drift_row["reorder_point_units"], int)
    assert drift_row["reorder_point_units"] > 0
    assert drift_row["money_at_stake"] is not None
    unknown_drift = admin_client.get(
        "/api/drift/by-key",
        params={"item_code": "ITEM-NOPE", "site_code": SITE_CODE},
    )
    assert unknown_drift.status_code == 404

    # -- The table shape: columns, CHECK constraint, index. -------------
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable, "
                "character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'planner_decisions' ORDER BY ordinal_position"
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
            "reason": ("text", "YES", None),
            "snooze_until": ("date", "YES", None),
            "as_of": ("date", "NO", None),
            "recommended_reorder_point_units": ("integer", "NO", None),
            "current_reorder_point": ("double precision", "YES", None),
            "money_at_stake": ("double precision", "YES", None),
            "decided_by": ("character varying", "NO", 255),
            "decided_by_name": ("character varying", "YES", 255),
            "decided_at": ("timestamp with time zone", "NO", None),
        }, columns
        with conn.cursor() as cur:
            cur.execute(
                "SELECT conname, pg_get_constraintdef(oid) "
                "FROM pg_constraint WHERE conrelid = 'planner_decisions'::regclass "
                "AND contype = 'c'"
            )
            check = cur.fetchone()
        assert check is not None
        definition = check[1]
        for value in ("accepted", "dismissed", "snoozed"):
            assert f"'{value}'" in definition, definition
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'planner_decisions'"
            )
            indexes = [row[0] for row in cur.fetchall()]
        assert any(
            "item_code, site_code, decided_at" in index for index in indexes
        ), indexes
        # The CHECK constraint itself refuses an unknown decision.
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO planner_decisions "
                "(id, item_code, site_code, decision, as_of, "
                "recommended_reorder_point_units, decided_by) "
                "VALUES ('id-172-check', %s, %s, 'approved', %s, 1, 'seeder')",
                (ITEM_CODE, SITE_CODE, AS_OF),
            )
            conn.commit()
            raise AssertionError("CHECK constraint did not fire")
        except psycopg2.errors.CheckViolation:
            conn.rollback()
        finally:
            cur.close()
    finally:
        conn.close()
    assert _count_decisions() == 0

    # -- Auth boundary: 401 without a token, 403 without admin. ---------
    anon_resp = anon_client.get("/api/drift/decisions")
    assert anon_resp.status_code == 401
    assert (
        anon_resp.json()["detail"]
        == "Authentication required. Provide a valid Bearer token."
    )
    anon_post = anon_client.post(
        "/api/drift/decisions",
        json={"item_code": ITEM_CODE, "site_code": SITE_CODE, "decision": "accepted"},
    )
    assert anon_post.status_code == 401
    forbidden = user_client.post(
        "/api/drift/decisions",
        json={"item_code": ITEM_CODE, "site_code": SITE_CODE, "decision": "accepted"},
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["detail"] == (
        "Role 'admin' is required to perform this action."
    )

    # -- AC-4: the accepted decision snapshots the drift row. ------------
    accepted_body = {
        "item_code": ITEM_CODE,
        "site_code": SITE_CODE,
        "decision": "accepted",
        "reason": "agreed with the planner review",
    }
    accepted_resp = _post_decision(admin_client, accepted_body)
    assert accepted_resp.status_code == 201, accepted_resp.text
    accepted = accepted_resp.json()
    assert set(accepted.keys()) == ALL_COLUMNS
    assert accepted["id"]
    assert accepted["decided_at"]
    assert accepted["item_code"] == ITEM_CODE
    assert accepted["site_code"] == SITE_CODE
    assert accepted["decision"] == "accepted"
    assert accepted["reason"] == "agreed with the planner review"
    assert accepted["snooze_until"] is None
    assert accepted["as_of"] == drift_row["as_of"]
    assert (
        accepted["recommended_reorder_point_units"] == drift_row["reorder_point_units"]
    )
    assert accepted["current_reorder_point"] == drift_row["current_reorder_point"]
    assert accepted["money_at_stake"] == drift_row["money_at_stake"]
    assert accepted["decided_by"] == ADMIN_SUB
    assert _count_decisions(ITEM_CODE) == 1
    assert _stored_decision(accepted["id"])["decision"] == "accepted"

    # -- AC-4: the dismissed decision becomes the current one. -----------
    dismissed_body = {
        "item_code": ITEM_CODE,
        "site_code": SITE_CODE,
        "decision": "dismissed",
        "reason": "demand pattern is seasonal; point is intentional",
    }
    dismissed_resp = _post_decision(admin_client, dismissed_body)
    assert dismissed_resp.status_code == 201, dismissed_resp.text
    dismissed = dismissed_resp.json()
    assert dismissed["id"] != accepted["id"]
    assert dismissed["decision"] == "dismissed"
    assert dismissed["decided_by"] == ADMIN_SUB
    # The unfiltered list now carries the dismissed decision as the
    # item-site's current one.
    current_resp = user_client.get("/api/drift/decisions")
    assert current_resp.status_code == 200, current_resp.text
    current_rows = current_resp.json()
    assert isinstance(current_rows, list)
    assert [row["id"] for row in current_rows] == [dismissed["id"]]
    assert current_rows[0]["decision"] == "dismissed"
    assert (
        current_rows[0]["recommended_reorder_point_units"]
        == drift_row["reorder_point_units"]
    )
    # The item-site's history carries both, newest first.
    history_resp = user_client.get(
        "/api/drift/decisions",
        params={"item_code": ITEM_CODE, "site_code": SITE_CODE},
    )
    assert history_resp.status_code == 200, history_resp.text
    history_rows = history_resp.json()
    assert isinstance(history_rows, list)
    assert [row["id"] for row in history_rows] == [dismissed["id"], accepted["id"]]
    assert [row["decision"] for row in history_rows] == ["dismissed", "accepted"]
    _assert_full_columns(history_rows)

    # -- AC-2: every business 422, all in the validation envelope. ------
    detail = _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "approve",
            },
        )
    )
    assert detail[0]["loc"] == ["body", "decision"]
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "dismissed",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "dismissed",
                "reason": "   ",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "accepted",
                "reason": "x" * 1001,
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "snoozed",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "snoozed",
                "snooze_until": (date.today() + timedelta(days=91)).isoformat(),
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "snoozed",
                "snooze_until": date.today().isoformat(),
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "snoozed",
                "snooze_until": (date.today() - timedelta(days=1)).isoformat(),
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "dismissed",
                "reason": "not snoozed",
                "snooze_until": (date.today() + timedelta(days=7)).isoformat(),
            },
        )
    )
    # Hostile inputs stay 422, never a 500.
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": "I" * 65,
                "site_code": SITE_CODE,
                "decision": "accepted",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE + "\x00",
                "site_code": SITE_CODE,
                "decision": "accepted",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "accepted",
                "reason": "bad\x00reason",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": ITEM_CODE,
                "site_code": SITE_CODE,
                "decision": "snoozed",
                "snooze_until": "not-a-date",
            },
        )
    )
    # Nothing was stored by the rejected bodies.
    assert _count_decisions(ITEM_CODE) == 2

    # -- AC-4: the 90-day snooze boundary is accepted (201). --------------
    snoozed_resp = _post_decision(
        admin_client,
        {
            "item_code": ITEM_CODE,
            "site_code": SITE_CODE,
            "decision": "snoozed",
            "snooze_until": (date.today() + timedelta(days=90)).isoformat(),
        },
    )
    assert snoozed_resp.status_code == 201, snoozed_resp.text
    snoozed = snoozed_resp.json()
    assert snoozed["decision"] == "snoozed"
    assert snoozed["snooze_until"] == (date.today() + timedelta(days=90)).isoformat()
    assert _count_decisions(ITEM_CODE) == 3

    # -- AC-4: an unknown item-site has no current drift row. ------------
    not_found = _post_decision(
        admin_client,
        {
            "item_code": "ITEM-NOPE",
            "site_code": SITE_CODE,
            "decision": "accepted",
        },
    )
    assert not_found.status_code == 404, not_found.text
    assert _count_decisions() == 3

    # -- AC-6: duplicate POSTs are separate append-only entries. ---------
    snapshot = _stored_decision(accepted["id"])
    duplicate_a = _post_decision(admin_client, accepted_body)
    assert duplicate_a.status_code == 201, duplicate_a.text
    duplicate_b = _post_decision(admin_client, accepted_body)
    assert duplicate_b.status_code == 201, duplicate_b.text
    duplicate_rows = [accepted, snoozed, duplicate_a.json(), duplicate_b.json()]
    assert len({row["id"] for row in duplicate_rows}) == 4
    assert _count_decisions(ITEM_CODE) == 5
    # The earlier rows were neither changed nor deleted.
    after = _stored_decision(accepted["id"])
    for column in ALL_COLUMNS:
        assert after[column] == snapshot[column], column
    dismissed_snapshot = _stored_decision(dismissed["id"])
    assert dismissed_snapshot["decision"] == "dismissed"
    assert dismissed_snapshot["reason"] == dismissed_body["reason"]

    # -- GET limits and the one-code-without-the-other 422. --------------
    _assert_validation_envelope(
        user_client.get("/api/drift/decisions", params={"item_code": ITEM_CODE})
    )
    _assert_validation_envelope(
        user_client.get("/api/drift/decisions", params={"site_code": SITE_CODE})
    )
    _assert_validation_envelope(
        user_client.get("/api/drift/decisions", params={"limit": 0})
    )
    _assert_validation_envelope(
        user_client.get("/api/drift/decisions", params={"limit": 1001})
    )
    _assert_validation_envelope(
        user_client.get("/api/drift/decisions", params={"offset": -1})
    )
    paged = user_client.get("/api/drift/decisions", params={"limit": 2})
    assert paged.status_code == 200
    # Only one item-site has decisions at this point.
    assert len(paged.json()) == 1

    # -- AC-3: decided_at DESC, id DESC in history mode. -----------------
    _seed_decision(
        "id-172-h1",
        ITEM_CODE,
        SITE_CODE,
        "accepted",
        "2026-09-01T10:00:00+00:00",
    )
    _seed_decision(
        "id-172-h2",
        ITEM_CODE,
        SITE_CODE,
        "snoozed",
        "2026-09-02T10:00:00+00:00",
        snooze_until="2026-09-09",
    )
    # id-172-h3 shares id-172-h2's decided_at and sorts first: id DESC
    # is the tiebreaker.
    _seed_decision(
        "id-172-h3",
        ITEM_CODE,
        SITE_CODE,
        "dismissed",
        "2026-09-02T10:00:00+00:00",
        reason="same-instant twin",
    )
    history_resp = user_client.get(
        "/api/drift/decisions",
        params={"item_code": ITEM_CODE, "site_code": SITE_CODE},
    )
    assert history_resp.status_code == 200
    history_ids = [row["id"] for row in history_resp.json()]
    assert history_ids == [
        duplicate_b.json()["id"],
        duplicate_a.json()["id"],
        snoozed["id"],
        dismissed["id"],
        accepted["id"],
        "id-172-h3",
        "id-172-h2",
        "id-172-h1",
    ], history_ids
    # decided_at is non-increasing along the history, id decides ties.
    history_rows = history_resp.json()
    for earlier, later in zip(history_rows, history_rows[1:]):
        assert earlier["decided_at"] >= later["decided_at"]
        if earlier["decided_at"] == later["decided_at"]:
            assert earlier["id"] > later["id"]

    # -- AC-3: decided_at DESC, id DESC in current mode. -----------------
    _seed_decision(
        "id-172-a1",
        "ITEM-ORD-A",
        "SITE-ORD",
        "accepted",
        "2026-09-10T00:00:00+00:00",
    )
    _seed_decision(
        "id-172-a2",
        "ITEM-ORD-A",
        "SITE-ORD",
        "dismissed",
        "2026-09-11T00:00:00+00:00",
        reason="newer per item-site",
    )
    # b2 wins over b1 (same decided_at, larger id) and over a2 (same
    # decided_at, larger id than a2) in the unfiltered list.
    _seed_decision(
        "id-172-b1",
        "ITEM-ORD-B",
        "SITE-ORD",
        "accepted",
        "2026-09-11T00:00:00+00:00",
    )
    _seed_decision(
        "id-172-b2",
        "ITEM-ORD-B",
        "SITE-ORD",
        "snoozed",
        "2026-09-11T00:00:00+00:00",
        snooze_until="2026-09-18",
    )
    current_resp = user_client.get("/api/drift/decisions")
    assert current_resp.status_code == 200
    current_rows = current_resp.json()
    assert [row["id"] for row in current_rows] == [
        duplicate_b.json()["id"],
        "id-172-b2",
        "id-172-a2",
    ], current_rows
    assert [row["item_code"] for row in current_rows] == [
        ITEM_CODE,
        "ITEM-ORD-B",
        "ITEM-ORD-A",
    ]
    _assert_full_columns(current_rows)
    # Paging walks the same order.
    page_one = user_client.get("/api/drift/decisions", params={"limit": 1})
    assert page_one.status_code == 200
    assert [row["id"] for row in page_one.json()] == [duplicate_b.json()["id"]]
    page_two = user_client.get(
        "/api/drift/decisions",
        params={"limit": 1, "offset": 1},
    )
    assert [row["id"] for row in page_two.json()] == ["id-172-b2"]
    page_rest = user_client.get(
        "/api/drift/decisions",
        params={"limit": 1000, "offset": 3},
    )
    assert [row["id"] for row in page_rest.json()] == []

    # -- AC-5: the OpenAPI documents 404 and the append-only duplicates. -
    schema = app.openapi()
    post = schema["paths"]["/api/drift/decisions"]["post"]
    assert post["responses"]["404"]["description"] == (
        "No current drift row exists for the item-site."
    )
    description = post["description"]
    assert "duplicate POSTs are accepted as separate append-only duplicates" in (
        description
    )
    assert "without requiring an Idempotency-Key header" in description
    assert post["security"] == [{"bearerAuth": []}]
    get = schema["paths"]["/api/drift/decisions"]["get"]
    assert get["security"] == [{"bearerAuth": []}]

    # -- Identity, viewer pass: the admin-only control is absent for a
    #    role "user" while the page's data still renders. ----------------
    viewer_current = user_client.get("/api/drift/decisions")
    assert viewer_current.status_code == 200
    assert [row["id"] for row in viewer_current.json()] == [
        row["id"] for row in current_rows
    ]
    viewer_history = user_client.get(
        "/api/drift/decisions",
        params={"item_code": ITEM_CODE, "site_code": SITE_CODE},
    )
    assert viewer_history.status_code == 200
    assert [row["id"] for row in viewer_history.json()] == history_ids
    viewer_post = user_client.post(
        "/api/drift/decisions",
        json={"item_code": ITEM_CODE, "site_code": SITE_CODE, "decision": "accepted"},
    )
    assert viewer_post.status_code == 403
    assert viewer_post.json()["detail"] == (
        "Role 'admin' is required to perform this action."
    )
    assert anon_client.get("/api/drift/decisions").status_code == 401
