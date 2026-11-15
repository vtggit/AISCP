"""Issue #795 proof: GET /api/exceptions.

AC-1 — the five sources of the triage list, each read ONCE per call,
are replaced in the route module with fakes that answer canned rows
(the way the issue 719 proof replaces the ERP clients): the DDMRP
source (the shared plan code), ``stockout_ranked``, ``expedite_list``
and the two failure repositories (``plan_releases``,
``parameter_writebacks``).  Proved here:

  * mapping — every canned source row maps to the stated kind,
    severity, key (``<kind>:<ref>``), detail and view: the DDMRP
    "stocked_out"/"critical" plan rows to buffer_stocked_out (high)
    and buffer_critical (medium) with "on hand <n> (<pct>% of top of
    red)" and "#/buffers"; the stockout "likely"/"at_risk" rows to
    stockout_likely (high) and stockout_at_risk (medium) with
    "projected stockout <date>, <p>% chance" (p_stockout as a whole
    percent) and "#/queue?kind=stockout"; the expedite
    "after_stockout"/"late" lines to expedite_after_stockout (high)
    and expedite_late (medium) with "<open_units> units[,
    <days_late> days late]" (the days part only when set) and
    "#/expedite"; the "error"/"aborted" plan_releases rows to
    release_failed (high) with "release to <vendor_code or
    supplier_code> failed" (never the stored error text) and
    "#/releases"; the "failed" parameter_writebacks rows to
    erp_push_failed (high) with "push to <source_system> failed"
    (never the stored error) and "#/writeback";
  * order and counts — the high rows come first, in the kind order,
    then item_code and key, and the response counts match;
  * the 14-day window — a release error from 20 days ago, an
    aborted release one second before the exact cutoff, a "created"
    release, a "pending" release and the old push failures do not
    appear, while a row exactly AT the UTC cutoff (now minus 14
    days) does — the window is a UTC timestamp cutoff, inclusive of
    the exact cutoff, not calendar days and not the instance time
    zone;
  * no stored error text — the push failures' details never contain
    the stored error strings;
  * a failing source — a source that raises is listed in
    ``sources_failed`` while the other sources still answer, with
    status 200;
  * a DDMRP mapping failure — a DDMRP source whose read answers but
    whose row mapping raises is listed in ``sources_failed`` and its
    as-of is not reported: with the other as-of sources down too,
    the response ``as_of`` is null, not the plan's date;
  * a null ``on_hand_pct_of_red`` — the plan reports it as null when
    the top of red is 0, and the detail is "on hand <n>" without the
    percentage part;
  * unauthenticated access — no token answers 401;
  * no ERP call — the ERP client classes are patched to fail if
    constructed, and the route still answers;
  * the schema file — ``backend/openapi.json`` lists the path with
    its operation and the ExceptionRow/ExceptionCounts/
    ExceptionResponse models;
  * the identity requirement — the proof also runs as a NON-ADMIN
    (role "user"): a second pass in the same test after a full
    reload, with the page booted the production way (the token it
    would take from the URL hash as ``#access_token=...`` — the
    development token, role "user" — sent as its Bearer token),
    ``GET /api/auth/config`` answering publicly and ``GET
    /api/auth/me`` resolving the viewer, sees the triage page still
    render (200 with the same rows) while no admin-only control
    stands on it (the route is open to any signed-in user), and no
    token is still a 401.
"""

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
import app.feed.acumatica_client as acumatica_client_module
import app.feed.odoo_client as odoo_client_module
from app.main import app

URL = "/api/exceptions"
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

# The pinned current UTC time (the route's window anchor).
NOW = datetime(2026, 10, 10, 12, 0, 0, tzinfo=UTC)
CUTOFF = NOW - timedelta(days=14)  # 2026-09-26 12:00:00+00:00

# The as-of dates the successful sources report.
DDMRP_AS_OF = date(2026, 10, 9)
STOCKOUT_AS_OF = date(2026, 10, 9)
EXPEDITE_AS_OF = "2026-10-10"

