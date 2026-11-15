"""Issue #970 proof: the reorder point counts the demand signals.

Against the test database (as-of 2026-09-30, date.today pinned to
2026-10-02), with item-sites seeded the way
``tests/test_issue365_freeform.py`` seeds them and demand signals
stored through ``POST /api/demand-signals``:

* K970 — 2.0 a day for 360 days (smooth), lead times 8/10/12 (mean
  10, sample std 2), unit cost 1.0, current 25.0/10.0: the formula's
  values at the seeded inputs are safety stock ``Z95 * 4`` and reorder
  point ``2 * 10 + Z95 * 4`` (whole units 7 and 27);
* I970 and I970B — the intermittent item of
  ``tests/test_issue692_freeform.py`` (60 units on the first day, 4 a
  day for the last 15 days, 6 a day for the 10 days ending 30 days
  before the as-of; 35 of the 351 overlapping 10-day span totals
  positive, the 334th smallest of the 351 spans — ``k =
  ceil(0.95 * 351)`` — the 18th positive total, 32.0); I970 carries a
  stored empirical adoption (the two intermittent item-sites make the
  stored adoptions relevant to the call).

Every expected number below is written out as arithmetic on the
seeded inputs, never computed by calling the code under test.

* History basis: with the signal of 300 units over the 30-day window
  starting the day after the as-of, ``signal_units`` is 100 (10.0 a
  day over all 10 window days) and ``reorder_point`` the formula's
  value plus 100; ``reorder_point_before_signals_units`` the formula's
  ceiling, ``inputs.d`` unchanged, ``safety_stock`` unchanged;
* Forecast basis: the same check with the demand basis set to
  "forecast", and ``other_basis`` (history) carries the 100 as well;
* Outside the lead time: a signal starting after the first 10 days
  gives ``signal_units`` 0 and today's values;
* Partly inside: a 20-day window starting on day 6 counts 5 of its 20
  days (15.0);
* Two signals: two overlapping signals add together (115.0);
* An override wins: an override covering days 1 to 5 removes those
  days' shares from ``signal_units`` (50.0), on both bases — the
  forecast basis' demand itself moves to the override's day shares;
* Withdrawn: after the withdrawals the row reads today's values again;
* The notes: ``arithmetic`` and ``notes`` carry the stated text with a
  signal and their existing text without one;
* Empirical: the adopted intermittent item-site's empirical
  ``reorder_point`` carries plus ``signal_units``;
* Downstream: ``suggested_order_quantity`` reflects the new reorder
  point;
* One read: one ``GET /api/recommendations`` (and one ``by-key``)
  issues the counted-signals query once;
* Every reader agrees: with the history-basis signal stored,
  ``GET /api/recommendations/by-key``, the planning-parameters CSV and
  ``GET /api/supply-plan?horizon_days=7`` all carry the new values,
  each compared with its value from before the signal was stored;
* Scenarios: ``POST /api/scenarios/simulate`` with
  ``demand_multiplier`` 1.5 answers the same deltas and dollar impact
  with and without the signal stored;
* No switch: ``recommendations_for_all`` accepts no argument that
  turns the signals off.
"""

import csv
import inspect
import io
import math
import statistics
from datetime import date, datetime, timedelta

import psycopg2
import pytest
from fastapi.testclient import TestClient

from app.aggregate.projection import aggregate_projection
from app.db.connection import get_connection
from app.main import app
from app.recommend.empirical import METHOD as EMPIRICAL_METHOD
from app.recommend.empirical import empirical_reorder_point
from app.recommend.reorder import METHOD as FORMULA_METHOD
from app.recommend.reorder import recommendations_for_all

AS_OF = date(2026, 9, 30)
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
AS_OF_STR = AS_OF.isoformat()

SITE = "SITE-970"
SMOOTH = "K970"
INTER = "I970"
INTER_B = "I970B"

SERVICE_LEVEL = 0.95
Z95 = statistics.NormalDist().inv_cdf(SERVICE_LEVEL)  # 1.644854

# The seeded lead times of 8, 10 and 12 days: mean 10, sample std 2,
# n_used 3 -> not low confidence.
LT_MEAN = 10.0
LT_SD = 2.0
WINDOW_DAYS = 10  # ceil(LT_MEAN): the window the signals count over

