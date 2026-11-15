"""Issue #955 proof: the "sop_signoff_due" exception kind.

AC-1 — the ``exceptions.sop_signoff_day`` system setting (scope
"system", type "number", default 10, min 1, max 28) is registered the
way ``exceptions.release_approval_days`` is, and the new "sop" source
of ``GET /api/exceptions`` reads the ``sop_signoffs`` rows once per
call — ``SopSignoffsPostgresRepository.list_newest(limit=1)`` (the
newest sign-off of any month) and
``SopSignoffsPostgresRepository.latest_for_month(<the current
month>)`` (the "YYYY-MM" of the current server date, the review month
``GET /api/sop/review`` uses), once each.  One row, kind
"sop_signoff_due" (medium), key "sop_signoff_due:<month>",
``item_code`` and ``site_code`` null, title "S&OP sign-off due",
detail "<month> has not been signed off. The last sign-off was for
<the newest row's month> on <the date of its created_at>.", view
"#/sop", ``detected_on`` the date in the current month whose day is
the setting, is yielded only when at least one sign-off of any month
exists, the current month has none and the current server date's day
of the month is at least the setting — read from the stored system
settings the source loop already holds, a missing or out-of-range
stored value falling back to the default, as the other settings do.
In the kind order the row stands directly after "erp_push_failed", as
the last kind.  A failure of either read lists "sop" in
``sources_failed`` and the other sources still answer, with status
200.  The seven other sources of the triage list are replaced in the
route module with fakes the way the issue 822 proof does (the DDMRP
source, ``stockout_ranked``, ``expedite_list``, the
``plan_releases``, ``parameter_writebacks`` and open-purchase
repositories, the transfer releases repository and
``realised_accuracy``), and the sign-off repository is replaced with
a fake holding rows.

AC-2 — proved in one test, the current server date pinned by
replacing the ``date`` the module uses (so the result never depends
on the day the test runs):

  * flagged — on 2026-11-15, with one sign-off for "2026-10" created
    2026-10-05, one row appears: kind "sop_signoff_due", severity
    "medium", key "sop_signoff_due:2026-11", detail "2026-11 has not
    been signed off. The last sign-off was for 2026-10 on
    2026-10-05.", view "#/sop", ``detected_on`` "2026-11-10";
  * the exact day — on 2026-11-10 the row appears, on 2026-11-09 it
    does not;
  * signed off — with a sign-off for "2026-11" stored too, no row
    appears;
  * never signed off — with no sign-offs at all, on 2026-11-28, no
    row appears;
  * the setting — with the setting stored as 3, on 2026-11-03 the
    row appears with ``detected_on`` "2026-11-03"; a stored value of
    40 (written straight to the settings table, past the max 28)
    falls back to the default of 10; ``GET /api/settings`` lists
    ``exceptions.sop_signoff_day`` with default 10;
  * the fractional setting — with the setting stored as 10.5, the
    due-day comparison is made against the stored value, never a
    truncated whole day: on 2026-11-10 no row appears, on
    2026-11-11 the row appears with ``detected_on`` "2026-11-11",
    the first due day;
  * order — the row sorts after the "erp_push_failed" row, as the
    last kind;
  * failure — a repository whose read raises lists "sop" in
    ``sources_failed``, still answers 200 and leaves the other rows
    in place;
  * reads — one call makes one ``list_newest(limit=1)`` and one
    ``latest_for_month`` of the current month, and the stored
    settings read stays one per call (the source adds no read of its
    own);
  * writes nothing — the fake repository's ``create`` is never
    called and no ERP client is constructed.
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
SOP_DAY_KEY = "exceptions.sop_signoff_day"


class PinnedDate(date):
    """The fake for the route module's ``date``: ``today()`` answers
    the pinned current server date, set per scenario on ``fixed``
    (the module's other uses of ``date`` — ``fromisoformat`` and
    the constructors — inherit the real behaviour)."""

    fixed: date | None = None

    @classmethod
    def today(cls) -> date:
        return cls.fixed


# The pinned current UTC time (the route's failure-window anchor).
NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)

# The as-of dates the successful sources report: the expedite's is
# the newest, so it is the response as_of (the S&OP sign-off due,
# the failure kinds and the forecast bias report none, their dates
# are the rows' detected_on).  The canned values are PinnedDate
# instances so the module's (faked) ``isinstance(value, date)``
# checks recognise them.
DDMRP_AS_OF = PinnedDate(2026, 11, 9)
STOCKOUT_AS_OF = PinnedDate(2026, 11, 9)
STOCKOUT_ON = PinnedDate(2026, 11, 12)
EXPEDITE_AS_OF = "2026-11-10"
FORECAST_AS_OF = PinnedDate(2026, 11, 15)


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
                    "item_code": "ITEM-955-A",
                    "site_code": "SITE-955-A",
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
                "item_code": "ITEM-955-A",
                "site_code": "SITE-955-A",
                "p_stockout": 0.83,
                "projected_stockout_on": STOCKOUT_ON,
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
                    "item_code": "ITEM-955-C",
                    "site_code": "SITE-955-C",
                    "open_units": 120.0,
                    "days_late": 3,
                    "reasons": ["late"],
                },
            ],
        }


class FakePlanReleasesRepo:
    """The fake for the route module's plan_releases repository: no
    rows — the other kinds are not the point of this proof."""

    calls = 0

    def list_newest(self):
        FakePlanReleasesRepo.calls += 1
        return []


class FakeOpenPurchaseRepo:
    """The fake for the route module's open-purchase repository."""

    calls = 0

    def list_latest(self):
        FakeOpenPurchaseRepo.calls += 1
        return []


