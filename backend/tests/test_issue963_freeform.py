"""Issue #963 proof: stored demand signals and their withdrawals.

The proof covers, in one test, against the test database (demand
events inserted for the test so that one item-site has a forecast and
one has none):

* demand events for ITEM-A — 2.0 every day for 401 days ending on
  2026-09-30, the shared default as-of date (13 constant 60.0
  history buckets, class smooth, method mean, 12 forecast buckets of
  60.0 the first of which runs 2026-10-01 through 2026-10-30) — and
  no demand events at all for ITEM-B;
* storing: an admin POST of a "promotion" of 500 units over two
  future dates answering 201, and the row listing with ``withdrawn``
  false and the admin's display name; the "event" and "deal" kinds
  accepted, and a quantity of exactly 1,000,000,000 accepted (only
  a quantity above it is refused);
* refusals, each storing nothing: a planner POST answering 403, no
  token answering 401, and each 422 — a kind other than promotion,
  deal or event; a quantity of 0, negative, NaN (not finite) or
  above 1,000,000,000; an empty, all-spaces or over-1,000-character
  reason; a start date on or before the as-of date; an end date
  before the start date or after the last day of the 12 forecast
  periods; an item-site with no statistical forecast — plus an extra
  field in the body answering 422;
* withdrawing: a withdrawal answering 201 with the stored
  withdrawal row carrying the signal's item-site and dates, the
  default list then omitting the signal, ``include_withdrawn=true``
  listing it once with ``withdrawn`` true and the withdrawal's
  reason and name, a second withdrawal answering 409, a withdrawal
  naming the withdrawal row answering 409, an unknown id answering
  404, and a planner withdrawal answering 403;
* a passed window: a signal whose ``end_date`` is on or before the
  as-of date, inserted directly, answering 409 on withdrawal, the
  row staying;
* the stored-row rules a plain CHECK cannot express: a withdrawal
  referencing another withdrawal refused, and a duplicate
  withdrawal of one signal refused by the partial unique index on
  ``withdraws_id``;
* two concurrent withdrawals: one uncommitted withdrawal insert
  held in a separate connection while the API withdraws the same
  signal — one row stored, the API answer 409;
* append-only: a direct UPDATE and a direct DELETE on the table
  each failing with SQLSTATE 55000;
* reading as a planner: the list readable, ``item_code`` without
  ``site_code`` (and vice versa) answering 422, newest-first order
  and the limit bounds (1 to 500);
* nothing else reads it: with signals stored,
  ``GET /api/forecast/by-key`` for the item-site answering the same
  ``forecast`` as before they were stored;
* the migration: one head, equal to ``CURRENT_HEAD`` from
  ``tests/migration_head`` (this proof names no revision of its
  own), and ``demand_signals`` with the stated columns.

The identity requirement is met at the API boundary (this issue
adds no frontend page): the read side of the whole proof runs as a
NON-ADMIN (role "user", the development token's ":user" form) and
asserts the admin-only controls (both POST routes) are denied with
403 while the list still renders with 200, a request without a
token still gets 401, and a second pass after a full reload (a
fresh TestClient on the plain development token, role "user" by
default) sees the same list and the same 403s.
"""

import os
import threading
import time
from datetime import date, timedelta

import psycopg2
import psycopg2.extras
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.main import app
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MIGRATIONS_DIR = os.path.join(_BACKEND_ROOT, "migrations")

# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
LAST = date(2026, 9, 30)
# The first seeded demand day, 401 days back (13 whole 30-day
# history buckets, the 11 earliest days outside the window).
HIST_START = LAST - timedelta(days=400)
# The last day of the 12 forecast periods at the as-of date: the
# as-of date plus one full horizon (12 x 30 days).
HORIZON_END = LAST + timedelta(days=360)

ITEM_A, SITE_A = "ITEM-963-A", "SITE-963-A"
ITEM_B, SITE_B = "ITEM-963-B", "SITE-963-B"

# The stored "promotion": two future dates inside the horizon.
SIGNAL_START = date(2026, 10, 5)
SIGNAL_END = date(2026, 10, 12)
# The "event" signal: the boundary quantity, exactly 1,000,000,000.
EVENT_START = date(2026, 11, 1)
EVENT_END = date(2026, 11, 15)
# The "deal" signal: the one the concurrent withdrawals race for.
DEAL_START = date(2026, 12, 1)
DEAL_END = date(2026, 12, 20)
# A passed window, inserted directly: it ended on the as-of date.
CLOSED_START = LAST - timedelta(days=10)
CLOSED_END = LAST
# A second passed window: it ended before the as-of date.
OLDER_START = LAST - timedelta(days=60)
OLDER_END = LAST - timedelta(days=30)

