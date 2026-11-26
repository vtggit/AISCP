"""Issue #1631 proof: the DDMRP plan's spike on the day's total.

AC-1 — ``qualified_demand_for`` directly: with a top of red of 20,
``dlt_days`` 9.2 (a horizon of 10 days) and an as-of date D, the due
sum is the 3 units with a null ``ship_on`` plus the 4 units due on D
(7 exactly); the lines with ``ship_on`` after the as-of date and no
later than the horizon are summed per ``ship_on`` date and a day
counts its whole total only when the total is at least 50% of the
top of red, so the spike is 34 exactly — the 12 units of D+1 (the
-3 units of D+1 skipped before the sums), the 10 units of D+3
(exactly 50% of the top of red) and the 12 units of D+10 (the
horizon itself), the 9 units of D+2 and the 50 units on D+11 past
the horizon counting for nothing.  The 50% threshold keeps exact
precision for a large odd top of red of ``2**53 + 1``, where a day
totaling exactly ``2**52``, a half unit below the true half, counts
for nothing and the next representable total of ``2**52 + 1``
qualifies.

AC-2 — through ``GET /api/ddmrp/plan``, with a buffered item-site
seeded the way ``test_issue1548_freeform`` seeds one (the smooth
item-site behind the known zones, the whole-unit tops 20, 40, 581,
dlt_days 10): a day holding several open sales lines that are each
below 50% of the row's ``top_of_red`` but whose total reaches it
counts that total in ``qualified_spike``, the row's ``net_flow`` is
lower by it, and its note reads "qualified demand <q> units: <d>
due, <s> spike" with the new totals.  Every field is read strictly.
"""

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.ddmrp.plan import qualified_demand_for
from app.main import app
from app.repositories.open_sales_lines_postgres_repository import (
    OpenSalesLinePostgresRepository,
)

PLAN_URL = "/api/ddmrp/plan"
ZONES_URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"

ITEM, SITE = "ITEM-1631", "SITE-1631"  # the buffered item-site

SOURCE_1 = "erp-1631-1"  # one source system, one sync

SO_1 = "test:1631:so-1"  # 3.0, null ship_on: due
SO_2 = "test:1631:so-2"  # 4.0, due on the as-of date: due
SO_3 = "test:1631:so-3"  # 4.0, as-of + 1
SO_4 = "test:1631:so-4"  # 4.0, as-of + 1
SO_5 = "test:1631:so-5"  # 4.0, as-of + 1: the day's total of 12
SO_6 = "test:1631:so-6"  # 4.0, as-of + 2
SO_7 = "test:1631:so-7"  # 5.0, as-of + 2: the day's total of 9
SO_KEYS = (SO_1, SO_2, SO_3, SO_4, SO_5, SO_6, SO_7)

QUALIFIED_NOTE = "qualified demand 19 units: 7 due, 12 spike"

SO_REPO = OpenSalesLinePostgresRepository()

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


def _item_site(cur, item, site):
    """The item-site's current planning settings (the way test 1548
    seeds its item-sites)."""
    _seed(
        cur,
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, master_lead_time_days, "
        "source_system) VALUES (%s, %s, %s, 1.0, 25.0, 10.0, %s, 'test')",
        (f"id-is-1631-{item}", item, site, None),
    )


