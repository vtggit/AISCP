"""Issue #221 proof: GET /api/decisions/summary, the cross-kind decision summary.

The proof covers, in one test, with drift, excess and stockout rows
seeded at fixed past dates (10/day, 1/day and 5/day demand from
2025-09-01 through 2026-08-31 — the latest demand event, so the
default as-of is 2026-08-31 — and 9/10/11-day lead-time
observations, so the lead time is 10 days with a standard deviation
of 1) and every decision recorded through the three
``POST .../decisions`` endpoints:

* the route boundary — ``GET /api/decisions/summary`` answers 401
  without a token, enforces the same authentication and
  authorization as ``GET /api/drift/decisions/summary`` (401
  unauthenticated, open to every authenticated user, no role
  restriction), and its own dependency keeps answering 401 even when
  the router is mounted without the app's include-time gate;
* the non-admin identity — a role "user" caller (the development
  token suffixed ``:user``, validated the production way through the
  Bearer header) reads the summary while the admin-only decision
  POSTs stay 403 for that caller, so the admin-only control is
  absent on the read route while the summary still renders;
* the buckets over each item-site's current decision (the latest by
  decided_at, id breaking ties): an accepted excess item-site whose
  on-hand stock is then reduced so that it leaves the
  dead-and-excess list counts as excess applied with its stored
  money_tied_up, an accepted excess item-site that stays listed
  counts as outstanding, an accepted stockout item-site whose
  on-hand stock is then raised so that it leaves the stockout list
  counts as stockout applied with its stored money_at_risk, the
  drift buckets equal ``GET /api/drift/decisions/summary`` bucket by
  bucket (including a null-money accepted decision counted in
  without_money), a dismissal and a snooze count in their kind in
  every kind, ``accepted`` equals ``applied`` plus ``outstanding``
  in count and money, and ``total`` is the bucket-by-bucket sum of
  the three kinds;
* the ranked excess and stockout rows are each computed exactly once
  per request, and the summary never changes data.
"""

import time
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.decision_summary as decision_summary
from app.api.decision_summary import router as decision_summary_router
from app.db.connection import get_connection
from app.main import app

SITE = "SITE-221"
FIRST_DAY = date(2025, 9, 1)  # demand from here ...
DEMAND_DAYS = 365  # ... through 2026-08-31
AS_OF = "2026-08-31"  # the latest seeded demand event
AS_OF_STAMP = "2026-08-31T00:00:00+00"
SNOOZE_UNTIL = (min(date.today(), date.max - timedelta(7)) + timedelta(7)).isoformat()

# Drift items: current reorder point = recommended + gap (whole units),
# all "over" rows; D5 has no unit cost, so its money at stake is null.
D1 = "ITEM-221-D1"
D2 = "ITEM-221-D2"
D3 = "ITEM-221-D3"
D4 = "ITEM-221-D4"
D5 = "ITEM-221-D5"
DRIFT_GAPS = {D1: 20, D2: 10, D3: 8, D4: 6, D5: 10}

# Excess items (1/day demand: 360 units in the 12-month cover window,
# justified 360): on hand above justified, money tied up above.
E1 = "ITEM-221-E1"  # accepted; on hand reduced below justified -> applied
E2 = "ITEM-221-E2"  # accepted; stays listed -> outstanding
E3 = "ITEM-221-E3"  # dismissed
E4 = "ITEM-221-E4"  # snoozed

# Stockout items (5/day demand, lead-time demand 50 +/- 5): every
# position below 50 is listed, and 60 is above the acceptable risk.
S1 = "ITEM-221-S1"  # accepted; on hand raised to 60 -> applied
S2 = "ITEM-221-S2"  # accepted; stays listed -> outstanding
S3 = "ITEM-221-S3"  # dismissed
S4 = "ITEM-221-S4"  # snoozed

# item_code -> (daily demand, unit cost, reorder point at seed time)
ITEMS = {
    D1: (10.0, 2.0, 1000.0),
    D2: (10.0, 3.0, 1000.0),
    D3: (10.0, 1.5, 1000.0),
    D4: (10.0, 2.5, 1000.0),
    D5: (10.0, None, 1000.0),
    E1: (1.0, 2.0, 100.0),
    E2: (1.0, 3.0, 100.0),
    E3: (1.0, 1.0, 100.0),
    E4: (1.0, 0.5, 100.0),
    S1: (5.0, 2.0, 100.0),
    S2: (5.0, 1.5, 100.0),
    S3: (5.0, 1.0, 100.0),
    S4: (5.0, 0.5, 100.0),
}

