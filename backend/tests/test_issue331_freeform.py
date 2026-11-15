"""Issue #331 proof: forecast snapshots.

The proof covers, in one test, against the test database (demand
events inserted for the test, explicit as-of date 2026-08-31):

* a live capture (no ``as_of``) storing, for the shared default
  as-of date (2026-08-31 — the latest demand day), exactly the
  forecast buckets ``GET /api/forecast`` returns for that date:
  12 rows for each of the two item-sites that have a forecast
  (smooth, intermittent) and none for the ``no_demand`` item-site
  (its only demand is older than the 360-day stats window), with
  the same bucket dates, quantities and method — and again for a
  backfill capture with an explicit ``as_of`` (2026-07-31);
* a second capture for an already-snapshotted date storing nothing
  and returning ``created`` false with the first snapshot's
  ``origin`` (``live``, not the ``backfill`` the caller's explicit
  ``as_of`` would imply) and counts, the stored rows unchanged;
* a capture for a date whose forecast is empty (no item-site has a
  forecast for it) storing zero rows and answering ``created`` false
  — never true — with ``item_sites`` 0 and ``rows`` 0, leaving no
  snapshot behind;
* origin ``backfill`` for a capture with an ``as_of`` and origin
  ``live`` for one without;
* ``POST /api/forecast/snapshots`` (admin only): 401 without a
  token, 403 for a non-admin, 422 with the standard error envelope
  for a date later than today and for malformed dates (and other
  hostile bodies);
* ``GET /api/forecast/snapshots`` for any signed-in user: the
  stored snapshots newest as-of date first with their snapshot-level
  ``origin`` / ``item_sites`` / ``rows`` / ``created_at``, honouring
  ``limit`` (default 50, 1 to 500) and ``offset``;
* the stored snapshot record — one row per date in
  ``forecast_snapshot_records``, written in the same transaction as
  the bucket rows: the already-exists capture result carries exactly
  ``as_of``, ``origin``, ``created``, ``item_sites`` and ``rows``
  (no ``created_at``) read from that record, and the list answers
  the record's own ``created_at`` (the database's now() at insert
  time), not the bucket rows' application timestamp; the record
  itself is immutable and one per date;
* the table's CHECK constraint refusing any other origin (and any
  out-of-range bucket index), the unique constraint refusing a
  duplicate (as_of, item_code, site_code, bucket_index) row, and
  the one-snapshot-per-date guard refusing any insert for a date
  that already has a snapshot, plus the immutability of stored
  rows (no updates, no deletes).

The identity requirement is met at the API boundary (this issue
adds no frontend page): after the admin pass, the test makes the
same requests as a non-admin viewer (role "user", the development
token's "T:user" form) and asserts the admin-only control (POST)
is denied with 403 while the page's data (GET) still renders with
200, and a request without a token still gets 401.
"""

from datetime import UTC, date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.forecast.snapshots import capture_snapshot
from app.main import app

# The explicit as-of date the test seeds and captures for.
AS_OF = date(2026, 8, 31)
AS_OF_STR = AS_OF.isoformat()
# The earlier date the backfill capture targets.
BACKFILL_AS_OF = date(2026, 7, 31)
BACKFILL_AS_OF_STR = BACKFILL_AS_OF.isoformat()
# A date before the first seeded demand day (2025-07-16): every
# pair's 360-day window is empty, every pair classifies no_demand,
# and GET /api/forecast returns no row for it — an empty forecast.
EMPTY_AS_OF = date(2025, 1, 31)
EMPTY_AS_OF_STR = EMPTY_AS_OF.isoformat()

ITEM_SMOOTH, SITE_SMOOTH = "ITEM-331-S", "SITE-331-S"
ITEM_INTERMITTENT, SITE_INTERMITTENT = "ITEM-331-I", "SITE-331-I"
ITEM_NO_DEMAND, SITE_NO_DEMAND = "ITEM-331-N", "SITE-331-N"

