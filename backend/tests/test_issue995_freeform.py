"""Issue #995 proof: the "erp_code_clash" exception kind.

AC-1 — the "clashes" source of ``GET /api/exceptions`` reads the
``erp_code_clashes`` rows once per call —
``ErpCodeClashPostgresRepository.list_open(limit=200)``: the rows
whose ``resolved_at`` is still null, the most recently seen first
(``last_seen_at`` descending), then by ``id`` (ascending), in one
SQL statement.  One row per open clash, kind "erp_code_clash"
(high), the key "erp_code_clash:<refused_source>:<item_code>/<site_code>",
the clash's ``item_code`` and ``site_code``, title "Same codes in
two ERPs", the detail "<item_code> at <site_code> is held by
<held_by>, so <refused_source> rows for it are not loaded.", view
"#/settings" and ``detected_on`` the date of ``first_seen_at``.  In
the kind order the row stands directly after "sop_signoff_due", as
the last kind (whose number does not change).  A failure of the read
lists "clashes" in ``sources_failed`` and the other sources still
answer, with status 200.  The other eight sources of the triage list
are replaced in the route module with fakes the way the issue 955
proof does, and the ``erp_code_clashes`` rows are inserted straight
into the table.

AC-2 — proved in one test, the current UTC time pinned like the
issue 955 proof (so the push failure window never depends on the
day the test runs):

  * the read — three open clash rows with the middle one seen most
    recently and two tied on the newest seeing (the earlier insert
    first) answer ``list_open(limit=200)`` in that order, a row
    with ``resolved_at`` set answers no row, and one call runs one
    SQL statement;
  * the row — an open clash of ITEM-A at S1, held by acumatica,
    refused by odoo, first seen 2026-11-02, answers one row with
    every field above and ``detected_on`` "2026-11-02", the date of
    ``first_seen_at``;
  * resolved — a row with ``resolved_at`` set answers no row in
    the triage list either;
  * ordered last among equals — ``_KIND_ORDER["erp_code_clash"]``
    is ``_KIND_ORDER["sop_signoff_due"]`` plus 1 (13 after the
    unchanged 12), and the clash row sorts after every other row of
    the response;
  * failed read — with ``list_open`` raising, the call answers 200,
    ``sources_failed`` holds "clashes" and the other sources' rows
    still answer;
  * acknowledged — acknowledging the row's key through the
    existing ``PUT /api/exceptions/ack`` route marks the row
    acknowledged on the next read;
  * one read — one ``GET /api/exceptions`` calls ``list_open``
    once (one SQL statement), and the stored settings read stays
    one per call.
"""

from datetime import UTC, date, datetime, timedelta

from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
import app.feed.acumatica_client as acumatica_client_module
import app.feed.odoo_client as odoo_client_module
import app.repositories.erp_code_clashes_postgres_repository as clashes_repo_module
from app.db.connection import get_connection
from app.main import app

URL = "/api/exceptions"
ACK_URL = "/api/exceptions/ack"

# The pinned current UTC time (the route's failure-window anchor).
NOW = datetime(2026, 11, 15, 12, 0, 0, tzinfo=UTC)

# The clash row under test: first seen 2026-11-02, most recently
# seen 2026-11-14, still open.
FIRST_SEEN_AT = "2026-11-02T09:30:00+00:00"
LAST_SEEN_AT = "2026-11-14T10:00:00+00:00"


class FakeDdmrpSource:
    """The fake for the route module's DDMRP source: one stocked-out
    buffer row as of the canned as-of date."""

    calls = 0

    def __call__(self):
        FakeDdmrpSource.calls += 1
        return (
            date(2026, 11, 9),
            [
                {
                    "item_code": "ITEM-995-A",
                    "site_code": "SITE-995-A",
                    "on_hand": 0.0,
                    "on_hand_pct_of_red": 0.0,
                    "on_hand_status": "stocked_out",
                },
            ],
        )


class FakeStockoutSource:
    """The fake for the route module's ``stockout_ranked``: no rows."""

    calls = 0

    def __call__(self, as_of=None):
        FakeStockoutSource.calls += 1
        return []


