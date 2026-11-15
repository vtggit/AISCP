"""Issue #708 proof: per item-site empirical adoptions.

An empirical adoption (``PUT /api/empirical-adoptions``, admin only,
one stored row per item-site) makes the adopted item-site act like
the ``replenishment.intermittent_recommendations`` switch, for that
item-site only: an item-site with a row in ``empirical_adoptions``, a
``demand_class`` of ``"intermittent"`` or ``"lumpy"`` and a non-null
``empirical`` is recommended on it — status ``"ok"``, the empirical
reorder point and safety stock, the empirical method and arithmetic
and the ``"empirical lead-time demand (intermittent)"`` note — with
the switch off as well as on.  Every recommendation result gains
``empirical_adopted`` (true for an item-site with a row, false
otherwise, the API answering false for a result that carries no
key) and each row of
``GET /api/recommendations/intermittent-impact`` carries ``adopted``
with the same value — omitted, never masked as false, when the
call did not read the stored adoptions.

Against the test database (as-of 2026-09-30, date.today pinned to
2026-10-02, service level 0.95, z 1.644854) with two intermittent
item-sites — one item-site each of two items, demand in 3 of its 12
buckets — 60 units in each — and a usable pooled lead time of mean
10, std 2, n_used 3; current 9.0/1.0 — and one smooth item-site
(2.0 a day for 360 days, current 25.0/10.0), all at one site (the
single-site population, seeded the way the #692 proof seeds its
smooth and intermittent item-sites):

  * with an adoption stored for the first intermittent item-site only
    (``PUT /api/empirical-adoptions`` as an admin) and the switch off
    (the default): the adopted item-site reads ``"ok"`` with its
    ``reorder_point_units`` equal to its ``empirical`` ones and
    ``empirical_adopted`` true; the other intermittent item-site reads
    ``not_recommendable_demand`` with ``empirical_adopted`` false;
    the smooth item-site is unchanged with ``empirical_adopted``
    false; the single-key path agrees with the list and the impact
    rows carry ``adopted`` true and false accordingly;
  * with the switch on (stored through the admin PUT): both
    intermittent item-sites read ``"ok"`` and ``empirical_adopted``
    is true only for the adopted one;
  * withdrawn (``DELETE /api/empirical-adoptions``) with the switch
    off: the first intermittent item-site reads
    ``not_recommendable_demand`` again;
  * an impact row of a population the call did not read the stored
    adoptions for (one intermittent item-site at one site, the
    shape the existing statement budgets pin) carries no
    ``adopted`` key at all — a value the call did not read is not
    masked as false — while the same result's list row answers
    ``empirical_adopted`` false;
  * the fixed statement budget, counted the way test 365 counts
    (wrapping the cursor's ``execute``), is the same with 2
    item-sites as with 6 (the fixed budget of 10).
"""

import statistics
from datetime import date, timedelta

import psycopg2
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.recommend.empirical import METHOD as EMPIRICAL_METHOD
from app.recommend.empirical import empirical_reorder_point
from app.recommend.order_quantity import recommend_order_quantity
from app.recommend.reorder import (
    METHOD,
    recommendation_for_key,
    recommendations_for_all,
)
from app.stats.demand import demand_stats_for_pair

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
S1 = "S1"
INTER1 = "I708-1"
INTER2 = "I708-2"
SMOOTH = "S708"
M1 = "M708-1"
M2 = "M708-2"
M3 = "M708-3"
SOLO = "I708-solo"
KEY = "replenishment.intermittent_recommendations"
NOTE = "empirical lead-time demand (intermittent)"
Z95 = statistics.NormalDist().inv_cdf(0.95)  # 1.644854
SERVICE_LEVEL = 0.95
# The pooled lead time of the 8/10/12-day observations every item
# carries: mean 10 (the lead time the formula and the empirical
# method use), sample std 2, n_used 3 -> not low confidence.
LEAD_TIME_DAYS = 10.0
SD_LEAD_TIME = 2.0

# The intermittent item's daily demand at each of its sites: 60
# units on the first day (the pair's first-seen day, in bucket 11),
# 4 units a day for 15 days in bucket 0 and 6 units a day for 10
# days in bucket 1 — 3 of its 12 buckets non-zero, each totalling 60
# (ADI 4, cv2 0 -> intermittent), 35 of the 351 overlapping 10-day
# span totals positive, so the 95th percentile of the spans is
# positive.
INTER_DAILY: dict[date, float] = {AS_OF - timedelta(days=359): 60.0}
for _OFFSET in range(15):
    INTER_DAILY[AS_OF - timedelta(days=_OFFSET)] = 4.0
