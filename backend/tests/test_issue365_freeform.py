"""Issue #365 proof: recommendations_for_all on a fixed query budget.

AC-1/AC-3 — ``recommendations_for_all`` builds every item-site's
forecast basis from the inputs the list already loaded: the demand
statistics row ``demand_stats_for_all`` loaded, the 30-day history
buckets of the new ``history_for_all`` (one query for every
item-site, equivalent to ``history_for_pair`` for each pair by the
same complete-bucket rule), the counted overrides of one
``counted_overrides`` call (one query for every item-site, the rule
``effective_for_all`` shares) and the
``replenishment.demand_basis`` setting read once per call — so the
number of SQL statements is fixed, whatever the number of item-sites.

AC-2 — against the test database (as-of 2026-09-30, date.today pinned
to 2026-10-02) with demand events and lead-time observations:
  * ``history_for_all`` equals ``history_for_pair`` for every
    item-site, including a young item with fewer than 12 complete
    buckets and an item-site with no demand at all;
  * ``recommendations_for_all`` returns, for every item-site, exactly
    the recommendation ``recommendation_for_key`` returns, with the
    setting at history and again at forecast, one item-site carrying
    a stored planner override of its first forecast period;
  * the number of SQL statements ``recommendations_for_all``
    executes, counted by wrapping the cursor's ``execute``, is the
    same with 5 recommendable item-sites as with 25;
  * the setting is read once per call, counted the same way.
"""

import math
import statistics
from datetime import date, timedelta

import psycopg2
import pytest

from app.db.connection import get_connection
from app.forecast.history import history_for_all, history_for_pair
from app.recommend.reorder import (
    recommendation_for_key,
    recommendations_for_all,
)

AS_OF = date(2026, 9, 30)
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
SITE = "SITE-1"
KEY = "replenishment.demand_basis"
Z95 = statistics.NormalDist().inv_cdf(0.95)  # 1.644854
# The fixed budget of recommendations_for_all for an explicit as_of:
# 2 demand_stats_for_all + 1 lead_time_stats_for_all + 1 item_sites +
# 1 on_hand + 1 open_purchase_lines + 1 settings + 1 history_for_all +
# 1 counted overrides (issue #453 added the on_hand and
# open_purchase_lines reads.
FIXED_STATEMENTS = 10


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


class _CountingCursor:
    """A cursor that counts its execute calls, delegating the rest."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def execute(self, query, *args, **kwargs):
        self._counter[0] += 1
        self._queries.append(query)
        return self._real.execute(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._real.close()
        return False


class _CountingConnection:
    """A connection whose cursors count their execute calls."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def cursor(self, *args, **kwargs):
        return _CountingCursor(
            self._real.cursor(*args, **kwargs), self._counter, self._queries
        )

    def __getattr__(self, name):
        return getattr(self._real, name)


def _settings_reads(queries):
    """The settings-table reads (the SELECT of the settings get)."""
    return [
        query
        for query in queries
        if "FROM settings " in query and "settings_changes" not in query
    ]


def _measure(fn):
    """Run fn with psycopg2.connect wrapped; (result, statements, SQL)."""
    counter = [0]
    queries: list[str] = []
    real_connect = psycopg2.connect

    def counting_connect(**params):
        return _CountingConnection(real_connect(**params), counter, queries)

    psycopg2.connect = counting_connect
    try:
        result = fn()
    finally:
        psycopg2.connect = real_connect
    return result, counter[0], queries


def _demand(cur, item, first, days, quantity):
    """``days`` of ``quantity`` units a day for one item, from ``first``."""
    cur.executemany(
        "INSERT INTO demand_events (id, business_key, item_code, site_code, "
        "quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                f"id-de-365-{item}-{i}",
                f"de-365-{item}-{i}",
                item,
                SITE,
                quantity,
                (first + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(days)
        ],
    )


def _lead_times(cur, item):
    """Three lead-time observations of 8, 10 and 12 days: mean 10,
    sample std 2, not low confidence."""
    for i, (ordered, received) in enumerate(
        (
            ("2026-08-22", "2026-08-30"),
            ("2026-08-20", "2026-08-30"),
            ("2026-08-18", "2026-08-30"),
        )
    ):
        cur.execute(
            "INSERT INTO lead_time_observations (id, business_key, "
            "supplier_code, item_code, ordered_on, received_on, "
            "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                f"id-lt-365-{item}-{i}",
                f"lt-365-{item}-{i}",
                f"SUP-365-{item}",
                item,
                ordered,
                received,
                "test",
            ),
        )


def _item_site(cur, item, reorder_point, safety_stock):
    """The item-site's current planning settings."""
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, source_system) "
        "VALUES (%s, %s, %s, 1.0, %s, %s, 'test')",
        (f"id-is-365-{item}", item, SITE, reorder_point, safety_stock),
    )


