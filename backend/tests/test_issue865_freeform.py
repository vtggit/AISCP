"""Issue #865 proof: the "release_awaiting_approval" exception kind.

AC-1 — ``exceptions.release_approval_days`` is registered the way
``exceptions.forecast_bias_threshold`` is (scope "system", type
"number", default 3, min 1, max 60), and the releases source of
``GET /api/exceptions`` yields one row per in-flight ``plan_releases``
row older than the setting: in flight is the very rule
``app.aggregate.projection._in_flight_supply_lines`` applies (outcome
"created" or "exists", a non-null ``order_nbr`` and that order number
not among the newest open-purchase snapshot's feed order numbers),
older means the row's ``updated_at`` is at least the setting's number
of days before the current UTC time (inclusive of the exact cutoff).
The row is kind "release_awaiting_approval" (medium), keyed by the
row id, titled "Release awaiting approval", detailed "<order_nbr> to
<vendor_code, else supplier_code> awaiting approval in the ERP for
<n> days" (n the whole days since ``updated_at``), viewed at
"``#/releases``", detected on the row's date — and it sorts after
"forecast_bias" and before "release_failed" in the kind order.  The
release rows come from the single ``plan_releases`` read the
failed-release rows already use, the newest open-purchase snapshot is
read once per call and a failure of either read lists "releases" in
``sources_failed`` — no new source name.  The setting is read once
per call, a missing or out-of-range stored value falling back to the
registry default.  The six sources of the triage list are replaced in
the route module with fakes the way the issue 822 proof does: the
DDMRP source (the shared plan code), ``stockout_ranked``,
``expedite_list``, the two failure repositories (``plan_releases``,
``parameter_writebacks``), the open-purchase repository
(``list_latest``) and ``realised_accuracy``.

AC-2 — proved in one test:

  * flagged — a "created" row with order_nbr "PO-S10-1", vendor
    "V-1" and ``updated_at`` five days ago, absent from the
    snapshot, yields one "release_awaiting_approval" row (severity
    "medium", detail "PO-S10-1 to V-1 awaiting approval in the ERP
    for 5 days", view "#/releases");
  * "exists" counts too — the same row with outcome "exists" is
    flagged the same way;
  * too recent — the same row two days old is not flagged with the
    default of 3; with the setting stored as 1, it is flagged;
  * in the feed — once the snapshot holds a line whose business key
    carries "PO-S10-1", it is not flagged;
  * not in flight — rows with outcome "error", "pending" or
    "aborted", or a null ``order_nbr``, never yield the new kind;
  * order — the new row sorts after a "forecast_bias" row and before
    a "release_failed" row in the kind order;
  * failure — a snapshot read that raises lists "releases" in
    ``sources_failed`` and still answers 200;
  * the setting — ``GET /api/settings`` lists
    ``exceptions.release_approval_days`` with default 3.
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
RELEASE_DAYS_KEY = "exceptions.release_approval_days"

# The pinned current UTC time (the route's window anchor).
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)

# The as-of dates the successful sources report: the expedite's is
# the newest, so it is the response as_of (the forecast bias and the
# two failure kinds report none, and neither does release awaiting
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
                    "item_code": "ITEM-865-A",
                    "site_code": "SITE-865-A",
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
                "item_code": "ITEM-865-A",
                "site_code": "SITE-865-A",
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
                    "item_code": "ITEM-865-C",
                    "site_code": "SITE-865-C",
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
                "item_code": "ITEM-865-A",
                "site_code": "SITE-865-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=2),
                "error": "boom: the Acumatica write failed (api-key=SECRET-865)",
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
                    "item_code": "ITEM-865-B",
                    "site_code": "SITE-865-B",
                    "method": "moving_average",
                    "compared_buckets": 120,
                    "effective_bias": 0.84,
                },
            ],
        }


def _release_row(row_id, outcome, order_nbr, vendor_code, days_old, supplier_code):
    """One canned plan_releases row, updated ``days_old`` days ago."""
    return {
        "id": row_id,
        "outcome": outcome,
        "order_nbr": order_nbr,
        "vendor_code": vendor_code,
        "supplier_code": supplier_code,
        "updated_at": NOW - timedelta(days=days_old),
        "error": None,
    }


# The failed-release row every scenario carries: within the 14-day
# window, so it keeps answering release_failed from the very same
# plan_releases read the new kind filters.
FAILED_ROW = _release_row(901, "error", None, "V-100", 1, "S-100")

# The six rows the other sources always answer, in the response's
# order (high before medium, then kind order).
BASE_ROWS = [
    {
        "kind": "buffer_stocked_out",
        "severity": "high",
        "key": "buffer_stocked_out:ITEM-865-A/SITE-865-A",
        "item_code": "ITEM-865-A",
        "site_code": "SITE-865-A",
        "title": "Buffer stocked out",
        "detail": "on hand 0 (0% of top of red)",
        "view": "#/buffers",
        "detected_on": DDMRP_AS_OF.isoformat(),
    },
    {
        "kind": "stockout_likely",
        "severity": "high",
        "key": "stockout_likely:ITEM-865-A/SITE-865-A",
        "item_code": "ITEM-865-A",
        "site_code": "SITE-865-A",
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
        "item_code": "ITEM-865-C",
        "site_code": "SITE-865-C",
        "title": "Expedite late",
        "detail": "120 units, 3 days late",
        "view": "#/expedite",
        "detected_on": EXPEDITE_AS_OF,
    },
    {
        "kind": "forecast_bias",
        "severity": "medium",
        "key": "forecast_bias:ITEM-865-B/SITE-865-B",
        "item_code": "ITEM-865-B",
        "site_code": "SITE-865-B",
        "title": "Forecast bias",
        "detail": "forecast over actual by 84% across 120 periods",
        "view": "#/demand",
        "detected_on": FORECAST_AS_OF.isoformat(),
    },
]


def _awaiting_row(row_id, order_nbr, target, days_old) -> dict:
    """The expected exception row of one in-flight release row."""
    return {
        "kind": "release_awaiting_approval",
        "severity": "medium",
        "key": f"release_awaiting_approval:{row_id}",
        "item_code": None,
        "site_code": None,
        "title": "Release awaiting approval",
        "detail": (
            f"{order_nbr} to {target} awaiting approval "
            f"in the ERP for {days_old} days"
        ),
        "view": "#/releases",
        "detected_on": (NOW - timedelta(days=days_old)).date().isoformat(),
    }


def _assert_response(body, awaiting_rows, sources_failed, base_rows=BASE_ROWS):
    """The full expected triage list: the canned rows (minus the
    failed release when the releases source failed) plus the
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


