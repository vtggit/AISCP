"""Issue #453 proof: the recommendation position fields.

AC-1 — ``app.recommend.reorder.recommendations_for_all`` and
``app.recommend.reorder.recommendation_for_key`` add four fields to
every recommendation dict: ``on_hand_units`` (the item-site's
``on_hand.quantity_on_hand`` in whole units, 0 when the pair has no
on_hand row), ``on_order_units`` (the sum of the pair's open purchase
lines' ``open_units`` — the rows of the newest sync, the same read
``app.recommend.stockout`` uses — 0 when none), ``unit_cost`` (carried
from the ``item_sites`` row, null when absent) and
``suggested_order_quantity`` =
``max(0, reorder_point_units - (on_hand_units + on_order_units))`` for
a recommendable item-site, null when the recommendation has no
``reorder_point_units`` (a stopped, excess-only or otherwise
non-recommendable item-site). The on-hand and open-purchase reads reuse
the ``on_hand`` and ``open_purchase_lines`` repositories the ERP sync
writes, fetched once per call and joined by (item_code, site_code) like
the existing item_sites join, so the bulk endpoint stays a fixed number
of queries; no pre-existing field changes and the drift/excess/
stockout/scenario behaviours are untouched (their own proofs still
pass in the full suite).

AC-2 / AC-5 — against the test database (as-of 2026-09-30,
date.today pinned to 2026-10-02, service level 0.95, z 1.644854):

  * I453-1 @ S1 — 10.0 a day for 360 days (d 10, sd_d 0), lead times
    10, 10, 10 (LT 10, sd_LT 0) -> reorder point 100.0 (100 whole
    units); 30 on hand and 25 on one open line -> on_hand_units 30,
    on_order_units 25, suggested_order_quantity 45 (100 - (30 + 25));
  * I453-2 @ S1 — the same d/LT, 90 on hand and TWO open lines (25 and
    15) -> on_order_units 40 (both lines summed), position 130 meets
    the reorder point -> suggested_order_quantity 0 (the int 0, not
    null) and unit_cost null (no unit cost in the item_sites row);
  * I453-3 @ S1 — 5.0 a day (d 5), LT 8 -> reorder point 40.0 (40
    units); no on_hand row and no open line of the newest sync (a 999
    units line of an older sync is excluded) -> on_hand_units 0,
    on_order_units 0, suggested_order_quantity 40 =
    reorder_point_units;
  * I453-4 @ S1 — intermittent demand (two events, ADI 6) ->
    not_recommendable_demand with reorder_point_units null ->
    suggested_order_quantity null, while on_hand_units 7 and
    on_order_units 12 are still reported;
  * I453-1 @ S2 — the same item at a second site (200 on hand, 50
    open) -> on_hand_units 200 / on_order_units 50, proving the join
    is by (item_code, site_code) and not by item alone; position 250
    -> suggested 0;
  * I453-5 @ S1 — 2.0 a day, LT 5 -> reorder point 10.0 (10 units)
    with NO item_sites row: current settings and unit_cost null, 4 on
    hand + 3 open -> suggested 3; /by-key still 404s it (the
    item-site existence gate is unchanged).

For the same fixtures every pre-existing field is unchanged:
``status``, ``demand_class``, ``safety_stock``, ``reorder_point``,
``reorder_point_units``, ``safety_stock_units``, ``current``,
``delta_reorder_point``, ``demand_basis``, ``inputs``, ``method``,
``notes`` and the exact ``arithmetic`` string are asserted against the
hand-computed values, and both GET /api/recommendations and /by-key
carry the four new fields and equal the service-level results.

AC-3 — the new fields are optional in
``app.api.recommendations.RecommendationResponse`` and default to null
when not provided (the required list is unchanged).

AC-4 — backend/openapi.json is regenerated and the
RecommendationResponse schema lists all four new properties as
optional (nullable).

AC-5 — a computed suggested quantity of zero is represented as the int
0, never null; null is reserved for the non-recommendable item-sites.

The bulk endpoint stays a fixed number of SQL statements: the
cursors' ``execute`` calls are counted (not the connections — every
read opens its own connection, so counting connections would only
count the reads, not the statements the reads execute), and re-running
``recommendations_for_all`` with one more item-site (I453-6: 1.0 a
day, LT 3 -> reorder point 3.0 (3 units), no position rows ->
suggested 3) executes the same fixed number of SQL statements (9) as
before.

The three new integer fields are strict: the response model rejects a
float (even an integer-valued one) and a Decimal for ``on_hand_units``,
``on_order_units`` and ``suggested_order_quantity`` and accepts only a
true int or null.
"""

