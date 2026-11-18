"""Issue #1375 proof: the snapshot stores the correction's proposed
quantity.

A live capture of a forecast snapshot stores, in each stored bucket
row's new ``proposed_quantity`` column (a nullable ``double
precision`` on ``forecast_snapshots``, no default, added by the
migration after the forecast-override-proposals one — the only
revision with that as its predecessor), the ``proposed_quantity``
the forecast list (``GET /api/forecast/by-key?include_correction
=true``) proposes for that bucket at capture time: the
``proposed_quantity`` of the period of the item-site's
``correction`` with the same ``period_start`` as the bucket's
``bucket_start``.  A backfill capture stores null on every row — a
hindcast has no correction read, as it has no overrides and no
signals — and a capture of a date that already has a snapshot
returns the stored snapshot before any row is built, as before.

Everything is read through the app's test client, signed in with the
development tokens (admin and user, the way test 1365 signs in), with
the two correction reads replaced by stand-ins in
``app.api.forecast`` — ``_stored_system_settings`` answering ``{}``
(the bounds then the registry defaults) and
``_forecast_bias_accuracy`` answering the 1365 measure: A at 159
with 6 compared buckets and totals 900 and 500 (factor 500/900), B
at 159 with 4 and totals 300 and 1000 (factor 1000/300), and no
entry for C — while the demand events are real: A, B and C at site
159, every day for 401 days ending on 2026-11-16, ``date.today``
pinned to 2026-11-17 the way test 374 pins it, so the shared
default as-of is 2026-11-16: A and C 2.0 a day (12 forecast periods
of 60.0, the first running 2026-11-17 to 2026-12-16), B 1.0 a day
(12 periods of 30.0).  Both stand-ins record their calls, and either
can be made to raise.

Before the capture, as the admin: an override on A for 2026-12-17
(its second period) at 45, through ``POST
/api/forecast/overrides``, and a demand signal on B from 2027-01-20
to 2027-01-25 (inside its third period) for 12, posted the way
issue #964's proof posts one.  Then proved here:

* the live capture — ``capture_snapshot()`` answers ``{"as_of":
  2026-11-16, "origin": "live", "created": true, "item_sites": 3,
  "rows": 36}``, and during it each stand-in was called exactly
  once; the stored rows, read with SQL in item, site and bucket
  order, carry: A 33.33 in every bucket but the second, which is
  null (its override, whose ``override_days`` is 30); B 100.0 in
  every bucket but the third, which is null (its signal, whose
  ``signal_quantity`` is 12); C null in every bucket (no
  correction); each non-null value equals the
  ``proposed_quantity`` of the period with the same start in ``GET
  /api/forecast/by-key?include_correction=true`` for its item-site,
  read right after the capture;
* no read when nothing is stored — a second ``capture_snapshot()``
  answers ``created`` false with the same 36 rows stored and calls
  neither stand-in; a backfill,
  ``capture_snapshot(as_of=date(2026, 10, 17))``, answers
  ``created`` true, calls neither stand-in, and stores null in
  every bucket's ``proposed_quantity``;
* a failed read — with demand events for A, B and C added on
  2026-11-17 and ``date.today`` pinned to 2026-11-18, and
  ``_forecast_bias_accuracy`` raising, ``capture_snapshot()``
  answers ``{"as_of": 2026-11-17, "origin": "live", "created":
  true, "item_sites": 3, "rows": 36}``, every stored bucket's
  ``proposed_quantity`` is null, and the stand-ins were called once
  each;
* the migration — ``information_schema.columns`` shows
  ``proposed_quantity`` on ``forecast_snapshots`` as
  ``double precision``, nullable, with no default; the script
  directory has exactly one head; exactly one revision has
  ``down_revision`` ``0048_forecast_override_proposals``, and its
  id is not compared with the head, so a later migration leaves the
  check unchanged (this proof never names the new revision).

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

import app.api.forecast as forecast_api
from app.api.forecast import OverrideRow
from app.db.connection import get_connection
from app.forecast.snapshots import capture_snapshot
from app.main import app

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

OVERRIDES_URL = "/api/forecast/overrides"
SIGNALS_URL = "/api/demand-signals"
SNAPSHOTS_URL = "/api/forecast/snapshots"
BY_KEY_URL = "/api/forecast/by-key"
SITE = "159"

TODAY = date(2026, 11, 17)
# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
AS_OF = date(2026, 11, 16)
# The first seeded demand day, 401 days back.
HIST_START = AS_OF - timedelta(days=400)

# The seeded statistical quantities: the daily demand times the 30
# days of a forecast period.
DAILY_A, DAILY_B, DAILY_C = 2.0, 1.0, 2.0
QTY_A = DAILY_A * 30  # 60.0 — A's and C's statistical period
QTY_B = DAILY_B * 30  # 30.0 — B's statistical period

# The measure factors: the actual total over the effective total.
FACTOR_A = 500.0 / 900.0  # A: bias 0.8
FACTOR_B = 1000.0 / 300.0  # B: bias -0.7
# The proposed quantities, the periods' statistical quantities at
# their factors, rounded to 2 decimals.
PROPOSED_A = round(QTY_A * FACTOR_A, 2)  # 33.33
PROPOSED_B = round(QTY_B * FACTOR_B, 2)  # 100.0

PERIOD_1_START = AS_OF + timedelta(days=1)  # 2026-11-17
PERIOD_1_END = AS_OF + timedelta(days=30)  # 2026-12-16
# The override: A's second forecast period, at 45.
OVERRIDE_START = AS_OF + timedelta(days=31)  # 2026-12-17
OVERRIDE_QTY = 45.0
# The signal: 12 over the six days 2027-01-20 to 2027-01-25, inside
# B's third forecast period.
SIGNAL_START = date(2027, 1, 20)
SIGNAL_END = date(2027, 1, 25)
SIGNAL_QTY = 12.0
# The backfill capture's as-of date: an earlier date, still inside
# the seeded demand.
BACKFILL_AS_OF = date(2026, 10, 17)
# The failed-read capture: demand added on this day, ``date.today``
# pinned one day after it, so the shared default as-of is this day.
FAILED_AS_OF = date(2026, 11, 17)
TODAY_FAILED = date(2026, 11, 18)


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
    """401 days of demand ending on the as-of date: 2.0 a day for A
    and C, 1.0 a day for B, all at site 159."""
    events = []
    for item, daily in (("A", DAILY_A), ("B", DAILY_B), ("C", DAILY_C)):
        for offset in range((AS_OF - HIST_START).days + 1):
            events.append(
                (
                    f"id-de-1375-{item}-{offset}",
                    f"de-1375-{item}-{offset}",
                    item,
                    SITE,
                    daily,
                    (HIST_START + timedelta(days=offset)).isoformat(),
                    "shipment",
                    "test",
                )
            )
    _insert_events(events)


def _seed_failed_day() -> None:
    """One more demand day for A, B and C: the failed-read capture's
    shared default as-of date, 2026-11-17."""
    events = [
        (
            f"id-de-1375-failed-{item}",
            f"de-1375-failed-{item}",
            item,
            SITE,
            daily,
            FAILED_AS_OF.isoformat(),
            "shipment",
            "test",
        )
        for item, daily in (("A", DAILY_A), ("B", DAILY_B), ("C", DAILY_C))
    ]
    _insert_events(events)


def _entry(item_code, compared, effective, actual):
    """One measure item-site entry: every field ``realised_accuracy``
    answers, ``effective_bias`` (effective - actual) over actual."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "method": "naive",
        "compared_buckets": compared,
        "forecast_total": float(effective),
        "actual_total": float(actual),
        "wape": abs(effective - actual) / actual,
        "bias": (effective - actual) / actual,
        "effective_total": float(effective),
        "effective_wape": abs(effective - actual) / actual,
        "effective_bias": (effective - actual) / actual,
        "overridden_buckets": 0,
        "value_added": 0.0,
    }


