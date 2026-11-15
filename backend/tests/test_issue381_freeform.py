"""Issue #381 proof: the effective forecast in forecast snapshots.

Migration 0015_forecast_snapshot_effective (down_revision
0014_forecast_overrides) adds two nullable columns to
forecast_snapshots — ``effective_quantity DOUBLE PRECISION`` and
``override_days INTEGER`` — and its downgrade drops both.  It updates
no existing row, and the guard triggers stay as they are.

The proof covers, in one test, against the test database (demand
events inserted for the test — 2.0 a day for 401 days, ending on
2026-08-31, the shared default as-of date, for each item-site — and
one override stored through POST /api/forecast/overrides):

* a live capture (no ``as_of``) storing, for the overridden bucket
  (item 1's first 30-day period), the ``effective_quantity`` and
  ``override_days`` that ``GET /api/forecast/by-key`` shows for it at
  capture time [rule: OVR-4], and for every other bucket
  ``effective_quantity`` equal to ``quantity`` and ``override_days`` 0;
* a backfill capture for an earlier date (2026-07-31) storing
  ``effective_quantity`` equal to ``quantity`` and ``override_days`` 0
  for every row, the override notwithstanding — a hindcast has no
  overrides;
* the statistical columns of both captures — ``quantity``, ``method``,
  ``demand_class``, ``history_buckets`` and the bucket boundaries —
  equal to what the per-item-site path gives (one
  ``history_for_pair`` and ``choose_forecast`` per item-site), the
  capture building its rows from ``forecast_rows`` on a fixed number
  of queries, not one history query per item-site;
* a row inserted the old way, with both new columns null, and a row
  with a null ``effective_quantity`` beside a stored non-null
  ``override_days``, each read back with ``effective_quantity``
  equal to ``quantity`` and ``override_days`` 0, wherever snapshot
  rows are read;
* an UPDATE of a stored snapshot row still refused by the guard
  trigger, the rows immutable;
* the number of SQL statements a capture executes being the same with
  5 item-sites as with 25 — one history query for every item-site and
  one counted-override query in total;
* the migration upgrading and downgrading cleanly: at head the two
  nullable columns exist, the downgrade to 0014_forecast_overrides
  drops them leaving the stored rows untouched, and the upgrade back
  to head restores them with every pre-existing row's new columns
  still null — the migration does not update existing rows.
"""

import os
from datetime import date, timedelta

import psycopg2
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.forecast.history import history_for_pair
from app.forecast.select import choose_forecast
from app.forecast.snapshots import (
    HORIZON_BUCKETS,
    capture_snapshot,
    stored_snapshot_rows,
)
from app.main import app
from app.stats.demand import BUCKET_DAYS, demand_stats_for_pair
from tests.migration_head import CURRENT_HEAD

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BASE_REVISION = "0014_forecast_overrides"
_HEAD_REVISION = "0022_lead_time_promised_on"

# The latest demand day across all seeded events — the shared default
# as-of date (never later than today).
AS_OF = date(2026, 8, 31)
FIRST_DAY = AS_OF - timedelta(days=400)  # 2025-07-16
# The earlier date the backfill capture targets.
BACKFILL_AS_OF = date(2026, 7, 31)
# Two fresh dates for the fixed-statement-count captures.
COUNT_FIVE_AS_OF = date(2026, 8, 1)
COUNT_TWENTY_FIVE_AS_OF = date(2026, 8, 2)
# A fresh date for the row inserted the old way.
OLD_ROW_AS_OF = date(2026, 6, 30)
# The old row's statistical quantity.
OLD_ROW_QUANTITY = 42.0
# A fresh date for the row whose effective_quantity is null while a
# non-null override_days is stored.
NULL_EFFECTIVE_AS_OF = date(2026, 6, 29)
# That row's statistical quantity.
NULL_EFFECTIVE_QUANTITY = 17.5
# The stored override_days the null effective column must read back
# as 0.
NULL_EFFECTIVE_OVERRIDE_DAYS = 7

SITE = "SITE-381"
OVERRIDDEN_QUANTITY = 99.0
# Item 1's first forecast period at the shared default as-of date.
P1_START = AS_OF + timedelta(days=1)  # 2026-09-01
P1_END = AS_OF + timedelta(days=BUCKET_DAYS)  # 2026-09-30


