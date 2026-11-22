"""Issue #1548 proof: the DDMRP plan's pairs-filtered feed reads.

AC-1 — the reads, against the migrated test database, with open
purchase lines and open sales lines stored through the repositories'
``upsert_many`` the way test 759 stores its open sales lines: each
repository's ``list_latest`` answers exactly as before with no
``pairs`` — the newest sync of each source system, chosen over every
row of the table, every pair's newest rows, C's included — and with
``pairs`` set to the sorted distinct ``(item_code, site_code)`` of the
"ok" zone rows, only those pairs' rows, in the same order and shape,
the pair filter in the SQL: B's rows are all in the older sync of its
source system, so it gets none, and C's rows are never fetched.  An
empty tuple answers ``[]`` without a query.  The sales repository
answers whether the feed holds any row at all with ``has_lines``, in
one ``SELECT EXISTS`` query.

AC-2 — the plan: ``plan_ddmrp`` (through ``GET /api/ddmrp/plan``)
reads the two feeds with ``pairs`` set to the sorted distinct
``(item_code, site_code)`` of the "ok" zone rows (spied) and answers
every field of every row, and the summary, exactly as before: for A
the open supply of 78.0 (one stale line excluded), the qualified
demand 62.0 — 50.0 due, 12.0 spike — and the notes, all from its
newest-sync lines only; for B the open supply 0.0 and the qualified
demand 0.0, its older-sync-only lines counting for nothing.  Whether
the feed carries any open sales-order lines at all is answered by
``has_lines``, never by the filtered read: with open sales lines only
for C in the feed, A's and B's rows carry the qualified-demand note
with 0 units, not "no open sales-order demand in the feed", and with
no open sales lines at all they carry that note with the two part
fields out of the row.  With no "ok" zone row, neither ``list_latest``
read and not ``has_lines()`` is called (spied).
"""

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

import app.ddmrp.plan as plan_module
import app.repositories.open_purchase_lines_postgres_repository as po_repo_module
import app.repositories.open_sales_lines_postgres_repository as so_repo_module
from app.db.connection import get_connection
from app.main import app
from app.repositories.open_purchase_lines_postgres_repository import (
    OpenPurchaseLinePostgresRepository,
)
from app.repositories.open_sales_lines_postgres_repository import (
    OpenSalesLinePostgresRepository,
)

PLAN_URL = "/api/ddmrp/plan"
ZONES_URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"

ITEM_A, SITE_A = "ITEM-1548A", "SITE-1548A"  # buffered, newest-sync lines
ITEM_B, SITE_B = "ITEM-1548B", "SITE-1548B"  # buffered, older-sync lines only
ITEM_C, SITE_C = "ITEM-1548C", "SITE-1548C"  # an item-site that is not buffered

SOURCE_1 = "erp-1548-1"  # two syncs of one source system: older and newest
SOURCE_2 = "erp-1548-2"  # a second source system: its newest sync, A only

# The sorted distinct (item_code, site_code) of the "ok" zone rows.
EXPECTED_PAIRS = ((ITEM_A, SITE_A), (ITEM_B, SITE_B))

DEMAND_NOTE = "no open sales-order demand in the feed"
A_QUALIFIED_NOTE = "qualified demand 62 units: 50 due, 12 spike"
ZERO_QUALIFIED_NOTE = "qualified demand 0 units: 0 due, 0 spike"
A_STALE_NOTE = "1 stale open purchase lines excluded"

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
ON_HAND_SQL = (
    "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
    "as_of, source_system) VALUES (%s, %s, %s, %s, %s, 'test')"
)

# The open-line business keys, one per stored row.
PO_A1_OLD = "test:1548:po-a1-old"
PO_B1_OLD = "test:1548:po-b1-old"
PO_C1_OLD = "test:1548:po-c1-old"
PO_A2 = "test:1548:po-a2"
PO_A3 = "test:1548:po-a3"
PO_A4 = "test:1548:po-a4"
PO_A5 = "test:1548:po-a5"
PO_C2 = "test:1548:po-c2"
PO_A6 = "test:1548:po-a6"
PO_KEYS = (
    PO_A1_OLD,
    PO_B1_OLD,
    PO_C1_OLD,
    PO_A2,
    PO_A3,
    PO_A4,
    PO_A5,
    PO_C2,
    PO_A6,
)