FORECAST_PAIRS = {
    (ITEM_SMOOTH, SITE_SMOOTH),
    (ITEM_INTERMITTENT, SITE_INTERMITTENT),
}

ALL_COLUMNS = {
    "id",
    "as_of",
    "origin",
    "item_code",
    "site_code",
    "bucket_index",
    "bucket_start",
    "bucket_end",
    "quantity",
    "effective_quantity",
    "override_days",
    "signal_quantity",
    "method",
    "demand_class",
    "history_buckets",
    "created_at",
}

_SNAPSHOT_INSERT = (
    "INSERT INTO forecast_snapshots "
    "(id, as_of, origin, item_code, site_code, bucket_index, "
    "bucket_start, bucket_end, quantity, method, demand_class, "
    "history_buckets, created_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())"
)


def _seed() -> None:
    """Demand events for three item-sites, all on fixed dates.

    * smooth: 10.0 every day from AS_OF-400 through AS_OF (401
      events) — twelve full 30-day buckets of 300 each;
    * intermittent: 30.0 on AS_OF-5, AS_OF-95, AS_OF-185 and
      AS_OF-275 — non-zero buckets with gaps (ADI >= 1.32, CV2 0);
    * no_demand: a single 5.0 on AS_OF-400 — older than the 360-day
      stats window at both as-of dates, so it classifies no_demand
      and has no forecast.
    """
    events = []

    def add(item: str, site: str, day: date, quantity: float, tag: str) -> None:
        events.append(
            (
                f"id-de-331-{tag}",
                f"de-331-{tag}",
                item,
                site,
                quantity,
                day.isoformat(),
                "shipment",
                "test",
            )
        )

    for offset in range(401):
        add(
            ITEM_SMOOTH,
            SITE_SMOOTH,
            AS_OF - timedelta(days=400 - offset),
            10.0,
            f"s{offset}",
        )
    for index, days_before in enumerate((5, 95, 185, 275)):
        add(
            ITEM_INTERMITTENT,
            SITE_INTERMITTENT,
            AS_OF - timedelta(days=days_before),
            30.0,
            f"i{index}",
        )
    add(ITEM_NO_DEMAND, SITE_NO_DEMAND, AS_OF - timedelta(days=400), 5.0, "n0")

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                events,
            )
        conn.commit()
    finally:
        conn.close()


def _stored_rows(as_of: date) -> list[dict]:
    """Every stored snapshot row for one as-of date, deterministically ordered."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM forecast_snapshots WHERE as_of = %s "
                "ORDER BY item_code, site_code, bucket_index",
                (as_of,),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _utc(value: datetime | str) -> datetime:
    """A datetime or its ISO string, normalized to UTC for comparison."""
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.astimezone(UTC)


def _stored_record(as_of: date) -> dict | None:
    """The stored snapshot record for one as-of date, if any."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM forecast_snapshot_records WHERE as_of = %s", (as_of,)
            )
            row = cur.fetchone()
            return dict(row) if row is not None else None
    finally:
        conn.close()


def _expect_db_error(
    sql: str, params: tuple, pgcode: str, message_fragment: str | None = None
) -> None:
    """Run one statement and assert it fails with the given SQLSTATE."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(sql, params)
            except psycopg2.Error as exc:
                assert exc.pgcode == pgcode, (exc.pgcode, exc)
                if message_fragment is not None:
                    assert message_fragment in str(exc), str(exc)
                return
            raise AssertionError(f"expected a {pgcode} database error, none raised")
        conn.rollback()
    finally:
        conn.close()


def _assert_validation_envelope(resp) -> list[dict]:
    """A 422 in FastAPI's standard validation error envelope."""
    assert resp.status_code == 422, (resp.status_code, resp.text)
    body = resp.json()
    detail = body.get("detail")
    assert isinstance(detail, list) and detail, body
    for error in detail:
        assert isinstance(error, dict), body
        for key in ("type", "loc", "msg"):
            assert key in error, (key, body)
    return detail