# K970's formula results at the seeded inputs (d 2.0, sd_d 0.0, LT
# 10, sd_LT 2, z of the 0.95 service level).
SS = Z95 * math.sqrt(LT_MEAN * 0.0 * 0.0 + 2.0 * 2.0 * LT_SD * LT_SD)
ROP = 2.0 * LT_MEAN + SS
SS_UNITS = math.ceil(round(SS, 9))  # 7
ROP_UNITS = math.ceil(round(ROP, 9))  # 27
CURRENT_ROP = 25.0

# The stored planning costs (order cost 1.0, holding rate 1.0, unit
# cost 1.0): the EOQ sqrt(2 * 365 * 2 * 1 / (1 * 1)).
EOQ_UNITS = math.ceil(round(math.sqrt(2 * 365 * 2.0 * 1.0 / (1.0 * 1.0)), 9))  # 39

SIGNAL_NOTE = f"includes demand signals over {WINDOW_DAYS} days"
EMPIRICAL_NOTE = "empirical lead-time demand (intermittent)"

# The signal windows, in days after the as-of date.
S_MAIN_START = AS_OF + timedelta(days=1)  # day 1 of the window
S_MAIN_END = AS_OF + timedelta(days=30)  # a 30-day window
S_MAIN_QTY = 300.0  # 10.0 a day
S_MAIN_UNITS = S_MAIN_QTY / 30 * WINDOW_DAYS  # 100.0: every window day
S_PART_START = AS_OF + timedelta(days=6)  # starts on day 6
S_PART_END = AS_OF + timedelta(days=25)  # a 20-day window
S_PART_QTY = 60.0  # 3.0 a day
S_PART_UNITS = S_PART_QTY / 20 * 5  # 15.0: 5 of its 20 days in the window
S_OUT_START = AS_OF + timedelta(days=11)  # starts after day 10
S_OUT_END = AS_OF + timedelta(days=40)
S_OUT_QTY = 300.0  # 0.0: no window day covered

# The override of the override-wins step: the first 5 window days.
OVR_START = AS_OF + timedelta(days=1)
OVR_END = AS_OF + timedelta(days=5)
OVR_QTY = 100.0  # 20.0 a day over its 5-day period
OVR_UNITS = S_MAIN_UNITS - 10.0 * 5  # 50.0: the uncovered days 6-10

# K970's forecast basis once the override covers days 1-5: the bucket's
# effective quantity is the override's 100.0 plus the 25 uncovered days'
# 2.0 shares (50.0) — 150.0 over its 30 days, 5.0 a day for every day
# of the window — so d 5.0, sd_d 0.0 (the backtest's MAE is 0 and the
# override never touches the history).
OVR_FORECAST_D = (5 * OVR_QTY / 5 + (30 - 5) * 2.0) / 30  # 5.0
OVR_FORECAST_SS = Z95 * math.sqrt(
    LT_MEAN * 0.0 * 0.0 + OVR_FORECAST_D * OVR_FORECAST_D * LT_SD * LT_SD
)
OVR_FORECAST_ROP = OVR_FORECAST_D * LT_MEAN + OVR_FORECAST_SS

# The demand_multiplier 1.5 scenario of K970: d 3.0, sd_d 0.0.
SCEN_D = 2.0 * 1.5
SCEN_SS = Z95 * math.sqrt(LT_MEAN * 0.0 * 0.0 + SCEN_D * SCEN_D * LT_SD * LT_SD)
SCEN_ROP = SCEN_D * LT_MEAN + SCEN_SS
SCEN_SS_UNITS = math.ceil(round(SCEN_SS, 9))  # 10
SCEN_ROP_UNITS = math.ceil(round(SCEN_ROP, 9))  # 40

# I970's and I970B's intermittent demand (the test 692 pattern): 60
# units on the first day, 4 a day for the last 15 days, 6 a day for
# the 10 days ending on the as-of minus 30.  35 of the 351 span
# totals are positive — 4, 6 (x2), 8, 12 (x3), 16, 18 (x2), 20, 24
# (x3), 28, 30 (x2), 32, 36 (x3), 40 (x6), 42 (x2), 48 (x2), 54 (x2)
# and 60 (x2) — and the 334th smallest of the 351 (k = ceil(0.95 *
# 351), the 18th positive) is 32.0; the mean span total is 1080 / 351.
INTER_DAILY: dict[date, float] = {AS_OF - timedelta(days=359): 60.0}
for _OFFSET in range(15):
    INTER_DAILY[AS_OF - timedelta(days=_OFFSET)] = 4.0
