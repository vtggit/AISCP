"""Issue #1464 proof: the process-wide cache of the reorder
recommendations.

``app.recommend.reorder.recommendations_for_all`` keeps a
process-wide cache of the rows it has computed: at most four
entries, an ``OrderedDict`` of key to rows held under a
``threading.Lock``, used unless the ``AIERP_RECOMMENDATIONS_CACHE``
environment variable is ``0``, ``false`` or ``off``, read at call
time.  The key is the resolved as-of date, the data stamp
``app.db.data_stamp.data_stamp()`` answers at call time — a
committed write to any of the eleven input tables moves the stamp,
so it changes the key — and the values of the environment variables
the computation reads at call time, ``AIERP_SERVICE_LEVEL``,
``AIERP_ORDER_COST`` and ``AIERP_HOLDING_RATE``.  The call checks the
cache after the request's ``recommendations_memo``: a hit answers a
copy of the stored rows built by ``copy_tree`` without computing,
and it is also stored in the active request memo the way a computed
answer is; a miss computes, stores a copy of the computed rows built
by ``copy_tree``, evicting the least recently used entries beyond
four, and answers the computed rows.  The suite keeps computing on
every call (``tests/conftest.py`` sets the switch off in its session
fixture); this proof turns the cache on itself with
``monkeypatch.setenv`` and counts the calls of
``app.recommend.reorder._compute_recommendations`` through a
wrapper.

Proved here, on the test database with the demand seeded the way
``tests/test_issue1410_freeform.py`` seeds its data — every public
table truncated, the rows inserted on a direct connection and
committed (360 days of 2.0 a day for one item-site, a usable pooled
lead time and the item-site's planning settings):

* two calls, outside any request memo, compute once and answer equal
  rows; changing a row of the first answer leaves the second
  unchanged;
* a committed write to an input table between two calls makes the
  second compute again — a demand signal inserted and deleted
  through the table's own access path (the guard trigger dropped for
  the delete and recreated, the way
  ``test_issue1463_freeform.py`` does), and
  ``replenishment.service_level`` stored through
  ``PUT /api/settings/replenishment.service_level`` as an admin and
  reset afterwards (the stored level changes the answer while it
  stands, the reset brings the earlier rows back);
* a write to a table that is not an input (``exception_acks``) does
  not: the next call answers from the cache;
* the hit is also stored in the active request memo the way a
  computed answer is: a call inside ``recommendations_memo`` that
  the cache answers can be repeated with the switch off and still
  computes nothing, while a computed answer inside the memo is
  memoized for the context as before;
* two as-of dates compute once each, and repeated calls of each
  compute nothing; after five distinct as-of dates the first date
  computes again — evicted beyond four entries — while the last one
  is still answered from the cache;
* with ``AIERP_RECOMMENDATIONS_CACHE`` at ``0``, ``false`` or
  ``off``, every call computes;
* across requests, ``GET /api/recommendations?limit=100`` read twice
  computes once and answers the same body;
* two threads calling ``recommendations_for_all`` at once both
  answer equal rows without an error;
* the key's env values are the computation's env values: a writer of
  ``AIERP_ORDER_COST`` that changes the variable between the key's
  read and the computation's — or during the computation — changes
  neither, the call answers the rows for the values it read at the
  start and the cache stores them under the key that says the same.
"""

import os
import threading
from datetime import date, timedelta

import psycopg2
import psycopg2.sql

from app.db.connection import get_connection, get_cursor
from app.recommend import reorder
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

CACHE_ENV = "AIERP_RECOMMENDATIONS_CACHE"

ITEM = "ITEM-1464"
SITE = "SITE-1464"
# The latest occurred_on: the default as-of the call resolves.
LAST_DAY = date(2026, 11, 10)
FIRST_DAY = LAST_DAY - timedelta(days=359)

# Two as-of dates inside the seeded window (AC: two dates compute
# once each), five more for the eviction check, and one date used
# only inside a request memo.
TWO_DATES = (date(2026, 10, 15), date(2026, 10, 1))
FIVE_DATES = (
    date(2026, 9, 15),
    date(2026, 9, 1),
    date(2026, 8, 15),
    date(2026, 8, 1),
    date(2026, 7, 15),
)
MEMO_DATE = date(2026, 6, 15)

