"""Issue #818 proof: the forecast-bias source of GET /api/exceptions.

AC-1/AC-2 — the six sources of the triage list are replaced in the
route module with fakes the way the issue 795 proof does: the DDMRP
source (the shared plan code), ``stockout_ranked``, ``expedite_list``,
the two failure repositories (``plan_releases``,
``parameter_writebacks``) and ``realised_accuracy``.  The forecast
fake answers item-sites with (a) an effective bias of 0.84 over 120
compared buckets, (b) -0.71 over 72, (c) 0.49 over 50, (d) 0.9 over
2, and (e) no bias at all.  Proved here:

  * selection — only (a) and (b) become rows: the bias must be a
    number of absolute value at least 0.5 (so (c) 0.49 and (e) null
    do not) and the item-site must have compared at least 3 buckets
    (so (d) does not);
  * the mapping — kind "forecast_bias", severity medium, key
    "forecast_bias:<item_code>/<site_code>", the item and site
    codes set, title "Forecast bias", view "#/demand", detail
    "forecast <over|under> actual by <p>% across <n> periods" (a
    positive bias is "over", the percent the absolute bias as a
    whole percent) and detected_on the accuracy result's as-of;
  * order — among the medium rows the forecast_bias rows come
    directly after the expedite_late rows, last of the mediums;
  * the response — the as_of is the latest of the plan's,
    stockout's and expedite's as-ofs (the forecast bias never
    reports one), the counts cover the rows returned, and the
    response shape is unchanged;
  * the failure — a forecast source that raises is listed in
    sources_failed as "forecast" while the other five sources still
    answer, with status 200;
  * the one read — the forecast source is called exactly once per
    GET.
"""

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
import app.feed.acumatica_client as acumatica_client_module
import app.feed.odoo_client as odoo_client_module
from app.main import app

URL = "/api/exceptions"

ADMIN_TOKEN = "dev-secret-token:admin"

# The pinned current UTC time (the route's failure-window anchor).
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)

# The as-of dates the successful sources report: the forecast's is
# the newest, so it is the response as_of.
DDMRP_AS_OF = date(2026, 10, 9)
STOCKOUT_AS_OF = date(2026, 10, 9)
EXPEDITE_AS_OF = "2026-10-10"
FORECAST_AS_OF = date(2026, 10, 11)

RELEASE_ERROR_TEXT = "Acumatica batch failed: HTTP 500 (request-id=818)"
PUSH_ERROR_TEXT = "boom: the Acumatica write failed (api-key=SECRET-818)"


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
                    "item_code": "ITEM-818-A",
                    "site_code": "SITE-818-A",
                    "on_hand": 0.0,
                    "on_hand_pct_of_red": 0.0,
                    "on_hand_status": "stocked_out",
                },
                {
                    "item_code": "ITEM-818-B",
                    "site_code": "SITE-818-B",
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
                "item_code": "ITEM-818-A",
                "site_code": "SITE-818-A",
                "p_stockout": 0.83,
                "projected_stockout_on": date(2026, 10, 12),
                "as_of": STOCKOUT_AS_OF,
            },
            {
                "category": "at_risk",
                "item_code": "ITEM-818-E",
                "site_code": "SITE-818-E",
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
                    "item_code": "ITEM-818-F",
                    "site_code": "SITE-818-F",
                    "open_units": 120.0,
                    "days_late": 3,
                    "reasons": ["late", "after_stockout"],
                },
                {
                    "order_nbr": "PO-2",
                    "line_nbr": 20,
                    "item_code": "ITEM-818-G",
                    "site_code": "SITE-818-G",
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
                "error": RELEASE_ERROR_TEXT,
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
                "item_code": "ITEM-818-A",
                "site_code": "SITE-818-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=2),
                "error": PUSH_ERROR_TEXT,
            },
        ]


