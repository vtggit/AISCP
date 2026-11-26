"""Issue #1630 proof: the in-flight supply in the DDMRP plan's net flow.

AC-3 — the releases: the buffers, demand, lead times, on hand and
open purchase lines are seeded the way
``tests/test_issue742_freeform.py`` seeds them (two buffered
item-sites of one item, the smooth 2.0-a-day profile, the
8/10/12-day observations, the designation through
``PUT /api/ddmrp/buffers``), and the plan-release rows are inserted
the way ``tests/test_issue860_freeform.py`` inserts them (raw
INSERTs into ``plan_releases``).  The buffered row's plan row
signals with no releases (net flow at or below the top of yellow);
a "created" plan-release row for the pair — its order number absent
from the feed, its month the as-of month, enough units to lift the
net flow above the top of yellow — raises the row's ``open_supply``
and ``net_flow`` by exactly those units, turns ``order_signal``
false, zeroes ``suggested_order_units`` and gains the note "<r>
units released, not yet in the feed"; the same row with outcome
"exists" counts the same; with outcome "error", or with a null
order number, it adds nothing; and once the newest open-purchase
snapshot carries a line whose business key holds the order number,
the pair counts only the feed's units, never both.  The proof reads
every field strictly (``row["open_supply"]``, never ``.get`` with a
default).

AC-4 — the transfers, and the two paths agree: a
``transfer_releases`` row is inserted the way
``tests/test_issue914_freeform.py`` inserts them (outcome "created",
a reference number, ``erp_status`` null); it moves units of the
buffered item from the buffered site to a second buffered site —
the buffered row's ``open_supply`` and ``net_flow`` fall by exactly
those units with the "take" note, the second site's row rises by
the same units with the "add" note — and the same row with
``erp_status`` "Released" counts nothing.  For the seeded pair, the
plan's in-flight part (its ``open_supply`` minus its open purchase
units) equals what ``GET /api/recommendations`` adds to the pair's
``on_order_units`` beyond the same open purchase units.  Each
reorder helper runs once per plan call, the release helper with the
plan's own as-of date over the reorder module's own full open-
purchase read, and with no "ok" zone row neither helper is called.
"""

import json
import math
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.ddmrp.plan import plan_ddmrp
from app.main import app
from app.recommend import reorder as reorder_module

PLAN_URL = "/api/ddmrp/plan"
ZONES_URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"
RECOMMENDATIONS_URL = "/api/recommendations"

SITE_1630 = "SITE-1630-1"  # the buffered sending site: 10 on hand
SITE_1630_2 = "SITE-1630-2"  # the buffered receiving site: 0 on hand
ITEM = "ITEM-1630A"
ORDER_NBR = "PO-1630-1"
SUPPLIER = "SUP-1630"
SOURCE_SYSTEM = "test"
RELEASE_ORIGIN = "AIERP order plan 1630 site SITE-1630-1 vendor SUP-1630"
TRANSFER_ORIGIN = "AIERP transfer 1630 site SITE-1630-1 to SITE-1630-2"
DEMAND_NOTE = "no open sales-order demand in the feed"

