"""Issue #1382 proof: the snapshot stores the history mean.

Every stored snapshot bucket row gains a new ``history_quantity``
column (a nullable ``double precision`` on ``forecast_snapshots``,
no default, added by the migration after the
snapshot-proposed-quantity one — the only revision with that as its
predecessor): the item-site's history mean over the demand
statistics window, scaled to the bucket's length — the statistics
row's ``mean_daily`` (the 360-day window the forecast row is built
from) times the bucket's days (30 for every horizon bucket), not
rounded — and null when the row's ``mean_daily`` is missing or not
a finite number.  A live capture and a backfill alike fill the
column from the very demand statistics row the forecast row was
built from, read through the new keyword-only
``forecast_rows(..., include_history_mean=True)`` flag (one
``demand_stats_for_all`` call per capture, no extra query, and the
default flag leaves every row exactly as before).

Everything is read through the app's test client, signed in with
the development tokens (admin and user, the way test 1375 signs
in), with no stand-in for the forecast or the correction reads:
the demand events are real — A, B and D at site 159, every day for
the 401 days ending on 2026-11-16, ``date.today`` pinned to
2026-11-17 the way test 1375 pins it, so the shared default as-of
is 2026-11-16: A 2.0 a day, B 1.0 a day, and D 1.0 a day up to
2026-08-07 and 4.0 a day from 2026-08-08.  Proved here:

* the live capture — ``capture_snapshot()`` answers ``{"as_of":
  2026-11-16, "origin": "live", "created": true, "item_sites": 3,
  "rows": 36}``; the stored rows, read with SQL in item, site and
  bucket order, carry ``history_quantity`` 60.0 for A, 30.0 for B
  and 55.25 for D — 663 over the 360 days ending 2026-11-16, times
  30 — in all 12 buckets, each value equal to 30 times the
  ``mean_daily`` ``GET /api/demand-stats/by-key`` answers for the
  item-site (compared with ``pytest.approx``);
* the backfill — ``capture_snapshot(as_of=date(2026, 10, 17))``
  answers ``created`` true and its rows carry A 60.0, B 30.0 and D
  47.75 — 573 over the 360 days ending 2026-10-17, times 30 — in
  every bucket, each equal to 30 times the ``mean_daily`` the same
  route answers with ``as_of=2026-10-17``;
* one read of the statistics — a counting wrapper around
  ``app.forecast.listing.demand_stats_for_all`` sees exactly one
  call during each of the two captures; a second
  ``capture_snapshot()`` answers ``created`` false, calls it no
  more, and leaves the stored rows as they were;
* the rows by default — ``forecast_rows(date(2026, 11, 16))``
  answers rows without a ``history_mean_daily`` key; with
  ``include_history_mean=True`` each row's
  ``history_mean_daily`` equals the route's ``mean_daily`` for its
  item-site; and no row of ``GET /api/forecast`` carries the key;
* the migration — ``information_schema.columns`` shows
  ``history_quantity`` on ``forecast_snapshots`` as
  ``double precision``, nullable, with no default; the script
  directory has exactly one head; exactly one revision has
  ``down_revision`` ``0049_snapshot_proposed_quantity``, and its
  id is not compared with the head, so a later migration leaves
  the check unchanged (this proof never names the new revision).

Identity — the proof also runs as a NON-ADMIN (role ``user``): a
second pass after a full reload, with the page booted the production
way (the token it would take from the URL hash as
``#access_token=...``), ``GET /api/auth/config`` answering publicly
and ``GET /api/auth/me`` resolving the viewer, sees the admin-only
snapshot capture refused 403 (nothing stored) while the page still
renders for the viewer.
"""

import os
from datetime import date, timedelta

import psycopg2.extras
import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.forecast.listing as listing
from app.db.connection import get_connection
from app.forecast.listing import forecast_rows
from app.forecast.snapshots import capture_snapshot
from app.main import app

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

SNAPSHOTS_URL = "/api/forecast/snapshots"
FORECAST_URL = "/api/forecast"
DEMAND_STATS_BY_KEY_URL = "/api/demand-stats/by-key"
SITE = "159"

TODAY = date(2026, 11, 17)
# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
AS_OF = date(2026, 11, 16)
# The first seeded demand day, 401 days back.
HIST_START = AS_OF - timedelta(days=400)
# The day D's daily demand steps from 1.0 to 4.0.
D_STEP_DAY = date(2026, 8, 8)
# The backfill capture's as-of date: an earlier date, still inside
# the seeded demand.
BACKFILL_AS_OF = date(2026, 10, 17)