def test_issue365_freeform(client, monkeypatch, admin_headers):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    # --- seed 5 recommendable item-sites (360 days, 2.0 a day -> smooth) --
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for i in range(1, 6):
                item = f"K{i}"
                _demand(cur, item, FIRST_DAY, 360, 2.0)
                _lead_times(cur, item)
                _item_site(cur, item, 25.0, 10.0)
        conn.commit()
    finally:
        conn.close()

    # --- the fixed query budget: 5 recommendable item-sites ---------------
    rows5, count5, queries5 = _measure(lambda: recommendations_for_all(AS_OF))
    assert [(r["item_code"], r["site_code"]) for r in rows5] == [
        (f"K{i}", SITE) for i in range(1, 6)
    ]
    assert all(row["status"] == "ok" for row in rows5)
    # Every item-site's forecast basis is built from the already-loaded
    # inputs, so the statement count is the fixed budget — not 9 plus
    # five per-item-site forecast reads.
    assert count5 == FIXED_STATEMENTS
    # The setting is read once per call.
    assert len(_settings_reads(queries5)) == 1

    # --- and still fixed with 25 recommendable item-sites -----------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for i in range(6, 26):
                item = f"K{i}"
                _demand(cur, item, FIRST_DAY, 360, 2.0)
                _lead_times(cur, item)
                _item_site(cur, item, 25.0, 10.0)
        conn.commit()
    finally:
        conn.close()

    rows25, count25, queries25 = _measure(lambda: recommendations_for_all(AS_OF))
    assert len(rows25) == 25
    assert count25 == count5 == FIXED_STATEMENTS
    assert len(_settings_reads(queries25)) == 1

    # --- the equivalence fixture ------------------------------------------
    # Y365: young — 65 days of demand, only two complete buckets (the
    # first demand's 5 earlier days are not in the history).  O365: the
    # item that carries a planner override.  N365: in item_sites but
    # with no demand events at all.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand(cur, "Y365", AS_OF - timedelta(days=64), 65, 4.0)
            _lead_times(cur, "Y365")
            _item_site(cur, "Y365", 40.0, 12.0)
            _demand(cur, "O365", FIRST_DAY, 360, 2.0)
            _lead_times(cur, "O365")
            _item_site(cur, "O365", 30.0, 8.0)
            _item_site(cur, "N365", 5.0, 2.0)
        conn.commit()
    finally:
        conn.close()

    # The planner override of O365's first forecast period
    # (2026-10-01, the day after the as-of, through 2026-10-30) to
    # 100 units.
    response = client.post(
        "/api/forecast/overrides",
        json={
            "item_code": "O365",
            "site_code": SITE,
            "period_start": "2026-10-01",
            "quantity": 100.0,
            "reason": "planner test for issue 365",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["applies"] is True

    # --- history_for_all equals history_for_pair, for every item-site -----
    all_items = [f"K{i}" for i in range(1, 26)] + ["O365", "Y365", "N365"]
    histories = history_for_all(AS_OF)
    assert set(histories) == {(item, SITE) for item in all_items if item != "N365"}
    for item in all_items:
        pair = (item, SITE)
        assert histories.get(pair) == history_for_pair(item, SITE, AS_OF), item
    # The young item has fewer than 12 complete buckets, the stopped
    # item none.
    young = histories[("Y365", SITE)]
    assert len(young) == 2
    assert young[0]["bucket_start"] == AS_OF - timedelta(days=59)
    assert young[0]["bucket_end"] == AS_OF - timedelta(days=30)
    assert young[-1]["bucket_start"] == AS_OF - timedelta(days=29)
    assert young[-1]["bucket_end"] == AS_OF
    assert all(bucket["quantity"] == 120.0 for bucket in young)
    assert histories.get(("N365", SITE)) is None
    assert history_for_pair("N365", SITE, AS_OF) is None

    # --- recommendations_for_all == recommendation_for_key, basis history -
    listed = recommendations_for_all(AS_OF)
    assert [(r["item_code"], r["site_code"]) for r in listed] == sorted(
        (item, SITE) for item in all_items if item != "N365"
    )
    by_item = {(r["item_code"], r["site_code"]): r for r in listed}
    for item in all_items:
        if item == "N365":
            continue
        single = recommendation_for_key(item, SITE, AS_OF)
        assert by_item[(item, SITE)] == single, item

    # K1 spot check, hand-computed: d 2.0, sd_d 0.0, LT 10, sd_LT 2 ->
    # safety stock Z95 * 4, reorder point 20 + Z95 * 4.  The override
    # does not touch the historical inputs.
    k1 = by_item[("K1", SITE)]
    assert k1["demand_basis"] == "history"
    assert k1["notes"] == []
    assert k1["safety_stock"] == pytest.approx(Z95 * 4.0)
    assert k1["reorder_point"] == pytest.approx(20.0 + Z95 * 4.0)
    assert k1["safety_stock_units"] == 7
    assert k1["reorder_point_units"] == 27
    assert k1["other_basis"]["basis"] == "forecast"
    assert k1["other_basis"]["inputs"]["d"] == pytest.approx(2.0)
    assert k1["other_basis"]["inputs"]["sd_d"] == pytest.approx(0.0)
    assert k1["current"] == {"reorder_point": 25.0, "safety_stock": 10.0}
    assert k1["delta_reorder_point"] == pytest.approx(25.0 - (20.0 + Z95 * 4.0))

    # Y365, the young item: d 4.0, sd_d 0.0 -> reorder point 40 + Z95*8;
    # with two buckets the forecast is the mean with no backtest, so
    # the other basis reuses the historical spread and equals this one.
    y_row = by_item[("Y365", SITE)]
    assert y_row["status"] == "ok"
    assert y_row["reorder_point"] == pytest.approx(40.0 + Z95 * 8.0)
    assert y_row["other_basis"]["inputs"]["d"] == pytest.approx(4.0)
    assert y_row["other_basis"]["inputs"]["sd_d"] == pytest.approx(0.0)
    assert y_row["other_basis"]["reorder_point"] == pytest.approx(
        y_row["reorder_point"]
    )

    # O365 carries the override: the forecast basis takes 100/30 a day
    # over the 10-day window, the historical inputs stay untouched.
    o_row = by_item[("O365", SITE)]
    assert o_row["demand_basis"] == "history"
    assert o_row["other_basis"]["basis"] == "forecast"
    assert o_row["other_basis"]["inputs"]["d"] == pytest.approx(100.0 / 30.0)
    assert o_row["other_basis"]["inputs"]["sd_d"] == pytest.approx(0.0)
    assert o_row["other_basis"]["reorder_point"] == pytest.approx(
        (100.0 / 30.0) * 10 + Z95 * math.sqrt(4.0 * (100.0 / 30.0) ** 2)
    )
    assert o_row["reorder_point"] == pytest.approx(20.0 + Z95 * 4.0)
    assert o_row["current"] == {"reorder_point": 30.0, "safety_stock": 8.0}

    # N365, the no-demand item-site, is not in the list; the per-key
    # path reports not_recommendable_demand with the history basis.
    stopped = recommendation_for_key("N365", SITE, AS_OF)
    assert stopped["status"] == "not_recommendable_demand"
    assert stopped["demand_class"] == "no_demand"
    assert stopped["demand_basis"] == "history"
    assert stopped["other_basis"] is None
    assert stopped["notes"] == []
    assert stopped["current"] == {"reorder_point": 5.0, "safety_stock": 2.0}
    assert stopped["delta_reorder_point"] is None

    # --- the same answers with the setting at forecast --------------------
    response = client.put(
        f"/api/settings/{KEY}", json={"value": "forecast"}, headers=admin_headers
    )
    assert response.status_code == 200, response.text

    listed = recommendations_for_all(AS_OF)
    by_item = {(r["item_code"], r["site_code"]): r for r in listed}
    for item in all_items:
        if item == "N365":
            continue
        single = recommendation_for_key(item, SITE, AS_OF)
        assert by_item[(item, SITE)] == single, item

    # O365 is now built on the forecast: the overridden 100/30 a day,
    # and the delta follows the active basis (30.0 - 44.299356).
    o_row = by_item[("O365", SITE)]
    assert o_row["demand_basis"] == "forecast"
    assert o_row["inputs"]["d"] == pytest.approx(100.0 / 30.0)
    assert o_row["reorder_point"] == pytest.approx(
        (100.0 / 30.0) * 10 + Z95 * math.sqrt(4.0 * (100.0 / 30.0) ** 2)
    )
    assert o_row["notes"] == ["forecast demand over 10 days"]
    assert o_row["delta_reorder_point"] == pytest.approx(
        30.0 - ((100.0 / 30.0) * 10 + Z95 * math.sqrt(4.0 * (100.0 / 30.0) ** 2))
    )
    assert o_row["other_basis"]["basis"] == "history"
    assert o_row["other_basis"]["inputs"]["d"] == pytest.approx(2.0)

    # Y365: the young item's forecast has no backtest, so the historical
    # spread is used and the note says so.
    y_row = by_item[("Y365", SITE)]
    assert y_row["demand_basis"] == "forecast"
    assert y_row["notes"] == [
        "forecast demand over 10 days",
        "no backtest: historical variability used",
    ]

    # N365 has no forecast: built on the history with the note.
    stopped = recommendation_for_key("N365", SITE, AS_OF)
    assert stopped["status"] == "not_recommendable_demand"
    assert stopped["demand_basis"] == "history"
    assert stopped["notes"] == ["no forecast: history used"]
    assert stopped["other_basis"] is None

    # Back to the default.
    response = client.delete(f"/api/settings/{KEY}", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert response.json()["value"] == "history"
    assert response.json()["source"] == "default"
