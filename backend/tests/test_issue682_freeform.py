"""Issue #682 proof: the empirical reorder point of intermittent demand.

AC-1 — ``demand_stats_for_all`` and ``demand_stats_for_pair`` gain a
``with_daily`` keyword. With it true each returned row also carries
``daily_totals``, the pair's ``{date: day_total}`` mapping the
function already reads, and ``daily_window_start``, the first day of
the pair's window: the 360-day window's start, or the pair's
first-seen day when that is later. With it false (the default) every
row is exactly as before, and both functions execute exactly the same
SQL statements, in the same number, either way.

AC-2 — ``recommendations_for_all`` and ``recommendation_for_key``
read the demand statistics with ``with_daily=True`` and add
``empirical`` to every result: for an item-site whose
``demand_class`` is ``intermittent`` or ``lumpy`` and that has a
usable lead time, ``empirical_reorder_point`` over the pair's
``daily_totals`` from ``daily_window_start`` to the call's as-of, at
the lead time's mean days ``recommend`` chooses (the pooled mean,
else the master lead time) and the service level the item-site is
recommended at (its class level when service levels by class are
on); null for every other demand class and for an intermittent or
lumpy item-site with no usable lead time. Every other field of every
result is unchanged — the intermittent item-site keeps status
``not_recommendable_demand`` with null ``safety_stock`` and
``reorder_point`` — the single-key path and the list agree, and
``recommendations_for_all`` executes the same fixed number of SQL
statements as before (the count test 453 pins still holds there).

AC-3 — ``app.api.recommendations`` gains the
``EmpiricalReorderPoint`` model with the eleven keys of the
``empirical_reorder_point`` result; both ``RecommendationResponse``
routes return it as the optional ``empirical`` field and the
model's ``required`` list is unchanged. backend/openapi.json carries
the new model and property and nothing else.

AC-4 — against the test database (as-of 2026-09-30, date.today
pinned to 2026-10-02, service level 0.95, z 1.644854) with one
smooth item-site (2.0 a day for 360 days) and one intermittent
item-site (demand in 3 of its 12 buckets — 60 units in each — and a
usable pooled lead time of mean 10 days):

  * the intermittent item-site reports status
    ``not_recommendable_demand`` with null ``reorder_point`` and
    ``empirical`` equal to ``empirical_reorder_point`` called
    directly on the same daily totals, window, lead time and service
    level (95th percentile of the 351 overlapping 10-day span
    totals, 35 of them positive, hence positive);
  * the smooth item-site keeps exactly the pre-change
    recommendation with ``empirical`` null;
  * GET /api/recommendations/by-key for the intermittent item-site
    returns the same ``empirical`` object as GET /api/recommendations;
  * counted by wrapping the cursor's ``execute`` (the way test 365
    counts), ``recommendations_for_all`` executes the same number of
    statements with 2 item-sites as with 6 (the fixed budget of 9);
  * with ``with_daily`` false (the default) ``demand_stats_for_all``
    returns rows without ``daily_totals`` or ``daily_window_start``;
  * the master-lead-time fallback stands in for a pooled lead time
    whose ``std_days`` is unusable (non-finite or negative) exactly as
    it does for an unusable ``mean_days`` — the selection, the
    recommendation and the empirical all use the master lead time, and
    without a usable master lead time there is no usable lead time at
    all (the database cannot produce the case, so the pure selection
    is proven directly).
"""

import math
import statistics
from datetime import date, timedelta

import psycopg2

from app.db.connection import get_connection
from app.recommend import reorder
from app.recommend.empirical import empirical_reorder_point
from app.recommend.order_quantity import recommend_order_quantity
from app.recommend.reorder import (
    METHOD,
    recommendation_for_key,
    recommendations_for_all,
)
from app.stats.demand import demand_stats_for_all, demand_stats_for_pair

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
SITE = "S1"
SMOOTH = "S682"
INTER = "I682"
EXTRA = [f"M682-{i}" for i in range(1, 5)]
Z95 = statistics.NormalDist().inv_cdf(0.95)  # 1.644854
SERVICE_LEVEL = 0.95
# The pooled lead time of the 8/10/12-day observations both items
# carry: mean 10 (the lead time the formula and the empirical method
# use), sample std 2, n_used 3 -> not low confidence.
LEAD_TIME_DAYS = 10.0