# item_code -> on_hand quantity at decision time
ON_HAND = {
    E1: 400.0,
    E2: 500.0,
    E3: 400.0,
    E4: 380.0,
    S1: 45.0,
    S2: 40.0,
    S3: 30.0,
    S4: 25.0,
}

# The on-hand stock after the decisions: E1 drops below the justified
# cover (360 units) so it leaves the dead-and-excess list, and S1
# rises to 60 (p_stockout 0.0228, below the acceptable 0.05) so it
# leaves the stockout list.
ON_HAND_AFTER = {E1: 350.0, S1: 60.0}

EXPECTED_EXCESS_MONEY = {
    E1: 80.0,  # (400 - 360) * 2.0
    E2: 420.0,  # (500 - 360) * 3.0
    E3: 40.0,  # (400 - 360) * 1.0
    E4: 10.0,  # (380 - 360) * 0.5
}


def _seed() -> None:
    """The fixed-date rows: thirteen item-sites, one listed row each."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            demand_rows = []
            lead_time_rows = []
            item_site_rows = []
            on_hand_rows = []
            for item_code, (daily, unit_cost, reorder_point) in ITEMS.items():
                suffix = item_code.split("-")[2].lower()
                demand_rows.extend(
                    (
                        f"id-de-221-{suffix}-{day_index}",
                        f"de-221-{suffix}-{day_index}",
                        item_code,
                        SITE,
                        daily,
                        (FIRST_DAY + timedelta(days=day_index)).isoformat(),
                        "shipment",
                        "test",
                    )
                    for day_index in range(DEMAND_DAYS)
                )
                lead_time_rows.extend(
                    (
                        f"id-lt-221-{suffix}-{index}",
                        f"lt-221-{suffix}-{index}",
                        f"SUP-221-{suffix}",
                        item_code,
                        ordered.isoformat(),
                        received.isoformat(),
                        "test",
                    )
                    for index, (ordered, received) in enumerate(
                        (
                            (date(2026, 5, 1), date(2026, 5, 10)),  # 9 days
                            (date(2026, 5, 5), date(2026, 5, 15)),  # 10 days
                            (date(2026, 5, 9), date(2026, 5, 20)),  # 11 days
                        ),
                        start=1,
                    )
                )
                item_site_rows.append(
                    (
                        f"id-is-221-{suffix}",
                        item_code,
                        SITE,
                        unit_cost,
                        reorder_point,
                        "test",
                    )
                )
                if item_code in ON_HAND:
                    on_hand_rows.append(
                        (
                            f"id-oh-221-{suffix}",
                            item_code,
                            SITE,
                            ON_HAND[item_code],
                            AS_OF_STAMP,
                            "test",
                        )
                    )
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                demand_rows,
            )
            cur.executemany(
                "INSERT INTO lead_time_observations "
                "(id, business_key, supplier_code, item_code, ordered_on, "
                "received_on, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                lead_time_rows,
            )
            cur.executemany(
                "INSERT INTO item_sites "
                "(id, item_code, site_code, unit_cost, reorder_point, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                item_site_rows,
            )
            cur.executemany(
                "INSERT INTO on_hand "
                "(id, item_code, site_code, quantity_on_hand, as_of, "
                "source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                on_hand_rows,
            )
        conn.commit()
    finally:
        conn.close()


def _set_reorder_point(item_code: str, value: float) -> None:
    """Move the item-site's current reorder point directly."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE item_sites SET reorder_point = %s "
                "WHERE item_code = %s AND site_code = %s",
                (value, item_code, SITE),
            )
            assert cur.rowcount == 1, item_code
        conn.commit()
    finally:
        conn.close()


