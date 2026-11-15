"""Issue #822 proof: the forecast-bias rule's two system settings.

AC-1 — the two settings are registered the way the ``ddmrp.*``
number settings are (scope "system", type "number":
``exceptions.forecast_bias_threshold`` default 0.5, min 0.1, max 10;
``exceptions.forecast_bias_min_periods`` default 3, min 1, max 120),
and the forecast source of ``GET /api/exceptions`` reads the stored
system settings once per call, inside the same try that guards the
forecast source: an item-site is a row when its ``compared_buckets``
is at least the min-periods setting and its absolute
``effective_bias`` is at least the threshold setting, a missing or
out-of-range stored value falls back to the registry default, and
with the defaults the rows are exactly the pre-change ones.  The six
sources of the triage list are replaced in the route module with
fakes the way the issue 818 proof does: the DDMRP source (the shared
plan code), ``stockout_ranked``, ``expedite_list``, the two failure
repositories (``plan_releases``, ``parameter_writebacks``) and
``realised_accuracy``.  The forecast fake answers item-sites with
(a) an effective bias of 0.84 over 120 compared periods, (b) 0.3
over 50, and (c) 0.9 over 2.

AC-2 — proved in one test:

  * the defaults — only (a) is a row;
  * a lower threshold — after an admin PUT of
    ``exceptions.forecast_bias_threshold`` to 0.25, both (a) and
    (b) are rows;
  * fewer periods — after the same admin sets
    ``exceptions.forecast_bias_min_periods`` to 2, (c) is a row
    too;
  * out of range — a stored value outside the bounds, written
    directly to the settings table, falls back to the default for
    each setting;
  * non-finite — an ``effective_bias`` of nan, inf or -inf (each
    over 120 compared periods) is never a row and never fails the
    whole forecast source: the other sources' rows still answer
    and "forecast" is not in ``sources_failed``;
  * the one read — the stored settings are read exactly once per
    GET, and a settings read failure lists "forecast" in
    ``sources_failed`` like any other forecast failure;
  * the listing — ``GET /api/settings`` lists both keys with their
    defaults, min, max and scope "system";
  * admin only — a non-admin PUT answers 403.

Identity — the two settings are system settings that only an admin
edits, so the pass also runs as a NON-ADMIN (role ``user``): a
second pass in the same test after a full reload (a fresh
TestClient) boots the page the production way — the development
token the page would hold from its ``#access_token=...`` fragment
sent as its Bearer token, ``GET /api/auth/config`` (public), then
``GET /api/auth/me`` — and asserts the admin-only control is
absent: both settings list ``editable`` false and a PUT of either
answers 403 with the unchanged detail — while the page still
renders, ``GET /api/settings`` answering 200 with every registered
setting before and after the refusals.
"""

from datetime import UTC, date, datetime, timedelta

import psycopg2.extras
from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
import app.feed.acumatica_client as acumatica_client_module
import app.feed.odoo_client as odoo_client_module
from app.db.connection import get_connection
from app.main import app

URL = "/api/exceptions"
SETTINGS_URL = "/api/settings"

THRESHOLD_KEY = "exceptions.forecast_bias_threshold"
MIN_PERIODS_KEY = "exceptions.forecast_bias_min_periods"

ADMIN_ONLY_DETAIL = "Role 'admin' is required to perform this action."

# The pinned current UTC time (the route's failure-window anchor).
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)

# The as-of dates the successful sources report: the expedite's is
# the newest, so it is the response as_of (the forecast bias never
# reports one).
DDMRP_AS_OF = date(2026, 10, 9)
STOCKOUT_AS_OF = date(2026, 10, 9)
EXPEDITE_AS_OF = "2026-10-10"
FORECAST_AS_OF = date(2026, 10, 11)


