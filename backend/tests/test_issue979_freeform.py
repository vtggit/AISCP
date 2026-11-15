"""Issue #979 proof: the snapshot stores the signal quantity.

The proof covers, in one test, against the test database (demand
events inserted for the test the way issue #331's proof seeds them,
explicit as-of date 2026-08-31 — the latest demand day, the shared
default as-of date):

* the column: after the migrations, ``information_schema.columns``
  shows ``signal_quantity`` on ``forecast_snapshots`` as nullable,
  with data type ``double precision`` and no default;
* a live capture: a signal stored through
  ``POST /api/demand-signals`` the way issue #964's proof stores it
  — 60 units over a 20-day window straddling the first two forecast
  buckets — and a live capture (no ``as_of``) stores, on each bucket
  row of the signal's item-site, the ``signal_quantity``
  ``GET /api/forecast/by-key`` shows for the same bucket, at least
  one of them above 0, and their sum that answer's
  ``total_signal_quantity``, while every row of the other item-site
  stores 0;
* effective and statistical: the stored ``effective_quantity`` of the
  signal's buckets is its stored ``quantity`` plus its stored
  ``signal_quantity``, and the stored ``quantity`` is the
  statistical bucket;
* a backfill: a capture for an earlier ``as_of`` (2026-07-31), made
  while the signal stands, stores ``signal_quantity`` 0 on every
  row — a hindcast has no signals, as it has no overrides;
* frozen: after ``POST /api/demand-signals/{id}/withdraw`` the
  stored rows of the live capture still carry the same
  ``signal_quantity`` — they record what was believed on the as-of
  date, and the forecast list has moved on to 0;
* an older row: a row inserted directly without the column stores
  null, and ``GET /api/forecast/accuracy`` still answers 200.

The stored rows are read with direct SQL.
"""

from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

# The explicit as-of date the test seeds and captures for — the
# latest demand day, the shared default as-of date.
AS_OF = date(2026, 8, 31)
AS_OF_STR = AS_OF.isoformat()
# The earlier date the backfill capture targets.
BACKFILL_AS_OF = date(2026, 7, 31)
BACKFILL_AS_OF_STR = BACKFILL_AS_OF.isoformat()
# A date with no snapshot: the directly inserted older row lives
# there.
OLDER_AS_OF = date(2026, 6, 30)

ITEM_SMOOTH, SITE_SMOOTH = "ITEM-979-S", "SITE-979-S"
ITEM_INTERMITTENT, SITE_INTERMITTENT = "ITEM-979-I", "SITE-979-I"
ITEM_NO_DEMAND, SITE_NO_DEMAND = "ITEM-979-N", "SITE-979-N"

# The seeded statistical quantity of a smooth bucket: 10.0 a day
# over the bucket's 30 days.
QTY_SMOOTH = 300.0

# The signal: 60 units over a 20-day window (3.0 a day) — 5 days in
# the first forecast bucket (2026-09-26 through 2026-09-30) and 15
# in the second (2026-10-01 through 2026-10-15).
SIGNAL_START = AS_OF + timedelta(days=26)  # 2026-09-26
SIGNAL_END = AS_OF + timedelta(days=45)  # 2026-10-15
SIGNAL_QTY = 60.0
SIGNAL_DAYS = (SIGNAL_END - SIGNAL_START).days + 1  # 20
SIGNAL_DAYS_B1 = 5
SIGNAL_DAYS_B2 = 15
SHARE_B1 = SIGNAL_QTY / SIGNAL_DAYS * SIGNAL_DAYS_B1  # 15.0
SHARE_B2 = SIGNAL_QTY / SIGNAL_DAYS * SIGNAL_DAYS_B2  # 45.0