SIGNAL_INSERT = (
    "INSERT INTO demand_signals (kind, item_code, site_code, "
    "start_date, end_date, quantity, reason, created_by) VALUES "
    "('promotion', %s, %s, '2026-11-11', '2026-11-15', 3.0, "
    "'issue 1464 proof', 'test-1464') RETURNING id"
)


def _truncate_all() -> None:
    """Every public table (but alembic_version) empty, the way
    ``test_issue1410_freeform.py`` seeds: a direct connection,
    autocommit, so the proof sees a database of its own rows,
    whatever ran before it (data_versions reseeds at version 0)."""
    conn = get_connection()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
            tables = [row[0] for row in cur.fetchall()]
            for table in tables:
                cur.execute(
                    psycopg2.sql.SQL("TRUNCATE TABLE {} CASCADE").format(
                        psycopg2.sql.Identifier(table)
                    )
                )
    finally:
        conn.close()


def _seed_demand() -> None:
    """360 days of 2.0 a day (one statement) and three pooled
    lead-time observations (8, 10 and 12 days: mean 10, sample std
    2, not low confidence) for one item-site, plus the item-site's
    planning settings — a smooth, recommendable pair at every as-of
    date this proof uses."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO demand_events (id, business_key, "
                "item_code, site_code, quantity, occurred_on, "
                "event_type, source_system) "
                "SELECT '1464-d-' || g, '1464-bk-' || g, %s, %s, 2.0, "
                "%s::date + g, 'shipment', 'test' "
                "FROM generate_series(0, 359) AS g",
                (ITEM, SITE, FIRST_DAY.isoformat()),
            )
            for i, (ordered, received) in enumerate(
                (
                    ("2026-08-22", "2026-08-30"),
                    ("2026-08-20", "2026-08-30"),
                    ("2026-08-18", "2026-08-30"),
                )
            ):
                cur.execute(
                    "INSERT INTO lead_time_observations (id, "
                    "business_key, supplier_code, item_code, "
                    "ordered_on, received_on, source_system) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        f"id-lt-1464-{i}",
                        f"lt-1464-{i}",
                        "SUP-1464",
                        ITEM,
                        ordered,
                        received,
                        "test",
                    ),
                )
        conn.commit()
    finally:
        conn.close()
    ItemSitePostgresRepository().upsert_many(
        [
            {
                "item_code": ITEM,
                "site_code": SITE,
                "unit_cost": 1.0,
                "source_system": "test",
            }
        ]
    )


def _signal_insert_delete() -> None:
    """One committed demand-signal write pair — inserted and deleted
    through the table's own access path: the append-only guard
    trigger is dropped for the delete and recreated, exactly as
    ``test_issue1463_freeform.py`` does, so the guard itself never
    blocks the proof's delete."""
    conn = get_connection()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(SIGNAL_INSERT, (ITEM, SITE))
            signal_id = cur.fetchone()[0]
            cur.execute(
                "DROP TRIGGER IF EXISTS demand_signals_guard " "ON demand_signals"
            )
            try:
                cur.execute(
                    "DELETE FROM demand_signals WHERE id = %s",
                    (signal_id,),
                )
            finally:
                cur.execute(
                    "CREATE TRIGGER demand_signals_guard "
                    "BEFORE INSERT OR UPDATE OR DELETE ON demand_signals "
                    "FOR EACH ROW EXECUTE FUNCTION aierp_demand_signals_guard()"
                )
    finally:
        conn.close()


def _setting_store_reset(client, admin_headers) -> None:
    """``replenishment.service_level`` stored through
    ``PUT /api/settings/replenishment.service_level`` as an admin
    (0.97, inside the setting's bounds) and reset afterwards — two
    committed writes to the ``settings`` input table."""
    put = client.put(
        "/api/settings/replenishment.service_level",
        json={"value": 0.97},
        headers=admin_headers,
    )
    assert put.status_code == 200, put.text
    delete = client.delete(
        "/api/settings/replenishment.service_level",
        headers=admin_headers,
    )
    assert delete.status_code == 200, delete.text