import json
import math
import statistics
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import psycopg2
import pytest
from pydantic import ValidationError

from app.api.recommendations import RecommendationResponse
from app.db.connection import get_connection
from app.recommend.reorder import (
    METHOD,
    recommendation_for_key,
    recommendations_for_all,
)

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
S1 = "S1"
S2 = "S2"
I1, I2, I3, I4, I5, I6 = "I453-1", "I453-2", "I453-3", "I453-4", "I453-5", "I453-6"
Z95 = statistics.NormalDist().inv_cdf(0.95)  # 1.644854

SYNC_AS_OF = "2026-09-30T00:00:00+00:00"
STALE_AS_OF = "2026-09-01T00:00:00+00:00"

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

# The fixed budget of recommendations_for_all for an explicit as_of:
# 2 demand_stats_for_all + 1 lead_time_stats_for_all + 1 item_sites +
# 1 on_hand + 1 open_purchase_lines + 1 settings + 1 history_for_all +
# 1 counted overrides.
FIXED_STATEMENTS = 11


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


def _arithmetic(d: float, lt: float) -> str:
    """The exact arithmetic string of the sd_d 0 / sd_LT 0 fixtures,
    with the same formatting the formula applies."""
    ss = Z95 * math.sqrt(lt * 0.0 + d * d * 0.0)  # 0.0
    rp = d * lt + ss
    return (
        "safety_stock = z * sqrt(LT * sd_d^2 + d^2 * sd_LT^2) "
        f"= {Z95:.6f} * sqrt({lt:g} * 0^2 + {d:g}^2 * 0^2) "
        f"= {ss:.2f}; "
        "reorder_point = d * LT + safety_stock "
        f"= {d:g} * {lt:g} + {ss:.2f} = {rp:.2f}"
    )


# Hand-computed expectations per (item_code, site_code).
EXPECTED = {
    (I1, S1): dict(
        status="ok",
        demand_class="smooth",
        safety_stock=0.0,
        reorder_point=100.0,
        safety_stock_units=0,
        reorder_point_units=100,
        current=(120.0, 5.0),
        delta=20.0,
        d=10.0,
        lt=10.0,
        on_hand=30,
        on_order=25,
        unit_cost=2.5,
        suggested=45,  # 100 - (30 + 25)
    ),
    (I1, S2): dict(
        status="ok",
        demand_class="smooth",
        safety_stock=0.0,
        reorder_point=100.0,
        safety_stock_units=0,
        reorder_point_units=100,
        current=(100.0, 0.0),
        delta=0.0,
        d=10.0,
        lt=10.0,
        on_hand=200,
        on_order=50,
        unit_cost=4.0,
        suggested=0,  # position 250 already exceeds the reorder point
    ),
    (I2, S1): dict(
        status="ok",
        demand_class="smooth",
        safety_stock=0.0,
        reorder_point=100.0,
        safety_stock_units=0,
        reorder_point_units=100,
        current=(100.0, 0.0),
        delta=0.0,
        d=10.0,
        lt=10.0,
        on_hand=90,
        on_order=40,  # 25 + 15, two open lines summed
        unit_cost=None,
        suggested=0,  # position 130 meets the reorder point
    ),
    (I3, S1): dict(
        status="ok",
        demand_class="smooth",
        safety_stock=0.0,
        reorder_point=40.0,
        safety_stock_units=0,
        reorder_point_units=40,
        current=(40.0, 2.0),
        delta=0.0,
        d=5.0,
        lt=8.0,
        on_hand=0,
        on_order=0,
        unit_cost=1.0,
        suggested=40,  # no position: the full reorder point
    ),
    (I4, S1): dict(
        status="not_recommendable_demand",
        demand_class="intermittent",
        safety_stock=None,
        reorder_point=None,
        safety_stock_units=None,
        reorder_point_units=None,
        current=(9.0, 1.0),
        delta=None,
        on_hand=7,
        on_order=12,
        unit_cost=3.0,
        suggested=None,  # no reorder_point_units -> null
    ),
    (I5, S1): dict(
        status="ok",
        demand_class="smooth",
        safety_stock=0.0,
        reorder_point=10.0,
        safety_stock_units=0,
        reorder_point_units=10,
        current=None,  # no item_sites row
        delta=None,
        d=2.0,
        lt=5.0,
        on_hand=4,
        on_order=3,
        unit_cost=None,  # no item_sites row
        suggested=3,  # 10 - (4 + 3)
    ),
    (I6, S1): dict(
        status="ok",
        demand_class="smooth",
        safety_stock=0.0,
        reorder_point=3.0,
        safety_stock_units=0,
        reorder_point_units=3,
        current=None,
        delta=None,
        d=1.0,
        lt=3.0,
        on_hand=0,
        on_order=0,
        unit_cost=None,
        suggested=3,  # no position rows at all
    ),
}


