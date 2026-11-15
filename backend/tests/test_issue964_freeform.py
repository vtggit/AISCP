"""Issue #964 proof: the effective forecast counts the stored signals.

The proof covers, in one test, against the test database (demand
events inserted for the test so that two item-sites have a forecast):

* demand events for ITEM-A — 2.0 every day for 401 days ending on
  2026-09-30, the shared default as-of date (13 constant 60.0
  history buckets, class smooth, method mean, 12 forecast buckets of
  60.0 the first of which runs 2026-10-01 through 2026-10-30) — and
  for ITEM-B — 1.0 every day over the same 401 days (12 forecast
  buckets of 30.0);
* with no signal stored: every bucket's ``signal_quantity`` reads 0
  and ``effective_quantity`` its ``quantity`` exactly, every row's
  ``total_signal_quantity`` 0 and ``total_effective_forecast`` the
  sum of its buckets' ``effective_quantity``;
* one bucket: a signal of 300 units whose 30-day window is exactly
  the first forecast bucket gives that bucket ``signal_quantity`` 300
  and ``effective_quantity`` its ``quantity`` plus 300; the other
  buckets have ``signal_quantity`` 0 and ``effective_quantity`` their
  ``quantity``;
* across buckets: a signal of 60 units over a 20-day window spanning
  two buckets — 5 days in the first and 15 in the second — adds 15
  and 45;
* two signals covering the same days add together;
* an override wins: on ITEM-B, with a signal over the first bucket
  and an override covering the whole bucket, ``effective_quantity``
  equals the override's quantity and ``signal_quantity`` is 0; with
  the override covering 10 of the signal's 30 days (a second override
  stored directly), the signal adds only its 20 uncovered days'
  shares;
* a signal for one item-site leaves the other's buckets unchanged;
* the statistical forecast: ``quantity`` and ``override_days``
  identical with and without signals;
* the row totals: ``total_signal_quantity`` and
  ``total_effective_forecast`` on both ``GET /api/forecast`` and
  ``GET /api/forecast/by-key`` equal the bucket sums, at every step;
* withdrawn: after the withdrawals, the signals add nothing and the
  item-site's buckets are back to ``signal_quantity`` 0 and
  ``effective_quantity`` equal to ``quantity``;
* one read: ``effective_for_all`` over several rows calls the
  counted-signals query once (and the counted-override query once).

Every expected number is written out as arithmetic on the seeded
statistical quantities, never computed by calling the code under
test.
"""

from datetime import date, timedelta

import psycopg2
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.forecast.history import history_for_pair
from app.forecast.listing import build_forecast_row
from app.forecast.overrides import effective_for_all
from app.main import app

# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
LAST = date(2026, 9, 30)
# The first seeded demand day, 401 days back (13 whole 30-day
# history buckets, the 11 earliest days outside the window).
HIST_START = LAST - timedelta(days=400)

ITEM_A, SITE_A = "ITEM-964-A", "SITE-964-A"
ITEM_B, SITE_B = "ITEM-964-B", "SITE-964-B"

# The seeded statistical quantities: the daily demand times the 30
# days of a forecast bucket.
DAILY_A = 2.0
DAILY_B = 1.0
BUCKET_DAYS = 30
QTY_A = DAILY_A * BUCKET_DAYS  # 60.0 — ITEM-A's statistical bucket
QTY_B = DAILY_B * BUCKET_DAYS  # 30.0 — ITEM-B's statistical bucket

# The forecast buckets at the as-of date: bucket k runs from
# as_of + 30(k-1) + 1 through as_of + 30k.
B1_START = LAST + timedelta(days=1)  # 2026-10-01
B1_END = LAST + timedelta(days=30)  # 2026-10-30
B2_START = LAST + timedelta(days=31)  # 2026-10-31

