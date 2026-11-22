"""Issue #1562 proof: one connection per plan build, and the
buffered pairs' demand statistics.

AC-1 — one connection per plan build: with ``psycopg2.connect``
counted and the ``supply_plan.plan_buffers_by_signal`` setting at its
default (on — no stored value), one ``build_supply_plan(7)`` opens
exactly one connection — the plan's own reads and the buffer read's
ride the one ``one_connection`` block the builder runs its work in —
and a build inside an outer ``one_connection`` block opens no other
connection: the two plans are equal.

AC-2 — the buffered item-sites' demand statistics: item-sites A and
B are designated as buffers and C is not, each with the same demand
the way ``tests/test_issue1548_freeform.py`` seeds its A, B and C.
``demand_stats_for_all(as_of, pairs=((A), (B)))`` answers exactly the
rows ``demand_stats_for_all(as_of)`` answers for A and B, in the same
order; ``pairs=()`` answers ``[]`` and opens no connection, counted
through ``psycopg2.connect``.  ``GET /api/ddmrp/zones`` gets the same
answer whether ``app.api.ddmrp.demand_stats_for_all`` is the real
function or a spy that ignores ``pairs`` and answers every pair's
statistics — the spy records one call, with ``pairs`` equal to A and
B, sorted — and ``GET /api/ddmrp/candidates`` answers C among its
candidates, as today (the candidates' context still passes none).

AC-4 — the demand statistics' cache key carries the normalized pair
set: inside one ``read_once`` block the all-pair and the
pair-restricted reads keep separate entries (neither serves the
other's result), and the same set named in the other order serves the
restricted entry — no extra read — while the all-pair entry stays its
own.
"""

from datetime import UTC, date, datetime, timedelta

import psycopg2
from fastapi.testclient import TestClient

import app.api.ddmrp as ddmrp_module
from app.api.supply_plan import build_supply_plan
from app.db.connection import get_connection, one_connection
from app.db.read_cache import read_once
from app.main import app
from app.stats.demand import demand_stats_for_all

ZONES_URL = "/api/ddmrp/zones"
CANDIDATES_URL = "/api/ddmrp/candidates"
BUFFERS_URL = "/api/ddmrp/buffers"

ITEM_A, SITE_A = "ITEM-1562A", "SITE-1562A"  # designated as a buffer
ITEM_B, SITE_B = "ITEM-1562B", "SITE-1562B"  # designated as a buffer
ITEM_C, SITE_C = "ITEM-1562C", "SITE-1562C"  # an item-site that is not designated

PAIR_A = (ITEM_A, SITE_A)
PAIR_B = (ITEM_B, SITE_B)
PAIR_C = (ITEM_C, SITE_C)
DESIGNATED = (PAIR_A, PAIR_B)

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
    """The item-site's current planning settings (the way
    ``tests/test_issue1548_freeform.py`` seeds its item-sites)."""
    _seed(
        cur,
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, master_lead_time_days, "
        "source_system) VALUES (%s, %s, %s, 1.0, 25.0, 10.0, %s, 'test')",
        (f"id-is-1562-{item}", item, site, None),
    )


