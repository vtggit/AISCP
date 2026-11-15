"""Issue #339 proof: planner forecast overrides and withdrawals.

The proof covers, in one test, against the test database (demand
events inserted for the test so that one item-site has a forecast and
one has none):

* demand events for ITEM-A — 2.0 every day for 401 days ending on
  2026-09-30, the shared default as-of date (13 constant 60.0
  history buckets, class smooth, method mean, 12 forecast buckets of
  60.0 the first of which runs 2026-10-01 through 2026-10-30) — and
  no demand events at all for ITEM-B;
* an override stored for the first forecast period: the period's two
  dates (2026-10-01 / 2026-10-30), the quantity, the trimmed reason,
  the caller's subject and display name, 201, and ``applies`` true in
  the list;
* a second override for the same period stored as a new row, the
  first row unchanged, and ``applies`` true on the second only
  (append-only, highest id wins);
* a zero quantity accepted;
* each refusal answering 422 with a sentence in ``detail`` and
  nothing stored: a blank reason, a reason of only spaces, a reason
  of 1,001 characters, a negative quantity, a non-finite quantity
  (NaN), a ``period_start`` one day inside a period, one in the
  past, one beyond the twelfth period, and an item-site with no
  forecast;
* a withdrawal stored as a new row with the stored period's dates,
  after which no row of that period has ``applies`` true, a second
  withdrawal answering 409, and a new override after the withdrawal
  applying again;
* a completed period: with an override row inserted directly for a
  period that ended before the as-of date, a withdrawal for it
  answers 409 and the row stays;
* order by id, not by time: two rows inserted directly for one
  period, the later ``id`` carrying the earlier ``created_at``, and
  the later ``id`` being the one that applies;
* the trigger refusing an UPDATE and a DELETE of a stored row, and
  the CHECK constraints refusing an unknown ``kind``, an ``override``
  with a null quantity, and a blank reason;
* 403 for a non-admin and 401 without a token on both POST routes,
  with nothing stored, and the list readable by a non-admin;
* the list's ``item_code`` and ``site_code`` filter (exactly one of
  the two refused with 422 and nothing returned), its newest-first
  order, and ``limit`` and ``offset``;
* ``GET /api/forecast`` and ``GET /api/forecast/by-key`` returning
  the same body before and after the overrides are stored.

The identity requirement is met at the API boundary (this issue adds
no frontend page): the read side of the whole proof runs as a
NON-ADMIN (role "user", the development token's ":user" form) and
asserts the admin-only controls (both POST routes) are denied with
403 while the list still renders with 200, a request without a token
still gets 401, and a second pass after a full reload (a fresh
TestClient on the plain development token, role "user" by default)
sees the same list and the same 403.
"""

from datetime import UTC, date, datetime, timedelta, timezone

import psycopg2
import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app

# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
LAST = date(2026, 9, 30)
# The first seeded demand day, 401 days back (13 whole 30-day
# history buckets, the 11 earliest days outside the window).
HIST_START = LAST - timedelta(days=400)

ITEM_A, SITE_A = "ITEM-339-A", "SITE-339-A"
ITEM_B, SITE_B = "ITEM-339-B", "SITE-339-B"

# The forecast periods at the shared default as-of date: period k
# runs from as_of + 30(k-1) + 1 through as_of + 30k.
P1_START = LAST + timedelta(days=1)  # 2026-10-01
P1_END = LAST + timedelta(days=30)  # 2026-10-30
P2_START = LAST + timedelta(days=31)
P2_END = LAST + timedelta(days=60)
P3_START = LAST + timedelta(days=61)
P3_END = LAST + timedelta(days=90)
# One day inside period 1, a past date, and the first day beyond the
# twelfth period (as_of + 361 days).
INSIDE_P1 = P1_START + timedelta(days=1)
PAST_START = LAST - timedelta(days=15)
BEYOND_HORIZON = LAST + timedelta(days=361)
# A completed period, stored directly: it ended before the as-of date.
CLOSED_START = LAST - timedelta(days=90)
CLOSED_END = LAST - timedelta(days=61)