# The intermittent item's daily demand: 60 units on the first day
# (the pair's first-seen day, in bucket 11), 4 units a day for 15
# days in bucket 0 and 6 units a day for 10 days in bucket 1 — 3 of
# its 12 buckets non-zero, each totalling 60 (ADI 4, cv2 0 ->
# intermittent), 35 of the 351 overlapping 10-day span totals
# positive, so the 95th percentile of the spans is positive.
INTER_DAILY: dict[date, float] = {AS_OF - timedelta(days=359): 60.0}
for _OFFSET in range(15):
    INTER_DAILY[AS_OF - timedelta(days=_OFFSET)] = 4.0
for _OFFSET in range(30, 40):
    INTER_DAILY[AS_OF - timedelta(days=_OFFSET)] = 6.0
# The pair's window starts on the 360-day window's start: first_seen
# (the first day) is not later than it.
INTER_WINDOW_START = FIRST_DAY
INTER_SPANS = 351  # 360 days - 10 + 1
INTER_SPANS_WITH_DEMAND = 35

# The fixed budget of recommendations_for_all for an explicit as_of:
# 2 demand_stats_for_all + 1 lead_time_stats_for_all + 1 item_sites +
# 1 on_hand + 1 open_purchase_lines + 1 settings + 1 history_for_all +
# 1 counted overrides.
FIXED_STATEMENTS = 10

# The 17 keys of a demand statistics row as it is today (no daily
# keys): the identity, as_of and compute_demand_stats output.
STATS_KEYS = {
    "item_code",
    "site_code",
    "as_of",
    "bucket_days",
    "n_buckets",
    "nonzero_buckets",
    "window_start",
    "window_end",
    "total_quantity",
    "mean_daily",
    "std_daily",
    "adi",
    "cv2",
    "demand_class",
    "recommendable",
    "intermittent",
    "method",
}


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


def _measure(fn):
    """Run fn with psycopg2.connect wrapped; (result, statements, SQL)."""
    counter = [0]
    queries: list[str] = []
    real_connect = psycopg2.connect

    def counting_connect(*args, **kwargs):
        return _CountingConnection(real_connect(*args, **kwargs), counter, queries)

    psycopg2.connect = counting_connect
    try:
        result = fn()
    finally:
        psycopg2.connect = real_connect
    return result, counter[0], queries


DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)


def _demand(cur, item, first, days, quantity):
    """``days`` of ``quantity`` units a day for one item, from ``first``."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-682-{item}-{i}",
                f"de-682-{item}-{i}",
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


def _demand_events(cur, item, daily: dict[date, float]):
    """One demand event per day of ``daily`` for one item."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-682-{item}-{day.isoformat()}",
                f"de-682-{item}-{day.isoformat()}",
                item,
                SITE,
                quantity,
                day.isoformat(),
                "shipment",
                "test",
            )
            for day, quantity in sorted(daily.items(), reverse=True)
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
                f"id-lt-682-{item}-{i}",
                f"lt-682-{item}-{i}",
                f"SUP-682-{item}",
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
        (f"id-is-682-{item}", item, SITE, reorder_point, safety_stock),
    )


