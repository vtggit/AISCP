"""Issue #742 proof: the DDMRP plan.

AC-1 — against the live app and the migrated test database, six
designated item-sites (``PUT /api/ddmrp/buffers`` the way test 740
designates them), demand events and lead-time observations seeded the
way test 740 seeds them, and on-hand rows and open purchase lines:
``GET /api/ddmrp/plan`` (any signed-in user) answers, as of the
demand statistics' default as-of date, the "ok" zone rows — built
with the same shared code the zones route uses, as of the same date
and with the same numbers — each enriched with the on-hand quantity
(0 with the note "no on-hand record; treated as 0" when the pair has
no on-hand row), the open supply (the positive open units promised
null or within 90 days before the as-of date, future dates included,
a stale note when a line is excluded), the qualified demand 0 with
the note "no open sales-order demand in the feed", the net flow, the
planning zone, the order signal, the suggested order units, the
planning priority percentage, the on-hand status and the on-hand
percentage of the top of red — ordered by the planning priority
ascending (nulls last), then item_code and site_code, with the
summary counts.  The route writes nothing: the zones response is
unchanged and the row counts of ``on_hand``, ``open_purchase_lines``
and ``ddmrp_buffers`` are unchanged; and no token answers 401.

AC-2 — for a row whose zones are computed from the zones route's own
numbers: on hand below the top of red with no open supply gives
"red", the order signal, ``suggested_order_units`` =
ceil(top_of_green − on_hand) and "critical" or "low" by the 50% rule;
enough open supply to lift the net flow above the top of yellow
gives no signal and 0 suggested; a line promised 120 days before the
as-of is excluded with the stale note; a pair without an on-hand
record reads on hand 0, the note and "stocked_out"; every row
carries "no open sales-order demand in the feed"; and the ordering
by ``planning_priority_pct`` and the summary counts hold.

AC-3 — the open supply includes a line promised exactly 90 days
before the as-of date and a line with a future ``promised_on`` date;
a line promised 120 days before the as-of is stale.

Identity — the plan route is open to any signed-in user, so the
proof also runs as a NON-ADMIN (role "user"): a second pass in the
same test after a full reload, with the page booted the production
way (the token it would take from the URL hash as
``#access_token=...`` — the development token, role "user" — sent as
its Bearer token), ``GET /api/auth/config`` answering publicly and
``GET /api/auth/me`` resolving the viewer, sees the plan page still
render (200 with every planned row) while the admin-only controls
(the ``PUT``/``DELETE`` of ``/api/ddmrp/buffers``) are absent for
that caller (403), and no token is still a 401.
"""

import math
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

PLAN_URL = "/api/ddmrp/plan"
ZONES_URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"

SITE_A = "SITE-742-1"
SITE_B = "SITE-742-2"
SITE_C = "SITE-742-3"
SITE_D = "SITE-742-4"
SITE_E = "SITE-742-5"
SITE_F = "SITE-742-6"
ITEM_A = "ITEM-742A"  # on hand 10, no supply: "red", signal, "low"
ITEM_B = "ITEM-742B"  # on hand 10, 105 open supply: "green", no signal
ITEM_C = "ITEM-742C"  # no on-hand record: 0, note, "stocked_out"
ITEM_D = "ITEM-742D"  # on hand 5, one stale line: "red", signal, "critical"
ITEM_E = "ITEM-742E"  # intermittent, master lead time 0: null priority, "over"
ITEM_F = "ITEM-742F"  # on hand 30, no supply: "yellow", signal, "ok"

SMOOTH_ITEMS = (
    (ITEM_A, SITE_A),
    (ITEM_B, SITE_B),
    (ITEM_C, SITE_C),
    (ITEM_D, SITE_D),
    (ITEM_F, SITE_F),
)

DESIGNATED = SMOOTH_ITEMS + ((ITEM_E, SITE_E),)

ON_HAND_NOTE = "no on-hand record; treated as 0"
DEMAND_NOTE = "no open sales-order demand in the feed"

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
ON_HAND_SQL = (
    "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
    "as_of, source_system) VALUES (%s, %s, %s, %s, %s, 'test')"
)
OPEN_LINE_SQL = (
    "INSERT INTO open_purchase_lines (id, business_key, item_code, "
    "site_code, supplier_code, order_type, open_units, promised_on, "
    "ordered_on, as_of, source_system) "
    "VALUES (%s, %s, %s, %s, %s, 'po', %s, %s, %s, %s, 'test')"
)


