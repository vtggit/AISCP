"""Issue #809 proof: first-seen dates for exceptions.

AC-1 — the five sources of the triage list are replaced in the route
module with fakes that answer canned rows (the way the issue 796
proof does), while the new ``exception_first_seen`` store is the real
Postgres table the new migration 0032_exception_first_seen (revising
0031_exception_acks, the head and the only head) creates in the test
database.  Proved here:

  * the migration — the head is the 0032 revision (the module of the
    0032 file reports the head as its own revision, revising
    0031_exception_acks), it is the only head, the test database is
    migrated to it, and ``exception_first_seen`` has the stated
    columns (``key`` the String(255) primary key, ``first_seen_on``
    a not-null date);
  * before any capture — every row reads ``first_seen_on`` null and
    ``new`` false, and ``counts.new`` is 0;
  * the first capture — with the list's as-of date set to D, a
    capture inserts one row per key (``{"captured_on", "inserted"}``
    with the number of keys), after which every row reads
    ``first_seen_on`` D and ``new`` true and ``counts.new`` equals
    the row count; a second capture the same day inserts 0;
  * the next day — with the list's as-of date at D+1 and one extra
    exception, a capture inserts only the new key: only that row
    reads ``new`` true, the others keep ``first_seen_on`` D and read
    ``new`` false;
  * snoozed rows — the extra exception is snoozed until a date after
    the as-of date, so it is left out of the plain list (and the
    counts) — yet its key is captured too;
  * the as-of fallback — a list whose sources report no as-of date
    (only the two failure kinds answer, which never report one) is
    captured with the current UTC date (the pinned "now"), inserting
    only the one uncaptured key;
  * isolation — with the repository's ``list`` patched to raise, the
    route answers 200 with every row ``first_seen_on`` null and
    ``new`` false;
  * read-only GET — two GET calls insert nothing into
    ``exception_first_seen`` and read the table exactly once per
    call;
  * the schema file — ``backend/openapi.json`` adds only the two row
    fields (``first_seen_on``, ``new``) and the ``new`` count;
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

import importlib.util
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

import app.api.exceptions as exceptions
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

# The pinned current UTC time (the route's failure-window anchor).
NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
CUTOFF = NOW - timedelta(days=14)  # the 14-day failure window

# D: the canned sources' latest as-of (the response as-of date), the
# day the first capture runs.  D+1: the next day's list as-of.
D = date(2026, 10, 10)
D_NEXT = date(2026, 10, 11)
# After the next day's as-of (it snoozes the extra row out of the
# plain list) and not before the pinned current UTC date (it is a
# valid snooze date).
SNOOZE_AFTER_NEXT = date(2026, 10, 12)

# The extra exception the next day's list carries (snoozed, so it is
# out of the plain list while its key is still captured).
NEW_KEY = "expedite_late:PO-9/90"

RELEASE_ERROR_TEXT = (
    "Acumatica batch failed: HTTP 500 InternalServerError "
    "(request-id=8f3a2c, api-key=SECRET-809)"
)
PUSH_ERROR_TEXT = (
    "boom: the Acumatica ItemWarehouse write for ITEM-809-A at "
    "SITE-809-A failed (api-key=SECRET-809)"
)


class FakeDdmrpSource:
    """The fake for the route module's DDMRP source: answers the
    canned plan rows (the rows the DDMRP plan route builds) as of
    the canned date, once per call."""

    def __call__(self):
        return (
            date(2026, 10, 9),
            [
                {
                    "item_code": "ITEM-809-A",
                    "site_code": "SITE-809-A",
                    "on_hand": 0.0,
                    "on_hand_pct_of_red": 0.0,
                    "on_hand_status": "stocked_out",
                },
                {
                    "item_code": "ITEM-809-B",
                    "site_code": "SITE-809-B",
                    "on_hand": 5.0,
                    "on_hand_pct_of_red": 25.0,
                    "on_hand_status": "critical",
                },
                # The "low" and "ok" rows are not exceptions.
                {
                    "item_code": "ITEM-809-C",
                    "site_code": "SITE-809-C",
                    "on_hand": 12.0,
                    "on_hand_pct_of_red": 60.0,
                    "on_hand_status": "low",
                },
                {
                    "item_code": "ITEM-809-D",
                    "site_code": "SITE-809-D",
                    "on_hand": 40.0,
                    "on_hand_pct_of_red": 200.0,
                    "on_hand_status": "ok",
                },
                # The top of red is 0: the plan reports
                # on_hand_pct_of_red as null, and the detail keeps no
                # percentage part.
                {
                    "item_code": "ITEM-809-Z",
                    "site_code": "SITE-809-Z",
                    "on_hand": 0.0,
                    "on_hand_pct_of_red": None,
                    "on_hand_status": "stocked_out",
                },
            ],
        )


class FakeStockoutSource:
    """The fake for the route module's ``stockout_ranked``."""

    def __call__(self, as_of=None):
        return [
            {
                "category": "likely",
                "item_code": "ITEM-809-A",
                "site_code": "SITE-809-A",
                "p_stockout": 0.83,
                "projected_stockout_on": date(2026, 10, 12),
                "as_of": date(2026, 10, 9),
            },
            {
                "category": "at_risk",
                "item_code": "ITEM-809-E",
                "site_code": "SITE-809-E",
                "p_stockout": 0.45,
                "projected_stockout_on": date(2026, 10, 20),
                "as_of": date(2026, 10, 9),
            },
        ]


