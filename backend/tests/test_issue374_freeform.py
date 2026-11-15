"""Issue #374 proof: the forecast list on a fixed query budget.

Against the test database (as-of 2026-09-30, date.today pinned to
2026-10-02) with demand events inserted for the test:

* ``app.forecast.listing.forecast_rows`` returns every item-site's
  forecast row — the effective forecast beside the statistical one —
  row for row and field for field equal to the list the per-item-site
  path (the demand statistics, one ``history_for_pair`` per item-site,
  the row builder and the counted overrides) builds, including a
  young item-site with fewer than 12 history buckets, a no-demand
  item-site left out (and a ``no_demand``-class one) and an item-site
  with a stored planner override; the same with a ``demand_class``
  filter (every class) and with an explicit as-of date (and the
  shared default as-of date);
* ``GET /api/forecast`` with an offset and a limit returns the
  matching slice of ``forecast_rows()``;
* the number of SQL statements ``forecast_rows()`` executes, counted
  by wrapping the cursor's ``execute``, is the same with 5 item-sites
  as with 25 — the demand statistics come from ``demand_stats_for_all``,
  every item-site's history from one ``history_for_all`` query and the
  counted overrides of every row from one ``effective_for_all`` query,
  so the statement count does not grow with the number of item-sites.
"""

from datetime import date, timedelta

import psycopg2
import pytest

from app.db.connection import get_connection
from app.forecast.history import history_for_pair
from app.forecast.listing import build_forecast_row, forecast_rows
from app.forecast.overrides import effective_for_all
from app.stats.demand import demand_stats_for_all

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
SITE = "SITE-374"
# The fixed statement budget for an explicit as_of:
# 1 history_for_all + 2 demand_stats_for_all + 1 counted overrides +
# 1 counted signals.
FIXED_STATEMENTS_EXPLICIT = 5
# And for the shared default as_of: each batched reader resolves the
# default from demand_events with one query of its own.
FIXED_STATEMENTS_DEFAULT = 7


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
                f"id-de-374-{item}-{i}",
                f"de-374-{item}-{i}",
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


def _item_site(cur, item):
    """The item-site's current planning settings (planning values unused here)."""
    cur.execute(
        "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
        "reorder_point, safety_stock, source_system) "
        "VALUES (%s, %s, %s, 1.0, 25.0, 10.0, 'test')",
        (f"id-is-374-{item}", item, SITE),
    )


def _legacy_rows(as_of=None, demand_class=None):
    """The per-item-site path: the list the route built before #374.

    The demand statistics from ``demand_stats_for_all``, one
    ``history_for_pair`` per item-site, the same row builder and the
    counted overrides — in the same order and with the same fields.
    """
    rows: list[dict] = []
    for stats in demand_stats_for_all(as_of):
        if demand_class is not None and stats["demand_class"] != demand_class:
            continue
        row = build_forecast_row(
            stats["item_code"],
            stats["site_code"],
            stats["demand_class"],
            stats["as_of"],
            history_for_pair(stats["item_code"], stats["site_code"], stats["as_of"]),
        )
        if row is not None:
            rows.append(row)
    rows = effective_for_all(rows)
    rows.sort(
        key=lambda row: (-row["total_forecast"], row["item_code"], row["site_code"])
    )
    return rows


def _jsonable(value):
    """The row's JSON form: dates as ISO strings, the rest unchanged."""
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, date):
        return value.isoformat()
    return value


