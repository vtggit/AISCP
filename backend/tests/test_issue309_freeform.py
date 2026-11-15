"""Issue #309 proof: the forecast list and the item-site history buckets.

The proof covers, in one test, against the test database (demand events
inserted for the test, as of 2026-08-31):

* the history reader — ``app.forecast.history.history_for_pair`` reads
  one item-site's demand from ``demand_events`` into consecutive 30-day
  buckets ending on the as-of date, with the same bucket alignment as
  ``app.stats.demand`` (bucket ``k`` covers the 30 days ending on
  ``as_of - 30k``), oldest first, each bucket carrying ``bucket_start``,
  ``bucket_end`` and ``quantity``, a day without demand counting as
  zero: a three-bucket known history with hand-picked event dates, a
  first demand that lands exactly on a 30-day boundary and one that
  does not (each leaves a single last-30-days bucket, the demand of
  the earlier days not in the history), and a late first demand that
  leaves a single 30-day bucket; a pair with
  no demand events returns None; with no ``as_of`` supplied the buckets
  end on the shared ``app.stats.demand.default_as_of`` (here the latest
  ``occurred_on``, which the demand-stats default agrees with);
* the seasonal item-site — 36 buckets of a repeating 12-bucket pattern
  (sum 60) get ``seasonal`` with a perfect backtest (mae 0, baseline
  the mean's 7/3, wape 0, bias 0) and 12 forecast buckets whose first
  starts the day after the as-of date;
* the list — one row per item-site with a forecast (the ``no_demand``
  item-site, whose only demand event falls outside the 360-day stats
  window, is absent), every row carrying item_code, site_code, as_of,
  demand_class, method, params, history_buckets, total_forecast (the
  sum of the 12 buckets), forecast, backtest and arithmetic; rows
  ordered by total_forecast descending, then item_code and site_code —
  including a three-way total_forecast tie at 144 broken by site_code
  and item_code, a two-way tie at 60 broken by item_code and a two-way
  tie at 24 broken by item_code;
* paging (limit 1..1000, default 100, offset) and the ``demand_class``
  filter (smooth / lumpy / the empty classes), with a 422 for a
  malformed class, a malformed ``as_of`` date, an out-of-range limit
  and offset, and a NUL byte or an over-long item code in ``by-key``;
* ``by-key`` — the row plus ``history``, the buckets the forecast was
  built from (exact boundaries and quantities, 36 for the seasonal
  item-site, one for each boundary first-demand item-site), and 404
  for an item-site with no demand events and for the ``no_demand``
  item-site;
* the shared default as-of date — a list call without ``as_of`` resolves
  to the latest ``occurred_on`` and returns the same rows as the
  explicit-as-of call;
* an unauthenticated request is refused with 401 on both operations;
* the whole proof runs as a NON-ADMIN (the development token's role is
  ``user``, asserted via ``GET /api/auth/me``), and a second pass after
  a full reload (a fresh ``TestClient`` on the plain ``:user`` token)
  sees the identical list and by-key rows: there is no admin-only
  control on the forecast operations, the page still renders for a
  plain user, and an admin sees nothing different.
"""

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db.connection import get_connection
from app.forecast.history import history_for_pair
from app.main import app
from app.stats.demand import demand_stats_for_all

# The as-of date every explicit call uses; the latest seeded demand
# event falls on this day, so it is also the shared default as-of.
AS_OF = date(2026, 8, 31)
AS_OF_STR = AS_OF.isoformat()

# A repeating 12-bucket pattern of positive quantities (demand
# events must be positive); sum 60, mean 5.0.
PATTERN = [10, 3, 6, 2, 8, 1, 7, 6, 9, 4, 2, 2]

ROW_FIELDS = {
    "item_code",
    "site_code",
    "as_of",
    "demand_class",
    "method",
    "params",
    "history_buckets",
    "total_forecast",
    "forecast",
    "total_effective_forecast",
    "total_signal_quantity",
    "overridden_buckets",
    "backtest",
    "arithmetic",
}