def _measure():
    """The stand-in measure: the as-of 2026-11-16 and the entries for
    A and B at 159 — C has no entry."""
    sites = [
        _entry("A", 6, 900, 500),
        _entry("B", 4, 300, 1000),
    ]
    return {
        "as_of": AS_OF,
        "compared_buckets": sum(site["compared_buckets"] for site in sites),
        "overall": {
            "forecast_total": sum(site["forecast_total"] for site in sites),
            "actual_total": sum(site["actual_total"] for site in sites),
            "wape": 0.0,
            "bias": 0.0,
            "effective_total": sum(site["effective_total"] for site in sites),
            "effective_wape": 0.0,
            "effective_bias": 0.0,
            "overridden_buckets": 0,
            "value_added": None,
        },
        "by_horizon": [],
        "item_sites": sites,
    }


class _SettingsRead:
    """The stand-in stored system settings read: records every call,
    answers the configured payload and can be made to raise."""

    def __init__(self):
        self.answer = {}
        self.raises = None
        self.calls = []

    def __call__(self):
        self.calls.append(())
        if self.raises is not None:
            raise self.raises
        return self.answer


class _MeasureRead:
    """The stand-in bias-exception measure read: records every call's
    argument and answers the fixed measure, whatever it is given —
    or raises."""

    def __init__(self):
        self.raises = None
        self.calls = []

    def __call__(self, stored_settings):
        self.calls.append(stored_settings)
        if self.raises is not None:
            raise self.raises
        return _measure()


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