class FakeDdmrpSource:
    """The fake for the route module's DDMRP source: answers the
    canned plan rows (the rows the DDMRP plan route builds) as of
    the canned as-of date."""

    calls = 0

    def __call__(self):
        FakeDdmrpSource.calls += 1
        return (
            DDMRP_AS_OF,
            [
                {
                    "item_code": "ITEM-822-A",
                    "site_code": "SITE-822-A",
                    "on_hand": 0.0,
                    "on_hand_pct_of_red": 0.0,
                    "on_hand_status": "stocked_out",
                },
                {
                    "item_code": "ITEM-822-B",
                    "site_code": "SITE-822-B",
                    "on_hand": 5.0,
                    "on_hand_pct_of_red": 25.0,
                    "on_hand_status": "critical",
                },
            ],
        )


class FakeStockoutSource:
    """The fake for the route module's ``stockout_ranked``."""

    calls = 0

    def __call__(self, as_of=None):
        FakeStockoutSource.calls += 1
        return [
            {
                "category": "likely",
                "item_code": "ITEM-822-A",
                "site_code": "SITE-822-A",
                "p_stockout": 0.83,
                "projected_stockout_on": date(2026, 10, 12),
                "as_of": STOCKOUT_AS_OF,
            },
            {
                "category": "at_risk",
                "item_code": "ITEM-822-E",
                "site_code": "SITE-822-E",
                "p_stockout": 0.45,
                "projected_stockout_on": date(2026, 10, 20),
                "as_of": STOCKOUT_AS_OF,
            },
        ]


class FakeExpediteSource:
    """The fake for the route module's ``expedite_list``."""

    calls = 0

    def __call__(self, as_of=None):
        FakeExpediteSource.calls += 1
        return {
            "as_of": EXPEDITE_AS_OF,
            "stale_excluded": 0,
            "lines": [
                {
                    "order_nbr": "PO-1",
                    "line_nbr": 10,
                    "item_code": "ITEM-822-F",
                    "site_code": "SITE-822-F",
                    "open_units": 120.0,
                    "days_late": 3,
                    "reasons": ["late", "after_stockout"],
                },
                {
                    "order_nbr": "PO-2",
                    "line_nbr": 20,
                    "item_code": "ITEM-822-G",
                    "site_code": "SITE-822-G",
                    "open_units": 75.0,
                    "days_late": 2,
                    "reasons": ["late"],
                },
            ],
        }


class FakePlanReleasesRepo:
    """The fake for the route module's plan_releases repository."""

    calls = 0

    def list_newest(self):
        FakePlanReleasesRepo.calls += 1
        return [
            {
                "id": 1,
                "outcome": "error",
                "vendor_code": "V-100",
                "supplier_code": "S-100",
                "updated_at": NOW - timedelta(days=1),
                "error": "Acumatica batch failed: HTTP 500 (request-id=822)",
            },
        ]


class FakeParameterWritebacksRepo:
    """The fake for the route module's parameter_writebacks
    repository."""

    calls = 0

    def list(self, limit=100, offset=0, item_code=None, site_code=None):
        FakeParameterWritebacksRepo.calls += 1
        return [
            {
                "id": "pw-1",
                "item_code": "ITEM-822-A",
                "site_code": "SITE-822-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=2),
                "error": "boom: the Acumatica write failed (api-key=SECRET-822)",
            },
        ]


class FakeRealisedAccuracy:
    """The fake for the route module's ``realised_accuracy``: the
    three canned item-sites, as of the canned as-of date — (a) a
    bias of 0.84 over 120 periods, (b) 0.3 over 50, (c) 0.9 over 2."""

    calls = 0

    def __call__(self, **kwargs):
        FakeRealisedAccuracy.calls += 1
        return {
            "as_of": FORECAST_AS_OF,
            "compared_buckets": 172,
            "overall": {},
            "by_horizon": [],
            "item_sites": [
                {
                    "item_code": "ITEM-822-A",
                    "site_code": "SITE-822-A",
                    "method": "moving_average",
                    "compared_buckets": 120,
                    "effective_bias": 0.84,
                },
                {
                    "item_code": "ITEM-822-B",
                    "site_code": "SITE-822-B",
                    "method": "moving_average",
                    "compared_buckets": 50,
                    "effective_bias": 0.3,
                },
                {
                    "item_code": "ITEM-822-C",
                    "site_code": "SITE-822-C",
                    "method": "moving_average",
                    "compared_buckets": 2,
                    "effective_bias": 0.9,
                },
            ],
        }