class FakeRealisedAccuracy:
    """The fake for the route module's ``realised_accuracy``: the
    five canned item-sites, as of the canned as-of date."""

    calls = 0

    def __call__(self, **kwargs):
        FakeRealisedAccuracy.calls += 1
        return {
            "as_of": FORECAST_AS_OF,
            "compared_buckets": 284,
            "overall": {},
            "by_horizon": [],
            "item_sites": [
                {
                    "item_code": "ITEM-818-A",
                    "site_code": "SITE-818-A",
                    "method": "moving_average",
                    "compared_buckets": 120,
                    "effective_bias": 0.84,
                },
                {
                    "item_code": "ITEM-818-B",
                    "site_code": "SITE-818-B",
                    "method": "moving_average",
                    "compared_buckets": 72,
                    "effective_bias": -0.71,
                },
                # Below the 0.5 absolute bias: not an exception.
                {
                    "item_code": "ITEM-818-C",
                    "site_code": "SITE-818-C",
                    "method": "moving_average",
                    "compared_buckets": 50,
                    "effective_bias": 0.49,
                },
                # Fewer than 3 compared buckets: not an exception.
                {
                    "item_code": "ITEM-818-D",
                    "site_code": "SITE-818-D",
                    "method": "moving_average",
                    "compared_buckets": 2,
                    "effective_bias": 0.9,
                },
                # No bias at all: not an exception.
                {
                    "item_code": "ITEM-818-E",
                    "site_code": "SITE-818-E",
                    "method": "moving_average",
                    "compared_buckets": 40,
                    "effective_bias": None,
                },
            ],
        }


def _expected_rows():
    """The full expected triage list, in order."""
    return [
        {
            "kind": "buffer_stocked_out",
            "severity": "high",
            "key": "buffer_stocked_out:ITEM-818-A/SITE-818-A",
            "item_code": "ITEM-818-A",
            "site_code": "SITE-818-A",
            "title": "Buffer stocked out",
            "detail": "on hand 0 (0% of top of red)",
            "view": "#/buffers",
            "detected_on": DDMRP_AS_OF.isoformat(),
        },
        {
            "kind": "stockout_likely",
            "severity": "high",
            "key": "stockout_likely:ITEM-818-A/SITE-818-A",
            "item_code": "ITEM-818-A",
            "site_code": "SITE-818-A",
            "title": "Stockout likely",
            "detail": "projected stockout 2026-10-12, 83% chance",
            "view": "#/queue?kind=stockout",
            "detected_on": STOCKOUT_AS_OF.isoformat(),
        },
        {
            "kind": "expedite_after_stockout",
            "severity": "high",
            "key": "expedite_after_stockout:PO-1/10",
            "item_code": "ITEM-818-F",
            "site_code": "SITE-818-F",
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
            "key": "buffer_critical:ITEM-818-B/SITE-818-B",
            "item_code": "ITEM-818-B",
            "site_code": "SITE-818-B",
            "title": "Buffer critical",
            "detail": "on hand 5 (25% of top of red)",
            "view": "#/buffers",
            "detected_on": DDMRP_AS_OF.isoformat(),
        },
        {
            "kind": "stockout_at_risk",
            "severity": "medium",
            "key": "stockout_at_risk:ITEM-818-E/SITE-818-E",
            "item_code": "ITEM-818-E",
            "site_code": "SITE-818-E",
            "title": "Stockout at risk",
            "detail": "projected stockout 2026-10-20, 45% chance",
            "view": "#/queue?kind=stockout",
            "detected_on": STOCKOUT_AS_OF.isoformat(),
        },
        {
            "kind": "expedite_late",
            "severity": "medium",
            "key": "expedite_late:PO-2/20",
            "item_code": "ITEM-818-G",
            "site_code": "SITE-818-G",
            "title": "Expedite late",
            "detail": "75 units, 2 days late",
            "view": "#/expedite",
            "detected_on": EXPEDITE_AS_OF,
        },
        {
            "kind": "forecast_bias",
            "severity": "medium",
            "key": "forecast_bias:ITEM-818-A/SITE-818-A",
            "item_code": "ITEM-818-A",
            "site_code": "SITE-818-A",
            "title": "Forecast bias",
            "detail": "forecast over actual by 84% across 120 periods",
            "view": "#/demand",
            "detected_on": FORECAST_AS_OF.isoformat(),
        },
        {
            "kind": "forecast_bias",
            "severity": "medium",
            "key": "forecast_bias:ITEM-818-B/SITE-818-B",
            "item_code": "ITEM-818-B",
            "site_code": "SITE-818-B",
            "title": "Forecast bias",
            "detail": "forecast under actual by 71% across 72 periods",
            "view": "#/demand",
            "detected_on": FORECAST_AS_OF.isoformat(),
        },
    ]


