"""Issue #796 proof: the exception acknowledgements (exception_acks).

AC-1 — the five sources of the triage list are replaced in the route
module with fakes that answer canned rows (the way the issue 795
proof does), while the exception_acks store is the real Postgres
table the new migration ``0031_exception_acks`` creates in the test
database.  Proved here:

  * the migration — ``0031_exception_acks`` is the head and the only
    head, the test database is migrated to it, and ``exception_acks``
    has the stated columns (``key`` the String(255) primary key,
    ``acknowledged_by`` not null, ``acknowledged_by_name`` and
    ``note`` nullable, ``acknowledged_at`` a not-null timestamptz and
    ``snooze_until`` a nullable date);
  * the unacknowledged list — every row gains ``acknowledged`` false
    and ``acknowledged_by_name``, ``acknowledged_at`` and
    ``snooze_until`` null, and ``counts`` gains ``acknowledged`` 0;
  * acknowledging — an admin PUT /api/exceptions/ack of one
    exception's key answers 200 with the stored row (the token
    subject and its display name, a fresh ``acknowledged_at``), the
    row reads ``acknowledged`` true with the admin's name,
    ``counts.acknowledged`` is 1, and a second PUT replaces the note;
  * snoozing — a ``snooze_until`` after the as-of date hides the row
    (``counts`` drop accordingly) and ``include_snoozed=true`` shows
    it again; a ``snooze_until`` that is after the as-of date but
    before the current UTC date at request time answers 422 — the
    past check is against the current UTC date, never the response
    as-of date; an omitted ``snooze_until`` in a PUT clears the
    stored one;
  * removing — DELETE /api/exceptions/ack?key=… answers 204 and
    restores the unacknowledged row; a second DELETE answers 404;
  * stale acknowledgements — a stored acknowledgement whose key no
    longer appears in the list is simply ignored: the list answers
    unchanged;
  * refusals — a key not in the current exceptions list answers 404,
    a past ``snooze_until`` 422, an empty key 422, a NUL byte in the
    key or the note 422 (never a 500), a key that would not fit the
    stored column 422, a non-admin (planner) PUT and DELETE 403 and
    no token 401;
  * the schema file — ``backend/openapi.json`` lists the ack routes,
    the ``include_snoozed`` query parameter and the new row and count
    fields;
  * hostile values, never a 500 — a ranked stockout row whose
    ``p_stockout`` is null answers a detail without the chance part
    (never the literal ``None``), and an admin token whose identity
    has no display name answers 200 (the name is stored null)
    instead of a 500;
  * the identity requirement — the proof also runs as a NON-ADMIN
    (role "user"): a second pass in the same test after a full
    reload, with the page booted the production way (the token it
    would take from the URL hash as ``#access_token=...`` — the
    development token, role "user" — sent as its Bearer token),
    ``GET /api/auth/config`` answering publicly and ``GET
    /api/auth/me`` resolving the viewer, sees the triage page still
    render (200 with the same rows) while the admin-only controls
    are absent for the viewer (the ack PUT and the ack DELETE answer
    403), and no token is still a 401.
"""

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
from app.auth.models import AuthUser
from app.auth.security import validate_token
from app.db.connection import get_cursor
from app.main import app
from tests.migration_head import CURRENT_HEAD

URL = "/api/exceptions"
ACK_URL = "/api/exceptions/ack"
_OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
_BACKEND = Path(__file__).resolve().parents[1]

ADMIN_TOKEN = "dev-secret-token:admin"
# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
ADMIN_SUB = "dev-admin-1"  # the admin stub user's subject
ADMIN_NAME = "admin"  # display_name of the admin stub user

# The pinned current UTC time (the past-snooze check's anchor).  Two
# days after the canned sources' latest as-of (2026-10-10), so a
# snooze past the as-of date but before the pinned current UTC date
# can be told apart from the as-of date.
NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
CUTOFF = NOW - timedelta(days=14)  # the 14-day failure window

