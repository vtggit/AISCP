"""Issue #336 proof: realised accuracy of forecast snapshots.

The proof covers, in one test, against the test database (demand
events inserted for the test):

* demand events for three item-sites, each with 400 days of constant
  1.0/day demand before the snapshot date; after the snapshot the
  "same" item-site keeps 1.0/day through the last demand day, the
  "doubling" item-site moves to 2.0/day, and the "stopped"
  item-site has no demand at all;
* a backfill snapshot captured with ``capture_snapshot`` for the date
  60 days before the last demand day (2026-08-01, the last demand
  day being 2026-09-30 — the shared default as-of date): 12 buckets
  of 30.0 each (method ``mean`` — 13 constant history buckets make
  every backtest score tie, and the tie goes to ``mean``) for each of
  the three item-sites, 36 rows;
* ``GET /api/forecast/accuracy`` (any signed-in user) with no
  ``as_of`` using the shared default as-of date (the latest demand
  day, so demand after it is unknown, not zero): exactly the two
  completed forecast buckets compared (the third — 2026-10-01
  through 2026-10-30 — has not completed and is left out),
  6 compared buckets, and the hand-computed measures: the
  unchanged item-site 2 compared buckets, ``wape`` 0 and ``bias`` 0;
  the doubling item-site forecast_total 60 and actual_total 120,
  ``wape`` 0.5 and ``bias`` -0.5; the stopped item-site
  actual_total 0 and null ``wape`` and ``bias``; ``overall``
  forecast_total 180, actual_total 180, ``wape`` 120/180 and
  ``bias`` 0; ``by_horizon`` with exactly horizons 1 and 2 present,
  each 3 compared buckets, forecast_total 90, actual_total 90,
  ``wape`` 60/90, ``bias`` 0;
* a partly elapsed bucket left out: with ``as_of`` five days before
  the last demand day only the first bucket has completed
  (3 compared buckets);
* the ``origin`` filter: ``backfill`` keeps everything here (the only
  snapshot is backfill) and ``live`` keeps nothing;
* the ``bucket_index`` filter: horizon 2 alone, and combined with
  ``origin``;
* the item-site order (actual_total descending, then item_code and
  site_code) and ``limit`` / ``offset`` paging ``item_sites`` only,
  with ``overall`` (and ``by_horizon``) unchanged;
* nothing to compare (an ``as_of`` before the first bucket completes)
  answering compared_buckets 0, measures 0, 0, null and null and both
  lists empty;
* 422 in FastAPI's validation envelope for a malformed ``as_of``,
  ``origin`` or ``bucket_index`` (and out-of-range ``limit`` /
  ``offset``), and 401 without a token.

The identity requirement is met at the API boundary (this issue adds
no frontend page): the whole proof runs as a NON-ADMIN (role "user",
the development token's ":user" form) and asserts no admin-only
control on the accuracy operation — the viewer's request renders the
data with 200, not 403 — and a second pass after a full reload (a
fresh TestClient on the plain development token, role "user" by
default) sees the same result, as does an admin (nothing different).
"""

import os
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.forecast.snapshots import capture_snapshot
from app.main import app

# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
LAST = date(2026, 9, 30)
LAST_STR = LAST.isoformat()
# The backfill snapshot date: 60 days before the last demand day.
SNAP = LAST - timedelta(days=60)
SNAP_STR = SNAP.isoformat()
# The first seeded demand day, 400 days before the snapshot date.
HIST_START = SNAP - timedelta(days=400)

ITEM_SAME, SITE_SAME = "ITEM-336-A", "SITE-336-A"
ITEM_DOUBLE, SITE_DOUBLE = "ITEM-336-B", "SITE-336-B"
ITEM_STOPPED, SITE_STOPPED = "ITEM-336-C", "SITE-336-C"

# One 30-day bucket of the constant pre-snapshot demand.
BUCKET = 30.0


