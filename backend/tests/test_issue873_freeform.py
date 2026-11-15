"""Issue #873 proof: the awaiting-approval detail's wording.

The "release_awaiting_approval" detail reads "<order_nbr> to
<target> awaiting approval in the ERP for <wait>", where <target>
is the vendor code, else the supplier code, else "an unknown
supplier" — never the literal "None" (AC-3) — and <wait> is "1 day"
at exactly one whole day and "<n> days" at every other count
(AC-4).  Proved through ``GET /api/exceptions`` with the six
sources of the triage list replaced in the route module with fakes
the way the issue 865 proof does, and the route's "current UTC
time" pinned:

  * no code (AC-1) — a "created" row with order_nbr "PO-FX5-1", no
    vendor or supplier code and ``updated_at`` five days ago reads
    "PO-FX5-1 to an unknown supplier awaiting approval in the ERP
    for 5 days"; a row whose codes are null, and one whose codes
    are empty strings, read the same "an unknown supplier"; a row
    with a vendor code is unchanged ("... to V-9 ... for 5 days");
  * one day (AC-2) — with ``exceptions.release_approval_days``
    stored as 1, a "created" row with vendor "V-1" whose
    ``updated_at`` is one day and one hour ago reads "PO-FX5-2 to
    V-1 awaiting approval in the ERP for 1 day";
  * many days (AC-2) — the same row at three days reads "PO-FX5-2
    to V-1 awaiting approval in the ERP for 3 days".

Every other row, field and kind of the triage list is unchanged.
"""

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
import app.feed.acumatica_client as acumatica_client_module
import app.feed.odoo_client as odoo_client_module
from app.main import app

URL = "/api/exceptions"
SETTINGS_URL = "/api/settings"
RELEASE_DAYS_KEY = "exceptions.release_approval_days"

# The pinned current UTC time (the route's window anchor).
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)

# The as-of dates the successful sources report: the expedite's is
# the newest, so it is the response as_of (the forecast bias and
# the failure kinds report none, and neither does release awaiting
# approval).
DDMRP_AS_OF = date(2026, 10, 9)
STOCKOUT_AS_OF = date(2026, 10, 9)
EXPEDITE_AS_OF = "2026-10-10"
FORECAST_AS_OF = date(2026, 10, 11)


class FakeDdmrpSource:
    """The fake for the route module's DDMRP source: answers the
    canned plan row as of the canned as-of date."""

    calls = 0

    def __call__(self):
        FakeDdmrpSource.calls += 1
        return (
            DDMRP_AS_OF,
            [
                {
                    "item_code": "ITEM-873-A",
                    "site_code": "SITE-873-A",
                    "on_hand": 0.0,
                    "on_hand_pct_of_red": 0.0,
                    "on_hand_status": "stocked_out",
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
                "item_code": "ITEM-873-A",
                "site_code": "SITE-873-A",
                "p_stockout": 0.83,
                "projected_stockout_on": date(2026, 10, 12),
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
                    "item_code": "ITEM-873-C",
                    "site_code": "SITE-873-C",
                    "open_units": 120.0,
                    "days_late": 3,
                    "reasons": ["late"],
                },
            ],
        }


class FakePlanReleasesRepo:
    """The fake for the route module's plan_releases repository: the
    scenarios set ``rows`` (the canned plan_releases rows), and the
    read raises while ``raise_on_read`` is true."""

    rows: list = []
    calls = 0
    raise_on_read = False

    def list_newest(self):
        FakePlanReleasesRepo.calls += 1
        if FakePlanReleasesRepo.raise_on_read:
            raise RuntimeError("the plan_releases read is down")
        return list(FakePlanReleasesRepo.rows)


class FakeOpenPurchaseRepo:
    """The fake for the route module's open-purchase repository: the
    scenarios set ``rows`` (the newest snapshot's rows), and the read
    raises while ``raise_on_read`` is true."""

    rows: list = []
    calls = 0
    raise_on_read = False

    def list_latest(self):
        FakeOpenPurchaseRepo.calls += 1
        if FakeOpenPurchaseRepo.raise_on_read:
            raise RuntimeError("the open-purchase read is down")
        return list(FakeOpenPurchaseRepo.rows)


class FakeParameterWritebacksRepo:
    """The fake for the route module's parameter_writebacks
    repository."""

    calls = 0

    def list(self, limit=100, offset=0, item_code=None, site_code=None):
        FakeParameterWritebacksRepo.calls += 1
        return [
            {
                "id": "pw-1",
                "item_code": "ITEM-873-A",
                "site_code": "SITE-873-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=2),
                "error": "boom: the Acumatica write failed (api-key=SECRET-873)",
            },
        ]