for _OFFSET in range(30, 40):
    INTER_DAILY[AS_OF - timedelta(days=_OFFSET)] = 6.0
INTER_SPANS = 351  # 360 days - 10 + 1
INTER_MEAN_DAILY = (60.0 + 15 * 4.0 + 10 * 6.0) / 360  # 0.5


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    return _FixedDate


class _CountingCursor:
    """A cursor that records its executed SQL, delegating the rest."""

    def __init__(self, real, queries):
        self._real = real
        self._queries = queries

    def execute(self, query, *args, **kwargs):
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
    """A connection whose cursors record their executed SQL."""

    def __init__(self, real, queries):
        self._real = real
        self._queries = queries

    def cursor(self, *args, **kwargs):
        return _CountingCursor(self._real.cursor(*args, **kwargs), self._queries)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _measure(fn):
    """Run fn with psycopg2.connect wrapped; (result, SQL statements)."""
    queries: list[str] = []
    real_connect = psycopg2.connect

    def counting_connect(*args, **kwargs):
        return _CountingConnection(real_connect(*args, **kwargs), queries)

    psycopg2.connect = counting_connect
    try:
        result = fn()
    finally:
        psycopg2.connect = real_connect
    return result, queries


def _signal_reads(queries: list[str]) -> list[str]:
    """The counted-signals reads (the demand_signals table SELECT)."""
    return [query for query in queries if "FROM demand_signals" in query]


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


BASE_ARITHMETIC = _basis_arithmetic(2.0, 0.0, LT_MEAN, LT_SD)


def _signal_clause(before: float, units: float) -> str:
    """The arithmetic clause the signals append, as the code writes it."""
    return (
        f"; with demand signals = {before:.2f} + {units:.2f}" f" = {before + units:.2f}"
    )


def _demand(cur, item, first, days, quantity):
    """``days`` of ``quantity`` units a day for one item, from ``first``."""
    cur.executemany(
        "INSERT INTO demand_events (id, business_key, item_code, site_code, "
        "quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                f"id-de-970-{item}-{i}",
                f"de-970-{item}-{i}",
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
        "INSERT INTO demand_events (id, business_key, item_code, site_code, "
        "quantity, occurred_on, event_type, source_system) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        [
            (
                f"id-de-970-{item}-{day.isoformat()}",
                f"de-970-{item}-{day.isoformat()}",
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
                f"id-lt-970-{item}-{i}",
                f"lt-970-{item}-{i}",
                f"SUP-970-{item}",
                item,
                ordered,
                received,
                "test",
            ),
        )


def _item_site(cur, item, reorder_point, safety_stock):
    """The item-site's current planning settings (unit cost 1.0)."""
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, source_system) "
        "VALUES (%s, %s, %s, 1.0, %s, %s, 'test')",
        (f"id-is-970-{item}", item, SITE, reorder_point, safety_stock),
    )


def _signal_body(item, start, end, quantity, reason) -> dict:
    return {
        "kind": "promotion",
        "item_code": item,
        "site_code": SITE,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "quantity": quantity,
        "reason": reason,
    }


