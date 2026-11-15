"""Issue #899 proof: the "transfer_failed" exception kind.

AC-1 — a new source named "transfers" in ``GET /api/exceptions``
reads the ``transfer_releases`` rows once per call through
``TransferReleasePostgresRepository.list_newest(limit=200)``; a
failure of the read lists "transfers" in ``sources_failed`` and the
other sources still answer, with status 200.  Each row whose outcome
is "error" or "aborted" and whose ``updated_at`` falls within the
same 14-day UTC window the other failure kinds use (the exact
cutoff included) yields one exception: kind "transfer_failed"
(high), key "transfer_failed:<row id>", ``item_code`` and
``site_code`` null, title "Transfer failed", detail "transfer
<from_site_code> → <to_site_code> failed" (never the stored error
text), view "#/orders", ``detected_on`` the date of ``updated_at``.
In the kind order, "transfer_failed" stands directly after
"release_failed" and before "erp_push_failed".

AC-2 — proved in one test, the seven sources of the triage list
replaced in the route module with fakes the way the issue 865 proof
does (and the transfer releases repository faked for its
``list_newest`` read):

  * flagged — an "error" row from 154 to 159, updated a day ago,
    yields one "transfer_failed" row (severity "high", detail
    "transfer 154 → 159 failed", view "#/orders") and the stored
    error text appears nowhere in the response;
  * aborted counts too — an "aborted" row yields the kind as well;
  * window — a row one second older than the 14-day cutoff is left
    out, and a row exactly at the cutoff is included;
  * not failed — rows "created", "exists" and "pending" yield
    nothing;
  * order — the row sorts after a "release_failed" row and before
    an "erp_push_failed" row;
  * failure — a transfer read that raises lists "transfers" in
    ``sources_failed`` and still answers 200 with the other
    sources' rows.
"""

from datetime import UTC, date, datetime, timedelta

import app.api.exceptions as exceptions
import app.feed.acumatica_client as acumatica_client_module
import app.feed.odoo_client as odoo_client_module

URL = "/api/exceptions"

# The pinned current UTC time (the route's window anchor).
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)

# The 14-day UTC cutoff the failure window compares against
# (inclusive of the exact cutoff).
CUTOFF = NOW - timedelta(days=14)

# The as-of dates the successful sources report: the expedite's is
# the newest, so it is the response as_of (the forecast bias and the
# failure kinds report none, their dates are the rows' detected_on).
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
                    "item_code": "ITEM-899-A",
                    "site_code": "SITE-899-A",
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
                "item_code": "ITEM-899-A",
                "site_code": "SITE-899-A",
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
                    "item_code": "ITEM-899-C",
                    "site_code": "SITE-899-C",
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
                "item_code": "ITEM-899-A",
                "site_code": "SITE-899-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=2),
                "error": "boom: the Acumatica write failed (api-key=SECRET-899-PW)",
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
                    "item_code": "ITEM-899-B",
                    "site_code": "SITE-899-B",
                    "method": "moving_average",
                    "compared_buckets": 120,
                    "effective_bias": 0.84,
                },
            ],
        }


class FakeTransferReleasesRepo:
    """The fake for the route module's transfer releases repository:
    the scenarios set ``rows`` (the canned transfer_releases rows),
    the read raises while ``raise_on_read`` is true, and every call
    records the limit it was called with."""

    rows: list = []
    calls = 0
    raise_on_read = False
    limits: list = []

    def list_newest(self, limit=50):
        FakeTransferReleasesRepo.calls += 1
        FakeTransferReleasesRepo.limits.append(limit)
        if FakeTransferReleasesRepo.raise_on_read:
            raise RuntimeError("the transfer_releases read is down")
        return list(FakeTransferReleasesRepo.rows)


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
# window, so it keeps answering release_failed.
FAILED_ROW = _release_row(901, "error", None, "V-100", 1, "S-100")


def _transfer_row(row_id, outcome, from_site, to_site, updated_at, error=None):
    """One canned transfer_releases row, updated at ``updated_at``."""
    return {
        "id": row_id,
        "origin": "supply_plan",
        "from_site_code": from_site,
        "to_site_code": to_site,
        "lines": [],
        "line_count": 0,
        "outcome": outcome,
        "reference_nbr": None,
        "erp_status": None,
        "error": error,
        "created_at": updated_at,
        "updated_at": updated_at,
    }


def _transfer_exception(row_id, from_site, to_site, updated_at) -> dict:
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