class FakeExpediteSource:
    """The fake for the route module's ``expedite_list``: no lines,
    but an as-of date the response reports (the newest one, so it is
    the response's as_of)."""

    calls = 0

    def __call__(self, as_of=None):
        FakeExpediteSource.calls += 1
        return {
            "as_of": date(2026, 11, 10),
            "stale_excluded": 0,
            "lines": [],
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
                "item_code": "ITEM-995-A",
                "site_code": "SITE-995-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=2),
                "error": "boom: the Acumatica write failed (api-key=SECRET-995)",
            },
        ]


class FakeSopSignoffsRepo:
    """The fake for the route module's sop_signoffs repository: no
    sign-offs — no S&OP sign-off due row."""

    calls = 0
    latest_calls = 0

    def list_newest(self, month=None, limit=50):
        FakeSopSignoffsRepo.calls += 1
        return []

    def latest_for_month(self, month):
        FakeSopSignoffsRepo.latest_calls += 1
        return None


class FakeRealisedAccuracy:
    """The fake for the route module's ``realised_accuracy``: no
    item-sites — no forecast-bias rows."""

    calls = 0

    def __call__(self, **kwargs):
        FakeRealisedAccuracy.calls += 1
        return {
            "as_of": date(2026, 11, 15),
            "compared_buckets": 0,
            "overall": {},
            "by_horizon": [],
            "item_sites": [],
        }


class CountingClashRepo:
    """The counting stand-in for the route module's clash
    repository: delegates every ``list_open`` to the real repository
    (the rows come from the table the test seeds directly), records
    the calls and the limits, and raises while its flag is set."""

    calls = 0
    limits: list = []
    raise_on_list_open = False

    def __init__(self, inner):
        self.inner = inner

    def list_open(self, limit=200):
        CountingClashRepo.calls += 1
        CountingClashRepo.limits.append(limit)
        if CountingClashRepo.raise_on_list_open:
            raise RuntimeError("the erp_code_clashes read is down")
        return self.inner.list_open(limit=limit)


# The SQL statements the clash repository executes, in order: one
# ``list_open`` must run exactly one statement.
SQL_STATEMENTS: list = []


class _CountingCursor:
    """Wraps the real cursor and records every executed statement."""

    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, *args, **kwargs):
        SQL_STATEMENTS.append(args[0])
        return self._cursor.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._cursor, name)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return self._cursor.__exit__(*exc_info)


class _CountingConnection:
    """Wraps the real connection: its cursors record their
    statements, everything else is delegated."""

    def __init__(self, real):
        self._real = real

    def cursor(self, *args, **kwargs):
        return _CountingCursor(self._real.cursor(*args, **kwargs))

    def __getattr__(self, name):
        return getattr(self._real, name)