def _item(index: int) -> str:
    return f"ITEM-381-{index}"


def _seed_items(cur, first_index: int, last_index: int) -> None:
    """2.0 a day for 401 days ending on AS_OF, for one item-site each."""
    for index in range(first_index, last_index + 1):
        item_code = _item(index)
        cur.executemany(
            "INSERT INTO demand_events "
            "(id, business_key, item_code, site_code, quantity, "
            "occurred_on, event_type, source_system) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    f"id-de-381-{item_code}-{offset}",
                    f"de-381-{item_code}-{offset}",
                    item_code,
                    SITE,
                    2.0,
                    (FIRST_DAY + timedelta(days=offset)).isoformat(),
                    "shipment",
                    "test",
                )
                for offset in range(401)
            ],
        )


def _per_pair_statistical(item_code: str, site_code: str, as_of: date) -> dict:
    """The statistical bucket rows the per-item-site path stores.

    The capture's row path before this issue: one
    ``demand_stats_for_pair``, one ``history_for_pair`` and one
    ``choose_forecast`` for the pair — the bucket boundaries,
    quantities, method, demand class and history bucket count.
    """
    stats = demand_stats_for_pair(item_code, site_code, as_of)
    history = history_for_pair(item_code, site_code, as_of)
    chosen = choose_forecast(
        [bucket["quantity"] for bucket in history],
        stats["demand_class"],
        horizon=HORIZON_BUCKETS,
    )
    rows = {}
    for index, quantity in enumerate(chosen["forecast"], start=1):
        rows[index] = {
            "bucket_start": as_of + timedelta(days=BUCKET_DAYS * (index - 1) + 1),
            "bucket_end": as_of + timedelta(days=BUCKET_DAYS * index),
            "quantity": quantity,
            "method": chosen["method"],
            "demand_class": stats["demand_class"],
            "history_buckets": chosen["history_buckets"],
        }
    return rows


class _CountingCursor:
    """A cursor that counts its statement calls, delegating the rest."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def execute(self, query, *args, **kwargs):
        self._counter[0] += 1
        self._queries.append(query)
        return self._real.execute(query, *args, **kwargs)

    def executemany(self, query, *args, **kwargs):
        self._counter[0] += 1
        self._queries.append(query)
        return self._real.executemany(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._real.close()
        return False


class _CountingConnection:
    """A connection whose cursors count their statement calls."""

    def __init__(self, real, counter, queries):
        self._real = real
        self._counter = counter
        self._queries = queries

    def cursor(self, *args, **kwargs):
        return _CountingCursor(
            self._real.cursor(*args, **kwargs), self._counter, self._queries
        )

    def __getattr__(self, name):
        return getattr(self._real, name)


def _measure(fn):
    """Run fn with psycopg2.connect wrapped; (result, statements, SQL)."""
    counter = [0]
    queries: list[str] = []
    real_connect = psycopg2.connect

    def counting_connect(**params):
        return _CountingConnection(real_connect(**params), counter, queries)

    psycopg2.connect = counting_connect
    try:
        result = fn()
    finally:
        psycopg2.connect = real_connect
    return result, counter[0], queries


def _alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(os.path.join(_BACKEND_ROOT, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(_BACKEND_ROOT, "migrations"))
    return cfg


def _alembic_revision() -> str:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version_num FROM alembic_version")
            return cur.fetchone()[0]
    finally:
        conn.close()


def _snapshot_columns() -> dict:
    """The two new columns of forecast_snapshots, if present."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT column_name, data_type, is_nullable "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'forecast_snapshots' "
                "AND column_name IN ('effective_quantity', 'override_days') "
                "ORDER BY column_name"
            )
            return {row[0]: (row[1], row[2]) for row in cur.fetchall()}
    finally:
        conn.close()


_NEW_COLUMNS = {
    "effective_quantity": ("double precision", "YES"),
    "override_days": ("integer", "YES"),
}


