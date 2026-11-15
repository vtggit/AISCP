"""Issue #759 proof: the qualified demand of the DDMRP plan.

AC-1 — against the live app and the migrated test database, one
designated item-site seeded the way test 742 seeds its item-sites
(with the known zones), and open sales-order lines stored through
``OpenSalesLinePostgresRepository.upsert_many``: ``GET
/api/ddmrp/plan`` answers the plan row with the qualified demand of
the pair's ``open_sales_lines`` rows (read once per call from
``list_latest`` and grouped by item-site), limited to the lines with
``open_units`` above 0: ``qualified_due`` the lines with ``ship_on``
at or before the as-of date (a null ``ship_on`` counts as due on the
as-of date), ``qualified_spike`` the lines with ``ship_on`` after the
as-of date and no later than the as-of date plus the row's
``dlt_days`` rounded up to whole days, counting only the lines whose
own ``open_units`` is at least 50% of the row's top of red.  The
``qualified_demand`` is the sum of the two and the ``net_flow`` is
the on hand plus the open supply minus it, with the planning zone,
the order signal and the suggested order units following from it.
The row carries the note "qualified demand <q> units: <d> due, <s>
spike" (whole units) instead of the no-demand note.

AC-2 — the due lines are a line due 5 days ago and a line with a
null ``ship_on``; the spikes are a line due inside the horizon above
50% of the top of red (a smaller one in the horizon does not count
and a large one beyond the horizon does not); a line with
``open_units`` 0 is ignored.  With the ``open_sales_lines`` table
emptied, the plan is identical to #742's output, note included: the
qualified demand is 0, the net flow is the on hand plus the open
supply, the note is "no open sales-order demand in the feed" and the
two qualified-demand part fields stay out of the response.
"""

import math
from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from app.repositories.open_sales_lines_postgres_repository import (
    OpenSalesLinePostgresRepository,
)

PLAN_URL = "/api/ddmrp/plan"
ZONES_URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"

ITEM_A = "ITEM-759A"  # on hand 10, no supply, qualified demand 62
SITE_A = "SITE-759-1"

DEMAND_NOTE = "no open sales-order demand in the feed"
QUALIFIED_NOTE = "qualified demand 62 units: 50 due, 12 spike"

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
ON_HAND_SQL = (
    "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
    "as_of, source_system) VALUES (%s, %s, %s, %s, %s, 'test')"
)


def _seed(cur, sql, params):
    cur.execute(sql, params)


def _item_site(cur, item, site, master=None):
    """The item-site's current planning settings (the way test 740
    and test 742 seed their item-sites), with the master lead time
    when given."""
    _seed(
        cur,
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, master_lead_time_days, "
        "source_system) VALUES (%s, %s, %s, 1.0, 25.0, 10.0, %s, 'test')",
        (f"id-is-759-{item}", item, site, master),
    )