def _demand_events(cur, item, site, daily):
    """One demand event per day of ``daily`` for one item (the way
    test 1548 seeds its smooth item-sites)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-1631-{item}-{day.isoformat()}",
                f"de-1631-{item}-{day.isoformat()}",
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
    10 (the way test 1548 seeds its lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        _seed(
            cur,
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-1631-{item}-{i}",
                f"lt-1631-{item}-{i}",
                f"SUP-1631-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def _on_hand(cur, item, site, quantity, as_of):
    _seed(cur, ON_HAND_SQL, (f"id-oh-1631-{item}", item, site, quantity, as_of))


def _so_line(key, item, site, open_units, ship_on, as_of, source_system):
    """One ``open_sales_lines`` row shaped for the repository's
    ``upsert_many``."""
    return {
        "business_key": key,
        "item_code": item,
        "site_code": site,
        "order_type": "SO",
        "open_units": open_units,
        "ship_on": ship_on,
        "as_of": as_of,
        "source_system": source_system,
    }


def test_issue1631_freeform(client, admin_token, monkeypatch):
    # --- AC-1: qualified_demand_for directly ------------------------
    # A top of red of 20 (50%: 10), dlt_days 9.2: the horizon is the
    # as-of date plus 9.2 rounded up to whole days, 10 days.
    D = date(2026, 5, 11)
    d1 = D + timedelta(days=1)
    d2 = D + timedelta(days=2)
    d3 = D + timedelta(days=3)
    d10 = D + timedelta(days=10)
    d11 = D + timedelta(days=11)
    lines = [
        {"open_units": 3.0, "ship_on": None},  # due (null ship_on)
        {"open_units": 4.0, "ship_on": D},  # due (on the as-of date)
        {"open_units": 4.0, "ship_on": d1},
        {"open_units": 4.0, "ship_on": d1},
        {"open_units": 4.0, "ship_on": d1},  # D+1: 12 total: a spike
        {"open_units": -3.0, "ship_on": d1},  # skipped before the sums
        {"open_units": 4.0, "ship_on": d2},
        {"open_units": 5.0, "ship_on": d2},  # D+2: 9 total: nothing
        {"open_units": 10.0, "ship_on": d3},  # D+3: exactly 50%: a spike
        {"open_units": 6.0, "ship_on": d10},
        {"open_units": 6.0, "ship_on": d10},  # D+10: the horizon: a spike
        {"open_units": 50.0, "ship_on": d11},  # past the horizon: nothing
    ]
    due, spike = qualified_demand_for(lines, D, 9.2, 20)
    assert due == 7.0
    assert spike == 34.0

    # The 50% threshold keeps exact precision for a large odd top of
    # red: 0.5 * (2**53 + 1) rounds down to 2**52, a half unit below
    # the true half, so a day totaling exactly 2**52 counts for
    # nothing while the next representable total qualifies.
    big_top = 2**53 + 1
    big_lines = [
        {"open_units": float(2**52), "ship_on": d1},  # 0.5 below the half: nothing
        {"open_units": float(2**52 + 1), "ship_on": d2},  # above the half: a spike
    ]
    due, spike = qualified_demand_for(big_lines, D, 9.2, big_top)
    assert due == 0.0
    assert spike == float(2**52 + 1)

    # --- AC-2: through GET /api/ddmrp/plan --------------------------
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    # The recommendation inputs of the "ok" rows: the registry
    # defaults, never ambient environment values (the way test 1548
    # pins them).
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # The as-of date: the demand statistics' default as-of date is the
    # earlier of today and the latest demand event, so the latest
    # event seeded a day ago is the as-of date the routes answer as
    # of.  One snapshot instant for the on-hand row and the newest
    # sync.
    as_of = date.today() - timedelta(days=1)
    now = datetime.now(UTC)

    conn = get_connection()
    try:
        # --- The item-site: buffered, smooth -------------------------
        with conn.cursor() as cur:
            _item_site(cur, ITEM, SITE)
            # 2.0 units a day for 360 days ending on the as-of date
            # (the way test 1548 seeds its smooth item-site):
            # "smooth", ADU 2.0, the pooled observed lead time 10
            # days and the known zones behind the whole-unit tops
            # (20, 40, 581).
            daily_smooth = {
                as_of - timedelta(days=offset): 2.0 for offset in range(360)
            }
            _demand_events(cur, ITEM, SITE, daily_smooth)
            # Three 8/10/12-day observations, received 30 days before
            # the as-of date (the way test 1548 seeds its lead times):
            # pooled mean 10 days.
            _lead_times(cur, ITEM, as_of - timedelta(days=30))
            _on_hand(cur, ITEM, SITE, 10.0, now)
        conn.commit()

        # Designate the item-site the way test 1548 designates its
        # item-sites.
        resp = admin.put(
            BUFFERS_URL,
            json={"item_code": ITEM, "site_code": SITE, "note": "buffer"},
        )
        assert resp.status_code == 200, resp.text

        # The open sales lines, one sync:
        #   3.0 null ship_on                  -> due
        #   4.0 due on the as-of date         -> due
        #   4.0 + 4.0 + 4.0 on as-of + 1      -> each line below 50%
        #                                        of the top of red 20,
        #                                        the day's total of 12
        #                                        reaches it: a spike
        #   4.0 + 5.0 on as-of + 2            -> day total of 9:
        #                                        nothing
        result = SO_REPO.upsert_many(
            [
                _so_line(SO_1, ITEM, SITE, 3.0, None, now, SOURCE_1),
                _so_line(SO_2, ITEM, SITE, 4.0, as_of, now, SOURCE_1),
                _so_line(
                    SO_3,
                    ITEM,
                    SITE,
                    4.0,
                    as_of + timedelta(days=1),
                    now,
                    SOURCE_1,
                ),
                _so_line(
                    SO_4,
                    ITEM,
                    SITE,
                    4.0,
                    as_of + timedelta(days=1),
                    now,
                    SOURCE_1,
                ),
                _so_line(
                    SO_5,
                    ITEM,
                    SITE,
                    4.0,
                    as_of + timedelta(days=1),
                    now,
                    SOURCE_1,
                ),
                _so_line(
                    SO_6, ITEM, SITE, 4.0, as_of + timedelta(days=2), now, SOURCE_1
                ),
                _so_line(
                    SO_7, ITEM, SITE, 5.0, as_of + timedelta(days=2), now, SOURCE_1
                ),
            ]
        )
        assert result == {"inserted": 7, "updated": 0}

        # --- The zones route's own numbers: the known zones ---------
        resp = client.get(ZONES_URL)
        assert resp.status_code == 200, resp.text
        zones = resp.json()
        assert zones["as_of"] == as_of.isoformat()
        assert [(r["item_code"], r["site_code"]) for r in zones["rows"]] == [
            (ITEM, SITE)
        ]
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

        # --- The plan: the day's total qualifies the spike ----------
        resp = client.get(PLAN_URL)
        assert resp.status_code == 200, resp.text
        plan = resp.json()
        assert plan["as_of"] == as_of.isoformat()
        rows = plan["rows"]
        assert [(r["item_code"], r["site_code"]) for r in rows] == [(ITEM, SITE)]
        row = rows[0]
        # Read every field strictly: no .get with a default.
        assert row["status"] == "ok"
        assert row["top_of_red"] == 20
        assert row["top_of_yellow"] == 40
        assert row["top_of_green"] == 581
        assert row["on_hand"] == 10.0
        assert row["open_supply"] == 0.0
        assert row["qualified_due"] == 7.0
        # The three 4-unit lines of as-of + 1 are each below 50% of
        # the top of red, but the day's total of 12 reaches it, so
        # the whole total counts; the 9-unit day of as-of + 2 does
        # not qualify, so its lines count for nothing.
        assert row["qualified_spike"] == 12.0
        assert row["qualified_demand"] == 19.0
        # The net flow is the on hand plus the open supply minus the
        # qualified demand: lower by the spike's 12 units than the
        # qualified due alone would leave.
        assert row["net_flow"] == -9.0
        assert row["net_flow"] == (
            row["on_hand"] + row["open_supply"] - row["qualified_demand"]
        )
        assert row["planning_zone"] == "red"
        assert row["order_signal"] is True
        assert row["suggested_order_units"] == 590
        assert row["planning_priority_pct"] == -1.5
        assert row["on_hand_status"] == "low"
        assert row["on_hand_pct_of_red"] == 50.0
        # The note carries the new totals: 7 due, 12 spike, 19 all.
        assert row["notes"] == [QUALIFIED_NOTE]
        assert plan["summary"] == {
            "signals": 1,
            "stocked_out": 0,
            "critical": 0,
            "low": 1,
        }
    finally:
        # Every row this test inserted, deleted.
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM open_sales_lines WHERE business_key = ANY(%s)",
                    (list(SO_KEYS),),
                )
                cur.execute(
                    "DELETE FROM ddmrp_buffers "
                    "WHERE (item_code, site_code) IN ((%s, %s))",
                    (ITEM, SITE),
                )
                cur.execute(
                    "DELETE FROM on_hand WHERE id = ANY(%s)",
                    ([f"id-oh-1631-{ITEM}"],),
                )
                cur.execute(
                    "DELETE FROM lead_time_observations "
                    "WHERE business_key LIKE 'lt-1631-%'"
                )
                cur.execute(
                    "DELETE FROM demand_events WHERE business_key LIKE 'de-1631-%'"
                )
                cur.execute(
                    "DELETE FROM item_sites WHERE id = ANY(%s)",
                    ([f"id-is-1631-{ITEM}"],),
                )
            conn.commit()
        finally:
            conn.close()