def _snapshot_totals() -> tuple:
    """The snapshot table's row count and quantity sum."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*), SUM(quantity) FROM forecast_snapshots")
            return cur.fetchone()
    finally:
        conn.close()


def test_issue381_freeform(client, admin_headers):
    admin = TestClient(app, headers=admin_headers)

    # ------------------------------------------------------------------
    # Seed: 5 item-sites, 2.0 a day for 401 days ending on AS_OF
    # (smooth, method mean, twelve 60.0 forecast buckets each), and one
    # override stored through the API for item 1's first period.
    # ------------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed_items(cur, 1, 5)
        conn.commit()
    finally:
        conn.close()

    resp = admin.post(
        "/api/forecast/overrides",
        json={
            "item_code": _item(1),
            "site_code": SITE,
            "period_start": P1_START.isoformat(),
            "quantity": OVERRIDDEN_QUANTITY,
            "reason": "planner override",
        },
    )
    assert resp.status_code == 201, resp.text
    override = resp.json()
    assert override["kind"] == "override"
    assert override["period_start"] == P1_START.isoformat()
    assert override["period_end"] == P1_END.isoformat()
    assert override["quantity"] == OVERRIDDEN_QUANTITY
    assert override["applies"] is True

    # ------------------------------------------------------------------
    # AC-1: a live capture (no as_of) stores, for the overridden
    # bucket, the effective quantity and override_days GET
    # /api/forecast/by-key shows at capture time [rule: OVR-4] — and
    # for every other bucket effective_quantity equal to quantity and
    # override_days 0.
    # ------------------------------------------------------------------
    resp = admin.post("/api/forecast/snapshots")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "as_of": AS_OF.isoformat(),
        "origin": "live",
        "created": True,
        "item_sites": 5,
        "rows": 60,
    }

    stored = stored_snapshot_rows(AS_OF)
    assert len(stored) == 60
    assert {(row["item_code"], row["site_code"]) for row in stored} == {
        (_item(index), SITE) for index in range(1, 6)
    }

    resp = client.get(
        "/api/forecast/by-key",
        params={"item_code": _item(1), "site_code": SITE},
    )
    assert resp.status_code == 200, resp.text
    api_row = resp.json()
    api_buckets = api_row["forecast"]
    # The override shows in item 1's first bucket and nowhere else.
    assert api_buckets[0]["quantity"] == 60.0
    assert api_buckets[0]["effective_quantity"] == OVERRIDDEN_QUANTITY
    assert api_buckets[0]["override_days"] == BUCKET_DAYS
    for bucket in api_buckets[1:]:
        assert bucket["effective_quantity"] == bucket["quantity"]
        assert bucket["override_days"] == 0

    item_one = [row for row in stored if row["item_code"] == _item(1)]
    assert [row["bucket_index"] for row in item_one] == list(range(1, 13))
    for row, api_bucket in zip(item_one, api_buckets):
        assert row["quantity"] == api_bucket["quantity"]
        assert row["effective_quantity"] == api_bucket["effective_quantity"]
        assert row["override_days"] == api_bucket["override_days"]
    assert item_one[0]["effective_quantity"] == OVERRIDDEN_QUANTITY
    assert item_one[0]["override_days"] == BUCKET_DAYS

    for row in stored:
        if row["item_code"] == _item(1) and row["bucket_index"] == 1:
            continue
        assert row["effective_quantity"] == row["quantity"], row
        assert row["override_days"] == 0, row

    # ------------------------------------------------------------------
    # AC-1: a backfill capture for an earlier date stores
    # effective_quantity equal to quantity and override_days 0 for
    # every row, the override notwithstanding — a hindcast has no
    # overrides.
    # ------------------------------------------------------------------
    resp = admin.post(
        "/api/forecast/snapshots",
        json={"as_of": BACKFILL_AS_OF.isoformat()},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {
        "as_of": BACKFILL_AS_OF.isoformat(),
        "origin": "backfill",
        "created": True,
        "item_sites": 5,
        "rows": 60,
    }

    backfill = stored_snapshot_rows(BACKFILL_AS_OF)
    assert len(backfill) == 60
    for row in backfill:
        assert row["effective_quantity"] == row["quantity"], row
        assert row["override_days"] == 0, row

    # ------------------------------------------------------------------
    # AC-1: the statistical columns of both captures are exactly the
    # ones the per-item-site path gives.
    # ------------------------------------------------------------------
    for as_of, rows in ((AS_OF, stored), (BACKFILL_AS_OF, backfill)):
        for index in range(1, 6):
            item_code = _item(index)
            expected = _per_pair_statistical(item_code, SITE, as_of)
            for row in rows:
                if row["item_code"] != item_code:
                    continue
                mine = expected[row["bucket_index"]]
                assert row["quantity"] == mine["quantity"], (as_of, row)
                assert row["method"] == mine["method"], (as_of, row)
                assert row["demand_class"] == mine["demand_class"], (as_of, row)
                assert row["history_buckets"] == mine["history_buckets"], (as_of, row)
                assert row["bucket_start"] == mine["bucket_start"], (as_of, row)
                assert row["bucket_end"] == mine["bucket_end"], (as_of, row)

    # ------------------------------------------------------------------
    # AC-1: a row inserted the old way — the 13-column insert, both
    # new columns null — reads back with effective_quantity equal to
    # quantity and override_days 0.
    # ------------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO forecast_snapshots "
                "(id, as_of, origin, item_code, site_code, bucket_index, "
                "bucket_start, bucket_end, quantity, method, demand_class, "
                "history_buckets, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())",
                (
                    "id-381-old",
                    OLD_ROW_AS_OF,
                    "live",
                    _item(2),
                    SITE,
                    1,
                    OLD_ROW_AS_OF + timedelta(days=1),
                    OLD_ROW_AS_OF + timedelta(days=BUCKET_DAYS),
                    OLD_ROW_QUANTITY,
                    "mean",
                    "smooth",
                    12,
                ),
            )
            cur.execute(
                "SELECT quantity, effective_quantity, override_days "
                "FROM forecast_snapshots WHERE id = %s",
                ("id-381-old",),
            )
            quantity, effective_quantity, override_days = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    assert quantity == OLD_ROW_QUANTITY
    assert effective_quantity is None
    assert override_days is None

    old_rows = stored_snapshot_rows(OLD_ROW_AS_OF)
    assert len(old_rows) == 1
    assert old_rows[0]["quantity"] == OLD_ROW_QUANTITY
    assert old_rows[0]["effective_quantity"] == old_rows[0]["quantity"]
    assert old_rows[0]["override_days"] == 0

    # ------------------------------------------------------------------
    # AC-1: a stored row whose effective_quantity is null is read
    # with override_days 0 even when a non-null override_days is
    # stored — the null effective column forces the statistical
    # fallback for both columns.
    # ------------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO forecast_snapshots "
                "(id, as_of, origin, item_code, site_code, bucket_index, "
                "bucket_start, bucket_end, quantity, effective_quantity, "
                "override_days, method, demand_class, history_buckets, "
                "created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NULL, %s, "
                "%s, %s, %s, now())",
                (
                    "id-381-null-effective",
                    NULL_EFFECTIVE_AS_OF,
                    "live",
                    _item(3),
                    SITE,
                    1,
                    NULL_EFFECTIVE_AS_OF + timedelta(days=1),
                    NULL_EFFECTIVE_AS_OF + timedelta(days=BUCKET_DAYS),
                    NULL_EFFECTIVE_QUANTITY,
                    NULL_EFFECTIVE_OVERRIDE_DAYS,
                    "mean",
                    "smooth",
                    12,
                ),
            )
            cur.execute(
                "SELECT quantity, effective_quantity, override_days "
                "FROM forecast_snapshots WHERE id = %s",
                ("id-381-null-effective",),
            )
            quantity, effective_quantity, override_days = cur.fetchone()
        conn.commit()
    finally:
        conn.close()
    # The row really does store a null effective_quantity beside a
    # non-null override_days.
    assert quantity == NULL_EFFECTIVE_QUANTITY
    assert effective_quantity is None
    assert override_days == NULL_EFFECTIVE_OVERRIDE_DAYS

    null_effective_rows = stored_snapshot_rows(NULL_EFFECTIVE_AS_OF)
    assert len(null_effective_rows) == 1
    assert null_effective_rows[0]["quantity"] == NULL_EFFECTIVE_QUANTITY
    assert null_effective_rows[0]["effective_quantity"] == NULL_EFFECTIVE_QUANTITY
    assert null_effective_rows[0]["override_days"] == 0

    # ------------------------------------------------------------------
    # AC-1: the guard triggers stay as they are — an UPDATE of a
    # stored snapshot row is still refused.
    # ------------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    "UPDATE forecast_snapshots SET quantity = quantity + 1 "
                    "WHERE as_of = %s",
                    (AS_OF,),
                )
            except psycopg2.Error as exc:
                assert exc.pgcode == "55000", (exc.pgcode, exc)
                assert "immutable" in str(exc), str(exc)
            else:
                raise AssertionError("the guard did not refuse the update")
        conn.rollback()
    finally:
        conn.close()

    # ------------------------------------------------------------------
    # AC-1: the number of SQL statements a capture executes is fixed —
    # the same with 5 item-sites as with 25, one history query for
    # every item-site and one counted-override query in total.
    # ------------------------------------------------------------------
    result5, count5, queries5 = _measure(
        lambda: capture_snapshot(as_of=COUNT_FIVE_AS_OF)
    )
    assert result5 == {
        "as_of": COUNT_FIVE_AS_OF,
        "origin": "backfill",
        "created": True,
        "item_sites": 5,
        "rows": 60,
    }
    assert len([q for q in queries5 if "MIN(occurred_on)" in q]) == 2
    assert len([q for q in queries5 if "forecast_overrides" in q]) == 1

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed_items(cur, 6, 25)
        conn.commit()
    finally:
        conn.close()

    result25, count25, queries25 = _measure(
        lambda: capture_snapshot(as_of=COUNT_TWENTY_FIVE_AS_OF)
    )
    assert result25 == {
        "as_of": COUNT_TWENTY_FIVE_AS_OF,
        "origin": "backfill",
        "created": True,
        "item_sites": 25,
        "rows": 300,
    }
    # Five times as many item-sites, the same number of statements.
    assert count25 == count5
    assert len([q for q in queries25 if "MIN(occurred_on)" in q]) == 2
    assert len([q for q in queries25 if "forecast_overrides" in q]) == 1

    # ------------------------------------------------------------------
    # AC-1: the migration upgrades and downgrades cleanly — the two
    # nullable columns at head, dropped by the downgrade with the
    # stored rows untouched, restored by the upgrade with every
    # pre-existing row's new columns still null.
    # ------------------------------------------------------------------
    assert _alembic_revision() == CURRENT_HEAD
    assert _snapshot_columns() == _NEW_COLUMNS
    totals_before = _snapshot_totals()
    assert (
        totals_before[0] == 482
    )  # 60 + 60 + 60 + 300 + 1 old row + 1 null-effective row

    cfg = _alembic_config()
    alembic_command.downgrade(cfg, _BASE_REVISION)
    assert _alembic_revision() == _BASE_REVISION
    # The columns are gone, the stored rows are left exactly as they
    # were — the downgrade drops nothing else.
    assert _snapshot_columns() == {}
    assert _snapshot_totals() == totals_before

    alembic_command.upgrade(cfg, "head")
    heads = ScriptDirectory.from_config(cfg).get_heads()
    assert len(heads) == 1
    assert _alembic_revision() == CURRENT_HEAD
    assert _snapshot_columns() == _NEW_COLUMNS

    # The migration does not update existing rows: every pre-existing
    # row keeps both new columns null...
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*), COUNT(effective_quantity), "
                "COUNT(override_days) FROM forecast_snapshots"
            )
            total, with_effective, with_days = cur.fetchone()
    finally:
        conn.close()
    assert total == totals_before[0]
    assert with_effective == 0
    assert with_days == 0

    # ...and they read back with the statistical fallback.
    for as_of in (AS_OF, BACKFILL_AS_OF, COUNT_FIVE_AS_OF, COUNT_TWENTY_FIVE_AS_OF):
        for row in stored_snapshot_rows(as_of):
            assert row["effective_quantity"] == row["quantity"], (as_of, row)
            assert row["override_days"] == 0, (as_of, row)