class FakeExpediteSource:
    """The fake for the route module's ``expedite_list``: the
    day-D lines, with the canned as-of (the latest of the sources)."""

    def __call__(self, as_of=None):
        return {
            "as_of": "2026-10-10",
            "stale_excluded": 0,
            "lines": [
                {
                    "order_nbr": "PO-1",
                    "line_nbr": 10,
                    "item_code": "ITEM-809-F",
                    "site_code": "SITE-809-F",
                    "open_units": 120.0,
                    "days_late": 3,
                    "reasons": ["late", "after_stockout"],
                },
                {
                    "order_nbr": "PO-2",
                    "line_nbr": 20,
                    "item_code": "ITEM-809-G",
                    "site_code": "SITE-809-G",
                    "open_units": 40.0,
                    "days_late": None,
                    "reasons": ["after_stockout"],
                },
                {
                    "order_nbr": "PO-3",
                    "line_nbr": 30,
                    "item_code": "ITEM-809-H",
                    "site_code": "SITE-809-H",
                    "open_units": 75.0,
                    "days_late": 2,
                    "reasons": ["late"],
                },
            ],
        }


class FakeNextDayExpediteSource(FakeExpediteSource):
    """The next day's expedite lines: the as-of moved to D+1 and one
    extra line (the extra exception the next day's capture sees)."""

    def __call__(self, as_of=None):
        payload = super().__call__(as_of)
        payload["as_of"] = "2026-10-11"
        payload["lines"].append(
            {
                "order_nbr": "PO-9",
                "line_nbr": 90,
                "item_code": "ITEM-809-N",
                "site_code": "SITE-809-N",
                "open_units": 60.0,
                "days_late": 1,
                "reasons": ["late"],
            }
        )
        return payload


class FakePlanReleasesRepo:
    """The fake for the route module's plan_releases repository: the
    canned rows span every case of the 14-day UTC window."""

    def list_newest(self):
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
                "id": 3,
                "outcome": "error",
                "vendor_code": "V-999",
                "supplier_code": "S-999",
                "updated_at": CUTOFF,  # exactly the cutoff: in
                "error": RELEASE_ERROR_TEXT,
            },
            {
                "id": 5,
                "outcome": "aborted",
                "vendor_code": None,
                "supplier_code": "S-809",
                "updated_at": NOW - timedelta(days=2),
                "error": RELEASE_ERROR_TEXT,
            },
        ]