def _install_counter(monkeypatch) -> tuple[list, object]:
    """The counting wrapper around
    ``app.recommend.reorder._compute_recommendations``; answered with
    the counter and the wrapper itself, so a section can install a
    wrapper of its own on top and restore this one."""
    calls = [0]
    lock = threading.Lock()
    original = reorder._compute_recommendations

    def counting(resolved_as_of):
        with lock:
            calls[0] += 1
        return original(resolved_as_of)

    monkeypatch.setattr(reorder, "_compute_recommendations", counting)
    return calls, counting


def test_issue1464_freeform(client, admin_headers, monkeypatch) -> None:
    # The suite keeps computing on every call (conftest sets the
    # switch off); this proof turns the cache on itself, with the
    # three computation environment variables unset so their key
    # values are None.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    monkeypatch.setenv(CACHE_ENV, "1")
    calls, counting = _install_counter(monkeypatch)

    _truncate_all()
    _seed_demand()

    # -- Two calls, outside any request memo -------------------------
    first = reorder.recommendations_for_all()
    assert calls[0] == 1
    assert first, "the seed answers no recommendation rows at all"
    row0 = first[0]
    assert row0["item_code"] == ITEM and row0["site_code"] == SITE
    assert row0["as_of"] == LAST_DAY
    assert row0["status"] == "ok"

    second = reorder.recommendations_for_all()
    assert calls[0] == 1, "the second call must answer from the cache"
    assert first == second

    # Changing a row of the first answer leaves the second unchanged.
    first[0]["item_code"] = "MUTATED"
    third = reorder.recommendations_for_all()
    assert calls[0] == 1
    assert third == second

    # -- A committed write to an input table makes the next call
    #    compute again: a demand signal inserted and deleted --------
    _signal_insert_delete()
    after_signal = reorder.recommendations_for_all()
    assert calls[0] == 2, "the written input table must move the stamp"
    assert after_signal == second, "the signal is gone again"

    # ... and ``replenishment.service_level`` stored through PUT and
    # reset through DELETE as an admin.
    put = client.put(
        "/api/settings/replenishment.service_level",
        json={"value": 0.97},
        headers=admin_headers,
    )
    assert put.status_code == 200, put.text
    with_setting = reorder.recommendations_for_all()
    assert calls[0] == 3, "the written settings table must move the stamp"
    assert with_setting != second, "the stored level stands in for the env"
    delete = client.delete(
        "/api/settings/replenishment.service_level",
        headers=admin_headers,
    )
    assert delete.status_code == 200, delete.text
    after_reset = reorder.recommendations_for_all()
    assert calls[0] == 4, "the reset must move the stamp again"
    assert after_reset == second, "no stored level: the earlier rows"

    # -- A write to a table that is not an input does not ------------
    ack_key = "issue-1464-proof"
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO exception_acks (key, acknowledged_by) " "VALUES (%s, %s)",
            (ack_key, "test-1464"),
        )
    after_ack = reorder.recommendations_for_all()
    assert calls[0] == 4, "exception_acks is not an input table"
    assert after_ack == second
    with get_cursor() as cur:
        cur.execute("DELETE FROM exception_acks WHERE key = %s", (ack_key,))

    # -- The hit is also stored in the active request memo the way a
    #    computed answer is ------------------------------------------
    with reorder.recommendations_memo():
        memo_hit = reorder.recommendations_for_all()
        assert calls[0] == 4, "the process cache answers the hit"
        assert memo_hit == second
        # With the switch off the only way the next call computes
        # nothing is the memo's own entry for the hit's rows.
        monkeypatch.setenv(CACHE_ENV, "0")
        memo_again = reorder.recommendations_for_all()
        assert calls[0] == 4, "the hit's copy must be in the active memo"
        assert memo_again == second
    monkeypatch.setenv(CACHE_ENV, "1")

    # A computed answer inside the memo is memoized for the context,
    # as before the cache.
    with reorder.recommendations_memo():
        computed1 = reorder.recommendations_for_all(as_of=MEMO_DATE)
        assert calls[0] == 5
        computed2 = reorder.recommendations_for_all(as_of=MEMO_DATE)
        assert calls[0] == 5
        assert computed1 == computed2 and computed1

    # -- Two as-of dates compute once each ---------------------------
    d1, d2 = TWO_DATES
    before = calls[0]
    r1a = reorder.recommendations_for_all(as_of=d1)
    assert calls[0] == before + 1
    r1b = reorder.recommendations_for_all(as_of=d1)
    assert calls[0] == before + 1
    assert r1a == r1b and r1a
    r2a = reorder.recommendations_for_all(as_of=d2)
    assert calls[0] == before + 2
    r2b = reorder.recommendations_for_all(as_of=d2)
    assert calls[0] == before + 2
    assert r2a == r2b and r2a

    # -- Five distinct as-of dates: the first computes again ---------
    before = calls[0]
    for i, as_of in enumerate(FIVE_DATES, 1):
        rows = reorder.recommendations_for_all(as_of=as_of)
        assert calls[0] == before + i, f"date {i} of five must compute"
        assert rows and rows[0]["as_of"] == as_of
    first_again = reorder.recommendations_for_all(as_of=FIVE_DATES[0])
    assert calls[0] == before + 6, "evicted beyond four: it computes again"
    assert first_again and first_again[0]["as_of"] == FIVE_DATES[0]
    last_again = reorder.recommendations_for_all(as_of=FIVE_DATES[-1])
    assert calls[0] == before + 6, "the last date is still cached"
    assert last_again and last_again[0]["as_of"] == FIVE_DATES[-1]

    # -- With the switch off, every call computes ---------------------
    for value in ("0", "false", "off"):
        monkeypatch.setenv(CACHE_ENV, value)
        before = calls[0]
        off_a = reorder.recommendations_for_all()
        off_b = reorder.recommendations_for_all()
        assert calls[0] == before + 2, f"switch {value!r}: both compute"
        assert off_a == off_b
    monkeypatch.setenv(CACHE_ENV, "1")

    # -- Across requests: GET /api/recommendations?limit=100 ---------
    # One more committed write to an input table (stored and reset,
    # the way an admin does it) gives the route's key a stamp no
    # earlier call computed.
    _setting_store_reset(client, admin_headers)
    before = calls[0]
    list1 = client.get("/api/recommendations", params={"limit": 100})
    assert list1.status_code == 200, list1.text
    assert calls[0] == before + 1, "the first read computes"
    list2 = client.get("/api/recommendations", params={"limit": 100})
    assert list2.status_code == 200, list2.text
    assert calls[0] == before + 1, "the second read answers from the cache"
    assert list1.json() == list2.json() and list1.json()

    # -- Concurrency: two threads at once ----------------------------
    # A fresh stamp again: both threads start from a cold key.
    _setting_store_reset(client, admin_headers)
    errors: list = []
    barrier = threading.Barrier(2)
    results: list = [None, None]

    def worker(index):
        try:
            barrier.wait(timeout=10)
            results[index] = reorder.recommendations_for_all()
        except Exception as exc:
            errors.append(exc)

    before = calls[0]
    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors, errors
    assert results[0] is not None and results[1] is not None
    assert results[0] == results[1] and results[0]
    assert before + 1 <= calls[0] <= before + 2

    # -- The key's env values are the computation's env values -------
    # A writer of the variables that changes them between the key's
    # read and the computation's — or during the computation —
    # changes neither: the key and the computation share one read, so
    # the call answers the rows for the values it read at the start,
    # and the cache stores them under the key that says the same.
    monkeypatch.setenv("AIERP_ORDER_COST", "80")

    def diverging(resolved_as_of):
        os.environ["AIERP_ORDER_COST"] = "120"
        try:
            return counting(resolved_as_of)
        finally:
            os.environ["AIERP_ORDER_COST"] = "80"

    monkeypatch.setattr(reorder, "_compute_recommendations", diverging)
    before = calls[0]
    diverged = reorder.recommendations_for_all()
    assert calls[0] == before + 1, "a new key's env value: it computes"
    assert (
        diverged and diverged[0]["order_quantity"]["order_cost"] == 80.0
    ), "the key's value, not the value written mid-call"
    monkeypatch.setattr(reorder, "_compute_recommendations", counting)
    diverged_again = reorder.recommendations_for_all()
    assert calls[0] == before + 1, "the entry stands under the key's values"
    assert diverged_again == diverged
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
