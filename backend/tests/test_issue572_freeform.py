"""Issue #572 proof: feed status — freshness of the five feed tables.

``app.feed.status.feed_status()`` reports, for each of the five OData
feed tables (``item_sites``, ``demand_events``, ``on_hand``,
``open_purchase_lines``, ``lead_time_observations``), the newest
``synced_at`` and the row count, plus the newest
``demand_events.occurred_on`` as the data horizon.  It reads through
the two module-level helpers ``_table_stats`` and ``_data_through`` —
the only database seams, both going through
``app.db.connection.get_cursor`` — and writes nothing.  A table name
comes only from the fixed five-name list, never from input.
``GET /api/feed/status`` serves that dict behind the app's
``dependencies=auth_gate`` (401 without a token, 200 for any signed-in
role; the route carries no admin-only control).

Proven here, in order, with ``_table_stats`` and ``_data_through``
monkeypatched (no database):

  * mixed timestamps: ``item_sites`` at 2026-10-21T07:01:05Z with 774
    rows, ``demand_events`` at 07:01:17Z with 23435 rows, ``on_hand``
    at 07:01:20Z with 1520 rows, ``open_purchase_lines`` at 07:01:23Z
    with 89 rows, ``lead_time_observations`` never synced (null) with 0
    rows, and data through 2026-06-30 — ``feed_status()`` gives
    ``synced_at`` "2026-10-21T07:01:23+00:00" (the newest),
    ``oldest_synced_at`` "2026-10-21T07:01:05+00:00" (the oldest
    non-null — the gap between the two shows a partial sync),
    ``data_through`` "2026-06-30", and the five tables in order with
    their rows, the null table showing ``synced_at`` null;
  * empty feed: every table null with 0 rows gives ``synced_at``,
    ``oldest_synced_at`` and ``data_through`` all null;
  * endpoint: ``GET /api/feed/status`` gives 401 without a token and
    200 with the planner token the existing API tests use (the bare
    development token the suite's ``client`` carries, role user), with
    the same body;
  * fixed list: ``_table_stats`` is called exactly once for each of the
    five names — in order — and with no other name;
  * review defects, through the helpers' real bodies with
    ``get_cursor`` stubbed: a naive ``synced_at`` (the shape a
    ``timestamp without time zone`` column hands back) is pinned to UTC
    — never presumed to be the server's local time, proven with the
    process timezone flipped to UTC+14 — and an aware non-UTC value is
    still converted; and ``_data_through`` coerces whatever shape the
    column hands back (datetime, date, ISO string, null) to a ``date``;
  * identity, as a NON-ADMIN (role ``user``), in a second pass after a
    full reload: the page boots the production way (the token it would
    take from the URL hash as ``#access_token=...``, the bare
    development token, with ``/api/auth/config`` answering publicly and
    ``/api/auth/me`` resolving the viewer for that user) — the route
    carries no admin-only control, so the non-admin viewer reads the
    feed status and the page still renders, while unauthenticated
    /api traffic is still 401.
"""

import inspect
import os
import time
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.api.feed_status as feed_status_api
import app.feed.status as status_module
from app.feed.status import (
    FEED_TABLES,
    feed_status,
)
from app.feed.status import (
    _data_through as real_data_through,
)
from app.feed.status import (
    _table_stats as real_table_stats,
)
from app.main import app

URL = "/api/feed/status"

# Mixed-sync fixture: four of the five tables synced within 18 seconds
# on 2026-10-21, lead_time_observations never synced, data through
# 2026-06-30.
MIXED_STATS: dict[str, tuple[datetime | None, int]] = {
    "item_sites": (datetime(2026, 10, 21, 7, 1, 5, tzinfo=UTC), 774),
    "demand_events": (datetime(2026, 10, 21, 7, 1, 17, tzinfo=UTC), 23435),
    "on_hand": (datetime(2026, 10, 21, 7, 1, 20, tzinfo=UTC), 1520),
    "open_purchase_lines": (datetime(2026, 10, 21, 7, 1, 23, tzinfo=UTC), 89),
    "lead_time_observations": (None, 0),
}
MIXED_DATA_THROUGH = date(2026, 6, 30)

