"""Issue #1365 proof: the correction proposed with a forecast override.

``POST /api/forecast/overrides`` stores, with every override it
records, the correction ``GET /api/forecast/by-key?include_correction
=true`` proposed for the item-site when the override was stored: the
``ForecastCorrection`` object in JSON, with every key — worked out
before the override is stored, the correction the admin was shown,
while the new override's period is still in it, through the by-key
route's own function, and stored on a new
``forecast_override_proposals`` table (one row per proposal, the
override's id the primary key and the foreign key to
``forecast_overrides.id`` with ON DELETE CASCADE, the correction
jsonb with a CHECK that it is a JSON object whose ``periods`` is an
array, ``created_at`` defaulted to ``now()``) written by a
repository with an ``insert`` and a ``for_overrides`` and nothing
else [rule: OVR-2].  A request the route refuses is refused exactly
as before, with the same status and ``detail`` and neither an
override nor a proposal stored for it — the non-admin's refusal
before the route runs, so neither correction read runs for it
[rule: OVR-10] — and while no correction is proposed for the period,
or working out or storing one fails, the override is stored and
answered as usual, with no proposal row.  Nothing in any answer
changes: the POST answers 201 with exactly the keys of
``OverrideRow``, no answer of a post, no row of
``GET /api/forecast/overrides`` and no row of ``GET /api/forecast``
or ``GET /api/forecast/by-key`` carries a ``proposed_correction``
key, and ``POST /api/forecast/overrides/withdrawals`` stores no
proposal.

A new ``GET /api/forecast/overrides/proposals`` route, on the
forecast router and open to every signed-in user, writes nothing and
answers ``ForecastOverrideProposalCounts`` over the current
overrides, the very rows the effective forecast counts — each
period's highest-``id`` row, when it is an ``override`` [rule:
OVR-2] [rule: OVR-9] [rule: OVR-13], read in one query — and their
stored proposals, one ``for_overrides`` read: ``overrides`` their
number; ``proposed`` those whose stored proposal has a period whose
``period_start`` is the override's own ``period_start`` (the
override's proposed period); ``taken`` those of them whose
``quantity``, rounded to 2 decimals, equals that period's
``proposed_quantity``; ``same_direction`` those of them for which
the override's ``quantity`` minus the period's ``quantity`` and the
period's ``proposed_quantity`` minus its ``quantity`` are both above
0 or both below 0; ``without_proposal`` the current overrides with
no stored proposal, or none for their period, every override stored
before this slice among them, so ``proposed`` plus
``without_proposal`` equals ``overrides``.

Everything is read through the app's test client, signed in with the
development tokens (admin and user, the way test 1313 signs in),
with the two correction reads replaced by stand-ins in
``app.api.forecast`` — ``_stored_system_settings`` answering ``{}``
(the bounds then the registry defaults, 0.5 and 3) and
``_forecast_bias_accuracy`` answering the as-of 2026-11-16 and two
``item_sites`` entries, each with every field ``realised_accuracy``
answers: A at 159 with 6 compared buckets and totals 900 and 500
(bias 0.8, factor 500/900) and B at 159 with 4 and totals 300 and
1000 (bias -0.7, factor 1000/300); C has no entry — while the demand
events are real: A, B and C at site 159, every day for 401 days
ending on 2026-11-16, ``date.today`` pinned to 2026-11-17 the way
test 374 pins it, so the shared default as-of is 2026-11-16: A and C
2.0 a day (12 forecast periods of 60.0, the first running
2026-11-17 to 2026-12-16), B 1.0 a day (12 periods of 30.0).  Both
stand-ins record their calls, and either can be made to raise.

Proved here:

* stored with each override — the admin reads the by-key correction
  for the item-site before each post, and posts, in order, each with
  a reason: A 2026-11-17 33.33 (the proposed quantity, 60 x 500/900
  rounded), A 2026-12-17 45, A 2027-01-16 80, B 2026-11-17 100 (the
  proposed quantity), B 2026-12-17 50, C 2026-11-17 70, A 2027-02-15
  20 (which the proof then withdraws), B 2026-11-17 90: each answers
  201 with exactly the keys of ``OverrideRow``, and for every post
  but C's and the second on B 2026-11-17 ``for_overrides`` on the
  stored override's id answers exactly the by-key read's
  ``correction`` before it; after the first post A's correction has
  11 periods (the overridden one left out), C's override has no
  proposal row (C has no correction), the second B 2026-11-17 has
  none (its period already carried an override) and the withdrawal
  stores none;
* the reads and refusals — each override post called
  ``_stored_system_settings`` once and ``_forecast_bias_accuracy``
  once; a post signed in with the user token answers 403 as before,
  calls neither read and stores nothing; a post for A on 2026-11-18,
  inside a period, answers 422 with the detail "The period start
  must be the first day of one of the 12 forecast periods." and
  stores neither an override nor a proposal;
* the answers — no answer of a post, no row of
  ``GET /api/forecast/overrides`` and no row of ``GET /api/forecast``
  or ``GET /api/forecast/by-key`` carries a ``proposed_correction``
  key;
* the counts — ``GET /api/forecast/overrides/proposals`` then
  answers exactly ``{"overrides": 6, "proposed": 4, "taken": 1,
  "same_direction": 3, "without_proposal": 2}``: the six current
  overrides are A 2026-11-17 (taken, the same direction), A
  2026-12-17 (the same direction), A 2027-01-16 (up against a
  proposal down), the second B 2026-11-17 (no proposal), B
  2026-12-17 (the same direction) and C's (no proposal) — neither
  the withdrawn A 2027-02-15 nor the first B 2026-11-17, which no
  longer applies, is counted; the user token gets the same answer
  and no token gets 401;
* a failed read — after the counts, a post on A 2027-03-17 with
  ``_forecast_bias_accuracy`` raising, one on A 2027-04-16 with
  ``_stored_system_settings`` raising and one on A 2027-05-16 with
  ``_override_proposal_repo.insert`` raising each answer 201 and
  store the override with no proposal row;
* the migration — the script directory has exactly one head, exactly
  one revision in its history has ``down_revision``
  ``0047_exception_ack_events`` (its id not compared with the head,
  so a later migration leaves the check unchanged), and the test
  database has the table and the constraint: storing
  ``{"periods": 1}`` through ``insert`` for a stored override that
  has no proposal raises ``psycopg2.errors.CheckViolation`` and
  stores nothing.

Identity — the proof also runs as a NON-ADMIN (role ``user``): a
second pass after a full reload, with the page booted the production
way (the token it would take from the URL hash as
``#access_token=...``), ``GET /api/auth/config`` answering publicly
and ``GET /api/auth/me`` resolving the viewer, sees the admin-only
override post refused 403 (nothing stored) while the page still
renders for the viewer.
"""