RELEASE_ERROR_TEXT = (
    "Acumatica batch failed: HTTP 500 InternalServerError "
    "(request-id=8f3a2c, api-key=SECRET-795)"
)
PUSH_ERROR_TEXT = (
    "boom: the Acumatica ItemWarehouse write for ITEM-795-A at "
    "SITE-795-A failed (api-key=SECRET-795)"
)


class FakeDdmrpSource:
    """The fake for the route module's DDMRP source: answers the
    canned plan rows (the rows the DDMRP plan route builds) as of
    the canned date, once per call."""

    calls = 0

    def __call__(self):
        FakeDdmrpSource.calls += 1
        return (
            DDMRP_AS_OF,
            [
                {
                    "item_code": "ITEM-795-A",
                    "site_code": "SITE-795-A",
                    "on_hand": 0.0,
                    "on_hand_pct_of_red": 0.0,
                    "on_hand_status": "stocked_out",
                },
                {
                    "item_code": "ITEM-795-B",
                    "site_code": "SITE-795-B",
                    "on_hand": 5.0,
                    "on_hand_pct_of_red": 25.0,
                    "on_hand_status": "critical",
                },
                # The "low" and "ok" rows are not exceptions.
                {
                    "item_code": "ITEM-795-C",
                    "site_code": "SITE-795-C",
                    "on_hand": 12.0,
                    "on_hand_pct_of_red": 60.0,
                    "on_hand_status": "low",
                },
                {
                    "item_code": "ITEM-795-D",
                    "site_code": "SITE-795-D",
                    "on_hand": 40.0,
                    "on_hand_pct_of_red": 200.0,
                    "on_hand_status": "ok",
                },
                # The top of red is 0: the plan reports
                # on_hand_pct_of_red as null, and the detail keeps no
                # percentage part.
                {
                    "item_code": "ITEM-795-Z",
                    "site_code": "SITE-795-Z",
                    "on_hand": 0.0,
                    "on_hand_pct_of_red": None,
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
                "item_code": "ITEM-795-A",
                "site_code": "SITE-795-A",
                "p_stockout": 0.83,
                "projected_stockout_on": date(2026, 10, 12),
                "as_of": STOCKOUT_AS_OF,
            },
            {
                "category": "at_risk",
                "item_code": "ITEM-795-E",
                "site_code": "SITE-795-E",
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
                    "item_code": "ITEM-795-F",
                    "site_code": "SITE-795-F",
                    "open_units": 120.0,
                    "days_late": 3,
                    "reasons": ["late", "after_stockout"],
                },
                {
                    "order_nbr": "PO-2",
                    "line_nbr": 20,
                    "item_code": "ITEM-795-G",
                    "site_code": "SITE-795-G",
                    "open_units": 40.0,
                    "days_late": None,
                    "reasons": ["after_stockout"],
                },
                {
                    "order_nbr": "PO-3",
                    "line_nbr": 30,
                    "item_code": "ITEM-795-H",
                    "site_code": "SITE-795-H",
                    "open_units": 75.0,
                    "days_late": 2,
                    "reasons": ["late"],
                },
            ],
        }