class FakeRealisedAccuracy:
    """The fake for the route module's ``realised_accuracy``: one
    item-site with a bias of 0.84 over 120 compared periods — a
    forecast-bias row against the default settings."""

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
                    "item_code": "ITEM-873-B",
                    "site_code": "SITE-873-B",
                    "method": "moving_average",
                    "compared_buckets": 120,
                    "effective_bias": 0.84,
                },
            ],
        }


def _release_row(row_id, outcome, order_nbr, vendor_code, age, supplier_code):
    """One canned plan_releases row, updated ``age`` before the
    pinned current UTC time."""
    return {
        "id": row_id,
        "outcome": outcome,
        "order_nbr": order_nbr,
        "vendor_code": vendor_code,
        "supplier_code": supplier_code,
        "updated_at": NOW - age,
        "error": None,
    }


# The failed-release row every scenario carries: within the 14-day
# window, so it keeps answering release_failed from the very same
# plan_releases read the awaiting kind filters.
FAILED_ROW = _release_row(901, "error", None, "V-100", timedelta(days=1), "S-100")

# The six rows the other sources always answer, in the response's
# order (high before medium, then kind order).
BASE_ROWS = [
    {
        "kind": "buffer_stocked_out",
        "severity": "high",
        "key": "buffer_stocked_out:ITEM-873-A/SITE-873-A",
        "item_code": "ITEM-873-A",
        "site_code": "SITE-873-A",
        "title": "Buffer stocked out",
        "detail": "on hand 0 (0% of top of red)",
        "view": "#/buffers",
        "detected_on": DDMRP_AS_OF.isoformat(),
    },
    {
        "kind": "stockout_likely",
        "severity": "high",
        "key": "stockout_likely:ITEM-873-A/SITE-873-A",
        "item_code": "ITEM-873-A",
        "site_code": "SITE-873-A",
        "title": "Stockout likely",
        "detail": "projected stockout 2026-10-12, 83% chance",
        "view": "#/queue?kind=stockout",
        "detected_on": STOCKOUT_AS_OF.isoformat(),
    },
    {
        "kind": "release_failed",
        "severity": "high",
        "key": "release_failed:901",
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
        "kind": "expedite_late",
        "severity": "medium",
        "key": "expedite_late:PO-1/10",
        "item_code": "ITEM-873-C",
        "site_code": "SITE-873-C",
        "title": "Expedite late",
        "detail": "120 units, 3 days late",
        "view": "#/expedite",
        "detected_on": EXPEDITE_AS_OF,
    },
    {
        "kind": "forecast_bias",
        "severity": "medium",
        "key": "forecast_bias:ITEM-873-B/SITE-873-B",
        "item_code": "ITEM-873-B",
        "site_code": "SITE-873-B",
        "title": "Forecast bias",
        "detail": "forecast over actual by 84% across 120 periods",
        "view": "#/demand",
        "detected_on": FORECAST_AS_OF.isoformat(),
    },
]


def _awaiting_row(row_id: int, detail: str, age: timedelta) -> dict:
    """The expected exception row of one in-flight release row, with
    the detail's exact wording spelled out."""
    return {
        "kind": "release_awaiting_approval",
        "severity": "medium",
        "key": f"release_awaiting_approval:{row_id}",
        "item_code": None,
        "site_code": None,
        "title": "Release awaiting approval",
        "detail": detail,
        "view": "#/releases",
        "detected_on": (NOW - age).date().isoformat(),
    }


def _assert_response(body, awaiting_rows, sources_failed, base_rows=BASE_ROWS):
    """The full expected triage list: the canned rows plus the
    release-awaiting-approval rows, in order."""
    expected = base_rows + awaiting_rows
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