def _forecast_row(letter: str, pct: int, buckets: int) -> dict:
    """The exception row of one forecast-bias item-site."""
    item_code = f"ITEM-822-{letter}"
    site_code = f"SITE-822-{letter}"
    return {
        "kind": "forecast_bias",
        "severity": "medium",
        "key": f"forecast_bias:{item_code}/{site_code}",
        "item_code": item_code,
        "site_code": site_code,
        "title": "Forecast bias",
        "detail": f"forecast over actual by {pct}% across {buckets} periods",
        "view": "#/demand",
        "detected_on": FORECAST_AS_OF.isoformat(),
    }


# The three canned item-sites as exception rows.
ROW_A = _forecast_row("A", 84, 120)
ROW_B = _forecast_row("B", 30, 50)
ROW_C = _forecast_row("C", 90, 2)

# The eight rows the other five sources always answer, in the
# response's order (high before medium, then kind order).
BASE_ROWS = [
    {
        "kind": "buffer_stocked_out",
        "severity": "high",
        "key": "buffer_stocked_out:ITEM-822-A/SITE-822-A",
        "item_code": "ITEM-822-A",
        "site_code": "SITE-822-A",
        "title": "Buffer stocked out",
        "detail": "on hand 0 (0% of top of red)",
        "view": "#/buffers",
        "detected_on": DDMRP_AS_OF.isoformat(),
    },
    {
        "kind": "stockout_likely",
        "severity": "high",
        "key": "stockout_likely:ITEM-822-A/SITE-822-A",
        "item_code": "ITEM-822-A",
        "site_code": "SITE-822-A",
        "title": "Stockout likely",
        "detail": "projected stockout 2026-10-12, 83% chance",
        "view": "#/queue?kind=stockout",
        "detected_on": STOCKOUT_AS_OF.isoformat(),
    },
    {
        "kind": "expedite_after_stockout",
        "severity": "high",
        "key": "expedite_after_stockout:PO-1/10",
        "item_code": "ITEM-822-F",
        "site_code": "SITE-822-F",
        "title": "Expedite after stockout",
        "detail": "120 units, 3 days late",
        "view": "#/expedite",
        "detected_on": EXPEDITE_AS_OF,
    },
    {
        "kind": "release_failed",
        "severity": "high",
        "key": "release_failed:1",
        "item_code": None,
        "site_code": None,
        "title": "Release failed",
        "detail": "release to V-100 failed",
        "view": "#/releases",
        "detected_on": (NOW - timedelta(days=1)).date().isoformat(),
    },
    {
        "kind": "erp_push_failed",
        "severity": "high",
        "key": "erp_push_failed:pw-1",
        "item_code": None,
        "site_code": None,
        "title": "ERP push failed",
        "detail": "push to acumatica failed",
        "view": "#/writeback",
        "detected_on": (NOW - timedelta(days=2)).date().isoformat(),
    },
    {
        "kind": "buffer_critical",
        "severity": "medium",
        "key": "buffer_critical:ITEM-822-B/SITE-822-B",
        "item_code": "ITEM-822-B",
        "site_code": "SITE-822-B",
        "title": "Buffer critical",
        "detail": "on hand 5 (25% of top of red)",
        "view": "#/buffers",
        "detected_on": DDMRP_AS_OF.isoformat(),
    },
    {
        "kind": "stockout_at_risk",
        "severity": "medium",
        "key": "stockout_at_risk:ITEM-822-E/SITE-822-E",
        "item_code": "ITEM-822-E",
        "site_code": "SITE-822-E",
        "title": "Stockout at risk",
        "detail": "projected stockout 2026-10-20, 45% chance",
        "view": "#/queue?kind=stockout",
        "detected_on": STOCKOUT_AS_OF.isoformat(),
    },
    {
        "kind": "expedite_late",
        "severity": "medium",
        "key": "expedite_late:PO-2/20",
        "item_code": "ITEM-822-G",
        "site_code": "SITE-822-G",
        "title": "Expedite late",
        "detail": "75 units, 2 days late",
        "view": "#/expedite",
        "detected_on": EXPEDITE_AS_OF,
    },
]


