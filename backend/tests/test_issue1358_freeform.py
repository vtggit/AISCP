"""Issue #1358 proof: the forecast correction beside the forecast.

``GET /api/forecast`` and ``GET /api/forecast/by-key`` take
``include_correction``, a boolean query parameter, false by default,
declared the way ``GET /api/stockout`` declares ``include_remedy``.
While it is false each route answers exactly as today, key for key:
no row carries a ``correction`` key and neither of the two correction
reads runs.  ``ForecastRow`` gains ``correction``, a
``ForecastCorrection`` or null, and ``ForecastRowDetail`` inherits it;
keeping the key out takes the same private state the stockout remedy
rides on — a ``_include_correction`` attribute, false by default, a
wrap validator that reads it from the answered row's
``include_correction`` key (or a validated row's own attribute) and a
wrap serializer that drops ``correction`` while it is false.

While the parameter is true and at least one row is answered, the
route reads the stored system settings and the bias exception's
measure, once each, before it works out any correction — through
names in ``app.api.forecast`` (``_stored_system_settings`` and
``_forecast_bias_accuracy``, imported from ``app.api.exceptions`` the
way ``app.api.erp_currencies`` imports them), so a test can replace
each.  The two bounds are ``setting_value`` from ``app.ddmrp.zones``
on the two keys from ``app.api.exceptions`` (a missing or out-of-range
stored value the registry default, 0.5 and 3), and the item-sites
whose bias persists are the ones ``_forecast_rows`` — the very
function the exceptions list raises its forecast bias rows with, never
a copy — answers a row for.  With no row answered (an ``offset`` past
the rows) neither read runs, and anything raised while the reads run
or the corrections are worked out is caught: every answered row's
``correction`` is then null and the route still answers 200 with the
rows it answers without the parameter.

An answered row gets a correction only when ``_forecast_rows``
answered a row for its item-site, its figures from the measure's
``item_sites`` entry with the same codes: ``factor`` the entry's
actual total over its effective total (null when either is not a
finite number above 0, there is nothing to scale), one
``ForecastCorrectionPeriod`` per forecast bucket with no override day
and no demand signal, in order (its proposed quantity
``max(0, round(quantity x factor, 2))``, null again when no bucket is
left), ``measured_on`` the exception row's detected date, the entry's
own ``compared_buckets``, ``effective_total``, ``actual_total`` and
``effective_bias``, the unrounded ``factor`` and the one-line
``reason``.  The routes write nothing and call no ERP.

Everything is read through the app's test client, signed in the way
test 1344 signs in, with every read of the route replaced by a
stand-in in ``app.api.forecast`` that answers fixed data at as-of
2026-11-16, so no test reads the wall clock or the database:

* ``forecast_rows`` answers, on every call, nine fresh rows at as-of
  2026-11-16, with every ``ForecastRow`` field, in this order: A, B,
  C, D, E, F, G and H at site 159, then A at site 160;
* each row has three buckets: 2026-11-17 to 2026-12-16,
  2026-12-17 to 2027-01-15 and 2027-01-16 to 2027-02-14; a bucket's
  ``effective_quantity`` is its ``quantity`` and its
  ``override_days`` and ``signal_quantity`` are 0, unless named here:
  A's quantities are 120; 90 with a signal of 60 (effective 150); and
  45.9.  B's are 30; 12.5 overridden on all 30 days (effective 40);
  and 0.  F's are 40 overridden on all 30 days (effective 55); 40
  overridden on 10 days (effective 46); and 40 with a signal of 25
  (effective 65).  G's are -3, a quantity no forecast holds
  (AIERP-D-60), there to prove the floor; 7.5; and 1.  C, D, E, H
  and A at 160 have 50 in each bucket; the totals and
  ``overridden_buckets`` follow from the buckets;
* ``_forecast_bias_accuracy`` answers, whatever settings it is given,
  the as-of 2026-11-16 and these ``item_sites``, each entry with
  every field ``realised_accuracy`` answers and
  ``effective_bias`` equal to (effective total - actual total) over
  the actual total — given as compared buckets, effective total,
  actual total (bias): A at 159: 6, 900, 500 (0.8); B: 4, 300, 1000
  (-0.7); C: 2, 300, 100 (2.0); D: 12, 1100, 1000 (0.1); E: 5, 0, 20
  (-1.0); F: 8, 640, 400 (0.6); G: 3, 150, 100 (0.5); Z at 159, an
  item-site with no row: 10, 200, 100 (1.0); H and A at 160: no
  entry;
* ``_stored_system_settings`` answers ``{}`` (the bounds then read
  the registry defaults, 0.5 and 3), records its calls and can be
  made to raise; the measure read records its calls (its argument
  included) and can be made to raise too;
* for ``by-key``: ``demand_stats_for_pair`` answers, for the codes of
  one of the nine rows, those codes, the demand class ``smooth`` and
  the as-of 2026-11-16, and None for any other codes;
  ``history_for_pair`` answers one bucket, 2026-10-18 to
  2026-11-16 with quantity 100; ``build_forecast_row`` answers a
  fresh copy of that item-site's row; ``effective_for_all`` answers
  the rows it is given, unchanged;
* ``create_override``, ``withdraw_override`` and ``capture_snapshot``
  fail the test if they are called.

It checks: without the parameter the list answers the nine rows in
order, each with exactly today's fourteen keys and no ``correction``
key, and ``by-key`` for A at 159 answers A's row plus ``history``
with no ``correction`` key — neither read was called.  With it the
same nine rows in the same order, each with exactly one key more,
``correction``: A carries the two periods without a signal (proposed
at 55.6% of its forecast), B the two periods without an override
(333.3%) and G, at both bounds, all three periods (66.7%, the -3
bucket floored to 0); the three ``factor``s equal 500/900, 1000/300
and 100/150 within 1e-12 and every other figure is exact; null for
C (2 compared periods, fewer than 3), D (a bias of 0.1), E (an
exception row, but its measured forecast total is 0, so there is
nothing to scale), F (every period overridden, partly overridden or
with a signal), H (no entry in the measure) and A at 160 (no entry
at that site).  The exception agrees: ``_forecast_rows`` from
``app.api.exceptions``, given the stand-in measure, 0.5 and 3,
answers rows for exactly A, B, E, F, G and Z at 159, each corrected
row one of them, its reason beginning with that row's ``detail``,
first letter upper-cased.  The settings move both: with
``_stored_system_settings`` answering
``{"exceptions.forecast_bias_threshold": 0.75}`` the measure is
called with that answer, A carries the same correction as above, and
B and G read null.  ``by-key`` with the parameter carries A's
correction and reads null for H; Z at 159, which has no row, answers
404 and neither read is called.  Each request with the parameter and
at least one row calls ``_stored_system_settings`` once and
``_forecast_bias_accuracy`` once, with the settings' answer as its
one argument — that holds also when the request names
``as_of=2026-10-01``; with ``offset=9`` the list answers no rows and
neither read is called.  When the settings read raises, and again
when the measure read raises, every row's ``correction`` is null,
every other key reads as without the parameter, and the list answers
200; ``by-key`` for A answers 200 with ``correction`` null when the
measure read raises.  ``include_correction=xyz`` answers 422 on both
routes.  The app's OpenAPI document lists ``include_correction``
among both routes' query parameters (boolean, default false), each
route's description mentions ``include_correction`` and
``correction``, ``ForecastRow``'s properties are today's fourteen
plus ``correction`` (``ForecastCorrection`` or null) and
``ForecastRowDetail``'s are those plus ``history``;
``ForecastCorrection`` has exactly the eight keys and
``ForecastCorrectionPeriod`` exactly the four, and
``include_correction`` is in neither ``ForecastRow``'s fields nor its
schema.

Identity — the proof runs as a NON-ADMIN (role ``user``, asserted via
``GET /api/auth/me``): a fresh test client booted the production way
for the viewer (the token the page would take from the URL hash as
``#access_token=...``, ``GET /api/auth/config`` answering publicly
and ``GET /api/auth/me`` resolving the viewer) reads the whole list
with corrections, the same answer the admin's — the route has no
admin-only control, the page still renders — and a request without a
token still gets 401 on both routes.  Nothing is written: none of
the three writes was called.
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.api.forecast as forecast_api
from app.api.exceptions import _forecast_rows
from app.main import app

ADMIN_TOKEN = "dev-secret-token:admin"
VIEWER_TOKEN = "dev-secret-token:user"

LIST_URL = "/api/forecast"
BY_KEY_URL = "/api/forecast/by-key"
AS_OF = date(2026, 11, 16)

# The fourteen keys every forecast row answers today.
ROW_KEYS = {
    "item_code",
    "site_code",
    "as_of",
    "demand_class",
    "method",
    "params",
    "history_buckets",
    "total_forecast",
    "forecast",
    "total_effective_forecast",
    "total_signal_quantity",
    "overridden_buckets",
    "backtest",
    "arithmetic",
}

# The three forecast buckets every stand-in row carries, in order.
BUCKET_BOUNDS = [
    (date(2026, 11, 17), date(2026, 12, 16)),
    (date(2026, 12, 17), date(2027, 1, 15)),
    (date(2027, 1, 16), date(2027, 2, 14)),
]


def _bucket(quantity, override_days=0, signal=0.0, effective=None):
    """One stand-in bucket: its effective quantity is its quantity,
    and its override_days and signal_quantity are 0, unless named."""
    return {
        "quantity": quantity,
        "override_days": override_days,
        "signal": signal,
        "effective": effective if effective is not None else quantity,
    }


# The nine stand-in rows, in the order the stand-in forecast_rows
# answers them: A, B, C, D, E, F, G and H at site 159, then A at 160.
BUCKETS = {
    ("A", "159"): [
        _bucket(120),
        _bucket(90, signal=60, effective=150),
        _bucket(45.9),
    ],
    ("B", "159"): [
        _bucket(30),
        _bucket(12.5, override_days=30, effective=40),
        _bucket(0),
    ],
    ("C", "159"): [_bucket(50), _bucket(50), _bucket(50)],
    ("D", "159"): [_bucket(50), _bucket(50), _bucket(50)],
    ("E", "159"): [_bucket(50), _bucket(50), _bucket(50)],
    ("F", "159"): [
        _bucket(40, override_days=30, effective=55),
        _bucket(40, override_days=10, effective=46),
        _bucket(40, signal=25, effective=65),
    ],
    ("G", "159"): [_bucket(-3), _bucket(7.5), _bucket(1)],
    ("H", "159"): [_bucket(50), _bucket(50), _bucket(50)],
    ("A", "160"): [_bucket(50), _bucket(50), _bucket(50)],
}
ORDER = [
    ("A", "159"),
    ("B", "159"),
    ("C", "159"),
    ("D", "159"),
    ("E", "159"),
    ("F", "159"),
    ("G", "159"),
    ("H", "159"),
    ("A", "160"),
]


def _row(item_code, site_code):
    """One fresh stand-in row with every ForecastRow field."""
    buckets = BUCKETS[(item_code, site_code)]
    return {
        "item_code": item_code,
        "site_code": site_code,
        "as_of": AS_OF,
        "demand_class": "smooth",
        "method": "naive",
        "params": {},
        "history_buckets": 12,
        "total_forecast": sum(bucket["quantity"] for bucket in buckets),
        "forecast": [
            {
                "bucket_start": start,
                "bucket_end": end,
                "quantity": bucket["quantity"],
                "effective_quantity": bucket["effective"],
                "override_days": bucket["override_days"],
                "signal_quantity": bucket["signal"],
            }
            for (start, end), bucket in zip(BUCKET_BOUNDS, buckets)
        ],
        "total_effective_forecast": sum(bucket["effective"] for bucket in buckets),
        "total_signal_quantity": sum(bucket["signal"] for bucket in buckets),
        "overridden_buckets": sum(
            1 for bucket in buckets if bucket["override_days"] > 0
        ),
        "backtest": None,
        "arithmetic": "stand-in",
    }


def _standin_rows():
    """The nine stand-in rows, fresh on every call, in order."""
    return [_row(item_code, site_code) for item_code, site_code in ORDER]


def _entry(item_code, site_code, compared, effective, actual):
    """One measure item-site entry: every field realised_accuracy
    answers, effective_bias (effective - actual) over actual."""
    return {
        "item_code": item_code,
        "site_code": site_code,
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
    """The stand-in measure: the as-of 2026-11-16 and the item-sites
    A, B, C, D, E, F, G and Z at 159 (H and A at 160 have no entry)."""
    sites = [
        _entry("A", "159", 6, 900, 500),
        _entry("B", "159", 4, 300, 1000),
        _entry("C", "159", 2, 300, 100),
        _entry("D", "159", 12, 1100, 1000),
        _entry("E", "159", 5, 0, 20),
        _entry("F", "159", 8, 640, 400),
        _entry("G", "159", 3, 150, 100),
        _entry("Z", "159", 10, 200, 100),
    ]
    return {
        "as_of": AS_OF,
        "compared_buckets": sum(site["compared_buckets"] for site in sites),
        "overall": {
            "forecast_total": 0.0,
            "actual_total": 0.0,
            "wape": None,
            "bias": None,
            "effective_total": 0.0,
            "effective_wape": None,
            "effective_bias": None,
            "overridden_buckets": 0,
            "value_added": None,
        },
        "by_horizon": [],
        "item_sites": sites,
    }


class _ForecastRowsRead:
    """The stand-in forecast_rows: on every call the nine fresh rows,
    whatever the request asks."""

    def __init__(self):
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        return _standin_rows()


class _SettingsRead:
    """The stand-in stored system settings read: records every call,
    answers the configured payload and can be made to raise."""

    def __init__(self, answer):
        self.answer = answer
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


class _StatsRead:
    """The stand-in demand_stats_for_pair: the codes, the demand
    class smooth and the as-of 2026-11-16 for one of the nine rows,
    None for any other codes."""

    def __call__(self, item_code, site_code, as_of):
        if (item_code, site_code) in BUCKETS:
            return {
                "item_code": item_code,
                "site_code": site_code,
                "demand_class": "smooth",
                "as_of": AS_OF,
            }
        return None


class _HistoryRead:
    """The stand-in history_for_pair: one bucket, 2026-10-18 to
    2026-11-16 with quantity 100."""

    def __call__(self, item_code, site_code, as_of):
        return [
            {"bucket_start": date(2026, 10, 18), "bucket_end": AS_OF, "quantity": 100.0}
        ]


class _BuildRead:
    """The stand-in build_forecast_row: a fresh copy of that
    item-site's row."""

    def __call__(self, item_code, site_code, demand_class, as_of, history):
        return _row(item_code, site_code)