def _demand_events(cur, item, site, daily):
    """One demand event per day of ``daily`` for one item (the way
    ``tests/test_issue1548_freeform.py`` seeds its smooth
    item-sites)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-1562-{item}-{day.isoformat()}",
                f"de-1562-{item}-{day.isoformat()}",
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
    10 (the way ``tests/test_issue1548_freeform.py`` seeds its
    lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        _seed(
            cur,
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-1562-{item}-{i}",
                f"lt-1562-{item}-{i}",
                f"SUP-1562-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def _on_hand(cur, item, site, quantity, as_of):
    _seed(
        cur,
        ON_HAND_SQL,
        (f"id-oh-1562-{item}", item, site, quantity, as_of),
    )


def test_issue1562_freeform(client, admin_token, monkeypatch):
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    # The recommendation inputs of the "ok" rows: the registry
    # defaults, never ambient environment values (the way
    # test_issue1548_freeform.py pins them).
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # The as-of date: the demand statistics' default as-of date is the
    # earlier of today and the latest demand event, so the latest
    # event seeded a day ago is the as-of date the routes answer as
    # of.
    as_of = date.today() - timedelta(days=1)
    now = datetime.now(UTC)

    # Count every psycopg2.connect of the test.
    connects = [0]
    real_connect = psycopg2.connect

    def counting_connect(*args, **kwargs):
        connects[0] += 1
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(psycopg2, "connect", counting_connect)

    conn = get_connection()
    try:
        # --- The item-sites: A and B designated, C not -------------
        with conn.cursor() as cur:
            _item_site(cur, ITEM_A, SITE_A)
            _item_site(cur, ITEM_B, SITE_B)
            _item_site(cur, ITEM_C, SITE_C)
            # A, B and C: 2.0 units a day for 360 days ending on the
            # as-of date (the way test_issue1548_freeform.py seeds its
            # smooth item-sites): "smooth", ADU 2.0 and the pooled
            # observed lead time 10 days.
            daily_smooth = {
                as_of - timedelta(days=offset): 2.0 for offset in range(360)
            }
            for item, site in ((ITEM_A, SITE_A), (ITEM_B, SITE_B), (ITEM_C, SITE_C)):
                _demand_events(cur, item, site, daily_smooth)
                _lead_times(cur, item, as_of - timedelta(days=30))
                _on_hand(cur, item, site, 10.0, now)
        conn.commit()

        # Designate A and B the way test_issue1548_freeform.py
        # designates its item-sites; C stays an item-site without a
        # designation.
        for item, site in ((ITEM_A, SITE_A), (ITEM_B, SITE_B)):
            resp = admin.put(
                BUFFERS_URL,
                json={"item_code": item, "site_code": site, "note": "buffer"},
            )
            assert resp.status_code == 200, resp.text

        # --- The statistics: the restricted read --------------------
        # Every pair's rows, then the designated pairs' rows: the
        # restricted read answers exactly the rows the all-pair read
        # answers for A and B, in the same order — a pair's
        # statistics are the same either way.
        full = demand_stats_for_all(as_of)
        assert [(row["item_code"], row["site_code"]) for row in full] == [
            PAIR_A,
            PAIR_B,
            PAIR_C,
        ]
        full_by_pair = {(row["item_code"], row["site_code"]): row for row in full}
        for row in full:
            assert row["demand_class"] == "smooth"
        expected = [full_by_pair[PAIR_A], full_by_pair[PAIR_B]]
        restricted = demand_stats_for_all(as_of, pairs=DESIGNATED)
        assert restricted == expected
        assert PAIR_C not in {
            (row["item_code"], row["site_code"]) for row in restricted
        }

        # The empty pair set: no row, no query, no connection.
        connects[0] = 0
        assert demand_stats_for_all(as_of, pairs=()) == []
        assert connects[0] == 0

        # --- The cache key: the normalized pair set ------------------
        # Inside one read_once block the all-pair and the
        # pair-restricted reads keep separate entries — neither
        # serves the other's result — and the same set named in the
        # other order serves the restricted entry without a new read.
        with read_once():
            connects[0] = 0
            cached_all = demand_stats_for_all(as_of)
            assert cached_all == full
            assert connects[0] == 1
            cached_ab = demand_stats_for_all(as_of, pairs=DESIGNATED)
            assert cached_ab == expected
            assert connects[0] == 2, "the pair set must be in the cache key"
            cached_ba = demand_stats_for_all(as_of, pairs=(PAIR_B, PAIR_A))
            assert cached_ba == cached_ab
            assert connects[0] == 2, "the pair set must be normalized in the key"
            cached_all_again = demand_stats_for_all(as_of)
            assert cached_all_again == cached_all
            assert (
                connects[0] == 2
            ), "the all-pair entry must not serve a restricted read"

        # --- One connection per plan build ----------------------------
        # The supply_plan.plan_buffers_by_signal setting at its
        # default (on): no stored value.  One build opens exactly one
        # connection — the plan's own reads and the buffer read's —
        # and a build inside an outer one_connection() block opens no
        # other connection; the two plans are equal.
        connects[0] = 0
        plan_standalone = build_supply_plan(7)
        assert connects[0] == 1
        connects[0] = 0
        with one_connection():
            plan_nested = build_supply_plan(7)
        assert connects[0] == 1, "the build must open no connection but the block's"
        assert plan_standalone == plan_nested
        # The plan is not trivial: C is due from its reorder point.
        planned = {
            (line["item_code"], line["site_code"])
            for group in plan_standalone["suppliers"]
            for line in group["lines"]
        }
        assert PAIR_C in planned

        # --- The zone rows: the designated pairs only ------------------
        resp = client.get(ZONES_URL)
        assert resp.status_code == 200, resp.text
        zones_real = resp.json()
        assert [(row["item_code"], row["site_code"]) for row in zones_real["rows"]] == [
            PAIR_A,
            PAIR_B,
        ]
        for row in zones_real["rows"]:
            assert row["status"] == "ok"

        # The spy ignores pairs and answers every pair's statistics:
        # the zones route still gets the same answer, and the spy
        # records one call, with pairs equal to A and B, sorted.
        calls: list[dict] = []
        real_stats = ddmrp_module.demand_stats_for_all

        def spy_stats(*args, **kwargs):
            calls.append({"args": args, "kwargs": dict(kwargs)})
            kwargs.pop("pairs", None)
            return real_stats(*args, **kwargs)

        monkeypatch.setattr(ddmrp_module, "demand_stats_for_all", spy_stats)
        resp = client.get(ZONES_URL)
        assert resp.status_code == 200, resp.text
        assert resp.json() == zones_real
        assert len(calls) == 1
        assert calls[0]["kwargs"]["pairs"] == DESIGNATED
        assert calls[0]["args"] == (date.fromisoformat(zones_real["as_of"]),)

        # The candidates' context still passes none: C is a candidate
        # among them, as today — and not A or B, which are
        # designated.
        resp = client.get(CANDIDATES_URL)
        assert resp.status_code == 200, resp.text
        candidates = resp.json()
        candidate_pairs = [
            (row["item_code"], row["site_code"]) for row in candidates["rows"]
        ]
        assert PAIR_C in candidate_pairs
        assert PAIR_A not in candidate_pairs
        assert PAIR_B not in candidate_pairs
        row_c = next(row for row in candidates["rows"] if row["item_code"] == ITEM_C)
        assert row_c["abc_class"] == "A"
        assert row_c["demand_class"] == "smooth"
        assert row_c["adu"] == 2.0
        assert row_c["dlt_days"] == 10.0
        assert row_c["dlt_source"] == "observed"
        assert row_c["reason"] == "A class, 10-day lead time, smooth demand"
        assert len(calls) == 2
        assert calls[1]["kwargs"]["pairs"] is None
    finally:
        # Every row this test inserted, deleted.
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM ddmrp_buffers WHERE (item_code, site_code) IN %s",
                (DESIGNATED,),
            )
            cur.execute(
                "DELETE FROM on_hand WHERE id = ANY(%s)",
                ([f"id-oh-1562-{item}" for item in (ITEM_A, ITEM_B, ITEM_C)],),
            )
            cur.execute(
                "DELETE FROM lead_time_observations "
                "WHERE business_key LIKE 'lt-1562-%'"
            )
            cur.execute("DELETE FROM demand_events WHERE business_key LIKE 'de-1562-%'")
            cur.execute(
                "DELETE FROM item_sites WHERE id = ANY(%s)",
                ([f"id-is-1562-{item}" for item in (ITEM_A, ITEM_B, ITEM_C)],),
            )
        conn.commit()
        conn.close()