def _seed(cur) -> None:
    """The fixed-date rows: demand, lead times, item-sites, position."""
    # Smooth daily demand for 360 days (d = quantity, sd_d 0, smooth):
    # I1 at both sites (10/day), I2 (10/day), I3 (5/day), I5 (2/day).
    for item, site, quantity in (
        (I1, S1, 10.0),
        (I1, S2, 10.0),
        (I2, S1, 10.0),
        (I3, S1, 5.0),
        (I5, S1, 2.0),
    ):
        suffix = "s2" if site == S2 else "s1"
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-453-{item[-1]}-{suffix}-{i}",
                    f"de-453-{item[-1]}-{suffix}-{i}",
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
    # on the as-of: 2 non-zero of 12 buckets -> ADI 6 -> intermittent.
    cur.executemany(
        DEMAND_SQL,
        [
            (
                "id-de-453-4-1",
                "de-453-4-1",
                I4,
                S1,
                60.0,
                "2025-10-04",
                "shipment",
                "test",
            ),
            (
                "id-de-453-4-2",
                "de-453-4-2",
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
                    f"id-lt-453-{item[-1]}-{i}",
                    f"lt-453-{item[-1]}-{i}",
                    f"SUP-453-{item[-1]}",
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=duration)).isoformat(),
                    "test",
                ),
            )
    # item_sites: I2 has no unit cost, I5 has no row at all.
    cur.executemany(
        ITEM_SITE_SQL,
        [
            (f"id-is-453-1-{suffix}", I1, site, cost, rp, ss, "test")
            for site, suffix, cost, rp, ss in (
                (S1, "s1", 2.5, 120.0, 5.0),
                (S2, "s2", 4.0, 100.0, 0.0),
            )
        ]
        + [
            (f"id-is-453-{item[-1]}", item, S1, cost, rp, ss, "test")
            for item, cost, rp, ss in (
                (I2, None, 100.0, 0.0),
                (I3, 1.0, 40.0, 2.0),
                (I4, 3.0, 9.0, 1.0),
            )
        ],
    )
    # on_hand: no row for I3.
    cur.executemany(
        ON_HAND_SQL,
        [
            (f"id-oh-453-{item[-1]}-{suffix}", item, site, quantity, SYNC_AS_OF, "test")
            for item, site, suffix, quantity in (
                (I1, S1, "s1", 30.0),
                (I1, S2, "s2", 200.0),
                (I2, S1, "s1", 90.0),
                (I4, S1, "s1", 7.0),
                (I5, S1, "s1", 4.0),
            )
        ],
    )
    # Open purchase lines of the newest sync: I2 has two lines (25 and
    # 15); I3's 999 units line belongs to an OLDER sync and must not
    # count (the newest sync is the source of truth, as in stockout).
    cur.executemany(
        OPEN_LINE_SQL,
        [
            (
                f"id-ol-{key}",
                f"bk-453-{key}",
                item,
                site,
                f"SUP-453-{item[-1]}",
                "po",
                units,
                "2026-10-05",
                "2026-09-20",
                SYNC_AS_OF,
                "test",
            )
            for key, item, site, units in (
                ("1-s1", I1, S1, 25.0),
                ("1-s2", I1, S2, 50.0),
                ("2-1", I2, S1, 25.0),
                ("2-2", I2, S1, 15.0),
                ("4-1", I4, S1, 12.0),
                ("5-1", I5, S1, 3.0),
            )
        ],
    )
    cur.execute(
        OPEN_LINE_SQL,
        (
            "id-ol-453-3-stale",
            "bk-453-3-stale",
            I3,
            S1,
            "SUP-453-3",
            "po",
            999.0,
            "2026-09-10",
            "2026-08-20",
            STALE_AS_OF,
            "test",
        ),
    )