def _assert_response(body, forecast_rows, sources_failed):
    """The full expected triage list: the eight canned rows plus the
    forecast-bias rows the settings let through, in order."""
    expected = BASE_ROWS + forecast_rows
    assert set(body) == {"as_of", "counts", "rows", "sources_failed"}
    assert body["as_of"] == EXPEDITE_AS_OF
    assert body["sources_failed"] == sources_failed
    assert [
        {
            "kind": row["kind"],
            "severity": row["severity"],
            "key": row["key"],
            "item_code": row["item_code"],
            "site_code": row["site_code"],
            "title": row["title"],
            "detail": row["detail"],
            "view": row["view"],
            "detected_on": row["detected_on"],
            "acknowledged": row["acknowledged"],
            "acknowledged_by_name": row["acknowledged_by_name"],
            "acknowledged_at": row["acknowledged_at"],
            "snooze_until": row["snooze_until"],
            "first_seen_on": row["first_seen_on"],
            "new": row["new"],
        }
        for row in body["rows"]
    ] == [
        dict(
            row,
            acknowledged=False,
            acknowledged_by_name=None,
            acknowledged_at=None,
            snooze_until=None,
            first_seen_on=None,
            new=False,
        )
        for row in expected
    ]
    assert body["counts"] == {
        "high": sum(1 for row in expected if row["severity"] == "high"),
        "medium": sum(1 for row in expected if row["severity"] == "medium"),
        "acknowledged": 0,
        "new": 0,
    }