for _OFFSET in range(30, 40):
    INTER_DAILY[AS_OF - timedelta(days=_OFFSET)] = 6.0
# The pairs' windows start on the 360-day window's start: first_seen
# (the first day) is not later than it.
INTER_WINDOW_START = FIRST_DAY
INTER_SPANS = 351  # 360 days - 10 + 1
INTER_SPANS_WITH_DEMAND = 35

# The fixed budget of recommendations_for_all for an explicit as_of:
# 2 demand_stats_for_all + 1 lead_time_stats_for_all + 1 item_sites +
# 1 on_hand + 1 open_purchase_lines + 1 settings + 1 history_for_all +
# 1 counted overrides + 1 empirical adoptions — the adoptions read
# once for the call, whatever the number of item-sites.
FIXED_STATEMENTS = 11


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


def _demand_events(cur, item, site, daily: dict[date, float]):
    """One demand event per day of ``daily`` for one item at one site."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-708-{item}-{site}-{day.isoformat()}",
                f"de-708-{item}-{site}-{day.isoformat()}",
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


def _demand(cur, item, site, first, days, quantity):
    """``days`` of ``quantity`` units a day for one item at one site,
    from ``first``."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-708-{item}-{site}-{i}",
                f"de-708-{item}-{site}-{i}",
                item,
                site,
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
                f"id-lt-708-{item}-{i}",
                f"lt-708-{item}-{i}",
                f"SUP-708-{item}",
                item,
                ordered,
                received,
                "test",
            ),
        )


def _item_site(cur, item, site, reorder_point, safety_stock):
    """The item-site's current planning settings."""
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, source_system) "
        "VALUES (%s, %s, %s, 1.0, %s, %s, 'test')",
        (f"id-is-708-{item}-{site}", item, site, reorder_point, safety_stock),
    )


def _basis_arithmetic(d: float, sd_d: float, lt: float, sd_lt: float) -> str:
    """The exact arithmetic string the formula applies to the inputs."""
    safety_stock = Z95 * (lt * sd_d * sd_d + d * d * sd_lt * sd_lt) ** 0.5
    reorder_point = d * lt + safety_stock
    return (
        "safety_stock = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2) "
        f"= {Z95:.6f} * sqrt({lt:g} * {sd_d:g}^2 + {d:g}^2 * {sd_lt:g}^2) "
        f"= {safety_stock:.2f}; "
        "reorder_point = d * LT + safety_stock "
        f"= {d:g} * {lt:g} + {safety_stock:.2f} = {reorder_point:.2f}"
    )


def _smooth_expected() -> dict:
    """The smooth item-site's whole recommendation: ``"ok"`` with its
    formula values (d 2.0, sd_d 0.0, LT 10, sd_LT 2, z Z95, service
    level 0.95, unit cost 1.0, current 25.0/10.0, no position rows),
    ``empirical`` null and ``empirical_adopted`` false."""
    safety_stock = Z95 * 4.0
    reorder_point = 20.0 + safety_stock
    order_quantity = recommend_order_quantity(2.0, 1.0, 50.0, 0.25)
    return {
        "item_code": SMOOTH,
        "site_code": S1,
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
            "sd_lt": SD_LEAD_TIME,
            "z": Z95,
            "service_level": SERVICE_LEVEL,
        },
        "arithmetic": _basis_arithmetic(2.0, 0.0, LEAD_TIME_DAYS, SD_LEAD_TIME),
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
                "sd_lt": SD_LEAD_TIME,
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
        "empirical_adopted": False,
    }