def _basis_arithmetic(d: float, sd_d: float, lt: float, sd_lt: float) -> str:
    """The exact arithmetic string the formula applies to the inputs."""
    safety_stock = Z95 * math.sqrt(lt * sd_d * sd_d + d * d * sd_lt * sd_lt)
    reorder_point = d * lt + safety_stock
    return (
        "safety_stock = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2) "
        f"= {Z95:.6f} * sqrt({lt:g} * {sd_d:g}^2 + {d:g}^2 * {sd_lt:g}^2) "
        f"= {safety_stock:.2f}; "
        "reorder_point = d * LT + safety_stock "
        f"= {d:g} * {lt:g} + {safety_stock:.2f} = {reorder_point:.2f}"
    )


def _smooth_expected() -> dict:
    """The smooth item-site's whole recommendation as it was before
    this change (d 2.0, sd_d 0.0, LT 10, sd_LT 2, z Z95, service level
    0.95, unit cost 1.0, current 25.0/10.0, no position rows), with
    the new ``empirical`` field null."""
    safety_stock = Z95 * 4.0
    reorder_point = 20.0 + safety_stock
    order_quantity = recommend_order_quantity(2.0, 1.0, 50.0, 0.25)
    return {
        "item_code": SMOOTH,
        "site_code": SITE,
        "as_of": AS_OF,
        "status": "ok",
        "demand_class": "smooth",
        "safety_stock": safety_stock,
        "reorder_point": reorder_point,
        "safety_stock_units": 7,
        "reorder_point_units": 27,
        "signal_units": 0.0,
        "reorder_point_before_signals_units": 27,
        "inputs": {
            "d": 2.0,
            "sd_d": 0.0,
            "lt": LEAD_TIME_DAYS,
            "sd_lt": 2.0,
            "z": Z95,
            "service_level": SERVICE_LEVEL,
        },
        "arithmetic": _basis_arithmetic(2.0, 0.0, LEAD_TIME_DAYS, 2.0),
        "method": METHOD,
        "notes": [],
        "demand_basis": "history",
        "other_basis": {
            "basis": "forecast",
            "safety_stock": safety_stock,
            "reorder_point": reorder_point,
            "safety_stock_units": 7,
            "reorder_point_units": 27,
            "inputs": {
                "d": 2.0,
                "sd_d": 0.0,
                "lt": LEAD_TIME_DAYS,
                "sd_lt": 2.0,
                "z": Z95,
                "service_level": SERVICE_LEVEL,
            },
        },
        "service_level_source": {"abc_class": "A", "source": "single"},
        "current": {"reorder_point": 25.0, "safety_stock": 10.0},
        "delta_reorder_point": 25.0 - reorder_point,
        "on_hand_units": 0,
        "on_order_units": 0,
        "unit_cost": 1.0,
        "source_system": "test",
        "suggested_order_quantity": 27,
        "order_quantity": {
            **order_quantity,
            "recommended_max_units": 27 + order_quantity["order_quantity_units"],
            "current_order_qty": None,
            "current_max_qty": None,
        },
        "empirical": None,
    }


