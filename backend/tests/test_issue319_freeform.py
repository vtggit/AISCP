"""Issue #319 proof: the history buckets are complete 30-day buckets only.

The proof covers, in one test, against the test database (demand events
inserted for the test, as of 2026-08-31):

* a first demand 35 days before the as-of date: one bucket covering the
  last 30 days, whose quantity leaves out the demand of the 5 earlier
  days;
* first demands 59 and 60 days before: two buckets each, with the
  expected boundaries and quantities (the 60-day first demand itself
  falls before the oldest bucket and is not in the history);
* a first demand 10 days before: one bucket;
* a first demand more than 1,080 days before: 36 buckets;
* for each of those item-sites with 12 buckets or fewer, the count
  equal to ``n_buckets`` from
  ``app.stats.demand.demand_stats_for_pair``;
* ``GET /api/forecast/by-key`` for the 35-day item-site returning
  ``history_buckets`` 1 and one ``history`` entry;
* the review defect — with the ``demand_events`` table empty and no
  ``as_of`` given, ``history_for_pair`` returns None instead of
  raising: the default as-of resolves from ``demand_events`` alone
  (the latest ``occurred_on``, never later than today), proven with
  ``lead_time_observations`` missing.
"""

from datetime import date, timedelta

from app.db.connection import get_connection
from app.forecast.history import history_for_pair
from app.stats.demand import demand_stats_for_pair

# The as-of date every call uses.
AS_OF = date(2026, 8, 31)
AS_OF_STR = AS_OF.isoformat()

# A repeating 12-bucket pattern of positive quantities (demand events
# must be positive); sum 60, mean 5.0.
PATTERN = [10, 3, 6, 2, 8, 1, 7, 6, 9, 4, 2, 2]