def _set_on_hand(item_code: str, value: float) -> None:
    """Move the item-site's on-hand stock directly."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE on_hand SET quantity_on_hand = %s "
                "WHERE item_code = %s AND site_code = %s",
                (value, item_code, SITE),
            )
            assert cur.rowcount == 1, item_code
        conn.commit()
    finally:
        conn.close()


def _stored_state() -> dict:
    """The row counts and the two adjusted on-hand quantities."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT (SELECT COUNT(*) FROM planner_decisions), "
                "(SELECT COUNT(*) FROM excess_decisions), "
                "(SELECT COUNT(*) FROM stockout_decisions)"
            )
            counts = cur.fetchone()
            cur.execute(
                "SELECT item_code, quantity_on_hand FROM on_hand "
                "WHERE item_code IN (%s, %s) ORDER BY item_code",
                (E1, S1),
            )
            on_hand = {row[0]: row[1] for row in cur.fetchall()}
    finally:
        conn.close()
    return {"counts": tuple(counts), "on_hand": on_hand}


def _drift_row(client: TestClient, item_code: str) -> dict:
    resp = client.get(
        "/api/drift/by-key", params={"item_code": item_code, "site_code": SITE}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _post_drift(client: TestClient, item_code: str, payload: dict) -> dict:
    """Record a drift decision through POST /api/drift/decisions (201)."""
    time.sleep(0.02)  # distinct decided_at per call (ordering of the current)
    body = {"item_code": item_code, "site_code": SITE}
    body.update(payload)
    resp = client.post("/api/drift/decisions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post_excess(client: TestClient, item_code: str, payload: dict) -> dict:
    """Record an excess decision through POST /api/excess/decisions (201)."""
    time.sleep(0.02)  # distinct decided_at per call (ordering of the current)
    body = {"item_code": item_code, "site_code": SITE}
    body.update(payload)
    resp = client.post("/api/excess/decisions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _post_stockout(client: TestClient, item_code: str, payload: dict) -> dict:
    """Record a stockout decision through POST /api/stockout/decisions (201)."""
    time.sleep(0.02)  # distinct decided_at per call (ordering of the current)
    body = {"item_code": item_code, "site_code": SITE}
    body.update(payload)
    resp = client.post("/api/stockout/decisions", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _money(count: int, money: float, without_money: int = 0) -> dict:
    return {"count": count, "money": money, "without_money": without_money}


def _assert_shape(body: dict) -> None:
    assert set(body.keys()) == {"drift", "excess", "stockout", "total"}, body
    for kind in body.values():
        assert set(kind.keys()) == {
            "accepted",
            "applied",
            "outstanding",
            "dismissed",
            "snoozed",
        }, kind
        for name in ("accepted", "applied", "outstanding"):
            assert set(kind[name].keys()) == {
                "count",
                "money",
                "without_money",
            }, (name, kind)
        for name in ("dismissed", "snoozed"):
            assert set(kind[name].keys()) == {"count"}, (name, kind)


def test_issue221_freeform(client, admin_token, user_token):
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    user_client = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    anon_client = TestClient(app)

    _seed()

    # -- The seeded item-sites carry the expected rows at the fixed
    #    as-of, before any decision. -------------------------------------
    excess_rows = admin_client.get("/api/excess").json()
    excess_by_item = {row["item_code"]: row for row in excess_rows}
    for item_code, money in EXPECTED_EXCESS_MONEY.items():
        row = excess_by_item[item_code]
        assert row["as_of"] == AS_OF
        assert row["category"] == "excess"
        assert row["money_tied_up"] == money, item_code
    stockout_rows = admin_client.get("/api/stockout").json()
    stockout_by_item = {row["item_code"]: row for row in stockout_rows}
    for item_code in (S1, S2, S3, S4):
        assert stockout_by_item[item_code]["as_of"] == AS_OF
        assert stockout_by_item[item_code]["p_stockout"] > 0.5

    # -- The route boundary: 401 without a token, with the gate's exact
    #    body. ------------------------------------------------------------
    anon = anon_client.get("/api/decisions/summary")
    assert anon.status_code == 401
    assert (
        anon.json()["detail"]
        == "Authentication required. Provide a valid Bearer token."
    )

    # -- The summary route carries its own auth dependency: mounted on a
    #    bare app without the app's router-level gate it still answers
    #    401 without a token, and 200 to any authenticated user. --------
    ungated = FastAPI()
    ungated.include_router(decision_summary_router)
    with TestClient(ungated) as ungated_client:
        ungated_anon = ungated_client.get("/api/decisions/summary")
        assert ungated_anon.status_code == 401
        assert (
            ungated_anon.json()["detail"]
            == "Authentication required. Provide a valid Bearer token."
        )
        ungated_viewer = ungated_client.get(
            "/api/decisions/summary",
            headers={"Authorization": f"Bearer {user_token}"},
        )
        assert ungated_viewer.status_code == 200, ungated_viewer.text

    # -- Same authentication and authorization as the drift summary:
    #    unauthenticated 401, every authenticated role 200, on both. ----
    for test_client in (anon_client, user_client, admin_client):
        new_status = test_client.get("/api/decisions/summary").status_code
        drift_status = test_client.get("/api/drift/decisions/summary").status_code
        assert (
            new_status == drift_status == (401 if test_client is anon_client else 200)
        )

    # -- The drift item-sites drift over their recommended points, with
    #    the current reorder point set to recommended + gap. ------------
    drift_payloads = {
        D1: {"decision": "accepted"},
        D2: {"decision": "accepted"},
        D3: {"decision": "dismissed", "reason": "reviewed, no change"},
        D4: {"decision": "snoozed", "snooze_until": SNOOZE_UNTIL},
        D5: {"decision": "accepted"},
    }
    drift_money = {}
    for item_code, gap in DRIFT_GAPS.items():
        row = _drift_row(admin_client, item_code)
        assert row["as_of"] == AS_OF
        assert row["category"] == "drift"
        recommended = row["reorder_point_units"]
        assert isinstance(recommended, int) and recommended > 0
        _set_reorder_point(item_code, float(recommended + gap))
        stored = _post_drift(admin_client, item_code, drift_payloads[item_code])
        drift_money[item_code] = stored["money_at_stake"]
    assert drift_money[D1] is not None and drift_money[D2] is not None
    assert drift_money[D5] is None  # no unit cost -> null money at stake

    # -- The excess and stockout decisions, recorded through the POSTs. --
    stored_e1 = _post_excess(
        admin_client, E1, {"decision": "accepted", "action": "reduce"}
    )
    assert stored_e1["money_tied_up"] == EXPECTED_EXCESS_MONEY[E1]
    stored_e2 = _post_excess(
        admin_client, E2, {"decision": "accepted", "action": "dispose"}
    )
    assert stored_e2["money_tied_up"] == EXPECTED_EXCESS_MONEY[E2]
    _post_excess(admin_client, E3, {"decision": "dismissed", "reason": "seasonal"})
    _post_excess(
        admin_client, E4, {"decision": "snoozed", "snooze_until": SNOOZE_UNTIL}
    )
    stored_s1 = _post_stockout(
        admin_client, S1, {"decision": "accepted", "action": "expedite"}
    )
    assert stored_s1["money_at_risk"] is not None
    stored_s2 = _post_stockout(
        admin_client, S2, {"decision": "accepted", "action": "raise_order"}
    )
    assert stored_s2["money_at_risk"] is not None
    _post_stockout(admin_client, S3, {"decision": "dismissed", "reason": "backordered"})
    _post_stockout(
        admin_client, S4, {"decision": "snoozed", "snooze_until": SNOOZE_UNTIL}
    )

    # -- E1 drops to the justified cover so it leaves the
    #    dead-and-excess list; S1 rises above the acceptable risk so it
    #    leaves the stockout list; D1's reorder point moves to the
    #    recommended value so its accepted drift decision applies. -----
    for item_code, quantity in ON_HAND_AFTER.items():
        _set_on_hand(item_code, quantity)
    _set_reorder_point(D1, float(_drift_row(admin_client, D1)["reorder_point_units"]))

    # -- The summary, read the way a non-admin reads it. ----------------
    body = user_client.get("/api/decisions/summary").json()
    _assert_shape(body)

    # -- The drift buckets equal GET /api/drift/decisions/summary. ------
    drift_ref = user_client.get("/api/drift/decisions/summary").json()
    for name in ("accepted", "applied", "outstanding"):
        assert body["drift"][name] == _money(
            drift_ref[name]["count"],
            drift_ref[name]["money_at_stake"],
            drift_ref[name]["without_money"],
        )
    for name in ("dismissed", "snoozed"):
        assert body["drift"][name] == drift_ref[name]
    # ... and the expected buckets, including D5's null money.
    assert body["drift"]["accepted"] == _money(3, drift_money[D1] + drift_money[D2], 1)
    assert body["drift"]["applied"] == _money(1, drift_money[D1])
    assert body["drift"]["outstanding"] == _money(2, drift_money[D2], 1)
    assert body["drift"]["dismissed"] == {"count": 1}
    assert body["drift"]["snoozed"] == {"count": 1}

    # -- Excess: E1 (left the list) applied with its stored
    #    money_tied_up; E2 (still listed) outstanding. ------------------
    assert body["excess"] == {
        "accepted": _money(2, stored_e1["money_tied_up"] + stored_e2["money_tied_up"]),
        "applied": _money(1, stored_e1["money_tied_up"]),
        "outstanding": _money(1, stored_e2["money_tied_up"]),
        "dismissed": {"count": 1},
        "snoozed": {"count": 1},
    }

    # -- Stockout: S1 (left the list) applied with its stored
    #    money_at_risk; S2 (still listed) outstanding. ------------------
    assert body["stockout"] == {
        "accepted": _money(2, stored_s1["money_at_risk"] + stored_s2["money_at_risk"]),
        "applied": _money(1, stored_s1["money_at_risk"]),
        "outstanding": _money(1, stored_s2["money_at_risk"]),
        "dismissed": {"count": 1},
        "snoozed": {"count": 1},
    }

    # -- In every kind, accepted equals applied plus outstanding in
    #    count and money. ------------------------------------------------
    for kind in (body["drift"], body["excess"], body["stockout"]):
        assert (
            kind["accepted"]["count"]
            == kind["applied"]["count"] + kind["outstanding"]["count"]
        )
        assert (
            kind["accepted"]["money"]
            == kind["applied"]["money"] + kind["outstanding"]["money"]
        )

    # -- Total is the bucket-by-bucket sum of the three kinds. ----------
    for name in ("accepted", "applied", "outstanding"):
        assert body["total"][name] == _money(
            body["drift"][name]["count"]
            + body["excess"][name]["count"]
            + body["stockout"][name]["count"],
            body["drift"][name]["money"]
            + body["excess"][name]["money"]
            + body["stockout"][name]["money"],
            body["drift"][name]["without_money"]
            + body["excess"][name]["without_money"]
            + body["stockout"][name]["without_money"],
        )
    for name in ("dismissed", "snoozed"):
        assert body["total"][name] == {
            "count": (
                body["drift"][name]["count"]
                + body["excess"][name]["count"]
                + body["stockout"][name]["count"]
            )
        }

    # -- Nothing changes data: the summary reads only. -------------------
    before = _stored_state()
    assert before == _stored_state()
    recheck = user_client.get("/api/decisions/summary").json()
    assert recheck == body
    assert before == _stored_state()

    # -- The ranked rows are each computed exactly once per request. ----
    calls = {"excess": 0, "stockout": 0}
    real_excess_ranked = decision_summary.excess_ranked
    real_stockout_ranked = decision_summary.stockout_ranked

    def counting_excess(*args, **kwargs):
        calls["excess"] += 1
        return real_excess_ranked(*args, **kwargs)

    def counting_stockout(*args, **kwargs):
        calls["stockout"] += 1
        return real_stockout_ranked(*args, **kwargs)

    decision_summary.excess_ranked = counting_excess
    decision_summary.stockout_ranked = counting_stockout
    try:
        for _ in range(2):
            before_excess, before_stockout = calls["excess"], calls["stockout"]
            resp = user_client.get("/api/decisions/summary")
            assert resp.status_code == 200, resp.text
            assert resp.json() == body
            assert calls["excess"] - before_excess == 1
            assert calls["stockout"] - before_stockout == 1
    finally:
        decision_summary.excess_ranked = real_excess_ranked
        decision_summary.stockout_ranked = real_stockout_ranked

    # -- Identity, non-admin pass: the role "user" caller reads the
    #    summary (no admin-only control on the read route) while the
    #    admin-only decision POSTs stay 403 for that caller. ------------
    assert user_client.get("/api/decisions/summary").status_code == 200
    for url, payload in (
        (
            "/api/excess/decisions",
            {
                "item_code": E2,
                "site_code": SITE,
                "decision": "accepted",
                "action": "reduce",
            },
        ),
        (
            "/api/stockout/decisions",
            {
                "item_code": S2,
                "site_code": SITE,
                "decision": "accepted",
                "action": "expedite",
            },
        ),
        (
            "/api/drift/decisions",
            {"item_code": D2, "site_code": SITE, "decision": "accepted"},
        ),
    ):
        denied = user_client.post(url, json=payload)
        assert denied.status_code == 403, (url, denied.text)