class FakePlanReleasesRepo:
    """The fake for the route module's plan_releases repository.

    The canned rows span every case: the 20-days-old "error" row
    (outside the window), the "created" and "pending" rows (wrong
    outcomes), the "error" row exactly AT the UTC cutoff (in), the
    "aborted" row one second before the cutoff (out), the fresh
    "aborted" row (in), and an old "error" row whose timestamp is
    the ISO string the real repository returns.
    """

    calls = 0

    def list_newest(self):
        FakePlanReleasesRepo.calls += 1
        return [
            {
                "id": 1,
                "outcome": "error",
                "vendor_code": "V-100",
                "supplier_code": "S-100",
                "updated_at": NOW - timedelta(days=20),
                "error": RELEASE_ERROR_TEXT,
            },
            {
                "id": 2,
                "outcome": "created",
                "vendor_code": "V-200",
                "supplier_code": "S-200",
                "updated_at": NOW - timedelta(days=1),
                "error": None,
            },
            {
                "id": 3,
                "outcome": "error",
                "vendor_code": "V-999",
                "supplier_code": "S-999",
                "updated_at": CUTOFF,  # exactly the cutoff: in
                "error": RELEASE_ERROR_TEXT,
            },
            {
                "id": 4,
                "outcome": "aborted",
                "vendor_code": None,
                "supplier_code": "S-400",
                "updated_at": CUTOFF - timedelta(seconds=1),  # out
                "error": RELEASE_ERROR_TEXT,
            },
            {
                "id": 5,
                "outcome": "aborted",
                "vendor_code": None,
                "supplier_code": "S-795",
                "updated_at": NOW - timedelta(days=2),
                "error": RELEASE_ERROR_TEXT,
            },
            {
                "id": 6,
                "outcome": "pending",
                "vendor_code": None,
                "supplier_code": "S-600",
                "updated_at": NOW,
                "error": None,
            },
            {
                "id": 7,
                "outcome": "error",
                "vendor_code": None,
                "supplier_code": "S-700",
                "updated_at": "2026-09-25T00:00:00Z",  # ISO string: out
                "error": RELEASE_ERROR_TEXT,
            },
        ]


class FakeParameterWritebacksRepo:
    """The fake for the route module's parameter_writebacks
    repository: the canned audit rows, with the stored error text
    that the public details must never expose."""

    calls = 0

    def list(self, limit=100, offset=0, item_code=None, site_code=None):
        FakeParameterWritebacksRepo.calls += 1
        return [
            {
                "id": "pw-1",
                "item_code": "ITEM-795-A",
                "site_code": "SITE-795-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=3),
                "error": PUSH_ERROR_TEXT,
            },
            {
                "id": "pw-2",
                "item_code": "ITEM-795-B",
                "site_code": "SITE-795-B",
                "source_system": "odoo",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=20),  # out
                "error": PUSH_ERROR_TEXT,
            },
            {
                "id": "pw-3",
                "item_code": "ITEM-795-C",
                "site_code": "SITE-795-C",
                "source_system": "odoo",
                "outcome": "written",
                "written_at": NOW - timedelta(days=1),
                "error": None,
            },
            {
                "id": "pw-4",
                "item_code": "ITEM-795-D",
                "site_code": "SITE-795-D",
                "source_system": "odoo",
                "outcome": "failed",
                # The ISO string spelling, exactly at the cutoff: in
                "written_at": CUTOFF.isoformat(),
                "error": "Odoo: 500 internal (trace=abc795)",
            },
            {
                "id": "pw-5",
                "item_code": "ITEM-795-E",
                "site_code": "SITE-795-E",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": CUTOFF - timedelta(seconds=1),  # out
                "error": PUSH_ERROR_TEXT,
            },
        ]


