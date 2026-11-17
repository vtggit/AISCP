"""Issue #1357 proof: the latest periods of the forecast accuracy.

AC-1 — ``realised_accuracy`` takes ``latest_periods``: while it is
``None`` the function reads, computes and answers exactly as before
(the same statement, the same parameters, the same answer), and with
a whole number of at least 1 the rows are read exactly as without
it — the same statement and parameters, with the ``as_of``,
``origin`` and ``bucket_index`` filters — and then, for each
item-site, only the rows of its kept origins are compared: the
item-site's distinct snapshot ``as_of`` dates walked back in date
order from the latest, the latest kept and then each origin whose
date is at least ``BUCKET_DAYS`` (30) days before the last kept one
kept and the nearer ones skipped, until ``latest_periods`` origins
are kept or none is left — an item-site with fewer such origins
keeps all it has, and every row of a kept origin counts.  The
stand-ins, so that no check reads the wall clock or a stored
snapshot: ``get_cursor`` in ``app.forecast.accuracy`` is replaced by
a stand-in whose cursor records each ``execute(sql, params)`` call,
its ``fetchall()`` answering fresh copies of the stand-in rows,
keeping only the rows whose ``bucket_index`` equals the statement's
last parameter when the statement contains ``s.bucket_index = %s``;
``default_as_of`` in ``app.forecast.accuracy`` answers 2026-11-16.
Each stand-in row carries ``as_of`` (its origin), ``item_code``,
``site_code``, ``bucket_index``, ``method`` "mean",
``forecast_quantity`` and ``effective_quantity`` (both the effective
figure named), ``signal_quantity`` 0, ``override_days`` 0 and
``actual_quantity`` (the actual named): A at bucket index 1 from
nine origins, B at bucket index 1 from two origins, and C from
three origins with both horizons of each.

AC-2 — the registry holds ``exceptions.forecast_bias_periods`` with
scope "system", type "number", default 6, min 1, max 36 and its
description, and the two existing forecast-bias settings keep their
defaults and ranges.

AC-3 — the new module function ``_forecast_bias_accuracy`` takes the
stored system settings the caller already read and answers
``realised_accuracy(bucket_index=1, latest_periods=periods)`` with
``periods`` the whole part of
``exceptions.forecast_bias_periods`` read through ``setting_value``
(a missing, out-of-range or non-numeric stored value reads the
registry default, 6), through the module's own
``realised_accuracy`` name — a recorder replacing it sees the call
and its exact keyword arguments, and the helper answers what the
read answers.  The detail keeps its words, "forecast {over or under}
actual by {percent}% across {count} periods", {count} the
item-site's ``compared_buckets``, now the number of distinct periods
measured, at most the setting's value.  Through the app's test
client, with ``_stored_system_settings`` in ``app.api.exceptions``
answering ``{"exceptions.forecast_bias_periods": 3}`` and
``realised_accuracy`` there wrapped by a recorder that calls the
real one, ``GET /api/exceptions`` answers 200: its forecast_bias
rows are exactly A's, "forecast" is not in ``sources_failed``, the
stored settings were read once and the recorder saw exactly one
call — the other sources answer what they answer on the test
database, and the proof reads only the forecast_bias rows and
``sources_failed``.

AC-5 route stays — the app's OpenAPI document lists no
``latest_periods`` among the parameters of ``GET
/api/forecast/accuracy``.

Identity — a fresh test client signed in as a NON-ADMIN (role
"user", checked through ``GET /api/auth/me``) boots the way the
page would: the development token the page would hold from its
``#access_token=...`` fragment sent as its Bearer token, ``GET
/api/auth/config`` (public), then ``GET /api/auth/me`` — and gets
the same forecast_bias rows, while a request without a token
answers 401.
"""

from datetime import date

from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
import app.forecast.accuracy as accuracy
from app.main import app
from app.settings.registry import get_setting

AS_OF = date(2026, 11, 16)
SITE = "S1357"
PERIODS_DESCRIPTION = (
    "How many of the latest forecast periods, each forecast one "
    "period ahead, the forecast bias is measured over."
)


def _row(as_of, item_code, bucket_index, effective, actual):
    """One stand-in row: both quantity figures the effective figure
    named, no signal, no override."""
    return {
        "as_of": as_of,
        "item_code": item_code,
        "site_code": SITE,
        "bucket_index": bucket_index,
        "method": "mean",
        "forecast_quantity": effective,
        "effective_quantity": effective,
        "signal_quantity": 0,
        "override_days": 0,
        "actual_quantity": actual,
    }


