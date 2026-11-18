"""Issue #1389 proof: the history basis, measured against the actuals.

``realised_accuracy`` (issue #336) compares the completed buckets of
the stored forecast snapshots with what actually happened.  This
issue adds ``GET /api/forecast/accuracy/basis`` (any signed-in user,
``as_of`` and ``origin`` with the types, defaults and 422 behaviour
of ``GET /api/forecast/accuracy/corrections``, and ``bucket_index``
with those of ``GET /api/forecast/accuracy`` — an integer from 1 to
12, absent by default): the same completed-bucket comparison
restricted to the buckets with a stored ``history_quantity`` (a null
history is left out), with ``actual_total``, ``history_total`` (the
sum of the history quantities), ``forecast_total`` (the sum of the
effective quantities — ``COALESCE(effective_quantity, quantity)``),
``history_wape`` / ``forecast_wape`` (each the sum of the absolute
differences from the actuals, over ``actual_total``, of the history
and the effective forecast, null when ``actual_total`` is 0),
``history_bias`` / ``forecast_bias`` (each ``history_total`` or
``forecast_total`` minus ``actual_total``, over ``actual_total``,
null likewise) and ``value_added`` (``history_wape`` minus
``forecast_wape`` — positive when the forecast basis was nearer the
actuals) — ``as_of``, ``compared_buckets``, those measures and
``item_sites``, one entry per item-site with ``item_code``,
``site_code``, ``compared_buckets`` and the same measures, ordered
by ``actual_total`` descending, then ``item_code`` and ``site_code``.
With nothing compared, ``compared_buckets`` and the totals are 0,
the ratios and ``value_added`` null, and ``item_sites`` empty.  The
other accuracy answers gain no field.

The proof covers, in one test, against the test database (demand
events and snapshot rows inserted for the test, the way
``test_issue1376_freeform.py`` does — one direct insert per bucket,
``history_quantity`` set, one transaction per snapshot date; every
bucket's statistical and effective quantity 100.0, method ``mean``,
site ``SITE-BAS``), and runs entirely as a NON-ADMIN (role "user"):

* one live snapshot: Q1's first bucket has history 50 and actual
  60, its second bucket history 70 and actual 80; Q2's first bucket
  has history 120 and actual 100; Q4's first bucket has history 30
  and no demand in its range; Q3's first bucket has a null history
  and is left out, and Q1's third bucket has not completed and is
  left out too;
* one backfill snapshot on another date: Q5's first bucket has
  history 40 and actual 50;
* one period ahead, live only (``bucket_index=1&origin=live``):
  ``compared_buckets`` 3, ``actual_total`` 160, ``history_total``
  200 and ``forecast_total`` 300, ``history_wape`` 60/160 and
  ``forecast_wape`` 140/160, ``history_bias`` 40/160 and
  ``forecast_bias`` 140/160 and ``value_added`` 60/160 minus 140/160
  (that is, -0.5), the item-sites in the order Q2, Q1, Q4: Q2
  ``history_wape`` 0.2, ``forecast_wape`` 0 and ``value_added`` 0.2;
  Q1 10/60, 40/60 and -0.5; Q4 ``actual_total`` 0 with every ratio
  null, at status 200; Q3 and Q5 do not appear;
* the filters: with no filter, ``compared_buckets`` 5, Q1's entry
  compares 2 and Q5 appears; ``bucket_index=2`` — only Q1's second
  bucket, ``compared_buckets`` 1, ``history_wape`` 0.125,
  ``forecast_wape`` 0.25 and ``value_added`` -0.125;
  ``origin=backfill`` — only Q5, ``history_wape`` 0.2,
  ``forecast_wape`` 1.0 and ``value_added`` -0.8; an ``as_of``
  before every bucket end — ``compared_buckets`` 0, the ratios null
  and ``item_sites`` empty;
* a malformed ``as_of``, a malformed ``origin``, and a
  ``bucket_index`` of 0, of 13 or of ``x`` each answer 422 in
  FastAPI's validation envelope; with no token the route answers
  401;
* the existing reads over the same data answer exactly the keys
  they answer today, and no entry of any of them carries a key
  that starts with ``history``.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
LAST = date(2026, 8, 30)
LAST_STR = LAST.isoformat()
# The live snapshot date: bucket 1 runs 2026-07-02 through
# 2026-07-31, bucket 2 runs 2026-08-01 through LAST, and bucket 3
# (2026-08-31 through 2026-09-29) has not completed by LAST.
SNAP = date(2026, 7, 1)
# The backfill snapshot date: its one Q5 bucket runs 2026-06-02
# through 2026-06-30, completed by LAST.
SNAP_BACKFILL = date(2026, 6, 1)
# An as-of before every bucket end — nothing is compared at it.
EARLY = date(2026, 5, 31)
EARLY_STR = EARLY.isoformat()

SITE = "SITE-BAS"
# Two completed live buckets (histories 50 and 70) plus a third that
# has not completed by LAST — left out of the route.
ITEM_Q1 = "Q1"
# One completed live bucket, history 120 against actual 100.
ITEM_Q2 = "Q2"
# One completed live bucket whose history is null — left out.
ITEM_Q3 = "Q3"
# One completed live bucket with no demand in its range.
ITEM_Q4 = "Q4"
# The backfill bucket, history 40 against actual 50.
ITEM_Q5 = "Q5"

# Every statistical and effective quantity of the proof
# (a constant forecast).
QTY = 100.0


def _seed_demand() -> None:
    """Demand events for the actuals, in one transaction.

    Q1: 60.0 in the first bucket's range and 80.0 on the last day
    (making LAST the shared default as-of date, in the second
    bucket's range); Q2: 100.0 in the first bucket's range; Q5: 50.0
    in the backfill bucket's range; Q3 and Q4 have no demand events
    at all, so their actuals are 0.
    """
    events = [
        (
            "id-de-1389-q1a",
            "de-1389-q1a",
            ITEM_Q1,
            SITE,
            60.0,
            date(2026, 7, 15).isoformat(),
            "shipment",
            "test",
        ),
        (
            "id-de-1389-q1b",
            "de-1389-q1b",
            ITEM_Q1,
            SITE,
            80.0,
            LAST_STR,
            "shipment",
            "test",
        ),
        (
            "id-de-1389-q2",
            "de-1389-q2",
            ITEM_Q2,
            SITE,
            100.0,
            date(2026, 7, 15).isoformat(),
            "shipment",
            "test",
        ),
        (
            "id-de-1389-q5",
            "de-1389-q5",
            ITEM_Q5,
            SITE,
            50.0,
            date(2026, 6, 15).isoformat(),
            "shipment",
            "test",
        ),
    ]
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


def _snapshot_rows(
    as_of: date, origin: str, buckets: dict[tuple[str, int], float | None]
) -> None:
    """One direct insert per (item, bucket), in one transaction.

    ``buckets`` maps an (item, bucket_index) pair to its
    ``history_quantity`` (None for the null-history bucket); the
    statistical and the effective quantity are always QTY, the
    30-day bounds fall out of the snapshot date, and no bucket has
    an override day, a stored signal or a stored proposal.
    """
    rows = []
    for (item, index), history in sorted(buckets.items()):
        rows.append(
            (
                f"fs-1389-{as_of.isoformat()}-{item}-{index}",
                as_of,
                origin,
                item,
                SITE,
                index,
                as_of + timedelta(days=30 * (index - 1) + 1),
                as_of + timedelta(days=30 * index),
                QTY,
                QTY,
                0,
                None,
                None,
                history,
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
                "override_days, signal_quantity, proposed_quantity, "
                "history_quantity, method, demand_class, history_buckets) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s, %s)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
# The hand-computed basis measures
# (GET /api/forecast/accuracy/basis).  Compared buckets —
# history_quantity not null, bucket_end on or before the as-of:
#
#   one period ahead, live (bucket 1, origin live):
#     Q2: history 120 vs 100 -> 20 ; forecast 100 vs 100 -> 0
#     Q1: history 50  vs 60  -> 10 ; forecast 100 vs 60 -> 40
#     Q4: history 30  vs 0   -> 30 ; forecast 100 vs 0 -> 100
#     overall: actual 160, history 200, forecast 300,
#       history_abs 60 -> wape 60/160, forecast_abs 140 -> wape
#       140/160, history_bias 40/160, forecast_bias 140/160,
#       value_added 60/160 - 140/160
#
#   no filter (all origins, all horizons):
#     plus Q1's second bucket (history 70 vs 80 -> 10, forecast
#     100 vs 80 -> 20) and Q5 (history 40 vs 50 -> 10, forecast 100
#     vs 50 -> 50):
#     overall: actual 290, history 310, forecast 500,
#       history_abs 80 -> wape 80/290, forecast_abs 210 -> wape
#       210/290, history_bias 20/290, forecast_bias 210/290,
#       value_added (80 - 210)/290
#     Q1: actual 140, history 120, forecast 200, history_abs 20,
#       forecast_abs 60; Q5: actual 50, history 40, forecast 100,
#       history_abs 10, forecast_abs 50
#
#   bucket 2 only: Q1's second bucket — actual 80, history 70,
#   forecast 100, history_abs 10 -> wape 0.125, forecast_abs 20 ->
#   wape 0.25, value_added -0.125
#
#   backfill only: Q5 — actual 50, history 40, forecast 100,
#   history_wape 0.2, forecast_wape 1.0, value_added -0.8
# ----------------------------------------------------------------------


def _basis_site(
    item: str,
    history: float,
    forecast: float,
    actual: float,
    history_abs: float,
    forecast_abs: float,
    compared: int = 1,
) -> dict:
    if actual:
        history_wape = history_abs / actual
        forecast_wape = forecast_abs / actual
        history_bias = (history - actual) / actual
        forecast_bias = (forecast - actual) / actual
        value_added = history_wape - forecast_wape
    else:
        history_wape = forecast_wape = None
        history_bias = forecast_bias = value_added = None
    return {
        "item_code": item,
        "site_code": SITE,
        "compared_buckets": compared,
        "actual_total": actual,
        "history_total": history,
        "forecast_total": forecast,
        "history_wape": history_wape,
        "forecast_wape": forecast_wape,
        "history_bias": history_bias,
        "forecast_bias": forecast_bias,
        "value_added": value_added,
    }


def _basis_overall(
    as_of: str,
    compared: int,
    actual: float,
    history: float,
    forecast: float,
    history_abs: float,
    forecast_abs: float,
) -> dict:
    if actual:
        history_wape = history_abs / actual
        forecast_wape = forecast_abs / actual
        return {
            "as_of": as_of,
            "compared_buckets": compared,
            "actual_total": actual,
            "history_total": history,
            "forecast_total": forecast,
            "history_wape": history_wape,
            "forecast_wape": forecast_wape,
            "history_bias": (history - actual) / actual,
            "forecast_bias": (forecast - actual) / actual,
            "value_added": history_wape - forecast_wape,
        }
    return {
        "as_of": as_of,
        "compared_buckets": compared,
        "actual_total": actual,
        "history_total": history,
        "forecast_total": forecast,
        "history_wape": None,
        "forecast_wape": None,
        "history_bias": None,
        "forecast_bias": None,
        "value_added": None,
    }


# One period ahead, live only, ordered by actual_total descending,
# then item_code, site_code: Q2 (100), Q1 (60), Q4 (0).  Q3 — the
# null history — and Q5 — the backfill bucket — left out.
_BASIS_P1_LIVE = {
    **_basis_overall(LAST_STR, 3, 160.0, 200.0, 300.0, 60.0, 140.0),
    "item_sites": [
        _basis_site(ITEM_Q2, 120.0, 100.0, 100.0, 20.0, 0.0),
        _basis_site(ITEM_Q1, 50.0, 100.0, 60.0, 10.0, 40.0),
        _basis_site(ITEM_Q4, 30.0, 100.0, 0.0, 30.0, 100.0),
    ],
}
# Everything with a stored history: five buckets, Q1 (actual 140,
# two buckets) ahead of Q2 (100), Q5 (50) and Q4 (0).  Q1's absolute
# errors are the per-bucket sums — 10 and 10 — not the total
# differences.
_BASIS_ALL = {
    **_basis_overall(LAST_STR, 5, 290.0, 310.0, 500.0, 80.0, 210.0),
    "item_sites": [
        _basis_site(ITEM_Q1, 120.0, 200.0, 140.0, 20.0, 60.0, compared=2),
        _basis_site(ITEM_Q2, 120.0, 100.0, 100.0, 20.0, 0.0),
        _basis_site(ITEM_Q5, 40.0, 100.0, 50.0, 10.0, 50.0),
        _basis_site(ITEM_Q4, 30.0, 100.0, 0.0, 30.0, 100.0),
    ],
}
# The second horizon only: Q1's second bucket.
_BASIS_P2 = {
    **_basis_overall(LAST_STR, 1, 80.0, 70.0, 100.0, 10.0, 20.0),
    "item_sites": [
        _basis_site(ITEM_Q1, 70.0, 100.0, 80.0, 10.0, 20.0),
    ],
}
# The backfill bucket alone.
_BASIS_BACKFILL = {
    **_basis_overall(LAST_STR, 1, 50.0, 40.0, 100.0, 10.0, 50.0),
    "item_sites": [
        _basis_site(ITEM_Q5, 40.0, 100.0, 50.0, 10.0, 50.0),
    ],
}
# An as-of before every bucket end: nothing compared.
_BASIS_EMPTY = {
    **_basis_overall(EARLY_STR, 0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "item_sites": [],
}

_BASIS_KEYS = (
    "actual_total",
    "history_total",
    "forecast_total",
    "history_wape",
    "forecast_wape",
    "history_bias",
    "forecast_bias",
    "value_added",
)


def _assert_basis(body: dict, expected: dict) -> None:
    """Key for key, every ratio through pytest.approx."""
    assert body["as_of"] == expected["as_of"], body["as_of"]
    assert body["compared_buckets"] == expected["compared_buckets"]
    for key in _BASIS_KEYS:
        got, want = body[key], expected[key]
        if want is None:
            assert got is None, (key, got)
        else:
            assert got == pytest.approx(want), (key, got)
    assert [
        (entry["item_code"], entry["site_code"]) for entry in body["item_sites"]
    ] == [
        (entry["item_code"], entry["site_code"]) for entry in expected["item_sites"]
    ], body[
        "item_sites"
    ]
    for got, want in zip(body["item_sites"], expected["item_sites"]):
        assert got["compared_buckets"] == want["compared_buckets"]
        for key in _BASIS_KEYS:
            if want[key] is None:
                assert got[key] is None, (got["item_code"], key, got[key])
            else:
                assert got[key] == pytest.approx(want[key]), (
                    got["item_code"],
                    key,
                    got[key],
                )


# ----------------------------------------------------------------------
# The hand-computed answers of the three existing reads over the
# same data (six buckets compared by LAST: Q1's two completed live
# buckets, Q2, Q3, Q4 and Q5's backfill bucket; Q1's bucket 3 not
# completed).  No bucket has an override day, a stored signal or a
# stored proposal, so the effective figures are the statistical
# ones, the before-signals figures the same, and the signal and
# correction reads compare nothing.
# ----------------------------------------------------------------------


def _accuracy_site(item: str, actual: float, compared: int) -> dict:
    forecast = compared * QTY
    if actual:
        wape = abs(forecast - actual) / actual
        bias = (forecast - actual) / actual
        value_added = 0.0
    else:
        wape = bias = value_added = None
    return {
        "item_code": item,
        "site_code": SITE,
        "method": "mean",
        "compared_buckets": compared,
        "forecast_total": forecast,
        "actual_total": actual,
        "wape": wape,
        "bias": bias,
        "effective_total": forecast,
        "effective_wape": wape,
        "effective_bias": bias,
        "overridden_buckets": 0,
        "value_added": value_added,
    }


_ACCURACY_OVERALL = {
    "forecast_total": 6 * QTY,
    "actual_total": 290.0,
    "wape": 310.0 / 290.0,
    "bias": (6 * QTY - 290.0) / 290.0,
    "effective_total": 6 * QTY,
    "effective_wape": 310.0 / 290.0,
    "effective_bias": (6 * QTY - 290.0) / 290.0,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_ACCURACY_HORIZON_1 = {
    "bucket_index": 1,
    "compared_buckets": 5,
    "forecast_total": 5 * QTY,
    "actual_total": 210.0,
    "wape": 290.0 / 210.0,
    "bias": (5 * QTY - 210.0) / 210.0,
    "effective_total": 5 * QTY,
    "effective_wape": 290.0 / 210.0,
    "effective_bias": (5 * QTY - 210.0) / 210.0,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_ACCURACY_HORIZON_2 = {
    "bucket_index": 2,
    "compared_buckets": 1,
    "forecast_total": QTY,
    "actual_total": 80.0,
    "wape": 20.0 / 80.0,
    "bias": 20.0 / 80.0,
    "effective_total": QTY,
    "effective_wape": 20.0 / 80.0,
    "effective_bias": 20.0 / 80.0,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_ACCURACY_DEFAULT = {
    "as_of": LAST_STR,
    "compared_buckets": 6,
    "overall": _ACCURACY_OVERALL,
    "by_horizon": [
        _ACCURACY_HORIZON_1,
        _ACCURACY_HORIZON_2,
    ],
    # Ordered by actual_total descending, then item_code, site_code.
    "item_sites": [
        _accuracy_site(ITEM_Q1, 140.0, 2),
        _accuracy_site(ITEM_Q2, 100.0, 1),
        _accuracy_site(ITEM_Q5, 50.0, 1),
        _accuracy_site(ITEM_Q3, 0.0, 1),
        _accuracy_site(ITEM_Q4, 0.0, 1),
    ],
}
# No stored signal at all: nothing is compared.
_SIGNALS_EMPTY = {
    "as_of": LAST_STR,
    "compared_buckets": 0,
    "actual_total": 0.0,
    "before_total": 0.0,
    "after_total": 0.0,
    "signal_total": 0.0,
    "before_wape": None,
    "after_wape": None,
    "value_added": None,
    "item_sites": [],
}
# No stored proposal at all: nothing is compared.
_CORRECTIONS_EMPTY = {
    "as_of": LAST_STR,
    "compared_buckets": 0,
    "actual_total": 0.0,
    "before_total": 0.0,
    "after_total": 0.0,
    "before_wape": None,
    "after_wape": None,
    "before_bias": None,
    "after_bias": None,
    "value_added": None,
    "item_sites": [],
}


def _assert_no_history_key(obj) -> None:
    """No dict at any depth of the answer carries a key that starts
    with ``history``."""
    if isinstance(obj, dict):
        for key in obj:
            assert not key.startswith("history"), obj
        for value in obj.values():
            _assert_no_history_key(value)
    elif isinstance(obj, list):
        for value in obj:
            _assert_no_history_key(value)


def test_issue1389_freeform(client, user_headers):
    _seed_demand()
    _snapshot_rows(
        SNAP,
        "live",
        {
            (ITEM_Q1, 1): 50.0,
            (ITEM_Q1, 2): 70.0,
            # Q1's third bucket: not completed by LAST — left out of
            # the route.
            (ITEM_Q1, 3): 90.0,
            (ITEM_Q2, 1): 120.0,
            # Q3's null history — left out of the route.
            (ITEM_Q3, 1): None,
            (ITEM_Q4, 1): 30.0,
        },
    )
    _snapshot_rows(SNAP_BACKFILL, "backfill", {(ITEM_Q5, 1): 40.0})
    # The whole proof runs as a NON-ADMIN (role "user"); ``client``
    # is requested for its per-test table truncation only.
    viewer = TestClient(app, headers=user_headers)

    # ------------------------------------------------------------------
    # The stored rows: seven live and backfill buckets — Q1's
    # not-completed bucket and Q3's null history included.
    # ------------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT item_code, bucket_index, origin, "
                "COALESCE(quantity, -1), COALESCE(effective_quantity, -1), "
                "history_quantity "
                "FROM forecast_snapshots WHERE site_code = %s "
                "ORDER BY item_code, bucket_index",
                (SITE,),
            )
            stored = cur.fetchall()
    finally:
        conn.close()
    assert stored == [
        (ITEM_Q1, 1, "live", QTY, QTY, 50.0),
        (ITEM_Q1, 2, "live", QTY, QTY, 70.0),
        (ITEM_Q1, 3, "live", QTY, QTY, 90.0),
        (ITEM_Q2, 1, "live", QTY, QTY, 120.0),
        (ITEM_Q3, 1, "live", QTY, QTY, None),
        (ITEM_Q4, 1, "live", QTY, QTY, 30.0),
        (ITEM_Q5, 1, "backfill", QTY, QTY, 40.0),
    ], stored

    # ------------------------------------------------------------------
    # One period ahead, live only — the shared default as-of date
    # (LAST; demand after it is unknown, not zero): the three
    # completed live first-horizon buckets with a stored history,
    # hand-computed above.
    # ------------------------------------------------------------------
    resp = viewer.get(
        "/api/forecast/accuracy/basis",
        params={"bucket_index": 1, "origin": "live"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["as_of"] == LAST_STR

    # The measures the issue names, ratios through pytest.approx.
    assert body["compared_buckets"] == 3
    assert body["actual_total"] == pytest.approx(160.0)
    assert body["history_total"] == pytest.approx(200.0)
    assert body["forecast_total"] == pytest.approx(300.0)
    assert body["history_wape"] == pytest.approx(60.0 / 160.0)
    assert body["forecast_wape"] == pytest.approx(140.0 / 160.0)
    assert body["history_bias"] == pytest.approx(40.0 / 160.0)
    assert body["forecast_bias"] == pytest.approx(140.0 / 160.0)
    assert body["value_added"] == pytest.approx(60.0 / 160.0 - 140.0 / 160.0)

    # Key for key with the hand-computed answer: Q2, Q1, Q4 in
    # actual_total descending order; Q3 — the null history — and Q5
    # — the backfill bucket — left out.
    _assert_basis(body, _BASIS_P1_LIVE)
    q2, q1, q4 = body["item_sites"]
    # The forecast basis was nearer Q2's actuals: value_added 0.2.
    assert q2["history_wape"] == pytest.approx(0.2)
    assert q2["forecast_wape"] == pytest.approx(0.0)
    assert q2["value_added"] == pytest.approx(0.2)
    # Q1: history 10/60 off, forecast 40/60 off — value_added -0.5.
    assert q1["history_wape"] == pytest.approx(10.0 / 60.0)
    assert q1["forecast_wape"] == pytest.approx(40.0 / 60.0)
    assert q1["value_added"] == pytest.approx(-0.5)
    assert q1["compared_buckets"] == 1
    # No demand: the ratios are null, with status 200.
    assert q4["actual_total"] == pytest.approx(0.0)
    assert q4["history_wape"] is None
    assert q4["forecast_wape"] is None
    assert q4["history_bias"] is None
    assert q4["forecast_bias"] is None
    assert q4["value_added"] is None
    assert [entry["item_code"] for entry in body["item_sites"]] == [
        ITEM_Q2,
        ITEM_Q1,
        ITEM_Q4,
    ]

    # ------------------------------------------------------------------
    # No filter: every stored history of every completed bucket —
    # five buckets, Q1's entry comparing two, Q5's backfill bucket
    # counted, Q3's null history left out.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/basis")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_basis(body, _BASIS_ALL)
    assert body["compared_buckets"] == 5
    by_item = {entry["item_code"]: entry for entry in body["item_sites"]}
    assert by_item[ITEM_Q1]["compared_buckets"] == 2
    assert ITEM_Q5 in by_item
    assert ITEM_Q3 not in by_item

    # ------------------------------------------------------------------
    # bucket_index=2: only Q1's second bucket.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/basis", params={"bucket_index": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_basis(body, _BASIS_P2)
    assert body["compared_buckets"] == 1
    assert body["history_wape"] == pytest.approx(0.125)
    assert body["forecast_wape"] == pytest.approx(0.25)
    assert body["value_added"] == pytest.approx(-0.125)
    assert [entry["item_code"] for entry in body["item_sites"]] == [ITEM_Q1]

    # ------------------------------------------------------------------
    # origin=backfill: only Q5's bucket.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/basis", params={"origin": "backfill"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    _assert_basis(body, _BASIS_BACKFILL)
    assert body["compared_buckets"] == 1
    assert body["history_wape"] == pytest.approx(0.2)
    assert body["forecast_wape"] == pytest.approx(1.0)
    assert body["value_added"] == pytest.approx(-0.8)
    assert [entry["item_code"] for entry in body["item_sites"]] == [ITEM_Q5]

    # ------------------------------------------------------------------
    # An as-of before every bucket end: nothing compared — counts
    # and totals 0, the ratios null, item_sites empty.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/basis", params={"as_of": EARLY_STR})
    assert resp.status_code == 200, resp.text
    _assert_basis(resp.json(), _BASIS_EMPTY)

    # ------------------------------------------------------------------
    # 422 in FastAPI's validation envelope for a malformed as_of, a
    # malformed origin, and a bucket_index of 0, of 13 or of x.
    # ------------------------------------------------------------------
    for params in (
        {"as_of": "not-a-date"},
        {"origin": "nope"},
        {"bucket_index": "0"},
        {"bucket_index": "13"},
        {"bucket_index": "x"},
    ):
        resp = viewer.get("/api/forecast/accuracy/basis", params=params)
        assert resp.status_code == 422, (params, resp.text)
        assert "detail" in resp.json()

    # ------------------------------------------------------------------
    # With no token the route answers 401 (any signed-in user is
    # admitted; none at all is refused by the app's auth gate).
    # ------------------------------------------------------------------
    anonymous = TestClient(app)
    assert anonymous.get("/api/forecast/accuracy/basis").status_code == 401

    # ------------------------------------------------------------------
    # GET /api/forecast/accuracy over the same data: exactly the
    # keys it answers today, every measure the hand-computed one,
    # and no entry carrying a key that starts with ``history``.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == _ACCURACY_DEFAULT
    _assert_no_history_key(body)

    # ------------------------------------------------------------------
    # GET /api/forecast/accuracy/signals over the same data: no
    # stored signal anywhere — nothing compared, exactly the keys it
    # answers today, and no entry carrying a key that starts with
    # ``history``.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/signals")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == _SIGNALS_EMPTY
    _assert_no_history_key(body)

    # ------------------------------------------------------------------
    # GET /api/forecast/accuracy/corrections over the same data: no
    # stored proposal anywhere — nothing compared, exactly the keys
    # it answers today, and no entry carrying a key that starts with
    # ``history``.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/corrections")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == _CORRECTIONS_EMPTY
    _assert_no_history_key(body)
