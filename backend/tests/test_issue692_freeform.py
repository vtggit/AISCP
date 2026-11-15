"""Issue #692 proof: the empirical recommendations switch.

AC-1 — ``replenishment.intermittent_recommendations`` is a registered
system boolean setting defaulting to false, listed by
``GET /api/settings``.  With it on (stored through
``PUT /api/settings/replenishment.intermittent_recommendations`` by an
admin), ``recommendations_for_all`` and ``recommendation_for_key``
recommend every intermittent or lumpy item-site whose ``empirical`` is
not null on it: status ``"ok"``, the empirical ``safety_stock`` /
``reorder_point`` and their whole-unit fields, ``method``
``app.recommend.empirical.METHOD`` and its arithmetic, the note
``"empirical lead-time demand (intermittent)"``, ``inputs`` the
history-basis values the formula recommendation would (``d`` and
``sd_d`` from the demand statistics, ``lt`` and ``sd_lt`` from the
selected lead time, ``z`` of the service level, ``service_level``),
``demand_basis`` ``"history"`` and ``other_basis`` null, and
``delta_reorder_point``, ``suggested_order_quantity`` and
``order_quantity`` computed from these values as for any "ok"
recommendation — the result's ``empirical`` stays present.  The
settings are read once per call, from the read the call already makes:
counted by wrapping the cursor's ``execute`` (the way test 365 counts),
the call executes the same number of SQL statements with the switch on
as off (the fixed budget of 9).  With it off (the default) every
result is identical to before this change — the intermittent item-site
reads ``not_recommendable_demand`` with a null ``reorder_point`` — and
every other item-site (other demand classes, and intermittent or lumpy
item-sites with a null ``empirical``) is unchanged either way.

AC-2 — against the test database (as-of 2026-09-30, date.today pinned
to 2026-10-02, service level 0.95, z 1.644854) with one smooth
item-site (2.0 a day for 360 days, current 25.0/10.0) and one
intermittent item-site (demand in 3 of its 12 buckets — 60 units in
each — and a usable pooled lead time of mean 10, std 2, n_used 3;
current 9.0/1.0):

  * off (the default): both recommendations equal the pre-change
    results — the smooth one ``"ok"`` with its formula values and
    ``empirical`` null, the intermittent one
    ``not_recommendable_demand`` with a null ``reorder_point`` and its
    ``empirical`` — and the single-key path agrees with the list;
  * on (stored through the admin PUT): the intermittent item-site
    reads ``"ok"`` with the empirical values (its
    ``reorder_point_units`` and ``safety_stock_units`` equal its
    ``empirical`` ones, its ``method`` and ``arithmetic`` are the
    empirical ones, ``delta_reorder_point`` is its reorder point minus
    the ERP's current one), the smooth item-site is unchanged, and
    ``GET /api/recommendations/by-key`` returns the same values;
  * the listing: ``GET /api/settings`` lists the new key with default
    false (and the stored value once it is set).
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
SITE = "S1"
SMOOTH = "S692"
INTER = "I692"
KEY = "replenishment.intermittent_recommendations"
NOTE = "empirical lead-time demand (intermittent)"
Z95 = statistics.NormalDist().inv_cdf(0.95)  # 1.644854
SERVICE_LEVEL = 0.95
# The pooled lead time of the 8/10/12-day observations both items
# carry: mean 10 (the lead time the formula and the empirical method
# use), sample std 2, n_used 3 -> not low confidence.
LEAD_TIME_DAYS = 10.0
SD_LEAD_TIME = 2.0

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
# 1 counted overrides — the same with the switch on as off.
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
                f"id-de-692-{item}-{i}",
                f"de-692-{item}-{i}",
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
                f"id-de-692-{item}-{day.isoformat()}",
                f"de-692-{item}-{day.isoformat()}",
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
                f"id-lt-692-{item}-{i}",
                f"lt-692-{item}-{i}",
                f"SUP-692-{item}",
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
        (f"id-is-692-{item}", item, SITE, reorder_point, safety_stock),
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
    """The smooth item-site's whole recommendation as it was before
    this change (d 2.0, sd_d 0.0, LT 10, sd_LT 2, z Z95, service level
    0.95, unit cost 1.0, current 25.0/10.0, no position rows), with
    ``empirical`` null."""
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
    }


def _intermittent_off_expected(emp_expected: dict) -> dict:
    """The intermittent item-site's whole recommendation as it was
    before this change: ``not_recommendable_demand`` with a null
    ``reorder_point`` and its ``empirical``, the unit cost 1.0 and
    the current 9.0/1.0, no position rows."""
    return {
        "item_code": INTER,
        "site_code": SITE,
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


def _intermittent_on_expected(emp_expected: dict, inter_stats: dict) -> dict:
    """The intermittent item-site's whole recommendation with the
    switch on: recommended on its empirical lead-time demand, with
    ``inputs`` the history-basis values the formula recommendation
    would carry and the position fields computed from these values as
    for any "ok" recommendation."""
    order_quantity = recommend_order_quantity(
        inter_stats["mean_daily"], 1.0, 50.0, 0.25
    )
    return {
        "item_code": INTER,
        "site_code": SITE,
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
    }


def test_issue692_freeform(client, admin_token, monkeypatch):
    from app.main import app

    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    admin_client = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})

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

    # The empirical reorder point both item-sites' results carry: the
    # 95th percentile of the 351 overlapping 10-day span totals, 35 of
    # them positive, hence positive.
    emp_expected = empirical_reorder_point(
        INTER_DAILY, INTER_WINDOW_START, AS_OF, LEAD_TIME_DAYS, SERVICE_LEVEL
    )
    assert emp_expected is not None
    assert emp_expected["window_days"] == 10
    assert emp_expected["spans"] == INTER_SPANS
    assert emp_expected["spans_with_demand"] == INTER_SPANS_WITH_DEMAND
    assert emp_expected["service_level"] == SERVICE_LEVEL
    assert emp_expected["reorder_point"] > 0.0

    # --- AC-1: the listing — the new key, default false ------------------
    resp = client.get("/api/settings")
    assert resp.status_code == 200, resp.text
    entry = next(e for e in resp.json() if e["key"] == KEY)
    assert entry["scope"] == "system"
    assert entry["type"] == "boolean"
    assert entry["default"] is False
    assert entry["value"] is False
    assert entry["source"] == "default"
    assert entry["editable"] is False, "a non-admin cannot edit a system setting"
    assert entry["description"] == (
        "Recommend intermittent and lumpy item-sites on their "
        "empirical lead-time demand (off: shown for review only)."
    )

    # --- AC-2: off (the default) — the pre-change results ----------------
    rows = recommendations_for_all(AS_OF)
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (INTER, SITE),
        (SMOOTH, SITE),
    ]
    inter_row, smooth_row = rows
    assert inter_row == _intermittent_off_expected(emp_expected)
    assert inter_row["status"] == "not_recommendable_demand"
    assert inter_row["reorder_point"] is None
    assert smooth_row == _smooth_expected()
    # The single-key path agrees with the list.
    assert recommendation_for_key(INTER, SITE, AS_OF) == inter_row
    assert recommendation_for_key(SMOOTH, SITE, AS_OF) == smooth_row

    # The API list agrees too (the shape the screen shows).
    api_off = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert api_off.status_code == 200, api_off.text
    api_rows_off = {(row["item_code"], row["site_code"]): row for row in api_off.json()}
    assert set(api_rows_off) == {(INTER, SITE), (SMOOTH, SITE)}

    # The fixed statement budget, counted the way test 365 counts.
    measured_off, count_off, _ = _measure(lambda: recommendations_for_all(AS_OF))
    assert len(measured_off) == 2
    assert count_off == FIXED_STATEMENTS

    # --- AC-1/AC-2: on — stored through the admin PUT ---------------------
    put = admin_client.put(f"/api/settings/{KEY}", json={"value": True})
    assert put.status_code == 200, put.text
    assert put.json()["value"] is True
    assert put.json()["source"] == "system"
    listing = next(
        e for e in admin_client.get("/api/settings").json() if e["key"] == KEY
    )
    assert listing["value"] is True
    assert listing["default"] is False
    assert listing["source"] == "system"
    assert listing["editable"] is True, "the admin may edit a system setting"

    rows_on = recommendations_for_all(AS_OF)
    inter_on, smooth_on = rows_on
    # The intermittent item-site is now recommended on its empirical
    # lead-time demand, and every other field follows from it.
    assert inter_on == _intermittent_on_expected(
        emp_expected, demand_stats_for_pair(INTER, SITE, AS_OF)
    )
    assert inter_on["status"] == "ok"
    assert inter_on["reorder_point_units"] == emp_expected["reorder_point_units"]
    assert inter_on["safety_stock_units"] == emp_expected["safety_stock_units"]
    assert inter_on["method"] == EMPIRICAL_METHOD
    assert inter_on["arithmetic"] == emp_expected["arithmetic"]
    assert inter_on["delta_reorder_point"] == 9.0 - emp_expected["reorder_point"]
    # The smooth item-site is unchanged either way.
    assert smooth_on == smooth_row
    # The single-key path agrees with the list.
    assert recommendation_for_key(INTER, SITE, AS_OF) == inter_on
    assert recommendation_for_key(SMOOTH, SITE, AS_OF) == smooth_on

    # The API: by-key returns the same values, and the smooth row the
    # screen shows is the pre-switch one.
    api_on = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert api_on.status_code == 200, api_on.text
    api_rows_on = {(row["item_code"], row["site_code"]): row for row in api_on.json()}
    assert api_rows_on[(SMOOTH, SITE)] == api_rows_off[(SMOOTH, SITE)]
    assert api_rows_on[(INTER, SITE)]["status"] == "ok"
    by_key = client.get(
        "/api/recommendations/by-key",
        params={"item_code": INTER, "site_code": SITE, "as_of": AS_OF_STR},
    )
    assert by_key.status_code == 200, by_key.text
    assert by_key.json() == api_rows_on[(INTER, SITE)]

    # The fixed statement budget holds with the switch on too.
    measured_on, count_on, _ = _measure(lambda: recommendations_for_all(AS_OF))
    assert len(measured_on) == 2
    assert count_on == count_off
    assert count_on == FIXED_STATEMENTS