_ROW_FIELDS = {
    "id",
    "kind",
    "item_code",
    "site_code",
    "start_date",
    "end_date",
    "quantity",
    "reason",
    "created_by_name",
    "created_at",
    "withdrawn",
    "withdrawn_at",
    "withdrawn_by_name",
    "withdrawn_reason",
}


def _seed() -> None:
    """401 days of 2.0/day demand for ITEM-A, nothing for ITEM-B."""
    events = [
        (
            f"id-de-963-{offset}",
            f"de-963-{offset}",
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


def _count() -> int:
    """Every stored row of demand_signals (signals and withdrawals)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM demand_signals")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _withdrawal_count(signal_id: int) -> int:
    """The stored withdrawals of one signal id."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM demand_signals WHERE withdraws_id = %s",
                (signal_id,),
            )
            return cur.fetchone()[0]
    finally:
        conn.close()


def _insert_signal_direct(
    start: date,
    end: date,
    kind: str = "promotion",
    quantity: float = 50.0,
    reason: str = "direct insert",
    withdraws_id: int | None = None,
    item_code: str = ITEM_A,
    site_code: str = SITE_A,
) -> int:
    """A direct insert bypassing the API (for the stored-row checks)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO demand_signals "
                "(kind, item_code, site_code, start_date, end_date, "
                "quantity, withdraws_id, reason, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'direct-insert') "
                "RETURNING id",
                (
                    kind,
                    item_code,
                    site_code,
                    start,
                    end,
                    quantity,
                    withdraws_id,
                    reason,
                ),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


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


def _assert_422_sentence(resp) -> None:
    """A 422 whose ``detail`` is a sentence, not a validation list."""
    assert resp.status_code == 422, (resp.status_code, resp.text)
    detail = resp.json().get("detail")
    assert isinstance(detail, str) and detail.strip(), resp.text


def _signal_body(**overrides) -> dict:
    body = {
        "kind": "promotion",
        "item_code": ITEM_A,
        "site_code": SITE_A,
        "start_date": SIGNAL_START.isoformat(),
        "end_date": SIGNAL_END.isoformat(),
        "quantity": 500.0,
        "reason": "  promo spike  ",
    }
    body.update(overrides)
    return body


def test_issue963_freeform(client, admin_headers, user_headers):
    _seed()
    # The read side of the proof runs as a NON-ADMIN (role "user");
    # ``client`` is requested for its per-test table truncation only.
    admin = TestClient(app, headers=admin_headers)
    viewer = TestClient(app, headers=user_headers)
    anon = TestClient(app)

    # ------------------------------------------------------------------
    # Baseline: the forecast the signals must not change.
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

    resp = admin.get(
        "/api/forecast/by-key", params={"item_code": ITEM_A, "site_code": SITE_A}
    )
    assert resp.status_code == 200, resp.text
    by_key_before = resp.json()
    assert len(by_key_before["history"]) == 13
    assert by_key_before["history"][-1]["bucket_end"] == LAST.isoformat()

    # ------------------------------------------------------------------
    # Storing: a promotion of 500 units over two future dates.
    # ------------------------------------------------------------------
    resp = admin.post("/api/demand-signals", json=_signal_body())
    assert resp.status_code == 201, resp.text
    first = resp.json()
    assert set(first) == _ROW_FIELDS
    assert first["kind"] == "promotion"
    assert first["item_code"] == ITEM_A
    assert first["site_code"] == SITE_A
    assert first["start_date"] == SIGNAL_START.isoformat()
    assert first["end_date"] == SIGNAL_END.isoformat()
    assert first["quantity"] == 500.0
    assert first["reason"] == "promo spike"  # stored trimmed
    assert first["created_by_name"] == "admin"
    assert isinstance(first["created_at"], str) and first["created_at"]
    assert first["withdrawn"] is False
    assert first["withdrawn_at"] is None
    assert first["withdrawn_by_name"] is None
    assert first["withdrawn_reason"] is None
    first_id = first["id"]
    assert isinstance(first_id, int)

    # The "event" and "deal" kinds are accepted too; the boundary
    # quantity of exactly 1,000,000,000 is (only a quantity above it
    # is refused).
    resp = admin.post(
        "/api/demand-signals",
        json=_signal_body(
            kind="event",
            start_date=EVENT_START.isoformat(),
            end_date=EVENT_END.isoformat(),
            quantity=1_000_000_000.0,
            reason="holiday event",
        ),
    )
    assert resp.status_code == 201, resp.text
    event_id = resp.json()["id"]
    resp = admin.post(
        "/api/demand-signals",
        json=_signal_body(
            kind="deal",
            start_date=DEAL_START.isoformat(),
            end_date=DEAL_END.isoformat(),
            quantity=250.0,
            reason="bundle deal",
        ),
    )
    assert resp.status_code == 201, resp.text
    deal_id = resp.json()["id"]

    # The row lists with ``withdrawn`` false and the admin's display
    # name, readable by a planner.
    resp = viewer.get("/api/demand-signals")
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    assert [entry["id"] for entry in listed] == [deal_id, event_id, first_id]
    entry = next(entry for entry in listed if entry["id"] == first_id)
    assert entry["created_by_name"] == "admin"
    assert entry["withdrawn"] is False
    assert entry["withdrawn_at"] is None
    assert entry["quantity"] == 500.0

    # ------------------------------------------------------------------
    # Refusals: each stores nothing.
    # ------------------------------------------------------------------
    before = _count()

    resp = viewer.post("/api/demand-signals", json=_signal_body())
    assert resp.status_code == 403, resp.text
    assert _count() == before
    resp = anon.post("/api/demand-signals", json=_signal_body())
    assert resp.status_code == 401, resp.text
    assert _count() == before

    # Every 422: a sentence in ``detail``, nothing stored.
    for case in (
        _signal_body(kind="flash"),
        _signal_body(quantity=0.0),
        _signal_body(quantity=-5.0),
        _signal_body(quantity=1_000_000_000.0 + 1.0),
        _signal_body(reason=""),
        _signal_body(reason="     "),
        _signal_body(reason="x" * 1001),
        _signal_body(start_date=LAST.isoformat()),  # on the as-of date
        _signal_body(start_date=(LAST - timedelta(days=15)).isoformat()),
        _signal_body(end_date=(SIGNAL_START - timedelta(days=1)).isoformat()),
        _signal_body(end_date=(HORIZON_END + timedelta(days=1)).isoformat()),
        _signal_body(item_code=ITEM_B, site_code=SITE_B),
    ):
        resp = admin.post("/api/demand-signals", json=case)
        _assert_422_sentence(resp)
        assert _count() == before

    # A non-finite quantity (NaN) in the raw JSON body: still 422,
    # never a 500, nothing stored.
    resp = admin.post(
        "/api/demand-signals",
        content=(
            '{"kind": "promotion", "item_code": "ITEM-963-A", '
            '"site_code": "SITE-963-A", "start_date": "2026-10-05", '
            '"end_date": "2026-10-12", "quantity": NaN, '
            '"reason": "not finite"}'
        ),
        headers={"Content-Type": "application/json"},
    )
    _assert_422_sentence(resp)
    assert _count() == before

    # An extra field in the body answers 422, nothing stored.
    resp = admin.post("/api/demand-signals", json={**_signal_body(), "bogus": 1})
    assert resp.status_code == 422, resp.text
    assert _count() == before

    # ------------------------------------------------------------------
    # Withdrawing.
    # ------------------------------------------------------------------
    resp = admin.post(
        f"/api/demand-signals/{first_id}/withdraw", json={"reason": "promo cancelled"}
    )
    assert resp.status_code == 201, resp.text
    withdrawal = resp.json()
    assert set(withdrawal) == _ROW_FIELDS
    assert withdrawal["kind"] == "withdrawal"
    # The withdrawal carries the signal's item-site and dates.
    assert withdrawal["item_code"] == ITEM_A
    assert withdrawal["site_code"] == SITE_A
    assert withdrawal["start_date"] == SIGNAL_START.isoformat()
    assert withdrawal["end_date"] == SIGNAL_END.isoformat()
    assert withdrawal["quantity"] is None
    assert withdrawal["reason"] == "promo cancelled"
    assert withdrawal["created_by_name"] == "admin"
    withdrawal_id = withdrawal["id"]
    assert withdrawal_id != first_id

    # The default list omits the signal...
    resp = viewer.get("/api/demand-signals")
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    assert [entry["id"] for entry in listed] == [deal_id, event_id]
    # ...and include_withdrawn=true lists it once, withdrawn, with
    # the withdrawal's reason and name.
    resp = viewer.get("/api/demand-signals", params={"include_withdrawn": "true"})
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    withdrawn_entries = [entry for entry in listed if entry["id"] == first_id]
    assert len(listed) == 3
    assert len(withdrawn_entries) == 1
    withdrawn_entry = withdrawn_entries[0]
    assert withdrawn_entry["withdrawn"] is True
    assert withdrawn_entry["withdrawn_reason"] == "promo cancelled"
    assert withdrawn_entry["withdrawn_by_name"] == "admin"
    assert isinstance(withdrawn_entry["withdrawn_at"], str)

    # A second withdrawal answers 409, and so does a withdrawal
    # naming the withdrawal row; an unknown id answers 404 and a
    # planner withdrawal answers 403 — nothing stored.
    before = _count()
    resp = admin.post(
        f"/api/demand-signals/{first_id}/withdraw",
        json={"reason": "again"},
    )
    assert resp.status_code == 409, resp.text
    resp = admin.post(
        f"/api/demand-signals/{withdrawal_id}/withdraw",
        json={"reason": "of a withdrawal"},
    )
    assert resp.status_code == 409, resp.text
    resp = admin.post(
        "/api/demand-signals/999999999/withdraw", json={"reason": "unknown"}
    )
    assert resp.status_code == 404, resp.text
    resp = viewer.post(
        f"/api/demand-signals/{deal_id}/withdraw", json={"reason": "planner try"}
    )
    assert resp.status_code == 403, resp.text
    resp = anon.post(
        f"/api/demand-signals/{deal_id}/withdraw", json={"reason": "anon try"}
    )
    assert resp.status_code == 401, resp.text
    assert _count() == before

    # ------------------------------------------------------------------
    # A passed window: end_date on or before the as-of date.
    # ------------------------------------------------------------------
    closed_id = _insert_signal_direct(CLOSED_START, CLOSED_END)
    older_id = _insert_signal_direct(OLDER_START, OLDER_END)
    resp = admin.post(
        f"/api/demand-signals/{closed_id}/withdraw", json={"reason": "too late"}
    )
    assert resp.status_code == 409, resp.text
    resp = admin.post(
        f"/api/demand-signals/{older_id}/withdraw", json={"reason": "long gone"}
    )
    assert resp.status_code == 409, resp.text
    assert _withdrawal_count(closed_id) == 0
    assert _withdrawal_count(older_id) == 0

    # ------------------------------------------------------------------
    # The stored-row rules a plain CHECK cannot express.
    # ------------------------------------------------------------------
    # A withdrawal cannot reference another withdrawal...
    _expect_sql_error(
        "INSERT INTO demand_signals "
        "(kind, item_code, site_code, start_date, end_date, quantity, "
        "withdraws_id, reason, created_by) "
        "VALUES ('withdrawal', %s, %s, %s, %s, NULL, %s, 'r', 'direct')",
        (ITEM_A, SITE_A, SIGNAL_START, SIGNAL_END, withdrawal_id),
        "55000",
        "cannot withdraw another withdrawal",
    )
    # ...and cannot duplicate a non-withdrawal signal.
    _insert_signal_direct(
        EVENT_START,
        EVENT_END,
        kind="withdrawal",
        quantity=None,
        reason="direct withdrawal",
        withdraws_id=event_id,
    )
    assert _withdrawal_count(event_id) == 1
    _expect_sql_error(
        "INSERT INTO demand_signals "
        "(kind, item_code, site_code, start_date, end_date, quantity, "
        "withdraws_id, reason, created_by) "
        "VALUES ('withdrawal', %s, %s, %s, %s, NULL, %s, 'r', 'direct')",
        (ITEM_A, SITE_A, EVENT_START, EVENT_END, event_id),
        "23505",
        "unique constraint",
    )
    assert _withdrawal_count(event_id) == 1

    # ------------------------------------------------------------------
    # Two concurrent withdrawals store one row; the other answers 409.
    # ------------------------------------------------------------------
    before = _count()
    holder = get_connection()
    try:
        with holder.cursor() as cur:
            cur.execute(
                "INSERT INTO demand_signals "
                "(kind, item_code, site_code, start_date, end_date, "
                "quantity, withdraws_id, reason, created_by) "
                "VALUES ('withdrawal', %s, %s, %s, %s, NULL, %s, "
                "'concurrent first', 'dev-admin-1')",
                (ITEM_A, SITE_A, DEAL_START, DEAL_END, deal_id),
            )
        outcome: dict = {}

        def _api_withdraw():
            outcome["resp"] = admin.post(
                f"/api/demand-signals/{deal_id}/withdraw",
                json={"reason": "concurrent second"},
            )

        thread = threading.Thread(target=_api_withdraw)
        thread.start()
        # Let the API request pass its pre-checks and block on the
        # uncommitted row before the first withdrawal commits.
        time.sleep(0.75)
        holder.commit()
        thread.join(timeout=15)
        assert not thread.is_alive()
        resp = outcome["resp"]
        assert resp.status_code == 409, resp.text
        assert _withdrawal_count(deal_id) == 1
        assert _count() == before + 1
    finally:
        holder.close()

    # ------------------------------------------------------------------
    # Append-only: the guard trigger refuses every UPDATE and DELETE.
    # ------------------------------------------------------------------
    _expect_sql_error(
        "UPDATE demand_signals SET quantity = quantity + 1 WHERE id = %s",
        (first_id,),
        "55000",
        "immutable",
    )
    _expect_sql_error(
        "DELETE FROM demand_signals WHERE id = %s",
        (first_id,),
        "55000",
        "cannot be deleted",
    )

    # ------------------------------------------------------------------
    # Reading as a planner: the list renders; exactly one of the two
    # codes answers 422; newest first; the limit bounds.
    # ------------------------------------------------------------------
    resp = viewer.get("/api/demand-signals")
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    ids = [entry["id"] for entry in listed]
    assert ids == sorted(ids, reverse=True)
    assert len(set(ids)) == len(ids)
    # The item-site's own list: every stored signal of the pair.
    resp = viewer.get(
        "/api/demand-signals",
        params={"item_code": ITEM_A, "site_code": SITE_A},
    )
    assert resp.status_code == 200, resp.text
    assert [entry["id"] for entry in resp.json()] == ids

    resp = viewer.get("/api/demand-signals", params={"item_code": ITEM_A})
    _assert_422_sentence(resp)
    assert "together" in resp.json()["detail"]
    resp = viewer.get("/api/demand-signals", params={"site_code": SITE_A})
    _assert_422_sentence(resp)
    assert "together" in resp.json()["detail"]

    resp = viewer.get("/api/demand-signals", params={"limit": 0})
    assert resp.status_code == 422, resp.text
    resp = viewer.get("/api/demand-signals", params={"limit": 501})
    assert resp.status_code == 422, resp.text
    resp = viewer.get("/api/demand-signals", params={"limit": 2})
    assert resp.status_code == 200, resp.text
    assert [entry["id"] for entry in resp.json()] == ids[:2]

    # ------------------------------------------------------------------
    # The migration: one head, equal to the shared pin (no revision
    # named here), and the stated columns.
    # ------------------------------------------------------------------
    heads = ScriptDirectory(_MIGRATIONS_DIR).get_heads()
    assert list(heads) == [CURRENT_HEAD]
    assert ScriptDirectory(_MIGRATIONS_DIR).get_current_head() == CURRENT_HEAD

    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable, is_identity FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'demand_signals' "
                "ORDER BY ordinal_position"
            )
            columns = {
                row["column_name"]: (
                    row["data_type"],
                    row["character_maximum_length"],
                    row["is_nullable"],
                    row["is_identity"],
                )
                for row in cur.fetchall()
            }
        conn.commit()
    finally:
        conn.close()
    assert columns == {
        "id": ("integer", None, "NO", "YES"),
        "kind": ("character varying", 16, "NO", "NO"),
        "item_code": ("character varying", 64, "NO", "NO"),
        "site_code": ("character varying", 64, "NO", "NO"),
        "start_date": ("date", None, "NO", "NO"),
        "end_date": ("date", None, "NO", "NO"),
        "quantity": ("double precision", None, "YES", "NO"),
        "withdraws_id": ("integer", None, "YES", "NO"),
        "reason": ("character varying", 1000, "NO", "NO"),
        "created_by": ("character varying", 255, "NO", "NO"),
        "created_by_name": ("character varying", 255, "YES", "NO"),
        "created_at": ("timestamp with time zone", None, "NO", "NO"),
    }

    # ------------------------------------------------------------------
    # Identity, second pass after a full reload: a fresh TestClient
    # on the plain development token (role "user" by default) sees
    # the same list and the same admin-only refusals; nothing stored.
    # ------------------------------------------------------------------
    before = _count()
    reloaded = TestClient(app, headers={"Authorization": "Bearer dev-secret-token"})
    resp = reloaded.get("/api/demand-signals")
    assert resp.status_code == 200, resp.text
    assert [entry["id"] for entry in resp.json()] == ids
    resp = reloaded.post("/api/demand-signals", json=_signal_body())
    assert resp.status_code == 403, resp.text
    resp = reloaded.post(
        f"/api/demand-signals/{deal_id}/withdraw", json={"reason": "reload try"}
    )
    assert resp.status_code == 403, resp.text
    assert _count() == before
