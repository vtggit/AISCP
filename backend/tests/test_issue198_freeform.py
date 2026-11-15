"""Issue #198 proof: append-only decisions on dead and excess stock rows.

The proof covers, in one test:

* the migration shape — ``excess_decisions`` exists with the documented
  columns, the decision CHECK constraint (only accepted / dismissed /
  snoozed), the action CHECK constraint (only stop_replenishing /
  reduce / dispose, null allowed) and the (item_code, site_code,
  decided_at) index,
* POST /api/excess/decisions behind the same auth gate as the excess
  router: 401 without a token, 403 for a user without the admin role,
  404 when the item-site has no current dead-and-excess row, and 201
  with the stored decision (new id, decided_at, the row's as_of /
  category / dead_reason / on_hand_units / excess_units /
  money_tied_up snapshot, decided_by = the token subject) when it does,
* every business 422 (accepted without an action, action on a
  non-accepted decision, an unknown action, dismissed without a
  non-blank reason, reason over 1000 characters, snoozed without
  snooze_until, snooze_until not after today or more than 90 days out,
  snooze_until on a non-snoozed decision, one filter code without the
  other) in FastAPI's HTTPValidationError envelope,
* GET /api/excess/decisions open to any authenticated user: the
  item-site's full decision history with both codes, the current
  decision of every item-site that has one without them (bare JSON
  array, limit 1..1000 default 100, offset 0 or more), and results
  ordered by decided_at DESC, id DESC,
* append-only semantics: the later dismissed decision becomes the
  current one while both appear, newest first, in the history and the
  earlier one is neither changed nor deleted,
* the OpenAPI documents the POST 404 and gates both routes on the
  Bearer security scheme.

The rows are seeded on fixed past dates so the ranked row and its
snapshot are deterministic: 10/day demand from 2025-07-06 through
2026-06-30 for one item-site (the latest demand event, so the default
as-of is 2026-06-30), and a second item-site with on hand but no
demand at all, listed as dead (never_moved) with its whole on hand in
excess.

The identity requirement is met at the API boundary (this issue adds
no frontend page): after the admin pass, the test reloads as a
non-admin viewer (role "user", the development token's "T:user" form)
and asserts the admin-only control (POST) is denied with 403 while the
page's data (GET /api/excess and GET /api/excess/decisions, both
modes) still renders with 200, and a request without a token still
gets 401.
"""

import time
from datetime import date, timedelta

import psycopg2
import psycopg2.errors
import psycopg2.extras
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

DEAD_ITEM = "ITEM-198"
DEAD_SITE = "SITE-198"
DEMAND_ITEM = "ITEM-198D"
DEMAND_SITE = "SITE-198D"
UNIT_COST = 2.5
DEAD_ON_HAND = 100.0
EXPECTED_MONEY = 250.0  # DEAD_ON_HAND * UNIT_COST
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
    "dead_reason",
    "on_hand_units",
    "excess_units",
    "money_tied_up",
    "decided_by",
    "decided_by_name",
    "decided_at",
}