# The six rows the other sources always answer, in the response's
# order (high before medium, then kind order): the transfer_failed
# rows slot in between the release_failed and the erp_push_failed
# rows.
BUFFER_ROW = {
    "kind": "buffer_stocked_out",
    "severity": "high",
    "key": "buffer_stocked_out:ITEM-899-A/SITE-899-A",
    "item_code": "ITEM-899-A",
    "site_code": "SITE-899-A",
    "title": "Buffer stocked out",
    "detail": "on hand 0 (0% of top of red)",
    "view": "#/buffers",
    "detected_on": DDMRP_AS_OF.isoformat(),
}
STOCKOUT_ROW = {
    "kind": "stockout_likely",
    "severity": "high",
    "key": "stockout_likely:ITEM-899-A/SITE-899-A",
    "item_code": "ITEM-899-A",
    "site_code": "SITE-899-A",
    "title": "Stockout likely",
    "detail": "projected stockout 2026-10-12, 83% chance",
    "view": "#/queue?kind=stockout",
    "detected_on": STOCKOUT_AS_OF.isoformat(),
}
RELEASE_FAILED_ROW = {
    "kind": "release_failed",
    "severity": "high",
    "key": "release_failed:901",
    "item_code": None,
    "site_code": None,
    "title": "Release failed",
    "detail": "release to V-100 failed",
    "view": "#/releases",
    "detected_on": (NOW - timedelta(days=1)).date().isoformat(),
}
ERP_PUSH_ROW = {
    "kind": "erp_push_failed",
    "severity": "high",
    "key": "erp_push_failed:pw-1",
    "item_code": None,
    "site_code": None,
    "title": "ERP push failed",
    "detail": "push to acumatica failed",
    "view": "#/writeback",
    "detected_on": (NOW - timedelta(days=2)).date().isoformat(),
}
EXPEDITE_ROW = {
    "kind": "expedite_late",
    "severity": "medium",
    "key": "expedite_late:PO-1/10",
    "item_code": "ITEM-899-C",
    "site_code": "SITE-899-C",
    "title": "Expedite late",
    "detail": "120 units, 3 days late",
    "view": "#/expedite",
    "detected_on": EXPEDITE_AS_OF,
}
FORECAST_ROW = {
    "kind": "forecast_bias",
    "severity": "medium",
    "key": "forecast_bias:ITEM-899-B/SITE-899-B",
    "item_code": "ITEM-899-B",
    "site_code": "SITE-899-B",
    "title": "Forecast bias",
    "detail": "forecast over actual by 84% across 120 periods",
    "view": "#/demand",
    "detected_on": FORECAST_AS_OF.isoformat(),
}
BASE_ROWS = [
    BUFFER_ROW,
    STOCKOUT_ROW,
    RELEASE_FAILED_ROW,
    ERP_PUSH_ROW,
    EXPEDITE_ROW,
    FORECAST_ROW,
]


def _expected_rows(transfer_rows) -> list[dict]:
    """The full expected triage list: the base rows plus the
    transfer_failed rows, which sort after release_failed and before
    erp_push_failed."""
    return BASE_ROWS[:3] + list(transfer_rows) + BASE_ROWS[3:]


def _assert_response(body, transfer_rows, sources_failed):
    """The full expected triage list: the six base rows plus the
    transfer_failed rows (in their kind-order slot), in order."""
    expected = _expected_rows(transfer_rows)
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