def _assert_matches_forecast_api(client: TestClient, as_of: date, origin: str) -> None:
    """The stored rows for ``as_of`` mirror GET /api/forecast for it."""
    resp = client.get("/api/forecast", params={"as_of": as_of.isoformat()})
    assert resp.status_code == 200, resp.text
    api_rows = resp.json()
    assert {(row["item_code"], row["site_code"]) for row in api_rows} == FORECAST_PAIRS
    assert (ITEM_NO_DEMAND, SITE_NO_DEMAND) not in {
        (row["item_code"], row["site_code"]) for row in api_rows
    }

    stored = _stored_rows(as_of)
    assert len(stored) == 24, len(stored)
    assert {(row["item_code"], row["site_code"]) for row in stored} == FORECAST_PAIRS
    for row in stored:
        assert set(row) == ALL_COLUMNS, row
        assert row["as_of"] == as_of
        assert row["origin"] == origin
        assert 1 <= row["bucket_index"] <= 12

    for api_row in api_rows:
        pair = (api_row["item_code"], api_row["site_code"])
        mine = sorted(
            (row for row in stored if (row["item_code"], row["site_code"]) == pair),
            key=lambda row: row["bucket_index"],
        )
        assert [row["bucket_index"] for row in mine] == list(range(1, 13))
        for row, api_bucket in zip(mine, api_row["forecast"]):
            assert row["bucket_start"].isoformat() == api_bucket["bucket_start"]
            assert row["bucket_end"].isoformat() == api_bucket["bucket_end"]
            assert row["quantity"] == api_bucket["quantity"]
            assert row["method"] == api_row["method"]
            assert row["demand_class"] == api_row["demand_class"]
            assert row["history_buckets"] == api_row["history_buckets"]


