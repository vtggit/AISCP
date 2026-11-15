"""Issue #980 proof: the signals' effect, measured alone.

``realised_accuracy`` (issue #336) compares the completed buckets of
the stored forecast snapshots with what actually happened.  This
issue makes its ``value_added`` the overrides' effect, measured
alone: ``wape`` minus the error of the effective forecast *before*
the signals, where a bucket's forecast before the signals is its
effective quantity minus its stored ``signal_quantity`` (a null
signal reads as 0) — while ``effective_total``, ``effective_wape``
and ``effective_bias`` stay the effective forecast's, signal
included, and with no signal in the compared buckets every measure
is exactly what it is today.  The issue adds
``GET /api/forecast/accuracy/signals`` (any signed-in user,
``as_of`` and ``origin`` with the same types, default and 422
behaviour as ``GET /api/forecast/accuracy``): the same
completed-bucket comparison restricted to the buckets with a stored
``signal_quantity`` above 0 (a null or 0 bucket is left out), with
``before_total`` (the sum of each bucket's effective quantity minus
its signal quantity), ``after_total`` (the sum of its effective
quantities), ``signal_total`` (the sum of its signal quantity),
``before_wape`` / ``after_wape`` (each the sum of the absolute
differences from the actuals, over ``actual_total``, null when
``actual_total`` is 0) and ``value_added`` (``before_wape`` minus
``after_wape``, positive when the signals moved the forecast nearer
the actuals) — ``as_of``, those measures and ``item_sites``, one
entry per item-site with ``item_code``, ``site_code`` and the same
measures, ordered by ``signal_total`` descending, then ``item_code``
and ``site_code``.  The existing accuracy answer gains no field.

The proof covers, in one test, against the test database (demand
events and snapshot rows inserted for the test, the way
``test_issue382_freeform.py`` does — one direct insert per item-site
bucket, ``signal_quantity`` set, one transaction per snapshot date,
origin ``live``; every bucket's statistical quantity 100.0, method
``mean``, site ``SITE-980``), and runs entirely as a NON-ADMIN (role
"user"):

* a signal that helped — one bucket, quantity 100, effective 130,
  signal 30, actual 125: the new route reads ``before_wape`` 0.2,
  ``after_wape`` 0.04 and ``value_added`` 0.16, and
  ``GET /api/forecast/accuracy`` over the same data reads
  ``value_added`` 0, ``overridden_buckets`` 0 and ``effective_wape``
  0.04;
* a signal that hurt — one bucket, quantity 100, effective 130,
  signal 30, actual 100: the new route reads ``value_added`` -0.3;
* an override and a signal together — one bucket, quantity 100,
  effective 110, signal 30, 30 override days, actual 100: the new
  route reads ``before_total`` 80, ``before_wape`` 0.2,
  ``after_wape`` 0.1 and ``value_added`` 0.1, and
  ``GET /api/forecast/accuracy`` reads ``value_added`` -0.2 and
  ``effective_wape`` 0.1, so the overrides' effect and the
  signals' effect add up to ``wape`` minus ``effective_wape``;
* left out — a bucket with signal 0, one with signal null and a
  signal bucket that has not completed do not appear in the new
  route, and an older row (``effective_quantity`` null beside a
  stored ``override_days`` of 5, ``signal_quantity`` null) reads in
  ``GET /api/forecast/accuracy`` exactly as it does today;
* no basis to judge — a compared signal bucket with no demand gives
  ``actual_total`` 0, the ratios null, with status 200;
* the answer's shape — two item-sites ordered by ``signal_total``
  descending;
* ``origin=backfill`` answers nothing compared; a malformed
  ``as_of`` or ``origin`` answers 422; with no token the route
  answers 401;
* one query — one call of the route runs one SQL statement, counted
  through the normal connection path (the production code does not
  swap, patch or rebind ``psycopg2.connect``).
"""

import inspect
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from fastapi.testclient import TestClient

import app.db.connection as connection_module
from app.db.connection import get_connection
from app.main import app

# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
LAST = date(2026, 8, 31)
LAST_STR = LAST.isoformat()
# The main snapshot date: bucket 1 runs 2026-08-02 through LAST, and
# bucket 3 (2026-10-01 through 2026-10-30) has not completed by it.
SNAP = date(2026, 8, 1)
# The shape group's snapshot date: its one bucket runs 2026-07-02
# through 2026-07-31, so an as-of of 2026-08-01 completes it while
# leaving the main group's bucket 1 (ending LAST) not completed.
SNAP_SHAPE = date(2026, 7, 1)
SHAPE_AS_OF = date(2026, 8, 1)

SITE = "SITE-980"
# The signal moved the forecast nearer the actuals.
ITEM_HELPED = "ITEM-980-A"
# The signal moved the forecast further away.
ITEM_HURT = "ITEM-980-B"
# An override and a signal cover the same bucket.
ITEM_BOTH = "ITEM-980-C"
# A stored signal of 0 — left out of the new route.
ITEM_SIG0 = "ITEM-980-D"
# A null stored signal — left out of the new route.
ITEM_SIGNULL = "ITEM-980-E"
# A signal bucket that has not completed — left out of both routes.
ITEM_PENDING = "ITEM-980-F"
# Stored the old way: effective_quantity and signal_quantity null,
# a stored override_days of 5 that must be ignored.
ITEM_LEGACY = "ITEM-980-G"
# A signal bucket with no demand at all — no basis to judge.
ITEM_DRY = "ITEM-980-H"
# The shape group: signal totals 50 and 20.
ITEM_SHAPE1 = "ITEM-980-S1"
ITEM_SHAPE2 = "ITEM-980-S2"

# Every statistical quantity of the proof (a constant history).
QTY = 100.0


def _seed_demand() -> None:
    """Demand events for the actuals, all inside the main bucket 1.

    helped 125.0 (75.0 mid-month and 50.0 on the last day, making
    LAST the shared default as-of date); hurt, both, sig0, signull
    and legacy 100.0 each on 2026-08-15; pending, dry and the shape
    group have no demand events at all, so their actuals are 0.
    """
    events = [
        (
            "id-de-980-a1",
            "de-980-a1",
            ITEM_HELPED,
            SITE,
            75.0,
            date(2026, 8, 10).isoformat(),
            "shipment",
            "test",
        ),
        (
            "id-de-980-a2",
            "de-980-a2",
            ITEM_HELPED,
            SITE,
            50.0,
            LAST_STR,
            "shipment",
            "test",
        ),
    ]
    for tag, item in (
        ("b", ITEM_HURT),
        ("c", ITEM_BOTH),
        ("d", ITEM_SIG0),
        ("e", ITEM_SIGNULL),
        ("g", ITEM_LEGACY),
    ):
        events.append(
            (
                f"id-de-980-{tag}",
                f"de-980-{tag}",
                item,
                SITE,
                QTY,
                date(2026, 8, 15).isoformat(),
                "shipment",
                "test",
            )
        )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                events,
            )
        conn.commit()
    finally:
        conn.close()