class _EffectiveRead:
    """The stand-in effective_for_all: the rows it is given,
    unchanged."""

    def __call__(self, rows):
        return rows


# The answered shape of A at 159's row, as JSON, without the
# parameter.
A_ROW = {
    "item_code": "A",
    "site_code": "159",
    "as_of": "2026-11-16",
    "demand_class": "smooth",
    "method": "naive",
    "params": {},
    "history_buckets": 12,
    "total_forecast": 255.9,
    "forecast": [
        {
            "bucket_start": "2026-11-17",
            "bucket_end": "2026-12-16",
            "quantity": 120.0,
            "effective_quantity": 120.0,
            "override_days": 0,
            "signal_quantity": 0.0,
        },
        {
            "bucket_start": "2026-12-17",
            "bucket_end": "2027-01-15",
            "quantity": 90.0,
            "effective_quantity": 150.0,
            "override_days": 0,
            "signal_quantity": 60.0,
        },
        {
            "bucket_start": "2027-01-16",
            "bucket_end": "2027-02-14",
            "quantity": 45.9,
            "effective_quantity": 45.9,
            "override_days": 0,
            "signal_quantity": 0.0,
        },
    ],
    "total_effective_forecast": 315.9,
    "total_signal_quantity": 60.0,
    "overridden_buckets": 0,
    "backtest": None,
    "arithmetic": "stand-in",
}