class FakeTransferReleasesRepo:
    """The fake for the route module's transfer releases repository:
    no rows for either of its reads."""

    calls = 0
    in_flight_calls = 0

    def list_newest(self, limit=50):
        FakeTransferReleasesRepo.calls += 1
        return []

    def list_in_flight(self):
        FakeTransferReleasesRepo.in_flight_calls += 1
        return []


class FakeParameterWritebacksRepo:
    """The fake for the route module's parameter_writebacks
    repository: one failed push, within the 14-day window."""

    calls = 0

    def list(self, limit=100, offset=0, item_code=None, site_code=None):
        FakeParameterWritebacksRepo.calls += 1
        return [
            {
                "id": "pw-1",
                "item_code": "ITEM-955-A",
                "site_code": "SITE-955-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=2),
                "error": "boom: the Acumatica write failed (api-key=SECRET-955)",
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
                    "item_code": "ITEM-955-B",
                    "site_code": "SITE-955-B",
                    "method": "moving_average",
                    "compared_buckets": 120,
                    "effective_bias": 0.84,
                },
            ],
        }


class FakeSopSignoffsRepo:
    """The fake for the route module's sop_signoffs repository: the
    scenarios set ``newest_rows`` (the any-month read's rows) and
    ``current_month_row`` (the current-month read's row or None),
    each read raises while its flag is true, every ``list_newest``
    call records the limit it was called with and every
    ``latest_for_month`` call its month, and ``create`` counts its
    calls (it must stay zero — the route writes nothing)."""

    newest_rows: list = []
    current_month_row: dict | None = None
    list_newest_calls = 0
    latest_for_month_calls = 0
    limits: list = []
    months: list = []
    raise_on_list_newest = False
    raise_on_latest_for_month = False
    create_calls = 0

    def list_newest(self, month=None, limit=50):
        FakeSopSignoffsRepo.list_newest_calls += 1
        FakeSopSignoffsRepo.limits.append(limit)
        if FakeSopSignoffsRepo.raise_on_list_newest:
            raise RuntimeError("the sop_signoffs read is down")
        return list(FakeSopSignoffsRepo.newest_rows)

    def latest_for_month(self, month):
        FakeSopSignoffsRepo.latest_for_month_calls += 1
        FakeSopSignoffsRepo.months.append(month)
        if FakeSopSignoffsRepo.raise_on_latest_for_month:
            raise RuntimeError("the current-month sign-off read is down")
        return FakeSopSignoffsRepo.current_month_row

    def create(self, **kwargs):
        FakeSopSignoffsRepo.create_calls += 1
        raise AssertionError("the exceptions route must not write sign-offs")


# The stored sign-off rows the scenarios hold: one for the previous
# month (created 2026-10-05) and one for the current month.
SIGNOFF_ROW = {
    "id": 1,
    "month": "2026-10",
    "note": None,
    "actor": "dev-admin-1",
    "actor_name": "Dev Admin",
    "created_at": "2026-10-05T09:30:00+00:00",
}
SIGNOFF_ROW_NOV = {
    "id": 2,
    "month": "2026-11",
    "note": None,
    "actor": "dev-admin-1",
    "actor_name": "Dev Admin",
    "created_at": "2026-11-02T10:00:00+00:00",
}