def _seed() -> None:
    """Demand events for the three item-sites, all on fixed dates.

    * same: 1.0 every day from HIST_START through LAST — the demand
      stays the same after the snapshot;
    * doubling: 1.0 every day from HIST_START through SNAP, then 2.0
      every day from SNAP+1 through LAST — the demand doubles after
      the snapshot;
    * stopped: 1.0 every day from HIST_START through SNAP — no demand
      after the snapshot.
    """
    events = []

    def add(item: str, site: str, day: date, quantity: float, tag: str) -> None:
        events.append(
            (
                f"id-de-336-{tag}",
                f"de-336-{tag}",
                item,
                site,
                quantity,
                day.isoformat(),
                "shipment",
                "test",
            )
        )

    for offset in range((SNAP - HIST_START).days + 1):
        day = HIST_START + timedelta(days=offset)
        add(ITEM_SAME, SITE_SAME, day, 1.0, f"a{offset}")
        add(ITEM_DOUBLE, SITE_DOUBLE, day, 1.0, f"b{offset}")
        add(ITEM_STOPPED, SITE_STOPPED, day, 1.0, f"c{offset}")
    for offset in range((LAST - SNAP).days):
        day = SNAP + timedelta(days=offset + 1)
        add(ITEM_SAME, SITE_SAME, day, 1.0, f"a-post{offset}")
        add(ITEM_DOUBLE, SITE_DOUBLE, day, 2.0, f"b-post{offset}")

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


def _stored_rows(as_of: date) -> list[dict]:
    """Every stored snapshot row for one as-of date, deterministically ordered."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM forecast_snapshots WHERE as_of = %s "
                "ORDER BY item_code, site_code, bucket_index",
                (as_of,),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _assert_validation_envelope(resp) -> None:
    """A 422 in FastAPI's standard validation error envelope."""
    assert resp.status_code == 422, (resp.status_code, resp.text)
    body = resp.json()
    detail = body.get("detail")
    assert isinstance(detail, list) and detail, body
    for error in detail:
        assert isinstance(error, dict), body
        for key in ("type", "loc", "msg"):
            assert key in error, (key, body)


def _empty_result(as_of: str) -> dict:
    """The nothing-to-compare result: 0 compared, 0/0/null/null, empty lists."""
    return {
        "as_of": as_of,
        "compared_buckets": 0,
        "overall": {
            "forecast_total": 0,
            "actual_total": 0,
            "wape": None,
            "bias": None,
            "effective_total": 0,
            "effective_wape": None,
            "effective_bias": None,
            "overridden_buckets": 0,
            "value_added": None,
        },
        "by_horizon": [],
        "item_sites": [],
    }


