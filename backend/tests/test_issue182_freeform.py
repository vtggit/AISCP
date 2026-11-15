"""Issue #182 proof: a summary of every item-site's current planner decision.

The proof covers, in one test:

* the route boundary — ``GET /api/drift/decisions/summary`` answers 401
  without a token, is readable by a non-admin viewer, and is a
  separate route from ``GET /api/drift/decisions``, which keeps its
  slice-1 behaviour (a bare JSON array of current decisions without
  the code filters, the item-site's full history with both);
* the summary buckets over the current decision of every item-site
  (the latest by decided_at, id breaking ties), each item-site's
  decision recorded through ``POST /api/drift/decisions`` against a
  drift row seeded at fixed past dates: with one accepted decision
  whose item-site's ``item_sites.reorder_point`` is then updated to
  the recommended value and one accepted decision left unchanged,
  ``applied`` and ``outstanding`` each count 1 and carry their
  snapshotted money, and ``accepted`` is their sum (in count and in
  ``money_at_stake``);
* a later dismissal of the applied item-site moves it out of
  ``accepted`` and into ``dismissed``;
* a reorder point 2 units away from the recommended value counts as
  outstanding (while 1 unit away counts as applied).
"""

import time
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.planner_decisions import router as planner_decisions_router
from app.db.connection import get_connection
from app.main import app

ITEM_A = "ITEM-182-A"
SITE_A = "SITE-182-A"
ITEM_B = "ITEM-182-B"
SITE_B = "SITE-182-B"
UNIT_COST_A = 2.5
UNIT_COST_B = 4.0
CURRENT_REORDER_POINT_A = 120.0
CURRENT_REORDER_POINT_B = 150.0
ORDER_QTY_A = 80.0
ORDER_QTY_B = 60.0
FIRST_DAY = date(2025, 9, 1)
DEMAND_DAYS = 365  # 10/day through 2026-08-31
AS_OF = "2026-08-31"  # the latest seeded demand event

