"""Issue #348 proof: the effective forecast beside the statistical one.

The proof covers, in one test, against the test database (demand
events inserted for the test so that two item-sites have a forecast —
ITEM-348-A with 2.0 every day and ITEM-348-B with 1.0 every day, both
for 401 days ending on 2026-09-30, the shared default as-of date):

* with no override: every bucket's ``effective_quantity`` equals its
  ``quantity`` exactly and ``override_days`` is 0,
  ``total_effective_forecast`` equals ``total_forecast`` and
  ``overridden_buckets`` is 0, on both item-sites, list and by-key;
* an override stored through the API for the first period: that
  bucket's ``effective_quantity`` equals the override's quantity
  exactly, ``override_days`` 30, its ``quantity`` unchanged, the
  other eleven buckets untouched, ``overridden_buckets`` 1 and
  ``total_effective_forecast`` the hand sum; a second override for
  the same period: the later quantity applies; the override
  withdrawn: every bucket back to ``effective_quantity`` equal to
  ``quantity``; a zero override: ``effective_quantity`` 0 with
  ``quantity`` unchanged;
* buckets that have moved: with ``as_of`` 10 days before the
  default, the first bucket has ``override_days`` 20 and an
  ``effective_quantity`` of ten thirtieths of its ``quantity`` plus
  twenty thirtieths of the override, and the second bucket has
  ``override_days`` 10 and the matching sum, while at the default
  ``as_of`` the override's period is exactly the first bucket and
  the effective quantity is the override's quantity exactly;
* two counted overrides covering the same days, the second inserted
  directly with dates shifted by 15 days and a higher ``id``: on the
  shared days the later one applies, checked by the hand-computed
  ``effective_quantity`` of the affected buckets;
* a period whose current row is a withdrawal, with older override
  rows under it, counting for nothing, whatever its older rows are;
* the other item-site untouched by the first one's overrides, and
  the list's order unchanged by an override that would reorder it if
  the effective forecast counted;
* the list and the by-key route giving the same buckets for the same
  item-site; a history bucket still having exactly three fields;
* a snapshot captured while an override applies storing the
  statistical quantity, not the effective one;
* ``created_by_name`` equal to the token subject for a caller whose
  token has no username and no email (the shared
  ``app.auth.display_name.display_name`` helper, never null), stored
  by both ``create_override`` and ``withdraw_override``;
* the effective forecast read with one query per item-site
  (``effective_buckets``) and one query in total for the whole list
  (``effective_for_all``), and the original buckets never mutated.

The identity requirement is met at the API boundary: the read side
of the whole proof runs as a NON-ADMIN (the development token's
":user" form, role asserted via ``GET /api/auth/me``), the override
writes and the snapshot capture as an admin, and a request without a
token is refused with 401 on both forecast read routes.
"""

from datetime import date, timedelta

import psycopg2
import psycopg2.extras
import pytest
from fastapi.testclient import TestClient

from app.auth.models import AuthUser
from app.db.connection import get_connection
from app.forecast.overrides import (
    create_override,
    effective_buckets,
    withdraw_override,
)
from app.main import app

# The last demand day across all seeded events — the shared default
# as-of date (never later than today).
LAST = date(2026, 9, 30)
LAST_STR = LAST.isoformat()
# 401 days of demand per item-site (13 whole 30-day history buckets,
# the 11 earliest days outside the window).
HIST_START = LAST - timedelta(days=400)

ITEM_A, SITE_A = "ITEM-348-A", "SITE-348-A"
ITEM_B, SITE_B = "ITEM-348-B", "SITE-348-B"

# The forecast periods at the shared default as-of date: period k
# runs from as_of + 30(k-1) + 1 through as_of + 30k.
P1_START = LAST + timedelta(days=1)
P1_END = LAST + timedelta(days=30)
P2_START = LAST + timedelta(days=31)
P2_END = LAST + timedelta(days=60)
# The directly inserted override of the two-overrides check: the
# first period shifted by 15 days.
SHIFT_START = P1_START + timedelta(days=15)
SHIFT_END = P1_END + timedelta(days=15)
SHIFT_QTY = 36.0