def _store_signal(admin, item, start, end, quantity, reason) -> int:
    resp = admin.post(
        "/api/demand-signals", json=_signal_body(item, start, end, quantity, reason)
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


def _withdraw(admin, signal_id: int) -> None:
    resp = admin.post(
        f"/api/demand-signals/{signal_id}/withdraw",
        json={"reason": "issue 970 test withdrawal"},
    )
    assert resp.status_code == 201, resp.text


def _rows(client) -> dict:
    """GET /api/recommendations keyed by (item_code, site_code)."""
    resp = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert resp.status_code == 200, resp.text
    return {(row["item_code"], row["site_code"]): row for row in resp.json()}


def _by_key(client, item) -> dict:
    resp = client.get(
        "/api/recommendations/by-key",
        params={"item_code": item, "site_code": SITE, "as_of": AS_OF_STR},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _csv_units(client) -> dict:
    """The planning-parameters CSV's recommended units by item."""
    resp = client.get("/api/recommendations/export.csv", params={"as_of": AS_OF_STR})
    assert resp.status_code == 200, resp.text
    assert resp.text.startswith("\ufeff")
    parsed = list(csv.reader(io.StringIO(resp.text[1:])))
    header = parsed[0]
    return {
        row[header.index("item_code")]: row[
            header.index("recommended_reorder_point_units")
        ]
        for row in parsed[1:]
    }


def _plan_line(client, item) -> dict:
    """One item's line of GET /api/supply-plan?horizon_days=7."""
    resp = client.get("/api/supply-plan", params={"horizon_days": 7})
    assert resp.status_code == 200, resp.text
    for group in resp.json()["suppliers"]:
        for line in group["lines"]:
            if line["item_code"] == item:
                return line
    raise AssertionError(f"{item} is not in the supply plan")


def _simulate(client) -> dict:
    """POST /api/scenarios/simulate with demand_multiplier 1.5."""
    resp = client.post("/api/scenarios/simulate", json={"demand_multiplier": 1.5})
    assert resp.status_code == 200, resp.text
    return resp.json()


def _check_k970_baseline(row: dict, inputs_a: dict) -> None:
    """K970 with no counted signal: exactly today's values."""
    assert row["status"] == "ok"
    assert row["demand_class"] == "smooth"
    assert row["method"] == FORMULA_METHOD
    assert row["demand_basis"] == "history"
    assert row["signal_units"] == 0.0
    assert row["reorder_point_before_signals_units"] == ROP_UNITS
    assert row["safety_stock"] == pytest.approx(SS)
    assert row["reorder_point"] == pytest.approx(ROP)
    assert row["safety_stock_units"] == SS_UNITS
    assert row["reorder_point_units"] == ROP_UNITS
    assert row["inputs"] == inputs_a
    assert row["arithmetic"] == BASE_ARITHMETIC
    assert row["notes"] == []
    assert row["delta_reorder_point"] == pytest.approx(CURRENT_ROP - ROP)
    assert row["suggested_order_quantity"] == ROP_UNITS
    assert row["order_quantity"]["order_quantity_units"] == EOQ_UNITS
    other = row["other_basis"]
    assert other["basis"] == "forecast"
    assert other["reorder_point"] == pytest.approx(ROP)
    assert other["reorder_point_units"] == ROP_UNITS
    assert other["inputs"]["d"] == 2.0


def test_issue970_freeform(client, admin_headers, monkeypatch):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    admin = TestClient(app, headers=admin_headers)

    # The empirical reorder point both intermittent item-sites carry:
    # the 95th percentile of the 351 overlapping 10-day span totals,
    # 35 of them positive, hence 32.0 — written out as arithmetic on
    # the seeded daily totals above.
    emp_expected = empirical_reorder_point(
        INTER_DAILY, FIRST_DAY, AS_OF, LT_MEAN, SERVICE_LEVEL
    )
    assert emp_expected is not None
    assert emp_expected["window_days"] == WINDOW_DAYS
    assert emp_expected["spans"] == INTER_SPANS
    assert emp_expected["spans_with_demand"] == 35
    assert emp_expected["reorder_point"] == 32.0
    emp_rop = emp_expected["reorder_point"]  # 32.0
    emp_units = emp_expected["reorder_point_units"]  # 32
    emp_arithmetic = emp_expected["arithmetic"]

    # --- seed: one smooth and two intermittent item-sites -------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand(cur, SMOOTH, FIRST_DAY, 360, 2.0)
            _lead_times(cur, SMOOTH)
            _item_site(cur, SMOOTH, CURRENT_ROP, 10.0)
            _demand_events(cur, INTER, INTER_DAILY)
            _lead_times(cur, INTER)
            _item_site(cur, INTER, 9.0, 1.0)
            _demand_events(cur, INTER_B, INTER_DAILY)
            _lead_times(cur, INTER_B)
            _item_site(cur, INTER_B, 9.0, 1.0)
        conn.commit()
    finally:
        conn.close()

    # The stored planning costs (EOQ sqrt(1460) = 39 units, below the
    # suggested quantity once the signal lifts the reorder point) and
    # I970's stored empirical adoption.
    resp = admin.put("/api/settings/replenishment.order_cost", json={"value": 1.0})
    assert resp.status_code == 200, resp.text
    resp = admin.put("/api/settings/replenishment.holding_rate", json={"value": 1.0})
    assert resp.status_code == 200, resp.text
    resp = admin.put(
        "/api/empirical-adoptions",
        json={"item_code": INTER, "site_code": SITE},
    )
    assert resp.status_code == 200, resp.text

    # ------------------------------------------------------------------
    # With no signal stored: every value answers exactly what it does
    # today, and the two new fields read 0 and today's units.
    # ------------------------------------------------------------------
    rows = _rows(client)
    assert set(rows) == {(item, SITE) for item in (SMOOTH, INTER, INTER_B)}
    k970 = rows[(SMOOTH, SITE)]
    i970 = rows[(INTER, SITE)]
    i970b = rows[(INTER_B, SITE)]
    k970_inputs = k970["inputs"]
    _check_k970_baseline(k970, k970_inputs)

    # The adopted intermittent item-site: recommended on its empirical
    # lead-time demand, with the new fields at 0 and its units.
    assert i970["status"] == "ok"
    assert i970["demand_class"] == "intermittent"
    assert i970["method"] == EMPIRICAL_METHOD
    assert i970["empirical_adopted"] is True
    assert i970["signal_units"] == 0.0
    assert i970["reorder_point_before_signals_units"] == emp_units
    assert i970["safety_stock"] == pytest.approx(emp_expected["safety_stock"])
    assert i970["reorder_point"] == pytest.approx(emp_rop)
    assert i970["reorder_point_units"] == emp_units
    assert i970["inputs"]["d"] == INTER_MEAN_DAILY
    assert i970["arithmetic"] == emp_arithmetic
    assert i970["notes"] == [EMPIRICAL_NOTE]
    assert i970["suggested_order_quantity"] == emp_units
    assert i970["empirical"] == emp_expected

    # The unadopted intermittent item-site: not ok — 0 and null.
    assert i970b["status"] == "not_recommendable_demand"
    assert i970b["signal_units"] == 0.0
    assert i970b["reorder_point_before_signals_units"] is None
    assert i970b["empirical_adopted"] is False
    assert i970b["empirical"] == emp_expected

    # The by-key reader agrees with the list before any signal.
    assert _by_key(client, SMOOTH)["reorder_point"] == pytest.approx(ROP)

    # The pre-signal snapshots of the other readers.
    plan_0 = _plan_line(client, SMOOTH)
    assert plan_0["quantity"] == max(EOQ_UNITS, ROP_UNITS)  # 39
    assert plan_0["reorder_point_units"] == ROP_UNITS
    sim_0 = _simulate(client)
    sim_0_by_item = {row["item_code"]: row for row in sim_0["item_sites"]}
    assert set(sim_0_by_item) == {SMOOTH, INTER}
    k970_sim = sim_0_by_item[SMOOTH]
    assert k970_sim["baseline"]["reorder_point_units"] == ROP_UNITS
    assert k970_sim["baseline"]["safety_stock_units"] == SS_UNITS
    assert k970_sim["scenario"]["reorder_point_units"] == SCEN_ROP_UNITS
    assert k970_sim["scenario"]["safety_stock_units"] == SCEN_SS_UNITS
    assert k970_sim["reorder_point_units_delta"] == (SCEN_ROP_UNITS - ROP_UNITS)
    assert k970_sim["safety_stock_units_delta"] == (SCEN_SS_UNITS - SS_UNITS)
    csv_0 = _csv_units(client)
    assert csv_0[SMOOTH] == str(ROP_UNITS)  # 27

    # ------------------------------------------------------------------
    # History basis: the 300-unit signal over the 30-day window
    # starting the day after the as-of.
    # ------------------------------------------------------------------
    s_main = _store_signal(
        admin, SMOOTH, S_MAIN_START, S_MAIN_END, S_MAIN_QTY, "issue 970 main signal"
    )
    # The same window on the adopted intermittent item-site.
    s_inter = _store_signal(
        admin,
        INTER,
        S_MAIN_START,
        S_MAIN_END,
        S_MAIN_QTY,
        "issue 970 intermittent signal",
    )

    rows = _rows(client)
    k970 = rows[(SMOOTH, SITE)]
    i970 = rows[(INTER, SITE)]
    i970b = rows[(INTER_B, SITE)]
    assert k970["status"] == "ok"
    assert k970["signal_units"] == S_MAIN_UNITS  # 100.0
    assert k970["reorder_point_before_signals_units"] == ROP_UNITS
    assert k970["safety_stock"] == pytest.approx(SS)  # unchanged
    assert k970["reorder_point"] == pytest.approx(ROP + S_MAIN_UNITS)
    assert k970["reorder_point_units"] == math.ceil(round(ROP + S_MAIN_UNITS, 9))  # 127
    assert k970["safety_stock_units"] == SS_UNITS
    assert k970["inputs"] == k970_inputs  # d included: unchanged
    assert k970["inputs"]["d"] == 2.0
    assert k970["arithmetic"] == (BASE_ARITHMETIC + _signal_clause(ROP, S_MAIN_UNITS))
    assert k970["notes"] == [SIGNAL_NOTE]
    assert k970["delta_reorder_point"] == pytest.approx(
        CURRENT_ROP - (ROP + S_MAIN_UNITS)
    )
    assert k970["suggested_order_quantity"] == math.ceil(
        round(ROP + S_MAIN_UNITS, 9)
    )  # 127: the new reorder point
    other = k970["other_basis"]
    assert other["basis"] == "forecast"
    assert other["reorder_point"] == pytest.approx(ROP + S_MAIN_UNITS)
    assert other["reorder_point_units"] == math.ceil(round(ROP + S_MAIN_UNITS, 9))
    assert other["inputs"]["d"] == 2.0

    # The adopted intermittent item-site: its empirical reorder point
    # plus the same signal units.
    assert i970["status"] == "ok"
    assert i970["signal_units"] == S_MAIN_UNITS
    assert i970["reorder_point_before_signals_units"] == emp_units
    assert i970["safety_stock"] == pytest.approx(emp_expected["safety_stock"])
    assert i970["reorder_point"] == pytest.approx(emp_rop + S_MAIN_UNITS)
    assert i970["reorder_point_units"] == math.ceil(
        round(emp_rop + S_MAIN_UNITS, 9)
    )  # 132
    assert i970["inputs"]["d"] == INTER_MEAN_DAILY  # unchanged
    assert i970["arithmetic"] == (
        emp_arithmetic + _signal_clause(emp_rop, S_MAIN_UNITS)
    )
    assert i970["notes"] == [EMPIRICAL_NOTE, SIGNAL_NOTE]
    assert i970["suggested_order_quantity"] == math.ceil(
        round(emp_rop + S_MAIN_UNITS, 9)
    )
    # The not-ok row keeps 0 and null, with or without the signal.
    assert i970b["signal_units"] == 0.0
    assert i970b["reorder_point_before_signals_units"] is None

    # --- every reader agrees with the list -----------------------------
    by_key = _by_key(client, SMOOTH)
    assert by_key["reorder_point"] == k970["reorder_point"]
    assert by_key["signal_units"] == k970["signal_units"] == S_MAIN_UNITS
    csv_1 = _csv_units(client)
    assert csv_1[SMOOTH] == str(math.ceil(round(ROP + S_MAIN_UNITS, 9)))  # 127
    assert csv_1[SMOOTH] != csv_0[SMOOTH]
    plan_1 = _plan_line(client, SMOOTH)
    assert plan_1["quantity"] == math.ceil(round(ROP + S_MAIN_UNITS, 9))  # 127
    assert plan_1["quantity"] > plan_0["quantity"]  # the higher quantity
    assert plan_1["reorder_point_units"] == math.ceil(round(ROP + S_MAIN_UNITS, 9))

    # The simulation answers the same deltas with the signal stored:
    # the scenario side is rebuilt from the inputs and carries none.
    sim_1 = _simulate(client)
    for item in (SMOOTH, INTER):
        before = sim_0_by_item[item]
        after = {row["item_code"]: row for row in sim_1["item_sites"]}[item]
        assert (
            after["reorder_point_units_delta"] == before["reorder_point_units_delta"]
        ), item
        assert (
            after["safety_stock_units_delta"] == before["safety_stock_units_delta"]
        ), item
        assert after["scenario"] == before["scenario"], item
        assert (
            after["baseline"]["reorder_point_units"]
            == before["baseline"]["reorder_point_units"]
        ), item
        assert (
            after["baseline"]["safety_stock_units"]
            == before["baseline"]["safety_stock_units"]
        ), item
        assert (
            after["baseline"]["safety_stock"] == before["baseline"]["safety_stock"]
        ), item
    assert sim_1["summary"]["dollar_impact"] == sim_0["summary"]["dollar_impact"]
    assert (
        sim_1["summary"]["reorder_point_units_delta_total"]
        == sim_0["summary"]["reorder_point_units_delta_total"]
    )

    # --- one read: the counted signals are read once per call ----------
    response, queries = _measure(
        lambda: client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    )
    assert response.status_code == 200
    assert len(_signal_reads(queries)) == 1
    response, queries = _measure(
        lambda: client.get(
            "/api/recommendations/by-key",
            params={"item_code": SMOOTH, "site_code": SITE, "as_of": AS_OF_STR},
        )
    )
    assert response.status_code == 200
    assert len(_signal_reads(queries)) == 1

    # ------------------------------------------------------------------
    # Two signals: the overlapping 20-day window adds its 5 in-window
    # days' shares; then the withdrawal of the main signal leaves them.
    # ------------------------------------------------------------------
    s_part = _store_signal(
        admin,
        SMOOTH,
        S_PART_START,
        S_PART_END,
        S_PART_QTY,
        "issue 970 partial signal",
    )

    k970 = _rows(client)[(SMOOTH, SITE)]
    assert k970["signal_units"] == S_MAIN_UNITS + S_PART_UNITS  # 115.0
    assert k970["reorder_point_units"] == math.ceil(
        round(ROP + S_MAIN_UNITS + S_PART_UNITS, 9)
    )  # 142
    assert k970["arithmetic"] == (
        BASE_ARITHMETIC + _signal_clause(ROP, S_MAIN_UNITS + S_PART_UNITS)
    )

    # Partly inside alone: the 20-day window starting on day 6 counts
    # 5 of its 20 days (15.0).
    _withdraw(admin, s_main)
    k970 = _rows(client)[(SMOOTH, SITE)]
    assert k970["signal_units"] == S_PART_UNITS  # 15.0
    assert k970["reorder_point"] == pytest.approx(ROP + S_PART_UNITS)
    assert k970["reorder_point_units"] == math.ceil(round(ROP + S_PART_UNITS, 9))  # 42
    assert k970["reorder_point_before_signals_units"] == ROP_UNITS

    # ------------------------------------------------------------------
    # Outside the lead time: a signal starting after the first 10 days
    # gives signal_units 0 and today's values — and so does a
    # withdrawn signal.
    # ------------------------------------------------------------------
    _withdraw(admin, s_part)
    _withdraw(admin, s_inter)
    k970 = _rows(client)[(SMOOTH, SITE)]
    assert k970["signal_units"] == 0.0
    # The adopted item-site back on its empirical values alone.
    i970 = _rows(client)[(INTER, SITE)]
    assert i970["signal_units"] == 0.0
    assert i970["reorder_point_before_signals_units"] == emp_units
    assert i970["reorder_point"] == pytest.approx(emp_rop)
    assert i970["reorder_point_units"] == emp_units
    assert i970["arithmetic"] == emp_arithmetic
    assert i970["notes"] == [EMPIRICAL_NOTE]
    assert i970["suggested_order_quantity"] == emp_units

    s_out = _store_signal(
        admin,
        SMOOTH,
        S_OUT_START,
        S_OUT_END,
        S_OUT_QTY,
        "issue 970 late signal",
    )
    k970 = _rows(client)[(SMOOTH, SITE)]
    assert k970["signal_units"] == 0.0  # no window day covered
    _check_k970_baseline(k970, k970_inputs)

    _withdraw(admin, s_out)
    k970 = _rows(client)[(SMOOTH, SITE)]
    assert k970["signal_units"] == 0.0
    _check_k970_baseline(k970, k970_inputs)

    # ------------------------------------------------------------------
    # Forecast basis: the same check with the demand basis set to
    # "forecast"; other_basis (history) carries the 100 as well.
    # ------------------------------------------------------------------
    resp = admin.put(
        "/api/settings/replenishment.demand_basis", json={"value": "forecast"}
    )
    assert resp.status_code == 200, resp.text
    s_main = _store_signal(
        admin,
        SMOOTH,
        S_MAIN_START,
        S_MAIN_END,
        S_MAIN_QTY,
        "issue 970 forecast signal",
    )

    k970 = _rows(client)[(SMOOTH, SITE)]
    assert k970["status"] == "ok"
    assert k970["demand_basis"] == "forecast"
    assert k970["inputs"]["d"] == 2.0
    assert k970["inputs"]["sd_d"] == 0.0
    assert k970["signal_units"] == S_MAIN_UNITS
    assert k970["reorder_point_before_signals_units"] == ROP_UNITS
    assert k970["reorder_point"] == pytest.approx(ROP + S_MAIN_UNITS)
    assert k970["reorder_point_units"] == math.ceil(round(ROP + S_MAIN_UNITS, 9))
    assert k970["arithmetic"] == (BASE_ARITHMETIC + _signal_clause(ROP, S_MAIN_UNITS))
    assert k970["notes"] == [
        f"forecast demand over {WINDOW_DAYS} days",
        SIGNAL_NOTE,
    ]
    other = k970["other_basis"]
    assert other["basis"] == "history"
    assert other["reorder_point"] == pytest.approx(ROP + S_MAIN_UNITS)
    assert other["reorder_point_units"] == math.ceil(round(ROP + S_MAIN_UNITS, 9))
    assert other["inputs"]["d"] == 2.0
    resp = admin.delete("/api/settings/replenishment.demand_basis")
    assert resp.status_code == 200, resp.text

    # ------------------------------------------------------------------
    # An override wins: an override covering days 1 to 5 removes those
    # days' shares from signal_units, on both bases.
    # ------------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO forecast_overrides "
                "(item_code, site_code, period_start, period_end, kind, "
                "quantity, reason, created_by) "
                "VALUES (%s, %s, %s, %s, 'override', %s, %s, 'direct-insert')",
                (SMOOTH, SITE, OVR_START, OVR_END, OVR_QTY, "issue 970 override"),
            )
        conn.commit()
    finally:
        conn.close()

    k970 = _rows(client)[(SMOOTH, SITE)]
    assert k970["demand_basis"] == "history"
    assert k970["signal_units"] == OVR_UNITS  # 50.0: days 6-10 only
    assert k970["reorder_point"] == pytest.approx(ROP + OVR_UNITS)
    assert k970["reorder_point_units"] == math.ceil(round(ROP + OVR_UNITS, 9))  # 77
    assert k970["arithmetic"] == BASE_ARITHMETIC + _signal_clause(ROP, OVR_UNITS)
    other = k970["other_basis"]
    assert other["basis"] == "forecast"
    assert other["inputs"]["d"] == OVR_FORECAST_D  # 11.0: the override's day shares
    assert other["reorder_point"] == pytest.approx(OVR_FORECAST_ROP + OVR_UNITS)
    assert other["reorder_point_units"] == math.ceil(
        round(OVR_FORECAST_ROP + OVR_UNITS, 9)
    )  # 197

    # The override withdrawn and the signal withdrawn: today's values
    # again.
    resp = admin.post(
        "/api/forecast/overrides/withdrawals",
        json={
            "item_code": SMOOTH,
            "site_code": SITE,
            "period_start": OVR_START.isoformat(),
            "reason": "issue 970 override withdrawal",
        },
    )
    assert resp.status_code == 201, resp.text
    _withdraw(admin, s_main)
    k970 = _rows(client)[(SMOOTH, SITE)]
    assert k970["signal_units"] == 0.0
    _check_k970_baseline(k970, k970_inputs)

    # ------------------------------------------------------------------
    # No switch: recommendations_for_all counts signals on every call.
    # ------------------------------------------------------------------
    assert list(inspect.signature(recommendations_for_all).parameters) == ["as_of"]
    with pytest.raises(TypeError):
        recommendations_for_all(AS_OF, include_signals=False)

    # ------------------------------------------------------------------
    # The projection setup's as-of validation: a datetime is not a
    # date, so it answers the non-date ValueError (never the TypeError
    # of comparing a datetime with a date).
    # ------------------------------------------------------------------
    signal_dt = datetime(AS_OF.year, AS_OF.month, AS_OF.day, 12, 0)
    with pytest.raises(ValueError, match="as_of must be a date"):
        aggregate_projection(as_of=signal_dt)