def _seed() -> None:
    """The fixed-date rows: one dead item-site and one quiet demand item-site.

    10/day demand for DEMAND_ITEM from 2025-07-06 through 2026-06-30,
    so the default as-of (the latest demand event) is 2026-06-30.
    DEAD_ITEM has on hand but no demand at all, so it is listed as
    dead (never_moved) with its whole on hand in excess; DEMAND_ITEM's
    on hand (10) is far below its justified cover, so it is not listed.
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
                        f"id-de-198-{day_index}",
                        f"de-198-{day_index}",
                        DEMAND_ITEM,
                        DEMAND_SITE,
                        10.0,
                        (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for day_index in range(DEMAND_DAYS)
                ],
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "order_qty, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-is-198-dead",
                        DEAD_ITEM,
                        DEAD_SITE,
                        UNIT_COST,
                        None,
                        None,
                        "test",
                    ),
                    (
                        "id-is-198-demand",
                        DEMAND_ITEM,
                        DEMAND_SITE,
                        1.0,
                        None,
                        None,
                        "test",
                    ),
                ],
            )
            cur.executemany(
                "INSERT INTO on_hand "
                "(id, item_code, site_code, quantity_on_hand, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                [
                    (
                        "id-oh-198-dead",
                        DEAD_ITEM,
                        DEAD_SITE,
                        DEAD_ON_HAND,
                        AS_OF_STAMP,
                        "test",
                    ),
                    (
                        "id-oh-198-demand",
                        DEMAND_ITEM,
                        DEMAND_SITE,
                        10.0,
                        AS_OF_STAMP,
                        "test",
                    ),
                ],
            )
        conn.commit()
    finally:
        conn.close()


def _count_decisions(item_code: str | None = None) -> int:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            if item_code is None:
                cur.execute("SELECT COUNT(*) FROM excess_decisions")
            else:
                cur.execute(
                    "SELECT COUNT(*) FROM excess_decisions WHERE item_code = %s",
                    (item_code,),
                )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _stored_decision(decision_id: str) -> dict:
    """The raw excess_decisions row of one id, as the API stores it."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM excess_decisions WHERE id = %s", (decision_id,))
            row = cur.fetchone()
    finally:
        conn.close()
    assert row is not None, f"decision {decision_id} disappeared"
    return dict(row)


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
    return client.post("/api/excess/decisions", json=body)


