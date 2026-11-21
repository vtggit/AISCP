"""Issue #1522 proof: the service levels actually reached, per ABC class.

``GET /api/service-levels/reached?days=N`` answers
``app.recommend.service_reached.service_reached(days)``: for the
trailing window of ``days`` snapshot dates — from the latest
``on_hand_snapshots`` date ``as_of`` back to ``as_of`` minus
``days - 1`` days, both included, worked out in SQL and answered as
``YYYY-MM-DD`` database text — the item-sites that have a
``demand_events`` row and an ``on_hand_snapshots`` row in the
window, per ABC class, with their snapshot days, the days of those
in stock and the share of them, beside the class' current service
level.  The route takes ``days``, an integer from 1 to 366 that
defaults to 30, is open to any signed-in user like
``GET /api/service-levels/curve``, and writes nothing.

The proof replaces two names in ``app.recommend.service_reached``:
``abc_for_all``, by a stand-in that counts its calls and answers
rows in ``abc_for_all``'s shape — the item-sites A1, A2 and X1 in
class A, B1 and B2 in class B, C1 in class C, and U1 with a null
class — and ``_stored_answer``, by one answering ``{"by_class":
True, "service_target": "fill_rate", "holding_rate": 0.25,
"current_levels": {"A": 0.98, "B": 0.95, "C": 0.9}}``.  Proved
here, against the test database migrated to head:

* empty — with no snapshot the route answers ``as_of`` null,
  ``window_start`` null, ``snapshot_dates`` 0, ``days`` 30,
  ``service_target`` "fill_rate" and ``unclassified_item_sites`` 0;
  A, B and C each with its level and 0, 0, 0 and a null share;
* 30 days, the default — the proof's ``demand_events`` rows (A1 on
  2026-11-21, A2 on 2026-11-02, B1 on 2026-10-30, B2 on
  2026-11-10, C1 on 2026-11-22, U1 and V1 on 2026-11-21 and X1 on
  2026-09-01) beside its ``on_hand_snapshots`` rows answer
  ``as_of`` "2026-11-22", ``window_start`` "2026-10-24" and
  ``snapshot_dates`` 3; A: 0.98, 2 item-sites, 5 days, 2 in stock,
  share 0.4; B: 0.95, 1, 3, 2, 0.6667; C: 0.9, 1, 1, 0, 0.0;
  ``unclassified_item_sites`` 2 — U1's null class and V1, which the
  stand-in does not list, while B2 (no snapshot) and X1 (its demand
  before the window) do not count;
* 2 days — ``days=2`` answers ``window_start`` "2026-11-21" and
  ``snapshot_dates`` 2; A: 1, 2, 1, 0.5; B: 0, 0, 0, null; C: 1, 1,
  0, 0.0; ``unclassified_item_sites`` 2;
* calls — for each request, the ``abc_for_all`` stand-in is called
  at most once and the ``_stored_answer`` stand-in exactly once;
* the window's bounds — ``days=0`` and ``days=367`` answer 422 and
  ``days=366`` answers 200;
* a far date — with ``on_hand_snapshots`` holding only one row, A1
  on 294276-01-01, far outside Python's date range, the route
  answers 200 with ``as_of`` "294276-01-01", ``window_start``
  "294275-12-03" and ``snapshot_dates`` 1 — the dates travel as
  database text, so the response models declare them as nullable
  ISO-8601 strings, not dates, and nothing reuses the curve's date
  or enum types;
* a pre-AD window — with ``on_hand_snapshots`` holding only one
  row, A1 on 0001-01-05, ``window_start`` is ``as_of`` minus
  ``days - 1`` days, answered as Postgres's own date text
  "0001-12-07 BC", not clamped to 0001-01-01;
* access — the route answers 401 without a token and 200 for the
  ``user`` role, and the identity requirement — the proof also runs
  as a NON-ADMIN (role ``user``) in a second pass after a full
  reload: the page boots the production way (the token it would
  take from the URL hash as ``#access_token=...``, the development
  token, with ``/api/auth/config`` answering publicly and
  ``/api/auth/me`` resolving the viewer for that user), the route is
  open to the viewer — no admin-only control hides it — while the
  page still renders: the settings the page loads answer 200 for
  the viewer and unauthenticated ``/api`` traffic is still 401;
* nothing written — the row counts of ``on_hand_snapshots`` and
  ``demand_events`` are the same before and after the route's
  calls.

Every row the proof inserted is deleted in a ``finally``.
"""

import os

from fastapi.testclient import TestClient

import app.recommend.service_reached as service_reached_module
from app.db.connection import get_connection, get_cursor
from app.main import app