def test_issue899_freeform(monkeypatch, client):
    # The seven sources in the route module are replaced with fakes
    # that answer canned rows, the way the issue 865 proof does.
    # Every fake records its calls.
    FakeDdmrpSource.calls = 0
    FakeStockoutSource.calls = 0
    FakeExpediteSource.calls = 0
    FakePlanReleasesRepo.rows = [FAILED_ROW]
    FakePlanReleasesRepo.calls = 0
    FakePlanReleasesRepo.raise_on_read = False
    FakeOpenPurchaseRepo.rows = []
    FakeOpenPurchaseRepo.calls = 0
    FakeOpenPurchaseRepo.raise_on_read = False
    FakeParameterWritebacksRepo.calls = 0
    FakeRealisedAccuracy.calls = 0
    FakeTransferReleasesRepo.rows = []
    FakeTransferReleasesRepo.calls = 0
    FakeTransferReleasesRepo.raise_on_read = False
    FakeTransferReleasesRepo.limits = []
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

    # The GETs run as the signed-in client (the route is open to any
    # signed-in user).
    exceptions_gets = {"count": 0}

    def _get():
        exceptions_gets["count"] += 1
        response = client.get(URL)
        assert response.status_code == 200, response.text
        return response.json(), response

    # --- Flagged: an "error" row 154 -> 159, updated a day ago --------
    error_text = "boom: the Acumatica batch failed (api-key=SECRET-899)"
    flagged_source_row = _transfer_row(
        89901, "error", "154", "159", NOW - timedelta(days=1), error_text
    )
    FakeTransferReleasesRepo.rows = [flagged_source_row]
    body, response = _get()
    flagged = _transfer_exception(89901, "154", "159", NOW - timedelta(days=1))
    _assert_response(body, [flagged], [])
    assert flagged["severity"] == "high"
    assert flagged["detail"] == "transfer 154 → 159 failed"
    assert flagged["view"] == "#/orders"
    assert flagged["detected_on"] == (NOW - timedelta(days=1)).date().isoformat()
    # The stored error text appears nowhere in the response.
    assert error_text not in response.text
    assert "SECRET-899" not in response.text
    # The one read per call, with the 200-row limit.
    assert FakeTransferReleasesRepo.calls == 1
    assert FakeTransferReleasesRepo.limits == [200]

    # --- Aborted counts too --------------------------------------------
    aborted_error = "boom: the batch aborted (api-key=SECRET-899-A)"
    FakeTransferReleasesRepo.rows = [
        _transfer_row(
            89902, "aborted", "154", "160", NOW - timedelta(days=1), aborted_error
        )
    ]
    body, response = _get()
    _assert_response(
        body,
        [_transfer_exception(89902, "154", "160", NOW - timedelta(days=1))],
        [],
    )
    assert "SECRET-899-A" not in response.text

    # --- Window: one second older than the cutoff is left out ---------
    FakeTransferReleasesRepo.rows = [
        _transfer_row(89903, "error", "154", "159", CUTOFF - timedelta(seconds=1))
    ]
    body, _ = _get()
    _assert_response(body, [], [])
    assert not any(row["kind"] == "transfer_failed" for row in body["rows"])

    # --- Window: exactly at the cutoff is included --------------------
    at_cutoff = _transfer_row(89904, "error", "154", "159", CUTOFF)
    FakeTransferReleasesRepo.rows = [at_cutoff]
    body, _ = _get()
    _assert_response(body, [_transfer_exception(89904, "154", "159", CUTOFF)], [])
    at_row = next(row for row in body["rows"] if row["kind"] == "transfer_failed")
    assert at_row["detected_on"] == CUTOFF.date().isoformat()

    # --- Not failed: created, exists and pending yield nothing --------
    FakeTransferReleasesRepo.rows = [
        _transfer_row(89905, "created", "154", "159", NOW - timedelta(days=1)),
        _transfer_row(89906, "exists", "154", "159", NOW - timedelta(days=1)),
        _transfer_row(89907, "pending", "154", "159", NOW - timedelta(days=1)),
    ]
    body, _ = _get()
    _assert_response(body, [], [])
    assert not any(row["kind"] == "transfer_failed" for row in body["rows"])

    # --- Order: after release_failed, before erp_push_failed ----------
    order = exceptions._KIND_ORDER
    assert (
        order["release_failed"] + 1 == order["transfer_failed"]
        and order["transfer_failed"] + 1 == order["erp_push_failed"]
    )
    FakeTransferReleasesRepo.rows = [flagged_source_row]
    body, _ = _get()
    kinds = [row["kind"] for row in body["rows"]]
    assert (
        kinds.index("release_failed")
        < kinds.index("transfer_failed")
        < kinds.index("erp_push_failed")
    )
    assert kinds == [
        "buffer_stocked_out",
        "stockout_likely",
        "release_failed",
        "transfer_failed",
        "erp_push_failed",
        "expedite_late",
        "forecast_bias",
    ]

    # --- Failure: a raising transfer read lists "transfers" -----------
    # The other sources still answer 200 with their rows; only the
    # transfer_failed rows drop out.
    FakeTransferReleasesRepo.rows = [flagged_source_row]
    FakeTransferReleasesRepo.raise_on_read = True
    body, response = _get()
    assert response.status_code == 200
    _assert_response(body, [], ["transfers"])
    kinds = [row["kind"] for row in body["rows"]]
    assert "release_failed" in kinds
    assert "erp_push_failed" in kinds
    assert not any(row["kind"] == "transfer_failed" for row in body["rows"])
    FakeTransferReleasesRepo.raise_on_read = False

    # --- The one read per call, always with the 200-row limit ---------
    assert FakeTransferReleasesRepo.calls == exceptions_gets["count"]
    assert FakeTransferReleasesRepo.limits == [200] * exceptions_gets["count"]