class FakeParameterWritebacksRepo:
    """The fake for the route module's parameter_writebacks
    repository: the canned audit rows, with the stored error text
    that the public details must never expose."""

    def list(self, limit=100, offset=0, item_code=None, site_code=None):
        return [
            {
                "id": "pw-1",
                "item_code": "ITEM-809-A",
                "site_code": "SITE-809-A",
                "source_system": "acumatica",
                "outcome": "failed",
                "written_at": NOW - timedelta(days=3),
                "error": PUSH_ERROR_TEXT,
            },
            {
                "id": "pw-4",
                "item_code": "ITEM-809-D",
                "site_code": "SITE-809-D",
                "source_system": "odoo",
                "outcome": "failed",
                "written_at": CUTOFF.isoformat(),  # exactly: in
                "error": "Odoo: 500 internal (trace=abc809)",
            },
        ]


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(str(_BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND / "migrations"))
    return cfg


def _stored_count() -> int:
    """The rows stored in exception_first_seen (the real table)."""
    with get_cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM exception_first_seen")
        return cur.fetchone()["n"]


def _row(body, key):
    """The response row of one key."""
    return next(row for row in body["rows"] if row["key"] == key)


def test_issue809_freeform(monkeypatch, client):
    # The five sources in the route module are replaced with fakes
    # that answer canned rows, the way the issue 796 proof replaces
    # the ERP clients.  The exception_first_seen store is NOT faked:
    # it is the real Postgres table the new migration creates in the
    # test database (truncated between tests by the shared fixture).
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
    # The anchor of the failure window: the route's "current UTC
    # time" (two days after the canned sources' latest as-of).
    monkeypatch.setattr(exceptions, "_utcnow", lambda: NOW)

    # The one read of the first-seen table per call: counted, and
    # the delegate the isolation pass restores.
    repo = exceptions._exception_first_seen_repo
    real_list = repo.list
    reads = 0

    def counting_list():
        nonlocal reads
        reads += 1
        return real_list()

    repo.list = counting_list

    admin = TestClient(app, headers={"Authorization": f"Bearer {ADMIN_TOKEN}"})
    TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
    TestClient(app)

    # --- The migration: the 0032 revision, the only head. ----------
    heads = ScriptDirectory.from_config(_alembic_config()).get_heads()
    assert list(heads) == [CURRENT_HEAD]
    # The head is the 0032 revision: the module of the 0032 file
    # reports the head as its own revision, revising the 0031 one.
    migration_file = (
        _BACKEND / "migrations" / "versions" / "0032_exception_first_seen.py"
    )
    spec = importlib.util.spec_from_file_location(
        "issue809_head_migration", migration_file
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.revision == "0032_exception_first_seen"
    assert migration.down_revision == "0031_exception_acks"
    # The test database is migrated to the head.
    with get_cursor() as cur:
        cur.execute("SELECT version_num FROM alembic_version")
        assert [row["version_num"] for row in cur.fetchall()] == [CURRENT_HEAD]
    # The table has the stated columns.
    with get_cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'exception_first_seen' "
            "ORDER BY ordinal_position"
        )
        assert [
            (row["column_name"], row["data_type"], row["is_nullable"])
            for row in cur.fetchall()
        ] == [
            ("key", "character varying", "NO"),
            ("first_seen_on", "date", "NO"),
        ]
    # And the key is the primary key.
    with get_cursor() as cur:
        cur.execute(
            "SELECT a.attname FROM pg_index i "
            "JOIN pg_attribute a "
            "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
            "WHERE i.indrelid = 'exception_first_seen'::regclass "
            "AND i.indisprimary"
        )
        assert [row["attname"] for row in cur.fetchall()] == ["key"]

    # --- Before any capture: null and false everywhere. -------------
    assert _stored_count() == 0
    response = admin.get(URL)
    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {"as_of", "counts", "rows", "sources_failed"}
    assert body["as_of"] == D.isoformat()
    assert body["sources_failed"] == []
    assert len(body["rows"]) == 12
    assert body["counts"] == {"high": 9, "medium": 3, "acknowledged": 0, "new": 0}
    for row in body["rows"]:
        assert row["first_seen_on"] is None
        assert row["new"] is False
    # The read-only GET: the first GET inserted nothing and read the
    # table exactly once.
    assert _stored_count() == 0
    assert reads == 1
    again = admin.get(URL)
    assert again.status_code == 200, again.text
    assert again.json() == body
    # Two GET calls inserted nothing, one read per call.
    assert _stored_count() == 0
    assert reads == 2

    # --- The first capture: one row per key, as of D. ---------------
    captured = exceptions.capture_exception_first_seen()
    assert captured == {"captured_on": D.isoformat(), "inserted": 12}
    assert _stored_count() == 12
    body = admin.get(URL).json()
    assert body["as_of"] == D.isoformat()
    assert len(body["rows"]) == 12
    for row in body["rows"]:
        assert row["first_seen_on"] == D.isoformat()
        assert row["new"] is True
    # counts.new equals the row count.
    assert body["counts"] == {"high": 9, "medium": 3, "acknowledged": 0, "new": 12}
    assert body["counts"]["new"] == len(body["rows"])

    # A second capture the same day inserts nothing.
    again = exceptions.capture_exception_first_seen()
    assert again == {"captured_on": D.isoformat(), "inserted": 0}
    assert _stored_count() == 12

    # --- The next day: as-of D+1, one extra (snoozed) exception. ----
    monkeypatch.setattr(exceptions, "expedite_list", FakeNextDayExpediteSource())
    # The extra row is snoozed until a date after the as-of date: it
    # is out of the plain list while its key must still be captured.
    snoozed = admin.put(
        ACK_URL,
        json={"key": NEW_KEY, "snooze_until": SNOOZE_AFTER_NEXT.isoformat()},
    )
    assert snoozed.status_code == 200, snoozed.text
    captured = exceptions.capture_exception_first_seen()
    # Only the new key: the twelve day-D keys keep their stored date.
    assert captured == {"captured_on": D_NEXT.isoformat(), "inserted": 1}
    assert _stored_count() == 13
    body = admin.get(URL).json()
    assert body["as_of"] == D_NEXT.isoformat()
    # The snoozed row is left out of the plain list and the counts.
    assert all(row["key"] != NEW_KEY for row in body["rows"])
    assert len(body["rows"]) == 12
    # The others keep first_seen_on D and read new false.
    for row in body["rows"]:
        assert row["first_seen_on"] == D.isoformat()
        assert row["new"] is False
    assert body["counts"]["new"] == 0
    # include_snoozed=true shows the extra row: it is the only new
    # one, first-seen D+1.
    shown = admin.get(URL, params={"include_snoozed": "true"}).json()
    assert len(shown["rows"]) == 13
    row = _row(shown, NEW_KEY)
    assert row["first_seen_on"] == D_NEXT.isoformat()
    assert row["new"] is True
    assert row["snooze_until"] == SNOOZE_AFTER_NEXT.isoformat()
    assert shown["counts"]["new"] == 1
    for other in shown["rows"]:
        if other["key"] != NEW_KEY:
            assert other["first_seen_on"] == D.isoformat()
            assert other["new"] is False
    # Leave the list clean: remove the snooze.
    assert admin.delete(ACK_URL, params={"key": NEW_KEY}).status_code == 204

    # --- The as-of fallback: no source reports an as-of date. ------
    def _ddmrp_down():
        raise RuntimeError("the DDMRP plan is down")

    class NoStockout:
        def __call__(self, as_of=None):
            return []

    class NoAsOfExpedite:
        def __call__(self, as_of=None):
            return {"lines": []}

    class NextPushesRepo(FakeParameterWritebacksRepo):
        # One more fresh failed push: the only uncaptured key of the
        # as-of-less list (the failure kinds never report an as-of).
        def list(self, limit=100, offset=0, item_code=None, site_code=None):
            rows = [
                dict(row)
                for row in super().list(
                    limit=limit,
                    offset=offset,
                    item_code=item_code,
                    site_code=site_code,
                )
            ]
            rows.append(
                {
                    "id": "pw-9",
                    "item_code": "ITEM-809-N",
                    "site_code": "SITE-809-N",
                    "source_system": "acumatica",
                    "outcome": "failed",
                    "written_at": NOW - timedelta(days=1),
                    "error": PUSH_ERROR_TEXT,
                }
            )
            return rows

    monkeypatch.setattr(exceptions, "_ddmrp_source", _ddmrp_down)
    monkeypatch.setattr(exceptions, "stockout_ranked", NoStockout())
    monkeypatch.setattr(exceptions, "expedite_list", NoAsOfExpedite())
    monkeypatch.setattr(exceptions, "_parameter_writebacks_repo", NextPushesRepo())
    body = admin.get(URL).json()
    assert body["as_of"] is None
    assert body["sources_failed"] == ["ddmrp"]
    assert len(body["rows"]) == 5
    # Every row reads new false while the as-of date is null.
    for row in body["rows"]:
        assert row["new"] is False
    captured = exceptions.capture_exception_first_seen()
    # The list reports no as-of: the capture stores the current UTC
    # date (the pinned "now"), inserting only the new push key.
    assert captured == {"captured_on": NOW.date().isoformat(), "inserted": 1}
    body = admin.get(URL).json()
    assert _row(body, "erp_push_failed:pw-9")["first_seen_on"] == NOW.date().isoformat()
    for row in body["rows"]:
        assert row["new"] is False  # the as-of is null: nothing is new
    monkeypatch.setattr(exceptions, "_ddmrp_source", FakeDdmrpSource())
    monkeypatch.setattr(exceptions, "stockout_ranked", FakeStockoutSource())
    monkeypatch.setattr(exceptions, "expedite_list", FakeExpediteSource())
    monkeypatch.setattr(
        exceptions, "_parameter_writebacks_repo", FakeParameterWritebacksRepo()
    )

    # --- Isolation: a failing first-seen read never breaks the
    # list — every row reads never captured, the route answers 200.
    def _first_seen_down():
        raise RuntimeError("the first-seen store is down")

    repo.list = _first_seen_down
    try:
        iso = admin.get(URL)
        assert iso.status_code == 200, iso.text
        iso_body = iso.json()
        assert iso_body["counts"]["new"] == 0
        for row in iso_body["rows"]:
            assert row["first_seen_on"] is None
            assert row["new"] is False
    finally:
        repo.list = counting_list

    # --- The schema file adds only the two row fields and the
    # count. ----------------------------------------------------------
    schema = json.loads(_OPENAPI.read_text())
    row_fields = schema["components"]["schemas"]["ExceptionRow"]["properties"]
    assert "first_seen_on" in row_fields
    assert "new" in row_fields
    assert row_fields["first_seen_on"]["anyOf"] == [
        {"type": "string", "format": "date"},
        {"type": "null"},
    ]
    assert row_fields["new"] == {"type": "boolean", "title": "New", "default": False}
    count_fields = schema["components"]["schemas"]["ExceptionCounts"]["properties"]
    assert "new" in count_fields
    assert count_fields["new"] == {"type": "integer", "title": "New"}

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
    assert viewer_reloaded.put(ACK_URL, json={"key": NEW_KEY}).status_code == 403
    assert viewer_reloaded.delete(ACK_URL, params={"key": NEW_KEY}).status_code == 403
    assert anon_reloaded.get(URL).status_code == 401
    assert anon_reloaded.put(ACK_URL, json={"key": NEW_KEY}).status_code == 401