def _seed() -> None:
    """Demand events for eight item-sites, all on fixed dates.

    * A/SITE-309-A and A/SITE-309-B and E: one bucket of 12 (total 144);
    * B: three buckets, 6 / 5 / 4 oldest first (total 60);
    * C: a late first demand — one bucket of 15 (total 180);
    * L: lumpy — 12 history buckets [3, 1, 0, ..., 20] (total 24);
    * S: 36 buckets of PATTERN, one event per bucket at its start
      (total 60, seasonal);
    * P1: first demand exactly on a 30-day boundary (AS_OF-30, the
      bucket_end of the second-newest bucket) — one bucket 3 (total
      36); the boundary-day 9 is before the last 30 days;
    * P2: first demand off the boundary (AS_OF-31, two days before the
      last 30 days) — one bucket 2 (total 24); the 7 is not in the
      history;
    * D: no_demand — a single event outside the 360-day stats window.
    """
    events = []

    def add(item: str, site: str, day: date, quantity: float, tag: str) -> None:
        events.append(
            (
                f"id-de-309-{tag}",
                f"de-309-{tag}",
                item,
                site,
                quantity,
                day.isoformat(),
                "shipment",
                "test",
            )
        )

    add("ITEM-309-A", "SITE-309-A", AS_OF - timedelta(days=3), 12.0, "a1")
    add("ITEM-309-A", "SITE-309-B", AS_OF - timedelta(days=3), 12.0, "a2")
    add("ITEM-309-B", "SITE-309-B", AS_OF - timedelta(days=89), 6.0, "b1")
    add("ITEM-309-B", "SITE-309-B", AS_OF - timedelta(days=50), 3.0, "b2")
    add("ITEM-309-B", "SITE-309-B", AS_OF - timedelta(days=33), 2.0, "b3")
    add("ITEM-309-B", "SITE-309-B", AS_OF - timedelta(days=5), 4.0, "b4")
    add("ITEM-309-C", "SITE-309-C", AS_OF - timedelta(days=5), 10.0, "c1")
    add("ITEM-309-C", "SITE-309-C", AS_OF, 5.0, "c2")
    add("ITEM-309-E", "SITE-309-E", AS_OF - timedelta(days=3), 12.0, "e1")
    add("ITEM-309-L", "SITE-309-L", AS_OF - timedelta(days=359), 3.0, "l1")
    add("ITEM-309-L", "SITE-309-L", AS_OF - timedelta(days=280), 1.0, "l2")
    add("ITEM-309-L", "SITE-309-L", AS_OF - timedelta(days=10), 20.0, "l3")
    # One event per bucket, at the bucket's first day, so the oldest
    # bucket (AS_OF-1079 .. AS_OF-1050) is counted and the pair gets
    # the full 36.
    for position in range(36):
        add(
            "ITEM-309-S",
            "SITE-309-S",
            AS_OF - timedelta(days=30 * (35 - position) + 29),
            float(PATTERN[position % 12]),
            f"s{position}",
        )
    # P1: the first demand lands exactly on a 30-day boundary —
    # AS_OF-30 is the bucket_end of the second-newest bucket, one day
    # before the single bucket's start.
    add("ITEM-309-P1", "SITE-309-P1", AS_OF - timedelta(days=30), 9.0, "p1a")
    add("ITEM-309-P1", "SITE-309-P1", AS_OF - timedelta(days=2), 3.0, "p1b")
    # P2: the first demand is two days before the single bucket's
    # start, off the boundary.
    add("ITEM-309-P2", "SITE-309-P2", AS_OF - timedelta(days=31), 7.0, "p2a")
    add("ITEM-309-P2", "SITE-309-P2", AS_OF - timedelta(days=1), 2.0, "p2b")
    add("ITEM-309-D", "SITE-309-D", AS_OF - timedelta(days=400), 1.0, "d1")

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


