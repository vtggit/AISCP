"""Issue #903 proof: the "transfer_awaiting_release" exception kind.

AC-1 — the "transfers" source of ``GET /api/exceptions`` reads the
``transfer_releases`` rows once per call through
``TransferReleasePostgresRepository.list_newest(limit=200)`` (the
failed-transfer rows, unchanged) and, alongside it, once per call
through ``TransferReleasePostgresRepository.list_in_flight()``
(every row whose outcome is "created" or "exists" and whose
``reference_nbr`` is non-null).  Each in-flight row whose
``erp_status`` is null or none of "Released", "Completed" and
"Canceled" and whose ``updated_at`` is at least the
``exceptions.release_approval_days`` system setting (default 3)
days before the current UTC time (inclusive of the exact cutoff)
yields one exception: kind "transfer_awaiting_release" (medium),
key "transfer_awaiting_release:<row id>", ``item_code`` and
``site_code`` null, title "Transfer awaiting release", detail
"transfer <reference_nbr> <from_site_code> → <to_site_code>
awaiting release in the ERP for <n> days" ("1 day" at exactly one
whole day; n the whole days since ``updated_at``), view
"``#/orders``", ``detected_on`` the row's date.  The setting is
read once per call, a missing or out-of-range stored value falling
back to the registry default, as for "release_awaiting_approval".
A failure of either transfer read lists "transfers" in
``sources_failed`` — the one source name — and the other sources
still answer, with status 200.  In the kind order,
"transfer_awaiting_release" stands directly after
"release_awaiting_approval" and before "release_failed".  Every
other kind, row, count and route is unchanged: the seven sources
of the triage list are replaced in the route module with fakes the
way the issue 865 proof does (the DDMRP source, ``stockout_ranked``,
``expedite_list``, the plan_releases and parameter_writebacks
repositories, the open-purchase repository ``list_latest``,
``realised_accuracy``), and the transfer releases repository is
faked for both its reads.

AC-2 — proved in one test:

  * flagged — a row with reference "000901", 154 → 159,
    ``erp_status`` "On Hold" and ``updated_at`` five days ago
    yields one "transfer_awaiting_release" row (severity "medium",
    detail "transfer 000901 154 → 159 awaiting release in the ERP
    for 5 days", view "#/orders"), and the stored error text
    appears nowhere in the response;
  * unknown status — the same row with a null ``erp_status`` is
    flagged the same way;
  * released — with ``erp_status`` "Released", "Completed" or
    "Canceled" nothing is flagged;
  * too recent — the row two days old is not flagged with the
    default of 3; with the setting stored as 1, it is flagged, and
    a row one day and one hour old reads "for 1 day";
  * order — the row sorts after a "release_awaiting_approval" row
    and before a "release_failed" row in the kind order;
  * failure — an in-flight read that raises lists "transfers" in
    ``sources_failed`` and still answers 200 with the other
    sources' rows.
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
# the newest, so it is the response as_of (the forecast bias, the
# failure kinds and the two awaiting kinds report none, their dates
# are the rows' detected_on).
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
                    "item_code": "ITEM-903-A",
                    "site_code": "SITE-903-A",
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
                "item_code": "ITEM-903-A",
                "site_code": "SITE-903-A",
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
                    "item_code": "ITEM-903-C",
                    "site_code": "SITE-903-C",
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
                "item_code": "ITEM-903-A",
                "site_code": "SITE-903-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=2),
                "error": "boom: the Acumatica write failed (api-key=SECRET-903)",
            },
        ]


class FakeTransferReleasesRepo:
    """The fake for the route module's transfer releases repository:
    the scenarios set ``rows`` (the newest-rows read) and
    ``in_flight_rows`` (the in-flight read), each read raises while
    its flag is true, and every newest-rows call records the limit
    it was called with."""

    rows: list = []
    in_flight_rows: list = []
    calls = 0
    in_flight_calls = 0
    raise_on_read = False
    raise_on_in_flight_read = False
    limits: list = []

    def list_newest(self, limit=50):
        FakeTransferReleasesRepo.calls += 1
        FakeTransferReleasesRepo.limits.append(limit)
        if FakeTransferReleasesRepo.raise_on_read:
            raise RuntimeError("the transfer_releases read is down")
        return list(FakeTransferReleasesRepo.rows)

    def list_in_flight(self):
        FakeTransferReleasesRepo.in_flight_calls += 1
        if FakeTransferReleasesRepo.raise_on_in_flight_read:
            raise RuntimeError("the in-flight transfer read is down")
        return list(FakeTransferReleasesRepo.in_flight_rows)


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
                    "item_code": "ITEM-903-B",
                    "site_code": "SITE-903-B",
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


def _transfer_row(
    row_id,
    outcome,
    from_site,
    to_site,
    updated_at,
    reference_nbr=None,
    erp_status=None,
    error=None,
):
    """One canned transfer_releases row, updated at ``updated_at``."""
    return {
        "id": row_id,
        "origin": "supply_plan",
        "from_site_code": from_site,
        "to_site_code": to_site,
        "lines": [],
        "line_count": 0,
        "outcome": outcome,
        "reference_nbr": reference_nbr,
        "erp_status": erp_status,
        "error": error,
        "created_at": updated_at,
        "updated_at": updated_at,
    }


# The failed release row every scenario carries: within the 14-day
# window, so it keeps answering release_failed.
FAILED_RELEASE_ROW = _release_row(901, "error", None, "V-100", 1, "S-100")

# The failed transfer row every scenario carries: within the 14-day
# window, so it keeps answering transfer_failed from the very same
# newest-rows read the new kind's in-flight read shares its source
# with.
FAILED_TRANSFER_ROW = _transfer_row(
    90310,
    "error",
    "154",
    "159",
    NOW - timedelta(days=1),
    error="boom: the Acumatica batch failed (api-key=SECRET-903-T)",
)


def _release_exception(row_id, order_nbr, target, days_old) -> dict:
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


def _transfer_exception(
    row_id, reference_nbr, from_site, to_site, updated_at, days_waited
) -> dict:
    """The expected exception row of one in-flight transfer row."""
    wait = "1 day" if days_waited == 1 else f"{days_waited} days"
    return {
        "kind": "transfer_awaiting_release",
        "severity": "medium",
        "key": f"transfer_awaiting_release:{row_id}",
        "item_code": None,
        "site_code": None,
        "title": "Transfer awaiting release",
        "detail": (
            f"transfer {reference_nbr} {from_site} → {to_site} "
            f"awaiting release in the ERP for {wait}"
        ),
        "view": "#/orders",
        "detected_on": updated_at.date().isoformat(),
    }


def _transfer_failed_exception(row_id, from_site, to_site, updated_at) -> dict:
    """The expected exception row of one failed transfer row."""
    return {
        "kind": "transfer_failed",
        "severity": "high",
        "key": f"transfer_failed:{row_id}",
        "item_code": None,
        "site_code": None,
        "title": "Transfer failed",
        "detail": f"transfer {from_site} → {to_site} failed",
        "view": "#/orders",
        "detected_on": updated_at.date().isoformat(),
    }


# The seven rows the other sources always answer, in the response's
# order (high before medium, then kind order): the transfer_failed
# row slots between the release_failed and the erp_push_failed rows.
BASE_ROWS = [
    {
        "kind": "buffer_stocked_out",
        "severity": "high",
        "key": "buffer_stocked_out:ITEM-903-A/SITE-903-A",
        "item_code": "ITEM-903-A",
        "site_code": "SITE-903-A",
        "title": "Buffer stocked out",
        "detail": "on hand 0 (0% of top of red)",
        "view": "#/buffers",
        "detected_on": DDMRP_AS_OF.isoformat(),
    },
    {
        "kind": "stockout_likely",
        "severity": "high",
        "key": "stockout_likely:ITEM-903-A/SITE-903-A",
        "item_code": "ITEM-903-A",
        "site_code": "SITE-903-A",
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
    _transfer_failed_exception(90310, "154", "159", NOW - timedelta(days=1)),
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
        "item_code": "ITEM-903-C",
        "site_code": "SITE-903-C",
        "title": "Expedite late",
        "detail": "120 units, 3 days late",
        "view": "#/expedite",
        "detected_on": EXPEDITE_AS_OF,
    },
    {
        "kind": "forecast_bias",
        "severity": "medium",
        "key": "forecast_bias:ITEM-903-B/SITE-903-B",
        "item_code": "ITEM-903-B",
        "site_code": "SITE-903-B",
        "title": "Forecast bias",
        "detail": "forecast over actual by 84% across 120 periods",
        "view": "#/demand",
        "detected_on": FORECAST_AS_OF.isoformat(),
    },
]

# The base rows when the whole transfers source failed: the
# transfer_failed row drops out with the new kind.
NO_TRANSFER_BASE_ROWS = [row for row in BASE_ROWS if row["kind"] != "transfer_failed"]


def _assert_response(body, extra_rows, sources_failed, base_rows=BASE_ROWS):
    """The full expected triage list: the canned rows plus the
    scenario's rows, in order."""
    expected = base_rows + extra_rows
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