def _insert_clash(
    item_code,
    site_code,
    held_by,
    refused_source,
    first_seen_at,
    last_seen_at,
    resolved_at=None,
):
    """One clash row straight into the table; returns its id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO erp_code_clashes "
                "(item_code, site_code, held_by, refused_source, "
                "first_seen_at, last_seen_at, resolved_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                "RETURNING id",
                (
                    item_code,
                    site_code,
                    held_by,
                    refused_source,
                    first_seen_at,
                    last_seen_at,
                    resolved_at,
                ),
            )
            row = cur.fetchone()
        conn.commit()
        return row[0]
    finally:
        conn.close()


def _truncate_clashes():
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("TRUNCATE TABLE erp_code_clashes")
        conn.commit()
    finally:
        conn.close()


def _row_with(row, **updates):
    expected = {
        "kind": row["kind"],
        "severity": row["severity"],
        "key": row["key"],
        "item_code": row["item_code"],
        "site_code": row["site_code"],
        "title": row["title"],
        "detail": row["detail"],
        "view": row["view"],
        "detected_on": row["detected_on"],
        "acknowledged": False,
        "acknowledged_by_name": None,
        "acknowledged_at": None,
        "snooze_until": None,
        "first_seen_on": None,
        "new": False,
    }
    expected.update(updates)
    return expected


def _assert_body(body, clash_rows, sources_failed, acknowledged_key=None):
    """The full expected triage list: the two canned high rows (the
    stocked-out buffer and the failed push) plus the clash rows the
    scenarios yield, in the response's order."""
    expected = [
        _row_with(
            {
                "kind": "buffer_stocked_out",
                "severity": "high",
                "key": "buffer_stocked_out:ITEM-995-A/SITE-995-A",
                "item_code": "ITEM-995-A",
                "site_code": "SITE-995-A",
                "title": "Buffer stocked out",
                "detail": "on hand 0 (0% of top of red)",
                "view": "#/buffers",
                "detected_on": "2026-11-09",
            }
        ),
        _row_with(
            {
                "kind": "erp_push_failed",
                "severity": "high",
                "key": "erp_push_failed:pw-1",
                "item_code": None,
                "site_code": None,
                "title": "ERP push failed",
                "detail": "push to acumatica failed",
                "view": "#/writeback",
                "detected_on": "2026-11-13",
            }
        ),
    ] + [
        _row_with(
            row,
            acknowledged=row["key"] == acknowledged_key,
            acknowledged_by_name="admin" if row["key"] == acknowledged_key else None,
            acknowledged_at=NOW.isoformat() if row["key"] == acknowledged_key else None,
        )
        for row in clash_rows
    ]
    # The acknowledged_at is the database's now(), not a value the
    # proof can pin: compare it as "a parseable ISO timestamp or
    # null", everything else field for field.
    assert len(body["rows"]) == len(expected)
    for actual_row, expected_row in zip(body["rows"], expected):
        actual_row = dict(actual_row)
        actual_at = actual_row.pop("acknowledged_at")
        expected_at = expected_row.pop("acknowledged_at")
        assert actual_row == expected_row, (actual_row, expected_row)
        if expected_at is None:
            assert actual_at is None
        else:
            assert isinstance(actual_at, str)
            datetime.fromisoformat(actual_at)
    assert set(body) == {"as_of", "counts", "rows", "sources_failed"}
    assert body["as_of"] == "2026-11-10"
    assert body["sources_failed"] == sources_failed
    assert body["counts"] == {
        "high": len(expected),
        "medium": 0,
        "acknowledged": sum(1 for row in expected if row["acknowledged"]),
        "new": 0,
    }


def _clash_row(detected_on: str) -> dict:
    """The expected erp_code_clash row of the seeded ITEM-A at S1
    clash (held by acumatica, refused by odoo)."""
    return {
        "kind": "erp_code_clash",
        "severity": "high",
        "key": "erp_code_clash:odoo:ITEM-A/S1",
        "item_code": "ITEM-A",
        "site_code": "S1",
        "title": "Same codes in two ERPs",
        "detail": (
            "ITEM-A at S1 is held by acumatica, " "so odoo rows for it are not loaded."
        ),
        "view": "#/settings",
        "detected_on": detected_on,
    }


