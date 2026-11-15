"""Issue #460 proof: the site-grouped roll-forward projection.

AC-1 — ``app.aggregate.projection.aggregate_projection`` and
``GET /api/aggregate/projection`` (mounted in ``app.main`` with the
Bearer gate like the other routers) project the recommendable
portfolio forward over consecutive calendar-month buckets grouped by
``site_code``.  The recommendable set and the per item-site
active-basis daily demand ``d`` are reused from
``app.recommend.reorder.recommendations_for_all`` (the
``status "ok"`` item-sites, intermittent/lumpy excluded), so a
non-recommendable item-site never enters a group total.  Per
(site, bucket): ``projected_demand_units`` is the ceiling of the
group's summed ``d x days-in-month``; ``projected_supply_units`` is
the group's open purchase lines' ``open_units`` promised in that
month (a line promised outside the window contributes to no bucket);
``projected_on_hand_units`` rolls forward from the group's summed
current ``on_hand`` as ``prior + supply - demand``;
``projected_shortfall`` is true exactly when the rolled on-hand is
below zero.  The dollar view sums each item-site's units at its
``item_sites.unit_cost`` like scenario planning: an item-site without
a unit cost contributes zero and is counted in
``item_sites_without_unit_cost``.  The excess threshold is not
applied.  The endpoint is read-only and performs a fixed number of
reads (one ``recommendations_for_all``, one on-hand read, one
open-purchase-line read) whatever the number of item-sites or
buckets.  ``as_of`` defaults to the current server date (the buckets
start from its month); an ``as_of`` after the current server date is
422.  ``months`` defaults to 6 and is bounded 1 to 12 inclusive: out
of bounds is 422, and the OpenAPI schema documents both bounds.

AC-2 — against the test database (as-of 2026-09-30, date.today
pinned to 2026-10-02, service level 0.95):

  * S1 (I460-1 d 10, I460-2 d 0.5, I460-3 d 0.25; on-hand 30 + 10):
    with months=3 the buckets are 2026-09 (30 days), 2026-10 (31)
    and 2026-11 (30); the September demand is ceil(10.75 x 30) =
    323 and the October demand ceil(10.75 x 31) = 334 — the
    fractional group sum is ceiled per bucket; the first bucket's
    on-hand starts from 40 (the summed current on-hand) and each
    bucket rolls forward prior + supply - demand (40 + 40 - 323 =
    -243, then -243 + 2000 - 334 = 1423, then 1423 - 323 = 1100);
    ``projected_shortfall`` is true exactly for the two negative
    buckets;
  * S1's September supply is the 40 units line promised 2026-09-20
    and the October supply the 2000 units line promised 2026-10-15,
    while the 99 units line promised 2026-12-10 (outside the window)
    and the 12 units line of the intermittent I460-4 (a
    non-recommendable item-site) contribute to no bucket;
  * S2 (I460-5 d 1, on-hand 100): demands 30 / 31 / 30, the 12 units
    line promised 2026-11-05 as the November supply, on-hand 70 / 39
    / 21, no shortfall;
  * the two sites come back as two groups ordered by site_code, each
    with months buckets ordered by month;
  * the dollar view sums at unit_cost (S1 September
    10 x 30 x 2.5 + 0.5 x 30 x 1.0 = 765.0, the costless I460-3
    adding zero dollars) with item_sites_without_unit_cost 1; S2 at
    3.0;
  * I460-4 (intermittent, on-hand 7, an open line of 12) is excluded
    from every S1 total;
  * the endpoint writes nothing: every table is unchanged after the
    calls;
  * the route is Bearer-gated at the mount: without the test's auth
    stub it answers 401 like the other gated routers;
  * a future as_of (2026-10-03, after the pinned server date) and a
    months above 12 (or below 1) are 422, while the boundaries
    (as_of = the server date, months 1 and 12) and the default as_of
    (buckets from the pinned today's month) are accepted;
  * the OpenAPI schema documents the months bounds (minimum 1,
    maximum 12);
  * adding one more recommendable item-site leaves the number of SQL
    statements fixed (one recommendations_for_all + one on-hand read
    + one open-purchase-line read = 11).
"""

import json
import statistics
from datetime import date, timedelta
from pathlib import Path