def _seed_i6(cur) -> None:
    """One more item-site (I453-6): 1.0 a day, LT 3, no position rows."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-453-6-{i}",
                f"de-453-6-{i}",
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
                f"id-lt-453-6-{i}",
                f"lt-453-6-{i}",
                "SUP-453-6",
                I6,
                ordered.isoformat(),
                (ordered + timedelta(days=3)).isoformat(),
                "test",
            ),
        )


def _check_row(row: dict, key: tuple[str, str], as_of: date | str) -> None:
    """Every field of one recommendation row, old and new."""
    expected = EXPECTED[key]
    assert row["item_code"] == key[0]
    assert row["site_code"] == key[1]
    assert row["as_of"] == as_of
    assert row["status"] == expected["status"]
    assert row["demand_class"] == expected["demand_class"]
    assert row["method"] == METHOD
    assert row["demand_basis"] == "history"
    assert row["notes"] == []
    if expected["safety_stock"] is None:
        # Non-recommendable: the recommendation fields are all null.
        assert row["safety_stock"] is None
        assert row["reorder_point"] is None
        assert row.get("safety_stock_units") is None
        assert row.get("reorder_point_units") is None
        assert row.get("arithmetic") is None
    else:
        assert row["safety_stock"] == pytest.approx(expected["safety_stock"])
        assert row["reorder_point"] == pytest.approx(expected["reorder_point"])
        assert row["safety_stock_units"] == expected["safety_stock_units"]
        assert row["reorder_point_units"] == expected["reorder_point_units"]
        assert row["inputs"] == {
            "d": expected["d"],
            "sd_d": 0.0,
            "lt": expected["lt"],
            "sd_lt": 0.0,
            "z": Z95,
            "service_level": 0.95,
        }
        assert row["arithmetic"] == _arithmetic(expected["d"], expected["lt"])
    if expected["current"] is None:
        assert row["current"] == {"reorder_point": None, "safety_stock": None}
    else:
        assert row["current"] == {
            "reorder_point": expected["current"][0],
            "safety_stock": expected["current"][1],
        }
    if expected["delta"] is None:
        assert row["delta_reorder_point"] is None
    else:
        assert row["delta_reorder_point"] == pytest.approx(expected["delta"])
    # The four new fields.
    assert row["on_hand_units"] == expected["on_hand"]
    assert row["on_order_units"] == expected["on_order"]
    if expected["unit_cost"] is None:
        assert row["unit_cost"] is None
    else:
        assert row["unit_cost"] == pytest.approx(expected["unit_cost"])
    assert row["suggested_order_quantity"] == expected["suggested"]
    # A computed suggestion is exactly max(0, rp_units - position).
    if expected["reorder_point_units"] is not None:
        assert row["suggested_order_quantity"] == max(
            0,
            expected["reorder_point_units"]
            - (expected["on_hand"] + expected["on_order"]),
        )
    # AC-5: zero is the int 0, never null.
    if expected["suggested"] == 0:
        assert type(row["suggested_order_quantity"]) is int
    elif expected["suggested"] is None:
        assert row["suggested_order_quantity"] is None


def test_issue453_freeform(client, monkeypatch):
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    # --- seed ------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # --- service level: the bulk list ------------------------------------
    rows = recommendations_for_all(as_of=AS_OF)
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (I1, S1),
        (I1, S2),
        (I2, S1),
        (I3, S1),
        (I4, S1),
        (I5, S1),
    ]
    first_keys = [key for key in EXPECTED if key != (I6, S1)]
    by_key = {(row["item_code"], row["site_code"]): row for row in rows}
    for key in first_keys:
        _check_row(by_key[key], key, AS_OF)

    # --- service level: one key equals its list row ----------------------
    for key in ((I1, S1), (I2, S1), (I3, S1), (I4, S1)):
        single = recommendation_for_key(key[0], key[1], as_of=AS_OF)
        assert single == by_key[key]

    # --- AC-3: the response model fields are optional, defaulting null ---
    minimal = RecommendationResponse(
        item_code="A",
        site_code="B",
        as_of=AS_OF,
        status="ok",
        demand_class="smooth",
        method=METHOD,
        notes=[],
        current={"reorder_point": None, "safety_stock": None},
    )
    assert minimal.on_hand_units is None
    assert minimal.on_order_units is None
    assert minimal.unit_cost is None
    assert minimal.suggested_order_quantity is None
    dumped = minimal.model_dump()
    assert dumped["on_hand_units"] is None
    assert dumped["on_order_units"] is None
    assert dumped["unit_cost"] is None
    assert dumped["suggested_order_quantity"] is None

    # The three new integer fields are strict: a true int (and null)
    # is accepted, a float — even an integer-valued one — and a
    # Decimal are not unit counts and are rejected.
    def _response(**overrides):
        return RecommendationResponse(
            item_code="A",
            site_code="B",
            as_of=AS_OF,
            status="ok",
            demand_class="smooth",
            method=METHOD,
            notes=[],
            current={"reorder_point": None, "safety_stock": None},
            **overrides,
        )

    whole = _response(
        on_hand_units=30,
        on_order_units=25,
        suggested_order_quantity=45,
    )
    assert type(whole.on_hand_units) is int
    assert type(whole.on_order_units) is int
    assert type(whole.suggested_order_quantity) is int
    for bad in (45.0, Decimal("45")):
        for field in (
            "on_hand_units",
            "on_order_units",
            "suggested_order_quantity",
        ):
            with pytest.raises(ValidationError):
                _response(**{field: bad})

    # --- AC-4: the committed OpenAPI contract carries the new fields -----
    openapi = json.loads(OPENAPI.read_text())
    schema = openapi["components"]["schemas"]["RecommendationResponse"]
    for name, kind in (
        ("on_hand_units", "integer"),
        ("on_order_units", "integer"),
        ("unit_cost", "number"),
        ("suggested_order_quantity", "integer"),
    ):
        types = {option.get("type") for option in schema["properties"][name]["anyOf"]}
        assert kind in types and "null" in types, (name, types)
    assert schema["required"] == [
        "item_code",
        "site_code",
        "as_of",
        "status",
        "demand_class",
        "method",
        "notes",
        "current",
    ]

    # --- API: the list route carries the new fields ----------------------
    response = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert response.status_code == 200, response.text
    api_rows = {(row["item_code"], row["site_code"]): row for row in response.json()}
    assert set(api_rows) == set(first_keys)
    for key in first_keys:
        _check_row(api_rows[key], key, AS_OF_STR)
        # The new fields are present in every body, old ones unchanged.
        for field in (
            "on_hand_units",
            "on_order_units",
            "unit_cost",
            "suggested_order_quantity",
            "reorder_point",
            "safety_stock",
            "reorder_point_units",
            "delta_reorder_point",
            "demand_basis",
            "arithmetic",
            "status",
        ):
            assert field in api_rows[key]
    # The API bodies equal the service-level results (date as string).
    for key, row in by_key.items():
        api_row = dict(api_rows[key])
        assert api_row["as_of"] == AS_OF_STR
        assert row["as_of"] == AS_OF

    # --- API: the default as_of resolves to the pinned as-of -------------
    response = client.get("/api/recommendations")
    assert response.status_code == 200, response.text
    default_rows = {
        (row["item_code"], row["site_code"]): row for row in response.json()
    }
    assert all(row["as_of"] == AS_OF_STR for row in default_rows.values())
    assert default_rows[(I1, S1)]["suggested_order_quantity"] == 45

    # --- API: /by-key carries the new fields, the gate is unchanged ------
    for key in ((I1, S1), (I1, S2), (I2, S1), (I3, S1), (I4, S1)):
        response = client.get(
            "/api/recommendations/by-key",
            params={"item_code": key[0], "site_code": key[1]},
        )
        assert response.status_code == 200, response.text
        _check_row(response.json(), key, AS_OF_STR)
        assert response.json() == api_rows[key]
    # I5 has no item_sites row: the by-key 404 gate is unchanged.
    response = client.get(
        "/api/recommendations/by-key",
        params={"item_code": I5, "site_code": S1},
    )
    assert response.status_code == 404

    # --- the bulk endpoint stays a fixed number of SQL statements -------
    # The cursor's execute calls are counted, not the connections: every
    # read opens its own connection, so counting connections would only
    # count the reads, not the statements the reads execute.
    _, count6 = _measure(lambda: recommendations_for_all(as_of=AS_OF))
    assert count6 == FIXED_STATEMENTS

    # One more item-site (I6): every bulk read is per-table, so the
    # number of SQL statements is unchanged.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed_i6(cur)
        conn.commit()
    finally:
        conn.close()
    rows, count7 = _measure(lambda: recommendations_for_all(as_of=AS_OF))
    assert count7 == count6 == FIXED_STATEMENTS
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (I1, S1),
        (I1, S2),
        (I2, S1),
        (I3, S1),
        (I4, S1),
        (I5, S1),
        (I6, S1),
    ]
    i6 = rows[-1]
    _check_row(i6, (I6, S1), AS_OF)
    # The new item-site's suggestion equals its full reorder point
    # (no on-hand row, no open lines).
    assert i6["suggested_order_quantity"] == i6["reorder_point_units"] == 3