# 40.5 released, 30.5 in flight: the notes round halves up (41 and
# 31), the way the qualified-demand note rounds.
RELEASE_LINE = {"item_code": ITEM, "quantity": 40.5, "unit_cost": 1.0, "value": 40.5}
TRANSFER_LINE = {"item_code": ITEM, "quantity": 30.5}
RELEASE_NOTE = "41 units released, not yet in the feed"
TAKE_NOTE = "in-flight transfers take 31 units"
ADD_NOTE = "in-flight transfers add 31 units"
# The two-paths-agree phase, in whole units: 40 released, 30 in
# flight, 25 open purchase.
RELEASE_LINE_WHOLE = {
    "item_code": ITEM,
    "quantity": 40,
    "unit_cost": 1.0,
    "value": 40.0,
}
TRANSFER_LINE_WHOLE = {"item_code": ITEM, "quantity": 30}
RELEASE_NOTE_WHOLE = "40 units released, not yet in the feed"
TAKE_NOTE_WHOLE = "in-flight transfers take 30 units"
ADD_NOTE_WHOLE = "in-flight transfers add 30 units"

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
LEAD_SQL = (
    "INSERT INTO lead_time_observations (id, business_key, supplier_code, "
    "item_code, ordered_on, received_on, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
ITEM_SITE_SQL = (
    "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
    "reorder_point, safety_stock, master_lead_time_days, "
    "source_system) VALUES (%s, %s, %s, 1.0, 25.0, 10.0, %s, 'test')"
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
RELEASE_SQL = (
    "INSERT INTO plan_releases (month, site_code, supplier_code, origin, "
    "order_nbr, outcome, line_count, value, lines, actor) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
TRANSFER_SQL = (
    "INSERT INTO transfer_releases (origin, from_site_code, to_site_code, "
    "reference_nbr, outcome, line_count, lines, actor, erp_status, "
    "updated_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)"
)


def _item_site(cur, site) -> None:
    """The item-site's current planning settings (the way test 742
    seeds its item-sites)."""
    cur.execute(ITEM_SITE_SQL, (f"id-is-1630-{site}", ITEM, site, None))


def _demand_events(cur, site, daily) -> None:
    """One demand event per day of ``daily`` for one site (the way
    test 742 seeds its demand events)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-1630-{site}-{day.isoformat()}",
                f"de-1630-{site}-{day.isoformat()}",
                ITEM,
                site,
                quantity,
                day.isoformat(),
                "shipment",
                SOURCE_SYSTEM,
            )
            for day, quantity in sorted(daily.items(), reverse=True)
        ],
    )


def _lead_times(cur, site, received) -> None:
    """Three lead-time observations of 8, 10 and 12 days: pooled mean
    10, sample std 2, not low confidence (the way test 742 seeds its
    lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        cur.execute(
            LEAD_SQL,
            (
                f"id-lt-1630-{site}-{i}",
                f"lt-1630-{site}-{i}",
                f"{SUPPLIER}-{site}",
                ITEM,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
                SOURCE_SYSTEM,
            ),
        )


def _on_hand(cur, site, quantity, as_of) -> None:
    cur.execute(ON_HAND_SQL, (f"id-oh-1630-{site}", ITEM, site, quantity, as_of))


def _open_line(cur, site, business_key, open_units, promised_on, as_of) -> None:
    """One open purchase line of the pair (the way test 742 seeds its
    open lines); ``business_key`` the line's feed key — the order
    number the feed carries is its second ``:``-separated part, the
    way the open-purchase feed keys its lines."""
    cur.execute(
        OPEN_LINE_SQL,
        (
            f"id-opl-1630-{site}",
            business_key,
            ITEM,
            site,
            f"{SUPPLIER}-{site}",
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


def _insert_release(cur, order_nbr, outcome, lines, month) -> None:
    """One plan_releases row of the item at SITE_1630, the way test
    860 inserts its plan-release rows."""
    cur.execute(
        RELEASE_SQL,
        (
            month,
            SITE_1630,
            SUPPLIER,
            RELEASE_ORIGIN,
            order_nbr,
            outcome,
            len(lines),
            sum(line["quantity"] * line["unit_cost"] for line in lines),
            json.dumps(lines),
            "test",
        ),
    )


def _insert_transfer(cur, reference_nbr, erp_status, updated_at, line) -> None:
    """One transfer_releases row of the item from SITE_1630 to
    SITE_1630_2, the way test 914 inserts its transfer rows."""
    cur.execute(
        TRANSFER_SQL,
        (
            TRANSFER_ORIGIN,
            SITE_1630,
            SITE_1630_2,
            reference_nbr,
            "created",
            1,
            json.dumps([line]),
            "test",
            erp_status,
            updated_at,
        ),
    )


def _expected_plan_row(zone_row: dict, on_hand: float, open_supply: float, notes: list):
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


def test_issue1630_freeform(client, admin_token, monkeypatch) -> None:
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})

    # The as-of date: the demand statistics' default as-of date is
    # the earlier of today and the latest demand event, so the latest
    # event seeded a day ago is the date the routes answer as of.
    as_of = date.today() - timedelta(days=1)
    month = as_of.strftime("%Y-%m")
    now = datetime.now(UTC)
    daily = {as_of - timedelta(days=offset): 2.0 for offset in range(360)}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for site in (SITE_1630, SITE_1630_2):
                _item_site(cur, site)
                _demand_events(cur, site, daily)
                _lead_times(cur, site, as_of - timedelta(days=30))
            _on_hand(cur, SITE_1630, 10.0, now)
            _on_hand(cur, SITE_1630_2, 0.0, now)
        conn.commit()
    finally:
        conn.close()

    for site in (SITE_1630, SITE_1630_2):
        resp = admin.put(
            BUFFERS_URL, json={"item_code": ITEM, "site_code": site, "note": "buffer"}
        )
        assert resp.status_code == 200, resp.text

    # The reorder module's own in-flight helpers, spied (delegating
    # to the real ones): the release helper's calls record their
    # as-of date, so the proof sees exactly what the plan passed.
    units_as_of: list[date] = []
    transfer_calls: list[bool] = []
    real_units = reorder_module._in_flight_units_by_key
    real_transfer = reorder_module._in_flight_transfer_units_by_key

    def _units_spy(open_lines, as_of_date):
        units_as_of.append(as_of_date)
        return real_units(open_lines, as_of_date)

    def _transfer_spy():
        transfer_calls.append(True)
        return real_transfer()

    monkeypatch.setattr(reorder_module, "_in_flight_units_by_key", _units_spy)
    monkeypatch.setattr(
        reorder_module, "_in_flight_transfer_units_by_key", _transfer_spy
    )

    def _plan() -> dict:
        resp = client.get(PLAN_URL)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def _rows(plan_body: dict) -> dict:
        return {(row["item_code"], row["site_code"]): row for row in plan_body["rows"]}

    # The zones' own numbers: both sites "ok", the default profile's
    # whole-unit tops (the way test 742 asserts them).
    zones = client.get(ZONES_URL).json()
    assert zones["as_of"] == as_of.isoformat()
    zone_by_key = {(row["item_code"], row["site_code"]): row for row in zones["rows"]}
    for site in (SITE_1630, SITE_1630_2):
        zone = zone_by_key[(ITEM, site)]
        assert zone["status"] == "ok"
        assert zone["adu"] == 2.0
        assert zone["dlt_days"] == 10.0
        assert (
            zone["top_of_red"],
            zone["top_of_yellow"],
            zone["top_of_green"],
        ) == (20, 40, 581)
    zone_rows = zones["rows"]

    # The plan's own call: each reorder helper runs exactly once,
    # the release helper with the plan's own as-of date.
    def _plan_direct(rows) -> dict:
        units_before = len(units_as_of)
        transfer_before = len(transfer_calls)
        body = plan_ddmrp(as_of, rows)
        assert (
            len(units_as_of) == units_before + 1
        ), "the release helper runs once per call"
        assert (
            len(transfer_calls) == transfer_before + 1
        ), "the transfer helper runs once per call"
        assert units_as_of[-1] == as_of, "the plan's own as-of date"
        return body

    # --- The baseline: both rows signal, no in-flight supply ------------
    plan = _plan()
    assert plan["as_of"] == as_of.isoformat()
    rows = _rows(plan)
    assert len(plan["rows"]) == 2
    # The ordering: the zero on-hand row (priority 0.0) first.
    assert [(row["item_code"], row["site_code"]) for row in plan["rows"]] == [
        (ITEM, SITE_1630_2),
        (ITEM, SITE_1630),
    ]
    row = rows[(ITEM, SITE_1630)]
    assert row == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630)], 10.0, 0.0, [DEMAND_NOTE]
    )
    # The buffered row signals with no releases: the net flow at or
    # below the top of yellow.
    assert row["net_flow"] <= row["top_of_yellow"]
    assert row["order_signal"] is True
    assert row["suggested_order_units"] == math.ceil(581 - 10)
    base_open_supply = row["open_supply"]
    base_net_flow = row["net_flow"]
    assert base_open_supply == 0.0
    assert base_net_flow == 10.0
    row2 = rows[(ITEM, SITE_1630_2)]
    assert row2 == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630_2)], 0.0, 0.0, [DEMAND_NOTE]
    )
    assert plan["summary"] == {
        "signals": 2,
        "stocked_out": 1,
        "critical": 0,
        "low": 1,
    }
    _plan_direct(zone_rows)

    # --- AC-3: the "created" release lifts the row above the top of
    #     yellow -----------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM open_purchase_lines "
                "WHERE business_key LIKE %s",
                (f"%{ORDER_NBR}%",),
            )
            assert cur.fetchone()[0] == 0, "the order number is absent from the feed"
            _insert_release(cur, ORDER_NBR, "created", [RELEASE_LINE], month)
        conn.commit()
    finally:
        conn.close()
    assert month == as_of.isoformat()[:7], "the release is in the as-of month"

    plan = _plan()
    rows = _rows(plan)
    row = rows[(ITEM, SITE_1630)]
    assert row == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630)], 10.0, 40.5, [RELEASE_NOTE, DEMAND_NOTE]
    )
    assert row["open_supply"] == base_open_supply + 40.5
    assert row["net_flow"] == base_net_flow + 40.5
    assert row["net_flow"] > row["top_of_yellow"]
    assert row["order_signal"] is False
    assert row["suggested_order_units"] == 0
    assert RELEASE_NOTE in row["notes"]
    # The other row is untouched.
    assert rows[(ITEM, SITE_1630_2)] == row2
    assert plan["summary"] == {
        "signals": 1,
        "stocked_out": 1,
        "critical": 0,
        "low": 1,
    }

    # --- outcome "exists" counts the same --------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_releases")
            _insert_release(cur, ORDER_NBR, "exists", [RELEASE_LINE], month)
        conn.commit()
    finally:
        conn.close()

    plan = _plan()
    row = _rows(plan)[(ITEM, SITE_1630)]
    assert row["open_supply"] == 40.5
    assert row["net_flow"] == 50.5
    assert row["order_signal"] is False
    assert row["suggested_order_units"] == 0
    assert row["notes"] == [RELEASE_NOTE, DEMAND_NOTE]

    # --- "error", and a null order number, add nothing --------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_releases")
            _insert_release(cur, "PO-1630-2", "error", [RELEASE_LINE], month)
            _insert_release(cur, None, "created", [RELEASE_LINE], month)
        conn.commit()
    finally:
        conn.close()

    plan = _plan()
    row = _rows(plan)[(ITEM, SITE_1630)]
    assert row == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630)], 10.0, 0.0, [DEMAND_NOTE]
    )
    assert row["open_supply"] == base_open_supply
    assert row["net_flow"] == base_net_flow
    assert row["order_signal"] is True
    assert row["suggested_order_units"] == math.ceil(581 - 10)

    # --- never twice: the newest snapshot carries the order number --------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_releases")
            _insert_release(cur, ORDER_NBR, "created", [RELEASE_LINE], month)
            _open_line(cur, SITE_1630, "po:PO-1630-1:1", 50.0, None, now)
        conn.commit()
    finally:
        conn.close()

    plan = _plan()
    row = _rows(plan)[(ITEM, SITE_1630)]
    assert row == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630)], 10.0, 50.0, [DEMAND_NOTE]
    )
    assert row["open_supply"] == 50.0, "the feed's units only, never both"
    assert row["net_flow"] == 60.0
    assert row["order_signal"] is False
    assert row["suggested_order_units"] == 0
    assert not any("released" in note for note in row["notes"])

    # --- AC-4: the in-flight transfer, take and add ------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_releases")
            cur.execute("DELETE FROM open_purchase_lines")
            _insert_transfer(cur, "TR-1630-1", None, now, TRANSFER_LINE)
        conn.commit()
    finally:
        conn.close()

    plan = _plan()
    rows = _rows(plan)
    row = rows[(ITEM, SITE_1630)]
    assert row == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630)], 10.0, -30.5, [TAKE_NOTE, DEMAND_NOTE]
    )
    assert row["open_supply"] == base_open_supply - 30.5
    assert row["net_flow"] == base_net_flow - 30.5
    assert row["order_signal"] is True
    assert row["suggested_order_units"] == math.ceil(581 + 20.5)
    assert TAKE_NOTE in row["notes"]
    # The second site is buffered too: its row rises by the same
    # units, with the "add" note.
    row2 = rows[(ITEM, SITE_1630_2)]
    assert row2 == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630_2)], 0.0, 30.5, [ADD_NOTE, DEMAND_NOTE]
    )
    assert row2["open_supply"] == 30.5
    assert row2["net_flow"] == 30.5
    assert row2["order_signal"] is True
    assert row2["suggested_order_units"] == math.ceil(581 - 30.5)
    assert ADD_NOTE in row2["notes"]
    # The ordering: the negative-priority row first.
    assert [(r["item_code"], r["site_code"]) for r in plan["rows"]] == [
        (ITEM, SITE_1630),
        (ITEM, SITE_1630_2),
    ]

    # --- the same row "Released" counts nothing ----------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transfer_releases")
            _insert_transfer(cur, "TR-1630-1", "Released", now, TRANSFER_LINE)
        conn.commit()
    finally:
        conn.close()

    plan = _plan()
    rows = _rows(plan)
    assert rows[(ITEM, SITE_1630)] == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630)], 10.0, 0.0, [DEMAND_NOTE]
    )
    assert rows[(ITEM, SITE_1630_2)] == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630_2)], 0.0, 0.0, [DEMAND_NOTE]
    )

    # --- the two paths agree -------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transfer_releases")
            _insert_release(cur, ORDER_NBR, "created", [RELEASE_LINE_WHOLE], month)
            _insert_transfer(cur, "TR-1630-2", None, now, TRANSFER_LINE_WHOLE)
            _open_line(cur, SITE_1630, "po:PO-1630-9:1", 25.0, None, now)
        conn.commit()
    finally:
        conn.close()

    plan = _plan()
    rows = _rows(plan)
    row = rows[(ITEM, SITE_1630)]
    assert row == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630)],
        10.0,
        35.0,
        [RELEASE_NOTE_WHOLE, TAKE_NOTE_WHOLE, DEMAND_NOTE],
    )
    row2 = rows[(ITEM, SITE_1630_2)]
    assert row2 == _expected_plan_row(
        zone_by_key[(ITEM, SITE_1630_2)], 0.0, 30.0, [ADD_NOTE_WHOLE, DEMAND_NOTE]
    )
    # The plan's in-flight part (open_supply minus the pair's open
    # purchase units) equals what the recommendations add to the
    # pair's on_order_units beyond the same open purchase units.
    resp = client.get(RECOMMENDATIONS_URL, params={"as_of": as_of.isoformat()})
    assert resp.status_code == 200, resp.text
    rec_by_key = {(rec["item_code"], rec["site_code"]): rec for rec in resp.json()}
    rec = rec_by_key[(ITEM, SITE_1630)]
    rec2 = rec_by_key[(ITEM, SITE_1630_2)]
    open_purchase_units = 25.0  # the pair's one open line, counted by both paths
    assert (
        row["open_supply"] - open_purchase_units
        == rec["on_order_units"] - open_purchase_units
    )
    assert row2["open_supply"] - 0.0 == rec2["on_order_units"] - 0
    assert row["open_supply"] - open_purchase_units == 10.0
    assert rec["on_order_units"] == 35
    assert rec2["on_order_units"] == 30
    _plan_direct(zone_rows)

    # --- with no "ok" zone row, neither reorder helper is called ------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM ddmrp_buffers")
        conn.commit()
    finally:
        conn.close()

    # At the route level the plan answers empty...
    plan = _plan()
    assert plan["as_of"] == as_of.isoformat()
    assert plan["rows"] == []
    assert plan["summary"] == {
        "signals": 0,
        "stocked_out": 0,
        "critical": 0,
        "low": 0,
    }

    # ...and the plan's own call reaches neither helper.
    def _no_units(open_lines, as_of_date):
        raise AssertionError("the release helper must not run without an 'ok' zone row")

    def _no_transfer():
        raise AssertionError(
            "the transfer helper must not run without an 'ok' zone row"
        )

    monkeypatch.setattr(reorder_module, "_in_flight_units_by_key", _no_units)
    monkeypatch.setattr(
        reorder_module, "_in_flight_transfer_units_by_key", _no_transfer
    )

    units_before = len(units_as_of)
    transfer_before = len(transfer_calls)
    assert plan_ddmrp(as_of, []) == {
        "as_of": as_of,
        "rows": [],
        "summary": {"signals": 0, "stocked_out": 0, "critical": 0, "low": 0},
    }
    assert len(units_as_of) == units_before, "neither reorder helper is called"
    assert len(transfer_calls) == transfer_before, "neither reorder helper is called"

    # Every as-of date the release helper saw in the test is the
    # plan's own as-of date (the recommendations' calls resolve the
    # same default).
    assert units_as_of and all(a == as_of for a in units_as_of)