# The signal windows.
S1_START, S1_END = B1_START, B1_END  # exactly bucket 1: 30 days
S2_START = B1_END - timedelta(days=4)  # 2026-10-26: 5 days in bucket 1
S2_END = B2_START + timedelta(days=14)  # 2026-11-14: 15 days in bucket 2
S2_DAYS_1 = 5
S2_DAYS_2 = 15
S2_DAYS = S2_DAYS_1 + S2_DAYS_2  # the 20-day window
S3_START, S3_END = B1_START, B1_END  # bucket 1 again: the same 30 days

# The signal quantities.
S1_QTY = 300.0  # 30.0 a day over its 30-day window
S2_QTY = 60.0  # 3.0 a day over its 20-day window
S3_QTY = 120.0  # 4.0 a day over its 30-day window
S4_QTY = 300.0  # ITEM-B: 10.0 a day over bucket 1

# ITEM-B's overrides: one covering the whole first bucket (through
# the API), one covering its first 10 days (stored directly).
O_FULL_START, O_FULL_END = B1_START, B1_END
O_FULL_QTY = 90.0
O_PART_DAYS = 10
O_PART_START, O_PART_END = B1_START, B1_START + timedelta(days=O_PART_DAYS - 1)
O_PART_QTY = 100.0  # 10.0 a day over its 10-day period