DECISION_COLUMNS = {
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
    """Two item-sites with drift rows, on fixed past calendar dates.

    10/day demand from 2025-09-01 through 2026-08-31 (the latest
    demand event, so the default as-of is 2026-08-31) and pooled
    9/10/10-day lead times per item, so both recommendations are
    "ok"; both item-sites carry a reorder point well above the
    recommended value, so both are "drift" rows with money at stake.
    """
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for prefix, item_code, site_code in (
                ("a", ITEM_A, SITE_A),
                ("b", ITEM_B, SITE_B),
            ):
                cur.executemany(
                    "INSERT INTO demand_events "
                    "(id, business_key, item_code, site_code, quantity, "
                    "occurred_on, event_type, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                    [
                        (
                            f"id-de-182-{prefix}-{day_index}",
                            f"de-182-{prefix}-{day_index}",
                            item_code,
                            site_code,
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
                            f"id-lt-182-{prefix}-1",
                            f"lt-182-{prefix}-1",
                            f"SUP-182-{prefix.upper()}",
                            item_code,
                            "2026-05-01",
                            "2026-05-10",
                            "test",
                        ),
                        (
                            f"id-lt-182-{prefix}-2",
                            f"lt-182-{prefix}-2",
                            f"SUP-182-{prefix.upper()}",
                            item_code,
                            "2026-05-05",
                            "2026-05-15",
                            "test",
                        ),
                        (
                            f"id-lt-182-{prefix}-3",
                            f"lt-182-{prefix}-3",
                            f"SUP-182-{prefix.upper()}",
                            item_code,
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
                        f"id-is-182-{prefix}",
                        item_code,
                        site_code,
                        UNIT_COST_A if prefix == "a" else UNIT_COST_B,
                        (
                            CURRENT_REORDER_POINT_A
                            if prefix == "a"
                            else CURRENT_REORDER_POINT_B
                        ),
                        ORDER_QTY_A if prefix == "a" else ORDER_QTY_B,
                        "test",
                    ),
                )
        conn.commit()
    finally:
        conn.close()


def _drift_row(client: TestClient, item_code: str, site_code: str) -> dict:
    """The item-site's current drift row through GET /api/drift/by-key."""
    resp = client.get(
        "/api/drift/by-key",
        params={"item_code": item_code, "site_code": site_code},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post_decision(
    client: TestClient,
    item_code: str,
    site_code: str,
    decision: str,
    reason: str | None = None,
) -> dict:
    """Record a decision through POST /api/drift/decisions (201)."""
    time.sleep(0.02)  # distinct decided_at per call (ordering of the current)
    body = {"item_code": item_code, "site_code": site_code, "decision": decision}
    if reason is not None:
        body["reason"] = reason
    resp = client.post("/api/drift/decisions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _set_reorder_point(item_code: str, site_code: str, value: float) -> None:
    """Move the item-site's current reorder point directly."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE item_sites SET reorder_point = %s "
                "WHERE item_code = %s AND site_code = %s",
                (value, item_code, site_code),
            )
            assert cur.rowcount == 1, (item_code, site_code)
        conn.commit()
    finally:
        conn.close()


def _summary(client: TestClient) -> dict:
    resp = client.get("/api/drift/decisions/summary")
    assert resp.status_code == 200, resp.text
    return resp.json()


def _assert_shape(body: dict) -> None:
    assert set(body.keys()) == {
        "accepted",
        "applied",
        "outstanding",
        "dismissed",
        "snoozed",
    }, body
    for name in ("accepted", "applied", "outstanding"):
        assert set(body[name].keys()) == {"count", "money_at_stake", "without_money"}, (
            name,
            body,
        )
    for name in ("dismissed", "snoozed"):
        assert set(body[name].keys()) == {"count"}, (name, body)


def _money(count: int, money_at_stake: float, without_money: int = 0) -> dict:
    return {
        "count": count,
        "money_at_stake": money_at_stake,
        "without_money": without_money,
    }


def test_issue182_freeform(client, admin_token, user_token):
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    user_client = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_client = TestClient(app)

    _seed()

    # -- The seeded item-sites have "drift" rows at a fixed as-of. ------
    row_a = _drift_row(admin_client, ITEM_A, SITE_A)
    assert row_a["as_of"] == AS_OF
    assert row_a["category"] == "drift"
    assert row_a["current_reorder_point"] == CURRENT_REORDER_POINT_A
    recommended_a = row_a["reorder_point_units"]
    assert isinstance(recommended_a, int) and recommended_a > 0
    assert abs(CURRENT_REORDER_POINT_A - recommended_a) >= 2
    assert row_a["money_at_stake"] is not None

    row_b = _drift_row(admin_client, ITEM_B, SITE_B)
    assert row_b["as_of"] == AS_OF
    assert row_b["category"] == "drift"
    assert row_b["current_reorder_point"] == CURRENT_REORDER_POINT_B
    recommended_b = row_b["reorder_point_units"]
    assert isinstance(recommended_b, int) and recommended_b > 0
    assert abs(CURRENT_REORDER_POINT_B - recommended_b) >= 2
    assert row_b["money_at_stake"] is not None

    # -- The route boundary: 401 without a token. ------------------------
    anon = anon_client.get("/api/drift/decisions/summary")
    assert anon.status_code == 401
    assert (
        anon.json()["detail"]
        == "Authentication required. Provide a valid Bearer token."
    )

    # -- The summary route carries its own auth dependency: mounted on a
    #    bare app without the app's router-level gate it still answers
    #    401 without a token, and 200 to any authenticated user. --------
    ungated = FastAPI()
    ungated.include_router(planner_decisions_router)
    with TestClient(ungated) as ungated_client:
        ungated_anon = ungated_client.get("/api/drift/decisions/summary")
        assert ungated_anon.status_code == 401
        assert (
            ungated_anon.json()["detail"]
            == "Authentication required. Provide a valid Bearer token."
        )
        ungated_viewer = ungated_client.get(
            "/api/drift/decisions/summary",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert ungated_viewer.status_code == 200, ungated_viewer.text

    # -- Both accepted decisions are recorded through the POST. ----------
    money_a = _post_decision(
        admin_client, ITEM_A, SITE_A, "accepted", "agreed with the review"
    )["money_at_stake"]
    money_b = _post_decision(
        admin_client, ITEM_B, SITE_B, "accepted", "agreed with the review"
    )["money_at_stake"]
    assert money_a == row_a["money_at_stake"]
    assert money_b == row_b["money_at_stake"]

    # -- Nothing moved yet: both accepted decisions are outstanding. -----
    body = _summary(user_client)
    _assert_shape(body)
    assert body["accepted"] == _money(2, money_a + money_b)
    assert body["applied"] == _money(0, 0)
    assert body["outstanding"] == _money(2, money_a + money_b)
    assert body["dismissed"] == {"count": 0}
    assert body["snoozed"] == {"count": 0}

    # -- A moves to its recommended value: applied and outstanding each
    #    count 1, each carrying its snapshotted money, and accepted is
    #    their sum in count and in money. ---------------------------------
    _set_reorder_point(ITEM_A, SITE_A, float(recommended_a))
    body = _summary(user_client)
    _assert_shape(body)
    assert body["applied"] == _money(1, money_a)
    assert body["outstanding"] == _money(1, money_b)
    assert body["accepted"] == _money(2, money_a + money_b)
    assert body["dismissed"] == {"count": 0}
    assert body["snoozed"] == {"count": 0}
    assert (
        body["accepted"]["count"]
        == body["applied"]["count"] + body["outstanding"]["count"]
    )
    assert (
        body["accepted"]["money_at_stake"]
        == body["applied"]["money_at_stake"] + body["outstanding"]["money_at_stake"]
    )

    # -- A later dismissal of the applied item-site moves it out of
    #    accepted and into dismissed. --------------------------------------
    _post_decision(admin_client, ITEM_A, SITE_A, "dismissed", "changed my mind")
    body = _summary(user_client)
    _assert_shape(body)
    assert body["accepted"] == _money(1, money_b)
    assert body["applied"] == _money(0, 0)
    assert body["outstanding"] == _money(1, money_b)
    assert body["dismissed"] == {"count": 1}
    assert body["snoozed"] == {"count": 0}

    # -- A reorder point 2 units away from the recommended value counts as
    #    outstanding (B re-accepted at recommended + 2). -------------------
    _set_reorder_point(ITEM_B, SITE_B, float(recommended_b) + 2)
    money_b_two_away = _post_decision(
        admin_client, ITEM_B, SITE_B, "accepted", "two units away"
    )["money_at_stake"]
    assert money_b_two_away is not None
    body = _summary(user_client)
    _assert_shape(body)
    assert body["accepted"] == _money(1, money_b_two_away)
    assert body["applied"] == _money(0, 0)
    assert body["outstanding"] == _money(1, money_b_two_away)
    assert body["dismissed"] == {"count": 1}
    assert body["snoozed"] == {"count": 0}

    # -- ... and 1 unit away is within tolerance, so it applies. ----------
    _set_reorder_point(ITEM_B, SITE_B, float(recommended_b) + 1)
    final_b = _post_decision(admin_client, ITEM_B, SITE_B, "accepted", "one unit away")
    money_b_one_away = final_b["money_at_stake"]
    assert money_b_one_away is not None
    body = _summary(user_client)
    _assert_shape(body)
    assert body["accepted"] == _money(1, money_b_one_away)
    assert body["applied"] == _money(1, money_b_one_away)
    assert body["outstanding"] == _money(0, 0)
    assert body["dismissed"] == {"count": 1}
    assert body["snoozed"] == {"count": 0}

    # -- GET /api/drift/decisions keeps its slice-1 behaviour: the summary
    #    is a separate route, never a filter of the list. ------------------
    current_list = user_client.get("/api/drift/decisions")
    assert current_list.status_code == 200, current_list.text
    current_rows = current_list.json()
    assert isinstance(current_rows, list)
    assert {row["item_code"] for row in current_rows} == {ITEM_A, ITEM_B}
    by_site = {(row["item_code"], row["site_code"]): row for row in current_rows}
    assert by_site[(ITEM_A, SITE_A)]["decision"] == "dismissed"
    assert by_site[(ITEM_B, SITE_B)]["decision"] == "accepted"
    assert by_site[(ITEM_B, SITE_B)]["id"] == final_b["id"]
    for row in current_rows:
        assert set(row.keys()) == DECISION_COLUMNS, row
    history = user_client.get(
        "/api/drift/decisions",
        params={"item_code": ITEM_B, "site_code": SITE_B},
    )
    assert history.status_code == 200, history.text
    history_rows = history.json()
    assert [row["decision"] for row in history_rows] == [
        "accepted",
        "accepted",
        "accepted",
    ]
    assert history_rows[0]["id"] == by_site[(ITEM_B, SITE_B)]["id"]
    assert history_rows[0]["recommended_reorder_point_units"] == recommended_b
    # And the summary of this final state still agrees with the list.
    body = _summary(user_client)
    assert body["dismissed"] == {"count": 1}
    assert body["applied"]["count"] == 1
    assert body["outstanding"]["count"] == 0