# The three corrections the answer carries: A's (the period with a
# signal is left out), B's (the overridden period is left out) and
# G's, at both bounds, all three periods, the -3 bucket floored to 0.
A_CORRECTION = {
    "measured_on": "2026-11-16",
    "compared_buckets": 6,
    "effective_total": 900.0,
    "actual_total": 500.0,
    "effective_bias": 0.8,
    "factor": 500.0 / 900.0,
    "periods": [
        {
            "period_start": "2026-11-17",
            "period_end": "2026-12-16",
            "quantity": 120.0,
            "proposed_quantity": 66.67,
        },
        {
            "period_start": "2027-01-16",
            "period_end": "2027-02-14",
            "quantity": 45.9,
            "proposed_quantity": 25.5,
        },
    ],
    "reason": (
        "Forecast over actual by 80% across 6 periods, so each period "
        "without an override or a demand signal is proposed at 55.6% "
        "of its forecast."
    ),
}
B_CORRECTION = {
    "measured_on": "2026-11-16",
    "compared_buckets": 4,
    "effective_total": 300.0,
    "actual_total": 1000.0,
    "effective_bias": -0.7,
    "factor": 1000.0 / 300.0,
    "periods": [
        {
            "period_start": "2026-11-17",
            "period_end": "2026-12-16",
            "quantity": 30.0,
            "proposed_quantity": 100.0,
        },
        {
            "period_start": "2027-01-16",
            "period_end": "2027-02-14",
            "quantity": 0.0,
            "proposed_quantity": 0.0,
        },
    ],
    "reason": (
        "Forecast under actual by 70% across 4 periods, so each period "
        "without an override or a demand signal is proposed at 333.3% "
        "of its forecast."
    ),
}
G_CORRECTION = {
    "measured_on": "2026-11-16",
    "compared_buckets": 3,
    "effective_total": 150.0,
    "actual_total": 100.0,
    "effective_bias": 0.5,
    "factor": 100.0 / 150.0,
    "periods": [
        {
            "period_start": "2026-11-17",
            "period_end": "2026-12-16",
            "quantity": -3.0,
            "proposed_quantity": 0.0,
        },
        {
            "period_start": "2026-12-17",
            "period_end": "2027-01-15",
            "quantity": 7.5,
            "proposed_quantity": 5.0,
        },
        {
            "period_start": "2027-01-16",
            "period_end": "2027-02-14",
            "quantity": 1.0,
            "proposed_quantity": 0.67,
        },
    ],
    "reason": (
        "Forecast over actual by 50% across 3 periods, so each period "
        "without an override or a demand signal is proposed at 66.7% "
        "of its forecast."
    ),
}
CORRECTIONS = {
    ("A", "159"): A_CORRECTION,
    ("B", "159"): B_CORRECTION,
    ("G", "159"): G_CORRECTION,
}
NULL_CORRECTIONS = [
    ("C", "159"),
    ("D", "159"),
    ("E", "159"),
    ("F", "159"),
    ("H", "159"),
    ("A", "160"),
]