def test_issue198_freeform(client, admin_token, user_token):
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    user_client = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_client = TestClient(app)

    _seed()

    # -- The seeded dead item-site is the one listed excess/dead row. --
    excess_resp = admin_client.get("/api/excess")
    assert excess_resp.status_code == 200, excess_resp.text
    excess_rows = excess_resp.json()
    assert [(row["item_code"], row["site_code"]) for row in excess_rows] == [
        (DEAD_ITEM, DEAD_SITE)
    ], excess_rows
    ranked_row = excess_rows[0]
    assert ranked_row["as_of"] == AS_OF
    assert ranked_row["category"] == "dead"
    assert ranked_row["dead_reason"] == "never_moved"
    assert ranked_row["on_hand_units"] == DEAD_ON_HAND
    assert ranked_row["excess_units"] == DEAD_ON_HAND
    assert ranked_row["money_tied_up"] == EXPECTED_MONEY

    # -- The table shape: columns, CHECK constraints, index. -----------
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable, "
                "character_maximum_length FROM information_schema.columns "
                "WHERE table_name = 'excess_decisions' ORDER BY ordinal_position"
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
            "dead_reason": ("character varying", "YES", 16),
            "on_hand_units": ("double precision", "NO", None),
            "excess_units": ("double precision", "NO", None),
            "money_tied_up": ("double precision", "YES", None),
            "decided_by": ("character varying", "NO", 255),
            "decided_by_name": ("character varying", "YES", 255),
            "decided_at": ("timestamp with time zone", "NO", None),
        }, columns
        with conn.cursor() as cur:
            cur.execute(
                "SELECT conname, pg_get_constraintdef(oid) "
                "FROM pg_constraint WHERE conrelid = 'excess_decisions'::regclass "
                "AND contype = 'c' ORDER BY conname"
            )
            checks = {row[0]: row[1] for row in cur.fetchall()}
        decision_check = checks["ck_excess_decisions_decision"]
        for value in ("accepted", "dismissed", "snoozed"):
            assert f"'{value}'" in decision_check, decision_check
        action_check = checks["ck_excess_decisions_action"]
        for value in ("stop_replenishing", "reduce", "dispose"):
            assert f"'{value}'" in action_check, action_check
        with conn.cursor() as cur:
            cur.execute(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = 'excess_decisions'"
            )
            indexes = [row[0] for row in cur.fetchall()]
        assert any(
            "item_code, site_code, decided_at" in index for index in indexes
        ), indexes
        # The CHECK constraints themselves refuse unknown values.
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO excess_decisions "
                "(id, item_code, site_code, decision, as_of, category, "
                "on_hand_units, excess_units, decided_by) "
                "VALUES ('id-198-check', %s, %s, 'approved', %s, 'dead', "
                "1, 1, 'seeder')",
                (DEAD_ITEM, DEAD_SITE, AS_OF),
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
                "INSERT INTO excess_decisions "
                "(id, item_code, site_code, decision, action, as_of, category, "
                "on_hand_units, excess_units, decided_by) "
                "VALUES ('id-198-check', %s, %s, 'accepted', 'sell', %s, 'dead', "
                "1, 1, 'seeder')",
                (DEAD_ITEM, DEAD_SITE, AS_OF),
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

    # -- Auth boundary: 401 without a token, 403 without admin. ---------
    anon_resp = anon_client.get("/api/excess/decisions")
    assert anon_resp.status_code == 401
    assert (
        anon_resp.json()["detail"]
        == "Authentication required. Provide a valid Bearer token."
    )
    anon_post = anon_client.post(
        "/api/excess/decisions",
        json={
            "item_code": DEAD_ITEM,
            "site_code": DEAD_SITE,
            "decision": "accepted",
            "action": "dispose",
        },
    )
    assert anon_post.status_code == 401
    forbidden = user_client.post(
        "/api/excess/decisions",
        json={
            "item_code": DEAD_ITEM,
            "site_code": DEAD_SITE,
            "decision": "accepted",
            "action": "dispose",
        },
    )
    assert forbidden.status_code == 403, forbidden.text
    assert forbidden.json()["detail"] == (
        "Role 'admin' is required to perform this action."
    )

    # -- AC-4: the accepted decision snapshots the ranked row. ----------
    accepted_body = {
        "item_code": DEAD_ITEM,
        "site_code": DEAD_SITE,
        "decision": "accepted",
        "action": "dispose",
        "reason": "stock is unserviceable; write it off",
    }
    accepted_resp = _post_decision(admin_client, accepted_body)
    assert accepted_resp.status_code == 201, accepted_resp.text
    accepted = accepted_resp.json()
    assert set(accepted.keys()) == ALL_COLUMNS
    assert accepted["id"]
    assert accepted["decided_at"]
    assert accepted["item_code"] == DEAD_ITEM
    assert accepted["site_code"] == DEAD_SITE
    assert accepted["decision"] == "accepted"
    assert accepted["action"] == "dispose"
    assert accepted["reason"] == "stock is unserviceable; write it off"
    assert accepted["snooze_until"] is None
    assert accepted["as_of"] == ranked_row["as_of"]
    assert accepted["category"] == ranked_row["category"]
    assert accepted["dead_reason"] == ranked_row["dead_reason"]
    assert accepted["on_hand_units"] == ranked_row["on_hand_units"]
    assert accepted["excess_units"] == ranked_row["excess_units"]
    assert accepted["money_tied_up"] == ranked_row["money_tied_up"]
    assert accepted["decided_by"] == ADMIN_SUB
    assert _count_decisions(DEAD_ITEM) == 1
    assert _stored_decision(accepted["id"])["decision"] == "accepted"

    # -- AC-4: the dismissed decision becomes the current one. ----------
    dismissed_body = {
        "item_code": DEAD_ITEM,
        "site_code": DEAD_SITE,
        "decision": "dismissed",
        "reason": "demand resumes with the new season; keep the stock",
    }
    dismissed_resp = _post_decision(admin_client, dismissed_body)
    assert dismissed_resp.status_code == 201, dismissed_resp.text
    dismissed = dismissed_resp.json()
    assert dismissed["id"] != accepted["id"]
    assert dismissed["decision"] == "dismissed"
    assert dismissed["action"] is None
    assert dismissed["reason"] == dismissed_body["reason"]
    assert dismissed["decided_by"] == ADMIN_SUB
    # The unfiltered list now carries the dismissed decision as the
    # item-site's current one.
    current_resp = user_client.get("/api/excess/decisions")
    assert current_resp.status_code == 200, current_resp.text
    current_rows = current_resp.json()
    assert isinstance(current_rows, list)
    assert [row["id"] for row in current_rows] == [dismissed["id"]]
    assert current_rows[0]["decision"] == "dismissed"
    _assert_full_columns(current_rows)
    # The item-site's history carries both, newest first.
    history_resp = user_client.get(
        "/api/excess/decisions",
        params={"item_code": DEAD_ITEM, "site_code": DEAD_SITE},
    )
    assert history_resp.status_code == 200, history_resp.text
    history_rows = history_resp.json()
    assert isinstance(history_rows, list)
    assert [row["id"] for row in history_rows] == [dismissed["id"], accepted["id"]]
    assert [row["decision"] for row in history_rows] == ["dismissed", "accepted"]
    _assert_full_columns(history_rows)

    # -- AC-2: every business 422, all in the validation envelope. ------
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "accepted",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "dismissed",
                "reason": "still unserviceable",
                "action": "reduce",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "accepted",
                "action": "scrub",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "dismissed",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "dismissed",
                "reason": "   ",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "dismissed",
                "reason": "x" * 1001,
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "snoozed",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "snoozed",
                "snooze_until": (date.today() + timedelta(days=91)).isoformat(),
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "snoozed",
                "snooze_until": date.today().isoformat(),
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "snoozed",
                "snooze_until": (date.today() - timedelta(days=1)).isoformat(),
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "accepted",
                "action": "dispose",
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
                "site_code": DEAD_SITE,
                "decision": "accepted",
                "action": "dispose",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM + "\x00",
                "site_code": DEAD_SITE,
                "decision": "accepted",
                "action": "dispose",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "dismissed",
                "reason": "bad\x00reason",
            },
        )
    )
    _assert_validation_envelope(
        _post_decision(
            admin_client,
            {
                "item_code": DEAD_ITEM,
                "site_code": DEAD_SITE,
                "decision": "snoozed",
                "snooze_until": "not-a-date",
            },
        )
    )
    # Nothing was stored by the rejected bodies.
    assert _count_decisions(DEAD_ITEM) == 2

    # -- AC-2: the 90-day snooze boundary is accepted (201). ------------
    snoozed_resp = _post_decision(
        admin_client,
        {
            "item_code": DEAD_ITEM,
            "site_code": DEAD_SITE,
            "decision": "snoozed",
            "snooze_until": (date.today() + timedelta(days=90)).isoformat(),
        },
    )
    assert snoozed_resp.status_code == 201, snoozed_resp.text
    snoozed = snoozed_resp.json()
    assert snoozed["decision"] == "snoozed"
    assert snoozed["snooze_until"] == (date.today() + timedelta(days=90)).isoformat()
    assert _count_decisions(DEAD_ITEM) == 3

    # -- AC-4: an unknown item-site has no current ranked row. ----------
    not_found = _post_decision(
        admin_client,
        {
            "item_code": "ITEM-NOPE",
            "site_code": DEAD_SITE,
            "decision": "accepted",
            "action": "dispose",
        },
    )
    assert not_found.status_code == 404, not_found.text
    assert not_found.json()["detail"] == "no current excess row for item site"
    assert _count_decisions() == 3

    # -- Append-only: the earlier decision was neither changed nor
    #    deleted. ---------------------------------------------------------
    snapshot = _stored_decision(accepted["id"])
    assert snapshot["decision"] == "accepted"
    assert snapshot["action"] == "dispose"
    assert snapshot["reason"] == accepted_body["reason"]
    dismissed_snapshot = _stored_decision(dismissed["id"])
    assert dismissed_snapshot["decision"] == "dismissed"
    assert dismissed_snapshot["reason"] == dismissed_body["reason"]

    # -- GET: one code without the other and the paging bounds. ---------
    _assert_validation_envelope(
        user_client.get("/api/excess/decisions", params={"item_code": DEAD_ITEM})
    )
    _assert_validation_envelope(
        user_client.get("/api/excess/decisions", params={"site_code": DEAD_SITE})
    )
    _assert_validation_envelope(
        user_client.get("/api/excess/decisions", params={"limit": 0})
    )
    _assert_validation_envelope(
        user_client.get("/api/excess/decisions", params={"limit": 1001})
    )
    _assert_validation_envelope(
        user_client.get("/api/excess/decisions", params={"offset": -1})
    )
    paged = user_client.get("/api/excess/decisions", params={"limit": 2})
    assert paged.status_code == 200
    # Only one item-site has decisions at this point.
    assert len(paged.json()) == 1

    # -- AC-3: decided_at DESC, id DESC in history mode. ----------------
    history_resp = user_client.get(
        "/api/excess/decisions",
        params={"item_code": DEAD_ITEM, "site_code": DEAD_SITE},
    )
    assert history_resp.status_code == 200
    history_ids = [row["id"] for row in history_resp.json()]
    assert history_ids == [snoozed["id"], dismissed["id"], accepted["id"]], history_ids
    # decided_at is non-increasing along the history, id decides ties.
    history_rows = history_resp.json()
    for earlier, later in zip(history_rows, history_rows[1:]):
        assert earlier["decided_at"] >= later["decided_at"]
        if earlier["decided_at"] == later["decided_at"]:
            assert earlier["id"] > later["id"]
    # Paging walks the same order.
    page_one = user_client.get(
        "/api/excess/decisions",
        params={"item_code": DEAD_ITEM, "site_code": DEAD_SITE, "limit": 1},
    )
    assert page_one.status_code == 200
    assert [row["id"] for row in page_one.json()] == [snoozed["id"]]
    page_rest = user_client.get(
        "/api/excess/decisions",
        params={
            "item_code": DEAD_ITEM,
            "site_code": DEAD_SITE,
            "limit": 1,
            "offset": 3,
        },
    )
    assert [row["id"] for row in page_rest.json()] == []

    # -- OpenAPI: the POST documents the 404; both routes are gated. ----
    schema = app.openapi()
    post = schema["paths"]["/api/excess/decisions"]["post"]
    assert post["responses"]["404"]["description"] == (
        "No current excess row exists for the item-site."
    )
    assert post["security"] == [{"bearerAuth": []}]
    get = schema["paths"]["/api/excess/decisions"]["get"]
    assert get["security"] == [{"bearerAuth": []}]

    # -- Identity, viewer pass: the admin-only control is absent for a
    #    role "user" while the page's data still renders. ----------------
    viewer_excess = user_client.get("/api/excess")
    assert viewer_excess.status_code == 200
    assert [(row["item_code"], row["site_code"]) for row in viewer_excess.json()] == []
    viewer_current = user_client.get("/api/excess/decisions")
    assert viewer_current.status_code == 200
    viewer_current_rows = viewer_current.json()
    assert isinstance(viewer_current_rows, list)
    assert [row["id"] for row in viewer_current_rows] == [snoozed["id"]]
    viewer_history = user_client.get(
        "/api/excess/decisions",
        params={"item_code": DEAD_ITEM, "site_code": DEAD_SITE},
    )
    assert viewer_history.status_code == 200
    assert [row["id"] for row in viewer_history.json()] == history_ids
    viewer_post = user_client.post(
        "/api/excess/decisions",
        json={
            "item_code": DEAD_ITEM,
            "site_code": DEAD_SITE,
            "decision": "accepted",
            "action": "dispose",
        },
    )
    assert viewer_post.status_code == 403
    assert viewer_post.json()["detail"] == (
        "Role 'admin' is required to perform this action."
    )
    assert _count_decisions() == 3
    assert anon_client.get("/api/excess/decisions").status_code == 401