def _seed() -> None:
    """Demand events for five item-sites, all on fixed dates.

    * 35: first demand 35 days before the as-of date (AS_OF-35, 9.0),
      plus 3.0 at AS_OF-2 — the 5 earlier days are outside the last 30;
    * 59: first demand on the oldest bucket's start (AS_OF-59), 7.0,
      plus 5.0 at AS_OF-31 (in the oldest bucket) and 4.0 at AS_OF-10;
    * 60: first demand one day before the oldest bucket (AS_OF-60),
      8.0, plus 6.0 at AS_OF-40 and 2.0 at AS_OF-10;
    * 10: first demand 10 days before the as-of date, 5.0, plus 7.0 at
      AS_OF-1;
    * LONG: first demand 1,081 days before the as-of date (AS_OF-1081,
      3.0, before the 36-bucket window), plus one event per bucket at
      its start (PATTERN, repeating).
    """
    events = []

    def add(item: str, site: str, day: date, quantity: float, tag: str) -> None:
        events.append(
            (
                f"id-de-319-{tag}",
                f"de-319-{tag}",
                item,
                site,
                quantity,
                day.isoformat(),
                "shipment",
                "test",
            )
        )

    add("ITEM-319-35", "SITE-319-35", AS_OF - timedelta(days=35), 9.0, "d35a")
    add("ITEM-319-35", "SITE-319-35", AS_OF - timedelta(days=2), 3.0, "d35b")
    add("ITEM-319-59", "SITE-319-59", AS_OF - timedelta(days=59), 7.0, "d59a")
    add("ITEM-319-59", "SITE-319-59", AS_OF - timedelta(days=31), 5.0, "d59b")
    add("ITEM-319-59", "SITE-319-59", AS_OF - timedelta(days=10), 4.0, "d59c")
    add("ITEM-319-60", "SITE-319-60", AS_OF - timedelta(days=60), 8.0, "d60a")
    add("ITEM-319-60", "SITE-319-60", AS_OF - timedelta(days=40), 6.0, "d60b")
    add("ITEM-319-60", "SITE-319-60", AS_OF - timedelta(days=10), 2.0, "d60c")
    add("ITEM-319-10", "SITE-319-10", AS_OF - timedelta(days=10), 5.0, "d10a")
    add("ITEM-319-10", "SITE-319-10", AS_OF - timedelta(days=1), 7.0, "d10b")
    # One event per bucket, at the bucket's first day, so the oldest
    # bucket (AS_OF-1079 .. AS_OF-1050) is counted and the pair gets
    # the full 36.
    add("ITEM-319-LONG", "SITE-319-LONG", AS_OF - timedelta(days=1081), 3.0, "lng0")
    for position in range(36):
        add(
            "ITEM-319-LONG",
            "SITE-319-LONG",
            AS_OF - timedelta(days=30 * (35 - position) + 29),
            float(PATTERN[position % 12]),
            f"lng{position + 1}",
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


def test_issue319_freeform(client):
    _seed()

    # ------------------------------------------------------------------
    # AC-1: complete 30-day buckets only, the count rule shared with
    # app.stats.demand.compute_demand_stats.
    # ------------------------------------------------------------------

    # A first demand 35 days before the as-of date leaves one bucket —
    # the last 30 days — and the demand of the 5 earlier days is not in
    # the history.
    late = history_for_pair("ITEM-319-35", "SITE-319-35", AS_OF)
    assert [
        (bucket["bucket_start"], bucket["bucket_end"], bucket["quantity"])
        for bucket in late
    ] == [(AS_OF - timedelta(days=29), AS_OF, 3.0)]

    # A first demand 59 days before: two buckets, the first demand on
    # the oldest bucket's start.
    fifty_nine = history_for_pair("ITEM-319-59", "SITE-319-59", AS_OF)
    assert [
        (bucket["bucket_start"], bucket["bucket_end"], bucket["quantity"])
        for bucket in fifty_nine
    ] == [
        (AS_OF - timedelta(days=59), AS_OF - timedelta(days=30), 12.0),
        (AS_OF - timedelta(days=29), AS_OF, 4.0),
    ]

    # A first demand 60 days before also gives two buckets; the first
    # demand itself (AS_OF-60) is before the oldest bucket and not in
    # the history.
    sixty = history_for_pair("ITEM-319-60", "SITE-319-60", AS_OF)
    assert [
        (bucket["bucket_start"], bucket["bucket_end"], bucket["quantity"])
        for bucket in sixty
    ] == [
        (AS_OF - timedelta(days=59), AS_OF - timedelta(days=30), 6.0),
        (AS_OF - timedelta(days=29), AS_OF, 2.0),
    ]

    # A first demand fewer than 30 days before still gives one bucket.
    short = history_for_pair("ITEM-319-10", "SITE-319-10", AS_OF)
    assert [
        (bucket["bucket_start"], bucket["bucket_end"], bucket["quantity"])
        for bucket in short
    ] == [(AS_OF - timedelta(days=29), AS_OF, 12.0)]

    # A first demand more than 1,080 days before: the full 36 buckets,
    # oldest first, the pre-window first demand not in the history.
    long_ = history_for_pair("ITEM-319-LONG", "SITE-319-LONG", AS_OF)
    assert len(long_) == 36
    assert (
        long_[0]["bucket_start"],
        long_[0]["bucket_end"],
        long_[0]["quantity"],
    ) == (AS_OF - timedelta(days=1079), AS_OF - timedelta(days=1050), PATTERN[0])
    assert (
        long_[-1]["bucket_start"],
        long_[-1]["bucket_end"],
        long_[-1]["quantity"],
    ) == (AS_OF - timedelta(days=29), AS_OF, PATTERN[11])
    for position, bucket in enumerate(long_):
        assert bucket["quantity"] == float(PATTERN[position % 12])
        assert (bucket["bucket_end"] - bucket["bucket_start"]).days == 29

    # Each item-site with 12 buckets or fewer counts the same buckets
    # app.stats.demand counts.
    for item_code, site_code in (
        ("ITEM-319-35", "SITE-319-35"),
        ("ITEM-319-59", "SITE-319-59"),
        ("ITEM-319-60", "SITE-319-60"),
        ("ITEM-319-10", "SITE-319-10"),
    ):
        stats = demand_stats_for_pair(item_code, site_code, AS_OF)
        assert stats is not None
        assert stats["n_buckets"] <= 12
        assert len(history_for_pair(item_code, site_code, AS_OF)) == stats["n_buckets"]

    # ------------------------------------------------------------------
    # AC-1 (shape): GET /api/forecast/by-key for the 35-day item-site —
    # history_buckets 1 and one history entry.
    # ------------------------------------------------------------------

    resp = client.get(
        "/api/forecast/by-key",
        params={
            "item_code": "ITEM-319-35",
            "site_code": "SITE-319-35",
            "as_of": AS_OF_STR,
        },
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert detail["history_buckets"] == 1
    assert detail["history"] == [
        {
            "bucket_start": (AS_OF - timedelta(days=29)).isoformat(),
            "bucket_end": AS_OF_STR,
            "quantity": 3.0,
        }
    ]

    # ------------------------------------------------------------------
    # Review defect: as_of is None and the demand_events table is
    # empty. The default as-of resolves from demand_events alone (the
    # latest occurred_on, never later than today), so the history
    # reader never touches any other table: with
    # lead_time_observations missing and demand_events empty,
    # history_for_pair returns None instead of raising
    # psycopg2.errors.UndefinedTable.
    # ------------------------------------------------------------------

    # With demand present, the default as-of ends the buckets on the
    # latest occurred_on overall — the 10-day item-site's AS_OF-1
    # event, never later than today — so its one bucket is the 30
    # days ending there, holding both of its events.
    default_ = history_for_pair("ITEM-319-10", "SITE-319-10", None)
    assert [
        (bucket["bucket_start"], bucket["bucket_end"], bucket["quantity"])
        for bucket in default_
    ] == [(AS_OF - timedelta(days=30), AS_OF - timedelta(days=1), 12.0)]

    # The reviewed crash scenario: demand_events is empty, no as_of is
    # given, and lead_time_observations does not exist — the pre-fix
    # default fell back to that table and raised.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS lead_time_observations")
            cur.execute("TRUNCATE demand_events CASCADE")
        conn.commit()
    finally:
        conn.close()
    try:
        assert history_for_pair("ITEM-319-35", "SITE-319-35", None) is None
        assert history_for_pair("ITEM-319-LONG", "SITE-319-LONG", None) is None
    finally:
        # Restore the table exactly as migration 0004 defines it (a
        # snapshot of the migrated schema — columns, the generated
        # column, the check, the unique constraint and the index — is
        # identical after this restore), so the rest of the session
        # sees the migrated database.
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "CREATE TABLE lead_time_observations ("
                    "id VARCHAR(64) PRIMARY KEY, "
                    "business_key VARCHAR(128) NOT NULL, "
                    "supplier_code VARCHAR(64) NOT NULL, "
                    "item_code VARCHAR(64) NOT NULL, "
                    "site_code VARCHAR(64), "
                    "ordered_on DATE NOT NULL, "
                    "received_on DATE NOT NULL, "
                    "lead_time_days INTEGER GENERATED ALWAYS AS "
                    "(received_on - ordered_on) STORED NOT NULL, "
                    "source_system VARCHAR(64), "
                    "synced_at TIMESTAMP WITH TIME ZONE NOT NULL "
                    "DEFAULT now(), "
                    "CONSTRAINT lead_time_observations_check "
                    "CHECK (received_on >= ordered_on), "
                    "CONSTRAINT lead_time_observations_business_key_key "
                    "UNIQUE (business_key))"
                )
                cur.execute(
                    "CREATE INDEX ix_lead_time_observations_supplier_item "
                    "ON lead_time_observations (supplier_code, item_code)"
                )
            conn.commit()
        finally:
            conn.close()
