"""Issue #1376 proof: the corrections' effect, measured alone.

``realised_accuracy`` (issue #336) and ``signal_accuracy`` (issue
#980) compare the completed buckets of the stored forecast
snapshots with what actually happened.  This issue adds
``GET /api/forecast/accuracy/corrections`` (any signed-in user,
``as_of`` and ``origin`` with the same types, default and 422
behaviour as the other two accuracy reads): the same completed-
bucket comparison restricted to the buckets with a stored
``proposed_quantity`` (a null proposal is left out), with
``before_total`` (the sum of each bucket's effective quantity —
the forecast before the corrections), ``after_total`` (the sum of
its proposed quantities), ``before_wape`` / ``after_wape`` (each
the sum of the absolute differences from the actuals, over
``actual_total``, null when it is 0), ``before_bias`` /
``after_bias`` (each ``before_total`` or ``after_total`` minus
``actual_total``, over ``actual_total``, null likewise) and
``value_added`` (``before_wape`` minus ``after_wape`` — positive
when the corrections would have moved the forecast nearer the
actuals) — ``as_of``, those measures and ``item_sites``, one entry
per item-site with ``item_code``, ``site_code`` and the same
measures, ordered by ``actual_total`` descending, then
``item_code`` and ``site_code``.  The two existing accuracy
answers gain no field.

The proof covers, in one test, against the test database (demand
events and snapshot rows inserted for the test, the way
``test_issue980_freeform.py`` does — one direct insert per item-
site bucket, ``proposed_quantity`` set, one transaction per
snapshot date; every bucket's statistical and effective quantity
100.0, method ``mean``, site ``SITE-COR``), and runs entirely as a
NON-ADMIN (role "user"):

* a correction that helped — item P1, one completed bucket,
  proposed 60, actual 50;
* a correction that hurt — item P2, one completed bucket,
  proposed 150, actual 100;
* no demand — item P4, one completed bucket, proposed 80, no
  demand in its range: ``actual_total`` 0, every ratio null, with
  status 200;
* left out — item P3's completed bucket with a null
  ``proposed_quantity``, and P1's completed-later bucket with a
  proposal, do not appear;
* the answer over the four items: ``compared_buckets`` 3,
  ``actual_total`` 150, ``before_total`` 300, ``after_total`` 290,
  ``before_wape`` 1.0, ``after_wape`` 140/150, ``before_bias`` 1.0,
  ``after_bias`` 140/150 and ``value_added`` 1.0 minus 140/150,
  the item-sites in the order P2, P1, P4 with the measures the
  hand-computation below names;
* a second snapshot date stored with origin ``backfill`` (a P1
  bucket, proposed 90, actual 100) is counted with no filter
  (``compared_buckets`` 4), left out by ``origin=live`` and is the
  only bucket counted with ``origin=backfill``;
* an ``as_of`` before every bucket end answers ``compared_buckets``
  0, the ratios null and ``item_sites`` empty;
* a malformed ``as_of`` or ``origin`` answers 422 in FastAPI's
  validation envelope; with no token the route answers 401;
* the existing reads over the same data answer exactly the keys
  they answer today, and no entry of either carries a
  ``proposed`` key.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
LAST = date(2026, 7, 31)
LAST_STR = LAST.isoformat()
# The main snapshot date: bucket 1 runs 2026-07-02 through LAST,
# and bucket 3 (2026-08-31 through 2026-09-29) has not completed
# by it.
SNAP = date(2026, 7, 1)
# The backfill snapshot date: its one P1 bucket runs 2026-06-02
# through 2026-06-30, completed by LAST.
SNAP_BACKFILL = date(2026, 6, 1)
# An as-of before every bucket end — nothing is compared at it.
EARLY = date(2026, 6, 1)
EARLY_STR = EARLY.isoformat()

SITE = "SITE-COR"
# A correction that moved the forecast nearer the actuals.
ITEM_HELPED = "P1"
# A correction that moved the forecast further away.
ITEM_HURT = "P2"
# A completed bucket with a null proposal — left out of the route.
ITEM_NO_PROPOSAL = "P3"
# A compared bucket with no demand at all — no basis to judge.
ITEM_DRY = "P4"

# Every statistical and effective quantity of the proof
# (a constant history).
QTY = 100.0


def _seed_demand() -> None:
    """Demand events for the actuals, in one transaction.

    P1: 50.0 on the last day (making LAST the shared default as-of
    date) and 100.0 in the backfill bucket's range; P2: 100.0
    mid-month; P3 and P4 have no demand events at all, so their
    actuals are 0.
    """
    events = [
        (
            "id-de-1376-p1a",
            "de-1376-p1a",
            ITEM_HELPED,
            SITE,
            50.0,
            LAST_STR,
            "shipment",
            "test",
        ),
        (
            "id-de-1376-p1b",
            "de-1376-p1b",
            ITEM_HELPED,
            SITE,
            100.0,
            date(2026, 6, 15).isoformat(),
            "shipment",
            "test",
        ),
        (
            "id-de-1376-p2",
            "de-1376-p2",
            ITEM_HURT,
            SITE,
            QTY,
            date(2026, 7, 15).isoformat(),
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
    as_of: date, origin: str, buckets: dict[tuple[str, int], float]
) -> None:
    """One direct insert per (item, bucket), in one transaction.

    ``buckets`` maps an (item, bucket_index) pair to its
    ``proposed_quantity``; the statistical and the effective
    quantity are always QTY, the 30-day bounds fall out of the
    snapshot date, and no bucket has an override day or a stored
    signal.
    """
    rows = []
    for (item, index), proposed in sorted(buckets.items()):
        rows.append(
            (
                f"fs-1376-{as_of.isoformat()}-{item}-{index}",
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
                proposed,
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
                "method, demand_class, history_buckets) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s, %s, %s)",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


# ----------------------------------------------------------------------
# The hand-computed correction measures
# (GET /api/forecast/accuracy/corrections).  Compared buckets —
# proposed_quantity not null, bucket_end on or before the as-of:
#
#   base (no backfill yet):
#     P2: before 100 vs 100 -> 0   ; after 150 vs 100 -> 50
#     P1: before 100 vs 50  -> 50  ; after 60 vs 50 -> 10
#     P4: before 100 vs 0   -> 100 ; after 80 vs 0 -> 80
#     overall: actual 150, before 300, after 290,
#       before_abs 150 -> wape 1.0, after_abs 140 -> wape 140/150,
#       before_bias 1.0, after_bias 140/150,
#       value_added 1.0 - 140/150
#
#   with the backfill bucket (P1: before 100 vs 100 -> 0,
#   after 90 vs 100 -> 10):
#     overall: actual 250, before 400, after 380,
#       before_abs 150 -> 0.6, after_abs 150 -> 0.6,
#       before_bias 0.6, after_bias 0.52, value_added 0
#     P1: actual 150, before 200, after 150, before_wape 50/150,
#       after_wape 20/150, before_bias 50/150, after_bias 0,
#       value_added 30/150
#
#   backfill alone: P1 actual 100, before 100, after 90,
#     before_wape 0, after_wape 0.1, before_bias 0, after_bias
#     -0.1, value_added 0.1
# ----------------------------------------------------------------------


def _correction_site(
    item: str,
    before: float,
    after: float,
    actual: float,
    before_abs: float,
    after_abs: float,
    compared: int = 1,
) -> dict:
    if actual:
        before_wape = before_abs / actual
        after_wape = after_abs / actual
        before_bias = (before - actual) / actual
        after_bias = (after - actual) / actual
        value_added = before_wape - after_wape
    else:
        before_wape = after_wape = before_bias = after_bias = value_added = None
    return {
        "item_code": item,
        "site_code": SITE,
        "compared_buckets": compared,
        "actual_total": actual,
        "before_total": before,
        "after_total": after,
        "before_wape": before_wape,
        "after_wape": after_wape,
        "before_bias": before_bias,
        "after_bias": after_bias,
        "value_added": value_added,
    }


def _correction_overall(
    as_of: str,
    compared: int,
    actual: float,
    before: float,
    after: float,
    before_abs: float,
    after_abs: float,
) -> dict:
    if actual:
        before_wape = before_abs / actual
        after_wape = after_abs / actual
        return {
            "as_of": as_of,
            "compared_buckets": compared,
            "actual_total": actual,
            "before_total": before,
            "after_total": after,
            "before_wape": before_wape,
            "after_wape": after_wape,
            "before_bias": (before - actual) / actual,
            "after_bias": (after - actual) / actual,
            "value_added": before_wape - after_wape,
        }
    return {
        "as_of": as_of,
        "compared_buckets": compared,
        "actual_total": actual,
        "before_total": before,
        "after_total": after,
        "before_wape": None,
        "after_wape": None,
        "before_bias": None,
        "after_bias": None,
        "value_added": None,
    }


# The live buckets only, ordered by actual_total descending, then
# item_code, site_code: P2 (100), P1 (50), P4 (0).  P3 — the null
# proposal — and P1's not-completed bucket are left out.
_CORR_BASE = {
    **_correction_overall(LAST_STR, 3, 150.0, 300.0, 290.0, 150.0, 140.0),
    "item_sites": [
        _correction_site(ITEM_HURT, 100.0, 150.0, 100.0, 0.0, 50.0),
        _correction_site(ITEM_HELPED, 100.0, 60.0, 50.0, 50.0, 10.0),
        _correction_site(ITEM_DRY, 100.0, 80.0, 0.0, 100.0, 80.0),
    ],
}
# The backfill bucket counted too: four buckets, P1 (actual 150,
# two buckets) ahead of P2 (100) and P4 (0).  P1's absolute errors
# are the per-bucket sums — 50 and 20 — not the total differences.
_CORR_ALL = {
    **_correction_overall(LAST_STR, 4, 250.0, 400.0, 380.0, 150.0, 150.0),
    "item_sites": [
        _correction_site(ITEM_HELPED, 200.0, 150.0, 150.0, 50.0, 20.0, compared=2),
        _correction_site(ITEM_HURT, 100.0, 150.0, 100.0, 0.0, 50.0),
        _correction_site(ITEM_DRY, 100.0, 80.0, 0.0, 100.0, 80.0),
    ],
}
# The backfill bucket alone.
_CORR_BACKFILL = {
    **_correction_overall(LAST_STR, 1, 100.0, 100.0, 90.0, 0.0, 10.0),
    "item_sites": [
        _correction_site(ITEM_HELPED, 100.0, 90.0, 100.0, 0.0, 10.0),
    ],
}
# An as-of before every bucket end: nothing compared.
_CORR_EMPTY = {
    **_correction_overall(EARLY_STR, 0, 0.0, 0.0, 0.0, 0.0, 0.0),
    "item_sites": [],
}

_CORR_KEYS = (
    "actual_total",
    "before_total",
    "after_total",
    "before_wape",
    "after_wape",
    "before_bias",
    "after_bias",
    "value_added",
)


def _assert_corrections(body: dict, expected: dict) -> None:
    """Key for key, every ratio through pytest.approx."""
    assert body["as_of"] == expected["as_of"], body["as_of"]
    assert body["compared_buckets"] == expected["compared_buckets"]
    for key in _CORR_KEYS:
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
        for key in _CORR_KEYS:
            if want[key] is None:
                assert got[key] is None, (got["item_code"], key, got[key])
            else:
                assert got[key] == pytest.approx(want[key]), (
                    got["item_code"],
                    key,
                    got[key],
                )


# ----------------------------------------------------------------------
# The hand-computed answers of the two existing reads over the same
# data (five buckets compared by LAST: P1 live, P1 backfill, P2, P3
# and P4; P1's bucket 3 not completed).  No bucket has an override
# day or a stored signal, so the effective figures are the
# statistical ones and the before-signals figures the same.
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
    "forecast_total": 5 * QTY,
    "actual_total": 250.0,
    "wape": 250.0 / 250.0,
    "bias": (5 * QTY - 250.0) / 250.0,
    "effective_total": 5 * QTY,
    "effective_wape": 250.0 / 250.0,
    "effective_bias": (5 * QTY - 250.0) / 250.0,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_ACCURACY_DEFAULT = {
    "as_of": LAST_STR,
    "compared_buckets": 5,
    "overall": _ACCURACY_OVERALL,
    "by_horizon": [
        {"bucket_index": 1, "compared_buckets": 5, **_ACCURACY_OVERALL},
    ],
    # Ordered by actual_total descending, then item_code, site_code.
    "item_sites": [
        _accuracy_site(ITEM_HELPED, 150.0, 2),
        _accuracy_site(ITEM_HURT, 100.0, 1),
        _accuracy_site(ITEM_NO_PROPOSAL, 0.0, 1),
        _accuracy_site(ITEM_DRY, 0.0, 1),
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


def _assert_no_proposed_key(obj) -> None:
    """No dict at any depth of the answer carries a ``proposed`` key."""
    if isinstance(obj, dict):
        assert "proposed" not in obj, obj
        for value in obj.values():
            _assert_no_proposed_key(value)
    elif isinstance(obj, list):
        for value in obj:
            _assert_no_proposed_key(value)


def test_issue1376_freeform(client, user_headers):
    _seed_demand()
    _snapshot_rows(
        SNAP,
        "live",
        {
            (ITEM_HELPED, 1): 60.0,
            # P1's second bucket: a proposal that has not completed
            # by LAST — left out of the route.
            (ITEM_HELPED, 3): 70.0,
            (ITEM_HURT, 1): 150.0,
            (ITEM_NO_PROPOSAL, 1): None,
            (ITEM_DRY, 1): 80.0,
        },
    )
    # The whole proof runs as a NON-ADMIN (role "user"); ``client``
    # is requested for its per-test table truncation only.
    viewer = TestClient(app, headers=user_headers)

    # ------------------------------------------------------------------
    # The stored rows: five live buckets — P1's proposal that has
    # not completed (bucket 3) and P3's null proposal included.
    # ------------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT item_code, bucket_index, origin, "
                "COALESCE(quantity, -1), COALESCE(effective_quantity, -1), "
                "proposed_quantity "
                "FROM forecast_snapshots WHERE site_code = %s "
                "ORDER BY item_code, bucket_index",
                (SITE,),
            )
            stored = cur.fetchall()
    finally:
        conn.close()
    assert stored == [
        (ITEM_HELPED, 1, "live", QTY, QTY, 60.0),
        (ITEM_HELPED, 3, "live", QTY, QTY, 70.0),
        (ITEM_HURT, 1, "live", QTY, QTY, 150.0),
        (ITEM_NO_PROPOSAL, 1, "live", QTY, QTY, None),
        (ITEM_DRY, 1, "live", QTY, QTY, 80.0),
    ], stored

    # ------------------------------------------------------------------
    # The new route, no as_of — the shared default as-of date (LAST;
    # demand after it is unknown, not zero): every completed bucket
    # with a stored proposal compared, hand-computed above.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/corrections")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["as_of"] == LAST_STR

    # The measures the issue names, ratios through pytest.approx.
    assert body["compared_buckets"] == 3
    assert body["actual_total"] == pytest.approx(150.0)
    assert body["before_total"] == pytest.approx(300.0)
    assert body["after_total"] == pytest.approx(290.0)
    assert body["before_wape"] == pytest.approx(1.0)
    assert body["after_wape"] == pytest.approx(140.0 / 150.0)
    assert body["before_bias"] == pytest.approx(1.0)
    assert body["after_bias"] == pytest.approx(140.0 / 150.0)
    assert body["value_added"] == pytest.approx(1.0 - 140.0 / 150.0)

    # Key for key with the hand-computed answer: P2, P1, P4 in
    # actual_total descending order; P3 — the null proposal — and
    # P1's not-completed bucket left out (P1 compares one bucket).
    _assert_corrections(body, _CORR_BASE)
    hurt, helped, dry = body["item_sites"]
    # A correction that hurt: value_added -0.5.
    assert hurt["before_wape"] == pytest.approx(0.0)
    assert hurt["after_wape"] == pytest.approx(0.5)
    assert hurt["before_bias"] == pytest.approx(0.0)
    assert hurt["after_bias"] == pytest.approx(0.5)
    assert hurt["value_added"] == pytest.approx(-0.5)
    # A correction that helped: value_added 0.8.
    assert helped["before_wape"] == pytest.approx(1.0)
    assert helped["after_wape"] == pytest.approx(0.2)
    assert helped["before_bias"] == pytest.approx(1.0)
    assert helped["after_bias"] == pytest.approx(0.2)
    assert helped["value_added"] == pytest.approx(0.8)
    assert helped["compared_buckets"] == 1
    # No demand: the ratios are null, with status 200.
    assert dry["actual_total"] == pytest.approx(0.0)
    assert dry["before_wape"] is None
    assert dry["after_wape"] is None
    assert dry["before_bias"] is None
    assert dry["after_bias"] is None
    assert dry["value_added"] is None
    assert [entry["item_code"] for entry in body["item_sites"]] == [
        ITEM_HURT,
        ITEM_HELPED,
        ITEM_DRY,
    ]

    # ------------------------------------------------------------------
    # A second snapshot date stored with origin backfill (a P1
    # bucket, proposed 90, actual 100): counted with no filter, left
    # out by origin=live, the only bucket counted with
    # origin=backfill.
    # ------------------------------------------------------------------
    _snapshot_rows(SNAP_BACKFILL, "backfill", {(ITEM_HELPED, 1): 90.0})

    resp = viewer.get("/api/forecast/accuracy/corrections")
    assert resp.status_code == 200, resp.text
    _assert_corrections(resp.json(), _CORR_ALL)

    resp = viewer.get("/api/forecast/accuracy/corrections", params={"origin": "live"})
    assert resp.status_code == 200, resp.text
    _assert_corrections(resp.json(), _CORR_BASE)

    resp = viewer.get(
        "/api/forecast/accuracy/corrections", params={"origin": "backfill"}
    )
    assert resp.status_code == 200, resp.text
    _assert_corrections(resp.json(), _CORR_BACKFILL)

    # ------------------------------------------------------------------
    # An as-of before every bucket end: nothing compared — counts
    # and totals 0, the ratios null, item_sites empty.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/corrections", params={"as_of": EARLY_STR})
    assert resp.status_code == 200, resp.text
    _assert_corrections(resp.json(), _CORR_EMPTY)

    # ------------------------------------------------------------------
    # 422 in FastAPI's validation envelope for a malformed as_of or
    # origin, as on the other two accuracy reads.
    # ------------------------------------------------------------------
    for params in ({"as_of": "not-a-date"}, {"origin": "nope"}):
        resp = viewer.get("/api/forecast/accuracy/corrections", params=params)
        assert resp.status_code == 422, (params, resp.text)
        assert "detail" in resp.json()

    # ------------------------------------------------------------------
    # With no token the route answers 401 (any signed-in user is
    # admitted; none at all is refused by the app's auth gate).
    # ------------------------------------------------------------------
    anonymous = TestClient(app)
    assert anonymous.get("/api/forecast/accuracy/corrections").status_code == 401

    # ------------------------------------------------------------------
    # GET /api/forecast/accuracy over the same data: exactly the
    # keys it answers today, every measure the hand-computed one,
    # and no entry carrying a ``proposed`` key.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == _ACCURACY_DEFAULT
    _assert_no_proposed_key(body)

    # ------------------------------------------------------------------
    # GET /api/forecast/accuracy/signals over the same data: no
    # stored signal anywhere — nothing compared, exactly the keys it
    # answers today, and no entry carrying a ``proposed`` key.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/forecast/accuracy/signals")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == _SIGNALS_EMPTY
    _assert_no_proposed_key(body)