def _intermittent_off_expected(
    item: str, emp_expected: dict, adopted: bool | None = None
) -> dict:
    """An intermittent item-site the way the switch-off result reads
    without an adoption: ``not_recommendable_demand`` with a null
    ``reorder_point`` and its ``empirical``, the unit cost 1.0 and
    the current 9.0/1.0, no position rows.  ``empirical_adopted`` is
    carried when the call read the stored adoptions (``adopted``);
    the key is absent when the call skipped the read (``adopted``
    None)."""
    result = {
        "item_code": item,
        "site_code": S1,
        "as_of": AS_OF,
        "status": "not_recommendable_demand",
        "demand_class": "intermittent",
        "safety_stock": None,
        "reorder_point": None,
        "signal_units": 0.0,
        "reorder_point_before_signals_units": None,
        "method": METHOD,
        "notes": [],
        "demand_basis": "history",
        "other_basis": None,
        "service_level_source": {"abc_class": None, "source": "single"},
        "current": {"reorder_point": 9.0, "safety_stock": 1.0},
        "delta_reorder_point": None,
        "on_hand_units": 0,
        "on_order_units": 0,
        "unit_cost": 1.0,
        "source_system": "test",
        "suggested_order_quantity": None,
        "order_quantity": None,
        "empirical": emp_expected,
    }
    if adopted is not None:
        result["empirical_adopted"] = adopted
    return result


def _empirical_recommended(
    item: str, emp_expected: dict, inter_stats: dict, adopted: bool
) -> dict:
    """The intermittent item-site's whole recommendation recommended
    on its empirical lead-time demand — the switch on, or the stored
    adoption with the switch off: ``"ok"`` with the empirical values,
    ``inputs`` the history-basis values the formula recommendation
    would carry, the position fields computed from these values as
    for any "ok" recommendation and ``empirical_adopted``
    ``adopted``."""
    order_quantity = recommend_order_quantity(
        inter_stats["mean_daily"], 1.0, 50.0, 0.25
    )
    return {
        "item_code": item,
        "site_code": S1,
        "as_of": AS_OF,
        "status": "ok",
        "demand_class": "intermittent",
        "safety_stock": emp_expected["safety_stock"],
        "reorder_point": emp_expected["reorder_point"],
        "safety_stock_units": emp_expected["safety_stock_units"],
        "reorder_point_units": emp_expected["reorder_point_units"],
        "signal_units": 0.0,
        "reorder_point_before_signals_units": emp_expected["reorder_point_units"],
        "inputs": {
            "d": inter_stats["mean_daily"],
            "sd_d": inter_stats["std_daily"],
            "lt": LEAD_TIME_DAYS,
            "sd_lt": SD_LEAD_TIME,
            "z": Z95,
            "service_level": SERVICE_LEVEL,
        },
        "arithmetic": emp_expected["arithmetic"],
        "method": EMPIRICAL_METHOD,
        "notes": [NOTE],
        "demand_basis": "history",
        "other_basis": None,
        "service_level_source": {"abc_class": None, "source": "single"},
        "current": {"reorder_point": 9.0, "safety_stock": 1.0},
        "delta_reorder_point": 9.0 - emp_expected["reorder_point"],
        "on_hand_units": 0,
        "on_order_units": 0,
        "unit_cost": 1.0,
        "source_system": "test",
        "suggested_order_quantity": emp_expected["reorder_point_units"],
        "order_quantity": {
            **order_quantity,
            "recommended_max_units": emp_expected["reorder_point_units"]
            + order_quantity["order_quantity_units"],
            "current_order_qty": None,
            "current_max_qty": None,
        },
        "empirical": emp_expected,
        "empirical_adopted": adopted,
    }