def _sop_row(detected_on: str) -> dict:
    """The expected sop_signoff_due row for the pinned current month
    (2026-11), off the one stored sign-off of 2026-10."""
    return {
        "kind": "sop_signoff_due",
        "severity": "medium",
        "key": "sop_signoff_due:2026-11",
        "item_code": None,
        "site_code": None,
        "title": "S&OP sign-off due",
        "detail": (
            "2026-11 has not been signed off. "
            "The last sign-off was for 2026-10 on 2026-10-05."
        ),
        "view": "#/sop",
        "detected_on": detected_on,
    }


# The five rows the other sources always answer, in the response's
# order (high before medium, then kind order): the S&OP sign-off
# due, when it appears, stands directly after the erp_push_failed
# row, as the last kind.
BASE_ROWS = [
    {
        "kind": "buffer_stocked_out",
        "severity": "high",
        "key": "buffer_stocked_out:ITEM-955-A/SITE-955-A",
        "item_code": "ITEM-955-A",
        "site_code": "SITE-955-A",
        "title": "Buffer stocked out",
        "detail": "on hand 0 (0% of top of red)",
        "view": "#/buffers",
        "detected_on": DDMRP_AS_OF.isoformat(),
    },
    {
        "kind": "stockout_likely",
        "severity": "high",
        "key": "stockout_likely:ITEM-955-A/SITE-955-A",
        "item_code": "ITEM-955-A",
        "site_code": "SITE-955-A",
        "title": "Stockout likely",
        "detail": "projected stockout 2026-11-12, 83% chance",
        "view": "#/queue?kind=stockout",
        "detected_on": STOCKOUT_AS_OF.isoformat(),
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
        "item_code": "ITEM-955-C",
        "site_code": "SITE-955-C",
        "title": "Expedite late",
        "detail": "120 units, 3 days late",
        "view": "#/expedite",
        "detected_on": EXPEDITE_AS_OF,
    },
    {
        "kind": "forecast_bias",
        "severity": "medium",
        "key": "forecast_bias:ITEM-955-B/SITE-955-B",
        "item_code": "ITEM-955-B",
        "site_code": "SITE-955-B",
        "title": "Forecast bias",
        "detail": "forecast over actual by 84% across 120 periods",
        "view": "#/demand",
        "detected_on": FORECAST_AS_OF.isoformat(),
    },
]


def _assert_response(body, sop_rows, sources_failed):
    """The full expected triage list: the five canned rows plus the
    S&OP sign-off due row the scenarios yield, in order."""
    expected = BASE_ROWS + sop_rows
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