# The canned sources' latest as-of (the response as-of date).
AS_OF = date(2026, 10, 10)
DDMRP_AS_OF = date(2026, 10, 9)
STOCKOUT_AS_OF = date(2026, 10, 9)
EXPEDITE_AS_OF = "2026-10-10"

# Snooze dates relative to the pinned current UTC date (2026-10-12):
# SNOOZE_TOMORROW is after the as-of date (it hides the row);
# SNOOZE_YESTERDAY is after the as-of date but before the current
# UTC date (it must answer 422 — the check is against the current
# UTC date, never the as-of date).
SNOOZE_TOMORROW = date(2026, 10, 13)
SNOOZE_YESTERDAY = date(2026, 10, 11)

# The exception key the proof acknowledges, snoozes and removes.
KEY = "buffer_stocked_out:ITEM-795-A/SITE-795-A"

RELEASE_ERROR_TEXT = (
    "Acumatica batch failed: HTTP 500 InternalServerError "
    "(request-id=8f3a2c, api-key=SECRET-796)"
)
PUSH_ERROR_TEXT = (
    "boom: the Acumatica ItemWarehouse write for ITEM-796-A at "
    "SITE-796-A failed (api-key=SECRET-796)"
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
    """The fake for the route module's plan_releases repository: the
    canned rows span every case of the 14-day UTC window."""

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
                "item_code": "ITEM-796-A",
                "site_code": "SITE-796-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=3),
                "error": PUSH_ERROR_TEXT,
            },
            {
                "id": "pw-2",
                "item_code": "ITEM-796-B",
                "site_code": "SITE-796-B",
                "source_system": "odoo",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=20),  # out
                "error": PUSH_ERROR_TEXT,
            },
            {
                "id": "pw-3",
                "item_code": "ITEM-796-C",
                "site_code": "SITE-796-C",
                "source_system": "odoo",
                "outcome": "written",
                "written_at": NOW - timedelta(days=1),
                "error": None,
            },
            {
                "id": "pw-4",
                "item_code": "ITEM-796-D",
                "site_code": "SITE-796-D",
                "source_system": "odoo",
                "outcome": "failed",
                # The ISO string spelling, exactly at the cutoff: in
                "written_at": CUTOFF.isoformat(),
                "error": "Odoo: 500 internal (trace=abc796)",
            },
            {
                "id": "pw-5",
                "item_code": "ITEM-796-E",
                "site_code": "SITE-796-E",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": CUTOFF - timedelta(seconds=1),  # out
                "error": PUSH_ERROR_TEXT,
            },
        ]


def _expected_rows():
    """The full expected triage list (unacknowledged), in order —
    every row carrying the issue #796 acknowledgement fields:
    ``acknowledged`` false and ``acknowledged_by_name``,
    ``acknowledged_at`` and ``snooze_until`` null."""
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


def _row(body, key):
    """The response row of one key."""
    return next(row for row in body["rows"] if row["key"] == key)


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND / "migrations"))
    return cfg