def test_issue865_freeform(monkeypatch, client, admin_token):
    # The six sources in the route module are replaced with fakes
    # that answer canned rows, the way the issue 822 proof does.
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

    # --- The setting: listed with default 3 ---------------------------
    listing = admin.get(SETTINGS_URL)
    assert listing.status_code == 200, listing.text
    entry = _setting_entry(listing.json(), RELEASE_DAYS_KEY)
    assert entry["scope"] == "system"
    assert entry["type"] == "number"
    assert entry["default"] == 3
    assert entry["min"] == 1
    assert entry["max"] == 60
    assert entry["description"].strip()
    assert entry["value"] == 3, "the default applies while unstored"
    assert entry["source"] == "default"
    assert entry["editable"] is True, "the admin may edit a system setting"

    # --- Flagged: "created", five days old, absent from the snapshot --
    FakePlanReleasesRepo.rows = [
        _release_row(900, "created", "PO-S10-1", "V-1", 5, "S-1"),
        FAILED_ROW,
    ]
    FakeOpenPurchaseRepo.rows = []
    body = _get()
    flagged = _awaiting_row(900, "PO-S10-1", "V-1", 5)
    _assert_response(body, [flagged], [])
    awaiting = next(
        row for row in body["rows"] if row["kind"] == "release_awaiting_approval"
    )
    assert awaiting["severity"] == "medium"
    assert awaiting["detail"] == (
        "PO-S10-1 to V-1 awaiting approval in the ERP for 5 days"
    )
    assert awaiting["view"] == "#/releases"
    assert awaiting["detected_on"] == "2026-10-06"
    # The one read per call: one plan_releases read (shared by both
    # kinds), one snapshot read and one settings read.
    assert FakePlanReleasesRepo.calls == 1
    assert FakeOpenPurchaseRepo.calls == 1
    assert settings_reads["count"] == 1

    # --- "exists" counts too ------------------------------------------
    FakePlanReleasesRepo.rows = [
        _release_row(900, "exists", "PO-S10-1", "V-1", 5, "S-1"),
        FAILED_ROW,
    ]
    body = _get()
    # Flagged the same way as the "created" row.
    _assert_response(body, [flagged], [])

    # --- Too recent: two days old is not flagged at the default 3 -----
    FakePlanReleasesRepo.rows = [
        _release_row(900, "created", "PO-S10-1", "V-1", 2, "S-1"),
        FAILED_ROW,
    ]
    body = _get()
    _assert_response(body, [], [])
    assert not any(row["kind"] == "release_awaiting_approval" for row in body["rows"])

    # --- Too recent: flagged once the setting is stored as 1 ----------
    put = admin.put(f"{SETTINGS_URL}/{RELEASE_DAYS_KEY}", json={"value": 1})
    assert put.status_code == 200, put.text
    assert put.json()["value"] == 1
    assert put.json()["source"] == "system"
    body = _get()
    _assert_response(body, [_awaiting_row(900, "PO-S10-1", "V-1", 2)], [])
    # --- Out of range: stored values outside the bounds fall back to
    # the default 3 (the two-days-old row is not flagged) --------------
    _store_out_of_range(RELEASE_DAYS_KEY, 61)
    body = _get()
    _assert_response(body, [], [])
    _store_out_of_range(RELEASE_DAYS_KEY, 0)
    body = _get()
    _assert_response(body, [], [])
    # The listing serves the stored value ...
    stored = _setting_entry(admin.get(SETTINGS_URL).json(), RELEASE_DAYS_KEY)
    assert stored["value"] == 0
    assert stored["source"] == "system"
    # ... the reset restores the default for the following scenarios.
    deleted = admin.delete(f"{SETTINGS_URL}/{RELEASE_DAYS_KEY}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["value"] == 3
    assert deleted.json()["source"] == "default"

    # --- In the feed: the snapshot carries the order number -----------
    FakePlanReleasesRepo.rows = [
        _release_row(900, "created", "PO-S10-1", "V-1", 5, "S-1"),
        FAILED_ROW,
    ]
    FakeOpenPurchaseRepo.rows = [
        {
            "business_key": "acumatica:PO-S10-1:10",
            "item_code": "ITEM-865-D",
            "site_code": "SITE-865-D",
            "supplier_code": "S-1",
            "open_units": 100,
            "as_of": date(2026, 10, 11),
        },
    ]
    body = _get()
    _assert_response(body, [], [])
    assert not any(row["kind"] == "release_awaiting_approval" for row in body["rows"])
    FakeOpenPurchaseRepo.rows = []

    # --- The vendor code falls back to the supplier code --------------
    FakePlanReleasesRepo.rows = [
        _release_row(900, "created", "PO-S10-1", None, 5, "S-7"),
        FAILED_ROW,
    ]
    body = _get()
    _assert_response(body, [_awaiting_row(900, "PO-S10-1", "S-7", 5)], [])

    # --- Not in flight: wrong outcomes and a null order_nbr -----------
    # The error / pending / aborted rows sit 20 days old, outside the
    # 14-day failure window, so none of them answers any row at all.
    FakePlanReleasesRepo.rows = [
        _release_row(910, "error", "PO-S10-2", "V-2", 20, "S-2"),
        _release_row(911, "pending", "PO-S10-3", "V-3", 20, "S-3"),
        _release_row(912, "aborted", "PO-S10-4", "V-4", 20, "S-4"),
        _release_row(913, "created", None, "V-5", 5, "S-5"),
        FAILED_ROW,
    ]
    body = _get()
    _assert_response(body, [], [])
    assert not any(row["kind"] == "release_awaiting_approval" for row in body["rows"])

    # --- Order: after forecast_bias, before release_failed ------------
    order = exceptions._KIND_ORDER
    assert (
        order["forecast_bias"]
        < order["release_awaiting_approval"]
        < order["release_failed"]
    )
    FakePlanReleasesRepo.rows = [
        _release_row(900, "created", "PO-S10-1", "V-1", 5, "S-1"),
        FAILED_ROW,
    ]
    body = _get()
    _assert_response(body, [flagged], [])
    kinds = [row["kind"] for row in body["rows"]]
    assert kinds.index("release_awaiting_approval") > kinds.index("forecast_bias")
    assert kinds == [
        "buffer_stocked_out",
        "stockout_likely",
        "release_failed",
        "erp_push_failed",
        "expedite_late",
        "forecast_bias",
        "release_awaiting_approval",
    ]

    # --- Failure: a raising snapshot read lists "releases" ------------
    # The whole releases source fails: the failed-release rows drop
    # out with the new kind, and the other sources still answer 200.
    no_failed_base = [row for row in BASE_ROWS if row["kind"] != "release_failed"]
    FakeOpenPurchaseRepo.raise_on_read = True
    body = _get()
    _assert_response(body, [], ["releases"], base_rows=no_failed_base)
    assert not any(row["kind"] == "release_awaiting_approval" for row in body["rows"])
    FakeOpenPurchaseRepo.raise_on_read = False

    # --- Failure: a raising plan_releases read lists "releases" -------
    FakePlanReleasesRepo.raise_on_read = True
    body = _get()
    _assert_response(body, [], ["releases"], base_rows=no_failed_base)
    FakePlanReleasesRepo.raise_on_read = False

    # --- The one reads per call ---------------------------------------
    assert exceptions_gets["count"] == settings_reads["count"]
    assert FakePlanReleasesRepo.calls == exceptions_gets["count"]
    # The snapshot is read once per call, except on the call where the
    # plan_releases read failed first and short-circuited the source.
    assert FakeOpenPurchaseRepo.calls == exceptions_gets["count"] - 1