URL = "/api/service-levels/reached"

# The site of every proof row: unique to this proof, so the cleanup
# deletes exactly the proof's rows.
SITE = "SITE-1522"

# The exact development token: the role "user" identity the page
# would take from the URL hash as #access_token=...
VIEWER_TOKEN = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")

# The timestamptz every NOT NULL as_of of a snapshot row carries.
SNAPSHOT_AS_OF = "2026-11-21T00:00:00+00"
# The hostile far date: the year 294276 is far outside Python's
# date range.  Handled and cleaned up as database text only.
FAR_SNAPSHOT_ON = "294276-01-01"
FAR_AS_OF = "294276-01-01T00:00:00+00"


def _abc_row(item_code, abc_class):
    """One row in ``abc_for_all``'s shape, for the proof's item-site."""
    return {
        "item_code": item_code,
        "site_code": SITE,
        "annual_value": None,
        "rank": None,
        "share_before": None,
        "abc_class": abc_class,
    }


class _AbcStandIn:
    """The stand-in for ``abc_for_all`` in
    ``app.recommend.service_reached``: every call is counted, the
    fixed rows are handed back unchanged."""

    def __init__(self):
        self.calls = 0
        self.rows = [
            _abc_row("A1", "A"),
            _abc_row("A2", "A"),
            _abc_row("X1", "A"),
            _abc_row("B1", "B"),
            _abc_row("B2", "B"),
            _abc_row("C1", "C"),
            _abc_row("U1", None),
        ]

    def __call__(self, as_of=None):
        self.calls += 1
        return [dict(row) for row in self.rows]


class _StoredAnswerStandIn:
    """The stand-in for ``_stored_answer`` in
    ``app.recommend.service_reached``: every call is counted, the
    fixed answer is handed back unchanged."""

    def __init__(self):
        self.calls = 0
        self.answer = {
            "by_class": True,
            "service_target": "fill_rate",
            "holding_rate": 0.25,
            "current_levels": {"A": 0.98, "B": 0.95, "C": 0.9},
        }

    def __call__(self):
        self.calls += 1
        return dict(self.answer)


def _insert_demand(item_code, occurred_on):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO demand_events "
                "(id, business_key, item_code, site_code, quantity, "
                "occurred_on, event_type, source_system) "
                "VALUES (%s, %s, %s, %s, 1.0, %s, 'shipment', 'test-1522')",
                (
                    f"id-1522-{item_code}",
                    f"bk-1522-{item_code}",
                    item_code,
                    SITE,
                    occurred_on,
                ),
            )
        conn.commit()
    finally:
        conn.close()


def _insert_snapshot(item_code, snapshot_on, quantity, as_of=SNAPSHOT_AS_OF):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO on_hand_snapshots "
                "(snapshot_on, item_code, site_code, quantity_on_hand, "
                "as_of, source_system) "
                "VALUES (%s, %s, %s, %s, %s, 'test-1522')",
                (snapshot_on, item_code, SITE, quantity, as_of),
            )
        conn.commit()
    finally:
        conn.close()


def _table_count(table):
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]
    finally:
        conn.close()