SO_A1_OLD = "test:1548:so-a1-old"
SO_B1_OLD = "test:1548:so-b1-old"
SO_C1_OLD = "test:1548:so-c1-old"
SO_A2 = "test:1548:so-a2"
SO_A3 = "test:1548:so-a3"
SO_A4 = "test:1548:so-a4"
SO_A5 = "test:1548:so-a5"
SO_A6 = "test:1548:so-a6"
SO_A7 = "test:1548:so-a7"
SO_C3 = "test:1548:so-c3"
SO_A8 = "test:1548:so-a8"
SO_KEYS = (
    SO_A1_OLD,
    SO_B1_OLD,
    SO_C1_OLD,
    SO_A2,
    SO_A3,
    SO_A4,
    SO_A5,
    SO_A6,
    SO_A7,
    SO_C3,
    SO_A8,
)

PO_REPO = OpenPurchaseLinePostgresRepository()
SO_REPO = OpenSalesLinePostgresRepository()


def _seed(cur, sql, params):
    cur.execute(sql, params)


def _item_site(cur, item, site):
    """The item-site's current planning settings (the way test 759
    seeds its item-sites)."""
    _seed(
        cur,
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, master_lead_time_days, "
        "source_system) VALUES (%s, %s, %s, 1.0, 25.0, 10.0, %s, 'test')",
        (f"id-is-1548-{item}", item, site, None),
    )


def _demand_events(cur, item, site, daily):
    """One demand event per day of ``daily`` for one item (the way
    test 759 seeds its smooth item-sites)."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-1548-{item}-{day.isoformat()}",
                f"de-1548-{item}-{day.isoformat()}",
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
    10, sample std 2, not low confidence (the way test 759 seeds its
    lead-time observations)."""
    for i, lead in enumerate((8, 10, 12)):
        _seed(
            cur,
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')",
            (
                f"id-lt-1548-{item}-{i}",
                f"lt-1548-{item}-{i}",
                f"SUP-1548-{item}",
                item,
                (received - timedelta(days=lead)).isoformat(),
                received.isoformat(),
            ),
        )


def _on_hand(cur, item, site, quantity, as_of):
    _seed(
        cur,
        ON_HAND_SQL,
        (f"id-oh-1548-{item}", item, site, quantity, as_of),
    )


def _po_line(key, item, site, open_units, promised_on, as_of, source_system):
    """One ``open_purchase_lines`` row shaped for the repository's
    ``upsert_many``."""
    return {
        "business_key": key,
        "item_code": item,
        "site_code": site,
        "supplier_code": f"SUP-1548-{item[-1]}",
        "order_type": "PO",
        "open_units": open_units,
        "promised_on": promised_on,
        "ordered_on": None,
        "as_of": as_of,
        "source_system": source_system,
    }


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


def _keys(rows):
    return [row["business_key"] for row in rows]


def _delete_keys(cur, table, keys):
    cur.execute(f"DELETE FROM {table} WHERE business_key = ANY(%s)", (list(keys),))