def _expected_rows():
    """The full expected triage list, in order."""
    rows = [
        {
            "kind": "buffer_stocked_out",
            "severity": "high",
            "key": "buffer_stocked_out:ITEM-795-A/SITE-795-A",
            "item_code": "ITEM-795-A",
            "site_code": "SITE-795-A",
            "title": "Buffer stocked out",
            "detail": "on hand 0 (0% of top of red)",
            "view": "#/buffers",
            "detected_on": DDMRP_AS_OF.isoformat(),
        },
        {
            "kind": "buffer_stocked_out",
            "severity": "high",
            "key": "buffer_stocked_out:ITEM-795-Z/SITE-795-Z",
            "item_code": "ITEM-795-Z",
            "site_code": "SITE-795-Z",
            "title": "Buffer stocked out",
            "detail": "on hand 0",
            "view": "#/buffers",
            "detected_on": DDMRP_AS_OF.isoformat(),
        },
        {
            "kind": "stockout_likely",
            "severity": "high",
            "key": "stockout_likely:ITEM-795-A/SITE-795-A",
            "item_code": "ITEM-795-A",
            "site_code": "SITE-795-A",
            "title": "Stockout likely",
            "detail": "projected stockout 2026-10-12, 83% chance",
            "view": "#/queue?kind=stockout",
            "detected_on": STOCKOUT_AS_OF.isoformat(),
        },
        {
            "kind": "expedite_after_stockout",
            "severity": "high",
            "key": "expedite_after_stockout:PO-1/10",
            "item_code": "ITEM-795-F",
            "site_code": "SITE-795-F",
            "title": "Expedite after stockout",
            "detail": "120 units, 3 days late",
            "view": "#/expedite",
            "detected_on": EXPEDITE_AS_OF,
        },
        {
            "kind": "expedite_after_stockout",
            "severity": "high",
            "key": "expedite_after_stockout:PO-2/20",
            "item_code": "ITEM-795-G",
            "site_code": "SITE-795-G",
            "title": "Expedite after stockout",
            "detail": "40 units",
            "view": "#/expedite",
            "detected_on": EXPEDITE_AS_OF,
        },
        {
            "kind": "release_failed",
            "severity": "high",
            "key": "release_failed:3",
            "item_code": None,
            "site_code": None,
            "title": "Release failed",
            "detail": "release to V-999 failed",
            "view": "#/releases",
            "detected_on": CUTOFF.date().isoformat(),
        },
        {
            "kind": "release_failed",
            "severity": "high",
            "key": "release_failed:5",
            "item_code": None,
            "site_code": None,
            "title": "Release failed",
            "detail": "release to S-795 failed",
            "view": "#/releases",
            "detected_on": (NOW - timedelta(days=2)).date().isoformat(),
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
            "detected_on": (NOW - timedelta(days=3)).date().isoformat(),
        },
        {
            "kind": "erp_push_failed",
            "severity": "high",
            "key": "erp_push_failed:pw-4",
            "item_code": None,
            "site_code": None,
            "title": "ERP push failed",
            "detail": "push to odoo failed",
            "view": "#/writeback",
            "detected_on": CUTOFF.date().isoformat(),
        },
        {
            "kind": "buffer_critical",
            "severity": "medium",
            "key": "buffer_critical:ITEM-795-B/SITE-795-B",
            "item_code": "ITEM-795-B",
            "site_code": "SITE-795-B",
            "title": "Buffer critical",
            "detail": "on hand 5 (25% of top of red)",
            "view": "#/buffers",
            "detected_on": DDMRP_AS_OF.isoformat(),
        },
        {
            "kind": "stockout_at_risk",
            "severity": "medium",
            "key": "stockout_at_risk:ITEM-795-E/SITE-795-E",
            "item_code": "ITEM-795-E",
            "site_code": "SITE-795-E",
            "title": "Stockout at risk",
            "detail": "projected stockout 2026-10-20, 45% chance",
            "view": "#/queue?kind=stockout",
            "detected_on": STOCKOUT_AS_OF.isoformat(),
        },
        {
            "kind": "expedite_late",
            "severity": "medium",
            "key": "expedite_late:PO-3/30",
            "item_code": "ITEM-795-H",
            "site_code": "SITE-795-H",
            "title": "Expedite late",
            "detail": "75 units, 2 days late",
            "view": "#/expedite",
            "detected_on": EXPEDITE_AS_OF,
        },
    ]
    # The issue #796 acknowledgement fields: every row reads
    # unacknowledged while no acknowledgement is stored.
    for row in rows:
        row["acknowledged"] = False
        row["acknowledged_by_name"] = None
        row["acknowledged_at"] = None
        row["snooze_until"] = None
        # The issue #809 first-seen fields: nothing is ever captured
        # by this proof, so every key reads never captured.
        row["first_seen_on"] = None
        row["new"] = False
    return rows