DAILY_A = 2.0
DAILY_B = 1.0
DAILY_D_BEFORE = 1.0
DAILY_D_AFTER = 4.0

# The stored history quantities: the mean_daily of the 360-day
# window ending on the as-of date, times the 30 days of a bucket.
LIVE_EXPECTED = {"A": 60.0, "B": 30.0, "D": 55.25}  # D: 663/360 * 30
BACKFILL_EXPECTED = {"A": 60.0, "B": 30.0, "D": 47.75}  # D: 573/360 * 30


def _fixed_date(today: date):
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return today

    return _FixedDate


def _insert_events(events: list[tuple]) -> None:
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


def _seed() -> None:
    """401 days of demand ending on the as-of date, all at site 159:
    A 2.0 a day, B 1.0 a day, D 1.0 a day up to 2026-08-07 and 4.0
    a day from 2026-08-08."""
    events = []
    for offset in range((AS_OF - HIST_START).days + 1):
        day = HIST_START + timedelta(days=offset)
        daily_d = DAILY_D_AFTER if day >= D_STEP_DAY else DAILY_D_BEFORE
        for item, daily in (
            ("A", DAILY_A),
            ("B", DAILY_B),
            ("D", daily_d),
        ):
            events.append(
                (
                    f"id-de-1382-{item}-{offset}",
                    f"de-1382-{item}-{offset}",
                    item,
                    SITE,
                    daily,
                    day.isoformat(),
                    "shipment",
                    "test",
                )
            )
    _insert_events(events)


def _stored_rows(as_of: date) -> list[dict]:
    """Every stored snapshot row for one as-of date, direct SQL, in
    item, site and bucket order."""
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