import psycopg2
import pytest
from fastapi.testclient import TestClient

from app.aggregate.projection import aggregate_projection
from app.db.connection import get_connection
from app.main import app
from app.recommend.reorder import recommendations_for_all

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
S1 = "S1"
S2 = "S2"
I1, I2, I3, I4, I5, I6 = (
    "I460-1",
    "I460-2",
    "I460-3",
    "I460-4",
    "I460-5",
    "I460-6",
)

SYNC_AS_OF = "2026-09-30T00:00:00+00:00"
OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"
URL = "/api/aggregate/projection"

# One recommendations_for_all (9 statements, as in the #453 proof) +
# one on_hand read + one open_purchase_lines read + the two demand-signal
# reads (one counted_signals, one counted_overrides).
FIXED_STATEMENTS = 14

# Hand-computed expectations for months=3, as_of 2026-09-30:
# buckets 2026-09 (30 days), 2026-10 (31), 2026-11 (30).
EXPECTED = {
    S1: {
        "without_unit_cost": 1,  # I460-3 has no unit cost
        "buckets": [
            {
                "month": "2026-09",
                "demand": 323,  # ceil((10 + 0.5 + 0.25) x 30) = ceil(322.5)
                "supply": 40.0,  # the line promised 2026-09-20
                "on_hand": -243.0,  # 40 + 40 - 323
                "shortfall": True,
                "demand_value": 765.0,  # 10x30x2.5 + 0.5x30x1.0 + 0.25x30x0
                "supply_value": 40.0,  # 40 x 1.0
            },
            {
                "month": "2026-10",
                "demand": 334,  # ceil(10.75 x 31) = ceil(333.25)
                "supply": 2000.0,  # the line promised 2026-10-15
                "on_hand": 1423.0,  # -243 + 2000 - 334
                "shortfall": False,
                "demand_value": 790.5,  # 10x31x2.5 + 0.5x31x1.0
                "supply_value": 5000.0,  # 2000 x 2.5
            },
            {
                "month": "2026-11",
                "demand": 323,
                "supply": 0.0,
                "on_hand": 1100.0,  # 1423 - 323
                "shortfall": False,
                "demand_value": 765.0,
                "supply_value": 0.0,
            },
        ],
    },
    S2: {
        "without_unit_cost": 0,
        "buckets": [
            {
                "month": "2026-09",
                "demand": 30,
                "supply": 0.0,
                "on_hand": 70.0,  # 100 - 30
                "shortfall": False,
                "demand_value": 90.0,  # 1x30x3.0
                "supply_value": 0.0,
            },
            {
                "month": "2026-10",
                "demand": 31,
                "supply": 0.0,
                "on_hand": 39.0,  # 70 - 31
                "shortfall": False,
                "demand_value": 93.0,
                "supply_value": 0.0,
            },
            {
                "month": "2026-11",
                "demand": 30,
                "supply": 12.0,  # the line promised 2026-11-05
                "on_hand": 21.0,  # 39 + 12 - 30
                "shortfall": False,
                "demand_value": 90.0,
                "supply_value": 36.0,  # 12 x 3.0
            },
        ],
    },
}

DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
LEAD_SQL = (
    "INSERT INTO lead_time_observations (id, business_key, supplier_code, "
    "item_code, ordered_on, received_on, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
ITEM_SITE_SQL = (
    "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
    "reorder_point, safety_stock, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s)"
)
ON_HAND_SQL = (
    "INSERT INTO on_hand (id, item_code, site_code, quantity_on_hand, "
    "as_of, source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)
OPEN_LINE_SQL = (
    "INSERT INTO open_purchase_lines (id, business_key, item_code, "
    "site_code, supplier_code, order_type, open_units, promised_on, "
    "ordered_on, as_of, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def _seed(cur) -> None:
    """The fixed-date rows: demand, lead times, item-sites, position."""
    # Smooth daily demand for 360 days (d = quantity, sd_d 0):
    # I1@S1 10/day, I2@S1 0.5/day, I3@S1 0.25/day, I5@S2 1/day.
    for item, site, quantity in (
        (I1, S1, 10.0),
        (I2, S1, 0.5),
        (I3, S1, 0.25),
        (I5, S2, 1.0),
    ):
        suffix = "s2" if site == S2 else "s1"
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-460-{item[-1]}-{suffix}-{i}",
                    f"de-460-{item[-1]}-{suffix}-{i}",
                    item,
                    site,
                    quantity,
                    (FIRST_DAY + timedelta(days=i)).isoformat(),
                    "shipment",
                    "test",
                )
                for i in range(360)
            ],
        )
    # I4: two events, 60 units each — one in the oldest bucket and one
    # on the as-of: 2 non-zero of 12 buckets -> ADI 6 -> intermittent
    # (not recommendable).
    cur.executemany(
        DEMAND_SQL,
        [
            (
                "id-de-460-4-1",
                "de-460-4-1",
                I4,
                S1,
                60.0,
                "2025-10-04",
                "shipment",
                "test",
            ),
            (
                "id-de-460-4-2",
                "de-460-4-2",
                I4,
                S1,
                60.0,
                AS_OF_STR,
                "shipment",
                "test",
            ),
        ],
    )
    # Three lead-time observations per item, all of the same duration
    # (LT = the duration, sd_LT 0, n_used 3 -> not low confidence):
    # I1 and I2 10 days, I3 8 days, I5 5 days. I4 has none.
    for item, duration, first in (
        (I1, 10, "2026-08-20"),
        (I2, 10, "2026-08-21"),
        (I3, 8, "2026-08-22"),
        (I5, 5, "2026-08-25"),
    ):
        base = date.fromisoformat(first)
        for i in range(3):
            ordered = base + timedelta(days=i)
            cur.execute(
                LEAD_SQL,
                (
                    f"id-lt-460-{item[-1]}-{i}",
                    f"lt-460-{item[-1]}-{i}",
                    f"SUP-460-{item[-1]}",
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=duration)).isoformat(),
                    "test",
                ),
            )
    # item_sites: I3 has no unit cost.
    cur.executemany(
        ITEM_SITE_SQL,
        [
            ("id-is-460-1", I1, S1, 2.5, 120.0, 5.0, "test"),
            ("id-is-460-2", I2, S1, 1.0, 100.0, 0.0, "test"),
            ("id-is-460-3", I3, S1, None, 40.0, 2.0, "test"),
            ("id-is-460-4", I4, S1, 3.0, 9.0, 1.0, "test"),
            ("id-is-460-5", I5, S2, 3.0, 10.0, 0.0, "test"),
        ],
    )
    # on_hand: no row for I2 (it contributes 0 to the group start).
    cur.executemany(
        ON_HAND_SQL,
        [
            ("id-oh-460-1", I1, S1, 30.0, SYNC_AS_OF, "test"),
            ("id-oh-460-3", I3, S1, 10.0, SYNC_AS_OF, "test"),
            ("id-oh-460-4", I4, S1, 7.0, SYNC_AS_OF, "test"),
            ("id-oh-460-5", I5, S2, 100.0, SYNC_AS_OF, "test"),
        ],
    )
    # Open purchase lines of the newest sync: I1 2000 units promised
    # 2026-10-15; I2 40 units promised 2026-09-20 and 99 units promised
    # 2026-12-10 (outside the 3-month window); I4 12 units promised
    # 2026-10-01 (a non-recommendable item-site); I5 12 units promised
    # 2026-11-05.
    cur.executemany(
        OPEN_LINE_SQL,
        [
            (
                "id-ol-460-1",
                "bk-460-1",
                I1,
                S1,
                "SUP-460-1",
                "po",
                2000.0,
                "2026-10-15",
                "2026-09-20",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-460-2a",
                "bk-460-2a",
                I2,
                S1,
                "SUP-460-2",
                "po",
                40.0,
                "2026-09-20",
                "2026-09-10",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-460-2b",
                "bk-460-2b",
                I2,
                S1,
                "SUP-460-2",
                "po",
                99.0,
                "2026-12-10",
                "2026-09-10",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-460-4",
                "bk-460-4",
                I4,
                S1,
                "SUP-460-4",
                "po",
                12.0,
                "2026-10-01",
                "2026-09-15",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-460-5",
                "bk-460-5",
                I5,
                S2,
                "SUP-460-5",
                "po",
                12.0,
                "2026-11-05",
                "2026-09-15",
                SYNC_AS_OF,
                "test",
            ),
        ],
    )