def _seed() -> None:
    """401 days of demand ending on the as-of date: 2.0/day for
    ITEM-A, 1.0/day for ITEM-B."""
    events = []
    for item, site, daily in ((ITEM_A, SITE_A, DAILY_A), (ITEM_B, SITE_B, DAILY_B)):
        for offset in range((LAST - HIST_START).days + 1):
            events.append(
                (
                    f"id-de-964-{item}-{offset}",
                    f"de-964-{item}-{offset}",
                    item,
                    site,
                    daily,
                    (HIST_START + timedelta(days=offset)).isoformat(),
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


def _signal_body(
    item: str,
    site: str,
    kind: str,
    start: date,
    end: date,
    quantity: float,
    reason: str,
) -> dict:
    return {
        "kind": kind,
        "item_code": item,
        "site_code": site,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "quantity": quantity,
        "reason": reason,
    }


def _check_totals(rows: list[dict]) -> None:
    """The row totals: the sums of the buckets' signal and effective
    quantities."""
    for row in rows:
        buckets = row["forecast"]
        assert row["total_signal_quantity"] == sum(
            bucket["signal_quantity"] for bucket in buckets
        ), row
        assert row["total_effective_forecast"] == sum(
            bucket["effective_quantity"] for bucket in buckets
        ), row


def _list(admin: TestClient) -> list[dict]:
    """GET /api/forecast, its totals checked."""
    resp = admin.get("/api/forecast")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    _check_totals(rows)
    return rows


def _by_key(admin: TestClient, item: str, site: str) -> dict:
    """GET /api/forecast/by-key, its totals checked."""
    resp = admin.get(
        "/api/forecast/by-key", params={"item_code": item, "site_code": site}
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()
    _check_totals([row])
    return row


def _statistical(row: dict) -> list[tuple[float, int]]:
    """The row's (quantity, override_days) per bucket — the statistical
    side, beside the effective forecast."""
    return [(b["quantity"], b["override_days"]) for b in row["forecast"]]


def _bucket_fields(row: dict) -> list[tuple[float, int, float, float]]:
    """The row's (quantity, override_days, signal_quantity,
    effective_quantity) per bucket."""
    return [
        (
            b["quantity"],
            b["override_days"],
            b["signal_quantity"],
            b["effective_quantity"],
        )
        for b in row["forecast"]
    ]


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
    """Run fn with psycopg2.connect wrapped; (result, SQL statements)."""
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
    return result, queries


def _untouched(row: dict, qty: float) -> None:
    """Every bucket of ``row``: no signal, no override, effective the
    quantity — the row is exactly what it was before any signal."""
    for bucket in row["forecast"]:
        assert bucket["quantity"] == qty
        assert bucket["override_days"] == 0
        assert bucket["signal_quantity"] == 0.0
        assert bucket["effective_quantity"] == qty


def test_issue964_freeform(client, admin_headers):
    _seed()
    admin = TestClient(app, headers=admin_headers)

    # ------------------------------------------------------------------
    # Baseline: with no signal stored, the new fields read 0 and the
    # effective forecast is the statistical one exactly.
    # ------------------------------------------------------------------
    rows = _list(admin)
    assert [(r["item_code"], r["site_code"]) for r in rows] == [
        (ITEM_A, SITE_A),
        (ITEM_B, SITE_B),
    ]
    by = {(r["item_code"], r["site_code"]): r for r in rows}
    a_row = by[(ITEM_A, SITE_A)]
    b_row = by[(ITEM_B, SITE_B)]
    assert a_row["total_forecast"] == QTY_A * 12
    assert b_row["total_forecast"] == QTY_B * 12
    _untouched(a_row, QTY_A)
    _untouched(b_row, QTY_B)
    assert a_row["total_signal_quantity"] == 0.0
    assert a_row["total_effective_forecast"] == QTY_A * 12
    assert b_row["total_signal_quantity"] == 0.0
    assert b_row["total_effective_forecast"] == QTY_B * 12
    assert a_row["overridden_buckets"] == 0
    assert b_row["overridden_buckets"] == 0
    by_key = _by_key(admin, ITEM_A, SITE_A)
    assert [b["signal_quantity"] for b in by_key["forecast"]] == [0.0] * 12
    assert by_key["total_signal_quantity"] == 0.0
    assert by_key["total_effective_forecast"] == QTY_A * 12

    # The statistical snapshots taken with and without signals.
    stat_a = [_statistical(a_row)]
    qty_b = [[b["quantity"] for b in b_row["forecast"]]]

    # ------------------------------------------------------------------
    # One bucket: a signal of 300 units whose 30-day window is exactly
    # the first forecast bucket.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/demand-signals",
        json=_signal_body(
            ITEM_A, SITE_A, "promotion", S1_START, S1_END, S1_QTY, "  promo window  "
        ),
    )
    assert resp.status_code == 201, resp.text
    s1_id = resp.json()["id"]

    rows = _list(admin)
    by = {(r["item_code"], r["site_code"]): r for r in rows}
    a_row = by[(ITEM_A, SITE_A)]
    buckets = a_row["forecast"]
    # 10.0 a day for 30 days: the whole quantity lands in bucket 1.
    assert buckets[0]["quantity"] == QTY_A
    assert buckets[0]["override_days"] == 0
    assert buckets[0]["signal_quantity"] == S1_QTY
    assert buckets[0]["effective_quantity"] == QTY_A + S1_QTY
    for bucket in buckets[1:]:
        assert bucket["signal_quantity"] == 0.0
        assert bucket["effective_quantity"] == bucket["quantity"] == QTY_A
    assert a_row["total_signal_quantity"] == S1_QTY
    assert a_row["total_effective_forecast"] == QTY_A * 12 + S1_QTY
    # Another item-site: the signal for ITEM-A leaves ITEM-B unchanged.
    b_row = by[(ITEM_B, SITE_B)]
    _untouched(b_row, QTY_B)
    assert b_row["total_signal_quantity"] == 0.0
    assert b_row["total_effective_forecast"] == QTY_B * 12
    by_key = _by_key(admin, ITEM_A, SITE_A)
    assert by_key["forecast"][0]["signal_quantity"] == S1_QTY
    assert by_key["forecast"][0]["effective_quantity"] == QTY_A + S1_QTY
    assert by_key["total_signal_quantity"] == S1_QTY
    assert by_key["total_effective_forecast"] == QTY_A * 12 + S1_QTY
    stat_a.append(_statistical(a_row))
    qty_b.append([b["quantity"] for b in b_row["forecast"]])

    # ------------------------------------------------------------------
    # Across buckets: 60 units over a 20-day window — 5 days in the
    # first bucket, 15 in the second — adds 15 and 45.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/demand-signals",
        json=_signal_body(
            ITEM_A, SITE_A, "deal", S2_START, S2_END, S2_QTY, "cross-bucket deal"
        ),
    )
    assert resp.status_code == 201, resp.text
    s2_id = resp.json()["id"]

    s2_share_1 = S2_QTY / S2_DAYS * S2_DAYS_1  # 3.0 a day, 5 days: 15.0
    s2_share_2 = S2_QTY / S2_DAYS * S2_DAYS_2  # 3.0 a day, 15 days: 45.0
    rows = _list(admin)
    by = {(r["item_code"], r["site_code"]): r for r in rows}
    a_row = by[(ITEM_A, SITE_A)]
    buckets = a_row["forecast"]
    assert buckets[0]["signal_quantity"] == S1_QTY + s2_share_1
    assert buckets[0]["effective_quantity"] == QTY_A + S1_QTY + s2_share_1
    assert buckets[1]["signal_quantity"] == s2_share_2
    assert buckets[1]["effective_quantity"] == QTY_A + s2_share_2
    for bucket in buckets[2:]:
        assert bucket["signal_quantity"] == 0.0
        assert bucket["effective_quantity"] == bucket["quantity"] == QTY_A
    assert a_row["total_signal_quantity"] == S1_QTY + s2_share_1 + s2_share_2
    assert (
        a_row["total_effective_forecast"]
        == QTY_A * 12 + S1_QTY + s2_share_1 + s2_share_2
    )
    _untouched(by[(ITEM_B, SITE_B)], QTY_B)
    by_key = _by_key(admin, ITEM_A, SITE_A)
    assert by_key["forecast"][0]["signal_quantity"] == S1_QTY + s2_share_1
    assert by_key["forecast"][1]["signal_quantity"] == s2_share_2
    assert by_key["total_signal_quantity"] == S1_QTY + s2_share_1 + s2_share_2
    assert by_key["total_effective_forecast"] == (
        QTY_A * 12 + S1_QTY + s2_share_1 + s2_share_2
    )
    stat_a.append(_statistical(a_row))
    qty_b.append([b["quantity"] for b in by[(ITEM_B, SITE_B)]["forecast"]])

    # ------------------------------------------------------------------
    # Two signals covering the same days add together.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/demand-signals",
        json=_signal_body(
            ITEM_A,
            SITE_A,
            "event",
            S3_START,
            S3_END,
            S3_QTY,
            "second same-window signal",
        ),
    )
    assert resp.status_code == 201, resp.text
    s3_id = resp.json()["id"]

    rows = _list(admin)
    by = {(r["item_code"], r["site_code"]): r for r in rows}
    a_row = by[(ITEM_A, SITE_A)]
    buckets = a_row["forecast"]
    assert buckets[0]["signal_quantity"] == S1_QTY + S3_QTY + s2_share_1
    assert buckets[0]["effective_quantity"] == QTY_A + S1_QTY + S3_QTY + s2_share_1
    assert buckets[1]["signal_quantity"] == s2_share_2
    assert buckets[1]["effective_quantity"] == QTY_A + s2_share_2
    for bucket in buckets[2:]:
        assert bucket["signal_quantity"] == 0.0
        assert bucket["effective_quantity"] == bucket["quantity"] == QTY_A
    assert a_row["total_signal_quantity"] == S1_QTY + S3_QTY + s2_share_1 + s2_share_2
    assert a_row["total_effective_forecast"] == (
        QTY_A * 12 + S1_QTY + S3_QTY + s2_share_1 + s2_share_2
    )
    # ITEM-B, untouched by ITEM-A's second signal.
    a_snapshot = _bucket_fields(a_row)
    _untouched(by[(ITEM_B, SITE_B)], QTY_B)
    by_key = _by_key(admin, ITEM_A, SITE_A)
    assert by_key["forecast"][0]["signal_quantity"] == S1_QTY + S3_QTY + s2_share_1
    assert by_key["total_signal_quantity"] == S1_QTY + S3_QTY + s2_share_1 + s2_share_2
    stat_a.append(_statistical(a_row))
    qty_b.append([b["quantity"] for b in by[(ITEM_B, SITE_B)]["forecast"]])

    # ------------------------------------------------------------------
    # An override wins: on ITEM-B, a signal over the first bucket and
    # an override covering the whole bucket.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/demand-signals",
        json=_signal_body(
            ITEM_B, SITE_B, "promotion", S1_START, S1_END, S4_QTY, "b promo window"
        ),
    )
    assert resp.status_code == 201, resp.text
    resp.json()["id"]
    resp = admin.post(
        "/api/forecast/overrides",
        json={
            "item_code": ITEM_B,
            "site_code": SITE_B,
            "period_start": O_FULL_START.isoformat(),
            "quantity": O_FULL_QTY,
            "reason": "whole bucket",
        },
    )
    assert resp.status_code == 201, resp.text

    rows = _list(admin)
    by = {(r["item_code"], r["site_code"]): r for r in rows}
    b_row = by[(ITEM_B, SITE_B)]
    buckets = b_row["forecast"]
    # The override covers every day of the bucket: the effective
    # quantity is the override's quantity exactly and the signal adds
    # nothing.
    assert buckets[0]["quantity"] == QTY_B
    assert buckets[0]["override_days"] == BUCKET_DAYS
    assert buckets[0]["signal_quantity"] == 0.0
    assert buckets[0]["effective_quantity"] == O_FULL_QTY
    for bucket in buckets[1:]:
        assert bucket["signal_quantity"] == 0.0
        assert bucket["effective_quantity"] == bucket["quantity"] == QTY_B
    assert b_row["total_signal_quantity"] == 0.0
    assert b_row["total_effective_forecast"] == O_FULL_QTY + QTY_B * 11
    assert b_row["overridden_buckets"] == 1
    # ITEM-A's buckets are unchanged by ITEM-B's signal and override.
    assert _bucket_fields(by[(ITEM_A, SITE_A)]) == a_snapshot
    by_key = _by_key(admin, ITEM_B, SITE_B)
    assert by_key["forecast"][0]["signal_quantity"] == 0.0
    assert by_key["forecast"][0]["effective_quantity"] == O_FULL_QTY
    assert by_key["total_signal_quantity"] == 0.0
    assert by_key["total_effective_forecast"] == O_FULL_QTY + QTY_B * 11
    stat_a.append(_statistical(by[(ITEM_A, SITE_A)]))
    qty_b.append([b["quantity"] for b in b_row["forecast"]])

    # The override covering 10 of the signal's 30 days: a second
    # override for the same period, stored directly, is the current
    # row — it covers only the first 10 days of the window.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO forecast_overrides "
                "(item_code, site_code, period_start, period_end, kind, "
                "quantity, reason, created_by) "
                "VALUES (%s, %s, %s, %s, 'override', %s, %s, 'direct-insert')",
                (
                    ITEM_B,
                    SITE_B,
                    O_PART_START,
                    O_PART_END,
                    O_PART_QTY,
                    "direct partial period",
                ),
            )
        conn.commit()
    finally:
        conn.close()

    rows = _list(admin)
    by = {(r["item_code"], r["site_code"]): r for r in rows}
    b_row = by[(ITEM_B, SITE_B)]
    buckets = b_row["forecast"]
    # 20 uncovered days take the statistical share, 10 days the
    # override's share; the signal adds only its 20 uncovered days'
    # shares.
    base = (BUCKET_DAYS - O_PART_DAYS) * (QTY_B / BUCKET_DAYS) + O_PART_QTY  # 120.0
    s4_uncovered = S4_QTY / BUCKET_DAYS * (BUCKET_DAYS - O_PART_DAYS)  # 200.0
    assert buckets[0]["override_days"] == O_PART_DAYS
    assert buckets[0]["signal_quantity"] == s4_uncovered
    assert buckets[0]["effective_quantity"] == base + s4_uncovered
    for bucket in buckets[1:]:
        assert bucket["signal_quantity"] == 0.0
        assert bucket["effective_quantity"] == bucket["quantity"] == QTY_B
    assert b_row["total_signal_quantity"] == s4_uncovered
    assert b_row["total_effective_forecast"] == base + s4_uncovered + QTY_B * 11
    # ITEM-A's buckets are still unchanged.
    assert _bucket_fields(by[(ITEM_A, SITE_A)]) == a_snapshot
    by_key = _by_key(admin, ITEM_B, SITE_B)
    assert by_key["forecast"][0]["signal_quantity"] == s4_uncovered
    assert by_key["forecast"][0]["effective_quantity"] == base + s4_uncovered
    assert by_key["total_signal_quantity"] == s4_uncovered
    assert by_key["total_effective_forecast"] == base + s4_uncovered + QTY_B * 11
    stat_a.append(_statistical(by[(ITEM_A, SITE_A)]))
    qty_b.append([b["quantity"] for b in b_row["forecast"]])

    # ------------------------------------------------------------------
    # Withdrawn: after the withdrawals the signals add nothing and
    # signal_quantity is 0 again.
    # ------------------------------------------------------------------
    for signal_id in (s1_id, s2_id, s3_id):
        resp = admin.post(
            f"/api/demand-signals/{signal_id}/withdraw",
            json={"reason": "no longer expected"},
        )
        assert resp.status_code == 201, resp.text
        assert resp.json()["kind"] == "withdrawal"

    rows = _list(admin)
    by = {(r["item_code"], r["site_code"]): r for r in rows}
    a_row = by[(ITEM_A, SITE_A)]
    _untouched(a_row, QTY_A)
    assert a_row["total_signal_quantity"] == 0.0
    assert a_row["total_effective_forecast"] == QTY_A * 12
    # ITEM-B: its standing signal and its 10-day override still apply.
    b_row = by[(ITEM_B, SITE_B)]
    assert b_row["forecast"][0]["signal_quantity"] == s4_uncovered
    assert b_row["forecast"][0]["effective_quantity"] == base + s4_uncovered
    assert b_row["total_signal_quantity"] == s4_uncovered
    assert b_row["total_effective_forecast"] == base + s4_uncovered + QTY_B * 11
    by_key = _by_key(admin, ITEM_A, SITE_A)
    assert [b["signal_quantity"] for b in by_key["forecast"]] == [0.0] * 12
    assert by_key["total_signal_quantity"] == 0.0
    assert by_key["total_effective_forecast"] == QTY_A * 12
    stat_a.append(_statistical(a_row))
    qty_b.append([b["quantity"] for b in b_row["forecast"]])

    # ------------------------------------------------------------------
    # The statistical forecast: quantity and override_days identical
    # with and without signals (ITEM-A stored no override at all);
    # ITEM-B's quantity likewise.
    # ------------------------------------------------------------------
    base_stat = [(QTY_A, 0)] * 12
    assert all(snapshot == base_stat for snapshot in stat_a)
    assert all(qtys == [QTY_B] * 12 for qtys in qty_b)

    # ------------------------------------------------------------------
    # One read: effective_for_all over several rows calls the
    # counted-signals query once.
    # ------------------------------------------------------------------

    def _row(item: str, site: str) -> dict:
        return build_forecast_row(
            item, site, "smooth", LAST, history_for_pair(item, site, LAST)
        )

    rows = [_row(ITEM_A, SITE_A), _row(ITEM_B, SITE_B), _row(ITEM_A, SITE_A)]
    result, queries = _measure(lambda: effective_for_all(rows))
    assert len([q for q in queries if "demand_signals" in q]) == 1
    assert len([q for q in queries if "forecast_overrides" in q]) == 1
    # ITEM-A's signals are all withdrawn: no signal share anywhere.
    for row in (result[0], result[2]):
        assert [b["signal_quantity"] for b in row["forecast"]] == [0.0] * 12
        assert [b["effective_quantity"] for b in row["forecast"]] == [QTY_A] * 12
    # ITEM-B's standing signal beside its 10-day override.
    b_buckets = result[1]["forecast"]
    assert b_buckets[0]["signal_quantity"] == s4_uncovered
    assert b_buckets[0]["effective_quantity"] == base + s4_uncovered
    assert b_buckets[0]["override_days"] == O_PART_DAYS
    assert result[1]["total_signal_quantity"] == s4_uncovered
    assert result[1]["total_effective_forecast"] == base + s4_uncovered + QTY_B * 11