def test_issue873_freeform(monkeypatch, client, admin_token):
    # The six sources in the route module are replaced with fakes
    # that answer canned rows, the way the issue 865 proof does.
    # Every fake records its calls.
    FakeDdmrpSource.calls = 0
    FakeStockoutSource.calls = 0
    FakeExpediteSource.calls = 0
    FakePlanReleasesRepo.rows = []
    FakePlanReleasesRepo.calls = 0
    FakePlanReleasesRepo.raise_on_read = False
    FakeOpenPurchaseRepo.rows = []
    FakeOpenPurchaseRepo.calls = 0
    FakeOpenPurchaseRepo.raise_on_read = False
    FakeParameterWritebacksRepo.calls = 0
    FakeRealisedAccuracy.calls = 0
    monkeypatch.setattr(exceptions, "_ddmrp_source", FakeDdmrpSource())
    monkeypatch.setattr(exceptions, "stockout_ranked", FakeStockoutSource())
    monkeypatch.setattr(exceptions, "expedite_list", FakeExpediteSource())
    monkeypatch.setattr(exceptions, "_plan_releases_repo", FakePlanReleasesRepo())
    monkeypatch.setattr(exceptions, "_open_purchase_lines_repo", FakeOpenPurchaseRepo())
    monkeypatch.setattr(
        exceptions, "_parameter_writebacks_repo", FakeParameterWritebacksRepo()
    )
    monkeypatch.setattr(exceptions, "realised_accuracy", FakeRealisedAccuracy())
    # The window anchor: the route's "current UTC time".
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

    # The GETs run as a non-admin (the route is open to any
    # signed-in user); the setting write needs the admin token.
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})
    exceptions_gets = {"count": 0}

    def _get():
        exceptions_gets["count"] += 1
        response = client.get(URL)
        assert response.status_code == 200, response.text
        return response.json()

    # --- No code: "an unknown supplier", never the literal "None" ----
    # PO-FX5-1 carries no vendor_code or supplier_code at all; the
    # null-coded and empty-string-coded rows read the same target.
    no_code_row = {
        "id": 950,
        "outcome": "created",
        "order_nbr": "PO-FX5-1",
        "updated_at": NOW - timedelta(days=5),
        "error": None,
    }
    FakePlanReleasesRepo.rows = [
        no_code_row,
        _release_row(951, "created", "PO-FX5-1A", None, timedelta(days=5), None),
        _release_row(952, "created", "PO-FX5-1B", "", timedelta(days=5), ""),
        # A coded row keeps the unchanged wording.
        _release_row(960, "created", "PO-FX5-9", "V-9", timedelta(days=5), "S-9"),
        FAILED_ROW,
    ]
    FakeOpenPurchaseRepo.rows = []
    body = _get()
    five_days = timedelta(days=5)
    _assert_response(
        body,
        [
            _awaiting_row(
                950,
                "PO-FX5-1 to an unknown supplier awaiting approval "
                "in the ERP for 5 days",
                five_days,
            ),
            _awaiting_row(
                951,
                "PO-FX5-1A to an unknown supplier awaiting approval "
                "in the ERP for 5 days",
                five_days,
            ),
            _awaiting_row(
                952,
                "PO-FX5-1B to an unknown supplier awaiting approval "
                "in the ERP for 5 days",
                five_days,
            ),
            _awaiting_row(
                960,
                "PO-FX5-9 to V-9 awaiting approval in the ERP for 5 days",
                five_days,
            ),
        ],
        [],
    )
    for row in body["rows"]:
        if row["kind"] == "release_awaiting_approval":
            assert "None" not in row["detail"]

    # --- One day: the setting stored as 1, a 1-day-1-hour wait -------
    put = admin.put(f"{SETTINGS_URL}/{RELEASE_DAYS_KEY}", json={"value": 1})
    assert put.status_code == 200, put.text
    assert put.json()["value"] == 1
    assert put.json()["source"] == "system"
    one_day_one_hour = timedelta(days=1, hours=1)
    FakePlanReleasesRepo.rows = [
        _release_row(970, "created", "PO-FX5-2", "V-1", one_day_one_hour, None),
        FAILED_ROW,
    ]
    body = _get()
    _assert_response(
        body,
        [
            _awaiting_row(
                970,
                "PO-FX5-2 to V-1 awaiting approval in the ERP for 1 day",
                one_day_one_hour,
            ),
        ],
        [],
    )

    # --- Many days: the same row at three days -----------------------
    three_days = timedelta(days=3)
    FakePlanReleasesRepo.rows = [
        _release_row(970, "created", "PO-FX5-2", "V-1", three_days, None),
        FAILED_ROW,
    ]
    body = _get()
    _assert_response(
        body,
        [
            _awaiting_row(
                970,
                "PO-FX5-2 to V-1 awaiting approval in the ERP for 3 days",
                three_days,
            ),
        ],
        [],
    )

    # --- The one reads per call ---------------------------------------
    assert FakePlanReleasesRepo.calls == exceptions_gets["count"]
    assert FakeOpenPurchaseRepo.calls == exceptions_gets["count"]
    assert settings_reads["count"] == exceptions_gets["count"]