def _setting_entry(body: list[dict], key: str) -> dict:
    entry = next((e for e in body if e["key"] == key), None)
    assert entry is not None, (key, body)
    return entry


def test_issue903_freeform(monkeypatch, client, admin_token):
    # The seven sources in the route module are replaced with fakes
    # that answer canned rows, the way the issue 865 proof does, and
    # the transfer releases repository is faked for both its reads.
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
    FakeTransferReleasesRepo.rows = []
    FakeTransferReleasesRepo.in_flight_rows = []
    FakeTransferReleasesRepo.calls = 0
    FakeTransferReleasesRepo.in_flight_calls = 0
    FakeTransferReleasesRepo.raise_on_read = False
    FakeTransferReleasesRepo.raise_on_in_flight_read = False
    FakeTransferReleasesRepo.limits = []
    FakeRealisedAccuracy.calls = 0
    monkeypatch.setattr(exceptions, "_ddmrp_source", FakeDdmrpSource())
    monkeypatch.setattr(exceptions, "stockout_ranked", FakeStockoutSource())
    monkeypatch.setattr(exceptions, "expedite_list", FakeExpediteSource())
    monkeypatch.setattr(exceptions, "_plan_releases_repo", FakePlanReleasesRepo())
    monkeypatch.setattr(exceptions, "_open_purchase_lines_repo", FakeOpenPurchaseRepo())
    monkeypatch.setattr(
        exceptions, "_parameter_writebacks_repo", FakeParameterWritebacksRepo()
    )
    monkeypatch.setattr(
        exceptions, "_transfer_releases_repo", FakeTransferReleasesRepo()
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
        return response.json(), response

    # --- The setting: the very same release-approval setting --------
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

    # The base state: the two failure reads' failed rows, nothing
    # in flight.
    FakePlanReleasesRepo.rows = [FAILED_RELEASE_ROW]
    FakeTransferReleasesRepo.rows = [FAILED_TRANSFER_ROW]
    FakeTransferReleasesRepo.in_flight_rows = []
    body, _ = _get()
    _assert_response(body, [], [])

    # --- Flagged: "On Hold", 154 -> 159, reference 000901, five days ---
    flagged_source_row = _transfer_row(
        90301,
        "created",
        "154",
        "159",
        NOW - timedelta(days=5),
        reference_nbr="000901",
        erp_status="On Hold",
        error="boom: the release is on hold (api-key=SECRET-903-H)",
    )
    FakeTransferReleasesRepo.in_flight_rows = [flagged_source_row]
    body, response = _get()
    flagged = _transfer_exception(
        90301, "000901", "154", "159", NOW - timedelta(days=5), 5
    )
    _assert_response(body, [flagged], [])
    assert flagged["severity"] == "medium"
    assert flagged["key"] == "transfer_awaiting_release:90301"
    assert flagged["detail"] == (
        "transfer 000901 154 → 159 awaiting release in the ERP for 5 days"
    )
    assert flagged["view"] == "#/orders"
    assert flagged["detected_on"] == (NOW - timedelta(days=5)).date().isoformat()
    # The stored error text appears nowhere in the response.
    assert "SECRET-903-H" not in response.text

    # --- Unknown status: a null erp_status is flagged the same way ---
    FakeTransferReleasesRepo.in_flight_rows = [
        _transfer_row(
            90302,
            "created",
            "154",
            "159",
            NOW - timedelta(days=5),
            reference_nbr="000902",
            erp_status=None,
        )
    ]
    body, _ = _get()
    _assert_response(
        body,
        [
            _transfer_exception(
                90302, "000902", "154", "159", NOW - timedelta(days=5), 5
            )
        ],
        [],
    )

    # --- Released: the released statuses stop the row counting -------
    for erp_status in ("Released", "Completed", "Canceled"):
        FakeTransferReleasesRepo.in_flight_rows = [
            _transfer_row(
                90303,
                "created",
                "154",
                "159",
                NOW - timedelta(days=5),
                reference_nbr="000903",
                erp_status=erp_status,
            )
        ]
        body, _ = _get()
        _assert_response(body, [], [])
        assert not any(
            row["kind"] == "transfer_awaiting_release" for row in body["rows"]
        )

    # --- Too recent: two days old is not flagged with the default 3 --
    FakeTransferReleasesRepo.in_flight_rows = [
        _transfer_row(
            90304,
            "created",
            "154",
            "159",
            NOW - timedelta(days=2),
            reference_nbr="000904",
            erp_status="On Hold",
        )
    ]
    body, _ = _get()
    _assert_response(body, [], [])
    assert not any(row["kind"] == "transfer_awaiting_release" for row in body["rows"])

    # --- The setting stored as 1: the two-days-old row is flagged ----
    put = admin.put(f"{SETTINGS_URL}/{RELEASE_DAYS_KEY}", json={"value": 1})
    assert put.status_code == 200, put.text
    assert put.json()["value"] == 1
    assert put.json()["source"] == "system"
    body, _ = _get()
    _assert_response(
        body,
        [
            _transfer_exception(
                90304, "000904", "154", "159", NOW - timedelta(days=2), 2
            )
        ],
        [],
    )

    # --- One day and one hour old reads "for 1 day" (setting 1) ------
    one_day_one_hour_ago = NOW - timedelta(days=1, hours=1)
    FakeTransferReleasesRepo.in_flight_rows = [
        _transfer_row(
            90305,
            "created",
            "154",
            "159",
            one_day_one_hour_ago,
            reference_nbr="000905",
            erp_status="On Hold",
        )
    ]
    body, _ = _get()
    _assert_response(
        body,
        [_transfer_exception(90305, "000905", "154", "159", one_day_one_hour_ago, 1)],
        [],
    )
    the_row = next(
        row for row in body["rows"] if row["kind"] == "transfer_awaiting_release"
    )
    assert the_row["detail"] == (
        "transfer 000905 154 → 159 awaiting release in the ERP for 1 day"
    )
    # The reset restores the default for the following scenarios.
    deleted = admin.delete(f"{SETTINGS_URL}/{RELEASE_DAYS_KEY}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["value"] == 3
    assert deleted.json()["source"] == "default"

    # --- Order: after release_awaiting_approval, before release_failed
    order = exceptions._KIND_ORDER
    assert (
        order["release_awaiting_approval"] + 1
        == order["transfer_awaiting_release"]
        < order["release_failed"]
    )
    FakePlanReleasesRepo.rows = [
        _release_row(900, "created", "PO-S10-1", "V-1", 5, "S-1"),
        FAILED_RELEASE_ROW,
    ]
    FakeOpenPurchaseRepo.rows = []
    FakeTransferReleasesRepo.in_flight_rows = [
        _transfer_row(
            90306,
            "created",
            "154",
            "159",
            NOW - timedelta(days=5),
            reference_nbr="000906",
            erp_status="On Hold",
        )
    ]
    body, _ = _get()
    _assert_response(
        body,
        [
            _release_exception(900, "PO-S10-1", "V-1", 5),
            _transfer_exception(
                90306, "000906", "154", "159", NOW - timedelta(days=5), 5
            ),
        ],
        [],
    )
    kinds = [row["kind"] for row in body["rows"]]
    assert kinds.index("release_awaiting_approval") < kinds.index(
        "transfer_awaiting_release"
    )
    assert kinds == [
        "buffer_stocked_out",
        "stockout_likely",
        "release_failed",
        "transfer_failed",
        "erp_push_failed",
        "expedite_late",
        "forecast_bias",
        "release_awaiting_approval",
        "transfer_awaiting_release",
    ]

    # --- Failure: a raising in-flight read lists "transfers" ---------
    # The whole transfers source fails: the transfer_failed rows drop
    # out with the new kind, and the other sources still answer 200
    # (the releases source keeps its in-flight release row from the
    # order scenario above).
    FakeTransferReleasesRepo.raise_on_in_flight_read = True
    body, response = _get()
    assert response.status_code == 200
    _assert_response(
        body,
        [_release_exception(900, "PO-S10-1", "V-1", 5)],
        ["transfers"],
        base_rows=NO_TRANSFER_BASE_ROWS,
    )
    assert not any(row["kind"] == "transfer_awaiting_release" for row in body["rows"])
    assert not any(row["kind"] == "transfer_failed" for row in body["rows"])
    FakeTransferReleasesRepo.raise_on_in_flight_read = False

    # --- Failure: a raising newest-rows read lists "transfers" -------
    FakeTransferReleasesRepo.raise_on_read = True
    body, response = _get()
    assert response.status_code == 200
    _assert_response(
        body,
        [_release_exception(900, "PO-S10-1", "V-1", 5)],
        ["transfers"],
        base_rows=NO_TRANSFER_BASE_ROWS,
    )
    assert "release_failed" in [row["kind"] for row in body["rows"]]
    FakeTransferReleasesRepo.raise_on_read = False

    # --- The one reads per call ---------------------------------------
    assert exceptions_gets["count"] == settings_reads["count"]
    assert FakeTransferReleasesRepo.calls == exceptions_gets["count"]
    assert FakeTransferReleasesRepo.limits == [200] * exceptions_gets["count"]
    # The in-flight read is once per call, except on the call where
    # the newest-rows read failed first and short-circuited the source.
    assert FakeTransferReleasesRepo.in_flight_calls == exceptions_gets["count"] - 1