def _assert_response(body, expected_rows, sources_failed, as_of):
    assert set(body) == {"as_of", "counts", "rows", "sources_failed"}
    assert body["as_of"] == as_of
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
        for row in expected_rows
    ]
    assert body["counts"] == {
        "high": sum(1 for row in expected_rows if row["severity"] == "high"),
        "medium": sum(1 for row in expected_rows if row["severity"] == "medium"),
        "acknowledged": 0,
        "new": 0,
    }


def test_issue818_freeform(monkeypatch, client):
    # The six sources in the route module are replaced with fakes
    # that answer canned rows, the way the issue 795 proof replaces
    # the ERP clients.  Each fake records its calls: every source
    # must be read exactly once per call.
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

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    anon = TestClient(app)

    # No token is a 401.
    assert anon.get(URL).status_code == 401

    # --- The list: the two forecast-bias rows, in order. -----------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    expected_rows = _expected_rows()
    _assert_response(body, expected_rows, [], EXPEDITE_AS_OF)

    # Selection: only the 0.84-over-120 and the -0.71-over-72
    # item-sites became rows — the 0.49 bias, the 2-bucket site and
    # the null bias did not.
    forecast_rows = [row for row in body["rows"] if row["kind"] == "forecast_bias"]
    assert [row["key"] for row in forecast_rows] == [
        "forecast_bias:ITEM-818-A/SITE-818-A",
        "forecast_bias:ITEM-818-B/SITE-818-B",
    ]
    assert not any(
        row["item_code"] in ("ITEM-818-C", "ITEM-818-D", "ITEM-818-E")
        for row in body["rows"]
        if row["kind"] == "forecast_bias"
    )
    # The mapping: kind, severity, key, view, detail, detected_on.
    by_key = {row["key"]: row for row in body["rows"]}
    over = by_key["forecast_bias:ITEM-818-A/SITE-818-A"]
    under = by_key["forecast_bias:ITEM-818-B/SITE-818-B"]
    for row, detail in (
        (over, "forecast over actual by 84% across 120 periods"),
        (under, "forecast under actual by 71% across 72 periods"),
    ):
        assert row["kind"] == "forecast_bias"
        assert row["severity"] == "medium"
        assert row["title"] == "Forecast bias"
        assert row["view"] == "#/demand"
        assert row["detail"] == detail
        assert row["detected_on"] == FORECAST_AS_OF.isoformat()
    # Order: among the medium rows the forecast_bias rows come
    # directly after the expedite_late rows, last of the mediums.
    medium = [row for row in body["rows"] if row["severity"] == "medium"]
    medium_kinds = [row["kind"] for row in medium]
    assert medium_kinds == [
        "buffer_critical",
        "stockout_at_risk",
        "expedite_late",
        "forecast_bias",
        "forecast_bias",
    ]
    last_expedite_late = max(
        index for index, kind in enumerate(medium_kinds) if kind == "expedite_late"
    )
    first_forecast_bias = min(
        index for index, kind in enumerate(medium_kinds) if kind == "forecast_bias"
    )
    assert first_forecast_bias == last_expedite_late + 1

    # The one read: every source, the forecast source included, was
    # called exactly once per GET.
    assert FakeDdmrpSource.calls == 1
    assert FakeStockoutSource.calls == 1
    assert FakeExpediteSource.calls == 1
    assert FakePlanReleasesRepo.calls == 1
    assert FakeParameterWritebacksRepo.calls == 1
    assert FakeRealisedAccuracy.calls == 1

    # A second GET reads every source exactly once more.
    again = admin.get(URL)
    assert again.status_code == 200
    assert again.json() == body
    assert FakeRealisedAccuracy.calls == 2

    # --- The failure: a raising forecast source is listed as
    # "forecast", the other sources still answer, with status 200.
    class RaisingRealisedAccuracy:
        calls = 0

        def __call__(self, **kwargs):
            RaisingRealisedAccuracy.calls += 1
            raise RuntimeError("the accuracy query is down")

    monkeypatch.setattr(exceptions, "realised_accuracy", RaisingRealisedAccuracy())
    failed = admin.get(URL)
    assert failed.status_code == 200
    failed_body = failed.json()
    expected_without_forecast = [
        row for row in expected_rows if row["kind"] != "forecast_bias"
    ]
    _assert_response(
        failed_body, expected_without_forecast, ["forecast"], EXPEDITE_AS_OF
    )
    # The failed source was still read exactly once.
    assert RaisingRealisedAccuracy.calls == 1