def _snapshot_rows(as_of: date, buckets: dict[str, tuple]) -> None:
    """One direct insert per (item, bucket), in one transaction.

    ``buckets`` maps an item to (bucket_index, effective_quantity,
    override_days, signal_quantity); the statistical quantity is
    always QTY, the 30-day bounds fall out of the snapshot date.
    """
    rows = []
    for item, (index, effective, override_days, signal) in buckets.items():
        rows.append(
            (
                f"fs-980-{as_of.isoformat()}-{item}-{index}",
                as_of,
                "live",
                item,
                SITE,
                index,
                as_of + timedelta(days=30 * (index - 1) + 1),
                as_of + timedelta(days=30 * index),
                QTY,
                effective,
                override_days,
                signal,
                "mean",
                "smooth",
                13,
            )
        )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO forecast_snapshots "
                "(id, as_of, origin, item_code, site_code, bucket_index, "
                "bucket_start, bucket_end, quantity, effective_quantity, "
                "override_days, signal_quantity, method, demand_class, "
                "history_buckets) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def _stored_rows() -> list[dict]:
    """Every stored snapshot row of the proof, deterministically ordered."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM forecast_snapshots WHERE site_code = %s "
                "ORDER BY as_of, item_code, bucket_index",
                (SITE,),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


# ----------------------------------------------------------------------
# The hand-computed signal measures (GET /api/forecast/accuracy/
# signals).  Compared buckets (signal above 0, completed by LAST):
#
#   helped:  before 100 (130-30) vs 125 -> 25 ; after 130 vs 125 -> 5
#   hurt:    before 100 vs 100  -> 0  ; after 130 vs 100 -> 30
#   both:    before  80 (110-30) vs 100 -> 20 ; after 110 vs 100 -> 10
#   dry:     before 100 vs 0 -> 100 ; after 130 vs 0 -> 130
#   shape1:  before 100 vs 0 -> 100 ; after 150 vs 0 -> 150
#   shape2:  before 100 vs 0 -> 100 ; after 120 vs 0 -> 120
#
#   overall (default as-of): actual 325, before 580, after 770,
#     signal 190, before_wape 345/325, after_wape 445/325,
#     value_added (345-445)/325
#
#   shape group (as-of 2026-08-01): actual 0, before 200, after 270,
#     signal 70, the ratios null
# ----------------------------------------------------------------------


def _signal_site(
    item: str,
    before: float,
    after: float,
    signal: float,
    actual: float,
) -> dict:
    if actual:
        before_wape = abs(before - actual) / actual
        after_wape = abs(after - actual) / actual
        value_added = before_wape - after_wape
    else:
        before_wape = after_wape = value_added = None
    return {
        "item_code": item,
        "site_code": SITE,
        "compared_buckets": 1,
        "actual_total": actual,
        "before_total": before,
        "after_total": after,
        "signal_total": signal,
        "before_wape": before_wape,
        "after_wape": after_wape,
        "value_added": value_added,
    }


_SIGNAL_SITES = {
    ITEM_HELPED: _signal_site(ITEM_HELPED, 100.0, 130.0, 30.0, 125.0),
    ITEM_HURT: _signal_site(ITEM_HURT, 100.0, 130.0, 30.0, 100.0),
    ITEM_BOTH: _signal_site(ITEM_BOTH, 80.0, 110.0, 30.0, 100.0),
    ITEM_DRY: _signal_site(ITEM_DRY, 100.0, 130.0, 30.0, 0.0),
    ITEM_SHAPE1: _signal_site(ITEM_SHAPE1, 100.0, 150.0, 50.0, 0.0),
    ITEM_SHAPE2: _signal_site(ITEM_SHAPE2, 100.0, 120.0, 20.0, 0.0),
}


def _signal_overall(
    as_of: str,
    compared: int,
    actual: float,
    before: float,
    after: float,
    signal: float,
    before_abs: float,
    after_abs: float,
) -> dict:
    if actual:
        before_wape = before_abs / actual
        after_wape = after_abs / actual
        value_added = before_wape - after_wape
    else:
        before_wape = after_wape = value_added = None
    return {
        "as_of": as_of,
        "compared_buckets": compared,
        "actual_total": actual,
        "before_total": before,
        "after_total": after,
        "signal_total": signal,
        "before_wape": before_wape,
        "after_wape": after_wape,
        "value_added": value_added,
    }


# Default as-of: the main group (helped, hurt, both, dry) and the
# shape group, ordered by signal_total descending, then item_code.
_SIGNAL_DEFAULT = {
    **_signal_overall(LAST_STR, 6, 325.0, 580.0, 770.0, 190.0, 345.0, 445.0),
    "item_sites": [
        _SIGNAL_SITES[ITEM_SHAPE1],
        _SIGNAL_SITES[ITEM_HELPED],
        _SIGNAL_SITES[ITEM_HURT],
        _SIGNAL_SITES[ITEM_BOTH],
        _SIGNAL_SITES[ITEM_DRY],
        _SIGNAL_SITES[ITEM_SHAPE2],
    ],
}
# The shape group alone: two item-sites by signal_total descending,
# no demand at all — no basis to judge.
_SIGNAL_SHAPE = {
    **_signal_overall(
        SHAPE_AS_OF.isoformat(), 2, 0.0, 200.0, 270.0, 70.0, 200.0, 270.0
    ),
    "item_sites": [
        _SIGNAL_SITES[ITEM_SHAPE1],
        _SIGNAL_SITES[ITEM_SHAPE2],
    ],
}
# Nothing compared: counts and totals 0, the ratios null.
_SIGNAL_EMPTY = {
    **_signal_overall(LAST_STR, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "item_sites": [],
}

# ----------------------------------------------------------------------
# The hand-computed accuracy measures (GET /api/forecast/accuracy).
# Compared buckets (completed by LAST, whatever the signal):
#
#   helped:  f 100 vs 125 -> 25 ; eff 130 -> 5  ; before-sig 100 -> 25
#   hurt:    f 100 vs 100 -> 0  ; eff 130 -> 30 ; before-sig 100 -> 0
#   both:    f 100 vs 100 -> 0  ; eff 110 -> 10 ; before-sig 80 -> 20,
#            1 overridden bucket
#   sig0:    f 100 vs 100 -> 0  ; eff 100 -> 0  ; before-sig 100 -> 0
#   signull: f 100 vs 100 -> 0  ; eff 100 -> 0  ; before-sig 100 -> 0
#   legacy:  f 100 vs 100 -> 0  ; eff null reads as 100, NOT
#            overridden (the stored override_days 5 is ignored)
#   dry:     f 100 vs 0 -> 100  ; eff 130 -> 130 ; before-sig 100 -> 100
#   shape1:  f 100 vs 0 -> 100  ; eff 150 -> 150 ; before-sig 100 -> 100
#   shape2:  f 100 vs 0 -> 100  ; eff 120 -> 120 ; before-sig 100 -> 100
#
#   overall (9 buckets): forecast 900, actual 625, wape 325/625,
#     effective 1070, effective_wape 445/625, overridden 1,
#     value_added (325-345)/625 — the overrides' effect, measured
#     alone (the before-signals error 345 = 25+0+20+0+0+0+100+100+100)
# ----------------------------------------------------------------------


def _accuracy_site(
    item: str,
    effective: float,
    signal: float,
    actual: float,
    overridden: int,
) -> dict:
    if actual:
        wape = abs(QTY - actual) / actual
        bias = (QTY - actual) / actual
        effective_wape = abs(effective - actual) / actual
        effective_bias = (effective - actual) / actual
        value_added = wape - abs(effective - signal - actual) / actual
    else:
        wape = bias = effective_wape = effective_bias = value_added = None
    return {
        "item_code": item,
        "site_code": SITE,
        "method": "mean",
        "compared_buckets": 1,
        "forecast_total": QTY,
        "actual_total": actual,
        "wape": wape,
        "bias": bias,
        "effective_total": effective,
        "effective_wape": effective_wape,
        "effective_bias": effective_bias,
        "overridden_buckets": overridden,
        "value_added": value_added,
    }


def _accuracy_overall() -> dict:
    forecast = 9 * QTY
    effective = 1070.0
    actual = 625.0
    # The same expressions the measures use: value_added is wape
    # minus the before-signals error over the actuals.
    wape = 325.0 / actual
    before_signal_wape = 345.0 / actual
    return {
        "forecast_total": forecast,
        "actual_total": actual,
        "wape": wape,
        "bias": (forecast - actual) / actual,
        "effective_total": effective,
        "effective_wape": 445.0 / actual,
        "effective_bias": (effective - actual) / actual,
        "overridden_buckets": 1,
        "value_added": wape - before_signal_wape,
    }


_ACCURACY_SITES = {
    ITEM_HELPED: _accuracy_site(ITEM_HELPED, 130.0, 30.0, 125.0, 0),
    ITEM_HURT: _accuracy_site(ITEM_HURT, 130.0, 30.0, 100.0, 0),
    ITEM_BOTH: _accuracy_site(ITEM_BOTH, 110.0, 30.0, 100.0, 1),
    ITEM_SIG0: _accuracy_site(ITEM_SIG0, 100.0, 0.0, 100.0, 0),
    ITEM_SIGNULL: _accuracy_site(ITEM_SIGNULL, 100.0, 0.0, 100.0, 0),
    ITEM_LEGACY: _accuracy_site(ITEM_LEGACY, 100.0, 0.0, 100.0, 0),
    ITEM_DRY: _accuracy_site(ITEM_DRY, 130.0, 30.0, 0.0, 0),
    ITEM_SHAPE1: _accuracy_site(ITEM_SHAPE1, 150.0, 50.0, 0.0, 0),
    ITEM_SHAPE2: _accuracy_site(ITEM_SHAPE2, 120.0, 20.0, 0.0, 0),
}
_ACCURACY_OVERALL = _accuracy_overall()
_ACCURACY_DEFAULT = {
    "as_of": LAST_STR,
    "compared_buckets": 9,
    "overall": _ACCURACY_OVERALL,
    "by_horizon": [
        {"bucket_index": 1, "compared_buckets": 9, **_ACCURACY_OVERALL},
    ],
    # Ordered by actual_total descending, then item_code, site_code.
    "item_sites": [
        _ACCURACY_SITES[ITEM_HELPED],
        _ACCURACY_SITES[ITEM_HURT],
        _ACCURACY_SITES[ITEM_BOTH],
        _ACCURACY_SITES[ITEM_SIG0],
        _ACCURACY_SITES[ITEM_SIGNULL],
        _ACCURACY_SITES[ITEM_LEGACY],
        _ACCURACY_SITES[ITEM_DRY],
        _ACCURACY_SITES[ITEM_SHAPE1],
        _ACCURACY_SITES[ITEM_SHAPE2],
    ],
}


# ----------------------------------------------------------------------
# Counting the SQL statements of a call, through the normal
# connection path (the same wrapper the #374 proof uses).
# ----------------------------------------------------------------------


class _CountingCursor:
    """A cursor that counts its execute calls, delegating the rest."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def execute(self, sql, *args, **kwargs):
        self._counter[0] += 1
        self._queries.append(sql)
        return self._real.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _CountingConnection:
    """A connection whose cursors count their statement calls."""

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