import os
from datetime import date, timedelta

import psycopg2
import pytest
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.forecast as forecast_api
from app.api.forecast import OverrideRow
from app.db.connection import get_connection
from app.main import app
from app.repositories.forecast_override_proposals_postgres_repository import (
    ForecastOverrideProposalPostgresRepository,
)

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"
VIEWER_SUB = "dev-user-1"  # the non-admin stub user's subject

OVERRIDES_URL = "/api/forecast/overrides"
PROPOSALS_URL = "/api/forecast/overrides/proposals"
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

# The eight override posts, in order.
POSTS = [
    ("A", date(2026, 11, 17), 33.33),  # the proposed quantity
    ("A", date(2026, 12, 17), 45.0),
    ("A", date(2027, 1, 16), 80.0),
    ("B", date(2026, 11, 17), 100.0),  # the proposed quantity
    ("B", date(2026, 12, 17), 50.0),
    ("C", date(2026, 11, 17), 70.0),
    ("A", date(2027, 2, 15), 20.0),  # withdrawn again below
    ("B", date(2026, 11, 17), 90.0),  # the period already overridden
]
WITHDRAWN_ITEM, WITHDRAWN_START = "A", date(2027, 2, 15)
# The 1-based post numbers whose stored override has no proposal row:
# C's (C has no correction) and the second on B 2026-11-17 (its
# period already carried an override when it was posted).
NO_PROPOSAL_POSTS = {6, 8}
# The inside-a-period refusal: the day after the first period's start.
INSIDE_PERIOD = AS_OF + timedelta(days=2)  # 2026-11-18

EXPECTED_COUNTS = {
    "overrides": 6,
    "proposed": 4,
    "taken": 1,
    "same_direction": 3,
    "without_proposal": 2,
}


def _fixed_date():
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return TODAY

    return _FixedDate