def _demand_events(cur, item, site, daily):
    """One demand event per day of ``daily`` for one item (the way
    test 742 seeds its smooth item-sites)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-759-{item}-{day.isoformat()}",
                f"de-759-{item}-{day.isoformat()}",
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
    10, sample std 2, not low confidence (the way test 742 seeds its
    lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        _seed(
            cur,
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-759-{item}-{i}",
                f"lt-759-{item}-{i}",
                f"SUP-759-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def _on_hand(cur, item, site, quantity, as_of):
    _seed(
        cur,
        ON_HAND_SQL,
        (f"id-oh-759-{item}", item, site, quantity, as_of),
    )


def _sales_line(key, item, site, open_units, ship_on, as_of):
    """One ``open_sales_lines`` row shaped for the repository's
    ``upsert_many``."""
    return {
        "business_key": f"test:759:{key}",
        "item_code": item,
        "site_code": site,
        "order_type": "SO",
        "open_units": open_units,
        "ship_on": ship_on,
        "as_of": as_of,
        "source_system": "test",
    }


def _expected_plan_row(
    zone_row: dict,
    on_hand: float,
    open_supply: float,
    notes: list[str],
    qualified_due: float | None = None,
    qualified_spike: float | None = None,
) -> dict:
    """The expected plan row of one item-site, computed from the
    zones route's own numbers (its zone fields pass through
    unchanged).  With the qualified-demand parts given, the row
    carries them and the qualified demand is their sum; without
    them, the qualified demand is 0 and the parts stay out of the
    response (the #742 shape)."""
    top_of_red = zone_row["top_of_red"]
    top_of_yellow = zone_row["top_of_yellow"]
    top_of_green = zone_row["top_of_green"]
    qualified_demand = 0.0 if qualified_due is None else qualified_due + qualified_spike
    net_flow = on_hand + open_supply - qualified_demand
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
            "qualified_demand": qualified_demand,
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
    if qualified_due is not None:
        expected["qualified_due"] = qualified_due
        expected["qualified_spike"] = qualified_spike
    return expected


def test_issue759_freeform(client, admin_token, monkeypatch):
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    # The recommendation inputs of the "ok" rows: the registry
    # defaults, never ambient environment values (the way test 742
    # pins them).
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # The as-of date: the demand statistics' default as-of date is the
    # earlier of today and the latest demand event, so the latest
    # event seeded a day ago is the as-of date the routes answer as
    # of.  One snapshot instant for the on-hand row and the open
    # sales lines.
    as_of = date.today() - timedelta(days=1)
    now = datetime.now(UTC)

    # Item A: 2.0 units a day for 360 days ending on the as-of date
    # (the way test 742 seeds its smooth item-sites): "smooth", ADU
    # 2.0, the pooled observed lead time 10 days and the known zones
    # red 20, yellow 40, green 541 behind the whole-unit tops
    # (20, 40, 581).
    daily_smooth = {as_of - timedelta(days=offset): 2.0 for offset in range(360)}

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _item_site(cur, ITEM_A, SITE_A)
            _demand_events(cur, ITEM_A, SITE_A, daily_smooth)
            # Three 8/10/12-day observations, received 30 days before
            # the as-of date (the way test 742 seeds its lead times):
            # pooled mean 10 days.
            _lead_times(cur, ITEM_A, as_of - timedelta(days=30))
            _on_hand(cur, ITEM_A, SITE_A, 10.0, now)
        conn.commit()
    finally:
        conn.close()

    # Designate the item-site the way test 742 designates its
    # item-sites.
    resp = admin.put(
        BUFFERS_URL, json={"item_code": ITEM_A, "site_code": SITE_A, "note": "buffer"}
    )
    assert resp.status_code == 200, resp.text

    # The zones route's own numbers: the known zones.
    resp = client.get(ZONES_URL)
    assert resp.status_code == 200, resp.text
    zones = resp.json()
    assert zones["as_of"] == as_of.isoformat()
    assert len(zones["rows"]) == 1
    zone_row = zones["rows"][0]
    assert zone_row["status"] == "ok"
    assert zone_row["adu"] == 2.0
    assert zone_row["dlt_days"] == 10.0
    assert zone_row["dlt_source"] == "observed"
    assert zone_row["demand_class"] == "smooth"
    assert (
        zone_row["top_of_red"],
        zone_row["top_of_yellow"],
        zone_row["top_of_green"],
    ) == (20, 40, 581)

    # The open sales-order lines of the pair, one snapshot, stored
    # through the repository:
    #   s1 due 5 days ago, 30 units        -> due
    #   s2 null ship_on, 20 units          -> due (due on the as-of)
    #   s3 in the horizon, 12 units (>= 10, 50% of the top of red 20)
    #                                        -> spike
    #   s4 in the horizon, 4 units (< 10)  -> not a spike
    #   s5 beyond the horizon, 100 units   -> not a spike
    #   s6 due 1 day ago, 0 units          -> ignored
    # The horizon is the as-of date plus dlt_days 10 rounded up to
    # whole days.
    sales = [
        _sales_line("s1", ITEM_A, SITE_A, 30.0, as_of - timedelta(days=5), now),
        _sales_line("s2", ITEM_A, SITE_A, 20.0, None, now),
        _sales_line("s3", ITEM_A, SITE_A, 12.0, as_of + timedelta(days=7), now),
        _sales_line("s4", ITEM_A, SITE_A, 4.0, as_of + timedelta(days=3), now),
        _sales_line("s5", ITEM_A, SITE_A, 100.0, as_of + timedelta(days=30), now),
        _sales_line("s6", ITEM_A, SITE_A, 0.0, as_of - timedelta(days=1), now),
    ]
    result = OpenSalesLinePostgresRepository().upsert_many(sales)
    assert result == {"inserted": 6, "updated": 0}

    # --- The plan with qualified demand ------------------------------
    resp = client.get(PLAN_URL)
    assert resp.status_code == 200, resp.text
    plan = resp.json()
    assert plan["as_of"] == as_of.isoformat()
    rows = plan["rows"]
    assert len(rows) == 1
    row = rows[0]

    # The zone fields pass through unchanged.
    for key, value in zone_row.items():
        assert row[key] == value, key

    # Due lines: the line due 5 days ago and the line with a null
    # ship_on both count; the zero-unit line is ignored.
    assert row["qualified_due"] == 50.0
    # Spikes: only the line due inside the horizon above 50% of the
    # top of red counts; the smaller one in the horizon and the large
    # one beyond the horizon do not.
    assert row["qualified_spike"] == 12.0
    # The arithmetic: the qualified demand is due plus spike, and the
    # net flow is the on hand plus the open supply minus it.
    assert row["qualified_demand"] == 62.0
    assert row["qualified_demand"] == row["qualified_due"] + row["qualified_spike"]
    assert row["on_hand"] == 10.0
    assert row["open_supply"] == 0.0
    assert (
        row["net_flow"] == row["on_hand"] + row["open_supply"] - row["qualified_demand"]
    )
    assert row["net_flow"] == -52.0
    # The zone, the signal and the suggestion follow from the net
    # flow: red at or below the top of red, the signal at or below
    # the top of yellow, the suggestion the ceiling of the top of
    # green minus the net flow.
    assert row["planning_zone"] == "red"
    assert row["order_signal"] is True
    assert row["suggested_order_units"] == 633
    assert row["on_hand_status"] == "low"
    assert row["on_hand_pct_of_red"] == 50.0
    # The note: the qualified-demand note instead of the no-demand
    # note.
    assert QUALIFIED_NOTE in row["notes"]
    assert DEMAND_NOTE not in row["notes"]
    # The whole row against the zones route's own numbers.
    assert row == _expected_plan_row(zone_row, 10.0, 0.0, [QUALIFIED_NOTE], 50.0, 12.0)
    assert plan["summary"] == {
        "signals": 1,
        "stocked_out": 0,
        "critical": 0,
        "low": 1,
    }

    # --- The empty table: identical to #742's output ------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM open_sales_lines")
        conn.commit()
    finally:
        conn.close()

    resp = client.get(PLAN_URL)
    assert resp.status_code == 200, resp.text
    plan_empty = resp.json()
    assert plan_empty["as_of"] == as_of.isoformat()
    assert len(plan_empty["rows"]) == 1
    row_empty = plan_empty["rows"][0]
    # The qualified demand is 0, the net flow is the on hand plus the
    # open supply, and the note is the no-demand note (the #742
    # shape, note included).
    assert row_empty["qualified_demand"] == 0
    assert row_empty["net_flow"] == row_empty["on_hand"] + row_empty["open_supply"]
    assert row_empty["net_flow"] == 10.0
    assert row_empty["notes"] == [DEMAND_NOTE]
    # The two qualified-demand part fields stay out of the response.
    assert "qualified_due" not in row_empty
    assert "qualified_spike" not in row_empty
    assert row_empty == _expected_plan_row(zone_row, 10.0, 0.0, [DEMAND_NOTE])
    assert plan_empty["summary"] == {
        "signals": 1,
        "stocked_out": 0,
        "critical": 0,
        "low": 1,
    }