def _assert_correction(got, expected):
    """The correction's figures: the factor within 1e-12, every
    other figure exact."""
    assert got is not None
    for field, value in expected.items():
        if field == "factor":
            assert abs(got[field] - value) < 1e-12, (field, got[field], value)
        else:
            assert got[field] == value, (field, got[field], value)
    assert set(got) == set(expected)


def test_issue1358_freeform(client, monkeypatch):
    # -- The stand-ins: every read of the route, in app.api.forecast. --
    rows_read = _ForecastRowsRead()
    settings = _SettingsRead({})
    measure = _MeasureRead()
    monkeypatch.setattr(forecast_api, "forecast_rows", rows_read)
    monkeypatch.setattr(forecast_api, "_stored_system_settings", settings)
    monkeypatch.setattr(forecast_api, "_forecast_bias_accuracy", measure)
    monkeypatch.setattr(forecast_api, "demand_stats_for_pair", _StatsRead())
    monkeypatch.setattr(forecast_api, "history_for_pair", _HistoryRead())
    monkeypatch.setattr(forecast_api, "build_forecast_row", _BuildRead())
    monkeypatch.setattr(forecast_api, "effective_for_all", _EffectiveRead())

    # -- Nothing is written: every store write fails the test. --------
    def _no_write(*args, **kwargs):
        pytest.fail("the route wrote to a store")

    monkeypatch.setattr(forecast_api, "create_override", _no_write)
    monkeypatch.setattr(forecast_api, "withdraw_override", _no_write)
    monkeypatch.setattr(forecast_api, "capture_snapshot", _no_write)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    anon = TestClient(app)

    # The proof's main client signs in as a NON-ADMIN (role "user").
    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["roles"] == ["user"]

    # ------------------------------------------------------------------
    # Without the parameter: exactly today's answer, key for key, and
    # neither of the two correction reads runs.
    # ------------------------------------------------------------------
    response = client.get(LIST_URL, params={"limit": 1000})
    assert response.status_code == 200, response.text
    rows = response.json()
    assert [(row["item_code"], row["site_code"]) for row in rows] == ORDER
    for row in rows:
        assert set(row) == ROW_KEYS
        assert "correction" not in row
    assert rows[0] == A_ROW
    # The totals follow from the buckets.
    totals = {
        ("B", "159"): (42.5, 70.0, 0.0, 1),
        ("F", "159"): (120.0, 166.0, 25.0, 2),
        ("G", "159"): (5.5, 5.5, 0.0, 0),
    }
    for row in rows:
        key = (row["item_code"], row["site_code"])
        expected = totals.get(key, (150.0, 150.0, 0.0, 0))
        if key == ("A", "159"):
            expected = (255.9, 315.9, 60.0, 0)
        assert (
            row["total_forecast"],
            row["total_effective_forecast"],
            row["total_signal_quantity"],
            row["overridden_buckets"],
        ) == expected, key
    assert settings.calls == []
    assert measure.calls == []

    detail = client.get(BY_KEY_URL, params={"item_code": "A", "site_code": "159"})
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert set(body) == ROW_KEYS | {"history"}
    assert "correction" not in body
    assert {key: value for key, value in body.items() if key != "history"} == A_ROW
    assert body["history"] == [
        {"bucket_start": "2026-10-18", "bucket_end": "2026-11-16", "quantity": 100.0}
    ]
    assert settings.calls == []
    assert measure.calls == []

    # ------------------------------------------------------------------
    # With it: the same nine rows in the same order, each with exactly
    # one key more, correction.
    # ------------------------------------------------------------------
    response = client.get(
        LIST_URL, params={"limit": 1000, "include_correction": "true"}
    )
    assert response.status_code == 200, response.text
    corrected = response.json()
    assert [(row["item_code"], row["site_code"]) for row in corrected] == ORDER
    for corrected_row, plain_row in zip(corrected, rows):
        assert set(corrected_row) == ROW_KEYS | {"correction"}
        assert {
            key: value for key, value in corrected_row.items() if key != "correction"
        } == plain_row
    by_key = {(row["item_code"], row["site_code"]): row for row in corrected}
    for key, expected in CORRECTIONS.items():
        _assert_correction(by_key[key]["correction"], expected)
    for key in NULL_CORRECTIONS:
        assert by_key[key]["correction"] is None, key
    # The one read each, before any correction, the measure with the
    # settings' answer as its one argument.
    assert settings.calls == [()]
    assert measure.calls == [{}]

    # ------------------------------------------------------------------
    # The exception agrees: the very rule the exceptions list raises
    # its forecast bias rows with answers exactly A, B, E, F, G and Z
    # at 159; each corrected row is one of them, its reason beginning
    # with that row's detail, first letter upper-cased.
    # ------------------------------------------------------------------
    exception_rows = _forecast_rows(_measure(), 0.5, 3)
    exception_pairs = {(row.item_code, row.site_code) for row in exception_rows}
    assert exception_pairs == {
        ("A", "159"),
        ("B", "159"),
        ("E", "159"),
        ("F", "159"),
        ("G", "159"),
        ("Z", "159"),
    }
    detail_by_pair = {
        (row.item_code, row.site_code): row.detail for row in exception_rows
    }
    for key, row in by_key.items():
        if row["correction"] is not None:
            assert key in exception_pairs
            detail = detail_by_pair[key]
            assert row["correction"]["reason"].startswith(
                detail[:1].upper() + detail[1:]
            ), key

    # ------------------------------------------------------------------
    # The reads: once each per request with the parameter and at
    # least one row — also when the request names its own as_of; and
    # neither runs with no row answered.
    # ------------------------------------------------------------------
    response = client.get(
        LIST_URL,
        params={
            "limit": 1000,
            "include_correction": "true",
            "as_of": "2026-10-01",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json() == corrected
    assert settings.calls == [(), ()]
    assert measure.calls == [{}, {}]

    response = client.get(
        LIST_URL,
        params={"limit": 1000, "offset": 9, "include_correction": "true"},
    )
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert settings.calls == [(), ()]
    assert measure.calls == [{}, {}]

    # ------------------------------------------------------------------
    # The settings move both: a higher threshold moves the bounds and
    # the measure together — A keeps its correction, B and G read
    # null.
    # ------------------------------------------------------------------
    settings.answer = {"exceptions.forecast_bias_threshold": 0.75}
    response = client.get(
        LIST_URL, params={"limit": 1000, "include_correction": "true"}
    )
    assert response.status_code == 200, response.text
    raised = {(row["item_code"], row["site_code"]): row for row in response.json()}
    _assert_correction(raised[("A", "159")]["correction"], A_CORRECTION)
    assert raised[("B", "159")]["correction"] is None
    assert raised[("G", "159")]["correction"] is None
    assert settings.calls[-1] == ()
    assert measure.calls[-1] == {"exceptions.forecast_bias_threshold": 0.75}
    settings.answer = {}

    # ------------------------------------------------------------------
    # by-key with the parameter: A's detail carries the same
    # correction as A's list row; H's reads null; Z at 159, which has
    # no row, answers 404 and neither read is called.
    # ------------------------------------------------------------------
    response = client.get(
        BY_KEY_URL,
        params={"item_code": "A", "site_code": "159", "include_correction": "true"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == ROW_KEYS | {"history", "correction"}
    _assert_correction(body["correction"], A_CORRECTION)
    assert settings.calls == [(), (), (), ()]
    assert measure.calls == [
        {},
        {},
        {"exceptions.forecast_bias_threshold": 0.75},
        {},
    ]

    response = client.get(
        BY_KEY_URL,
        params={"item_code": "H", "site_code": "159", "include_correction": "true"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["correction"] is None
    assert settings.calls == [(), (), (), (), ()]
    assert measure.calls == [
        {},
        {},
        {"exceptions.forecast_bias_threshold": 0.75},
        {},
        {},
    ]

    before = len(settings.calls)
    response = client.get(
        BY_KEY_URL,
        params={"item_code": "Z", "site_code": "159", "include_correction": "true"},
    )
    assert response.status_code == 404, response.text
    assert len(settings.calls) == before
    assert len(measure.calls) == before

    # ------------------------------------------------------------------
    # Failed reads: a settings read that raises, and again a measure
    # read that raises, never fail the answer — every row's
    # correction is null, every other key reads as without the
    # parameter, and the list answers 200.
    # ------------------------------------------------------------------
    settings.raises = RuntimeError("the settings read failed")
    response = client.get(
        LIST_URL, params={"limit": 1000, "include_correction": "true"}
    )
    assert response.status_code == 200, response.text
    failed = response.json()
    assert len(failed) == len(rows)
    for failed_row, plain_row in zip(failed, rows):
        assert failed_row["correction"] is None
        assert {
            key: value for key, value in failed_row.items() if key != "correction"
        } == plain_row
    assert len(settings.calls) == 6
    assert len(measure.calls) == 5
    settings.raises = None

    measure.raises = RuntimeError("the measure read failed")
    response = client.get(
        LIST_URL, params={"limit": 1000, "include_correction": "true"}
    )
    assert response.status_code == 200, response.text
    failed = response.json()
    for failed_row, plain_row in zip(failed, rows):
        assert failed_row["correction"] is None
        assert {
            key: value for key, value in failed_row.items() if key != "correction"
        } == plain_row
    assert len(settings.calls) == 7
    assert len(measure.calls) == 6

    response = client.get(
        BY_KEY_URL,
        params={"item_code": "A", "site_code": "159", "include_correction": "true"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["correction"] is None
    assert len(settings.calls) == 8
    assert len(measure.calls) == 7
    measure.raises = None

    # ------------------------------------------------------------------
    # A bad value: include_correction=xyz answers 422 on both routes,
    # before any read runs.
    # ------------------------------------------------------------------
    before_settings = len(settings.calls)
    before_measure = len(measure.calls)
    assert client.get(LIST_URL, params={"include_correction": "xyz"}).status_code == 422
    assert (
        client.get(
            BY_KEY_URL,
            params={"item_code": "A", "site_code": "159", "include_correction": "xyz"},
        ).status_code
        == 422
    )
    assert len(settings.calls) == before_settings
    assert len(measure.calls) == before_measure

    # ------------------------------------------------------------------
    # The schema: include_correction among both routes' query
    # parameters (boolean, default false), both descriptions mention
    # it and correction; ForecastRow's fourteen properties plus
    # correction (ForecastCorrection or null), ForecastRowDetail's
    # plus history; the two new schemas' exact keys; the parameter in
    # neither the model's fields nor its schema.
    # ------------------------------------------------------------------
    spec = app.openapi()
    for route in (LIST_URL, BY_KEY_URL):
        parameters = {
            parameter["name"]: parameter
            for parameter in spec["paths"][route]["get"]["parameters"]
        }
        parameter = parameters["include_correction"]
        assert parameter["in"] == "query"
        assert parameter["schema"]["type"] == "boolean"
        assert parameter["schema"]["default"] is False
        description = spec["paths"][route]["get"]["description"]
        assert "include_correction" in description
        assert "correction" in description
    row_schema = spec["components"]["schemas"]["ForecastRow"]
    assert set(row_schema["properties"]) == ROW_KEYS | {"correction"}
    assert row_schema["properties"]["correction"] == {
        "anyOf": [
            {"$ref": "#/components/schemas/ForecastCorrection"},
            {"type": "null"},
        ]
    }
    detail_schema = spec["components"]["schemas"]["ForecastRowDetail"]
    assert set(detail_schema["properties"]) == ROW_KEYS | {"history", "correction"}
    assert set(spec["components"]["schemas"]["ForecastCorrection"]["properties"]) == {
        "measured_on",
        "compared_buckets",
        "effective_total",
        "actual_total",
        "effective_bias",
        "factor",
        "periods",
        "reason",
    }
    assert set(
        spec["components"]["schemas"]["ForecastCorrectionPeriod"]["properties"]
    ) == {"period_start", "period_end", "quantity", "proposed_quantity"}
    assert "include_correction" not in forecast_api.ForecastRow.model_fields
    assert "include_correction" not in row_schema["properties"]

    # ------------------------------------------------------------------
    # Identity: the proof ran as a NON-ADMIN (role "user", asserted
    # above via GET /api/auth/me).  A fresh client booted the
    # production way for the viewer — the token the page would take
    # from the URL hash as #access_token=..., /api/auth/config
    # answering publicly and /api/auth/me resolving the viewer — reads
    # the whole list with corrections, the same answer the admin's:
    # the route has no admin-only control, the page still renders.
    # A request without a token still gets 401 on both routes.
    # ------------------------------------------------------------------
    assert TestClient(app).get("/api/auth/config").status_code == 200
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    response = viewer.get(
        LIST_URL, params={"limit": 1000, "include_correction": "true"}
    )
    assert response.status_code == 200, response.text
    admin_response = admin.get(
        LIST_URL, params={"limit": 1000, "include_correction": "true"}
    )
    assert admin_response.status_code == 200, admin_response.text
    assert response.json() == admin_response.json()
    assert anon.get(LIST_URL, params={"include_correction": "true"}).status_code == 401
    assert (
        anon.get(
            BY_KEY_URL,
            params={"item_code": "A", "site_code": "159", "include_correction": "true"},
        ).status_code
        == 401
    )