def test_issue309_freeform(client, user_token, admin_headers):
    approx = pytest.approx
    _seed()

    # ------------------------------------------------------------------
    # AC-1: the history reader — bucket boundaries and quantities for a
    # known history, including a late first demand.
    # ------------------------------------------------------------------

    # B: four events in three 30-day buckets; days without demand count
    # as zero. Oldest first: (AS_OF-89..AS_OF-60)=6, (AS_OF-59..AS_OF-30)
    # =3+2, (AS_OF-29..AS_OF)=4.
    known = history_for_pair("ITEM-309-B", "SITE-309-B", AS_OF)
    assert [
        (bucket["bucket_start"], bucket["bucket_end"], bucket["quantity"])
        for bucket in known
    ] == [
        (AS_OF - timedelta(days=89), AS_OF - timedelta(days=60), 6.0),
        (AS_OF - timedelta(days=59), AS_OF - timedelta(days=30), 5.0),
        (AS_OF - timedelta(days=29), AS_OF, 4.0),
    ]

    # C: the first demand is only five days before the as-of date, so
    # the pair gets a single bucket; both events fall in it.
    late = history_for_pair("ITEM-309-C", "SITE-309-C", AS_OF)
    assert [
        (bucket["bucket_start"], bucket["bucket_end"], bucket["quantity"])
        for bucket in late
    ] == [(AS_OF - timedelta(days=29), AS_OF, 15.0)]

    # P1: the first demand lands exactly on a 30-day boundary —
    # AS_OF-30, the bucket_end of the second-newest bucket — one day
    # before the single bucket's start, so only the last 30 days are in
    # the history and the boundary-day demand is left out.
    boundary = history_for_pair("ITEM-309-P1", "SITE-309-P1", AS_OF)
    assert [
        (bucket["bucket_start"], bucket["bucket_end"], bucket["quantity"])
        for bucket in boundary
    ] == [(AS_OF - timedelta(days=29), AS_OF, 3.0)]

    # P2: the first demand is not on a bucket-end boundary (AS_OF-31,
    # two days before the single bucket's start) — the pair gets one
    # bucket and the first demand is not in the history.
    in_bucket = history_for_pair("ITEM-309-P2", "SITE-309-P2", AS_OF)
    assert [
        (bucket["bucket_start"], bucket["bucket_end"], bucket["quantity"])
        for bucket in in_bucket
    ] == [(AS_OF - timedelta(days=29), AS_OF, 2.0)]

    # S: the full 36 buckets, oldest first, one event per bucket.
    seasonal = history_for_pair("ITEM-309-S", "SITE-309-S", AS_OF)
    assert len(seasonal) == 36
    assert (
        seasonal[0]["bucket_start"],
        seasonal[0]["bucket_end"],
        seasonal[0]["quantity"],
    ) == (AS_OF - timedelta(days=1079), AS_OF - timedelta(days=1050), PATTERN[0])
    assert (
        seasonal[-1]["bucket_start"],
        seasonal[-1]["bucket_end"],
        seasonal[-1]["quantity"],
    ) == (AS_OF - timedelta(days=29), AS_OF, PATTERN[11])
    for position, bucket in enumerate(seasonal):
        assert bucket["quantity"] == float(PATTERN[position % 12])
        assert (
            bucket["bucket_end"] - bucket["bucket_start"]
        ).days == 29  # every 30-day bucket
        if position:
            assert bucket["bucket_start"] == seasonal[position - 1][
                "bucket_end"
            ] + timedelta(days=1)

    # A pair with no demand events at all returns None.
    assert history_for_pair("ITEM-309-NONE", "SITE-309-NONE", AS_OF) is None

    # The default as-of date: no as_of supplied, the buckets end on the
    # shared app.stats.demand default (here the latest occurred_on,
    # AS_OF) — the same buckets as the explicit call, and the same date
    # the demand-stats default resolves to.
    assert history_for_pair("ITEM-309-B", "SITE-309-B") == known
    assert {stats["as_of"] for stats in demand_stats_for_all(None)} == {AS_OF}

    # ------------------------------------------------------------------
    # AC-2: the list — rows, fields, order, the absent no_demand pair.
    # ------------------------------------------------------------------

    resp = client.get("/api/forecast", params={"as_of": AS_OF_STR})
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 9  # the no_demand item-site D is left out
    for row in rows:
        assert set(row) == ROW_FIELDS
        assert row["as_of"] == AS_OF_STR
        assert len(row["forecast"]) == 12
        for entry in row["forecast"]:
            assert set(entry) == {
                "bucket_start",
                "bucket_end",
                "quantity",
                "effective_quantity",
                "signal_quantity",
                "override_days",
            }
            assert (
                date.fromisoformat(entry["bucket_end"])
                - date.fromisoformat(entry["bucket_start"])
            ).days == 29
        assert row["total_forecast"] == approx(
            sum(e["quantity"] for e in row["forecast"])
        )
        if row["backtest"] is not None:
            assert set(row["backtest"]) == {"mae", "baseline_mae", "wape", "bias"}

    # total_forecast descending, then item_code, then site_code: a
    # three-way tie at 144 (A's two sites by site_code, then E by
    # item_code), a two-way tie at 60 (B before S by item_code) and a
    # two-way tie at 24 (L before P2 by item_code).
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        ("ITEM-309-C", "SITE-309-C"),
        ("ITEM-309-A", "SITE-309-A"),
        ("ITEM-309-A", "SITE-309-B"),
        ("ITEM-309-E", "SITE-309-E"),
        ("ITEM-309-B", "SITE-309-B"),
        ("ITEM-309-S", "SITE-309-S"),
        ("ITEM-309-P1", "SITE-309-P1"),
        ("ITEM-309-L", "SITE-309-L"),
        ("ITEM-309-P2", "SITE-309-P2"),
    ]
    assert [row["total_forecast"] for row in rows] == [
        180.0,
        144.0,
        144.0,
        144.0,
        60.0,
        60.0,
        36.0,
        24.0,
        24.0,
    ]
    assert "ITEM-309-D" not in {row["item_code"] for row in rows}

    by_key = {(row["item_code"], row["site_code"]): row for row in rows}

    # The late-first-demand row: one history bucket, mean, no backtest,
    # 12 forecast buckets of 15 starting the day after the as-of date.
    c_row = by_key[("ITEM-309-C", "SITE-309-C")]
    assert (
        c_row["demand_class"],
        c_row["method"],
        c_row["params"],
        c_row["history_buckets"],
        c_row["backtest"],
    ) == ("smooth", "mean", {}, 1, None)
    assert c_row["forecast"][0] == {
        "bucket_start": (AS_OF + timedelta(days=1)).isoformat(),
        "bucket_end": (AS_OF + timedelta(days=30)).isoformat(),
        "quantity": 15.0,
        "effective_quantity": 15.0,
        "signal_quantity": 0.0,
        "override_days": 0,
    }
    assert c_row["forecast"][-1] == {
        "bucket_start": (AS_OF + timedelta(days=331)).isoformat(),
        "bucket_end": (AS_OF + timedelta(days=360)).isoformat(),
        "quantity": 15.0,
        "effective_quantity": 15.0,
        "signal_quantity": 0.0,
        "override_days": 0,
    }
    assert c_row["arithmetic"].startswith("mean: ")

    # The three-bucket row: mean over all three buckets, no backtest.
    b_row = by_key[("ITEM-309-B", "SITE-309-B")]
    assert (
        b_row["demand_class"],
        b_row["method"],
        b_row["params"],
        b_row["history_buckets"],
        b_row["backtest"],
    ) == ("smooth", "mean", {}, 3, None)
    assert b_row["total_forecast"] == 60.0
    assert all(entry["quantity"] == 5.0 for entry in b_row["forecast"])

    # The two boundary first-demand rows: one history bucket each (the
    # last 30 days; the earlier first demand is not in the history),
    # the mean over it, no backtest, 12 forecast buckets of the mean.
    p1_row = by_key[("ITEM-309-P1", "SITE-309-P1")]
    assert (
        p1_row["demand_class"],
        p1_row["method"],
        p1_row["params"],
        p1_row["history_buckets"],
        p1_row["backtest"],
        p1_row["total_forecast"],
    ) == ("smooth", "mean", {}, 1, None, 36.0)
    assert all(entry["quantity"] == 3.0 for entry in p1_row["forecast"])
    p2_row = by_key[("ITEM-309-P2", "SITE-309-P2")]
    assert (
        p2_row["demand_class"],
        p2_row["method"],
        p2_row["params"],
        p2_row["history_buckets"],
        p2_row["backtest"],
        p2_row["total_forecast"],
    ) == ("smooth", "mean", {}, 1, None, 24.0)
    assert all(entry["quantity"] == 2.0 for entry in p2_row["forecast"])

    # The seasonal row: 36 history buckets, seasonal, a perfect
    # backtest (mae 0, the mean's baseline 7/3, wape 0, bias 0),
    # forecast quantities reproducing the pattern, total exactly 60.
    s_row = by_key[("ITEM-309-S", "SITE-309-S")]
    assert (
        s_row["demand_class"],
        s_row["method"],
        s_row["params"],
        s_row["history_buckets"],
    ) == ("smooth", "seasonal", {}, 36)
    assert s_row["backtest"]["mae"] == 0.0
    assert s_row["backtest"]["baseline_mae"] == approx(7 / 3)
    assert s_row["backtest"]["wape"] == 0.0
    assert s_row["backtest"]["bias"] == 0.0
    assert s_row["total_forecast"] == 60.0
    assert [entry["quantity"] for entry in s_row["forecast"]] == [
        float(quantity) for quantity in PATTERN
    ]
    assert s_row["arithmetic"].startswith("seasonal: ")
    assert (
        s_row["forecast"][0]["bucket_start"] == (AS_OF + timedelta(days=1)).isoformat()
    )
    assert (
        s_row["forecast"][-1]["bucket_end"] == (AS_OF + timedelta(days=360)).isoformat()
    )

    # The lumpy row: 12 history buckets [3, 1, 0, ..., 20], the lumpy
    # class limits the candidates to mean and sba, mean wins, and the
    # backtest ran.
    l_row = by_key[("ITEM-309-L", "SITE-309-L")]
    assert (
        l_row["demand_class"],
        l_row["method"],
        l_row["params"],
        l_row["history_buckets"],
    ) == ("lumpy", "mean", {}, 12)
    assert l_row["total_forecast"] == 24.0
    assert l_row["backtest"]["mae"] == approx(153977 / 41580)
    assert l_row["backtest"]["baseline_mae"] == approx(153977 / 41580)
    assert l_row["backtest"]["wape"] == approx(153977 / 138600)
    assert l_row["backtest"]["bias"] == approx(-118183 / 138600)

    # ------------------------------------------------------------------
    # AC-2: paging and the demand_class filter.
    # ------------------------------------------------------------------

    def paged(limit: int, offset: int) -> list[dict]:
        resp = client.get(
            "/api/forecast",
            params={"as_of": AS_OF_STR, "limit": limit, "offset": offset},
        )
        assert resp.status_code == 200, resp.text
        return resp.json()

    assert [row["item_code"] for row in paged(2, 0)] == ["ITEM-309-C", "ITEM-309-A"]
    assert [(row["item_code"], row["site_code"]) for row in paged(2, 2)] == [
        ("ITEM-309-A", "SITE-309-B"),
        ("ITEM-309-E", "SITE-309-E"),
    ]
    assert [row["item_code"] for row in paged(2, 4)] == ["ITEM-309-B", "ITEM-309-S"]
    assert [row["item_code"] for row in paged(2, 6)] == ["ITEM-309-P1", "ITEM-309-L"]
    assert [row["item_code"] for row in paged(2, 8)] == ["ITEM-309-P2"]
    assert paged(2, 9) == []
    assert len(paged(1000, 0)) == 9
    assert len(client.get("/api/forecast", params={"as_of": AS_OF_STR}).json()) == 9

    smooth = client.get(
        "/api/forecast", params={"as_of": AS_OF_STR, "demand_class": "smooth"}
    )
    assert smooth.status_code == 200, smooth.text
    assert [row["item_code"] for row in smooth.json()] == [
        "ITEM-309-C",
        "ITEM-309-A",
        "ITEM-309-A",
        "ITEM-309-E",
        "ITEM-309-B",
        "ITEM-309-S",
        "ITEM-309-P1",
        "ITEM-309-P2",
    ]
    lumpy = client.get(
        "/api/forecast", params={"as_of": AS_OF_STR, "demand_class": "lumpy"}
    )
    assert lumpy.status_code == 200, lumpy.text
    assert [row["item_code"] for row in lumpy.json()] == ["ITEM-309-L"]
    for empty_class in ("erratic", "intermittent", "no_demand"):
        resp = client.get(
            "/api/forecast", params={"as_of": AS_OF_STR, "demand_class": empty_class}
        )
        assert resp.status_code == 200, resp.text
        assert resp.json() == []

    # ------------------------------------------------------------------
    # AC-2: hostile query values answer 422, never 500.
    # ------------------------------------------------------------------

    for bad in (
        {"as_of": "not-a-date"},
        {"as_of": "2026-02-30"},
        {"demand_class": "bogus"},
        {"limit": 0},
        {"limit": 1001},
        {"offset": -1},
    ):
        params = {"as_of": AS_OF_STR, **bad}
        resp = client.get("/api/forecast", params=params)
        assert resp.status_code == 422, (bad, resp.status_code, resp.text)

    for bad in (
        {"item_code": "ITEM\x00-309-B", "site_code": "SITE-309-B"},
        {"item_code": "ITEM-309-B", "site_code": "SITE\x00-309-B"},
        {"item_code": "I" * 65, "site_code": "SITE-309-B"},
        {"item_code": "ITEM-309-B", "site_code": "SITE-309-B", "as_of": "soon"},
    ):
        resp = client.get("/api/forecast/by-key", params=bad)
        assert resp.status_code == 422, (bad, resp.status_code, resp.text)

    # A non-NUL control character is not a match, not an error: 404,
    # never 500.
    resp = client.get(
        "/api/forecast/by-key",
        params={"item_code": "ITEM-309-B", "site_code": "SITE\x01-309-B"},
    )
    assert resp.status_code == 404, resp.text

    # An as-of date long before any demand answers 200 with no rows.
    resp = client.get("/api/forecast", params={"as_of": "2020-01-01"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == []

    # ------------------------------------------------------------------
    # AC-2: by-key — the row plus history, and both 404 cases.
    # ------------------------------------------------------------------

    resp = client.get(
        "/api/forecast/by-key",
        params={
            "item_code": "ITEM-309-B",
            "site_code": "SITE-309-B",
            "as_of": AS_OF_STR,
        },
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert set(detail) == ROW_FIELDS | {"history"}
    assert detail["history"] == [
        {
            "bucket_start": b["bucket_start"].isoformat(),
            "bucket_end": b["bucket_end"].isoformat(),
            "quantity": b["quantity"],
        }
        for b in known
    ]
    assert {field: detail[field] for field in ROW_FIELDS} == b_row

    resp = client.get(
        "/api/forecast/by-key",
        params={
            "item_code": "ITEM-309-S",
            "site_code": "SITE-309-S",
            "as_of": AS_OF_STR,
        },
    )
    assert resp.status_code == 200, resp.text
    detail = resp.json()
    assert len(detail["history"]) == 36
    assert detail["history"][0] == {
        "bucket_start": (AS_OF - timedelta(days=1079)).isoformat(),
        "bucket_end": (AS_OF - timedelta(days=1050)).isoformat(),
        "quantity": float(PATTERN[0]),
    }
    assert detail["history"][-1] == {
        "bucket_start": (AS_OF - timedelta(days=29)).isoformat(),
        "bucket_end": AS_OF_STR,
        "quantity": float(PATTERN[11]),
    }
    assert {field: detail[field] for field in ROW_FIELDS} == s_row

    # P1/P2 by-key: the row plus the single history bucket — the last
    # 30 days; the earlier first demand (on the boundary for P1, two
    # days before the bucket start for P2) is not in the history.
    for item_code, site_code, quantity, total in (
        ("ITEM-309-P1", "SITE-309-P1", 3.0, 36.0),
        ("ITEM-309-P2", "SITE-309-P2", 2.0, 24.0),
    ):
        resp = client.get(
            "/api/forecast/by-key",
            params={"item_code": item_code, "site_code": site_code, "as_of": AS_OF_STR},
        )
        assert resp.status_code == 200, resp.text
        detail = resp.json()
        assert detail["history"] == [
            {
                "bucket_start": (AS_OF - timedelta(days=29)).isoformat(),
                "bucket_end": AS_OF_STR,
                "quantity": quantity,
            },
        ]
        assert (
            detail["method"],
            detail["history_buckets"],
            detail["backtest"],
            detail["total_forecast"],
        ) == ("mean", 1, None, total)

    # 404: the item-site has no demand events at all ...
    resp = client.get(
        "/api/forecast/by-key",
        params={
            "item_code": "ITEM-309-NONE",
            "site_code": "SITE-309-NONE",
            "as_of": AS_OF_STR,
        },
    )
    assert resp.status_code == 404, resp.text
    # ... and 404: the item-site has demand events but its demand class
    # is no_demand.
    resp = client.get(
        "/api/forecast/by-key",
        params={
            "item_code": "ITEM-309-D",
            "site_code": "SITE-309-D",
            "as_of": AS_OF_STR,
        },
    )
    assert resp.status_code == 404, resp.text

    # ------------------------------------------------------------------
    # AC-2: the shared default as-of date, and the OpenAPI document.
    # ------------------------------------------------------------------

    resp = client.get("/api/forecast")
    assert resp.status_code == 200, resp.text
    default_rows = resp.json()
    assert all(row["as_of"] == AS_OF_STR for row in default_rows)
    assert [
        (row["item_code"], row["site_code"], row["total_forecast"])
        for row in default_rows
    ] == [(row["item_code"], row["site_code"], row["total_forecast"]) for row in rows]

    spec = app.openapi()
    assert "/api/forecast" in spec["paths"]
    assert "/api/forecast/by-key" in spec["paths"]
    list_params = {
        p["name"] for p in spec["paths"]["/api/forecast"]["get"]["parameters"]
    }
    assert {"limit", "offset", "demand_class", "as_of"} <= list_params
    by_key_params = {
        p["name"] for p in spec["paths"]["/api/forecast/by-key"]["get"]["parameters"]
    }
    assert {"item_code", "site_code", "as_of"} <= by_key_params
    list_schema = spec["paths"]["/api/forecast"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert list_schema["type"] == "array"
    assert list_schema["items"] == {"$ref": "#/components/schemas/ForecastRow"}
    assert spec["paths"]["/api/forecast/by-key"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"] == {"$ref": "#/components/schemas/ForecastRowDetail"}
    row_schema = spec["components"]["schemas"]["ForecastRow"]
    assert set(row_schema["properties"]) == ROW_FIELDS
    detail_schema = spec["components"]["schemas"]["ForecastRowDetail"]
    assert set(detail_schema["properties"]) == ROW_FIELDS | {"history"}

    # ------------------------------------------------------------------
    # AC-3: an unauthenticated request is refused.
    # ------------------------------------------------------------------

    anonymous = TestClient(app)
    assert anonymous.get("/api/forecast").status_code == 401
    assert (
        anonymous.get(
            "/api/forecast/by-key",
            params={"item_code": "ITEM-309-B", "site_code": "SITE-309-B"},
        ).status_code
        == 401
    )

    # ------------------------------------------------------------------
    # Identity: the whole proof ran as a NON-ADMIN (role "user"), and a
    # second pass after a full reload — a fresh TestClient on the plain
    # ":user" token — sees the same list and by-key rows: no admin-only
    # control on the forecast operations, and an admin sees nothing
    # different.
    # ------------------------------------------------------------------

    me = client.get("/api/auth/me")
    assert me.status_code == 200, me.text
    assert me.json()["user"]["roles"] == ["user"]

    viewer = TestClient(app, headers={"Authorization": f"Bearer {user_token}"})
    resp = viewer.get("/api/forecast", params={"as_of": AS_OF_STR})
    assert resp.status_code == 200, resp.text
    viewer_rows = resp.json()
    assert [
        (row["item_code"], row["site_code"], row["total_forecast"])
        for row in viewer_rows
    ] == [(row["item_code"], row["site_code"], row["total_forecast"]) for row in rows]
    resp = viewer.get(
        "/api/forecast/by-key",
        params={
            "item_code": "ITEM-309-B",
            "site_code": "SITE-309-B",
            "as_of": AS_OF_STR,
        },
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["history"]) == 3

    admin = TestClient(app, headers=admin_headers)
    resp = admin.get("/api/forecast", params={"as_of": AS_OF_STR})
    assert resp.status_code == 200, resp.text
    assert resp.json() == viewer_rows