# The hand-computed measures for the compared buckets. Every forecast
# bucket is 30.0 (mean of 13 constant 30.0 history buckets).
#
#   same:     b1 30 vs 30, b2 30 vs 30  -> 60, 60,  wape 0,   bias 0
#   doubling: b1 30 vs 60, b2 30 vs 60  -> 60, 120, wape 0.5, bias -0.5
#   stopped:  b1 30 vs 0,  b2 30 vs 0   -> 60, 0,   wape null, bias null
#
#   overall:  180, 180, wape (0+0+30+30+30+30)/180 = 120/180, bias 0
#   horizon 1: 90, 90,  wape (0+30+30)/90 = 60/90,  bias 0
#   horizon 2: 90, 90,  wape (0+30+30)/90 = 60/90,  bias 0
_SAME_SITES = {
    "item_code": ITEM_SAME,
    "site_code": SITE_SAME,
    "method": "mean",
    "compared_buckets": 2,
    "forecast_total": 2 * BUCKET,
    "actual_total": 2 * BUCKET,
    "wape": 0.0,
    "bias": 0.0,
    "effective_total": 2 * BUCKET,
    "effective_wape": 0.0,
    "effective_bias": 0.0,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_DOUBLE_SITES = {
    "item_code": ITEM_DOUBLE,
    "site_code": SITE_DOUBLE,
    "method": "mean",
    "compared_buckets": 2,
    "forecast_total": 2 * BUCKET,
    "actual_total": 4 * BUCKET,
    "wape": 0.5,
    "bias": -0.5,
    "effective_total": 2 * BUCKET,
    "effective_wape": 0.5,
    "effective_bias": -0.5,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_STOPPED_SITES = {
    "item_code": ITEM_STOPPED,
    "site_code": SITE_STOPPED,
    "method": "mean",
    "compared_buckets": 2,
    "forecast_total": 2 * BUCKET,
    "actual_total": 0.0,
    "wape": None,
    "bias": None,
    "effective_total": 2 * BUCKET,
    "effective_wape": None,
    "effective_bias": None,
    "overridden_buckets": 0,
    "value_added": None,
}
_OVERALL = {
    "forecast_total": 6 * BUCKET,
    "actual_total": 6 * BUCKET,
    "wape": (30.0 + 30.0 + 30.0 + 30.0) / (6 * BUCKET),
    "bias": 0.0,
    "effective_total": 6 * BUCKET,
    "effective_wape": (30.0 + 30.0 + 30.0 + 30.0) / (6 * BUCKET),
    "effective_bias": 0.0,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_HORIZON = {
    "compared_buckets": 3,
    "forecast_total": 3 * BUCKET,
    "actual_total": 3 * BUCKET,
    "wape": (30.0 + 30.0) / (3 * BUCKET),
    "bias": 0.0,
    "effective_total": 3 * BUCKET,
    "effective_wape": (30.0 + 30.0) / (3 * BUCKET),
    "effective_bias": 0.0,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_BASELINE = {
    "as_of": LAST_STR,
    "compared_buckets": 6,
    "overall": _OVERALL,
    "by_horizon": [
        {"bucket_index": 1, **_HORIZON},
        {"bucket_index": 2, **_HORIZON},
    ],
    # Ordered by actual_total descending, then item_code, site_code.
    "item_sites": [_DOUBLE_SITES, _SAME_SITES, _STOPPED_SITES],
}
# One horizon only (the partial-as-of case and the bucket_index case):
# doubling 30 vs 60, same 30 vs 30, stopped 30 vs 0.
_ONE_HORIZON_SITES = {
    "item_code": ITEM_DOUBLE,
    "site_code": SITE_DOUBLE,
    "method": "mean",
    "compared_buckets": 1,
    "forecast_total": BUCKET,
    "actual_total": 2 * BUCKET,
    "wape": 0.5,
    "bias": -0.5,
    "effective_total": BUCKET,
    "effective_wape": 0.5,
    "effective_bias": -0.5,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_ONE_HORIZON_OVERALL = {
    "forecast_total": 3 * BUCKET,
    "actual_total": 3 * BUCKET,
    "wape": (30.0 + 30.0) / (3 * BUCKET),
    "bias": 0.0,
    "effective_total": 3 * BUCKET,
    "effective_wape": (30.0 + 30.0) / (3 * BUCKET),
    "effective_bias": 0.0,
    "overridden_buckets": 0,
    "value_added": 0.0,
}
_ONE_HORIZON_ITEM_SITES = [
    _ONE_HORIZON_SITES,
    {
        "item_code": ITEM_SAME,
        "site_code": SITE_SAME,
        "method": "mean",
        "compared_buckets": 1,
        "forecast_total": BUCKET,
        "actual_total": BUCKET,
        "wape": 0.0,
        "bias": 0.0,
        "effective_total": BUCKET,
        "effective_wape": 0.0,
        "effective_bias": 0.0,
        "overridden_buckets": 0,
        "value_added": 0.0,
    },
    {
        "item_code": ITEM_STOPPED,
        "site_code": SITE_STOPPED,
        "method": "mean",
        "compared_buckets": 1,
        "forecast_total": BUCKET,
        "actual_total": 0.0,
        "wape": None,
        "bias": None,
        "effective_total": BUCKET,
        "effective_wape": None,
        "effective_bias": None,
        "overridden_buckets": 0,
        "value_added": None,
    },
]


def test_issue336_freeform(client, admin_headers, user_headers):
    _seed()
    # The whole proof runs as a NON-ADMIN (role "user"); ``client`` is
    # requested for its per-test table truncation only.
    viewer = TestClient(app, headers=user_headers)
    admin = TestClient(app, headers=admin_headers)
    anon = TestClient(app)

    # ------------------------------------------------------------------
    # AC-3: the backfill snapshot, captured with capture_snapshot for
    # the date 60 days before the last demand day — 12 buckets of
    # 30.0 (method mean) per item-site, 36 rows.
    # ------------------------------------------------------------------

    result = capture_snapshot(as_of=SNAP)
    assert result == {
        "as_of": SNAP,
        "origin": "backfill",
        "created": True,
        "item_sites": 3,
        "rows": 36,
    }
    rows = _stored_rows(SNAP)
    assert len(rows) == 36
    assert {(row["item_code"], row["site_code"]) for row in rows} == {
        (ITEM_SAME, SITE_SAME),
        (ITEM_DOUBLE, SITE_DOUBLE),
        (ITEM_STOPPED, SITE_STOPPED),
    }
    assert all(row["origin"] == "backfill" for row in rows)
    assert all(row["quantity"] == BUCKET for row in rows)
    assert all(row["method"] == "mean" for row in rows)
    # The third forecast bucket — stored with a forecast, but not
    # completed by the as-of date: it must stay out of the comparison.
    third = [row for row in rows if row["bucket_index"] == 3]
    assert len(third) == 3
    assert all(row["bucket_start"] == SNAP + timedelta(days=61) for row in third)
    assert all(row["bucket_end"] == SNAP + timedelta(days=90) for row in third)
    assert all(row["quantity"] == BUCKET for row in third)

    # ------------------------------------------------------------------
    # AC-1/AC-3: no as_of — the shared default as-of date (the last
    # demand day; demand after it is unknown, not zero) — exactly the
    # two completed buckets compared, hand-computed measures.
    # ------------------------------------------------------------------

    resp = viewer.get("/api/forecast/accuracy")
    assert resp.status_code == 200, resp.text
    assert resp.json() == _BASELINE

    # The third forecast bucket has not completed: 6 compared, not 9,
    # and by_horizon carries exactly horizons 1 and 2, in order.
    body = resp.json()
    assert body["compared_buckets"] == 6
    assert [entry["bucket_index"] for entry in body["by_horizon"]] == [1, 2]

    # ------------------------------------------------------------------
    # AC-1: a bucket that has only partly elapsed is left out — with
    # the as-of five days before the last demand day the second bucket
    # (ending on the last demand day) has not completed yet.
    # ------------------------------------------------------------------

    partial_as_of = LAST - timedelta(days=5)
    resp = viewer.get(
        "/api/forecast/accuracy", params={"as_of": partial_as_of.isoformat()}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["as_of"] == partial_as_of.isoformat()
    assert body["compared_buckets"] == 3
    assert body["overall"] == _ONE_HORIZON_OVERALL
    assert body["by_horizon"] == [
        {"bucket_index": 1, "compared_buckets": 3, **_ONE_HORIZON_OVERALL}
    ]
    assert body["item_sites"] == _ONE_HORIZON_ITEM_SITES

    # ------------------------------------------------------------------
    # AC-1: the origin filter — backfill keeps everything here (the
    # only stored snapshot is backfill), live keeps nothing.
    # ------------------------------------------------------------------

    resp = viewer.get("/api/forecast/accuracy", params={"origin": "backfill"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == _BASELINE

    resp = viewer.get("/api/forecast/accuracy", params={"origin": "live"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == _empty_result(LAST_STR)

    # ------------------------------------------------------------------
    # AC-1: the bucket_index filter — one horizon only, combined with
    # the origin filter.
    # ------------------------------------------------------------------

    resp = viewer.get("/api/forecast/accuracy", params={"bucket_index": 2})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["as_of"] == LAST_STR
    assert body["compared_buckets"] == 3
    assert body["overall"] == {
        "forecast_total": 3 * BUCKET,
        "actual_total": 3 * BUCKET,
        "wape": (30.0 + 30.0) / (3 * BUCKET),
        "bias": 0.0,
        "effective_total": 3 * BUCKET,
        "effective_wape": (30.0 + 30.0) / (3 * BUCKET),
        "effective_bias": 0.0,
        "overridden_buckets": 0,
        "value_added": 0.0,
    }
    assert body["by_horizon"] == [{"bucket_index": 2, **_HORIZON}]
    assert body["item_sites"] == _ONE_HORIZON_ITEM_SITES

    resp = viewer.get(
        "/api/forecast/accuracy",
        params={"bucket_index": 1, "origin": "backfill"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["compared_buckets"] == 3
    assert body["overall"] == _ONE_HORIZON_OVERALL
    assert body["by_horizon"] == [
        {"bucket_index": 1, "compared_buckets": 3, **_ONE_HORIZON_OVERALL}
    ]
    assert body["item_sites"] == _ONE_HORIZON_ITEM_SITES

    # ------------------------------------------------------------------
    # AC-1: the item-site order, and limit / offset paging item_sites
    # only — overall (and by_horizon) always cover everything compared.
    # ------------------------------------------------------------------

    resp = viewer.get("/api/forecast/accuracy", params={"limit": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item_sites"] == [_DOUBLE_SITES]
    assert body["compared_buckets"] == 6
    assert body["overall"] == _OVERALL
    assert body["by_horizon"] == _BASELINE["by_horizon"]

    resp = viewer.get("/api/forecast/accuracy", params={"limit": 1, "offset": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item_sites"] == [_SAME_SITES]
    assert body["overall"] == _OVERALL

    resp = viewer.get("/api/forecast/accuracy", params={"limit": 2, "offset": 1})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item_sites"] == [_SAME_SITES, _STOPPED_SITES]
    assert body["overall"] == _OVERALL
    assert body["by_horizon"] == _BASELINE["by_horizon"]

    resp = viewer.get("/api/forecast/accuracy", params={"limit": 1000})
    assert resp.status_code == 200, resp.text
    assert resp.json()["item_sites"] == _BASELINE["item_sites"]

    resp = viewer.get("/api/forecast/accuracy", params={"offset": 3})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["item_sites"] == []
    assert body["compared_buckets"] == 6
    assert body["overall"] == _OVERALL

    for bad in ({"limit": 0}, {"limit": 1001}, {"offset": -1}):
        _assert_validation_envelope(viewer.get("/api/forecast/accuracy", params=bad))

    # ------------------------------------------------------------------
    # AC-1: nothing to compare — an as-of before the first bucket
    # completes: 0 compared, measures 0, 0, null and null, empty lists.
    # ------------------------------------------------------------------

    resp = viewer.get(
        "/api/forecast/accuracy",
        params={"as_of": (SNAP - timedelta(days=1)).isoformat()},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == _empty_result((SNAP - timedelta(days=1)).isoformat())

    # ------------------------------------------------------------------
    # AC-2: 422 for a malformed as_of, origin or bucket_index (and the
    # hostile forms), in FastAPI's validation envelope — never 500.
    # ------------------------------------------------------------------

    for bad in (
        {"as_of": "not-a-date"},
        {"as_of": "2026-02-30"},
        {"as_of": "99999999999999999999"},
        {"as_of": "a" * 1000},
        {"as_of": SNAP_STR + "\x00"},
        {"origin": "stale"},
        {"origin": "LIVE"},
        {"origin": ""},
        {"origin": "backfill\x00"},
        {"bucket_index": 0},
        {"bucket_index": 13},
        {"bucket_index": -1},
        {"bucket_index": "abc"},
        {"bucket_index": "9" * 30},
    ):
        _assert_validation_envelope(viewer.get("/api/forecast/accuracy", params=bad))

    # ------------------------------------------------------------------
    # AC-2: 401 without a token.
    # ------------------------------------------------------------------

    assert anon.get("/api/forecast/accuracy").status_code == 401

    # ------------------------------------------------------------------
    # Identity: no admin-only control on the accuracy operation — the
    # NON-ADMIN viewer (role "user") rendered the data with 200 above
    # (not 403), and a second pass after a full reload — a fresh
    # TestClient on the plain development token (role "user" by
    # default) — sees the same result, as does an admin (nothing
    # different).
    # ------------------------------------------------------------------

    reloaded = TestClient(
        app,
        headers={
            "Authorization": f"Bearer {os.environ.get('AUTH_DEV_TOKEN', 'dev-secret-token')}"
        },
    )
    resp = reloaded.get("/api/forecast/accuracy")
    assert resp.status_code == 200, resp.text
    assert resp.json() == _BASELINE
    resp = admin.get("/api/forecast/accuracy")
    assert resp.status_code == 200, resp.text
    assert resp.json() == _BASELINE