def test_issue682_freeform(client, monkeypatch):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # --- seed one smooth and one intermittent item-site -----------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand(cur, SMOOTH, FIRST_DAY, 360, 2.0)
            _lead_times(cur, SMOOTH)
            _item_site(cur, SMOOTH, 25.0, 10.0)
            _demand_events(cur, INTER, INTER_DAILY)
            _lead_times(cur, INTER)
            _item_site(cur, INTER, 9.0, 1.0)
        conn.commit()
    finally:
        conn.close()

    # --- AC-1: with_daily false (the default) is exactly as today -------
    rows_plain = demand_stats_for_all(AS_OF)
    assert [(r["item_code"], r["site_code"]) for r in rows_plain] == [
        (INTER, SITE),
        (SMOOTH, SITE),
    ]
    for row in rows_plain:
        assert "daily_totals" not in row
        assert "daily_window_start" not in row
        assert set(row) == STATS_KEYS
    pair_plain = demand_stats_for_pair(INTER, SITE, AS_OF)
    assert "daily_totals" not in pair_plain
    assert "daily_window_start" not in pair_plain
    assert set(pair_plain) == STATS_KEYS

    # With it true: the same rows, each also carrying the pair's daily
    # totals already read and the first day of its window (the 360-day
    # window's start, or the pair's first-seen day when that is later).
    rows_daily = demand_stats_for_all(AS_OF, with_daily=True)
    assert [(r["item_code"], r["site_code"]) for r in rows_daily] == [
        (INTER, SITE),
        (SMOOTH, SITE),
    ]
    for plain, daily in zip(rows_plain, rows_daily):
        assert {
            key: value
            for key, value in daily.items()
            if key not in ("daily_totals", "daily_window_start")
        } == plain
        assert set(daily) == STATS_KEYS | {"daily_totals", "daily_window_start"}
    inter_stats = rows_daily[0]
    smooth_stats = rows_daily[1]
    # Both pairs' first-seen day is the first day: their window starts
    # on the 360-day window's start.
    assert inter_stats["daily_totals"] == INTER_DAILY
    assert inter_stats["daily_window_start"] == INTER_WINDOW_START
    assert smooth_stats["daily_totals"] == {
        FIRST_DAY + timedelta(days=i): 2.0 for i in range(360)
    }
    assert smooth_stats["daily_window_start"] == FIRST_DAY
    # The pair reader agrees with the list on the daily keys.
    pair_daily = demand_stats_for_pair(INTER, SITE, AS_OF, with_daily=True)
    for key, value in pair_plain.items():
        assert pair_daily[key] == value
    assert pair_daily["daily_totals"] == INTER_DAILY
    assert pair_daily["daily_window_start"] == INTER_WINDOW_START

    # The same statements, in the same number, either way.
    _, count_plain, _ = _measure(lambda: demand_stats_for_all(AS_OF))
    _, count_daily, _ = _measure(lambda: demand_stats_for_all(AS_OF, with_daily=True))
    assert count_plain == count_daily == 2
    _, count_pair_plain, _ = _measure(lambda: demand_stats_for_pair(INTER, SITE, AS_OF))
    _, count_pair_daily, _ = _measure(
        lambda: demand_stats_for_pair(INTER, SITE, AS_OF, with_daily=True)
    )
    assert count_pair_plain == count_pair_daily == 2

    # --- AC-2: the recommendation gains the empirical field -------------
    rows, count2, _ = _measure(lambda: recommendations_for_all(AS_OF))
    assert [(r["item_code"], r["site_code"]) for r in rows] == [
        (INTER, SITE),
        (SMOOTH, SITE),
    ]
    inter_row, smooth_row = rows
    # The smooth item-site: exactly the pre-change recommendation,
    # empirical null.
    assert smooth_row == _smooth_expected()
    assert smooth_row["empirical"] is None
    # The intermittent item-site: not recommendable with a null reorder
    # point, empirical the direct call on the same daily totals,
    # window, lead time and service level.
    assert inter_row["status"] == "not_recommendable_demand"
    assert inter_row["demand_class"] == "intermittent"
    assert inter_row["safety_stock"] is None
    assert inter_row["reorder_point"] is None
    emp_expected = empirical_reorder_point(
        INTER_DAILY, INTER_WINDOW_START, AS_OF, LEAD_TIME_DAYS, SERVICE_LEVEL
    )
    assert emp_expected is not None
    # Hand check: 351 overlapping 10-day spans, 35 of them positive,
    # so the 95th percentile (the 334th smallest of 351) is positive.
    assert emp_expected["window_days"] == 10
    assert emp_expected["spans"] == INTER_SPANS
    assert emp_expected["spans_with_demand"] == INTER_SPANS_WITH_DEMAND
    assert emp_expected["service_level"] == SERVICE_LEVEL
    assert emp_expected["reorder_point"] > 0.0
    assert inter_row["empirical"] == emp_expected
    # The single-key path agrees with the list, empirical included.
    assert recommendation_for_key(INTER, SITE, AS_OF) == inter_row
    assert recommendation_for_key(SMOOTH, SITE, AS_OF) == smooth_row

    # --- The lead-time fallback and an unusable std_days ----------------
    # A pooled lead time whose mean_days is usable but whose std_days is
    # not (non-finite or negative) is unusable in the formula; the
    # master-lead-time fallback stands in for it exactly as it does for
    # an unusable mean_days: the selection passes the item-site's master
    # lead time, recommend takes it (LT 14, sd_LT 0, the "master lead
    # time used" note) and the empirical follows the same lead time.
    # The database cannot produce it (lead_time_days is a non-negative
    # whole number of days), so the pure selection is proven directly.
    unusable_std = {
        "mean_days": 10.0,
        "std_days": float("inf"),
        "n_used": 5,
        "low_confidence": False,
    }
    master_days, selected_days = reorder._lead_time_selection(
        unusable_std, {"master_lead_time_days": 14.0}
    )
    assert (master_days, selected_days) == (14.0, 14.0)
    result = reorder.recommend(
        {
            "mean_daily": 2.0,
            "std_daily": 1.0,
            "demand_class": "smooth",
            "recommendable": True,
        },
        unusable_std,
        master_lead_time_days=master_days,
    )
    assert result["status"] == "ok"
    assert result["inputs"]["lt"] == 14.0
    assert result["inputs"]["sd_lt"] == 0.0
    assert "master lead time used" in result["notes"]
    emp_master = reorder._empirical(
        {
            "demand_class": "intermittent",
            "daily_totals": INTER_DAILY,
            "daily_window_start": INTER_WINDOW_START,
        },
        unusable_std,
        master_days,
        AS_OF,
        SERVICE_LEVEL,
    )
    assert emp_master == empirical_reorder_point(
        INTER_DAILY, INTER_WINDOW_START, AS_OF, 14.0, SERVICE_LEVEL
    )
    # Without a usable master lead time there is no usable lead time at
    # all: no fallback, the empirical stays null.
    assert reorder._lead_time_selection(
        unusable_std, {"master_lead_time_days": None}
    ) == (None, None)
    assert (
        reorder._empirical(
            {
                "demand_class": "intermittent",
                "daily_totals": INTER_DAILY,
                "daily_window_start": INTER_WINDOW_START,
            },
            unusable_std,
            None,
            AS_OF,
            SERVICE_LEVEL,
        )
        is None
    )

    # --- AC-3: the API carries the field, by-key equals the list --------
    response = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert response.status_code == 200, response.text
    api_rows = {(row["item_code"], row["site_code"]): row for row in response.json()}
    assert set(api_rows) == {(INTER, SITE), (SMOOTH, SITE)}
    for row in api_rows.values():
        assert "empirical" in row
    assert api_rows[(SMOOTH, SITE)]["empirical"] is None
    assert api_rows[(INTER, SITE)]["empirical"] == emp_expected
    by_key = client.get(
        "/api/recommendations/by-key",
        params={"item_code": INTER, "site_code": SITE, "as_of": AS_OF_STR},
    )
    assert by_key.status_code == 200, by_key.text
    # The same empirical object the list returned for the pair.
    assert by_key.json()["empirical"] == api_rows[(INTER, SITE)]["empirical"]
    assert by_key.json() == api_rows[(INTER, SITE)]

    # --- AC-2/AC-4: the bulk statement count stays fixed ----------------
    # The same fixed budget with 2 item-sites as with 6 (test 453 pins
    # the exact count and checks it itself, unchanged).
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for item in EXTRA:
                _demand(cur, item, FIRST_DAY, 360, 2.0)
                _lead_times(cur, item)
                _item_site(cur, item, 25.0, 10.0)
        conn.commit()
    finally:
        conn.close()
    rows6, count6, _ = _measure(lambda: recommendations_for_all(AS_OF))
    assert len(rows6) == 6
    assert count6 == count2 == FIXED_STATEMENTS