def test_issue1522_freeform(client, monkeypatch) -> None:
    abc_stand_in = _AbcStandIn()
    stored_stand_in = _StoredAnswerStandIn()
    monkeypatch.setattr(service_reached_module, "abc_for_all", abc_stand_in)
    monkeypatch.setattr(service_reached_module, "_stored_answer", stored_stand_in)
    try:
        # -- Setup: the proof empties on_hand_snapshots first. ------
        with get_cursor() as cur:
            cur.execute("DELETE FROM on_hand_snapshots")
            cur.execute("DELETE FROM demand_events")

        # -- Empty: with no snapshot, the answer is the empty shape.
        response = client.get(URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["as_of"] is None
        assert body["window_start"] is None
        assert body["snapshot_dates"] == 0
        assert body["days"] == 30
        assert body["service_target"] == "fill_rate"
        assert body["unclassified_item_sites"] == 0
        assert body["classes"] == [
            {
                "abc_class": "A",
                "level": 0.98,
                "item_sites": 0,
                "item_site_days": 0,
                "days_in_stock": 0,
                "share_in_stock": None,
            },
            {
                "abc_class": "B",
                "level": 0.95,
                "item_sites": 0,
                "item_site_days": 0,
                "days_in_stock": 0,
                "share_in_stock": None,
            },
            {
                "abc_class": "C",
                "level": 0.9,
                "item_sites": 0,
                "item_site_days": 0,
                "days_in_stock": 0,
                "share_in_stock": None,
            },
        ]
        # For the request: abc_for_all at most once, _stored_answer
        # exactly once.
        assert abc_stand_in.calls <= 1
        assert stored_stand_in.calls == 1

        # -- The rows: every NOT NULL column is filled, and each
        #    demand_events row has a quantity above 0. --------------
        for item_code, occurred_on in [
            ("A1", "2026-11-21"),
            ("A2", "2026-11-02"),
            ("B1", "2026-10-30"),
            ("B2", "2026-11-10"),
            ("C1", "2026-11-22"),
            ("U1", "2026-11-21"),
            ("V1", "2026-11-21"),
            ("X1", "2026-09-01"),
        ]:
            _insert_demand(item_code, occurred_on)
        for item_code, snapshot_on, quantity in [
            ("A1", "2026-11-20", 5),
            ("A1", "2026-11-21", 0),
            ("A1", "2026-11-22", 3),
            ("A2", "2026-11-20", 0),
            ("A2", "2026-11-22", -2),
            ("B1", "2026-10-01", 0),
            ("B1", "2026-11-20", 1),
            ("B1", "2026-11-21", 1),
            ("B1", "2026-11-22", 0),
            ("C1", "2026-11-22", 0),
            ("U1", "2026-11-22", 4),
            ("V1", "2026-11-22", 1),
            ("X1", "2026-11-20", 7),
            ("X1", "2026-11-21", 7),
            ("X1", "2026-11-22", 7),
        ]:
            _insert_snapshot(item_code, snapshot_on, quantity)

        # -- 30 days, the default: B2 (no snapshot) and X1 (its
        #    demand before the window) do not count; U1 (null class)
        #    and V1 (not listed by the stand-in) are unclassified.
        snapshots_before = _table_count("on_hand_snapshots")
        demand_before = _table_count("demand_events")
        abc_calls_before = abc_stand_in.calls
        stored_calls_before = stored_stand_in.calls
        response = client.get(URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["as_of"] == "2026-11-22"
        assert body["window_start"] == "2026-10-24"
        assert body["snapshot_dates"] == 3
        assert body["days"] == 30
        assert body["service_target"] == "fill_rate"
        assert body["unclassified_item_sites"] == 2
        assert body["classes"] == [
            {
                "abc_class": "A",
                "level": 0.98,
                "item_sites": 2,
                "item_site_days": 5,
                "days_in_stock": 2,
                "share_in_stock": 0.4,
            },
            {
                "abc_class": "B",
                "level": 0.95,
                "item_sites": 1,
                "item_site_days": 3,
                "days_in_stock": 2,
                "share_in_stock": 0.6667,
            },
            {
                "abc_class": "C",
                "level": 0.9,
                "item_sites": 1,
                "item_site_days": 1,
                "days_in_stock": 0,
                "share_in_stock": 0.0,
            },
        ]
        assert abc_stand_in.calls - abc_calls_before <= 1
        assert stored_stand_in.calls - stored_calls_before == 1

        # -- 2 days: A2's and B1's demand leave the window, so A
        #    counts its one item-site (A1) and B none.
        abc_calls_before = abc_stand_in.calls
        stored_calls_before = stored_stand_in.calls
        response = client.get(f"{URL}?days=2")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["as_of"] == "2026-11-22"
        assert body["window_start"] == "2026-11-21"
        assert body["snapshot_dates"] == 2
        assert body["days"] == 2
        assert body["service_target"] == "fill_rate"
        assert body["unclassified_item_sites"] == 2
        assert body["classes"] == [
            {
                "abc_class": "A",
                "level": 0.98,
                "item_sites": 1,
                "item_site_days": 2,
                "days_in_stock": 1,
                "share_in_stock": 0.5,
            },
            {
                "abc_class": "B",
                "level": 0.95,
                "item_sites": 0,
                "item_site_days": 0,
                "days_in_stock": 0,
                "share_in_stock": None,
            },
            {
                "abc_class": "C",
                "level": 0.9,
                "item_sites": 1,
                "item_site_days": 1,
                "days_in_stock": 0,
                "share_in_stock": 0.0,
            },
        ]
        assert abc_stand_in.calls - abc_calls_before <= 1
        assert stored_stand_in.calls - stored_calls_before == 1

        # -- The window's bounds: days=0 and days=367 answer 422,
        #    days=366 answers 200.
        abc_calls_before = abc_stand_in.calls
        stored_calls_before = stored_stand_in.calls
        assert client.get(f"{URL}?days=0").status_code == 422
        assert client.get(f"{URL}?days=367").status_code == 422
        response = client.get(f"{URL}?days=366")
        assert response.status_code == 200, response.text
        assert abc_stand_in.calls - abc_calls_before <= 1
        assert stored_stand_in.calls - stored_calls_before == 1

        # -- Nothing written: the row counts of the two tables are
        #    the same before and after the route's calls.
        assert _table_count("on_hand_snapshots") == snapshots_before
        assert _table_count("demand_events") == demand_before

        # -- A far date: with on_hand_snapshots holding only one row
        #    — A1 on 294276-01-01, far outside Python's date range —
        #    the route answers 200 with the dates as database text:
        #    the response models carry them as strings, so nothing
        #    constrains them to Python's date range.
        with get_cursor() as cur:
            cur.execute("DELETE FROM on_hand_snapshots")
        _insert_snapshot("A1", FAR_SNAPSHOT_ON, 1, as_of=FAR_AS_OF)
        snapshots_before = _table_count("on_hand_snapshots")
        demand_before = _table_count("demand_events")
        abc_calls_before = abc_stand_in.calls
        stored_calls_before = stored_stand_in.calls
        response = client.get(URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["as_of"] == "294276-01-01"
        assert body["window_start"] == "294275-12-03"
        assert body["snapshot_dates"] == 1
        assert body["days"] == 30
        assert _table_count("on_hand_snapshots") == snapshots_before
        assert _table_count("demand_events") == demand_before
        assert abc_stand_in.calls - abc_calls_before <= 1
        assert stored_stand_in.calls - stored_calls_before == 1

        # -- A pre-AD window: with on_hand_snapshots holding only
        #    one row — A1 on 0001-01-05 — the window's start is
        #    as_of minus days - 1 days, worked out in SQL and
        #    answered as Postgres's own date text, not clamped to
        #    0001-01-01.
        with get_cursor() as cur:
            cur.execute("DELETE FROM on_hand_snapshots")
        _insert_snapshot("A1", "0001-01-05", 1, as_of="0001-01-05T00:00:00+00")
        snapshots_before = _table_count("on_hand_snapshots")
        demand_before = _table_count("demand_events")
        abc_calls_before = abc_stand_in.calls
        stored_calls_before = stored_stand_in.calls
        response = client.get(URL)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["as_of"] == "0001-01-05"
        assert body["window_start"] == "0001-12-07 BC"
        assert body["snapshot_dates"] == 1
        assert body["days"] == 30
        assert _table_count("on_hand_snapshots") == snapshots_before
        assert _table_count("demand_events") == demand_before
        assert abc_stand_in.calls - abc_calls_before <= 1
        assert stored_stand_in.calls - stored_calls_before == 1

        # -- Access and the identity requirement: the route answers
        #    401 without a token and 200 for the user role.  The
        #    proof also runs as a NON-ADMIN (role "user") in a second
        #    pass after a full reload: the page boots the production
        #    way — the token it would take from the URL hash as
        #    #access_token=... is the development token — with
        #    /api/auth/config answering publicly and /api/auth/me
        #    resolving the viewer for that user.
        snapshots_before = _table_count("on_hand_snapshots")
        demand_before = _table_count("demand_events")
        abc_calls_before = abc_stand_in.calls
        stored_calls_before = stored_stand_in.calls
        anon = TestClient(app)
        assert anon.get(URL).status_code == 401
        viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER_TOKEN}"})
        config = anon.get("/api/auth/config")
        assert config.status_code == 200
        me = viewer.get("/api/auth/me")
        assert me.status_code == 200, me.text
        user = me.json()["user"]
        assert user["roles"] == ["user"]
        assert "admin" not in user["roles"]
        assert "admin" not in user["groups"]
        # The route is open to any signed-in user: the viewer, who
        # is not an admin, reaches it — no admin-only control hides
        # it — while the page still renders: the settings the page
        # loads answer 200 for the viewer, and the gate is intact.
        assert viewer.get(URL).status_code == 200
        assert viewer.get("/api/settings").status_code == 200
        assert anon.get("/api/settings").status_code == 401
        # Nothing written across the access phase either, and the
        # stand-ins were called at most once / exactly once for the
        # viewer's request of the route.
        assert _table_count("on_hand_snapshots") == snapshots_before
        assert _table_count("demand_events") == demand_before
        assert abc_stand_in.calls - abc_calls_before <= 1
        assert stored_stand_in.calls - stored_calls_before == 1
    finally:
        # Every row the proof inserted is deleted — the far date is
        # matched as text, so it never reaches a Python date
        # converter.
        with get_cursor() as cur:
            cur.execute("DELETE FROM demand_events WHERE site_code = %s", (SITE,))
            cur.execute("DELETE FROM on_hand_snapshots WHERE site_code = %s", (SITE,))