def _seed() -> None:
    """401 days of 2.0/day demand for ITEM-A, nothing for ITEM-B."""
    events = [
        (
            f"id-de-339-{offset}",
            f"de-339-{offset}",
            ITEM_A,
            SITE_A,
            2.0,
            (HIST_START + timedelta(days=offset)).isoformat(),
            "shipment",
            "test",
        )
        for offset in range((LAST - HIST_START).days + 1)
    ]
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


def _stored_rows(
    item_code: str | None = None, site_code: str | None = None
) -> list[dict]:
    """The stored override rows (lowest id first), optionally one item-site's."""
    sql = "SELECT * FROM forecast_overrides"
    params: list[object] = []
    if item_code is not None:
        sql += " WHERE item_code = %s AND site_code = %s"
        params.extend([item_code, site_code])
    sql += " ORDER BY id"
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _count(item_code: str | None = None, site_code: str | None = None) -> int:
    return len(_stored_rows(item_code, site_code))


def _expect_sql_error(sql: str, params: tuple, sqlstate: str, message: str) -> None:
    """``sql`` must fail with the given SQLSTATE and a matching message."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
        conn.commit()
        conn.rollback()
        raise AssertionError(f"expected SQL to be refused: {sql}")
    except psycopg2.Error as exc:
        conn.rollback()
        assert exc.diag.sqlstate == sqlstate, (exc.diag.sqlstate, str(exc))
        assert message in str(exc), str(exc)
    finally:
        conn.close()


def _insert_direct(
    item_code: str,
    site_code: str,
    period_start: date,
    period_end: date,
    kind: str,
    quantity: float | None,
    reason: str,
    created_by: str = "direct-insert",
    created_at: datetime | None = None,
) -> int:
    """A direct insert bypassing the API (for the stored-row checks)."""
    if created_at is None:
        # Leave created_at out of the insert so the column's now()
        # server default applies (an explicit NULL would not).
        sql = (
            "INSERT INTO forecast_overrides "
            "(item_code, site_code, period_start, period_end, kind, "
            "quantity, reason, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
        )
        params = (
            item_code,
            site_code,
            period_start,
            period_end,
            kind,
            quantity,
            reason,
            created_by,
        )
    else:
        sql = (
            "INSERT INTO forecast_overrides "
            "(item_code, site_code, period_start, period_end, kind, "
            "quantity, reason, created_by, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id"
        )
        params = (
            item_code,
            site_code,
            period_start,
            period_end,
            kind,
            quantity,
            reason,
            created_by,
            created_at,
        )
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


def _assert_422_sentence(resp) -> None:
    """A 422 whose ``detail`` is a sentence, not a validation list."""
    assert resp.status_code == 422, (resp.status_code, resp.text)
    detail = resp.json().get("detail")
    assert isinstance(detail, str) and detail.strip(), resp.text


def _override_body(period_start: date, quantity, reason: str) -> dict:
    return {
        "item_code": ITEM_A,
        "site_code": SITE_A,
        "period_start": period_start.isoformat(),
        "quantity": quantity,
        "reason": reason,
    }


def test_issue339_freeform(client, admin_headers, user_headers):
    _seed()
    # The read side of the proof runs as a NON-ADMIN (role "user");
    # ``client`` is requested for its per-test table truncation only.
    admin = TestClient(app, headers=admin_headers)
    viewer = TestClient(app, headers=user_headers)
    anon = TestClient(app)

    # ------------------------------------------------------------------
    # Baseline: the forecast the overrides must not change.
    # ------------------------------------------------------------------
    resp = admin.get("/api/forecast")
    assert resp.status_code == 200, resp.text
    forecast_before = resp.json()
    assert len(forecast_before) == 1, forecast_before
    row = forecast_before[0]
    assert (row["item_code"], row["site_code"]) == (ITEM_A, SITE_A)
    assert row["as_of"] == LAST.isoformat()
    assert row["demand_class"] == "smooth"
    assert row["method"] == "mean"
    assert row["total_forecast"] == 720.0
    assert [bucket["quantity"] for bucket in row["forecast"]] == [60.0] * 12
    assert row["forecast"][0] == {
        "bucket_start": P1_START.isoformat(),
        "bucket_end": P1_END.isoformat(),
        "quantity": 60.0,
        "effective_quantity": 60.0,
        "signal_quantity": 0.0,
        "override_days": 0,
    }

    resp = admin.get(
        "/api/forecast/by-key", params={"item_code": ITEM_A, "site_code": SITE_A}
    )
    assert resp.status_code == 200, resp.text
    by_key_before = resp.json()
    assert len(by_key_before["history"]) == 13
    assert by_key_before["history"][-1]["bucket_end"] == LAST.isoformat()

    # ------------------------------------------------------------------
    # AC-1 / AC-2: an override stored for the first forecast period.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/forecast/overrides",
        json=_override_body(P1_START, 25.5, "  promo spike  "),
    )
    assert resp.status_code == 201, resp.text
    first = resp.json()
    assert first["item_code"] == ITEM_A
    assert first["site_code"] == SITE_A
    assert first["kind"] == "override"
    assert first["period_start"] == P1_START.isoformat()
    assert first["period_end"] == P1_END.isoformat()
    assert first["quantity"] == 25.5
    assert first["reason"] == "promo spike"  # stored trimmed
    assert first["created_by"] == "dev-admin-1"
    assert first["created_by_name"] == "admin"
    assert first["applies"] is True
    first_id = first["id"]
    assert isinstance(first["id"], int)
    assert first["created_at"]

    # ------------------------------------------------------------------
    # AC-1: a second override for the same period is a new row; the
    # first row is unchanged; applies is true on the second only.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/forecast/overrides",
        json=_override_body(P1_START, 30, "promo spike confirmed"),
    )
    assert resp.status_code == 201, resp.text
    second = resp.json()
    assert second["id"] > first_id
    assert second["period_start"] == P1_START.isoformat()
    assert second["period_end"] == P1_END.isoformat()
    assert second["quantity"] == 30

    rows = _stored_rows(ITEM_A, SITE_A)
    assert len(rows) == 2
    assert rows[0]["quantity"] == 25.5
    assert rows[0]["reason"] == "promo spike"
    assert rows[1]["id"] == second["id"]

    resp = viewer.get(
        "/api/forecast/overrides",
        params={"item_code": ITEM_A, "site_code": SITE_A},
    )
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    assert [entry["id"] for entry in listed] == [second["id"], first_id]
    assert [entry["applies"] for entry in listed] == [True, False]

    # ------------------------------------------------------------------
    # AC-1: a zero quantity is allowed.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/forecast/overrides",
        json=_override_body(P2_START, 0, "nothing will sell"),
    )
    assert resp.status_code == 201, resp.text
    zero = resp.json()
    assert zero["quantity"] == 0
    assert zero["period_start"] == P2_START.isoformat()
    assert zero["period_end"] == P2_END.isoformat()

    # ------------------------------------------------------------------
    # AC-1 / AC-2: every refusal answers 422 with a sentence and
    # stores nothing.
    # ------------------------------------------------------------------
    refusals = [
        _override_body(P1_START, 25.5, ""),  # blank reason
        _override_body(P1_START, 25.5, "    "),  # only spaces
        _override_body(P1_START, 25.5, "x" * 1001),  # over 1,000 chars
        _override_body(P1_START, -1, "negative quantity"),
        _override_body(INSIDE_P1, 25.5, "inside a period"),
        _override_body(PAST_START, 25.5, "period in the past"),
        _override_body(BEYOND_HORIZON, 25.5, "beyond the horizon"),
    ]
    no_forecast = _override_body(P1_START, 25.5, "no forecast here")
    no_forecast["item_code"] = ITEM_B
    no_forecast["site_code"] = SITE_B
    refusals.append(no_forecast)

    for body in refusals:
        before = _count()
        resp = admin.post("/api/forecast/overrides", json=body)
        _assert_422_sentence(resp)
        assert _count() == before, body

    # A non-finite quantity (NaN) in the raw JSON body: still 422,
    # never a 500.
    before = _count()
    resp = admin.post(
        "/api/forecast/overrides",
        content=(
            '{"item_code": "ITEM-339-A", "site_code": "SITE-339-A", '
            '"period_start": "2026-10-01", "quantity": NaN, '
            '"reason": "not finite"}'
        ),
        headers={"Content-Type": "application/json"},
    )
    _assert_422_sentence(resp)
    assert _count() == before

    # ------------------------------------------------------------------
    # AC-1: a withdrawal is a new row with the stored period's dates;
    # after it, no row of the period applies; a second withdrawal is
    # 409; a new override after the withdrawal applies again.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/forecast/overrides/withdrawals",
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "period_start": P1_START.isoformat(),
            "reason": "promo cancelled",
        },
    )
    assert resp.status_code == 201, resp.text
    withdrawal = resp.json()
    assert withdrawal["kind"] == "withdrawal"
    assert withdrawal["period_start"] == P1_START.isoformat()
    assert withdrawal["period_end"] == P1_END.isoformat()
    assert withdrawal["quantity"] is None
    assert withdrawal["reason"] == "promo cancelled"
    assert withdrawal["created_by"] == "dev-admin-1"
    assert withdrawal["applies"] is False
    rows = _stored_rows(ITEM_A, SITE_A)
    assert len(rows) == 4  # the two overrides, the zero, the withdrawal
    assert {entry["kind"] for entry in rows} == {"override", "withdrawal"}

    resp = viewer.get(
        "/api/forecast/overrides",
        params={"item_code": ITEM_A, "site_code": SITE_A},
    )
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    period_rows = [
        entry for entry in listed if entry["period_start"] == P1_START.isoformat()
    ]
    assert len(period_rows) == 3
    assert all(entry["applies"] is False for entry in period_rows)

    before = _count()
    resp = admin.post(
        "/api/forecast/overrides/withdrawals",
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "period_start": P1_START.isoformat(),
            "reason": "again",
        },
    )
    assert resp.status_code == 409, resp.text
    assert isinstance(resp.json()["detail"], str) and resp.json()["detail"].strip()
    assert _count() == before

    resp = admin.post(
        "/api/forecast/overrides",
        json=_override_body(P1_START, 40, "promo is back"),
    )
    assert resp.status_code == 201, resp.text
    revived = resp.json()
    assert revived["quantity"] == 40
    resp = viewer.get(
        "/api/forecast/overrides",
        params={"item_code": ITEM_A, "site_code": SITE_A},
    )
    listed = resp.json()
    period_rows = [
        entry for entry in listed if entry["period_start"] == P1_START.isoformat()
    ]
    applying = [entry for entry in period_rows if entry["applies"]]
    assert applying == [revived]

    # ------------------------------------------------------------------
    # AC-1: a completed period is closed — a directly inserted
    # override for a period that ended before the as-of date: the
    # withdrawal answers 409 and the row stays.
    # ------------------------------------------------------------------
    _insert_direct(ITEM_A, SITE_A, CLOSED_START, CLOSED_END, "override", 5, "old promo")
    resp = admin.post(
        "/api/forecast/overrides/withdrawals",
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "period_start": CLOSED_START.isoformat(),
            "reason": "too late",
        },
    )
    assert resp.status_code == 409, resp.text
    assert isinstance(resp.json()["detail"], str) and resp.json()["detail"].strip()
    closed_rows = [
        row
        for row in _stored_rows(ITEM_A, SITE_A)
        if row["period_start"] == CLOSED_START
    ]
    assert len(closed_rows) == 1
    assert closed_rows[0]["quantity"] == 5

    # ------------------------------------------------------------------
    # AC-1: order by id, not by time — the later id carries the
    # earlier created_at and is the one that applies.
    # ------------------------------------------------------------------
    now = datetime.now(UTC)
    out_of_order_id = _insert_direct(
        ITEM_A,
        SITE_A,
        P3_START,
        P3_END,
        "override",
        1,
        "ordering x",
        created_at=now + timedelta(hours=2),
    )
    later_id = _insert_direct(
        ITEM_A,
        SITE_A,
        P3_START,
        P3_END,
        "override",
        2,
        "ordering y",
        created_at=now - timedelta(hours=2),
    )
    assert later_id > out_of_order_id
    rows = _stored_rows(ITEM_A, SITE_A)
    by_id = {row["id"]: row for row in rows}
    assert by_id[later_id]["created_at"] < by_id[out_of_order_id]["created_at"]

    resp = viewer.get(
        "/api/forecast/overrides",
        params={"item_code": ITEM_A, "site_code": SITE_A},
    )
    listed = resp.json()
    period_rows = [
        entry for entry in listed if entry["period_start"] == P3_START.isoformat()
    ]
    applying = [entry for entry in period_rows if entry["applies"]]
    assert applying == [entry for entry in period_rows if entry["id"] == later_id]
    assert applying[0]["quantity"] == 2

    # ------------------------------------------------------------------
    # AC-1: append-only — the trigger refuses an UPDATE and a DELETE
    # of a stored row; the CHECK constraints refuse an unknown kind,
    # an override with a null quantity, and a blank reason.
    # ------------------------------------------------------------------
    _expect_sql_error(
        "UPDATE forecast_overrides SET reason = 'hacked' WHERE id = %s",
        (first_id,),
        "55000",
        "immutable",
    )
    _expect_sql_error(
        "DELETE FROM forecast_overrides WHERE id = %s",
        (first_id,),
        "55000",
        "cannot be deleted",
    )
    _expect_sql_error(
        "INSERT INTO forecast_overrides "
        "(item_code, site_code, period_start, period_end, kind, quantity, "
        "reason, created_by) VALUES (%s, %s, %s, %s, 'adjustment', 1, "
        "'unknown kind', 'direct')",
        (ITEM_A, SITE_A, P1_START, P1_END),
        "23514",
        "ck_forecast_overrides_kind",
    )
    _expect_sql_error(
        "INSERT INTO forecast_overrides "
        "(item_code, site_code, period_start, period_end, kind, quantity, "
        "reason, created_by) VALUES (%s, %s, %s, %s, 'override', NULL, "
        "'null quantity', 'direct')",
        (ITEM_A, SITE_A, P1_START, P1_END),
        "23514",
        "ck_forecast_overrides_quantity",
    )
    _expect_sql_error(
        "INSERT INTO forecast_overrides "
        "(item_code, site_code, period_start, period_end, kind, quantity, "
        "reason, created_by) VALUES (%s, %s, %s, %s, 'override', 1, "
        "'   ', 'direct')",
        (ITEM_A, SITE_A, P1_START, P1_END),
        "23514",
        "ck_forecast_overrides_reason",
    )
    assert _count(ITEM_A, SITE_A) == len(_stored_rows(ITEM_A, SITE_A))

    # ------------------------------------------------------------------
    # AC-2: authorization — 403 for a non-admin and 401 without a
    # token on both POST routes, nothing stored; the list is readable
    # by a non-admin.
    # ------------------------------------------------------------------
    before = _count()
    resp = viewer.post(
        "/api/forecast/overrides", json=_override_body(P1_START, 25.5, "viewer try")
    )
    assert resp.status_code == 403, resp.text
    resp = viewer.post(
        "/api/forecast/overrides/withdrawals",
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "period_start": P1_START.isoformat(),
            "reason": "viewer try",
        },
    )
    assert resp.status_code == 403, resp.text
    assert _count() == before

    resp = anon.post(
        "/api/forecast/overrides", json=_override_body(P1_START, 25.5, "anon try")
    )
    assert resp.status_code == 401, resp.text
    resp = anon.post(
        "/api/forecast/overrides/withdrawals",
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "period_start": P1_START.isoformat(),
            "reason": "anon try",
        },
    )
    assert resp.status_code == 401, resp.text
    assert _count() == before

    resp = viewer.get("/api/forecast/overrides")
    assert resp.status_code == 200, resp.text
    resp = anon.get("/api/forecast/overrides")
    assert resp.status_code == 401, resp.text

    # ------------------------------------------------------------------
    # AC-2: the list — the item-site filter (one code alone refused
    # with 422, nothing returned), newest-first order, limit/offset.
    # ------------------------------------------------------------------
    total = _count(ITEM_A, SITE_A)
    assert total >= 8

    resp = viewer.get(
        "/api/forecast/overrides",
        params={"item_code": ITEM_A, "site_code": SITE_A},
    )
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    assert len(listed) == total
    assert all(
        entry["item_code"] == ITEM_A and entry["site_code"] == SITE_A
        for entry in listed
    )
    ids = [entry["id"] for entry in listed]
    assert ids == sorted(ids, reverse=True)
    assert len(set(ids)) == len(ids)

    resp = viewer.get("/api/forecast/overrides", params={"item_code": ITEM_A})
    _assert_422_sentence(resp)
    assert "together" in resp.json()["detail"]
    resp = viewer.get("/api/forecast/overrides", params={"site_code": SITE_A})
    _assert_422_sentence(resp)
    assert "together" in resp.json()["detail"]

    resp = viewer.get(
        "/api/forecast/overrides",
        params={"item_code": ITEM_A, "site_code": SITE_A, "limit": 2},
    )
    assert resp.status_code == 200, resp.text
    page = resp.json()
    assert [entry["id"] for entry in page] == ids[:2]
    resp = viewer.get(
        "/api/forecast/overrides",
        params={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "limit": 2,
            "offset": 2,
        },
    )
    assert resp.status_code == 200, resp.text
    assert [entry["id"] for entry in resp.json()] == ids[2:4]
    resp = viewer.get(
        "/api/forecast/overrides",
        params={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "limit": 2,
            "offset": total - 2,
        },
    )
    assert resp.status_code == 200, resp.text
    assert [entry["id"] for entry in resp.json()] == ids[-2:]

    resp = viewer.get(
        "/api/forecast/overrides",
        params={"item_code": ITEM_A, "site_code": SITE_A, "limit": 0},
    )
    assert resp.status_code == 422, resp.text
    resp = viewer.get(
        "/api/forecast/overrides",
        params={"item_code": ITEM_A, "site_code": SITE_A, "limit": 1001},
    )
    assert resp.status_code == 422, resp.text

    # ------------------------------------------------------------------
    # AC-2 / rule OVR-4: the statistical forecast — everything the
    # effective forecast sits beside — is exactly what it was before,
    # whatever overrides are stored; only the effective fields may
    # differ.
    # ------------------------------------------------------------------

    def _statistical(row):
        """A forecast row minus the effective-forecast fields."""
        row = {
            key: value
            for key, value in row.items()
            if key not in ("total_effective_forecast", "overridden_buckets")
        }
        row["forecast"] = [
            {
                key: value
                for key, value in bucket.items()
                if key not in ("effective_quantity", "override_days")
            }
            for bucket in row["forecast"]
        ]
        return row

    resp = admin.get("/api/forecast")
    assert resp.status_code == 200, resp.text
    assert [_statistical(row) for row in resp.json()] == [
        _statistical(row) for row in forecast_before
    ]
    resp = admin.get(
        "/api/forecast/by-key", params={"item_code": ITEM_A, "site_code": SITE_A}
    )
    assert resp.status_code == 200, resp.text
    assert _statistical(resp.json()) == _statistical(by_key_before)

    # ------------------------------------------------------------------
    # Identity, second pass after a full reload: a fresh TestClient
    # on the plain development token (role "user" by default) sees
    # the same list and the same admin-only refusal; nothing stored.
    # ------------------------------------------------------------------
    before = _count()
    reloaded = TestClient(app, headers={"Authorization": "Bearer dev-secret-token"})
    resp = reloaded.get("/api/forecast/overrides")
    assert resp.status_code == 200, resp.text
    # ITEM-B stored no rows, so the unfiltered list is the
    # item-site's own list.
    assert [entry["id"] for entry in resp.json()] == ids
    resp = reloaded.post(
        "/api/forecast/overrides",
        json=_override_body(P1_START, 25.5, "reload try"),
    )
    assert resp.status_code == 403, resp.text
    assert _count() == before