def test_issue331_freeform(client, admin_headers, user_headers):
    _seed()
    admin = TestClient(app, headers=admin_headers)
    viewer = TestClient(app, headers=user_headers)
    anon = TestClient(app)

    # ------------------------------------------------------------------
    # AC-1/AC-3: a live capture (no as_of) stores 12 rows per
    # item-site that has a forecast, none for the no_demand one, with
    # the same buckets GET /api/forecast returns for that date.
    # ------------------------------------------------------------------

    # The JSON body is optional: no body at all captures the shared
    # default as-of date (the latest demand day, 2026-08-31) live.
    resp = admin.post("/api/forecast/snapshots")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "as_of": AS_OF_STR,
        "origin": "live",
        "created": True,
        "item_sites": 2,
        "rows": 24,
    }
    _assert_matches_forecast_api(admin, AS_OF, "live")

    # The capture stored the snapshot-level record alongside the
    # rows — the source of the snapshot-level fields.
    record = _stored_record(AS_OF)
    assert record is not None
    assert record["origin"] == "live"
    assert record["item_sites"] == 2
    assert record["rows"] == 24
    assert record["created_at"] is not None

    # A capture with an as_of given stores origin backfill.
    resp = admin.post("/api/forecast/snapshots", json={"as_of": BACKFILL_AS_OF_STR})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "as_of": BACKFILL_AS_OF_STR,
        "origin": "backfill",
        "created": True,
        "item_sites": 2,
        "rows": 24,
    }
    _assert_matches_forecast_api(admin, BACKFILL_AS_OF, "backfill")

    record = _stored_record(BACKFILL_AS_OF)
    assert record is not None
    assert record["origin"] == "backfill"
    assert record["item_sites"] == 2
    assert record["rows"] == 24
    assert record["created_at"] is not None

    # ------------------------------------------------------------------
    # AC-1/AC-3/AC-5: one snapshot per date — a second capture for an
    # already-snapshotted date stores nothing and returns the stored
    # snapshot's fields (origin live, not the backfill the caller's
    # explicit as_of would imply), the stored rows unchanged.
    # ------------------------------------------------------------------

    before = _stored_rows(AS_OF)
    record = _stored_record(AS_OF)
    result = capture_snapshot(as_of=AS_OF)
    # Review fix: the already-exists result carries exactly the
    # spec keys — no created_at — and its snapshot-level fields
    # come from the stored record.
    assert set(result) == {"as_of", "origin", "created", "item_sites", "rows"}
    assert result["as_of"] == AS_OF
    assert result["origin"] == record["origin"]
    assert result["item_sites"] == record["item_sites"]
    assert result["rows"] == record["rows"]
    assert result["origin"] == "live"
    assert result["created"] is False
    assert result["item_sites"] == 2
    assert result["rows"] == 24
    after = _stored_rows(AS_OF)
    assert after == before

    # Same through the API: a POST for the date answers 200 with the
    # stored snapshot's fields and created false.
    resp = admin.post("/api/forecast/snapshots", json={"as_of": AS_OF_STR})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "as_of": AS_OF_STR,
        "origin": "live",
        "created": False,
        "item_sites": 2,
        "rows": 24,
    }
    assert _stored_rows(AS_OF) == before

    # ------------------------------------------------------------------
    # Review fix: a capture for a date whose forecast is empty stores
    # zero rows and must answer created false — created is true only
    # when rows were stored.
    # ------------------------------------------------------------------

    resp = admin.get("/api/forecast", params={"as_of": EMPTY_AS_OF_STR})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    result = capture_snapshot(as_of=EMPTY_AS_OF)
    assert result == {
        "as_of": EMPTY_AS_OF,
        "origin": "backfill",
        "created": False,
        "item_sites": 0,
        "rows": 0,
    }
    assert _stored_rows(EMPTY_AS_OF) == []

    # Same through the API: zero rows stored, created false.
    resp = admin.post("/api/forecast/snapshots", json={"as_of": EMPTY_AS_OF_STR})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "as_of": EMPTY_AS_OF_STR,
        "origin": "backfill",
        "created": False,
        "item_sites": 0,
        "rows": 0,
    }
    assert _stored_rows(EMPTY_AS_OF) == []

    # The empty capture stored no snapshot: the date is still
    # unsnapshotted (the list below shows exactly the two real
    # snapshots) and a repeated capture still stores nothing.
    result = capture_snapshot(as_of=EMPTY_AS_OF)
    assert result["created"] is False
    assert result["item_sites"] == 0
    assert result["rows"] == 0
    assert _stored_rows(EMPTY_AS_OF) == []

    # ------------------------------------------------------------------
    # AC-2/AC-5: the list — newest as-of date first, snapshot-level
    # fields from the stored record, limit (1..500, default 50) and
    # offset.
    # ------------------------------------------------------------------

    resp = admin.get("/api/forecast/snapshots")
    assert resp.status_code == 200, resp.text
    entries = resp.json()
    assert len(entries) == 2
    assert [entry["as_of"] for entry in entries] == [AS_OF_STR, BACKFILL_AS_OF_STR]
    assert EMPTY_AS_OF_STR not in [entry["as_of"] for entry in entries]
    assert [entry["origin"] for entry in entries] == ["live", "backfill"]
    for entry in entries:
        assert set(entry) == {"as_of", "origin", "item_sites", "rows", "created_at"}
        assert entry["item_sites"] == 2
        assert entry["rows"] == 24
    live_rows = _stored_rows(AS_OF)
    backfill_rows = _stored_rows(BACKFILL_AS_OF)
    # Review fix: the snapshot-level fields come from the stored
    # record, not derived from the bucket rows — each entry matches
    # its record row exactly, and its created_at is the record's
    # own (the database's now() at insert time), not the bucket
    # rows' application timestamp.
    for entry, as_of, rows in (
        (entries[0], AS_OF, live_rows),
        (entries[1], BACKFILL_AS_OF, backfill_rows),
    ):
        record = _stored_record(as_of)
        assert entry["as_of"] == record["as_of"].isoformat()
        assert entry["origin"] == record["origin"]
        assert entry["item_sites"] == record["item_sites"]
        assert entry["rows"] == record["rows"]
        assert _utc(entry["created_at"]) == _utc(record["created_at"])
        assert _utc(entry["created_at"]) != _utc(rows[0]["created_at"])

    # One record per stored snapshot — and none for the empty
    # forecast date, which stored no snapshot.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM forecast_snapshot_records")
            assert cur.fetchone()[0] == 2
            cur.execute(
                "SELECT 1 FROM forecast_snapshot_records WHERE as_of = %s",
                (EMPTY_AS_OF,),
            )
            assert cur.fetchone() is None
    finally:
        conn.close()

    resp = admin.get("/api/forecast/snapshots", params={"limit": 1})
    assert resp.status_code == 200, resp.text
    assert [entry["as_of"] for entry in resp.json()] == [AS_OF_STR]
    resp = admin.get("/api/forecast/snapshots", params={"limit": 1, "offset": 1})
    assert resp.status_code == 200, resp.text
    assert [entry["as_of"] for entry in resp.json()] == [BACKFILL_AS_OF_STR]
    resp = admin.get("/api/forecast/snapshots", params={"offset": 1})
    assert resp.status_code == 200, resp.text
    assert [entry["as_of"] for entry in resp.json()] == [BACKFILL_AS_OF_STR]
    resp = admin.get("/api/forecast/snapshots", params={"limit": 500, "offset": 0})
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 2

    for bad in (
        {"limit": 0},
        {"limit": 501},
        {"offset": -1},
    ):
        _assert_validation_envelope(admin.get("/api/forecast/snapshots", params=bad))

    # ------------------------------------------------------------------
    # AC-2/AC-3: auth — POST is admin only, GET any signed-in user,
    # 401 without a token.
    # ------------------------------------------------------------------

    resp = anon.post("/api/forecast/snapshots", json={"as_of": AS_OF_STR})
    assert resp.status_code == 401, resp.text
    resp = anon.get("/api/forecast/snapshots")
    assert resp.status_code == 401, resp.text

    # ------------------------------------------------------------------
    # AC-6: hostile bodies answer 422 with the standard error
    # envelope, never 500.
    # ------------------------------------------------------------------

    for body in (
        {"as_of": "2026-12-01"},  # later than today
        {"as_of": "not-a-date"},  # malformed
        {"as_of": "2026-02-30"},  # malformed
        {"as_of": "99999999999999999999"},  # huge
        {"as_of": "a" * 1000},  # huge
        {"as_of": AS_OF_STR + "\x00"},  # NUL byte
        [1, 2, 3],  # not an object
        "just a string",  # not an object
    ):
        _assert_validation_envelope(admin.post("/api/forecast/snapshots", json=body))
    _assert_validation_envelope(
        admin.post(
            "/api/forecast/snapshots",
            content=b"\x00garbage",
            headers={"Content-Type": "application/json"},
        )
    )
    # A 422 stored nothing: the date keeps exactly its first snapshot.
    assert len(_stored_rows(date(2026, 12, 1))) == 0

    # ------------------------------------------------------------------
    # Identity: the same pass as a non-admin viewer (role "user") —
    # the admin-only control is denied, the page's data still renders.
    # ------------------------------------------------------------------

    resp = viewer.post("/api/forecast/snapshots", json={"as_of": AS_OF_STR})
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"detail": "Role 'admin' is required to perform this action."}
    resp = viewer.get("/api/forecast/snapshots")
    assert resp.status_code == 200, resp.text
    assert [entry["as_of"] for entry in resp.json()] == [AS_OF_STR, BACKFILL_AS_OF_STR]

    # ------------------------------------------------------------------
    # AC-3/AC-4: the table's own guards — CHECK constraint, unique
    # constraint, one snapshot per date, immutability.
    # ------------------------------------------------------------------

    # CHECK: any origin other than live / backfill is refused
    # (case-sensitive), as is an out-of-range bucket index.
    base = (
        "id-331-check",
        date(2026, 6, 30),
        None,
        "ITEM-331-X",
        "SITE-331-X",
        1,
        date(2026, 7, 1),
        date(2026, 7, 30),
        1.0,
        "mean",
        "smooth",
        12,
    )
    for bad_origin in ("stale", "LIVE", ""):
        _expect_db_error(
            _SNAPSHOT_INSERT,
            (*base[:2], bad_origin, *base[3:]),
            "23514",
            "ck_forecast_snapshots_origin",
        )
    _expect_db_error(
        _SNAPSHOT_INSERT,
        (*base[:2], "live", *base[3:5], 13, *base[6:]),
        "23514",
        "ck_forecast_snapshots_bucket_index",
    )

    # UNIQUE: a duplicate (as_of, item_code, site_code, bucket_index)
    # row is refused.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            first = (
                "id-331-uniq-a",
                date(2026, 5, 31),
                "live",
                "ITEM-331-X",
                "SITE-331-X",
                1,
                date(2026, 6, 1),
                date(2026, 6, 30),
                1.0,
                "mean",
                "smooth",
                12,
            )
            cur.execute(_SNAPSHOT_INSERT, first)
            try:
                cur.execute(_SNAPSHOT_INSERT, ("id-331-uniq-b", *first[1:]))
            except psycopg2.Error as exc:
                assert exc.pgcode == "23505", (exc.pgcode, exc)
                assert "uq_forecast_snapshots_as_of_item_site_bucket" in str(exc), str(
                    exc
                )
            else:
                raise AssertionError("the unique constraint did not fire")
    finally:
        conn.rollback()
        conn.close()

    # AC-4: inserting rows for a date that already has a snapshot is
    # refused — whatever the item-site, even with a fresh id.
    _expect_db_error(
        _SNAPSHOT_INSERT,
        (
            "id-331-second-snapshot",
            AS_OF,
            "backfill",
            "ITEM-331-X",
            "SITE-331-X",
            1,
            date(2026, 9, 1),
            date(2026, 9, 30),
            1.0,
            "mean",
            "smooth",
            12,
        ),
        "23505",
        f"a forecast snapshot already exists for as_of {AS_OF_STR}",
    )

    # The stored record is the snapshot's own record: one per date
    # (a second one for a snapshotted date is refused) and immutable
    # like its rows.
    _expect_db_error(
        "INSERT INTO forecast_snapshot_records "
        "(as_of, origin, item_sites, rows) VALUES (%s, %s, %s, %s)",
        (AS_OF, "backfill", 1, 12),
        "23505",
        f"a forecast snapshot already exists for as_of {AS_OF_STR}",
    )
    _expect_db_error(
        "UPDATE forecast_snapshot_records SET rows = rows + 1 " "WHERE as_of = %s",
        (AS_OF,),
        "55000",
        "immutable",
    )
    _expect_db_error(
        "DELETE FROM forecast_snapshot_records WHERE as_of = %s",
        (AS_OF,),
        "55000",
        "cannot be deleted",
    )

    # The first snapshot of a date is never updated or deleted.
    _expect_db_error(
        "UPDATE forecast_snapshots SET quantity = quantity + 1 " "WHERE as_of = %s",
        (AS_OF,),
        "55000",
        "immutable",
    )
    _expect_db_error(
        "DELETE FROM forecast_snapshots WHERE as_of = %s",
        (AS_OF,),
        "55000",
        "cannot be deleted",
    )
    assert _stored_rows(AS_OF) == before
    assert len(_stored_rows(BACKFILL_AS_OF)) == 24