def _seed() -> None:
    """401 days of demand for each item-site: 2.0/day for A, 1.0/day for B."""
    events = []
    for item, site, quantity in (
        (ITEM_A, SITE_A, 2.0),
        (ITEM_B, SITE_B, 1.0),
    ):
        for offset in range((LAST - HIST_START).days + 1):
            events.append(
                (
                    f"id-de-348-{item}-{offset}",
                    f"de-348-{item}-{offset}",
                    item,
                    site,
                    quantity,
                    (HIST_START + timedelta(days=offset)).isoformat(),
                    "shipment",
                    "test",
                )
            )
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


def _insert_direct(
    item_code: str,
    site_code: str,
    period_start: date,
    period_end: date,
    kind: str,
    quantity: float | None,
    reason: str,
) -> int:
    """A direct insert bypassing the API (for the stored-row checks)."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO forecast_overrides "
                "(item_code, site_code, period_start, period_end, kind, "
                "quantity, reason, created_by) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, 'direct-insert') "
                "RETURNING id",
                (
                    item_code,
                    site_code,
                    period_start,
                    period_end,
                    kind,
                    quantity,
                    reason,
                ),
            )
            row_id = cur.fetchone()[0]
        conn.commit()
        return row_id
    finally:
        conn.close()


def _stored_snapshot_rows(as_of: date) -> list[dict]:
    """The stored snapshot bucket rows for one as-of date."""
    conn = get_connection()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT item_code, site_code, bucket_index, quantity "
                "FROM forecast_snapshots WHERE as_of = %s "
                "ORDER BY item_code, site_code, bucket_index",
                (as_of,),
            )
            return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def test_issue348_freeform(client, admin_headers, user_headers):
    _seed()
    # The read side of the proof runs as a NON-ADMIN (role "user");
    # ``client`` is requested for its per-test table truncation only.
    admin = TestClient(app, headers=admin_headers)
    viewer = TestClient(app, headers=user_headers)
    anon = TestClient(app)

    def list_rows(**params) -> list[dict]:
        resp = viewer.get("/api/forecast", params=params or None)
        assert resp.status_code == 200, resp.text
        return resp.json()

    def by_key(item_code: str, site_code: str, **params) -> dict:
        resp = viewer.get(
            "/api/forecast/by-key",
            params={"item_code": item_code, "site_code": site_code, **params},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    def row(rows: list[dict], item_code: str, site_code: str) -> dict:
        return next(
            r
            for r in rows
            if (r["item_code"], r["site_code"]) == (item_code, site_code)
        )

    # The read routes refuse an anonymous caller.
    assert anon.get("/api/forecast").status_code == 401
    assert (
        anon.get(
            "/api/forecast/by-key",
            params={"item_code": ITEM_A, "site_code": SITE_A},
        ).status_code
        == 401
    )

    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["roles"] == ["user"]

    # ------------------------------------------------------------------
    # Baseline: two item-sites with a forecast, ordered by
    # total_forecast descending (720 then 360).
    # ------------------------------------------------------------------
    rows = list_rows()
    assert [(r["item_code"], r["site_code"]) for r in rows] == [
        (ITEM_A, SITE_A),
        (ITEM_B, SITE_B),
    ]
    assert [r["total_forecast"] for r in rows] == [720.0, 360.0]
    for r in rows:
        assert r["as_of"] == LAST_STR
        assert len(r["forecast"]) == 12

    # ------------------------------------------------------------------
    # No override: the effective forecast is the statistical forecast
    # everywhere, exactly.
    # ------------------------------------------------------------------
    for r in rows:
        for b in r["forecast"]:
            assert b["effective_quantity"] == b["quantity"]
            assert b["override_days"] == 0
        assert r["total_effective_forecast"] == r["total_forecast"]
        assert r["overridden_buckets"] == 0

    # The by-key route: the same buckets as the list, and a history
    # bucket still has exactly three fields.
    detail = by_key(ITEM_A, SITE_A)
    assert detail["forecast"] == row(rows, ITEM_A, SITE_A)["forecast"]
    assert (
        detail["total_effective_forecast"]
        == row(rows, ITEM_A, SITE_A)["total_effective_forecast"]
    )
    assert detail["overridden_buckets"] == 0
    for h in detail["history"]:
        assert set(h) == {"bucket_start", "bucket_end", "quantity"}
    assert len(detail["history"]) == 13

    # ------------------------------------------------------------------
    # An override stored through the API for the first period: its
    # bucket carries the override's quantity exactly, beside the
    # unchanged statistical quantity.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/forecast/overrides",
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "period_start": P1_START.isoformat(),
            "quantity": 42.0,
            "reason": "  348 promo  ",
        },
    )
    assert resp.status_code == 201, resp.text
    stored = resp.json()
    assert stored["created_by"] == "dev-admin-1"
    assert stored["created_by_name"] == "admin"
    assert stored["applies"] is True
    first_id = stored["id"]

    rows = list_rows()
    a = row(rows, ITEM_A, SITE_A)
    b1 = a["forecast"][0]
    assert b1["quantity"] == 60.0  # the statistical quantity, unchanged
    assert b1["effective_quantity"] == stored["quantity"]  # exactly
    assert b1["override_days"] == 30
    for b in a["forecast"][1:]:
        assert b["effective_quantity"] == b["quantity"]
        assert b["override_days"] == 0
    assert a["overridden_buckets"] == 1
    assert a["total_effective_forecast"] == sum(
        [stored["quantity"]] + [b["quantity"] for b in a["forecast"][1:]]
    )
    assert a["total_effective_forecast"] == 702.0

    # The other item-site is untouched by the first one's overrides.
    b_row = row(rows, ITEM_B, SITE_B)
    for b in b_row["forecast"]:
        assert b["effective_quantity"] == b["quantity"]
        assert b["override_days"] == 0
    assert b_row["overridden_buckets"] == 0
    assert b_row["total_effective_forecast"] == b_row["total_forecast"]

    # effective_buckets on the item-site: the given buckets come back
    # with the effective forecast added, the input untouched.
    input_buckets = [
        {"bucket_start": P1_START, "bucket_end": P1_END, "quantity": 60.0},
        {"bucket_start": P2_START, "bucket_end": P2_END, "quantity": 60.0},
    ]
    out = effective_buckets(ITEM_A, SITE_A, input_buckets)
    assert out[0]["effective_quantity"] == 42.0
    assert out[0]["override_days"] == 30
    assert out[1]["effective_quantity"] == 60.0
    assert out[1]["override_days"] == 0
    assert out[0]["quantity"] == 60.0
    assert input_buckets == [
        {"bucket_start": P1_START, "bucket_end": P1_END, "quantity": 60.0},
        {"bucket_start": P2_START, "bucket_end": P2_END, "quantity": 60.0},
    ]

    # The list and the by-key route give the same buckets for the
    # same item-site, with an override in force.
    detail = by_key(ITEM_A, SITE_A)
    assert detail["forecast"] == a["forecast"]
    assert detail["total_effective_forecast"] == a["total_effective_forecast"]
    assert detail["overridden_buckets"] == a["overridden_buckets"]

    # ------------------------------------------------------------------
    # A second override for the same period: the later quantity
    # applies.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/forecast/overrides",
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "period_start": P1_START.isoformat(),
            "quantity": 55.0,
            "reason": "promo confirmed",
        },
    )
    assert resp.status_code == 201, resp.text
    second = resp.json()
    assert second["id"] > first_id
    a = row(list_rows(), ITEM_A, SITE_A)
    assert a["forecast"][0]["effective_quantity"] == second["quantity"]
    assert a["forecast"][0]["override_days"] == 30
    assert a["total_effective_forecast"] == sum(
        [second["quantity"]] + [b["quantity"] for b in a["forecast"][1:]]
    )
    assert a["total_effective_forecast"] == 715.0

    # ------------------------------------------------------------------
    # The override withdrawn: every bucket back to the statistical
    # forecast.
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
    assert withdrawal["created_by_name"] == "admin"
    assert withdrawal["applies"] is False
    a = row(list_rows(), ITEM_A, SITE_A)
    for b in a["forecast"]:
        assert b["effective_quantity"] == b["quantity"]
        assert b["override_days"] == 0
    assert a["overridden_buckets"] == 0
    assert a["total_effective_forecast"] == a["total_forecast"]

    # ------------------------------------------------------------------
    # A zero override: effective quantity 0, the statistical quantity
    # unchanged.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/forecast/overrides",
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "period_start": P2_START.isoformat(),
            "quantity": 0,
            "reason": "nothing will sell",
        },
    )
    assert resp.status_code == 201, resp.text
    zero = resp.json()
    a = row(list_rows(), ITEM_A, SITE_A)
    assert a["forecast"][1]["effective_quantity"] == 0
    assert a["forecast"][1]["quantity"] == 60.0
    assert a["forecast"][1]["override_days"] == 30
    assert a["overridden_buckets"] == 1
    assert a["total_effective_forecast"] == sum(
        [a["forecast"][0]["quantity"], zero["quantity"]]
        + [b["quantity"] for b in a["forecast"][2:]]
    )
    assert a["total_effective_forecast"] == 660.0
    # Withdrawn again: back to the statistical forecast.
    resp = admin.post(
        "/api/forecast/overrides/withdrawals",
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "period_start": P2_START.isoformat(),
            "reason": "back on",
        },
    )
    assert resp.status_code == 201, resp.text
    a = row(list_rows(), ITEM_A, SITE_A)
    for b in a["forecast"]:
        assert b["effective_quantity"] == b["quantity"]
        assert b["override_days"] == 0
    assert a["total_effective_forecast"] == a["total_forecast"]

    # ------------------------------------------------------------------
    # Buckets that have moved: the override applies to its stored
    # dates whatever as_of is asked for.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/forecast/overrides",
        json={
            "item_code": ITEM_A,
            "site_code": SITE_A,
            "period_start": P1_START.isoformat(),
            "quantity": 90.0,
            "reason": "shifted promo",
        },
    )
    assert resp.status_code == 201, resp.text
    shifted_base = resp.json()
    assert shifted_base["quantity"] == 90.0

    shifted = list_rows(as_of=(LAST - timedelta(days=10)).isoformat())
    assert all(r["as_of"] == (LAST - timedelta(days=10)).isoformat() for r in shifted)
    a = row(shifted, ITEM_A, SITE_A)
    b1 = a["forecast"][0]  # LAST-9 .. LAST+20: 20 of the 30 override days
    q1 = b1["quantity"]
    assert b1["override_days"] == 20
    assert b1["effective_quantity"] == 10 * (q1 / 30) + 20 * (
        shifted_base["quantity"] / 30
    )
    b2 = a["forecast"][1]  # LAST+21 .. LAST+50: 10 of the 30 override days
    q2 = b2["quantity"]
    assert b2["override_days"] == 10
    assert b2["effective_quantity"] == 20 * (q2 / 30) + 10 * (
        shifted_base["quantity"] / 30
    )
    for b in a["forecast"][2:]:
        assert b["override_days"] == 0
        assert b["effective_quantity"] == b["quantity"]
    # The other item-site is untouched at the shifted as_of too.
    b_row = row(shifted, ITEM_B, SITE_B)
    for b in b_row["forecast"]:
        assert b["override_days"] == 0
        assert b["effective_quantity"] == b["quantity"]

    # At the default as_of the override's period is exactly the first
    # bucket: the effective quantity is the override's quantity
    # exactly.
    a = row(list_rows(), ITEM_A, SITE_A)
    assert a["forecast"][0]["effective_quantity"] == 90.0
    assert a["forecast"][0]["override_days"] == 30
    assert a["forecast"][0]["quantity"] == 60.0

    # ------------------------------------------------------------------
    # The list's order stays the statistical forecast's, even where an
    # override would reorder it if it counted; the other item-site is
    # untouched by the first one's overrides.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/forecast/overrides",
        json={
            "item_code": ITEM_B,
            "site_code": SITE_B,
            "period_start": P1_START.isoformat(),
            "quantity": 100000.0,
            "reason": "big promo at B",
        },
    )
    assert resp.status_code == 201, resp.text
    rows = list_rows()
    # Statistical order: A (720) before B (360). By the effective
    # totals it would be the other way round (750 vs 100330).
    assert [(r["item_code"], r["site_code"]) for r in rows] == [
        (ITEM_A, SITE_A),
        (ITEM_B, SITE_B),
    ]
    a = row(rows, ITEM_A, SITE_A)
    assert a["forecast"][0]["effective_quantity"] == 90.0  # A's own only
    for b in a["forecast"][1:]:
        assert b["override_days"] == 0
        assert b["effective_quantity"] == b["quantity"]
    assert a["overridden_buckets"] == 1
    b_row = row(rows, ITEM_B, SITE_B)
    assert b_row["forecast"][0]["effective_quantity"] == 100000.0
    assert b_row["forecast"][0]["override_days"] == 30

    # ------------------------------------------------------------------
    # Two counted overrides covering the same days: the one whose
    # current row has the higher id applies on the shared days.
    # ------------------------------------------------------------------
    shift_id = _insert_direct(
        ITEM_A, SITE_A, SHIFT_START, SHIFT_END, "override", SHIFT_QTY, "direct shift"
    )
    assert shift_id > shifted_base["id"]
    a = row(list_rows(), ITEM_A, SITE_A)
    b1 = a["forecast"][0]
    # Days 1..15 of the bucket: the 90.0 override; days 16..30: the
    # later 36.0 one, whose dates are shifted by 15 days.
    q1 = b1["quantity"]
    expected_b1 = 15 * (shifted_base["quantity"] / 30) + 15 * (SHIFT_QTY / 30)
    assert b1["override_days"] == 30
    assert b1["effective_quantity"] == expected_b1
    b2 = a["forecast"][1]
    q2 = b2["quantity"]
    # Days 31..45: the 36.0 override; the remaining 15 days: the
    # bucket's own share.
    expected_b2 = 15 * (q2 / 30) + 15 * (SHIFT_QTY / 30)
    assert b2["override_days"] == 15
    assert b2["effective_quantity"] == expected_b2
    for b in a["forecast"][2:]:
        assert b["override_days"] == 0
        assert b["effective_quantity"] == b["quantity"]
    assert a["overridden_buckets"] == 2
    assert a["total_effective_forecast"] == sum(
        [expected_b1, expected_b2] + [b["quantity"] for b in a["forecast"][2:]]
    )
    assert a["total_effective_forecast"] == 711.0
    # B is untouched by A's directly inserted override.
    b_row = row(list_rows(), ITEM_B, SITE_B)
    assert b_row["forecast"][0]["effective_quantity"] == 100000.0

    # The list and the by-key route give the same buckets, with two
    # overlapping overrides in force.
    detail = by_key(ITEM_A, SITE_A)
    assert detail["forecast"] == a["forecast"]
    assert detail["total_effective_forecast"] == a["total_effective_forecast"]
    assert detail["overridden_buckets"] == a["overridden_buckets"]

    # ------------------------------------------------------------------
    # A period whose current row is a withdrawal, with older override
    # rows under it, counts for nothing.
    # ------------------------------------------------------------------
    _insert_direct(
        ITEM_A, SITE_A, P1_START, P1_END, "withdrawal", None, "direct withdrawal"
    )
    a = row(list_rows(), ITEM_A, SITE_A)
    # The 90.0 override no longer applies: on the first bucket only
    # the shifted 36.0 override's 15 days count.
    q1 = a["forecast"][0]["quantity"]
    expected_b1 = 15 * (q1 / 30) + 15 * (SHIFT_QTY / 30)
    assert a["forecast"][0]["override_days"] == 15
    assert a["forecast"][0]["effective_quantity"] == expected_b1
    q2 = a["forecast"][1]["quantity"]
    assert a["forecast"][1]["override_days"] == 15
    assert a["forecast"][1]["effective_quantity"] == expected_b2
    for b in a["forecast"][2:]:
        assert b["override_days"] == 0
        assert b["effective_quantity"] == b["quantity"]
    assert a["overridden_buckets"] == 2
    # The override list agrees: no row of the period applies, and the
    # shifted period's row still does.
    resp = viewer.get(
        "/api/forecast/overrides",
        params={"item_code": ITEM_A, "site_code": SITE_A},
    )
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    p1_rows = [e for e in listed if e["period_start"] == P1_START.isoformat()]
    assert len(p1_rows) == 5  # 42.0, 55.0, the withdrawal, 90.0, the withdrawal
    assert all(e["applies"] is False for e in p1_rows)
    shift_rows = [e for e in listed if e["period_start"] == SHIFT_START.isoformat()]
    assert len(shift_rows) == 1
    assert shift_rows[0]["applies"] is True
    assert shift_rows[0]["quantity"] == SHIFT_QTY

    # ------------------------------------------------------------------
    # A snapshot captured while an override applies stores the
    # statistical quantity, not the effective one.
    # ------------------------------------------------------------------
    a = row(list_rows(), ITEM_A, SITE_A)
    live_effective = a["forecast"][0]["effective_quantity"]
    assert live_effective != 60.0  # the override is in force
    resp = admin.post("/api/forecast/snapshots", json={})
    assert resp.status_code == 200, resp.text
    snap = resp.json()
    assert snap["created"] is True
    assert snap["origin"] == "live"
    assert snap["as_of"] == LAST_STR
    assert snap["item_sites"] == 2
    assert snap["rows"] == 24
    stored = _stored_snapshot_rows(LAST)
    a_stored = [r for r in stored if r["item_code"] == ITEM_A]
    b_stored = [r for r in stored if r["item_code"] == ITEM_B]
    assert len(a_stored) == 12 and len(b_stored) == 12
    # Every stored bucket carries the statistical quantity, the first
    # among them — 60.0, not the live effective quantity.
    assert all(r["quantity"] == 60.0 for r in a_stored)
    assert all(r["quantity"] == 30.0 for r in b_stored)
    assert a_stored[0]["bucket_index"] == 1
    assert a_stored[0]["quantity"] != live_effective

    # ------------------------------------------------------------------
    # created_by_name: the shared display-name helper — the token's
    # username, else its email, else its subject — never null, on a
    # caller whose token has no username and no email.
    # ------------------------------------------------------------------
    bare = AuthUser(sub="bare-subject-348", username=None, email=None, roles=["admin"])
    stored = create_override(ITEM_B, SITE_B, P2_START, 7.5, "bare token override", bare)
    assert stored["created_by"] == "bare-subject-348"
    assert stored["created_by_name"] == "bare-subject-348"
    withdrawn = withdraw_override(
        ITEM_B, SITE_B, P2_START, "bare token withdrawal", bare
    )
    assert withdrawn["created_by"] == "bare-subject-348"
    assert withdrawn["created_by_name"] == "bare-subject-348"
    # The stored rows carry the name through the API too.
    resp = viewer.get(
        "/api/forecast/overrides",
        params={"item_code": ITEM_B, "site_code": SITE_B},
    )
    assert resp.status_code == 200, resp.text
    by_id = {e["id"]: e for e in resp.json()}
    assert by_id[stored["id"]]["created_by_name"] == "bare-subject-348"
    assert by_id[withdrawn["id"]]["created_by_name"] == "bare-subject-348"