def test_issue708_freeform(client, admin_token, monkeypatch):
    from app.main import app

    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})

    # --- one intermittent item-site alone: the read stays skipped -----
    # A population of one item-site at one site (no item at two sites,
    # no second intermittent item-site) keeps the fixed statement
    # budget the existing proofs lock in: the stored adoptions are not
    # read, the result carries no ``empirical_adopted`` key, and the
    # impact row carries no ``adopted`` key either — a value the call
    # did not read is not masked as false.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand_events(cur, SOLO, S1, INTER_DAILY)
            _lead_times(cur, SOLO)
            _item_site(cur, SOLO, S1, 9.0, 1.0)
        conn.commit()
    finally:
        conn.close()

    emp_expected = empirical_reorder_point(
        INTER_DAILY, INTER_WINDOW_START, AS_OF, LEAD_TIME_DAYS, SERVICE_LEVEL
    )
    assert emp_expected is not None
    assert emp_expected["window_days"] == 10
    assert emp_expected["spans"] == INTER_SPANS
    assert emp_expected["spans_with_demand"] == INTER_SPANS_WITH_DEMAND
    assert emp_expected["service_level"] == SERVICE_LEVEL
    assert emp_expected["reorder_point"] > 0.0
    emp_units = emp_expected["reorder_point_units"]

    solo_rows = recommendations_for_all(AS_OF)
    assert [(row["item_code"], row["site_code"]) for row in solo_rows] == [
        (SOLO, S1),
    ]
    solo_row = solo_rows[0]
    assert solo_row == _intermittent_off_expected(SOLO, emp_expected)
    assert solo_row["status"] == "not_recommendable_demand"
    assert "empirical_adopted" not in solo_row
    solo_api = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert solo_api.status_code == 200, solo_api.text
    assert solo_api.json()[0]["empirical_adopted"] is False
    solo_impact = client.get(
        "/api/recommendations/intermittent-impact", params={"as_of": AS_OF_STR}
    )
    assert solo_impact.status_code == 200, solo_impact.text
    solo_body = solo_impact.json()
    assert [
        (row["item_code"], row["site_code"]) for row in solo_body["item_sites"]
    ] == [(SOLO, S1)]
    solo_row_impact = solo_body["item_sites"][0]
    assert solo_row_impact["demand_class"] == "intermittent"
    assert solo_row_impact["unit_cost"] == 1.0
    assert solo_row_impact["current_reorder_point"] == 9.0
    assert solo_row_impact["empirical_reorder_point_units"] == emp_units
    assert solo_row_impact["reorder_point_units_delta"] == emp_units - 9.0
    assert solo_row_impact["dollar_delta"] == emp_units - 9.0
    assert "adopted" not in solo_row_impact
    assert solo_body["summary"] == {
        "item_sites": 1,
        "current_value": 9.0,
        "empirical_value": float(emp_units),
        "dollar_delta": float(emp_units) - 9.0,
        "item_sites_without_unit_cost": 0,
        "switch_on": False,
    }

    # --- seed the two intermittent item-sites (two items, one site) ---
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM demand_events WHERE item_code = %s", (SOLO,))
            _demand_events(cur, INTER1, S1, INTER_DAILY)
            _demand_events(cur, INTER2, S1, INTER_DAILY)
            _lead_times(cur, INTER1)
            _lead_times(cur, INTER2)
            _item_site(cur, INTER1, S1, 9.0, 1.0)
            _item_site(cur, INTER2, S1, 9.0, 1.0)
        conn.commit()
    finally:
        conn.close()

    # The fixed statement budget: 2 item-sites (both intermittent at
    # one site — a stored adoption can distinguish them, so the call
    # reads the stored adoptions, once).
    measured2, count2, _ = _measure(lambda: recommendations_for_all(AS_OF))
    assert [(row["item_code"], row["site_code"]) for row in measured2] == [
        (INTER1, S1),
        (INTER2, S1),
    ]
    assert count2 == FIXED_STATEMENTS

    # --- store the adoption of the first intermittent item-site -------
    put = admin_client.put(
        "/api/empirical-adoptions",
        json={"item_code": INTER1, "site_code": S1, "note": "adopt the empirical"},
    )
    assert put.status_code == 200, put.text
    assert put.json()["item_code"] == INTER1
    assert put.json()["site_code"] == S1

    # --- switch off (the default): the adoption stands alone ----------
    rows = recommendations_for_all(AS_OF)
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (INTER1, S1),
        (INTER2, S1),
    ]
    row_1, row_2 = rows
    stats_1 = demand_stats_for_pair(INTER1, S1, AS_OF)
    stats_2 = demand_stats_for_pair(INTER2, S1, AS_OF)
    # The adopted item-site is recommended exactly as the switch on
    # would recommend it, with the switch off — at one site, in a
    # population every item of which demands at that one site.
    assert row_1 == _empirical_recommended(INTER1, emp_expected, stats_1, True)
    assert row_1["status"] == "ok"
    assert row_1["reorder_point_units"] == row_1["empirical"]["reorder_point_units"]
    assert row_1["empirical_adopted"] is True
    assert row_1["method"] == EMPIRICAL_METHOD
    assert row_1["arithmetic"] == emp_expected["arithmetic"]
    assert NOTE in row_1["notes"]
    # The other intermittent item-site reads exactly as today.
    assert row_2 == _intermittent_off_expected(INTER2, emp_expected, False)
    assert row_2["status"] == "not_recommendable_demand"
    assert row_2["reorder_point"] is None
    assert row_2["empirical_adopted"] is False
    # The single-key path agrees with the list.
    assert recommendation_for_key(INTER1, S1, AS_OF) == row_1
    assert recommendation_for_key(INTER2, S1, AS_OF) == row_2

    # The API: every result carries empirical_adopted and by-key
    # agrees with the list.
    api_off = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert api_off.status_code == 200, api_off.text
    api_rows_off = {(row["item_code"], row["site_code"]): row for row in api_off.json()}
    assert api_rows_off[(INTER1, S1)]["empirical_adopted"] is True
    assert api_rows_off[(INTER1, S1)]["status"] == "ok"
    assert api_rows_off[(INTER1, S1)]["reorder_point_units"] == emp_units
    assert api_rows_off[(INTER2, S1)]["empirical_adopted"] is False
    assert api_rows_off[(INTER2, S1)]["status"] == "not_recommendable_demand"
    by_key_1 = client.get(
        "/api/recommendations/by-key",
        params={"item_code": INTER1, "site_code": S1, "as_of": AS_OF_STR},
    )
    assert by_key_1.status_code == 200, by_key_1.text
    assert by_key_1.json() == api_rows_off[(INTER1, S1)]
    by_key_2 = client.get(
        "/api/recommendations/by-key",
        params={"item_code": INTER2, "site_code": S1, "as_of": AS_OF_STR},
    )
    assert by_key_2.status_code == 200, by_key_2.text
    assert by_key_2.json() == api_rows_off[(INTER2, S1)]

    # The impact rows carry adopted true and false accordingly (the
    # rows describe the switch, served with it off as on).
    impact = client.get(
        "/api/recommendations/intermittent-impact", params={"as_of": AS_OF_STR}
    )
    assert impact.status_code == 200, impact.text
    body = impact.json()
    assert [(row["item_code"], row["site_code"]) for row in body["item_sites"]] == [
        (INTER1, S1),
        (INTER2, S1),
    ]
    for row in body["item_sites"]:
        assert row["demand_class"] == "intermittent"
        assert row["unit_cost"] == 1.0
        assert row["current_reorder_point"] == 9.0
        assert row["empirical_reorder_point_units"] == emp_units
        assert row["reorder_point_units_delta"] == emp_units - 9.0
        assert row["dollar_delta"] == emp_units - 9.0
    assert body["item_sites"][0]["adopted"] is True
    assert body["item_sites"][1]["adopted"] is False
    assert body["summary"] == {
        "item_sites": 2,
        "current_value": 18.0,
        "empirical_value": float(2 * emp_units),
        "dollar_delta": float(2 * emp_units) - 18.0,
        "item_sites_without_unit_cost": 0,
        "switch_on": False,
    }

    # --- the fixed statement budget: the same with 6 item-sites -------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand(cur, SMOOTH, S1, FIRST_DAY, 360, 2.0)
            _lead_times(cur, SMOOTH)
            _item_site(cur, SMOOTH, S1, 25.0, 10.0)
            for item in (M1, M2, M3):
                _demand(cur, item, S1, FIRST_DAY, 360, 1.0)
                _lead_times(cur, item)
        conn.commit()
    finally:
        conn.close()

    measured6, count6, _ = _measure(lambda: recommendations_for_all(AS_OF))
    assert [(row["item_code"], row["site_code"]) for row in measured6] == [
        (INTER1, S1),
        (INTER2, S1),
        (M1, S1),
        (M2, S1),
        (M3, S1),
        (SMOOTH, S1),
    ]
    assert count6 == count2 == FIXED_STATEMENTS

    rows6 = recommendations_for_all(AS_OF)
    by_key_rows = {(row["item_code"], row["site_code"]): row for row in rows6}
    # The smooth item-site is unchanged, with empirical_adopted false.
    assert by_key_rows[(SMOOTH, S1)] == _smooth_expected()
    # The other smooth item-sites are recommendable, not adopted.
    for item in (M1, M2, M3):
        assert by_key_rows[(item, S1)]["status"] == "ok"
        assert by_key_rows[(item, S1)]["demand_class"] == "smooth"
        assert by_key_rows[(item, S1)]["empirical"] is None
        assert by_key_rows[(item, S1)]["empirical_adopted"] is False
    # The two intermittent item-sites read as with 2 item-sites.
    assert by_key_rows[(INTER1, S1)] == row_1
    assert by_key_rows[(INTER2, S1)] == row_2

    # --- switch on: both intermittent item-sites are recommended ------
    put = admin_client.put(f"/api/settings/{KEY}", json={"value": True})
    assert put.status_code == 200, put.text
    rows_on = recommendations_for_all(AS_OF)
    on_by_key = {(row["item_code"], row["site_code"]): row for row in rows_on}
    assert on_by_key[(INTER1, S1)]["status"] == "ok"
    assert on_by_key[(INTER2, S1)]["status"] == "ok"
    assert on_by_key[(INTER1, S1)] == _empirical_recommended(
        INTER1, emp_expected, stats_1, True
    )
    assert on_by_key[(INTER2, S1)] == _empirical_recommended(
        INTER2, emp_expected, stats_2, False
    )
    assert on_by_key[(INTER1, S1)]["empirical_adopted"] is True
    assert on_by_key[(INTER2, S1)]["empirical_adopted"] is False
    assert on_by_key[(INTER2, S1)]["reorder_point_units"] == emp_units
    # The smooth item-sites are unchanged either way.
    assert on_by_key[(SMOOTH, S1)] == by_key_rows[(SMOOTH, S1)]
    for item in (M1, M2, M3):
        assert on_by_key[(item, S1)] == by_key_rows[(item, S1)]
    # The single-key path agrees with the list.
    assert recommendation_for_key(INTER1, S1, AS_OF) == on_by_key[(INTER1, S1)]
    assert recommendation_for_key(INTER2, S1, AS_OF) == on_by_key[(INTER2, S1)]

    # The API by-key agrees with the list, and the impact rows keep
    # their adopted flags with the switch on.
    api_on = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert api_on.status_code == 200, api_on.text
    api_rows_on = {(row["item_code"], row["site_code"]): row for row in api_on.json()}
    assert api_rows_on[(INTER1, S1)]["empirical_adopted"] is True
    assert api_rows_on[(INTER2, S1)]["empirical_adopted"] is False
    by_key_on = client.get(
        "/api/recommendations/by-key",
        params={"item_code": INTER1, "site_code": S1, "as_of": AS_OF_STR},
    )
    assert by_key_on.status_code == 200, by_key_on.text
    assert by_key_on.json() == api_rows_on[(INTER1, S1)]
    by_key_on_2 = client.get(
        "/api/recommendations/by-key",
        params={"item_code": INTER2, "site_code": S1, "as_of": AS_OF_STR},
    )
    assert by_key_on_2.status_code == 200, by_key_on_2.text
    assert by_key_on_2.json() == api_rows_on[(INTER2, S1)]
    impact_on = client.get(
        "/api/recommendations/intermittent-impact", params={"as_of": AS_OF_STR}
    )
    assert impact_on.status_code == 200, impact_on.text
    assert impact_on.json()["item_sites"][0]["adopted"] is True
    assert impact_on.json()["item_sites"][1]["adopted"] is False
    assert impact_on.json()["summary"]["switch_on"] is True

    # --- withdrawn: the first item-site reads as without the adoption -
    off = admin_client.put(f"/api/settings/{KEY}", json={"value": False})
    assert off.status_code == 200, off.text
    delete = admin_client.delete(
        "/api/empirical-adoptions",
        params={"item_code": INTER1, "site_code": S1},
    )
    assert delete.status_code == 204, delete.text
    rows_withdrawn = recommendations_for_all(AS_OF)
    withdrawn = {(row["item_code"], row["site_code"]): row for row in rows_withdrawn}
    assert withdrawn[(INTER1, S1)] == _intermittent_off_expected(
        INTER1, emp_expected, False
    )
    assert withdrawn[(INTER1, S1)]["status"] == "not_recommendable_demand"
    assert withdrawn[(INTER1, S1)]["reorder_point"] is None
    assert withdrawn[(INTER1, S1)]["empirical_adopted"] is False
    assert withdrawn[(INTER2, S1)] == _intermittent_off_expected(
        INTER2, emp_expected, False
    )
    assert recommendation_for_key(INTER1, S1, AS_OF) == withdrawn[(INTER1, S1)]
    impact_withdrawn = client.get(
        "/api/recommendations/intermittent-impact", params={"as_of": AS_OF_STR}
    )
    assert impact_withdrawn.status_code == 200, impact_withdrawn.text
    assert [row["adopted"] for row in impact_withdrawn.json()["item_sites"]] == [
        False,
        False,
    ]