def test_issue796_freeform(monkeypatch, client):
    # The five sources in the route module are replaced with fakes
    # that answer canned rows, the way the issue 795 proof replaces
    # the ERP clients.  The exception_acks store is NOT faked: it is
    # the real Postgres table the new migration creates in the test
    # database (truncated between tests by the shared fixture).
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
    # The anchor of the past-snooze check: the route's "current UTC
    # time" (two days after the canned sources' latest as-of).
    monkeypatch.setattr(exceptions, "_utcnow", lambda: NOW)

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    anon = TestClient(app)

    # --- The migration: 0031_exception_acks, the only head. ----------
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    assert list(heads) == [CURRENT_HEAD]
    with get_cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        assert [row["version_num"] for row in cur.fetchall()] == [CURRENT_HEAD]
    with get_cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'exception_acks' "
            "ORDER BY ordinal_position"
        )
        assert [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in cur.fetchall()
        ] == [
            ("key", "character varying", "NO"),
            ("acknowledged_by", "character varying", "NO"),
            ("acknowledged_by_name", "character varying", "YES"),
            ("acknowledged_at", "timestamp with time zone", "NO"),
            ("snooze_until", "date", "YES"),
            ("note", "text", "YES"),
        ]
    with get_cursor() as cur:
        cur.execute(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a "
            "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'exception_acks'::regclass "
            "AND i.indisprimary"
        )
        assert [row["attname"] for row in cur.fetchall()] == ["key"]

    # --- Unacknowledged: the new fields, all null or false. ----------
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"as_of", "counts", "rows", "sources_failed"}
    assert body["as_of"] == AS_OF.isoformat()
    assert body["sources_failed"] == []
    assert body["counts"] == {"high": 9, "medium": 3, "acknowledged": 0, "new": 0}
    assert body["rows"] == _expected_rows()

    # --- Acknowledging: the stored row, the row, the counts. --------
    ack = admin.put(ACK_URL, json={"key": KEY, "note": "on it"})
    assert ack.status_code == 200, ack.text
    stored = ack.json()
    assert stored["key"] == KEY
    assert stored["acknowledged_by"] == ADMIN_SUB
    assert stored["acknowledged_by_name"] == ADMIN_NAME
    assert stored["note"] == "on it"
    assert stored["snooze_until"] is None
    first_at = datetime.fromisoformat(stored["acknowledged_at"])

    body = admin.get(URL).json()
    row = _row(body, KEY)
    assert row["acknowledged"] is True
    assert row["acknowledged_by_name"] == ADMIN_NAME
    assert datetime.fromisoformat(row["acknowledged_at"]) == first_at
    assert row["snooze_until"] is None
    assert body["counts"] == {"high": 9, "medium": 3, "acknowledged": 1, "new": 0}
    # The other rows still read unacknowledged.
    for other in body["rows"]:
        if other["key"] != KEY:
            assert other["acknowledged"] is False
            assert other["acknowledged_by_name"] is None
            assert other["acknowledged_at"] is None
            assert other["snooze_until"] is None

    # A second PUT replaces the acknowledgement: the new note, the
    # current request's subject, name and a fresh acknowledged_at.
    again = admin.put(ACK_URL, json={"key": KEY, "note": "replaced note"})
    assert again.status_code == 200, again.text
    replaced = again.json()
    assert replaced["note"] == "replaced note"
    assert replaced["acknowledged_by"] == ADMIN_SUB
    assert replaced["acknowledged_by_name"] == ADMIN_NAME
    second_at = datetime.fromisoformat(replaced["acknowledged_at"])
    assert second_at >= first_at
    body = admin.get(URL).json()
    assert _row(body, KEY)["acknowledged"] is True
    assert body["counts"]["acknowledged"] == 1

    # --- Snoozing ------------------------------------------------------
    # After the as-of date but before the pinned current UTC date:
    # 422 — the past check is against the current UTC date, never the
    # response as-of date.
    past = admin.put(
        ACK_URL, json={"key": KEY, "snooze_until": SNOOZE_YESTERDAY.isoformat()}
    )
    assert past.status_code == 422, past.text
    # The refused snooze stored nothing.
    body = admin.get(URL).json()
    assert _row(body, KEY)["snooze_until"] is None
    assert body["counts"] == {"high": 9, "medium": 3, "acknowledged": 1, "new": 0}

    # A snooze until tomorrow (after the as-of date) hides the row.
    snoozed = admin.put(
        ACK_URL, json={"key": KEY, "snooze_until": SNOOZE_TOMORROW.isoformat()}
    )
    assert snoozed.status_code == 200, snoozed.text
    assert snoozed.json()["snooze_until"] == SNOOZE_TOMORROW.isoformat()
    # The omitted note cleared the stored note.
    assert snoozed.json()["note"] is None

    body = admin.get(URL).json()
    assert all(row["key"] != KEY for row in body["rows"])
    assert body["counts"] == {"high": 8, "medium": 3, "acknowledged": 0, "new": 0}

    # include_snoozed=true shows the snoozed row again.
    shown = admin.get(URL, params={"include_snoozed": "true"}).json()
    row = _row(shown, KEY)
    assert row["acknowledged"] is True
    assert row["acknowledged_by_name"] == ADMIN_NAME
    assert row["snooze_until"] == SNOOZE_TOMORROW.isoformat()
    assert shown["counts"] == {"high": 9, "medium": 3, "acknowledged": 1, "new": 0}

    # An omitted snooze_until in a PUT clears the stored one: the row
    # is back on the list (still acknowledged).
    unsnoozed = admin.put(ACK_URL, json={"key": KEY})
    assert unsnoozed.status_code == 200, unsnoozed.text
    assert unsnoozed.json()["snooze_until"] is None
    body = admin.get(URL).json()
    assert _row(body, KEY)["snooze_until"] is None
    assert _row(body, KEY)["acknowledged"] is True
    assert body["counts"] == {"high": 9, "medium": 3, "acknowledged": 1, "new": 0}

    # --- A stale acknowledgement is simply ignored. -------------------
    before = admin.get(URL).json()
    exceptions._exception_acks_repo.upsert(
        key="ghost:42",
        acknowledged_by=ADMIN_SUB,
        acknowledged_by_name=ADMIN_NAME,
        snooze_until=SNOOZE_TOMORROW,
        note=None,
    )
    after = admin.get(URL).json()
    assert after == before
    # The ghost key still carries an acknowledgement: its DELETE
    # answers 204, and the list answers unchanged.
    assert admin.delete(ACK_URL, params={"key": "ghost:42"}).status_code == 204
    assert admin.get(URL).json() == before

    # --- Removing ------------------------------------------------------
    assert admin.delete(ACK_URL, params={"key": KEY}).status_code == 204
    body = admin.get(URL).json()
    assert body["rows"] == _expected_rows()
    assert body["counts"] == {"high": 9, "medium": 3, "acknowledged": 0, "new": 0}
    # A second DELETE answers 404: no acknowledgement is stored.
    assert admin.delete(ACK_URL, params={"key": KEY}).status_code == 404

    # --- Refusals ------------------------------------------------------
    # A key not in the current exceptions list answers 404.
    assert admin.put(ACK_URL, json={"key": "nope:404"}).status_code == 404
    # A past snooze_until answers 422, and the refusal stored nothing.
    assert (
        admin.put(
            ACK_URL, json={"key": KEY, "snooze_until": date(2026, 10, 1).isoformat()}
        ).status_code
        == 422
    )
    assert admin.delete(ACK_URL, params={"key": KEY}).status_code == 404
    # An empty key, a NUL byte in the key, a key that would not fit
    # the stored column and a NUL byte in the note answer 422 — never
    # a 500.
    assert admin.put(ACK_URL, json={"key": ""}).status_code == 422
    assert admin.put(ACK_URL, json={"key": "bad\x00key"}).status_code == 422
    assert admin.put(ACK_URL, json={"key": "x" * 10_000}).status_code == 422
    assert (
        admin.put(ACK_URL, json={"key": KEY, "note": "bad\x00note"}).status_code == 422
    )
    # A huge note is stored without error (the note column is Text).
    assert (
        admin.put(ACK_URL, json={"key": KEY, "note": "y" * 100_000}).status_code == 200
    )
    assert admin.delete(ACK_URL, params={"key": KEY}).status_code == 204
    # A NUL byte in a DELETE key answers 404 — never a 500.
    assert admin.delete(ACK_URL, params={"key": "bad\x00key"}).status_code == 404
    # A non-admin (planner) PUT and DELETE answer 403.
    assert viewer.put(ACK_URL, json={"key": KEY}).status_code == 403
    assert viewer.delete(ACK_URL, params={"key": KEY}).status_code == 403
    # No token answers 401.
    assert anon.put(ACK_URL, json={"key": KEY}).status_code == 401
    assert anon.delete(ACK_URL, params={"key": KEY}).status_code == 401
    assert anon.get(URL).status_code == 401

    # --- The schema file ----------------------------------------------
    schema = json.loads(_OPENAPI.read_text())
    assert "/api/exceptions/ack" in schema["paths"]
    assert "put" in schema["paths"]["/api/exceptions/ack"]
    assert "delete" in schema["paths"]["/api/exceptions/ack"]
    get_parameters = {
        param["name"]
        for param in schema["paths"]["/api/exceptions"]["get"]["parameters"]
    }
    assert "include_snoozed" in get_parameters
    row_fields = schema["components"]["schemas"]["ExceptionRow"]["properties"]
    for field in (
        "acknowledged",
        "acknowledged_by_name",
        "acknowledged_at",
        "snooze_until",
    ):
        assert field in row_fields
    assert (
        "acknowledged"
        in schema["components"]["schemas"]["ExceptionCounts"]["properties"]
    )

    # --- Hostile values: no 'None' in the stockout detail, and an
    # absent display name never becomes a 500.  ---------------------
    # A ranked stockout row whose p_stockout is null answers a detail
    # without the chance part — never the literal 'None'.
    monkeypatch.setattr(
        exceptions,
        "stockout_ranked",
        lambda as_of=None: [
            {
                "category": "likely",
                "item_code": "ITEM-796-N",
                "site_code": "SITE-796-N",
                "p_stockout": None,
                "projected_stockout_on": date(2026, 10, 15),
                "as_of": STOCKOUT_AS_OF,
            }
        ],
    )
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    row = _row(response.json(), "stockout_likely:ITEM-796-N/SITE-796-N")
    assert row["detail"] == "projected stockout 2026-10-15"
    assert "None" not in row["detail"]
    monkeypatch.setattr(exceptions, "stockout_ranked", FakeStockoutSource())

    # An admin token whose identity has no display name answers 200,
    # not a 500: the name is stored null, the row reads acknowledged
    # with a null name, and the length check tolerates the absent
    # name.
    class NamelessAdmin(AuthUser):
        @property
        def display_name(self):
            return None

    def nameless_token_validate(token):
        if token == "nameless-admin-token":
            return NamelessAdmin(sub="nameless-admin", roles=["admin", "user"])
        return validate_token(token)

    monkeypatch.setattr("app.auth.dependencies.validate_token", nameless_token_validate)
    nameless = TestClient(app, headers={"Authorization": "Bearer nameless-admin-token"})
    stored = nameless.put(ACK_URL, json={"key": KEY, "note": "no display name"})
    assert stored.status_code == 200, stored.text
    stored = stored.json()
    assert stored["acknowledged_by"] == "nameless-admin"
    assert stored["acknowledged_by_name"] is None
    body = admin.get(URL).json()
    row = _row(body, KEY)
    assert row["acknowledged"] is True
    assert row["acknowledged_by_name"] is None
    assert body["counts"]["acknowledged"] == 1
    # Leave the list as the identity pass below expects it.
    assert admin.delete(ACK_URL, params={"key": KEY}).status_code == 204

    # --- Identity: a second pass after a full reload, as the
    # NON-ADMIN (role "user").  The page is booted the production
    # way: the token it would take from the URL hash as
    # #access_token=... is the development token (role "user") sent
    # as its Bearer token; GET /api/auth/config answers publicly and
    # GET /api/auth/me resolves the viewer.
    anon_reloaded = TestClient(app)
    viewer_reloaded = TestClient(
        app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"}
    )
    assert anon_reloaded.get("/api/auth/config").status_code == 200
    me = viewer_reloaded.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["authenticated"] is True
    assert "user" in me.json()["user"]["roles"]
    assert "admin" not in me.json()["user"]["roles"]

    # The triage page still renders for the viewer — the list route
    # is open to any signed-in user — with the same rows, while the
    # admin-only controls are absent for them: the ack PUT and the
    # ack DELETE answer 403, and no token is still a 401.
    baseline = admin.get(URL).json()
    viewer_response = viewer_reloaded.get(URL)
    assert viewer_response.status_code == 200, viewer_response.text
    assert viewer_response.json() == baseline
    assert viewer_reloaded.put(ACK_URL, json={"key": KEY}).status_code == 403
    assert viewer_reloaded.delete(ACK_URL, params={"key": KEY}).status_code == 403
    assert anon_reloaded.get(URL).status_code == 401
    assert anon_reloaded.put(ACK_URL, json={"key": KEY}).status_code == 401