class _CountingStats:
    """A counting wrapper around ``app.forecast.listing.
    demand_stats_for_all``: records every call and delegates."""

    def __init__(self, real):
        self.real = real
        self.calls = []

    def __call__(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.real(*args, **kwargs)


def _mean_daily(client: TestClient, item: str, as_of: date | None) -> float:
    """The mean_daily GET /api/demand-stats/by-key answers for one
    item-site (the shared default as-of date when ``as_of`` is None)."""
    params = {"item_code": item, "site_code": SITE}
    if as_of is not None:
        params["as_of"] = as_of.isoformat()
    response = client.get(DEMAND_STATS_BY_KEY_URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()["mean_daily"]


def _assert_stored_history(rows: list[dict], expected: dict) -> None:
    """36 stored rows: 12 per item-site, every bucket carrying the
    expected history_quantity — and, per item-site, 30 times the
    mean_daily the demand-stats route answers for it."""
    assert len(rows) == 36, len(rows)
    for item, value in expected.items():
        mine = [row for row in rows if row["item_code"] == item]
        assert len(mine) == 12, (item, len(mine))
        for row in mine:
            assert row["history_quantity"] == pytest.approx(value), (item, row)


def test_issue1382_freeform(client, monkeypatch):
    # -- The clock: date.today pinned in the two modules that read it
    #    for the shared default as-of date (2026-11-16). ---------------
    monkeypatch.setattr("app.stats.demand.date", _fixed_date(TODAY))
    monkeypatch.setattr("app.forecast.history.date", _fixed_date(TODAY))

    # -- One read of the statistics: a counting wrapper around the very
    #    name forecast_rows calls. --------------------------------------
    stats = _CountingStats(listing.demand_stats_for_all)
    monkeypatch.setattr(listing, "demand_stats_for_all", stats)

    _seed()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    # -- The rows by default: no history_mean_daily key on the list. ---
    response = admin.get(FORECAST_URL)
    assert response.status_code == 200, response.text
    api_rows = response.json()
    assert len(api_rows) == 3
    for row in api_rows:
        assert "history_mean_daily" not in row

    # -- The live capture: created, 3 item-sites, 36 rows, exactly one
    #    statistics read during it. -------------------------------------
    pre = len(stats.calls)
    result = capture_snapshot()
    assert result == {
        "as_of": AS_OF,
        "origin": "live",
        "created": True,
        "item_sites": 3,
        "rows": 36,
    }
    assert len(stats.calls) - pre == 1
    live = _stored_rows(AS_OF)
    _assert_stored_history(live, LIVE_EXPECTED)
    # Each value: 30 times the mean_daily the route answers for the
    # item-site at the shared default as-of date.
    for item, value in LIVE_EXPECTED.items():
        assert value == pytest.approx(30.0 * _mean_daily(admin, item, None))
        for row in [r for r in live if r["item_code"] == item]:
            assert row["history_quantity"] == pytest.approx(
                30.0 * _mean_daily(admin, item, None)
            )

    # -- The backfill: created, its own statistics read, its own history
    #    quantities (D: 573 over the 360 days ending 2026-10-17). -------
    pre = len(stats.calls)
    result = capture_snapshot(as_of=BACKFILL_AS_OF)
    assert result == {
        "as_of": BACKFILL_AS_OF,
        "origin": "backfill",
        "created": True,
        "item_sites": 3,
        "rows": 36,
    }
    assert len(stats.calls) - pre == 1
    backfill = _stored_rows(BACKFILL_AS_OF)
    _assert_stored_history(backfill, BACKFILL_EXPECTED)
    for item, value in BACKFILL_EXPECTED.items():
        assert value == pytest.approx(30.0 * _mean_daily(admin, item, BACKFILL_AS_OF))
        for row in [r for r in backfill if r["item_code"] == item]:
            assert row["history_quantity"] == pytest.approx(
                30.0 * _mean_daily(admin, item, BACKFILL_AS_OF)
            )

    # -- A date already captured: returned before any row is built —
    #    created false, no statistics call, the stored rows unchanged. -
    pre = len(stats.calls)
    result = capture_snapshot()
    assert result == {
        "as_of": AS_OF,
        "origin": "live",
        "created": False,
        "item_sites": 3,
        "rows": 36,
    }
    assert len(stats.calls) - pre == 0
    assert _stored_rows(AS_OF) == live
    assert _stored_rows(BACKFILL_AS_OF) == backfill

    # -- The rows by default, called directly: no key; with
    #    include_history_mean=True, each row's key equals the route's
    #    mean_daily for its item-site. ----------------------------------
    plain = forecast_rows(date(2026, 11, 16))
    assert len(plain) == 3
    for row in plain:
        assert "history_mean_daily" not in row
    with_mean = forecast_rows(date(2026, 11, 16), include_history_mean=True)
    assert len(with_mean) == 3
    for row in with_mean:
        assert row["history_mean_daily"] == pytest.approx(
            _mean_daily(admin, row["item_code"], AS_OF)
        )

    # -- Identity, second pass after a full reload: the page boots the
    #    production way for the non-admin (the token it would take
    #    from the URL hash as #access_token=...), /api/auth/config
    #    answers publicly and /api/auth/me resolves the viewer: the
    #    admin-only snapshot capture is refused 403 (nothing stored)
    #    while the page still renders for the viewer. ------------------
    assert TestClient(app).get("/api/auth/config").status_code == 200
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == VIEWER_SUB
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    response = viewer_reloaded.post(SNAPSHOTS_URL)
    assert response.status_code == 403, response.text
    assert response.json() == {
        "detail": "Role 'admin' is required to perform this action."
    }
    # Nothing stored: the two captures the admin made stand, the stored
    # rows as they were — and the page still renders for the viewer,
    # its reads answering as the admin's do, while a request without a
    # token still gets 401.
    stored = viewer_reloaded.get(SNAPSHOTS_URL, params={"limit": 50}).json()
    assert [(entry["as_of"], entry["origin"]) for entry in stored] == [
        (AS_OF.isoformat(), "live"),
        (BACKFILL_AS_OF.isoformat(), "backfill"),
    ]
    assert all(entry["item_sites"] == 3 and entry["rows"] == 36 for entry in stored)
    assert _stored_rows(AS_OF) == live
    assert _stored_rows(BACKFILL_AS_OF) == backfill
    page = viewer_reloaded.get(FORECAST_URL)
    assert page.status_code == 200, page.text
    assert len(page.json()) == 3
    assert TestClient(app).get(SNAPSHOTS_URL).status_code == 401

    # -- The migration: the column in the test database, exactly one
    #    head, and exactly one successor of the snapshot-proposed-
    #    quantity revision — its id not compared with the head, so a
    #    later migration leaves the check unchanged. -------------------
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT is_nullable, data_type, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'forecast_snapshots' "
                "AND column_name = 'history_quantity'"
            )
            column = cur.fetchone()
    finally:
        conn.close()
    assert column is not None, "history_quantity is missing from forecast_snapshots"
    assert column["is_nullable"] == "YES", dict(column)
    assert column["data_type"] == "double precision", dict(column)
    assert column["column_default"] is None, dict(column)

    directory = ScriptDirectory(_MIGRATIONS_DIR)
    assert len(directory.get_heads()) == 1
    successors = [
        script
        for script in directory.walk_revisions()
        if script.down_revision == "0049_snapshot_proposed_quantity"
    ]
    assert len(successors) == 1