def _seed() -> None:
    """401 days of demand ending on the as-of date: 2.0 a day for A
    and C, 1.0 a day for B, all at site 159."""
    events = []
    for item, daily in (("A", DAILY_A), ("B", DAILY_B), ("C", DAILY_C)):
        for offset in range((AS_OF - HIST_START).days + 1):
            events.append(
                (
                    f"id-de-1365-{item}-{offset}",
                    f"de-1365-{item}-{offset}",
                    item,
                    SITE,
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


class _InsertDown:
    """The stand-in proposal ``insert``: records every call and
    raises, the way a down store would."""

    def __init__(self):
        self.calls = []

    def __call__(self, override_id, proposed_correction):
        self.calls.append((override_id, proposed_correction))
        raise RuntimeError("the proposal store is down")


def test_issue1365_freeform(client, monkeypatch):
    proposal_repo = ForecastOverrideProposalPostgresRepository()

    # -- The clock and the two correction reads, in app.api.forecast. --
    monkeypatch.setattr("app.stats.demand.date", _fixed_date())
    monkeypatch.setattr("app.forecast.history.date", _fixed_date())
    settings = _SettingsRead()
    measure = _MeasureRead()
    monkeypatch.setattr(forecast_api, "_stored_system_settings", settings)
    monkeypatch.setattr(forecast_api, "_forecast_bias_accuracy", measure)

    _seed()

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})

    def _by_key(item: str) -> dict:
        response = admin.get(
            BY_KEY_URL,
            params={
                "item_code": item,
                "site_code": SITE,
                "include_correction": "true",
            },
        )
        assert response.status_code == 200, response.text
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
    # The by-key read ran its two correction reads once each, the
    # settings' answer as the measure's one argument.
    assert settings.calls == [()]
    assert measure.calls == [{}]

    row = _by_key("B")
    assert [bucket["quantity"] for bucket in row["forecast"]] == [QTY_B] * 12
    correction = row["correction"]
    assert correction["factor"] == pytest.approx(FACTOR_B, rel=1e-12)
    assert [period["proposed_quantity"] for period in correction["periods"]] == [
        PROPOSED_B
    ] * 12

    # C has no measure entry: no correction.
    assert _by_key("C")["correction"] is None

    # -- Stored with each override: the eight posts, each with its
    #    by-key read first. ----------------------------------------------
    stored_ids = {}
    for index, (item, period_start, quantity) in enumerate(POSTS, start=1):
        pre = _by_key(item)
        correction = pre["correction"]
        settings_before = len(settings.calls)
        measure_before = len(measure.calls)
        response = admin.post(
            OVERRIDES_URL,
            json={
                "item_code": item,
                "site_code": SITE,
                "period_start": period_start.isoformat(),
                "quantity": quantity,
                "reason": f"override {index}",
            },
        )
        assert response.status_code == 201, response.text
        answered = response.json()
        # The answer is exactly OverrideRow's keys — no
        # proposed_correction in it.
        assert set(answered.keys()) == set(OverrideRow.model_fields), answered
        assert "proposed_correction" not in answered
        assert answered["item_code"] == item
        assert answered["site_code"] == SITE
        assert answered["period_start"] == period_start.isoformat()
        assert answered["kind"] == "override"
        assert answered["quantity"] == quantity
        assert answered["applies"] is True
        # The post's own reads: each of the two once — the proposal's
        # by-key read, worked out before the override is stored.
        assert settings.calls[settings_before:] == [()]
        assert measure.calls[measure_before:] == [{}]
        stored_id = answered["id"]
        stored_ids[(item, period_start.isoformat(), index)] = stored_id
        if index in NO_PROPOSAL_POSTS:
            assert proposal_repo.for_overrides([stored_id]) == {}
        else:
            # The stored proposal is exactly the correction the
            # by-key read answered before the post.
            assert proposal_repo.for_overrides([stored_id]) == {stored_id: correction}
        if index == 1:
            # After the first post, A's correction has 11 periods: the
            # overridden one left out.
            after = _by_key("A")["correction"]
            assert len(after["periods"]) == 11
            assert all(
                period["period_start"] != PERIOD_1_START.isoformat()
                for period in after["periods"]
            )
        if index == 8:
            # B's second 2026-11-17 stored no proposal: its period
            # already carried an override, so the by-key read's
            # correction no longer proposes it.
            assert len(correction["periods"]) == 10
            assert all(
                period["period_start"] != PERIOD_1_START.isoformat()
                for period in correction["periods"]
            )

    # The withdrawal: A 2027-02-15 again — it stores no proposal row,
    # while the withdrawn override's own proposal stands with it.
    response = admin.post(
        f"{OVERRIDES_URL}/withdrawals",
        json={
            "item_code": WITHDRAWN_ITEM,
            "site_code": SITE,
            "period_start": WITHDRAWN_START.isoformat(),
            "reason": "the withdrawal",
        },
    )
    assert response.status_code == 201, response.text
    answered = response.json()
    assert set(answered.keys()) == set(OverrideRow.model_fields), answered
    assert answered["kind"] == "withdrawal"
    assert answered["quantity"] is None
    assert answered["applies"] is False
    assert "proposed_correction" not in answered
    assert proposal_repo.for_overrides([answered["id"]]) == {}
    withdrawn_id = stored_ids[(WITHDRAWN_ITEM, WITHDRAWN_START.isoformat(), 7)]
    assert proposal_repo.for_overrides([withdrawn_id]) != {}

    # -- The reads and refusals. ------------------------------------------
    # A post signed in with the user token is refused as before, before
    # the route runs: neither correction read runs, and nothing is
    # stored.
    pre_settings = len(settings.calls)
    pre_measure = len(measure.calls)
    response = viewer.post(
        OVERRIDES_URL,
        json={
            "item_code": "A",
            "site_code": SITE,
            "period_start": PERIOD_1_START.isoformat(),
            "quantity": 10.0,
            "reason": "the viewer cannot post overrides",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json() == {
        "detail": "Role 'admin' is required to perform this action."
    }
    assert settings.calls[pre_settings:] == []
    assert measure.calls[pre_measure:] == []
    rows = admin.get(OVERRIDES_URL, params={"limit": 1000}).json()
    assert len(rows) == 9  # the eight overrides and the withdrawal

    # A post for A inside a period is refused as before — and it
    # stored neither an override nor a proposal.
    response = admin.post(
        OVERRIDES_URL,
        json={
            "item_code": "A",
            "site_code": SITE,
            "period_start": INSIDE_PERIOD.isoformat(),
            "quantity": 5.0,
            "reason": "inside a period",
        },
    )
    assert response.status_code == 422, response.text
    assert response.json() == {
        "detail": "The period start must be the first day of one of the "
        "12 forecast periods."
    }
    rows = admin.get(OVERRIDES_URL, params={"limit": 1000}).json()
    assert len(rows) == 9
    assert all(row["period_start"] != INSIDE_PERIOD.isoformat() for row in rows)

    # -- Answers unchanged: no proposed_correction anywhere. --------------
    rows = admin.get(OVERRIDES_URL, params={"limit": 1000}).json()
    assert len(rows) == 9
    for row in rows:
        assert set(row.keys()) == set(OverrideRow.model_fields), row
        assert "proposed_correction" not in row
    response = admin.get("/api/forecast")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [row["item_code"] for row in rows] == ["A", "C", "B"]
    for row in rows:
        assert "proposed_correction" not in row
        assert "correction" not in row
    row = _by_key("A")
    assert "proposed_correction" not in row
    assert row["correction"] is not None  # include_correction is on

    # -- The counts. -------------------------------------------------------
    response = admin.get(PROPOSALS_URL)
    assert response.status_code == 200, response.text
    counts = response.json()
    assert counts == EXPECTED_COUNTS
    # The invariant: every current override is proposed a period or
    # has no proposal for it.
    assert counts["proposed"] + counts["without_proposal"] == counts["overrides"]
    # The user token gets the same answer — and no signed-in token
    # gets 401, the bare development token (the client fixture's)
    # among them.
    response = viewer.get(PROPOSALS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == counts
    response = client.get(PROPOSALS_URL)
    assert response.status_code == 200, response.text
    assert response.json() == counts

    # -- Identity, second pass after a full reload: the page boots the
    #    production way for the non-admin (the token it would take from
    #    the URL hash as #access_token=...), /api/auth/config answers
    #    publicly and /api/auth/me resolves the viewer: the admin-only
    #    override post is refused 403 (nothing stored) while the page
    #    still renders for the viewer. -------------------------------------
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
    response = viewer_reloaded.post(
        OVERRIDES_URL,
        json={
            "item_code": "A",
            "site_code": SITE,
            "period_start": (AS_OF + timedelta(days=211)).isoformat(),
            "quantity": 10.0,
            "reason": "the viewer cannot post overrides",
        },
    )
    assert response.status_code == 403, response.text
    assert response.json() == {
        "detail": "Role 'admin' is required to perform this action."
    }
    # Nothing stored, and the page still renders: the viewer's reads
    # answer as the admin's do, and a request without a token still
    # gets 401.
    rows = viewer_reloaded.get(OVERRIDES_URL, params={"limit": 1000}).json()
    assert len(rows) == 9
    for row in rows:
        assert "proposed_correction" not in row
    assert viewer_reloaded.get(PROPOSALS_URL).json() == counts
    assert TestClient(app).get(PROPOSALS_URL).status_code == 401

    # -- A failed read: _forecast_bias_accuracy raising.  Both reads run —
    #    the measure raised while working out the correction — and the
    #    override is stored, with no proposal row. --------------------------
    measure.raises = RuntimeError("the measure read is down")
    pre_settings = len(settings.calls)
    pre_measure = len(measure.calls)
    response = admin.post(
        OVERRIDES_URL,
        json={
            "item_code": "A",
            "site_code": SITE,
            "period_start": (AS_OF + timedelta(days=121)).isoformat(),
            "quantity": 10.0,
            "reason": "the measure read is down",
        },
    )
    assert response.status_code == 201, response.text
    assert set(response.json().keys()) == set(OverrideRow.model_fields)
    assert "proposed_correction" not in response.json()
    assert settings.calls[pre_settings:] == [()]
    assert measure.calls[pre_measure:] == [{}]
    failed_measure_id = response.json()["id"]
    assert proposal_repo.for_overrides([failed_measure_id]) == {}
    measure.raises = None

    # -- A failed read: _stored_system_settings raising.  The settings
    #    read ran and raised: the measure read never ran, and the
    #    override is stored, with no proposal row. --------------------------
    settings.raises = RuntimeError("the settings read is down")
    pre_settings = len(settings.calls)
    pre_measure = len(measure.calls)
    response = admin.post(
        OVERRIDES_URL,
        json={
            "item_code": "A",
            "site_code": SITE,
            "period_start": (AS_OF + timedelta(days=151)).isoformat(),
            "quantity": 10.0,
            "reason": "the settings read is down",
        },
    )
    assert response.status_code == 201, response.text
    assert set(response.json().keys()) == set(OverrideRow.model_fields)
    assert "proposed_correction" not in response.json()
    assert settings.calls[pre_settings:] == [()]
    assert measure.calls[pre_measure:] == []
    failed_settings_id = response.json()["id"]
    assert proposal_repo.for_overrides([failed_settings_id]) == {}
    settings.raises = None

    # -- A failed read: the proposal store down.  The correction is
    #    proposed for the period and the store is called — and down: the
    #    override is stored, with no proposal row. ---------------------------
    insert_down = _InsertDown()
    monkeypatch.setattr(forecast_api._override_proposal_repo, "insert", insert_down)
    failed_insert_start = AS_OF + timedelta(days=181)
    response = admin.post(
        OVERRIDES_URL,
        json={
            "item_code": "A",
            "site_code": SITE,
            "period_start": failed_insert_start.isoformat(),
            "quantity": 10.0,
            "reason": "the proposal store is down",
        },
    )
    assert response.status_code == 201, response.text
    assert set(response.json().keys()) == set(OverrideRow.model_fields)
    failed_insert_id = response.json()["id"]
    assert insert_down.calls[0][0] == failed_insert_id
    # The proposed correction carried the post's own period.
    called_starts = [
        period["period_start"] for period in insert_down.calls[0][1]["periods"]
    ]
    assert failed_insert_start.isoformat() in called_starts
    assert proposal_repo.for_overrides([failed_insert_id]) == {}

    # -- The migration: exactly one head, exactly one successor of
    #    0047 (its id not compared with the head, so a later migration
    #    leaves the check unchanged), and the table and the constraint
    #    in the test database. -----------------------------------------------
    directory = ScriptDirectory(_MIGRATIONS_DIR)
    assert len(directory.get_heads()) == 1
    history = list(directory.walk_revisions())
    successors = [
        script
        for script in history
        if script.down_revision == "0047_exception_ack_events"
    ]
    assert len(successors) == 1
    # The table and the constraint, in the test database.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT to_regclass('public.forecast_override_proposals')")
            assert cur.fetchone()[0] is not None
            cur.execute(
                "SELECT 1 FROM pg_constraint "
                "WHERE conname = 'ck_forecast_override_proposals_shape'"
            )
            assert cur.fetchone() is not None
    finally:
        conn.close()
    # The constraint: a proposal whose periods is not an array is a
    # check violation, and nothing is stored — C's override has no
    # proposal row.
    c_id = stored_ids[("C", date(2026, 11, 17).isoformat(), 6)]
    with pytest.raises(psycopg2.errors.CheckViolation):
        proposal_repo.insert(c_id, {"periods": 1})
    assert proposal_repo.for_overrides([c_id]) == {}