def test_issue955_freeform(monkeypatch, client, admin_token):
    # The eight sources in the route module are replaced with fakes
    # that answer canned rows, the way the issue 822 proof does, and
    # the sign-off repository is replaced with a fake holding rows.
    # Every fake records its calls.
    FakeDdmrpSource.calls = 0
    FakeStockoutSource.calls = 0
    FakeExpediteSource.calls = 0
    FakePlanReleasesRepo.calls = 0
    FakeOpenPurchaseRepo.calls = 0
    FakeTransferReleasesRepo.calls = 0
    FakeTransferReleasesRepo.in_flight_calls = 0
    FakeParameterWritebacksRepo.calls = 0
    FakeRealisedAccuracy.calls = 0
    FakeSopSignoffsRepo.newest_rows = []
    FakeSopSignoffsRepo.current_month_row = None
    FakeSopSignoffsRepo.list_newest_calls = 0
    FakeSopSignoffsRepo.latest_for_month_calls = 0
    FakeSopSignoffsRepo.limits = []
    FakeSopSignoffsRepo.months = []
    FakeSopSignoffsRepo.raise_on_list_newest = False
    FakeSopSignoffsRepo.raise_on_latest_for_month = False
    FakeSopSignoffsRepo.create_calls = 0
    monkeypatch.setattr(exceptions, "_ddmrp_source", FakeDdmrpSource())
    monkeypatch.setattr(exceptions, "stockout_ranked", FakeStockoutSource())
    monkeypatch.setattr(exceptions, "expedite_list", FakeExpediteSource())
    monkeypatch.setattr(exceptions, "_plan_releases_repo", FakePlanReleasesRepo())
    monkeypatch.setattr(exceptions, "_open_purchase_lines_repo", FakeOpenPurchaseRepo())
    monkeypatch.setattr(
        exceptions, "_transfer_releases_repo", FakeTransferReleasesRepo()
    )
    monkeypatch.setattr(
        exceptions, "_parameter_writebacks_repo", FakeParameterWritebacksRepo()
    )
    monkeypatch.setattr(exceptions, "realised_accuracy", FakeRealisedAccuracy())
    monkeypatch.setattr(exceptions, "_sop_signoffs_repo", FakeSopSignoffsRepo())
    # The window anchor: the route's "current UTC time".
    monkeypatch.setattr(exceptions, "_utcnow", lambda: NOW)
    # The current server date: the route module's ``date`` answers the
    # pinned date, so the result never depends on the day the test
    # runs.
    monkeypatch.setattr(exceptions, "date", PinnedDate)

    # No ERP call: the client classes fail if constructed.
    def _no_client(self, *args, **kwargs):
        raise AssertionError("the exceptions route must not call an ERP")

    monkeypatch.setattr(acumatica_client_module.AcumaticaClient, "__init__", _no_client)
    monkeypatch.setattr(odoo_client_module.OdooClient, "__init__", _no_client)

    # The stored system settings are read from the real settings
    # table — counted, to prove the one read per call (the sop source
    # reads its setting from these, never on its own).
    real_stored = exceptions._stored_system_settings
    settings_reads = {"count": 0}
    gets = {"count": 0}

    def counting_stored_settings():
        settings_reads["count"] += 1
        return real_stored()

    monkeypatch.setattr(exceptions, "_stored_system_settings", counting_stored_settings)

    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})

    def _get(expect_latest: bool = True):
        """One GET of the triage list: it must make exactly one
        ``list_newest`` and one ``latest_for_month`` read, and one
        settings read."""
        gets["count"] += 1
        before_newest = FakeSopSignoffsRepo.list_newest_calls
        before_latest = FakeSopSignoffsRepo.latest_for_month_calls
        before_settings = settings_reads["count"]
        response = client.get(URL)
        assert response.status_code == 200, response.text
        assert FakeSopSignoffsRepo.list_newest_calls == before_newest + 1
        if expect_latest:
            assert FakeSopSignoffsRepo.latest_for_month_calls == before_latest + 1
        assert settings_reads["count"] == before_settings + 1
        return response.json(), response.text

    def _pin(day: date):
        PinnedDate.fixed = day

    # --- The listing: the setting with its default --------------------
    listing = admin.get(SETTINGS_URL)
    assert listing.status_code == 200, listing.text
    entry = _setting_entry(listing.json(), SOP_DAY_KEY)
    assert entry["scope"] == "system"
    assert entry["type"] == "number"
    assert entry["default"] == 10
    assert entry["min"] == 1
    assert entry["max"] == 28
    assert entry["description"].strip()
    assert entry["value"] == 10, "the default applies while unstored"
    assert entry["source"] == "default"
    assert entry["editable"] is True, "the admin may edit a system setting"

    # --- Flagged: 2026-11-15, one sign-off of 2026-10 ------------------
    _pin(PinnedDate(2026, 11, 15))
    FakeSopSignoffsRepo.newest_rows = [SIGNOFF_ROW]
    FakeSopSignoffsRepo.current_month_row = None
    body, text = _get()
    _assert_response(body, [_sop_row("2026-11-10")], [])
    # The current month read is the review month, "2026-11", and the
    # any-month read is the newest row only.
    assert FakeSopSignoffsRepo.months == ["2026-11"]
    assert FakeSopSignoffsRepo.limits == [1]
    # Order: the row sorts after the erp_push_failed row, as the
    # last kind.
    kinds = [row["kind"] for row in body["rows"]]
    assert (
        exceptions._KIND_ORDER["erp_push_failed"] + 1
        == exceptions._KIND_ORDER["sop_signoff_due"]
    )
    assert kinds.index("erp_push_failed") < kinds.index("sop_signoff_due")
    assert kinds[-1] == "sop_signoff_due"
    # The stored error text appears nowhere in the response.
    assert "SECRET-955" not in text

    # --- The exact day: 2026-11-10 flags, 2026-11-09 does not ----------
    _pin(PinnedDate(2026, 11, 10))
    body, _ = _get()
    _assert_response(body, [_sop_row("2026-11-10")], [])
    _pin(PinnedDate(2026, 11, 9))
    body, _ = _get()
    _assert_response(body, [], [])
    assert not any(row["kind"] == "sop_signoff_due" for row in body["rows"])

    # --- Signed off: a sign-off of 2026-11 stored — no row -------------
    _pin(PinnedDate(2026, 11, 15))
    FakeSopSignoffsRepo.newest_rows = [SIGNOFF_ROW_NOV, SIGNOFF_ROW]
    FakeSopSignoffsRepo.current_month_row = SIGNOFF_ROW_NOV
    body, _ = _get()
    _assert_response(body, [], [])
    assert not any(row["kind"] == "sop_signoff_due" for row in body["rows"])

    # --- Never signed off: no sign-offs at all — no row ----------------
    _pin(PinnedDate(2026, 11, 28))
    FakeSopSignoffsRepo.newest_rows = []
    FakeSopSignoffsRepo.current_month_row = None
    body, _ = _get()
    _assert_response(body, [], [])
    assert not any(row["kind"] == "sop_signoff_due" for row in body["rows"])

    # --- The setting stored as 3: due from the 3rd of the month --------
    put = admin.put(f"{SETTINGS_URL}/{SOP_DAY_KEY}", json={"value": 3})
    assert put.status_code == 200, put.text
    assert put.json()["value"] == 3
    assert put.json()["source"] == "system"
    assert put.json()["updated_by"] == "dev-admin-1"
    _pin(PinnedDate(2026, 11, 3))
    FakeSopSignoffsRepo.newest_rows = [SIGNOFF_ROW]
    FakeSopSignoffsRepo.current_month_row = None
    body, _ = _get()
    _assert_response(body, [_sop_row("2026-11-03")], [])
    assert body["rows"][-1]["detected_on"] == "2026-11-03"
    assert body["rows"][-1]["key"] == "sop_signoff_due:2026-11"
    assert body["rows"][-1]["detail"] == (
        "2026-11 has not been signed off. "
        "The last sign-off was for 2026-10 on 2026-10-05."
    )

    # --- Out of range: a stored 40 falls back to the default 10 --------
    _store_out_of_range(SOP_DAY_KEY, 40)
    _pin(PinnedDate(2026, 11, 15))
    body, _ = _get()
    _assert_response(body, [_sop_row("2026-11-10")], [])
    # The reset restores the default for the following calls.
    deleted = admin.delete(f"{SETTINGS_URL}/{SOP_DAY_KEY}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["value"] == 10
    assert deleted.json()["source"] == "default"

    # --- Fractional: a stored 10.5 compares as-is, not truncated ----
    put = admin.put(f"{SETTINGS_URL}/{SOP_DAY_KEY}", json={"value": 10.5})
    assert put.status_code == 200, put.text
    assert put.json()["value"] == 10.5
    assert put.json()["source"] == "system"
    # Day 10 < 10.5: a truncated comparison (day >= 10) would
    # wrongly flag here.
    _pin(PinnedDate(2026, 11, 10))
    body, _ = _get()
    _assert_response(body, [], [])
    assert not any(row["kind"] == "sop_signoff_due" for row in body["rows"])
    # Day 11 >= 10.5: flagged, detected_on the first due day.
    _pin(PinnedDate(2026, 11, 11))
    body, _ = _get()
    _assert_response(body, [_sop_row("2026-11-11")], [])
    assert body["rows"][-1]["detected_on"] == "2026-11-11"
    # The reset restores the default for the following sections.
    deleted = admin.delete(f"{SETTINGS_URL}/{SOP_DAY_KEY}")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["value"] == 10
    assert deleted.json()["source"] == "default"

    # --- Failure: a raising read lists "sop" — the others answer -------
    # The any-month read raises: the source fails like the other
    # two-read sources (the current-month read is not then reached).
    latest_before = FakeSopSignoffsRepo.latest_for_month_calls
    FakeSopSignoffsRepo.raise_on_list_newest = True
    body, _ = _get(expect_latest=False)
    assert body["sources_failed"] == ["sop"]
    _assert_response(body, [], ["sop"])
    assert FakeSopSignoffsRepo.latest_for_month_calls == latest_before
    # The current-month read raises: the source fails the same way.
    FakeSopSignoffsRepo.raise_on_list_newest = False
    FakeSopSignoffsRepo.raise_on_latest_for_month = True
    body, _ = _get()
    assert body["sources_failed"] == ["sop"]
    _assert_response(body, [], ["sop"])
    FakeSopSignoffsRepo.raise_on_latest_for_month = False
    body, _ = _get()
    _assert_response(body, [_sop_row("2026-11-10")], [])

    # --- Writes nothing: no sign-off write, no ERP client --------------
    assert FakeSopSignoffsRepo.create_calls == 0
    # The stored settings read stayed one per call — the sop source
    # read its setting from the settings the loop already held.
    assert settings_reads["count"] == gets["count"]