def _seed_i6(cur) -> None:
    """One more recommendable item-site (I460-6 @ S1): 1.0 a day, LT 3,
    5 on hand, no unit cost, no open lines."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-460-6-{i}",
                f"de-460-6-{i}",
                I6,
                S1,
                1.0,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(360)
        ],
    )
    base = date(2026, 8, 26)
    for i in range(3):
        ordered = base + timedelta(days=i)
        cur.execute(
            LEAD_SQL,
            (
                f"id-lt-460-6-{i}",
                f"lt-460-6-{i}",
                "SUP-460-6",
                I6,
                ordered.isoformat(),
                (ordered + timedelta(days=3)).isoformat(),
                "test",
            ),
        )
    cur.execute(
        ON_HAND_SQL,
        ("id-oh-460-6", I6, S1, 5.0, SYNC_AS_OF, "test"),
    )
    cur.execute(
        ITEM_SITE_SQL,
        ("id-is-460-6", I6, S1, None, 3.0, 0.0, "test"),
    )


def _check_group(group: dict, site_code: str) -> None:
    """Every field of one projected site group (months=3 fixture)."""
    expected = EXPECTED[site_code]
    assert group["group"] == {"dimension": "site", "code": site_code}
    assert group["item_sites_without_unit_cost"] == expected["without_unit_cost"]
    assert [row["month"] for row in group["buckets"]] == [
        row["month"] for row in expected["buckets"]
    ]
    for row, want in zip(group["buckets"], expected["buckets"]):
        assert row["projected_demand_units"] == want["demand"]
        assert row["projected_supply_units"] == pytest.approx(want["supply"])
        assert row["projected_on_hand_units"] == pytest.approx(want["on_hand"])
        assert row["projected_shortfall"] is want["shortfall"]
        assert row["projected_demand_value"] == pytest.approx(want["demand_value"])
        assert row["projected_supply_value"] == pytest.approx(want["supply_value"])
        # The shortfall flag tracks the sign of the rolled on-hand.
        assert row["projected_shortfall"] is (row["projected_on_hand_units"] < 0)


def _snapshot_tables() -> dict[str, list[tuple]]:
    """Every row of every user table, for the writes-nothing check."""
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version' "
                "ORDER BY tablename"
            )
            tables = [row[0] for row in cur.fetchall()]
            snapshot: dict[str, list[tuple]] = {}
            for table in tables:
                cur.execute(f'SELECT * FROM "{table}" ORDER BY 1')
                snapshot[table] = [tuple(row) for row in cur.fetchall()]
        return snapshot
    finally:
        conn.close()


class _CountingCursor:
    """A cursor that counts its execute calls, delegating the rest."""

    def __init__(self, real, counter):
        self._real = real
        self._counter = counter

    def execute(self, query, *args, **kwargs):
        self._counter[0] += 1
        return self._real.execute(query, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._real.close()
        return False


class _CountingConnection:
    """A connection whose cursors count their execute calls."""

    def __init__(self, real, counter):
        self._real = real
        self._counter = counter

    def cursor(self, *args, **kwargs):
        return _CountingCursor(self._real.cursor(*args, **kwargs), self._counter)

    def __getattr__(self, name):
        return getattr(self._real, name)


def _measure(fn):
    """Run fn with psycopg2.connect wrapped; (result, statements)."""
    counter = [0]
    real_connect = psycopg2.connect

    def counting_connect(*args, **kwargs):
        return _CountingConnection(real_connect(*args, **kwargs), counter)

    psycopg2.connect = counting_connect
    try:
        result = fn()
    finally:
        psycopg2.connect = real_connect
    return result, counter[0]


def test_issue460_freeform(client, monkeypatch):
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    # The projection resolves its default as_of and rejects a future
    # one against the current server date; the demand statistics'
    # default as_of is pinned too (the projection always passes an
    # explicit as_of to recommendations_for_all).
    monkeypatch.setattr("app.aggregate.projection.date", _FixedDate)
    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    # --- seed ------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    before = _snapshot_tables()

    # --- the recommendation set the projection reuses ---------------------
    recommendations = recommendations_for_all(as_of=AS_OF)
    by_key = {(row["item_code"], row["site_code"]): row for row in recommendations}
    # I4 is intermittent (ADI 6): not recommendable, out of every group.
    assert by_key[(I4, S1)]["status"] == "not_recommendable_demand"
    assert by_key[(I4, S1)]["demand_class"] == "intermittent"
    for key in ((I1, S1), (I2, S1), (I3, S1), (I5, S2)):
        assert by_key[key]["status"] == "ok"
    assert by_key[(I1, S1)]["inputs"]["d"] == pytest.approx(10.0)
    assert by_key[(I2, S1)]["inputs"]["d"] == pytest.approx(0.5)
    assert by_key[(I3, S1)]["inputs"]["d"] == pytest.approx(0.25)
    assert by_key[(I5, S2)]["inputs"]["d"] == pytest.approx(1.0)

    # --- the endpoint: two groups, ordered by site_code -------------------
    response = client.get(URL, params={"as_of": AS_OF_STR, "months": 3})
    assert response.status_code == 200, response.text
    groups = response.json()
    assert [group["group"]["code"] for group in groups] == [S1, S2]
    for group in groups:
        assert len(group["buckets"]) == 3
        _check_group(group, group["group"]["code"])

    # --- the roll-forward, bucket by bucket -------------------------------
    s1 = groups[0]["buckets"]
    # First bucket from the summed current on-hand (30 + 10; I2 has no
    # on_hand row and I4 is excluded): 40 + 40 - 323 = -243.
    assert s1[0]["projected_on_hand_units"] == pytest.approx(-243.0)
    # Each subsequent bucket: prior + supply - demand.
    assert s1[1]["projected_on_hand_units"] == pytest.approx(
        s1[0]["projected_on_hand_units"]
        + s1[1]["projected_supply_units"]
        - s1[1]["projected_demand_units"]
    )
    assert s1[2]["projected_on_hand_units"] == pytest.approx(
        s1[1]["projected_on_hand_units"] - s1[2]["projected_demand_units"]
    )
    s2 = groups[1]["buckets"]
    for previous, current in zip(s1, s1[1:]):
        assert current["projected_on_hand_units"] == pytest.approx(
            previous["projected_on_hand_units"]
            + current["projected_supply_units"]
            - current["projected_demand_units"]
        )
    for previous, current in zip(s2, s2[1:]):
        assert current["projected_on_hand_units"] == pytest.approx(
            previous["projected_on_hand_units"]
            + current["projected_supply_units"]
            - current["projected_demand_units"]
        )

    # --- supply: the lines promised in the bucket month, and only those --
    assert s1[0]["projected_supply_units"] == pytest.approx(40.0)  # 2026-09-20
    assert s1[1]["projected_supply_units"] == pytest.approx(2000.0)  # 2026-10-15
    assert s1[2]["projected_supply_units"] == pytest.approx(0.0)
    assert s2[2]["projected_supply_units"] == pytest.approx(12.0)  # 2026-11-05
    # The 99 units line promised 2026-12-10 (outside the window) and
    # I4's 12 units line (non-recommendable item-site) contribute to no
    # bucket: the S1 supplies are exactly 40 / 2000 / 0, not
    # 40 / 2012 / 99.
    assert [row["projected_supply_units"] for row in s1] == pytest.approx(
        [40.0, 2000.0, 0.0]
    )

    # --- the non-recommendable item-site is out of every S1 total ---------
    # Its d is not in the demand (10.75, not 10.75 + its mean), its
    # on-hand 7 is not in the start (40, not 47), its line 12 is not in
    # the October supply (2000, not 2012).
    assert s1[0]["projected_demand_units"] == 323  # ceil(10.75 x 30)
    assert s1[0]["projected_on_hand_units"] == pytest.approx(-243.0)
    assert (I4, S1) not in by_key or by_key[(I4, S1)]["status"] != "ok"

    # --- the dollar view, as in scenario planning -------------------------
    # I460-3 (no unit cost) adds zero dollars and is counted.
    assert groups[0]["item_sites_without_unit_cost"] == 1
    assert groups[1]["item_sites_without_unit_cost"] == 0
    assert s1[0]["projected_demand_value"] == pytest.approx(765.0)
    assert s1[1]["projected_demand_value"] == pytest.approx(790.5)
    assert s1[0]["projected_supply_value"] == pytest.approx(40.0)
    assert s1[1]["projected_supply_value"] == pytest.approx(5000.0)
    assert s2[2]["projected_demand_value"] == pytest.approx(90.0)
    assert s2[2]["projected_supply_value"] == pytest.approx(36.0)

    # --- the endpoint writes nothing ---------------------------------------
    after = _snapshot_tables()
    assert after == before

    # --- the mount-time Bearer gate ----------------------------------------
    with TestClient(app) as anonymous:
        response = anonymous.get(URL, params={"as_of": AS_OF_STR, "months": 3})
    assert response.status_code == 401, response.text
    assert response.headers.get("WWW-Authenticate") == "Bearer"

    # --- input bounds --------------------------------------------------------
    # A future as_of (after the pinned server date) is 422; the pinned
    # server date itself is the boundary and is accepted.
    future = (FIXED_TODAY + timedelta(days=1)).isoformat()  # 2026-10-03
    response = client.get(URL, params={"as_of": future, "months": 3})
    assert response.status_code == 422, response.text
    response = client.get(URL, params={"as_of": FIXED_TODAY.isoformat(), "months": 3})
    assert response.status_code == 200, response.text
    # A malformed as_of is 422, never a 500.
    assert client.get(URL, params={"as_of": "not-a-date"}).status_code == 422
    # months above 12 (or below 1) is 422; 1 and 12 are accepted.
    assert client.get(URL, params={"as_of": AS_OF_STR, "months": 13}).status_code == 422
    assert client.get(URL, params={"as_of": AS_OF_STR, "months": 0}).status_code == 422
    assert (
        client.get(URL, params={"as_of": AS_OF_STR, "months": "abc"}).status_code == 422
    )
    response = client.get(URL, params={"as_of": AS_OF_STR, "months": 1})
    assert response.status_code == 200, response.text
    assert [row["month"] for row in response.json()[0]["buckets"]] == ["2026-09"]
    response = client.get(URL, params={"as_of": AS_OF_STR, "months": 12})
    assert response.status_code == 200, response.text
    assert len(response.json()[0]["buckets"]) == 12
    # The default as_of is the current server date: the buckets start
    # from its month (2026-10).
    response = client.get(URL, params={"months": 3})
    assert response.status_code == 200, response.text
    assert [row["month"] for row in response.json()[0]["buckets"]] == [
        "2026-10",
        "2026-11",
        "2026-12",
    ]

    # --- the fixed number of reads -------------------------------------------
    rows, count = _measure(lambda: aggregate_projection(as_of=AS_OF, months=3))
    assert count == FIXED_STATEMENTS
    assert [group["group"]["code"] for group in rows] == [S1, S2]

    # One more recommendable item-site (I460-6 @ S1): every read is
    # per-table, so the number of SQL statements is unchanged.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed_i6(cur)
        conn.commit()
    finally:
        conn.close()
    rows, count_more = _measure(lambda: aggregate_projection(as_of=AS_OF, months=3))
    assert count_more == count == FIXED_STATEMENTS
    assert [group["group"]["code"] for group in rows] == [S1, S2]
    s1_more = rows[0]["buckets"][0]
    # I460-6 (d 1, 5 on hand, no unit cost) joins the S1 group: the
    # September demand is ceil(11.75 x 30) and the start on-hand 45.
    assert s1_more["projected_demand_units"] == 353
    assert s1_more["projected_on_hand_units"] == pytest.approx(45.0 + 40.0 - 353.0)
    assert rows[0]["item_sites_without_unit_cost"] == 2

    # --- the OpenAPI schema documents the bounds ------------------------------
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    operation = spec["paths"][URL]["get"]
    parameters = {param["name"]: param for param in operation["parameters"]}
    assert "as_of" in parameters
    months_schema = parameters["months"]["schema"]
    assert months_schema["minimum"] == 1
    assert months_schema["maximum"] == 12
    assert months_schema["default"] == 6
    assert "200" in operation["responses"]