def _assert_response(body, expected_rows, sources_failed, as_of):
    assert set(body) == {"as_of", "counts", "rows", "sources_failed"}
    assert body["as_of"] == as_of
    assert body["counts"] == {
        "high": sum(1 for row in expected_rows if row["severity"] == "high"),
        "medium": sum(1 for row in expected_rows if row["severity"] == "medium"),
        # The issue #796 acknowledgement count: nothing is stored.
        "acknowledged": 0,
        # The issue #809 first-seen count: nothing is captured.
        "new": 0,
    }
    assert body["sources_failed"] == sources_failed
    assert body["rows"] == expected_rows


def test_issue795_freeform(monkeypatch, client):
    # The five sources in the route module are replaced with fakes
    # that answer canned rows, the way the issue 719 proof replaces
    # the ERP clients.  Each fake records its calls: every source
    # must be read exactly once per call.
    FakeDdmrpSource.calls = 0
    FakeStockoutSource.calls = 0
    FakeExpediteSource.calls = 0
    FakePlanReleasesRepo.calls = 0
    FakeParameterWritebacksRepo.calls = 0
    monkeypatch.setattr(exceptions, "_ddmrp_source", FakeDdmrpSource())
    monkeypatch.setattr(exceptions, "stockout_ranked", FakeStockoutSource())
    monkeypatch.setattr(exceptions, "expedite_list", FakeExpediteSource())
    monkeypatch.setattr(
        exceptions,
        "realised_accuracy",
        lambda **kwargs: {"as_of": None, "item_sites": []},
    )
    monkeypatch.setattr(exceptions, "_plan_releases_repo", FakePlanReleasesRepo())
    monkeypatch.setattr(
        exceptions, "_parameter_writebacks_repo", FakeParameterWritebacksRepo()
    )
    # The window anchor: the route's "current UTC time".
    monkeypatch.setattr(exceptions, "_utcnow", lambda: NOW)

    # No ERP call: the client classes fail if constructed.
    def _no_client(self, *args, **kwargs):
        raise AssertionError("the exceptions route must not call an ERP")

    monkeypatch.setattr(acumatica_client_module.AcumaticaClient, "__init__", _no_client)
    monkeypatch.setattr(odoo_client_module.OdooClient, "__init__", _no_client)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    anon = TestClient(app)

    # --- Unauthenticated access: no token answers 401. --------------
    assert anon.get(URL).status_code == 401

    # --- The full triage list: mapping, order, counts, window. -----
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    _assert_response(body, _expected_rows(), [], EXPEDITE_AS_OF)

    # The stored error text never reaches a public detail.
    for row in body["rows"]:
        assert RELEASE_ERROR_TEXT not in row["detail"]
        assert PUSH_ERROR_TEXT not in row["detail"]
        assert "SECRET-795" not in row["detail"]
        assert "trace=abc795" not in row["detail"]

    # Each source was read exactly once per call.
    assert FakeDdmrpSource.calls == 1
    assert FakeStockoutSource.calls == 1
    assert FakeExpediteSource.calls == 1
    assert FakePlanReleasesRepo.calls == 1
    assert FakeParameterWritebacksRepo.calls == 1

    # A second call reads each source exactly once more.
    again = admin.get(URL)
    assert again.status_code == 200
    assert again.json() == body
    assert FakeDdmrpSource.calls == 2
    assert FakeStockoutSource.calls == 2
    assert FakeExpediteSource.calls == 2
    assert FakePlanReleasesRepo.calls == 2
    assert FakeParameterWritebacksRepo.calls == 2

    # --- A failing source: listed, the others still answer. --------
    def _stockout_down(as_of=None):
        raise RuntimeError("the stockout feed is down")

    monkeypatch.setattr(exceptions, "stockout_ranked", _stockout_down)
    failed = admin.get(URL)
    assert failed.status_code == 200
    failed_body = failed.json()
    expected_without_stockout = [
        row for row in _expected_rows() if not row["kind"].startswith("stockout_")
    ]
    _assert_response(
        failed_body, expected_without_stockout, ["stockout"], EXPEDITE_AS_OF
    )

    monkeypatch.setattr(exceptions, "stockout_ranked", FakeStockoutSource())

    def _ddmrp_down():
        raise RuntimeError("the DDMRP plan is down")

    monkeypatch.setattr(exceptions, "_ddmrp_source", _ddmrp_down)
    failed_ddmrp = admin.get(URL)
    assert failed_ddmrp.status_code == 200
    failed_ddmrp_body = failed_ddmrp.json()
    expected_without_ddmrp = [
        row for row in _expected_rows() if not row["kind"].startswith("buffer_")
    ]
    _assert_response(
        failed_ddmrp_body,
        expected_without_ddmrp,
        ["ddmrp"],
        # The latest as-of the remaining successful sources report.
        EXPEDITE_AS_OF,
    )
    monkeypatch.setattr(exceptions, "_ddmrp_source", FakeDdmrpSource())

    # --- A DDMRP mapping failure fails the whole source: the read
    # answered, the mapping raised — the source is listed in
    # sources_failed and its as-of is not reported.  With the other
    # as-of sources (stockout, expedite) down too, the response
    # as_of is null, not the plan's date.
    real_ddmrp_rows = exceptions._ddmrp_rows

    def _ddmrp_mapping_down(as_of, plan_rows):
        raise RuntimeError("the DDMRP row mapping is down")

    def _expedite_down(as_of=None):
        raise RuntimeError("the expedite feed is down")

    monkeypatch.setattr(exceptions, "_ddmrp_rows", _ddmrp_mapping_down)
    monkeypatch.setattr(exceptions, "stockout_ranked", _stockout_down)
    monkeypatch.setattr(exceptions, "expedite_list", _expedite_down)
    mapping_failed = admin.get(URL)
    assert mapping_failed.status_code == 200
    mapping_body = mapping_failed.json()
    _assert_response(
        mapping_body,
        [
            row
            for row in _expected_rows()
            if row["kind"] in ("release_failed", "erp_push_failed")
        ],
        ["ddmrp", "stockout", "expedite"],
        # The DDMRP as-of is not reported: its source failed.
        None,
    )
    monkeypatch.setattr(exceptions, "_ddmrp_rows", real_ddmrp_rows)
    monkeypatch.setattr(exceptions, "stockout_ranked", FakeStockoutSource())
    monkeypatch.setattr(exceptions, "expedite_list", FakeExpediteSource())

    # --- The schema file lists the route and its models. -----------
    schema = json.loads(_OPENAPI.read_text())
    assert "/api/exceptions" in schema["paths"]
    assert "get" in schema["paths"]["/api/exceptions"]
    for model in ("ExceptionRow", "ExceptionCounts", "ExceptionResponse"):
        assert model in schema["components"]["schemas"]

    # --- Identity: a second pass after a full reload, as the
    # NON-ADMIN (role "user").  The page is booted the production
    # way: the token it would take from the URL hash as
    # #access_token=... is the development token (role "user") sent
    # as its Bearer token; GET /api/auth/config answers publicly and
    # GET /api/auth/me resolves the viewer.
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon_reloaded = TestClient(app)
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is True
    assert "user" in me.json()["user"]["roles"]
    assert "admin" not in me.json()["user"]["roles"]

    # The triage page still renders for the viewer — the route is
    # open to any signed-in user, so no admin-only control stands on
    # it — with the same rows, and no token is still a 401.
    viewer_response = viewer.get(URL)
    assert viewer_response.status_code == 200, viewer_response.text
    assert viewer_response.json() == body
    assert anon_reloaded.get(URL).status_code == 401