def _seed(cur, sql, params):
    cur.execute(sql, params)


def _item_site(cur, item, site, master=None):
    """The item-site's current planning settings (the way test 740
    seeds its item-sites), with the master lead time when given."""
    _seed(
        cur,
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, master_lead_time_days, "
        "source_system) VALUES (%s, %s, %s, 1.0, 25.0, 10.0, %s, 'test')",
        (f"id-is-742-{item}", item, site, master),
    )


def _demand_events(cur, item, site, daily):
    """One demand event per day of ``daily`` for one item (the way
    test 740 seeds its demand events)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-742-{item}-{day.isoformat()}",
                f"de-742-{item}-{day.isoformat()}",
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


def _lead_times(cur, item, received):
    """Three lead-time observations of 8, 10 and 12 days: pooled mean
    10, sample std 2, not low confidence (the way test 740 seeds its
    lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        _seed(
            cur,
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-742-{item}-{i}",
                f"lt-742-{item}-{i}",
                f"SUP-742-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def _on_hand(cur, item, site, quantity, as_of):
    _seed(
        cur,
        ON_HAND_SQL,
        (f"id-oh-742-{item}", item, site, quantity, as_of),
    )


def _open_line(cur, item, site, key, open_units, promised_on, as_of):
    _seed(
        cur,
        OPEN_LINE_SQL,
        (
            f"id-opl-742-{key}",
            f"opl-742-{key}",
            item,
            site,
            f"SUP-742-{item}",
            open_units,
            promised_on.isoformat() if promised_on is not None else None,
            (
                (promised_on - timedelta(days=10)).isoformat()
                if promised_on is not None
                else None
            ),
            as_of,
        ),
    )


def _row_counts() -> dict:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            counts = {}
            for table in ("on_hand", "open_purchase_lines", "ddmrp_buffers"):
                cur.execute(f"SELECT count(*) FROM {table}")
                counts[table] = cur.fetchone()[0]
            return counts
    finally:
        conn.close()


def _designations() -> list[tuple]:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT item_code, site_code FROM ddmrp_buffers "
                "ORDER BY item_code, site_code"
            )
            return [tuple(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _expected_plan_row(
    zone_row: dict, on_hand: float, open_supply: float, notes: list[str]
) -> dict:
    """The expected plan row of one item-site, computed from the
    zones route's own numbers (its zone fields pass through
    unchanged)."""
    top_of_red = zone_row["top_of_red"]
    top_of_yellow = zone_row["top_of_yellow"]
    top_of_green = zone_row["top_of_green"]
    net_flow = on_hand + open_supply
    if net_flow <= top_of_red:
        planning_zone = "red"
    elif net_flow <= top_of_yellow:
        planning_zone = "yellow"
    elif net_flow <= top_of_green:
        planning_zone = "green"
    else:
        planning_zone = "over"
    if on_hand <= 0:
        on_hand_status = "stocked_out"
    elif on_hand < 0.5 * top_of_red:
        on_hand_status = "critical"
    elif on_hand < top_of_red:
        on_hand_status = "low"
    else:
        on_hand_status = "ok"
    expected = dict(zone_row)
    expected.update(
        {
            "on_hand": on_hand,
            "open_supply": open_supply,
            "qualified_demand": 0,
            "net_flow": net_flow,
            "planning_zone": planning_zone,
            "order_signal": net_flow <= top_of_yellow,
            "suggested_order_units": (
                math.ceil(top_of_green - net_flow) if net_flow <= top_of_yellow else 0
            ),
            "planning_priority_pct": (
                round(100.0 * net_flow / top_of_green, 1) if top_of_green else None
            ),
            "on_hand_status": on_hand_status,
            "on_hand_pct_of_red": (
                round(100.0 * on_hand / top_of_red, 1) if top_of_red else None
            ),
            "notes": notes,
        }
    )
    return expected


def test_issue742_freeform(client, admin_token, user_token, monkeypatch):
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    # The recommendation inputs of the "ok" rows: the registry
    # defaults, never ambient environment values.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # The as-of date: the demand statistics' default as-of date is the
    # earlier of today and the latest demand event, so the latest
    # event seeded a day ago is the as-of date the routes answer as
    # of.  One snapshot instant for every on-hand row and open
    # purchase line (the latest sync).
    as_of = date.today() - timedelta(days=1)
    now = datetime.now(UTC)

    # Items A-F: 2.0 units a day for 360 days ending on the as-of
    # date (the way test 740 seeds its smooth item-sites): "smooth",
    # ADU 2.0.  Item E: 1.0 every third day in the 90 days ending on
    # the as-of date and in the 30 days ending 357 days before it:
    # 11 buckets, 3 non-zero -> "intermittent" (not recommendable, so
    # no order cycle), ADU 30/90 above 0, master lead time 0.
    daily_smooth = {as_of - timedelta(days=offset): 2.0 for offset in range(360)}
    daily_e = {
        as_of - timedelta(days=offset): 1.0
        for offset in list(range(0, 90, 3)) + list(range(330, 360, 3))
    }

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item, site in SMOOTH_ITEMS:
                _item_site(cur, item, site)
            _item_site(cur, ITEM_E, SITE_E, master=0.0)
            for item, site in SMOOTH_ITEMS:
                _demand_events(cur, item, site, daily_smooth)
            _demand_events(cur, ITEM_E, SITE_E, daily_e)
            for item, _site in SMOOTH_ITEMS:
                # Three 8/10/12-day observations, received 30 days
                # before the as-of date (the way test 740 seeds its
                # lead times): pooled mean 10 days.
                _lead_times(cur, item, as_of - timedelta(days=30))
            _on_hand(cur, ITEM_A, SITE_A, 10.0, now)
            _on_hand(cur, ITEM_B, SITE_B, 10.0, now)
            _on_hand(cur, ITEM_D, SITE_D, 5.0, now)
            _on_hand(cur, ITEM_E, SITE_E, 5.0, now)
            _on_hand(cur, ITEM_F, SITE_F, 30.0, now)
            # Item C deliberately has no on-hand row.
            # Item B: a future promise (30 days after the as-of) and a
            # promise exactly 90 days before it: both count (AC-3).
            _open_line(
                cur, ITEM_B, SITE_B, "b1", 100.0, as_of + timedelta(days=30), now
            )
            _open_line(cur, ITEM_B, SITE_B, "b2", 5.0, as_of - timedelta(days=90), now)
            # Item D: a promise 120 days before the as-of (stale) and
            # a zero-unit line on the same old date: the former is
            # excluded with the note, the latter is neither counted
            # nor stale.
            _open_line(
                cur, ITEM_D, SITE_D, "d1", 50.0, as_of - timedelta(days=120), now
            )
            _open_line(cur, ITEM_D, SITE_D, "d2", 0.0, as_of - timedelta(days=120), now)
        conn.commit()
    finally:
        conn.close()

    # Designate the six item-sites the way test 740 designates them.
    for item, site in DESIGNATED:
        resp = admin.put(
            BUFFERS_URL, json={"item_code": item, "site_code": site, "note": "buffer"}
        )
        assert resp.status_code == 200, resp.text
    assert _designations() == sorted(tuple(pair) for pair in DESIGNATED)

    # --- The zones route's own numbers (the shared row builder) -----
    resp = client.get(ZONES_URL)
    assert resp.status_code == 200, resp.text
    zones = resp.json()
    assert zones["as_of"] == as_of.isoformat()
    zone_by_key = {(row["item_code"], row["site_code"]): row for row in zones["rows"]}
    assert len(zone_by_key) == 6
    for item, site in SMOOTH_ITEMS:
        zone = zone_by_key[(item, site)]
        assert zone["status"] == "ok"
        assert zone["adu"] == 2.0
        assert zone["dlt_days"] == 10.0
        assert zone["dlt_source"] == "observed"
        assert zone["demand_class"] == "smooth"
        # The zones the plan's expected values compute from: the
        # defaults' red 20, yellow 20, green 541 (EOQ 541 units
        # covering 270.5 days) behind the whole-unit tops.
        assert (
            zone["top_of_red"],
            zone["top_of_yellow"],
            zone["top_of_green"],
        ) == (20, 40, 581)
    zone_e = zone_by_key[(ITEM_E, SITE_E)]
    assert zone_e["status"] == "ok"
    assert zone_e["demand_class"] == "intermittent"
    assert zone_e["dlt_days"] == 0.0
    assert zone_e["dlt_source"] == "master"
    assert (
        zone_e["top_of_red"],
        zone_e["top_of_yellow"],
        zone_e["top_of_green"],
    ) == (0, 0, 0)

    # No side effects: the zones response and the row counts before
    # the plan route runs.
    zones_before = client.get(ZONES_URL).json()
    counts_before = _row_counts()
    assert counts_before == {
        "on_hand": 5,
        "open_purchase_lines": 4,
        "ddmrp_buffers": 6,
    }

    # --- The plan ------------------------------------------------------
    resp = client.get(PLAN_URL)
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["as_of"] == as_of.isoformat()
    rows = plan["rows"]
    assert len(rows) == 6, "only the designated 'ok' rows are planned"
    plan_by_key = {(row["item_code"], row["site_code"]): row for row in rows}

    # Every row: the zone fields pass through unchanged, the net flow
    # is on hand plus open supply minus the qualified demand 0, and
    # the note "no open sales-order demand in the feed" is on every
    # row.
    for row in rows:
        zone = zone_by_key[(row["item_code"], row["site_code"])]
        for key, value in zone.items():
            assert row[key] == value, (row["item_code"], key)
        assert row["status"] == "ok"
        assert row["qualified_demand"] == 0
        assert (
            row["net_flow"]
            == row["on_hand"] + row["open_supply"] - row["qualified_demand"]
        )
        assert DEMAND_NOTE in row["notes"], row["item_code"]

    # Item A: the order signal.  On hand below the top of red with no
    # open supply gives "red", the order signal,
    # suggested_order_units = ceil(top_of_green - on_hand) and "low"
    # by the 50% rule (10 is exactly half the top of red 20, not
    # below it).
    row_a = plan_by_key[(ITEM_A, SITE_A)]
    assert row_a == _expected_plan_row(
        zone_by_key[(ITEM_A, SITE_A)], 10.0, 0.0, [DEMAND_NOTE]
    )
    assert row_a["net_flow"] < zone_by_key[(ITEM_A, SITE_A)]["top_of_red"]
    assert row_a["planning_zone"] == "red"
    assert row_a["order_signal"] is True
    assert row_a["suggested_order_units"] == math.ceil(581 - 10)
    assert row_a["on_hand_status"] == "low"
    assert row_a["on_hand_pct_of_red"] == 50.0
    assert row_a["notes"] == [DEMAND_NOTE]

    # Item D: "critical" by the 50% rule (5 is below half the top of
    # red 20) and the stale note.  The line promised 120 days before
    # the as-of is excluded from the open supply; the zero-unit line
    # is neither counted nor stale.
    row_d = plan_by_key[(ITEM_D, SITE_D)]
    assert row_d == _expected_plan_row(
        zone_by_key[(ITEM_D, SITE_D)],
        5.0,
        0.0,
        ["1 stale open purchase lines excluded", DEMAND_NOTE],
    )
    assert row_d["planning_zone"] == "red"
    assert row_d["order_signal"] is True
    assert row_d["suggested_order_units"] == math.ceil(581 - 5)
    assert row_d["open_supply"] == 0.0
    assert row_d["on_hand_status"] == "critical"
    assert row_d["on_hand_pct_of_red"] == 25.0
    assert row_d["notes"] == [
        "1 stale open purchase lines excluded",
        DEMAND_NOTE,
    ]

    # Item F: "yellow" (the net flow between the tops of red and
    # yellow), the order signal and "ok" on hand (at or above the
    # top of red).
    row_f = plan_by_key[(ITEM_F, SITE_F)]
    assert row_f == _expected_plan_row(
        zone_by_key[(ITEM_F, SITE_F)], 30.0, 0.0, [DEMAND_NOTE]
    )
    assert row_f["planning_zone"] == "yellow"
    assert row_f["order_signal"] is True
    assert row_f["suggested_order_units"] == math.ceil(581 - 30)
    assert row_f["on_hand_status"] == "ok"
    assert row_f["on_hand_pct_of_red"] == 150.0

    # Item B: no signal.  The future promise (30 days after the
    # as-of) and the promise exactly 90 days before it both count
    # (AC-3), lifting the net flow above the top of yellow.
    row_b = plan_by_key[(ITEM_B, SITE_B)]
    assert row_b == _expected_plan_row(
        zone_by_key[(ITEM_B, SITE_B)], 10.0, 105.0, [DEMAND_NOTE]
    )
    assert row_b["open_supply"] == 105.0, "future and 90-day-boundary lines count"
    assert row_b["net_flow"] > zone_by_key[(ITEM_B, SITE_B)]["top_of_yellow"]
    assert row_b["planning_zone"] == "green"
    assert row_b["order_signal"] is False
    assert row_b["suggested_order_units"] == 0
    assert row_b["on_hand_status"] == "low"
    assert row_b["notes"] == [DEMAND_NOTE]

    # Item C: no on-hand record.  The pair reads on hand 0 with the
    # note and "stocked_out".
    row_c = plan_by_key[(ITEM_C, SITE_C)]
    assert row_c == _expected_plan_row(
        zone_by_key[(ITEM_C, SITE_C)], 0.0, 0.0, [ON_HAND_NOTE, DEMAND_NOTE]
    )
    assert row_c["on_hand"] == 0
    assert ON_HAND_NOTE in row_c["notes"]
    assert row_c["planning_zone"] == "red"
    assert row_c["order_signal"] is True
    assert row_c["suggested_order_units"] == math.ceil(581 - 0)
    assert row_c["on_hand_status"] == "stocked_out"
    assert row_c["on_hand_pct_of_red"] == 0.0
    assert row_c["notes"] == [ON_HAND_NOTE, DEMAND_NOTE]

    # Item E: the top of green (and of red) is 0, so the planning
    # priority and the on-hand percentage are null and the net flow
    # above the top of green is "over" with no signal.
    row_e = plan_by_key[(ITEM_E, SITE_E)]
    assert row_e == _expected_plan_row(
        zone_by_key[(ITEM_E, SITE_E)], 5.0, 0.0, [DEMAND_NOTE]
    )
    assert row_e["planning_priority_pct"] is None
    assert row_e["on_hand_pct_of_red"] is None
    assert row_e["planning_zone"] == "over"
    assert row_e["order_signal"] is False
    assert row_e["suggested_order_units"] == 0
    assert row_e["on_hand_status"] == "ok"

    # Ordering: by planning_priority_pct ascending (nulls last), then
    # item_code and site_code.  C 0.0 < D 0.9 < A 1.7 < F 5.2 < B
    # 19.8, E null last.
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (ITEM_C, SITE_C),
        (ITEM_D, SITE_D),
        (ITEM_A, SITE_A),
        (ITEM_F, SITE_F),
        (ITEM_B, SITE_B),
        (ITEM_E, SITE_E),
    ]
    priorities = [row["planning_priority_pct"] for row in rows]
    assert priorities == sorted(
        priority for priority in priorities if priority is not None
    ) + [None]

    # The summary counts.
    assert plan["summary"] == {
        "signals": 4,
        "stocked_out": 1,
        "critical": 1,
        "low": 2,
    }

    # The route writes nothing: the zones response is unchanged and
    # the row counts are unchanged.
    assert (
        client.get(ZONES_URL).json() == zones_before
    ), "the zones response is unchanged"
    assert _row_counts() == counts_before

    # No token answers 401.
    anon = TestClient(app)
    assert anon.get(PLAN_URL).status_code == 401

    # --- Identity: a second pass after a full reload, as the
    #     non-admin (role "user"). --------------------------------------
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

    # The admin-only controls are absent for that caller: the
    # designation writes are refused...
    resp = viewer.put(
        BUFFERS_URL,
        json={"item_code": ITEM_A, "site_code": SITE_A, "note": "viewer"},
    )
    assert resp.status_code == 403, resp.text
    resp = viewer.delete(BUFFERS_URL, params={"item_code": ITEM_A, "site_code": SITE_A})
    assert resp.status_code == 403, resp.text
    assert _designations() == sorted(
        tuple(pair) for pair in DESIGNATED
    ), "the viewer wrote and removed nothing"

    # ...while the plan page still renders for it, and no token is
    # still a 401.
    resp = viewer.get(PLAN_URL)
    assert resp.status_code == 200, resp.text
    assert resp.json() == plan
    assert anon_reloaded.get(PLAN_URL).status_code == 401