def _store_out_of_range(key: str, value: float) -> None:
    """A stored value written directly to the settings table, bypassing
    the API's bounds check."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE settings SET value = %s "
                "WHERE scope = 'system' AND owner = '' AND key = %s",
                (psycopg2.extras.Json(value), key),
            )
            assert cur.rowcount == 1, key
        conn.commit()
    finally:
        conn.close()


def _setting_entry(body: list[dict], key: str) -> dict:
    entry = next((e for e in body if e["key"] == key), None)
    assert entry is not None, (key, body)
    return entry


def test_issue822_freeform(monkeypatch, client, admin_token, user_token):
    # The six sources in the route module are replaced with fakes
    # that answer canned rows, the way the issue 818 proof does.
    # Every fake records its calls.
    FakeDdmrpSource.calls = 0
    FakeStockoutSource.calls = 0
    FakeExpediteSource.calls = 0
    FakePlanReleasesRepo.calls = 0
    FakeParameterWritebacksRepo.calls = 0
    FakeRealisedAccuracy.calls = 0
    monkeypatch.setattr(exceptions, "_ddmrp_source", FakeDdmrpSource())
    monkeypatch.setattr(exceptions, "stockout_ranked", FakeStockoutSource())
    monkeypatch.setattr(exceptions, "expedite_list", FakeExpediteSource())
    monkeypatch.setattr(exceptions, "_plan_releases_repo", FakePlanReleasesRepo())
    monkeypatch.setattr(
        exceptions, "_parameter_writebacks_repo", FakeParameterWritebacksRepo()
    )
    monkeypatch.setattr(exceptions, "realised_accuracy", FakeRealisedAccuracy())
    # The failure-window anchor: the route's "current UTC time".
    monkeypatch.setattr(exceptions, "_utcnow", lambda: NOW)

    # No ERP call: the client classes fail if constructed.
    def _no_client(self, *args, **kwargs):
        raise AssertionError("the exceptions route must not call an ERP")

    monkeypatch.setattr(acumatica_client_module.AcumaticaClient, "__init__", _no_client)
    monkeypatch.setattr(odoo_client_module.OdooClient, "__init__", _no_client)

    # The stored system settings are read from the real settings
    # table — counted, to prove the one read per call.
    real_stored = exceptions._stored_system_settings
    settings_reads = {"count": 0}

    def counting_stored_settings():
        settings_reads["count"] += 1
        return real_stored()

    monkeypatch.setattr(exceptions, "_stored_system_settings", counting_stored_settings)

    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})

    # --- The listing: both keys with their defaults -------------------
    listing = admin.get(SETTINGS_URL)
    assert listing.status_code == 200, listing.text
    body = listing.json()
    for entry, default, min_v, max_v in (
        (_setting_entry(body, THRESHOLD_KEY), 0.5, 0.1, 10),
        (_setting_entry(body, MIN_PERIODS_KEY), 3, 1, 120),
    ):
        assert entry["scope"] == "system"
        assert entry["type"] == "number"
        assert entry["default"] == default
        assert entry["min"] == min_v
        assert entry["max"] == max_v
        assert entry["description"].strip()
        assert entry["value"] == default, "the default applies while unstored"
        assert entry["source"] == "default"
        assert entry["editable"] is True, "the admin may edit a system setting"

    # --- The defaults: only (a) is a row ------------------------------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    _assert_response(response.json(), [ROW_A], [])
    # The settings were read exactly once for the call.
    assert settings_reads["count"] == 1

    # --- A lower threshold: both (a) and (b) are rows -----------------
    put = admin.put(f"{SETTINGS_URL}/{THRESHOLD_KEY}", json={"value": 0.25})
    assert put.status_code == 200, put.text
    entry = put.json()
    assert entry["value"] == 0.25
    assert entry["source"] == "system"
    assert entry["updated_by"] == "dev-admin-1"
    body = admin.get(URL).json()
    _assert_response(body, [ROW_A, ROW_B], [])
    assert settings_reads["count"] == 2

    # --- Fewer periods: (c) is a row too ------------------------------
    put = admin.put(f"{SETTINGS_URL}/{MIN_PERIODS_KEY}", json={"value": 2})
    assert put.status_code == 200, put.text
    assert put.json()["value"] == 2
    body = admin.get(URL).json()
    _assert_response(body, [ROW_A, ROW_B, ROW_C], [])
    assert settings_reads["count"] == 3

    # --- Out of range: the threshold stored below its min -------------
    # The listing serves the stored value ...
    body = admin.get(SETTINGS_URL).json()
    assert _setting_entry(body, THRESHOLD_KEY)["value"] == 0.25
    _store_out_of_range(THRESHOLD_KEY, 0.05)
    # ... but the rule falls back to the default 0.5: (b) drops out,
    # while (c) — two periods against the still-stored, in-range
    # min-periods 2 — stays a row.
    body = admin.get(URL).json()
    _assert_response(body, [ROW_A, ROW_C], [])
    assert settings_reads["count"] == 4
    body = admin.get(SETTINGS_URL).json()
    stored = _setting_entry(body, THRESHOLD_KEY)
    assert stored["value"] == 0.05
    assert stored["source"] == "system"

    # --- Out of range: the min-periods stored below its min -----------
    _store_out_of_range(MIN_PERIODS_KEY, 0)
    # Both settings back at their defaults: exactly today's rows.
    body = admin.get(URL).json()
    _assert_response(body, [ROW_A], [])
    assert settings_reads["count"] == 5
    body = admin.get(SETTINGS_URL).json()
    stored = _setting_entry(body, MIN_PERIODS_KEY)
    assert stored["value"] == 0
    assert stored["source"] == "system"

    # --- A non-finite effective_bias is never a row, never a failure -
    # nan, inf and -inf all pass float() but have no whole-percent:
    # the filter drops them — the forecast source must not fail.
    assert exceptions._forecast_bias(float("nan")) is None
    assert exceptions._forecast_bias(float("inf")) is None
    assert exceptions._forecast_bias(float("-inf")) is None
    assert exceptions._forecast_bias("nan") is None
    assert exceptions._forecast_bias("inf") is None
    assert exceptions._forecast_bias(0.84) == 0.84

    class NonFiniteAccuracy:
        """The accuracy fake's non-finite answer: three item-sites
        whose effective_bias is nan, inf or -inf, each with 120
        compared periods — plenty against the (default) min-periods
        3, so any of them treated as a row would show."""

        def __call__(self, **kwargs):
            return {
                "as_of": FORECAST_AS_OF,
                "compared_buckets": 172,
                "overall": {},
                "by_horizon": [],
                "item_sites": [
                    {
                        "item_code": "ITEM-822-NAN",
                        "site_code": "SITE-822-NAN",
                        "method": "moving_average",
                        "compared_buckets": 120,
                        "effective_bias": float("nan"),
                    },
                    {
                        "item_code": "ITEM-822-INF",
                        "site_code": "SITE-822-INF",
                        "method": "moving_average",
                        "compared_buckets": 120,
                        "effective_bias": float("inf"),
                    },
                    {
                        "item_code": "ITEM-822-NINF",
                        "site_code": "SITE-822-NINF",
                        "method": "moving_average",
                        "compared_buckets": 120,
                        "effective_bias": float("-inf"),
                    },
                ],
            }

    monkeypatch.setattr(exceptions, "realised_accuracy", NonFiniteAccuracy())
    body = admin.get(URL).json()
    _assert_response(body, [], [])
    assert settings_reads["count"] == 6

    # --- A settings read failure is a forecast failure ----------------
    class RaisingStoredSettings:
        def __call__(self):
            raise RuntimeError("the settings read is down")

    monkeypatch.setattr(exceptions, "_stored_system_settings", RaisingStoredSettings())
    failed = admin.get(URL)
    assert failed.status_code == 200
    _assert_response(failed.json(), [], ["forecast"])

    # --- Identity: the non-admin pass, after a full reload -------------
    # The two settings are system settings that only an admin edits.
    # A fresh client (full reload) carries the development token the
    # page would hold from its #access_token=... fragment: sub
    # dev-user-1, role "user" — a non-admin.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})

    config = viewer.get("/api/auth/config")
    assert config.status_code == 200
    assert config.json()["authEnabled"] is True

    me = viewer.get("/api/auth/me")
    assert me.status_code == 200
    me_body = me.json()
    assert me_body["authenticated"] is True
    assert me_body["user"]["sub"] == "dev-user-1"
    assert me_body["user"]["roles"] == ["user"]
    assert "admin" not in me_body["user"]["roles"]

    # The page still renders for the non-admin: its data call answers
    # with every registered setting, the two new system settings not
    # editable by this caller — the admin-only control is absent.
    page = viewer.get(SETTINGS_URL)
    assert page.status_code == 200
    entries = page.json()
    for key in (THRESHOLD_KEY, MIN_PERIODS_KEY):
        assert _setting_entry(entries, key)["editable"] is False, key

    put = viewer.put(f"{SETTINGS_URL}/{THRESHOLD_KEY}", json={"value": 0.2})
    assert put.status_code == 403
    assert put.json() == {"detail": ADMIN_ONLY_DETAIL}
    put = viewer.put(f"{SETTINGS_URL}/{MIN_PERIODS_KEY}", json={"value": 1})
    assert put.status_code == 403
    assert put.json() == {"detail": ADMIN_ONLY_DETAIL}

    # ...and the page still renders after the refusals, values
    # unchanged.
    again = viewer.get(SETTINGS_URL)
    assert again.status_code == 200
    before = {e["key"]: (e["editable"], e["value"]) for e in entries}
    after = {e["key"]: (e["editable"], e["value"]) for e in again.json()}
    assert before == after