# A at bucket index 1, one row per origin.
_ROWS_A = (
    _row(date(2026, 6, 30), "A", 1, 100, 50),
    _row(date(2026, 6, 20), "A", 1, 1000, 1000),
    _row(date(2026, 5, 31), "A", 1, 100, 50),
    _row(date(2026, 5, 2), "A", 1, 1000, 1000),
    _row(date(2026, 5, 1), "A", 1, 100, 50),
    _row(date(2026, 4, 1), "A", 1, 100, 50),
    _row(date(2026, 3, 2), "A", 1, 100, 50),
    _row(date(2026, 1, 31), "A", 1, 100, 50),
    _row(date(2026, 1, 1), "A", 1, 1000, 1000),
)
# B at bucket index 1.
_ROWS_B = (
    _row(date(2026, 6, 30), "B", 1, 40, 100),
    _row(date(2026, 5, 31), "B", 1, 40, 100),
)
# C from three origins, with both horizons of each.
_ROWS_C = (
    _row(date(2026, 6, 30), "C", 1, 10, 10),
    _row(date(2026, 6, 30), "C", 2, 20, 10),
    _row(date(2026, 6, 15), "C", 1, 500, 0),
    _row(date(2026, 5, 31), "C", 1, 10, 10),
    _row(date(2026, 5, 31), "C", 2, 20, 10),
)
ROWS = _ROWS_A + _ROWS_B + _ROWS_C


class _RecordingCursor:
    """The stand-in cursor: it records each ``execute(sql, params)``
    call, and its ``fetchall()`` answers fresh copies of the
    stand-in rows — the rows whose ``bucket_index`` equals the
    statement's last parameter when the statement contains
    ``s.bucket_index = %s``."""

    def __init__(self):
        self.executed = []

    def execute(self, sql, params):
        self.executed.append((sql, tuple(params)))

    def fetchall(self):
        rows = list(ROWS)
        if self.executed:
            sql, params = self.executed[-1]
            if "s.bucket_index = %s" in sql:
                rows = [row for row in rows if row["bucket_index"] == params[-1]]
        return [dict(row) for row in rows]

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _site(result, item_code):
    for entry in result["item_sites"]:
        if entry["item_code"] == item_code:
            return entry
    raise AssertionError(f"no item_sites entry for {item_code!r}: {result!r}")