def test_issue995_freeform(monkeypatch, client, admin_token):
    # The other eight sources in the route module are replaced with
    # fakes that answer canned rows, the way the issue 955 proof
    # does, and the clash repository is wrapped in a counter that
    # delegates to the real repository — the clash rows come from
    # the table the test seeds directly.  Every fake records its
    # calls.
    FakeDdmrpSource.calls = 0
    FakeStockoutSource.calls = 0
    FakeExpediteSource.calls = 0
    FakePlanReleasesRepo.calls = 0
    FakeOpenPurchaseRepo.calls = 0
    FakeTransferReleasesRepo.calls = 0
    FakeTransferReleasesRepo.in_flight_calls = 0
    FakeParameterWritebacksRepo.calls = 0
    FakeSopSignoffsRepo.calls = 0
    FakeSopSignoffsRepo.latest_calls = 0
    FakeRealisedAccuracy.calls = 0
    CountingClashRepo.calls = 0
    CountingClashRepo.limits = []
    CountingClashRepo.raise_on_list_open = False
    SQL_STATEMENTS.clear()
    real_clashes_repo = exceptions._erp_code_clashes_repo
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
    monkeypatch.setattr(exceptions, "_sop_signoffs_repo", FakeSopSignoffsRepo())
    monkeypatch.setattr(exceptions, "realised_accuracy", FakeRealisedAccuracy())
    monkeypatch.setattr(
        exceptions, "_erp_code_clashes_repo", CountingClashRepo(real_clashes_repo)
    )
    # The repository's connections are wrapped so every SQL statement
    # it executes is recorded (one list_open must run one statement).
    real_get_connection = clashes_repo_module.get_connection
    monkeypatch.setattr(
        clashes_repo_module,
        "get_connection",
        lambda: _CountingConnection(real_get_connection()),
    )
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
    collects = {"count": 0}

    def counting_stored_settings():
        settings_reads["count"] += 1
        return real_stored()

    monkeypatch.setattr(exceptions, "_stored_system_settings", counting_stored_settings)

    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})

    def _get(expect_sql: bool = True):
        """One GET of the triage list: it must make exactly one
        ``list_open`` read (one SQL statement, while the read is
        not failing) and one settings read."""
        collects["count"] += 1
        before_calls = CountingClashRepo.calls
        before_sql = len(SQL_STATEMENTS)
        before_settings = settings_reads["count"]
        response = client.get(URL)
        assert response.status_code == 200, response.text
        assert CountingClashRepo.calls == before_calls + 1
        if expect_sql:
            assert len(SQL_STATEMENTS) == before_sql + 1
        else:
            # A raising read runs no SQL at all.
            assert len(SQL_STATEMENTS) == before_sql
        assert settings_reads["count"] == before_settings + 1
        return response.json(), response.text

    # --- The read: open rows, most recently seen first, then by id ---
    _truncate_clashes()
    # c1: seen 2026-11-01.  c2 and c3: seen 2026-11-02, the same
    # instant — c2 was inserted earlier, so the id tie-break puts it
    # first.  cr: resolved — never open.
    clash_ids = {
        "c1": _insert_clash(
            "ITEM-X",
            "SX",
            "odoo",
            "acumatica",
            "2026-11-01T08:00:00+00:00",
            "2026-11-01T08:00:00+00:00",
        ),
        "c2": _insert_clash(
            "ITEM-Y",
            "SY",
            "acumatica",
            "odoo",
            "2026-10-20T08:00:00+00:00",
            "2026-11-02T08:00:00+00:00",
        ),
        "c3": _insert_clash(
            "ITEM-Z",
            "SZ",
            "acumatica",
            "odoo",
            "2026-10-21T08:00:00+00:00",
            "2026-11-02T08:00:00+00:00",
        ),
    }
    _insert_clash(
        "ITEM-C",
        "SC",
        "acumatica",
        "odoo",
        "2026-10-22T08:00:00+00:00",
        "2026-11-02T09:00:00+00:00",
        resolved_at="2026-11-03T08:00:00+00:00",
    )
    SQL_STATEMENTS.clear()
    open_rows = real_clashes_repo.list_open(limit=200)
    assert [row["id"] for row in open_rows] == [
        clash_ids["c2"],
        clash_ids["c3"],
        clash_ids["c1"],
    ]
    # One read, one SQL statement, the open rows only.
    assert len(SQL_STATEMENTS) == 1
    statement = SQL_STATEMENTS[0]
    assert "WHERE resolved_at IS NULL" in statement
    assert "ORDER BY last_seen_at DESC, id ASC" in statement
    assert "LIMIT" in statement
    # The default limit is 200 as well: the same three rows.
    SQL_STATEMENTS.clear()
    assert [row["id"] for row in real_clashes_repo.list_open()] == [
        clash_ids["c2"],
        clash_ids["c3"],
        clash_ids["c1"],
    ]
    assert len(SQL_STATEMENTS) == 1
    # A smaller limit truncates, still most recently seen first.
    SQL_STATEMENTS.clear()
    assert [row["id"] for row in real_clashes_repo.list_open(limit=2)] == [
        clash_ids["c2"],
        clash_ids["c3"],
    ]
    assert len(SQL_STATEMENTS) == 1

    # --- The row: one open clash of ITEM-A at S1 ----------------------
    _truncate_clashes()
    _insert_clash("ITEM-A", "S1", "acumatica", "odoo", FIRST_SEEN_AT, LAST_SEEN_AT)
    body, _ = _get()
    _assert_body(body, [_clash_row("2026-11-02")], [])
    # The row carries every stated field — checked against the full
    # expected dict above — and its detected_on is the date of
    # first_seen_at (2026-11-02), not its last seen day.
    clash_row = body["rows"][-1]
    assert clash_row["kind"] == "erp_code_clash"
    assert clash_row["severity"] == "high"
    assert clash_row["key"] == "erp_code_clash:odoo:ITEM-A/S1"
    assert clash_row["item_code"] == "ITEM-A"
    assert clash_row["site_code"] == "S1"
    assert clash_row["title"] == "Same codes in two ERPs"
    assert clash_row["detail"] == (
        "ITEM-A at S1 is held by acumatica, " "so odoo rows for it are not loaded."
    )
    assert clash_row["view"] == "#/settings"
    assert clash_row["detected_on"] == "2026-11-02"
    # The stored error text appears nowhere in the response.
    assert "SECRET-995" not in _

    # --- Resolved: a row with resolved_at set answers no row ----------
    _insert_clash(
        "ITEM-C",
        "SC",
        "odoo",
        "acumatica",
        "2026-11-05T08:00:00+00:00",
        "2026-11-06T08:00:00+00:00",
        resolved_at="2026-11-06T09:00:00+00:00",
    )
    body, _ = _get()
    _assert_body(body, [_clash_row("2026-11-02")], [])
    assert not any(
        row["key"] == "erp_code_clash:acumatica:ITEM-C/SC" for row in body["rows"]
    )

    # --- Ordered last among equals ------------------------------------
    # The S&OP sign-off due keeps its number; the clash stands
    # directly after it, as the last kind.
    assert exceptions._KIND_ORDER["sop_signoff_due"] == 12
    assert (
        exceptions._KIND_ORDER["erp_code_clash"]
        == exceptions._KIND_ORDER["sop_signoff_due"] + 1
        == 13
    )
    # Every other kind keeps its number.
    assert exceptions._KIND_ORDER["buffer_stocked_out"] == 0
    assert exceptions._KIND_ORDER["erp_push_failed"] == 11
    # The clash row sorts after every other row of the response.
    kinds = [row["kind"] for row in body["rows"]]
    assert kinds.index("erp_push_failed") < kinds.index("erp_code_clash")
    assert kinds[-1] == "erp_code_clash"

    # --- Failed read: "clashes" in sources_failed, the others answer -
    CountingClashRepo.raise_on_list_open = True
    body, _ = _get(expect_sql=False)
    _assert_body(body, [], ["clashes"])
    assert [row["kind"] for row in body["rows"]] == [
        "buffer_stocked_out",
        "erp_push_failed",
    ]
    CountingClashRepo.raise_on_list_open = False
    body, _ = _get()
    _assert_body(body, [_clash_row("2026-11-02")], [])

    # --- Acknowledged: the existing route marks the row acknowledged --
    put = admin.put(ACK_URL, json={"key": "erp_code_clash:odoo:ITEM-A/S1"})
    assert put.status_code == 200, put.text
    assert put.json()["key"] == "erp_code_clash:odoo:ITEM-A/S1"
    assert put.json()["acknowledged_by"] == "dev-admin-1"
    collects["count"] += 1  # the PUT re-reads the current list
    body, _ = _get()
    _assert_body(
        body,
        [_clash_row("2026-11-02")],
        [],
        acknowledged_key="erp_code_clash:odoo:ITEM-A/S1",
    )
    clash_row = body["rows"][-1]
    assert clash_row["acknowledged"] is True
    assert clash_row["acknowledged_by_name"] == "admin"

    # --- One read: one GET calls list_open once -----------------------
    collects["count"] += 1
    before_calls = CountingClashRepo.calls
    before_sql = len(SQL_STATEMENTS)
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert CountingClashRepo.calls == before_calls + 1
    assert len(SQL_STATEMENTS) == before_sql + 1
    assert CountingClashRepo.limits == [200] * CountingClashRepo.calls
    # The stored settings read stayed one per collect.
    assert settings_reads["count"] == collects["count"]