def test_issue1375_freeform(client, monkeypatch):
    # -- The clock and the two correction reads, in app.api.forecast. --
    monkeypatch.setattr("app.stats.demand.date", _fixed_date(TODAY))
    monkeypatch.setattr("app.forecast.history.date", _fixed_date(TODAY))
    settings = _SettingsRead()
    measure = _MeasureRead()
    monkeypatch.setattr(forecast_api, "_stored_system_settings", settings)
    monkeypatch.setattr(forecast_api, "_forecast_bias_accuracy", measure)

    _seed()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})

    def _by_key(item: str) -> dict:
        pre_settings = len(settings.calls)
        pre_measure = len(measure.calls)
        response = admin.get(
            BY_KEY_URL,
            params={
                "item_code": item,
                "site_code": SITE,
                "include_correction": "true",
            },
        )
        assert response.status_code == 200, response.text
        # The by-key read ran its two correction reads once each, the
        # settings' answer as the measure's one argument.
        assert settings.calls[pre_settings:] == [()]
        assert measure.calls[pre_measure:] == [{}]
        return response.json()

    # -- The seed: 12 forecast periods at the shared default as-of,
    #    the first running 2026-11-17 to 2026-12-16. -------------------
    row = _by_key("A")
    assert row["as_of"] == AS_OF.isoformat()
    assert [bucket["quantity"] for bucket in row["forecast"]] == [QTY_A] * 12
    assert row["forecast"][0]["bucket_start"] == PERIOD_1_START.isoformat()
    assert row["forecast"][0]["bucket_end"] == PERIOD_1_END.isoformat()
    correction = row["correction"]
    assert [period["proposed_quantity"] for period in correction["periods"]] == [
        PROPOSED_A
    ] * 12
    assert correction["factor"] == pytest.approx(FACTOR_A, rel=1e-12)
    assert correction["compared_buckets"] == 6
    assert correction["effective_total"] == 900.0
    assert correction["actual_total"] == 500.0

    row = _by_key("B")
    assert [bucket["quantity"] for bucket in row["forecast"]] == [QTY_B] * 12
    correction = row["correction"]
    assert correction["factor"] == pytest.approx(FACTOR_B, rel=1e-12)
    assert [period["proposed_quantity"] for period in correction["periods"]] == [
        PROPOSED_B
    ] * 12

    # C has no measure entry: no correction.
    assert _by_key("C")["correction"] is None

    # -- Before the capture: the override on A's second period. -------
    pre_settings = len(settings.calls)
    pre_measure = len(measure.calls)
    response = admin.post(
        OVERRIDES_URL,
        json={
            "item_code": "A",
            "site_code": SITE,
            "period_start": OVERRIDE_START.isoformat(),
            "quantity": OVERRIDE_QTY,
            "reason": "the override",
        },
    )
    assert response.status_code == 201, response.text
    answered = response.json()
    assert set(answered.keys()) == set(OverrideRow.model_fields), answered
    assert answered["kind"] == "override"
    assert answered["quantity"] == OVERRIDE_QTY
    assert answered["applies"] is True
    # The post's own correction reads, once each.
    assert settings.calls[pre_settings:] == [()]
    assert measure.calls[pre_measure:] == [{}]

    # -- Then the signal inside B's third period, posted the way
    #    issue #964's proof posts one. It runs no correction read. ---
    pre_settings = len(settings.calls)
    pre_measure = len(measure.calls)
    response = admin.post(
        SIGNALS_URL,
        json={
            "kind": "promotion",
            "item_code": "B",
            "site_code": SITE,
            "start_date": SIGNAL_START.isoformat(),
            "end_date": SIGNAL_END.isoformat(),
            "quantity": SIGNAL_QTY,
            "reason": "the signal",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["kind"] == "promotion"
    assert settings.calls[pre_settings:] == []
    assert measure.calls[pre_measure:] == []

    # -- The live capture: each stand-in called exactly once. ---------
    pre_settings = len(settings.calls)
    pre_measure = len(measure.calls)
    result = capture_snapshot()
    assert result == {
        "as_of": AS_OF,
        "origin": "live",
        "created": True,
        "item_sites": 3,
        "rows": 36,
    }
    assert settings.calls[pre_settings:] == [()]
    assert measure.calls[pre_measure:] == [{}]

    # -- The stored rows, direct SQL in item, site and bucket order. --
    live = _stored_rows(AS_OF)
    assert len(live) == 36, len(live)
    assert [row["site_code"] for row in live] == [SITE] * 36
    a_rows = [row for row in live if row["item_code"] == "A"]
    b_rows = [row for row in live if row["item_code"] == "B"]
    c_rows = [row for row in live if row["item_code"] == "C"]
    for item_rows in (a_rows, b_rows, c_rows):
        assert [row["bucket_index"] for row in item_rows] == list(range(1, 13))

    # A: 33.33 in every bucket but the second, which is null — its
    # override, 30 days of it.
    assert [row["proposed_quantity"] for row in a_rows] == [
        PROPOSED_A,
        None,
        *(PROPOSED_A for _ in range(10)),
    ]
    assert a_rows[1]["override_days"] == 30
    # B: 100.0 in every bucket but the third, which is null — its
    # signal, 12 over its window.
    assert [row["proposed_quantity"] for row in b_rows] == [
        PROPOSED_B,
        PROPOSED_B,
        None,
        *(PROPOSED_B for _ in range(9)),
    ]
    assert b_rows[2]["signal_quantity"] == SIGNAL_QTY
    # C: no correction — null in every bucket.
    assert [row["proposed_quantity"] for row in c_rows] == [None] * 12

    # Each non-null value equals the proposed_quantity of the period
    # with the same start in the by-key correction, read right after
    # the capture; the null buckets are exactly the ones the
    # correction has no period for.
    for item, item_rows in (("A", a_rows), ("B", b_rows), ("C", c_rows)):
        body = _by_key(item)
        if item == "C":
            assert body["correction"] is None
            continue
        periods = {
            period["period_start"]: period["proposed_quantity"]
            for period in body["correction"]["periods"]
        }
        for row in item_rows:
            start = row["bucket_start"].isoformat()
            if row["proposed_quantity"] is not None:
                assert row["proposed_quantity"] == periods[start], row
            else:
                assert start not in periods, row

    # -- No read when nothing is stored. ------------------------------
    # A second capture of the date answers the stored snapshot,
    # created false, the same 36 rows — and calls neither stand-in.
    pre_settings = len(settings.calls)
    pre_measure = len(measure.calls)
    result = capture_snapshot()
    assert result == {
        "as_of": AS_OF,
        "origin": "live",
        "created": False,
        "item_sites": 3,
        "rows": 36,
    }
    assert settings.calls[pre_settings:] == []
    assert measure.calls[pre_measure:] == []

    # The backfill: created, no correction read, null on every row.
    pre_settings = len(settings.calls)
    pre_measure = len(measure.calls)
    result = capture_snapshot(as_of=BACKFILL_AS_OF)
    assert result == {
        "as_of": BACKFILL_AS_OF,
        "origin": "backfill",
        "created": True,
        "item_sites": 3,
        "rows": 36,
    }
    assert settings.calls[pre_settings:] == []
    assert measure.calls[pre_measure:] == []
    backfill = _stored_rows(BACKFILL_AS_OF)
    assert len(backfill) == 36, len(backfill)
    assert all(row["proposed_quantity"] is None for row in backfill)

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
    # Nothing stored: the two captures the admin made stand — and the
    # page still renders for the viewer, its reads answering as the
    # admin's do, while a request without a token still gets 401.
    stored = viewer_reloaded.get(SNAPSHOTS_URL, params={"limit": 50}).json()
    assert [(entry["as_of"], entry["origin"]) for entry in stored] == [
        (AS_OF.isoformat(), "live"),
        (BACKFILL_AS_OF.isoformat(), "backfill"),
    ]
    assert all(entry["item_sites"] == 3 and entry["rows"] == 36 for entry in stored)
    by_key = viewer_reloaded.get(
        BY_KEY_URL, params={"item_code": "A", "site_code": SITE}
    )
    assert by_key.status_code == 200, by_key.text
    assert len(by_key.json()["forecast"]) == 12
    assert TestClient(app).get(SNAPSHOTS_URL).status_code == 401

    # -- A failed read: demand added on the day after the as-of,
    #    date.today pinned one day later, _forecast_bias_accuracy
    #    raising: the capture stores its rows as usual, null proposed
    #    on every bucket, the stand-ins called once each. -------------
    _seed_failed_day()
    monkeypatch.setattr("app.stats.demand.date", _fixed_date(TODAY_FAILED))
    monkeypatch.setattr("app.forecast.history.date", _fixed_date(TODAY_FAILED))
    measure.raises = RuntimeError("the measure read is down")
    pre_settings = len(settings.calls)
    pre_measure = len(measure.calls)
    result = capture_snapshot()
    assert result == {
        "as_of": FAILED_AS_OF,
        "origin": "live",
        "created": True,
        "item_sites": 3,
        "rows": 36,
    }
    assert settings.calls[pre_settings:] == [()]
    assert measure.calls[pre_measure:] == [{}]
    failed = _stored_rows(FAILED_AS_OF)
    assert len(failed) == 36, len(failed)
    assert all(row["proposed_quantity"] is None for row in failed)

    # -- The migration: the column in the test database, exactly one
    #    head, and exactly one successor of the forecast-override-
    #    proposals revision — its id not compared with the head, so a
    #    later migration leaves the check unchanged. -------------------
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT is_nullable, data_type, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'forecast_snapshots' "
                "AND column_name = 'proposed_quantity'"
            )
            column = cur.fetchone()
    finally:
        conn.close()
    assert column is not None, "proposed_quantity is missing from forecast_snapshots"
    assert column["is_nullable"] == "YES", dict(column)
    assert column["data_type"] == "double precision", dict(column)
    assert column["column_default"] is None, dict(column)

    directory = ScriptDirectory(_MIGRATIONS_DIR)
    assert len(directory.get_heads()) == 1
    successors = [
        script
        for script in directory.walk_revisions()
        if script.down_revision == "0048_forecast_override_proposals"
    ]
    assert len(successors) == 1