def test_issue374_freeform(client, monkeypatch, admin_headers):
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.setattr("app.forecast.history.date", _fixed_date())

    # --- seed 5 smooth item-sites (360 days, 2.0 a day) -------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for i in range(1, 6):
                _demand(cur, f"K{i}", FIRST_DAY, 360, 2.0)
                _item_site(cur, f"K{i}")
        conn.commit()
    finally:
        conn.close()

    # --- the fixed query budget: 5 item-sites ------------------------------
    rows5, count5, queries5 = _measure(lambda: forecast_rows(AS_OF))
    assert [(r["item_code"], r["site_code"]) for r in rows5] == [
        (f"K{i}", SITE) for i in range(1, 6)
    ]
    default5, count5_default, queries5_default = _measure(lambda: forecast_rows(None))
    assert default5 == rows5  # the shared default as-of is 2026-09-30
    assert count5 == FIXED_STATEMENTS_EXPLICIT
    assert count5_default == FIXED_STATEMENTS_DEFAULT
    # One history query and one counted-override query in total — never
    # one per item-site.
    assert len([q for q in queries5 if "MIN(occurred_on)" in q]) == 2
    assert len([q for q in queries5 if "forecast_overrides" in q]) == 1

    # --- and still fixed with 25 item-sites --------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            for i in range(6, 26):
                _demand(cur, f"K{i}", FIRST_DAY, 360, 2.0)
                _item_site(cur, f"K{i}")
        conn.commit()
    finally:
        conn.close()

    rows25, count25, queries25 = _measure(lambda: forecast_rows(AS_OF))
    assert len(rows25) == 25
    default25, count25_default, _ = _measure(lambda: forecast_rows(None))
    assert len(default25) == 25
    # Five times as many item-sites, the same number of statements.
    assert count25 == count5 == FIXED_STATEMENTS_EXPLICIT
    assert count25_default == count5_default == FIXED_STATEMENTS_DEFAULT
    assert len([q for q in queries25 if "MIN(occurred_on)" in q]) == 2
    assert len([q for q in queries25 if "forecast_overrides" in q]) == 1

    # --- the equivalence fixture -------------------------------------------
    # Y374: young — 65 days of demand, only two complete 30-day buckets
    # (the 5 days before the oldest bucket are not in the history).  L374:
    # lumpy — three non-zero buckets of 20, 1 and 3 out of eight.  O374:
    # smooth, and the item that carries a planner override.  N374: an
    # item-site with no demand events at all.  D374: its only demand
    # event falls outside the 360-day stats window — no_demand.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _demand(cur, "Y374", AS_OF - timedelta(days=64), 65, 4.0)
            _item_site(cur, "Y374")
            for offset, quantity in ((59, 20.0), (149, 1.0), (239, 3.0)):
                cur.execute(
                    "INSERT INTO demand_events (id, business_key, item_code, "
                    "site_code, quantity, occurred_on, event_type, "
                    "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s, 'test')",
                    (
                        f"id-de-374-L374-{offset}",
                        f"de-374-L374-{offset}",
                        "L374",
                        SITE,
                        quantity,
                        (AS_OF - timedelta(days=offset)).isoformat(),
                        "shipment",
                    ),
                )
            _item_site(cur, "L374")
            _demand(cur, "O374", FIRST_DAY, 360, 2.0)
            _item_site(cur, "O374")
            _item_site(cur, "N374")
            cur.execute(
                "INSERT INTO demand_events (id, business_key, item_code, "
                "site_code, quantity, occurred_on, event_type, "
                "source_system) VALUES (%s, %s, %s, %s, %s, %s, %s, 'test')",
                (
                    "id-de-374-D374",
                    "de-374-D374",
                    "D374",
                    SITE,
                    1.0,
                    (AS_OF - timedelta(days=400)).isoformat(),
                    "shipment",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # The planner override of O374's first forecast period (2026-10-01,
    # the day after the as-of, through 2026-10-30) to 100 units.
    response = client.post(
        "/api/forecast/overrides",
        json={
            "item_code": "O374",
            "site_code": SITE,
            "period_start": (AS_OF + timedelta(days=1)).isoformat(),
            "quantity": 100.0,
            "reason": "planner override for the issue 374 proof",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    assert response.json()["applies"] is True

    # --- AC-1: same answers as the per-item-site path ----------------------
    # Explicit as-of: row for row and field for field, the effective
    # forecast beside the statistical one.
    listed = forecast_rows(AS_OF)
    legacy = _legacy_rows(AS_OF)
    assert listed == legacy
    assert len(listed) == 28  # N374 (no demand) and D374 (no_demand) left out

    # The shared default as-of date: the same rows, resolved from
    # demand_events (the latest occurred_on, 2026-09-30).
    assert forecast_rows(None) == _legacy_rows(None)
    assert {row["as_of"] for row in forecast_rows(None)} == {AS_OF}

    # Every demand-class filter, the same answers.
    for demand_class in ("no_demand", "smooth", "erratic", "intermittent", "lumpy"):
        assert forecast_rows(AS_OF, demand_class=demand_class) == _legacy_rows(
            AS_OF, demand_class=demand_class
        ), demand_class
    assert [r["item_code"] for r in forecast_rows(AS_OF, demand_class="lumpy")] == [
        "L374"
    ]
    assert forecast_rows(AS_OF, demand_class="no_demand") == []
    assert forecast_rows(AS_OF, demand_class="erratic") == []

    # The order: total_forecast descending, then item_code, then
    # site_code — the young item first (1440), the 26 smooth items tied
    # at 720 by item_code, the lumpy item last.
    assert [(row["item_code"], row["site_code"]) for row in listed] == (
        [("Y374", SITE)]
        + [(code, SITE) for code in sorted([f"K{i}" for i in range(1, 26)] + ["O374"])]
        + [("L374", SITE)]
    )

    by_item = {row["item_code"]: row for row in listed}
    assert "N374" not in by_item  # the no-demand item-site is left out
    assert "D374" not in by_item  # the no_demand item-site is left out

    # The young item: two complete history buckets (fewer than 12), a
    # mean forecast with no backtest.
    young = by_item["Y374"]
    assert young["history_buckets"] == 2
    assert young["method"] == "mean"
    assert young["backtest"] is None
    assert young["total_forecast"] == pytest.approx(1440.0)
    assert len(young["forecast"]) == 12

    # The overridden item: the statistical forecast untouched, the
    # effective forecast beside it.
    overridden = by_item["O374"]
    assert overridden["total_forecast"] == pytest.approx(720.0)
    assert overridden["overridden_buckets"] == 1
    assert overridden["total_effective_forecast"] == pytest.approx(760.0)
    first = overridden["forecast"][0]
    assert first == {
        "bucket_start": AS_OF + timedelta(days=1),
        "bucket_end": AS_OF + timedelta(days=30),
        "quantity": 60.0,
        "effective_quantity": 100.0,
        "signal_quantity": 0.0,
        "override_days": 30,
    }
    assert all(
        bucket["effective_quantity"] == pytest.approx(bucket["quantity"])
        and bucket["override_days"] == 0
        for bucket in overridden["forecast"][1:]
    )

    # The same override beside the statistical forecast on the
    # per-item-site path (the row plus its history buckets).
    detail = client.get(
        "/api/forecast/by-key",
        params={"item_code": "O374", "site_code": SITE, "as_of": AS_OF_STR},
    )
    assert detail.status_code == 200, detail.text
    detail_row = detail.json()
    assert detail_row["total_forecast"] == pytest.approx(720.0)
    assert detail_row["total_effective_forecast"] == pytest.approx(760.0)
    assert len(detail_row["history"]) == 12  # 360 days of demand = 12 complete buckets
    assert _jsonable(detail_row) == _jsonable(
        {**overridden, "history": history_for_pair("O374", SITE, AS_OF)}
    )
    missing = client.get(
        "/api/forecast/by-key",
        params={"item_code": "N374", "site_code": SITE, "as_of": AS_OF_STR},
    )
    assert missing.status_code == 404

    # --- AC-2: the route pages forecast_rows with offset and limit --------
    full = forecast_rows(AS_OF)
    response = client.get(
        "/api/forecast", params={"as_of": AS_OF_STR, "offset": 2, "limit": 5}
    )
    assert response.status_code == 200, response.text
    assert response.json() == _jsonable(full[2:7])
    # The default paging (offset 0, limit 100) returns the whole list.
    response = client.get("/api/forecast", params={"as_of": AS_OF_STR})
    assert response.status_code == 200, response.text
    assert response.json() == _jsonable(full)
    # And the slice at the shared default as-of date.
    default = forecast_rows(None)
    response = client.get("/api/forecast", params={"limit": 4, "offset": 26})
    assert response.status_code == 200, response.text
    assert response.json() == _jsonable(default[26:30])