def test_issue980_freeform(client, user_headers):
    _seed_demand()
    _snapshot_rows(
        SNAP,
        {
            ITEM_HELPED: (1, 130.0, 0, 30.0),
            ITEM_HURT: (1, 130.0, 0, 30.0),
            ITEM_BOTH: (1, 110.0, 30, 30.0),
            ITEM_SIG0: (1, 100.0, 0, 0.0),
            ITEM_SIGNULL: (1, 100.0, 0, None),
            ITEM_PENDING: (3, 130.0, 0, 30.0),
            ITEM_LEGACY: (1, None, 5, None),
            ITEM_DRY: (1, 130.0, 0, 30.0),
        },
    )
    _snapshot_rows(
        SNAP_SHAPE,
        {
            ITEM_SHAPE1: (1, 150.0, 0, 50.0),
            ITEM_SHAPE2: (1, 120.0, 0, 20.0),
        },
    )
    # The whole proof runs as a NON-ADMIN (role "user"); ``client`` is
    # requested for its per-test table truncation only.
    viewer = TestClient(app, headers=user_headers)

    # ------------------------------------------------------------------
    # The stored rows: 10 buckets with signal_quantity set (null on
    # the two old rows), including the not-completed signal bucket
    # and the legacy row with a stored override_days of 5.
    # ------------------------------------------------------------------
    rows = _stored_rows()
    assert len(rows) == 10, len(rows)
    assert all(row["origin"] == "live" for row in rows)
    assert all(row["quantity"] == QTY for row in rows)
    by_key = {(r["item_code"], r["bucket_index"]): r for r in rows}
    assert by_key[(ITEM_HELPED, 1)]["signal_quantity"] == 30.0
    assert by_key[(ITEM_HELPED, 1)]["effective_quantity"] == 130.0
    assert by_key[(ITEM_BOTH, 1)]["signal_quantity"] == 30.0
    assert by_key[(ITEM_BOTH, 1)]["override_days"] == 30
    assert by_key[(ITEM_SIG0, 1)]["signal_quantity"] == 0.0
    assert by_key[(ITEM_SIGNULL, 1)]["signal_quantity"] is None
    assert by_key[(ITEM_PENDING, 3)]["signal_quantity"] == 30.0
    assert by_key[(ITEM_LEGACY, 1)]["signal_quantity"] is None
    assert by_key[(ITEM_LEGACY, 1)]["effective_quantity"] is None
    assert by_key[(ITEM_LEGACY, 1)]["override_days"] == 5
    assert by_key[(ITEM_DRY, 1)]["signal_quantity"] == 30.0
    assert by_key[(ITEM_SHAPE1, 1)]["signal_quantity"] == 50.0
    assert by_key[(ITEM_SHAPE2, 1)]["signal_quantity"] == 20.0

    # ------------------------------------------------------------------
    # The new route, no as_of — the shared default as-of date (LAST;
    # demand after it is unknown, not zero): every completed bucket
    # with a signal above 0 compared, hand-computed above.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/signals")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == _SIGNAL_DEFAULT

    # A signal that helped: before_wape 0.2, after_wape 0.04,
    # value_added 0.16.
    helped = body["item_sites"][1]
    assert helped["before_wape"] == 0.2
    assert helped["after_wape"] == 0.04
    assert helped["value_added"] == 0.16
    # A signal that hurt: value_added -0.3.
    hurt = body["item_sites"][2]
    assert hurt["value_added"] == -0.3
    # An override and a signal together: before_total 80,
    # before_wape 0.2, after_wape 0.1, value_added 0.1.
    both = body["item_sites"][3]
    assert both["before_total"] == 80.0
    assert both["before_wape"] == 0.2
    assert both["after_wape"] == 0.1
    assert both["value_added"] == 0.1
    # No basis to judge: the dry bucket's actual 0 gives the ratios
    # null, with status 200.
    dry = body["item_sites"][4]
    assert dry["actual_total"] == 0.0
    assert dry["before_wape"] is None
    assert dry["after_wape"] is None
    assert dry["value_added"] is None
    # Left out: signal 0, signal null, the not-completed bucket and
    # the legacy row do not appear (the entries above are all of
    # them, in signal_total descending order).
    items = [entry["item_code"] for entry in body["item_sites"]]
    assert items == [
        ITEM_SHAPE1,
        ITEM_HELPED,
        ITEM_HURT,
        ITEM_BOTH,
        ITEM_DRY,
        ITEM_SHAPE2,
    ]
    for absent in (ITEM_SIG0, ITEM_SIGNULL, ITEM_PENDING, ITEM_LEGACY):
        assert absent not in items

    # ------------------------------------------------------------------
    # The answer's shape: an as-of that completes only the shape
    # group's bucket — two item-sites, ordered by signal_total
    # descending.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/signals", params={"as_of": SHAPE_AS_OF})
    assert resp.status_code == 200, resp.text
    assert resp.json() == _SIGNAL_SHAPE

    # ------------------------------------------------------------------
    # The origin filter — backfill keeps nothing (nothing compared:
    # counts and totals 0, ratios null, item_sites empty); live keeps
    # everything here.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/signals", params={"origin": "backfill"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == _SIGNAL_EMPTY
    resp = viewer.get("/api/forecast/accuracy/signals", params={"origin": "live"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == _SIGNAL_DEFAULT

    # ------------------------------------------------------------------
    # 422 in FastAPI's validation envelope for a malformed as_of or
    # origin, as on GET /api/forecast/accuracy.
    # ------------------------------------------------------------------
    for params in ({"as_of": "not-a-date"}, {"origin": "sideways"}):
        resp = viewer.get("/api/forecast/accuracy/signals", params=params)
        assert resp.status_code == 422, (params, resp.text)
        assert "detail" in resp.json()

    # ------------------------------------------------------------------
    # With no token the route answers 401 (any signed-in user is
    # admitted; none at all is refused by the app's auth gate).
    # ------------------------------------------------------------------
    anonymous = TestClient(app)
    assert anonymous.get("/api/forecast/accuracy/signals").status_code == 401

    # ------------------------------------------------------------------
    # One query: one call of the route runs one SQL statement,
    # counted through the normal connection path; the production
    # code does not swap, patch or rebind psycopg2.connect.
    # ------------------------------------------------------------------
    response, statements, _ = _measure(
        lambda: viewer.get("/api/forecast/accuracy/signals", params={"as_of": LAST_STR})
    )
    assert response.status_code == 200, response.text
    assert statements == 1, statements
    connection_source = inspect.getsource(connection_module)
    assert "psycopg2.connect" in connection_source
    assert "psycopg2.connect =" not in connection_source

    # ------------------------------------------------------------------
    # GET /api/forecast/accuracy over the same data: the existing
    # answer gains no field, and every measure is what it is with
    # the overrides' effect measured alone (the before-signals
    # error).
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == _ACCURACY_DEFAULT

    # The existing fields, and no new ones, at every level.
    for block in (body["overall"], *body["by_horizon"], *body["item_sites"]):
        keys = {
            "forecast_total",
            "actual_total",
            "wape",
            "bias",
            "effective_total",
            "effective_wape",
            "effective_bias",
            "overridden_buckets",
            "value_added",
        }
        if "bucket_index" in block:
            keys |= {"bucket_index", "compared_buckets"}
        if "item_code" in block:
            keys |= {"item_code", "site_code", "method", "compared_buckets"}
        assert set(block) == keys, block

    # A signal that helped: value_added 0 (no override — the
    # before-signals forecast is the statistical one),
    # overridden_buckets 0, effective_wape 0.04.
    helped = body["item_sites"][0]
    assert helped["value_added"] == 0.0
    assert helped["overridden_buckets"] == 0
    assert helped["effective_wape"] == 0.04
    # An override and a signal together: value_added -0.2,
    # effective_wape 0.1 — and the overrides' effect plus the
    # signals' effect add up to wape minus effective_wape.
    both = body["item_sites"][2]
    assert both["value_added"] == -0.2
    assert both["effective_wape"] == 0.1
    assert (
        both["value_added"] + _SIGNAL_SITES[ITEM_BOTH]["value_added"]
        == both["wape"] - both["effective_wape"]
    )
    # Left out: signal 0 and signal null read as no signal —
    # value_added 0, effective measures equal the statistical ones.
    for entry in (body["item_sites"][3], body["item_sites"][4]):
        assert entry["value_added"] == 0.0
        assert entry["effective_total"] == entry["forecast_total"]
        assert entry["effective_wape"] == entry["wape"]
        assert entry["effective_bias"] == entry["bias"]
    # The older row (effective_quantity null beside a stored
    # override_days of 5) reads exactly as it does today: effective
    # equal to statistical, NOT overridden, value_added 0.
    legacy = body["item_sites"][5]
    assert legacy["effective_total"] == legacy["forecast_total"]
    assert legacy["effective_wape"] == legacy["wape"]
    assert legacy["effective_bias"] == legacy["bias"]
    assert legacy["overridden_buckets"] == 0
    assert legacy["value_added"] == 0.0
    # No basis to judge: the dry bucket's actual 0 leaves the ratios
    # null (and the statistical ones null).
    dry = body["item_sites"][6]
    assert dry["actual_total"] == 0.0
    assert dry["wape"] is None
    assert dry["bias"] is None
    assert dry["effective_wape"] is None
    assert dry["effective_bias"] is None
    assert dry["value_added"] is None
    # The not-completed signal bucket does not appear in the existing
    # answer either.
    assert all(entry["item_code"] != ITEM_PENDING for entry in body["item_sites"])
    # The overrides' effect, measured alone, over everything compared:
    # wape minus the before-signals error over the actuals.
    overall = body["overall"]
    assert overall["wape"] == 325.0 / 625.0
    assert overall["value_added"] == overall["wape"] - 345.0 / 625.0
    assert overall["effective_wape"] == 445.0 / 625.0
    assert overall["overridden_buckets"] == 1