def test_issue1548_freeform(client, admin_token, monkeypatch):
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    # The recommendation inputs of the "ok" rows: the registry
    # defaults, never ambient environment values (the way test 759
    # pins them).
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # The as-of date: the demand statistics' default as-of date is the
    # earlier of today and the latest demand event, so the latest
    # event seeded a day ago is the as-of date the routes answer as
    # of.  One snapshot instant for the on-hand rows and the newest
    # syncs, two days earlier for the older sync.
    as_of = date.today() - timedelta(days=1)
    now = datetime.now(UTC)
    as_of_old = now - timedelta(days=2)

    # Spy on the plan's own feed reads: the plan's repository
    # instances record every call and pass through to the original
    # method, so the plan's answers are the real ones while the test
    # sees exactly what the plan read.
    po_calls: list[tuple[tuple, dict]] = []
    so_calls: list[tuple[tuple, dict]] = []
    has_lines_calls: list[tuple[tuple, dict]] = []
    orig_po_list = plan_module._open_po_repo.list_latest
    orig_so_list = plan_module._open_so_repo.list_latest
    orig_has_lines = plan_module._open_so_repo.has_lines

    def _po_spy(*args, **kwargs):
        po_calls.append((args, kwargs))
        return orig_po_list(*args, **kwargs)

    def _so_spy(*args, **kwargs):
        so_calls.append((args, kwargs))
        return orig_so_list(*args, **kwargs)

    def _has_lines_spy(*args, **kwargs):
        has_lines_calls.append((args, kwargs))
        return orig_has_lines(*args, **kwargs)

    monkeypatch.setattr(plan_module._open_po_repo, "list_latest", _po_spy)
    monkeypatch.setattr(plan_module._open_so_repo, "list_latest", _so_spy)
    monkeypatch.setattr(plan_module._open_so_repo, "has_lines", _has_lines_spy)

    conn = get_connection()
    try:
        # --- The item-sites: A and B buffered, C not ---------------
        with conn.cursor() as cur:
            _item_site(cur, ITEM_A, SITE_A)
            _item_site(cur, ITEM_B, SITE_B)
            _item_site(cur, ITEM_C, SITE_C)
            # A and B: 2.0 units a day for 360 days ending on the
            # as-of date (the way test 759 seeds its smooth
            # item-sites): "smooth", ADU 2.0, the pooled observed
            # lead time 10 days and the known zones red 20, yellow
            # 40, green 541 behind the whole-unit tops (20, 40, 581).
            daily_smooth = {
                as_of - timedelta(days=offset): 2.0 for offset in range(360)
            }
            _demand_events(cur, ITEM_A, SITE_A, daily_smooth)
            _demand_events(cur, ITEM_B, SITE_B, daily_smooth)
            # Three 8/10/12-day observations, received 30 days before
            # the as-of date (the way test 759 seeds its lead times):
            # pooled mean 10 days.
            _lead_times(cur, ITEM_A, as_of - timedelta(days=30))
            _lead_times(cur, ITEM_B, as_of - timedelta(days=30))
            _on_hand(cur, ITEM_A, SITE_A, 10.0, now)
            _on_hand(cur, ITEM_B, SITE_B, 10.0, now)
        conn.commit()

        # Designate A and B the way test 759 designates its
        # item-sites; C stays an item-site without a designation.
        for item, site in ((ITEM_A, SITE_A), (ITEM_B, SITE_B)):
            resp = admin.put(
                BUFFERS_URL,
                json={"item_code": item, "site_code": site, "note": "buffer"},
            )
            assert resp.status_code == 200, resp.text

        # --- The syncs ----------------------------------------------
        # Source system 1, the older as_of: A, B and C all have open
        # purchase lines and open sales lines.  B's lines are only
        # ever in this older sync.
        #   A po 100.0 / so 999.0: excluded, the older sync is not
        #   A's or C's newest.
        #   B po 500.0 / so 42.0: excluded, B's only sync is the
        #   older one.
        #   C po 10.0 / so 5.0: excluded for the same reason.
        result = PO_REPO.upsert_many(
            [
                _po_line(PO_A1_OLD, ITEM_A, SITE_A, 100.0, as_of, as_of_old, SOURCE_1),
                _po_line(PO_B1_OLD, ITEM_B, SITE_B, 500.0, as_of, as_of_old, SOURCE_1),
                _po_line(PO_C1_OLD, ITEM_C, SITE_C, 10.0, as_of, as_of_old, SOURCE_1),
            ]
        )
        assert result == {"inserted": 3, "updated": 0}
        result = SO_REPO.upsert_many(
            [
                _so_line(
                    SO_A1_OLD,
                    ITEM_A,
                    SITE_A,
                    999.0,
                    as_of - timedelta(days=5),
                    as_of_old,
                    SOURCE_1,
                ),
                _so_line(
                    SO_B1_OLD,
                    ITEM_B,
                    SITE_B,
                    42.0,
                    as_of - timedelta(days=3),
                    as_of_old,
                    SOURCE_1,
                ),
                _so_line(
                    SO_C1_OLD,
                    ITEM_C,
                    SITE_C,
                    5.0,
                    as_of - timedelta(days=2),
                    as_of_old,
                    SOURCE_1,
                ),
            ]
        )
        assert result == {"inserted": 3, "updated": 0}
        # Source system 1, the newest as_of: A and C have lines, B
        # does not.  For A:
        #   po 40.0 promised 10 days ago        -> open supply
        #   po 25.5 no promise                  -> open supply
        #   po 7.0 promised 120 days ago        -> stale, excluded
        #   po 0.0 promised 120 days ago        -> neither (0 units)
        #   so 30.0 due 5 days ago              -> due
        #   so 20.0 null ship_on                -> due (due on the as-of)
        #   so 12.0 due inside the horizon, >= 10 (50% of the top of
        #   red 20)                             -> spike
        #   so 4.0 due inside the horizon, < 10 -> not a spike
        #   so 100.0 due beyond the horizon     -> not a spike
        #   so 0.0 due 1 day ago                -> ignored (0 units)
        result = PO_REPO.upsert_many(
            [
                _po_line(
                    PO_A2,
                    ITEM_A,
                    SITE_A,
                    40.0,
                    as_of - timedelta(days=10),
                    now,
                    SOURCE_1,
                ),
                _po_line(PO_A3, ITEM_A, SITE_A, 25.5, None, now, SOURCE_1),
                _po_line(
                    PO_A4,
                    ITEM_A,
                    SITE_A,
                    7.0,
                    as_of - timedelta(days=120),
                    now,
                    SOURCE_1,
                ),
                _po_line(
                    PO_A5,
                    ITEM_A,
                    SITE_A,
                    0.0,
                    as_of - timedelta(days=120),
                    now,
                    SOURCE_1,
                ),
                _po_line(PO_C2, ITEM_C, SITE_C, 30.0, as_of, now, SOURCE_1),
            ]
        )
        assert result == {"inserted": 5, "updated": 0}
        result = SO_REPO.upsert_many(
            [
                _so_line(
                    SO_A2,
                    ITEM_A,
                    SITE_A,
                    30.0,
                    as_of - timedelta(days=5),
                    now,
                    SOURCE_1,
                ),
                _so_line(SO_A3, ITEM_A, SITE_A, 20.0, None, now, SOURCE_1),
                _so_line(
                    SO_A4,
                    ITEM_A,
                    SITE_A,
                    12.0,
                    as_of + timedelta(days=7),
                    now,
                    SOURCE_1,
                ),
                _so_line(
                    SO_A5, ITEM_A, SITE_A, 4.0, as_of + timedelta(days=3), now, SOURCE_1
                ),
                _so_line(
                    SO_A6,
                    ITEM_A,
                    SITE_A,
                    100.0,
                    as_of + timedelta(days=30),
                    now,
                    SOURCE_1,
                ),
                _so_line(
                    SO_A7, ITEM_A, SITE_A, 0.0, as_of - timedelta(days=1), now, SOURCE_1
                ),
                _so_line(
                    SO_C3,
                    ITEM_C,
                    SITE_C,
                    50.0,
                    as_of - timedelta(days=1),
                    now,
                    SOURCE_1,
                ),
            ]
        )
        assert result == {"inserted": 7, "updated": 0}
        # Source system 2, its newest sync: lines for A only.
        #   po 12.5 promised 5 days from the as-of date -> open
        #   supply (future promises count).
        #   so 5.0 due inside the horizon, < 10          -> neither.
        result = PO_REPO.upsert_many(
            [
                _po_line(
                    PO_A6,
                    ITEM_A,
                    SITE_A,
                    12.5,
                    as_of + timedelta(days=5),
                    now,
                    SOURCE_2,
                ),
            ]
        )
        assert result == {"inserted": 1, "updated": 0}
        result = SO_REPO.upsert_many(
            [
                _so_line(
                    SO_A8, ITEM_A, SITE_A, 5.0, as_of + timedelta(days=2), now, SOURCE_2
                ),
            ]
        )
        assert result == {"inserted": 1, "updated": 0}

        # --- The zones route's own numbers: the known zones ---------
        resp = client.get(ZONES_URL)
        assert resp.status_code == 200, resp.text
        zones = resp.json()
        assert zones["as_of"] == as_of.isoformat()
        # C is not designated: its zone row is not among the rows.
        assert [(r["item_code"], r["site_code"]) for r in zones["rows"]] == [
            (ITEM_A, SITE_A),
            (ITEM_B, SITE_B),
        ]
        for zone_row in zones["rows"]:
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

        # --- The golden answers --------------------------------------
        resp = client.get(PLAN_URL)
        assert resp.status_code == 200, resp.text
        plan = resp.json()
        assert plan["as_of"] == as_of.isoformat()
        rows = plan["rows"]
        # Ordered by planning_priority_pct ascending: B (1.7) before
        # A (4.5).
        assert [(r["item_code"], r["site_code"]) for r in rows] == [
            (ITEM_B, SITE_B),
            (ITEM_A, SITE_A),
        ]
        row_b, row_a = rows

        # B: its lines are all in the older sync of its source
        # system, so they count for nothing: the open supply is 0 and
        # the qualified demand is 0, the note the 0-units
        # qualified-demand note (the feed carries lines).
        assert row_b["status"] == "ok"
        assert row_b["top_of_red"] == 20
        assert row_b["top_of_yellow"] == 40
        assert row_b["top_of_green"] == 581
        assert row_b["on_hand"] == 10.0
        assert row_b["open_supply"] == 0.0
        assert row_b["qualified_due"] == 0.0
        assert row_b["qualified_spike"] == 0.0
        assert row_b["qualified_demand"] == 0.0
        assert row_b["net_flow"] == 10.0
        assert row_b["planning_zone"] == "red"
        assert row_b["order_signal"] is True
        assert row_b["suggested_order_units"] == 571
        assert row_b["planning_priority_pct"] == 1.7
        assert row_b["on_hand_status"] == "low"
        assert row_b["on_hand_pct_of_red"] == 50.0
        assert row_b["notes"] == [ZERO_QUALIFIED_NOTE]

        # A: only the newest sync of each source system counts, so
        # the older-sync lines (po 100.0, so 999.0) are out; the
        # stale po line (promised 120 days before the as-of date) is
        # excluded with the note, the 0-unit lines count for nothing.
        assert row_a["status"] == "ok"
        assert row_a["top_of_red"] == 20
        assert row_a["top_of_yellow"] == 40
        assert row_a["top_of_green"] == 581
        assert row_a["on_hand"] == 10.0
        assert row_a["open_supply"] == 78.0
        assert row_a["qualified_due"] == 50.0
        assert row_a["qualified_spike"] == 12.0
        assert row_a["qualified_demand"] == 62.0
        assert row_a["net_flow"] == 26.0
        assert row_a["planning_zone"] == "yellow"
        assert row_a["order_signal"] is True
        assert row_a["suggested_order_units"] == 555
        assert row_a["planning_priority_pct"] == 4.5
        assert row_a["on_hand_status"] == "low"
        assert row_a["on_hand_pct_of_red"] == 50.0
        assert row_a["notes"] == [A_STALE_NOTE, A_QUALIFIED_NOTE]
        assert plan["summary"] == {
            "signals": 2,
            "stocked_out": 0,
            "critical": 0,
            "low": 2,
        }

        # The plan read both feeds once each, with pairs set to the
        # sorted distinct (item_code, site_code) of the "ok" zone
        # rows, and answered the feed check with has_lines.
        assert po_calls == [((), {"pairs": EXPECTED_PAIRS})]
        assert so_calls == [((), {"pairs": EXPECTED_PAIRS})]
        assert has_lines_calls == [((), {})]

        # --- What is read --------------------------------------------
        # With pairs set, the reads answer only A's and B's rows of
        # the newest syncs: B's older-sync-only rows are out (a pair
        # whose rows are all in an older sync of its source system
        # gets none) and C's rows are never fetched.
        po_filtered = PO_REPO.list_latest(pairs=EXPECTED_PAIRS)
        assert _keys(po_filtered) == [PO_A2, PO_A3, PO_A4, PO_A5, PO_A6]
        assert all(
            row["item_code"] == ITEM_A and row["site_code"] == SITE_A
            for row in po_filtered
        )
        so_filtered = SO_REPO.list_latest(pairs=EXPECTED_PAIRS)
        assert _keys(so_filtered) == [
            SO_A2,
            SO_A3,
            SO_A4,
            SO_A5,
            SO_A6,
            SO_A7,
            SO_A8,
        ]
        assert all(
            row["item_code"] == ITEM_A and row["site_code"] == SITE_A
            for row in so_filtered
        )
        # With no argument, the reads still answer every pair's
        # newest rows, C's included; B's older-sync rows are out.
        po_all = PO_REPO.list_latest()
        assert _keys(po_all) == [PO_A2, PO_A3, PO_A4, PO_A5, PO_A6, PO_C2]
        so_all = SO_REPO.list_latest()
        assert _keys(so_all) == [
            SO_A2,
            SO_A3,
            SO_A4,
            SO_A5,
            SO_A6,
            SO_A7,
            SO_A8,
            SO_C3,
        ]
        assert not any(row["item_code"] == ITEM_B for row in po_all + so_all)
        assert not any(row["item_code"] == ITEM_B for row in po_filtered + so_filtered)
        po_c2 = next(row for row in po_all if row["business_key"] == PO_C2)
        assert (po_c2["item_code"], po_c2["site_code"]) == (ITEM_C, SITE_C)
        assert po_c2["open_units"] == 30.0
        so_c3 = next(row for row in so_all if row["business_key"] == SO_C3)
        assert (so_c3["item_code"], so_c3["site_code"]) == (ITEM_C, SITE_C)
        assert so_c3["open_units"] == 50.0

        # An empty tuple answers [] without a query.
        def _no_get_cursor(*args, **kwargs):
            raise AssertionError("an empty pairs tuple must not run a query")

        orig_po_get_cursor = po_repo_module.get_cursor
        orig_so_get_cursor = so_repo_module.get_cursor
        monkeypatch.setattr(po_repo_module, "get_cursor", _no_get_cursor)
        assert PO_REPO.list_latest(pairs=()) == []
        monkeypatch.setattr(so_repo_module, "get_cursor", _no_get_cursor)
        assert SO_REPO.list_latest(pairs=()) == []
        monkeypatch.setattr(po_repo_module, "get_cursor", orig_po_get_cursor)
        monkeypatch.setattr(so_repo_module, "get_cursor", orig_so_get_cursor)
        # The feed holds rows: has_lines answers True.
        assert SO_REPO.has_lines() is True

        # --- The feed check: only C's open sales lines left ----------
        # A's and B's lines are out of the feed, C's are in it: the
        # filtered read of the "ok" pairs is empty, yet the feed
        # carries lines, so every row keeps the qualified-demand
        # note — with 0 units — not the no-demand note.
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _delete_keys(
                    cur,
                    "open_sales_lines",
                    (
                        SO_A1_OLD,
                        SO_B1_OLD,
                        SO_A2,
                        SO_A3,
                        SO_A4,
                        SO_A5,
                        SO_A6,
                        SO_A7,
                        SO_A8,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        assert SO_REPO.list_latest(pairs=EXPECTED_PAIRS) == []
        assert SO_REPO.has_lines() is True

        resp = client.get(PLAN_URL)
        assert resp.status_code == 200, resp.text
        plan = resp.json()
        rows = plan["rows"]
        assert [(r["item_code"], r["site_code"]) for r in rows] == [
            (ITEM_B, SITE_B),
            (ITEM_A, SITE_A),
        ]
        row_b, row_a = rows
        assert row_b["qualified_due"] == 0.0
        assert row_b["qualified_spike"] == 0.0
        assert row_b["qualified_demand"] == 0.0
        assert row_b["net_flow"] == 10.0
        assert row_b["suggested_order_units"] == 571
        assert row_b["notes"] == [ZERO_QUALIFIED_NOTE]
        # A: the demand is 0 (its pair has no line in the feed) but
        # the note is the qualified-demand note, not the no-demand
        # one; the net flow is the on hand plus the open supply.
        assert row_a["qualified_due"] == 0.0
        assert row_a["qualified_spike"] == 0.0
        assert row_a["qualified_demand"] == 0.0
        assert row_a["open_supply"] == 78.0
        assert row_a["net_flow"] == 88.0
        assert row_a["planning_zone"] == "green"
        assert row_a["order_signal"] is False
        assert row_a["suggested_order_units"] == 0
        assert row_a["planning_priority_pct"] == 15.1
        assert row_a["notes"] == [A_STALE_NOTE, ZERO_QUALIFIED_NOTE]
        for row in rows:
            assert DEMAND_NOTE not in row["notes"]
        assert len(po_calls) == 2 and len(so_calls) == 2 and len(has_lines_calls) == 2

        # --- The feed check: no open sales lines at all ---------------
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _delete_keys(cur, "open_sales_lines", (SO_C1_OLD, SO_C3))
            conn.commit()
        finally:
            conn.close()
        assert SO_REPO.has_lines() is False

        resp = client.get(PLAN_URL)
        assert resp.status_code == 200, resp.text
        plan = resp.json()
        rows = plan["rows"]
        assert [(r["item_code"], r["site_code"]) for r in rows] == [
            (ITEM_B, SITE_B),
            (ITEM_A, SITE_A),
        ]
        row_b, row_a = rows
        # Every row keeps the no-demand note, the qualified demand is
        # 0, and the two part fields stay out of the row, exactly as
        # before the feed existed.
        assert row_b["qualified_demand"] == 0.0
        assert "qualified_due" not in row_b
        assert "qualified_spike" not in row_b
        assert row_b["net_flow"] == 10.0
        assert row_b["order_signal"] is True
        assert row_b["suggested_order_units"] == 571
        assert row_b["notes"] == [DEMAND_NOTE]
        assert row_a["qualified_demand"] == 0.0
        assert "qualified_due" not in row_a
        assert "qualified_spike" not in row_a
        assert row_a["open_supply"] == 78.0
        assert row_a["net_flow"] == 88.0
        assert row_a["planning_zone"] == "green"
        assert row_a["order_signal"] is False
        assert row_a["suggested_order_units"] == 0
        assert row_a["planning_priority_pct"] == 15.1
        assert row_a["notes"] == [A_STALE_NOTE, DEMAND_NOTE]
        assert plan["summary"] == {
            "signals": 1,
            "stocked_out": 0,
            "critical": 0,
            "low": 2,
        }
        assert len(po_calls) == 3 and len(so_calls) == 3 and len(has_lines_calls) == 3

        # --- No buffered rows -----------------------------------------
        # No designation, no "ok" zone row: neither list_latest read
        # and not has_lines() is called (spied).
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ddmrp_buffers WHERE (item_code, site_code) IN %s",
                    (EXPECTED_PAIRS,),
                )
            conn.commit()
        finally:
            conn.close()

        resp = client.get(ZONES_URL)
        assert resp.status_code == 200, resp.text
        assert resp.json()["rows"] == []

        po_calls.clear()
        so_calls.clear()
        has_lines_calls.clear()
        resp = client.get(PLAN_URL)
        assert resp.status_code == 200, resp.text
        plan = resp.json()
        assert plan["rows"] == []
        assert plan["summary"] == {
            "signals": 0,
            "stocked_out": 0,
            "critical": 0,
            "low": 0,
        }
        assert po_calls == []
        assert so_calls == []
        assert has_lines_calls == []
    finally:
        # Every row this test inserted, deleted.
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _delete_keys(cur, "open_purchase_lines", PO_KEYS)
                _delete_keys(cur, "open_sales_lines", SO_KEYS)
                cur.execute(
                    "DELETE FROM ddmrp_buffers WHERE (item_code, site_code) IN %s",
                    (EXPECTED_PAIRS,),
                )
                cur.execute(
                    "DELETE FROM on_hand WHERE id = ANY(%s)",
                    ([f"id-oh-1548-{item}" for item in (ITEM_A, ITEM_B)],),
                )
                cur.execute(
                    "DELETE FROM lead_time_observations "
                    "WHERE business_key LIKE 'lt-1548-%'"
                )
                cur.execute(
                    "DELETE FROM demand_events WHERE business_key LIKE 'de-1548-%'"
                )
                cur.execute(
                    "DELETE FROM item_sites WHERE id = ANY(%s)",
                    ([f"id-is-1548-{item}" for item in (ITEM_A, ITEM_B, ITEM_C)],),
                )
            conn.commit()
        finally:
            conn.close()