EXPECTED_MIXED = {
    "tables": [
        {"name": "item_sites", "synced_at": "2026-10-21T07:01:05+00:00", "rows": 774},
        {
            "name": "demand_events",
            "synced_at": "2026-10-21T07:01:17+00:00",
            "rows": 23435,
        },
        {"name": "on_hand", "synced_at": "2026-10-21T07:01:20+00:00", "rows": 1520},
        {
            "name": "open_purchase_lines",
            "synced_at": "2026-10-21T07:01:23+00:00",
            "rows": 89,
        },
        {"name": "lead_time_observations", "synced_at": None, "rows": 0},
    ],
    "synced_at": "2026-10-21T07:01:23+00:00",
    "oldest_synced_at": "2026-10-21T07:01:05+00:00",
    "data_through": "2026-06-30",
}

# Empty-feed fixture: nothing synced anywhere, no data at all.
EMPTY_STATS: dict[str, tuple[datetime | None, int]] = {
    table: (None, 0) for table in FEED_TABLES
}
EXPECTED_EMPTY = {
    "tables": [{"name": table, "synced_at": None, "rows": 0} for table in FEED_TABLES],
    "synced_at": None,
    "oldest_synced_at": None,
    "data_through": None,
}


def test_issue572_freeform(monkeypatch, client):
    # -- 1. Mixed timestamps (helpers monkeypatched — no database) -----
    calls: list[str] = []

    def fake_table_stats(table: str) -> tuple[datetime | None, int]:
        calls.append(table)
        return MIXED_STATS[table]

    monkeypatch.setattr(status_module, "_table_stats", fake_table_stats)
    monkeypatch.setattr(status_module, "_data_through", lambda: MIXED_DATA_THROUGH)

    assert feed_status() == EXPECTED_MIXED

    # Fixed list: _table_stats was called exactly once for each of the
    # five names — in order — and with no other name.
    assert calls == list(FEED_TABLES)

    # -- 2. Empty feed ---------------------------------------------------
    monkeypatch.setattr(status_module, "_table_stats", lambda table: EMPTY_STATS[table])
    monkeypatch.setattr(status_module, "_data_through", lambda: None)
    assert feed_status() == EXPECTED_EMPTY

    # -- 3. Endpoint: 401 without a token, 200 with the planner token ---
    # The mixed fixture is back in place, so the endpoint body is the
    # same dict feed_status() returns.
    monkeypatch.setattr(status_module, "_table_stats", fake_table_stats)
    monkeypatch.setattr(status_module, "_data_through", lambda: MIXED_DATA_THROUGH)

    anon = TestClient(app)
    response = anon.get(URL)
    assert response.status_code == 401

    # ``client`` carries the planner token the existing API tests use —
    # the bare development token, role user.
    response = client.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_MIXED

    # -- 4. Review defects: hostile-shape values from the database ------
    # Both helpers are proven through their real bodies here, with
    # ``app.feed.status.get_cursor`` stubbed to hand back rows in the
    # shapes a driver might actually return (still no database).
    class _RowCursor:
        def __init__(self, row):
            self._row = row

        def execute(self, sql):
            pass

        def fetchone(self):
            return self._row

    real_get_cursor = status_module.get_cursor

    def stub_cursor(row):
        @contextmanager
        def fake_get_cursor():
            yield _RowCursor(row)

        monkeypatch.setattr(status_module, "get_cursor", fake_get_cursor)

    # (a) A naive synced_at is pinned to UTC, never presumed to be the
    # server's local time.  The process timezone is flipped to UTC+14
    # for the duration, so the old bug (naive -> local -> UTC) would
    # have shifted every instant by 14 hours.
    old_tz = os.environ.get("TZ")
    try:
        os.environ["TZ"] = "Pacific/Kiritimati"
        time.tzset()

        stub_cursor(
            {"newest_synced_at": datetime(2026, 10, 21, 7, 1, 5), "row_count": 3}
        )
        newest, rows = real_table_stats("item_sites")
        assert rows == 3
        assert newest == datetime(2026, 10, 21, 7, 1, 5, tzinfo=UTC)

        # An aware non-UTC value is still converted to its UTC instant.
        stub_cursor(
            {
                "newest_synced_at": datetime(
                    2026, 10, 21, 9, 1, 5, tzinfo=timezone(timedelta(hours=2))
                ),
                "row_count": 3,
            }
        )
        newest, _ = real_table_stats("item_sites")
        assert newest == datetime(2026, 10, 21, 7, 1, 5, tzinfo=UTC)

        # And end to end: naive values from the (patched) helper still
        # serve as the unshifted UTC instants.
        def naive_stats(table):
            value, count = MIXED_STATS[table]
            return (
                (None if value is None else value.replace(tzinfo=None)),
                count,
            )

        monkeypatch.setattr(status_module, "_table_stats", naive_stats)
        assert feed_status()["synced_at"] == "2026-10-21T07:01:23+00:00"
        assert feed_status()["oldest_synced_at"] == "2026-10-21T07:01:05+00:00"
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()

    # (b) _data_through coerces whatever shape the column hands back to
    # a date, so the response is always YYYY-MM-DD (or null).
    stub_cursor({"newest_occurred_on": datetime(2026, 6, 30, 13, 45, 22, tzinfo=UTC)})
    assert real_data_through() == date(2026, 6, 30)
    stub_cursor({"newest_occurred_on": datetime(2026, 6, 30)})
    assert real_data_through() == date(2026, 6, 30)
    stub_cursor({"newest_occurred_on": "2026-06-30"})
    assert real_data_through() == date(2026, 6, 30)
    stub_cursor({"newest_occurred_on": "2026-06-30 13:45:22"})
    assert real_data_through() == date(2026, 6, 30)
    stub_cursor({"newest_occurred_on": date(2026, 6, 30)})
    assert real_data_through() == date(2026, 6, 30)
    stub_cursor({"newest_occurred_on": None})
    assert real_data_through() is None
    monkeypatch.setattr(status_module, "get_cursor", real_get_cursor)

    # The mixed fixture is back in place for the identity pass below.
    monkeypatch.setattr(status_module, "_table_stats", fake_table_stats)
    monkeypatch.setattr(status_module, "_data_through", lambda: MIXED_DATA_THROUGH)

    # -- 5. Identity: the proof also runs as a NON-ADMIN ----------------
    # Second pass after a full reload: the page boots the production
    # way — the token it would take from the URL hash as
    # #access_token=... (the bare development token), with
    # /api/auth/config answering publicly and /api/auth/me resolving
    # (dev-mode auth standing in for the IdP) the non-admin viewer for
    # that user.
    dev_token = os.environ.get("AUTH_DEV_TOKEN", "dev-secret-token")
    viewer = TestClient(app, headers={"Authorization": f"Bearer {dev_token}"})
    assert anon.get("/api/auth/config").status_code == 200  # public, pre-token
    me = viewer.get("/api/auth/me")
    assert me.status_code == 200, me.text
    user = me.json()["user"]
    assert user["sub"] == "dev-user-1"
    assert user["roles"] == ["user"]
    assert "admin" not in user["roles"]
    assert "admin" not in user["groups"]
    # The admin-only control is absent: this route carries no admin
    # gate (the route module references no admin dependency) and stays
    # in the schema, so the non-admin viewer reads the feed status and
    # the page still renders.
    assert "require_admin" not in inspect.getsource(feed_status_api)
    assert URL in app.openapi()["paths"]
    response = viewer.get(URL)
    assert response.status_code == 200, response.text
    assert response.json() == EXPECTED_MIXED
    # The gate is intact: unauthenticated /api traffic is still 401.
    assert anon.get(URL).status_code == 401