def test_issue1357_freeform(monkeypatch, client, admin_token, user_token):
    # The stand-ins: the recorded read and the pinned default as-of.
    # The client fixture's teardown truncates the tables after the
    # test, so the first-seen capture of the list checks leaves no
    # row for the later tests.
    cursor = _RecordingCursor()
    monkeypatch.setattr(accuracy, "get_cursor", lambda: cursor)
    monkeypatch.setattr(accuracy, "default_as_of", lambda cur: AS_OF)

    # --- AC-1: None changes nothing -------------------------------
    base = accuracy.realised_accuracy(as_of=AS_OF)
    none_call = accuracy.realised_accuracy(as_of=AS_OF, latest_periods=None)
    assert none_call == base
    assert (
        cursor.executed[-2] == cursor.executed[-1]
    ), "both calls execute the same statement with the same parameters"
    assert base["compared_buckets"] == 16, "every row counted"
    assert _site(base, "A")["compared_buckets"] == 9
    assert _site(base, "A")["effective_total"] == 3600
    assert _site(base, "A")["actual_total"] == 3300
    assert _site(base, "B")["compared_buckets"] == 2
    assert _site(base, "C")["compared_buckets"] == 5

    # --- AC-1: the same read --------------------------------------
    # latest_periods changes no read: the statement and the
    # parameters of the same call without it, with and without
    # bucket_index=1 (and with the origin filter).
    accuracy.realised_accuracy(as_of=AS_OF)
    read_plain = cursor.executed[-1]
    accuracy.realised_accuracy(as_of=AS_OF, latest_periods=6)
    assert cursor.executed[-1] == read_plain
    accuracy.realised_accuracy(as_of=AS_OF, bucket_index=1)
    read_bucket = cursor.executed[-1]
    accuracy.realised_accuracy(as_of=AS_OF, bucket_index=1, latest_periods=6)
    assert cursor.executed[-1] == read_bucket
    accuracy.realised_accuracy(as_of=AS_OF, origin="live")
    read_origin = cursor.executed[-1]
    accuracy.realised_accuracy(as_of=AS_OF, origin="live", latest_periods=6)
    assert cursor.executed[-1] == read_origin

    # --- AC-1: the origins kept, with latest_periods=6 ------------
    kept = accuracy.realised_accuracy(as_of=AS_OF, latest_periods=6)
    a, b, c = _site(kept, "A"), _site(kept, "B"), _site(kept, "C")
    # A keeps 2026-06-30, 2026-05-31, 2026-05-01, 2026-04-01,
    # 2026-03-02 and 2026-01-31: 2026-06-20 is skipped (10 days
    # before 2026-06-30), 2026-05-31 is kept at exactly 30 days,
    # 2026-05-02 is skipped (29 days before 2026-05-31) and
    # 2026-01-01 is cut off as the seventh.
    assert a["compared_buckets"] == 6
    assert a["effective_total"] == 600
    assert a["actual_total"] == 300
    assert a["effective_bias"] == 1.0
    # B keeps both of its origins, fewer than 6.
    assert b["compared_buckets"] == 2
    assert b["effective_total"] == 80
    assert b["actual_total"] == 200
    assert b["effective_bias"] == -0.6
    # C keeps 2026-06-30 and 2026-05-31 with both horizons of each,
    # and skips 2026-06-15 (15 days before 2026-06-30).
    assert c["compared_buckets"] == 4
    assert c["effective_total"] == 60
    assert c["actual_total"] == 40
    assert c["effective_bias"] == 0.5
    assert kept["compared_buckets"] == 12
    assert kept["overall"]["effective_total"] == 740
    assert kept["overall"]["actual_total"] == 540
    assert [(h["bucket_index"], h["compared_buckets"]) for h in kept["by_horizon"]] == [
        (1, 10),
        (2, 2),
    ]

    # --- AC-1: the cut-off ----------------------------------------
    three = accuracy.realised_accuracy(as_of=AS_OF, latest_periods=3)
    assert _site(three, "A")["compared_buckets"] == 3
    assert _site(three, "A")["effective_total"] == 300
    assert _site(three, "A")["actual_total"] == 150
    seven = accuracy.realised_accuracy(as_of=AS_OF, latest_periods=7)
    # latest_periods=7 adds 2026-01-01, the seventh kept origin.
    assert _site(seven, "A")["compared_buckets"] == 7
    assert _site(seven, "A")["effective_total"] == 1600
    assert _site(seven, "A")["actual_total"] == 1300
    one = accuracy.realised_accuracy(as_of=AS_OF, latest_periods=1)
    # latest_periods=1 keeps 2026-06-30 alone.
    assert _site(one, "A")["compared_buckets"] == 1
    assert _site(one, "A")["effective_total"] == 100
    assert _site(one, "A")["actual_total"] == 50

    # --- AC-1: one period ahead -----------------------------------
    ahead = accuracy.realised_accuracy(as_of=AS_OF, bucket_index=1, latest_periods=6)
    assert _site(ahead, "C")["compared_buckets"] == 2
    assert _site(ahead, "C")["effective_total"] == 20
    assert _site(ahead, "C")["actual_total"] == 20
    assert _site(ahead, "C")["effective_bias"] == 0.0
    # A and B read as above.
    assert _site(ahead, "A")["compared_buckets"] == 6
    assert _site(ahead, "A")["effective_total"] == 600
    assert _site(ahead, "A")["actual_total"] == 300
    assert _site(ahead, "B")["compared_buckets"] == 2
    assert _site(ahead, "B")["effective_total"] == 80
    assert _site(ahead, "B")["actual_total"] == 200

    # --- AC-3: the helper's call ----------------------------------
    canned = {"as_of": AS_OF, "compared_buckets": 0, "item_sites": []}
    helper_calls = []

    def _recording_read(*args, **kwargs):
        helper_calls.append((args, kwargs))
        return canned

    monkeypatch.setattr(exceptions, "realised_accuracy", _recording_read)
    assert exceptions._forecast_bias_accuracy({}) == canned
    assert helper_calls == [((), {"bucket_index": 1, "latest_periods": 6})]
    for stored, periods in (
        ({}, 6),
        ({"exceptions.forecast_bias_periods": 4}, 4),
        ({"exceptions.forecast_bias_periods": 4.5}, 4),
        ({"exceptions.forecast_bias_periods": 0}, 6),
        ({"exceptions.forecast_bias_periods": 37}, 6),
        ({"exceptions.forecast_bias_periods": "six"}, 6),
    ):
        helper_calls.clear()
        assert exceptions._forecast_bias_accuracy(stored) == canned
        assert helper_calls == [((), {"bucket_index": 1, "latest_periods": periods})]

    # --- AC-3: the wording, with the real read --------------------
    monkeypatch.setattr(exceptions, "realised_accuracy", accuracy.realised_accuracy)
    payload = exceptions._forecast_bias_accuracy({})
    rows = exceptions._forecast_rows(payload, 0.5, 3)
    assert len(rows) == 1
    assert rows[0].kind == "forecast_bias"
    assert rows[0].item_code == "A"
    assert rows[0].site_code == SITE
    assert rows[0].detail == "forecast over actual by 100% across 6 periods"
    assert rows[0].detected_on == AS_OF
    # With a minimum of 2 periods B is a row too.
    assert [row.detail for row in exceptions._forecast_rows(payload, 0.5, 2)] == [
        "forecast over actual by 100% across 6 periods",
        "forecast under actual by 60% across 2 periods",
    ]
    # With the stored setting at 3, A's detail counts 3 periods.
    payload_three = exceptions._forecast_bias_accuracy(
        {"exceptions.forecast_bias_periods": 3}
    )
    rows_three = exceptions._forecast_rows(payload_three, 0.5, 3)
    assert [row.item_code for row in rows_three] == ["A"]
    assert rows_three[0].detail == "forecast over actual by 100% across 3 periods"

    # --- AC-3: the list, through the app ---------------------------
    settings_reads = {"count": 0}

    def _counting_stored_settings():
        settings_reads["count"] += 1
        return {"exceptions.forecast_bias_periods": 3}

    monkeypatch.setattr(
        exceptions, "_stored_system_settings", _counting_stored_settings
    )
    real_read = accuracy.realised_accuracy
    list_read_calls = []

    def _recording_real_read(*args, **kwargs):
        list_read_calls.append((args, kwargs))
        return real_read(*args, **kwargs)

    monkeypatch.setattr(exceptions, "realised_accuracy", _recording_real_read)

    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    response = admin.get("/api/exceptions")
    assert response.status_code == 200, response.text
    body = response.json()
    bias_rows = [row for row in body["rows"] if row["kind"] == "forecast_bias"]
    assert len(bias_rows) == 1
    assert bias_rows[0]["item_code"] == "A"
    assert bias_rows[0]["site_code"] == SITE
    assert bias_rows[0]["detail"] == "forecast over actual by 100% across 3 periods"
    assert "forecast" not in body["sources_failed"]
    # The stored settings were read once for the call, and the read
    # went out with the setting's value.
    assert settings_reads["count"] == 1
    assert list_read_calls == [((), {"bucket_index": 1, "latest_periods": 3})]

    # --- AC-2: the setting -----------------------------------------
    periods_setting = get_setting("exceptions.forecast_bias_periods")
    assert periods_setting is not None
    assert periods_setting.scope == "system"
    assert periods_setting.type == "number"
    assert periods_setting.default == 6
    assert periods_setting.min == 1
    assert periods_setting.max == 36
    assert periods_setting.description == PERIODS_DESCRIPTION
    threshold = get_setting("exceptions.forecast_bias_threshold")
    assert (threshold.default, threshold.min, threshold.max) == (0.5, 0.1, 10)
    min_periods = get_setting("exceptions.forecast_bias_min_periods")
    assert (min_periods.default, min_periods.min, min_periods.max) == (3, 1, 120)

    # --- AC-5: the route stays -------------------------------------
    schema = app.openapi()
    parameters = schema["paths"]["/api/forecast/accuracy"]["get"]["parameters"]
    assert all(parameter["name"] != "latest_periods" for parameter in parameters)

    # --- Identity: the non-admin pass ------------------------------
    # A fresh client (full reload) carries the development token the
    # page would hold from its #access_token=... fragment: a
    # non-admin, role "user".
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    config = viewer.get("/api/auth/config")
    assert config.status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]
    viewer_response = viewer.get("/api/exceptions")
    assert viewer_response.status_code == 200, viewer_response.text
    viewer_bias = [
        row for row in viewer_response.json()["rows"] if row["kind"] == "forecast_bias"
    ]
    assert viewer_bias == bias_rows
    # No token: the gate answers 401.
    anonymous = TestClient(app)
    assert anonymous.get("/api/exceptions").status_code == 401