def _seed() -> None:
    """Demand events for three item-sites, all on fixed dates —
    issue #331's seed: smooth 10.0 a day for 401 days ending on
    AS_OF, intermittent 30.0 on four spaced days, no_demand 5.0 once
    beyond the 360-day stats window."""
    events = []

    def add(item: str, site: str, day: date, quantity: float, tag: str) -> None:
        events.append(
            (
                f"id-de-979-{tag}",
                f"de-979-{tag}",
                item,
                site,
                quantity,
                day.isoformat(),
                "shipment",
                "test",
            )
        )

    for offset in range(401):
        add(
            ITEM_SMOOTH,
            SITE_SMOOTH,
            AS_OF - timedelta(days=400 - offset),
            10.0,
            f"s{offset}",
        )
    for index, days_before in enumerate((5, 95, 185, 275)):
        add(
            ITEM_INTERMITTENT,
            SITE_INTERMITTENT,
            AS_OF - timedelta(days=days_before),
            30.0,
            f"i{index}",
        )
    add(ITEM_NO_DEMAND, SITE_NO_DEMAND, AS_OF - timedelta(days=400), 5.0, "n0")

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
    """Every stored snapshot row for one as-of date, direct SQL,
    deterministically ordered."""
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


def test_issue979_freeform(client, admin_headers):
    _seed()
    admin = TestClient(app, headers=admin_headers)

    # ------------------------------------------------------------------
    # The column: nullable double precision, no default.
    # ------------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT is_nullable, data_type, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'forecast_snapshots' "
                "AND column_name = 'signal_quantity'"
            )
            column = cur.fetchone()
    finally:
        conn.close()
    assert column is not None, "signal_quantity is missing from forecast_snapshots"
    assert column["is_nullable"] == "YES", dict(column)
    assert column["data_type"] == "double precision", dict(column)
    assert column["column_default"] is None, dict(column)

    # ------------------------------------------------------------------
    # Store the signal through the API, the way issue #964's proof
    # does.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/demand-signals",
        json={
            "kind": "promotion",
            "item_code": ITEM_SMOOTH,
            "site_code": SITE_SMOOTH,
            "start_date": SIGNAL_START.isoformat(),
            "end_date": SIGNAL_END.isoformat(),
            "quantity": SIGNAL_QTY,
            "reason": "  promo window  ",
        },
    )
    assert resp.status_code == 201, resp.text
    signal_id = resp.json()["id"]

    # ------------------------------------------------------------------
    # A live capture (no as_of): each stored bucket carries the
    # signal_quantity the forecast list shows for it at capture time.
    # ------------------------------------------------------------------
    resp = admin.post("/api/forecast/snapshots")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "as_of": AS_OF_STR,
        "origin": "live",
        "created": True,
        "item_sites": 2,
        "rows": 24,
    }

    live = _stored_rows(AS_OF)
    assert len(live) == 24, len(live)
    assert (ITEM_NO_DEMAND, SITE_NO_DEMAND) not in {
        (row["item_code"], row["site_code"]) for row in live
    }
    smooth_rows = [
        row
        for row in live
        if (row["item_code"], row["site_code"]) == (ITEM_SMOOTH, SITE_SMOOTH)
    ]
    assert [row["bucket_index"] for row in smooth_rows] == list(range(1, 13))
    other_rows = [
        row
        for row in live
        if (row["item_code"], row["site_code"])
        == (ITEM_INTERMITTENT, SITE_INTERMITTENT)
    ]
    assert len(other_rows) == 12, len(other_rows)

    # What GET /api/forecast/by-key shows for the same buckets.
    resp = admin.get(
        "/api/forecast/by-key",
        params={"item_code": ITEM_SMOOTH, "site_code": SITE_SMOOTH},
    )
    assert resp.status_code == 200, resp.text
    by_key = resp.json()

    for row, bucket in zip(smooth_rows, by_key["forecast"]):
        assert row["bucket_start"].isoformat() == bucket["bucket_start"]
        assert row["bucket_end"].isoformat() == bucket["bucket_end"]
        # The stored signal_quantity is the forecast list's for the
        # same bucket.
        assert row["signal_quantity"] == bucket["signal_quantity"], row
        # The statistical side: the stored quantity is the bucket's.
        assert row["quantity"] == bucket["quantity"], row
        # Effective and statistical: the stored effective quantity is
        # the stored quantity plus the stored signal quantity (no
        # override covers a day of this item-site).
        assert (
            row["effective_quantity"] == row["quantity"] + row["signal_quantity"]
        ), row

    # At least one stored bucket above 0, and the stored sum the
    # answer's total_signal_quantity.
    assert any(row["signal_quantity"] > 0 for row in smooth_rows)
    assert (
        sum(row["signal_quantity"] for row in smooth_rows)
        == by_key["total_signal_quantity"]
    )
    # The window straddles the first two buckets: 15.0 and 45.0 —
    # every expected number written out, never computed through the
    # code under test.
    assert smooth_rows[0]["signal_quantity"] == SHARE_B1
    assert smooth_rows[1]["signal_quantity"] == SHARE_B2
    for row in smooth_rows[2:]:
        assert row["signal_quantity"] == 0.0, row
    assert by_key["total_signal_quantity"] == SHARE_B1 + SHARE_B2
    # The signal's bucket, effective and statistical: 300.0 plus its
    # signal quantity, the quantity unchanged.
    assert smooth_rows[0]["quantity"] == QTY_SMOOTH
    assert smooth_rows[0]["effective_quantity"] == QTY_SMOOTH + SHARE_B1
    assert smooth_rows[1]["effective_quantity"] == QTY_SMOOTH + SHARE_B2

    # Every row of the other item-site stores 0.
    for row in other_rows:
        assert row["signal_quantity"] == 0.0, row

    # ------------------------------------------------------------------
    # A backfill, made while the signal stands: signal_quantity 0 on
    # every row — a hindcast has no signals, as it has no overrides.
    # ------------------------------------------------------------------
    resp = admin.post("/api/forecast/snapshots", json={"as_of": BACKFILL_AS_OF_STR})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "as_of": BACKFILL_AS_OF_STR,
        "origin": "backfill",
        "created": True,
        "item_sites": 2,
        "rows": 24,
    }
    backfill = _stored_rows(BACKFILL_AS_OF)
    assert len(backfill) == 24, len(backfill)
    for row in backfill:
        assert row["signal_quantity"] == 0.0, row

    # ------------------------------------------------------------------
    # Frozen: the withdrawal leaves the stored rows of the live
    # capture as they were — they record what was believed on the
    # as-of date — while the forecast list has moved on.
    # ------------------------------------------------------------------
    before_withdraw = _stored_rows(AS_OF)
    resp = admin.post(
        f"/api/demand-signals/{signal_id}/withdraw",
        json={"reason": "the promo ended"},
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["kind"] == "withdrawal"

    after_withdraw = _stored_rows(AS_OF)
    assert after_withdraw == before_withdraw
    smooth_after = [
        row
        for row in after_withdraw
        if (row["item_code"], row["site_code"]) == (ITEM_SMOOTH, SITE_SMOOTH)
    ]
    assert smooth_after[0]["signal_quantity"] == SHARE_B1
    assert smooth_after[1]["signal_quantity"] == SHARE_B2
    # The forecast list no longer counts the withdrawn signal.
    resp = admin.get(
        "/api/forecast/by-key",
        params={"item_code": ITEM_SMOOTH, "site_code": SITE_SMOOTH},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["total_signal_quantity"] == 0.0

    # ------------------------------------------------------------------
    # An older row: inserted directly without the column it stores
    # null, and the accuracy read still answers 200.
    # ------------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO forecast_snapshots "
                "(id, as_of, origin, item_code, site_code, bucket_index, "
                "bucket_start, bucket_end, quantity, method, demand_class, "
                "history_buckets, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())",
                (
                    "id-979-older",
                    OLDER_AS_OF,
                    "backfill",
                    ITEM_SMOOTH,
                    SITE_SMOOTH,
                    1,
                    date(2026, 7, 1),
                    date(2026, 7, 30),
                    QTY_SMOOTH,
                    "mean",
                    "smooth",
                    12,
                ),
            )
            cur.execute(
                "SELECT signal_quantity, effective_quantity, override_days "
                "FROM forecast_snapshots WHERE id = %s",
                ("id-979-older",),
            )
            older = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    assert older == (None, None, None), older

    resp = admin.get("/api/forecast/accuracy")
    assert resp.status_code == 200, resp.text
    assert resp.json()["as_of"] == AS_OF_STR
