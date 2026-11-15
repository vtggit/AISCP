"""Issue #971 proof: the projection counts the demand signals by month.

AC-1 — ``app.aggregate.projection._projection_setup`` reads the current
signals of the projected item-sites once through
``app.forecast.signals.counted_signals`` and the counted overrides once
through ``app.forecast.overrides.counted_overrides`` — one batched read
each, on the normal connection path, whatever the number of
item-sites.  Each item-site's signal day share is, for each day after
the resolved as-of date that a counted signal's window covers and no
counted override covers, the signal's quantity over the days of its
window; the month's units, ``signal_units(month)``, are the day shares
falling in that calendar month (days outside the horizon count for
nothing) and every projection bucket carries the sum over its site
group's reviews as ``signal_units`` (the
``app.api.aggregate.ProjectionBucket`` model field, documented in
``backend/openapi.json``).  In the as-is roll each month's
``projected_demand_units`` and ``projected_demand_value`` include
``signal_units(month)`` at the item-site's unit cost by the existing
rule and the on-hand rolls subtract the same demand total.  In the
monthly review the on-hand rolls as
``prior + receipts - d x days-in-month - signal_units(month)`` and the
order-up-to level is ``rop + d x days-in-month + cover`` with ``rop``
the ``reorder_point_before_signals_units`` the setup reads and
``cover`` the sum of the review's day shares from the first day of the
month through the last day of the month plus ``ceil(lt)`` further days
(the lever-scaled ``lt``) — so a signal an earlier month's order
already carries does not raise a later order again.  A
``demand_multiplier`` never scales a signal.  ``planned_orders``,
``spend_against_budget``, ``deferral_candidates``, the release plan,
``GET /api/sop/review`` and ``GET /api/sop/scenarios`` read the new
values through the code they already call.  With no signals stored
every value is exactly what it is without them, field for field, with
no floating-point difference.

AC-2 — against the test database (date.today pinned to 2026-10-02 the
way test_issue927_freeform pins it, one item-site seeded the way that
test seeds one: 360 days of 10.0 a day ending 2026-09-30, three
10-day lead-time observations, unit cost 2.0, so d 10, sd_d 0, lt 10,
sd_lt 0 and the reorder point before the signals 100 = 10 x 10), with
the horizon as_of 2026-09-30, months 3 (2026-09, 30 days, 2026-10, 31,
2026-11, 30 — the third month) and the signals stored through
``POST /api/demand-signals``:

  * with on-hand 1400 (far above the order-up-to level, at most
    100 + 310 + 300) no order is placed in the months around the
    signal unless the signal needs one, and with no signal stored the
    group is exactly the no-signal roll (the bucket's field set gains
    ``signal_units`` 0.0);
  * a signal of 300 over the whole of the third month (30 days, 10 a
    day) adds 300 to that month's ``projected_demand_units`` and
    ``signal_units`` and 300 x 2.0 to its ``projected_demand_value``,
    leaves the first two months unchanged and leaves
    ``projected_on_hand_units`` 300 lower in the third month;
  * a signal of 310 over 31 days, 11 in one month and 20 in the next
    (10 a day), adds 110 and 200 to the two months'
    ``signal_units`` and demand;
  * with on-hand 0 (orders placed: 400 / 310 / 300, total 1010, the
    end planned on-hand 100, no planned shortfall) the 300-unit
    signal in the third month raises the total
    ``planned_receipts_units`` by exactly 300 (400 / 410 / 500, total
    1310) and the end ``planned_on_hand_units`` stays 100 — the
    signal is bought once, not twice;
  * a signal of 300 early in the third month (11-01 to 11-10, 30 a
    day) is carried by the order placed at the start of the month
    before (its cover window reaches 10 days in) and
    ``planned_shortfall`` stays false where the no-signal seed has
    none (400 / 610 / 300, total 1310, end 100);
  * an override covering part of the 31-day window (forecast period 2,
    10-31 to 11-29) removes those days' shares: 110 / 200 become 100
    / 0;
  * after a withdrawal the projection answers the no-signal values;
  * with ``demand_multiplier=2`` the third month's demand is
    2 x 10 x 30 + 300 = 900, so the signal is not doubled;
  * over the same data (on-hand 0, the 300 signal standing)
    ``GET /api/sop/review`` and ``GET /api/aggregate/spend`` carry the
    new figures and the review's third month of the horizon (2026-11)
    reads ``demand_units`` 600 with the 300 in it, ``planned_spend``
    500 x 2.0;
  * one ``GET /api/aggregate/projection`` calls ``counted_signals``
    once (and ``counted_overrides`` once), and ``psycopg2.connect`` is
    the same object before and during the call, checked from inside
    the wrapped ``counted_signals``.
"""

from datetime import date, timedelta

import psycopg2
from fastapi.testclient import TestClient

import app.aggregate.projection as projection_module
import app.sop.review as review_module
from app.db.connection import get_connection
from app.main import app

AS_OF = date(2026, 9, 30)  # the last seeded demand day
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
SYNC_AS_OF = "2026-09-30T00:00:00+00:00"
URL = "/api/aggregate/projection"

SITE = "S1"
ITEM = "I971-1"

# The seeded inputs.
D = 10.0  # daily demand (360 smooth days, zero variability)
LT = 10  # lead time, days (three identical observations)
COST = 2.0  # the item-site's unit cost
ROP = D * LT  # 100: the reorder point before the signals (sd 0)
HIGH = 1400.0  # on-hand: far above the order-up-to level (at most
# ROP + D x 31 + a whole third month of signal, 100 + 310 + 300)

SEP_DAYS, OCT_DAYS, NOV_DAYS, DEC_DAYS = 30, 31, 30, 31
SEP_DEMAND = D * SEP_DAYS  # 300
OCT_DEMAND = D * OCT_DAYS  # 310
NOV_DEMAND = D * NOV_DAYS  # 300
DEC_DEMAND = D * DEC_DAYS  # 310

# The 300-unit signal over the whole of the third month: 30 days, 10 a
# day — the month's units are exactly 300.
NOV_SHARE = 300 / NOV_DAYS  # 10.0
NOV_SIGNAL = NOV_DAYS * NOV_SHARE  # 300.0

# The 310-unit signal over 31 days (2026-10-21 to 2026-11-20): 10 a
# day, 11 days in October and 20 in November.
CROSS_SHARE = 310 / 31  # 10.0
CROSS_OCT = 11 * CROSS_SHARE  # 110.0
CROSS_NOV = 20 * CROSS_SHARE  # 200.0

# The 300-unit signal early in the third month (2026-11-01 to
# 2026-11-10): 30 a day, 10 days.
EARLY_SHARE = 300 / 10  # 30.0
EARLY_SIGNAL = 10 * EARLY_SHARE  # 300.0

BUCKET_KEYS = {
    "month",
    "projected_demand_units",
    "projected_supply_units",
    "projected_on_hand_units",
    "projected_shortfall",
    "projected_demand_value",
    "projected_supply_value",
    "signal_units",
    "planned_receipts_units",
    "planned_receipts_value",
    "planned_on_hand_units",
    "planned_shortfall",
}


def _bucket(month, demand, on_hand, signal, receipts, planned_on_hand):
    """One expected bucket row, the test's own arithmetic: zero supply
    (the seed has no open lines), the values at the seeded unit cost
    and the shortfall flags the arithmetic gives."""
    return {
        "month": month,
        "projected_demand_units": demand,
        "projected_supply_units": 0.0,
        "projected_on_hand_units": on_hand,
        "projected_shortfall": on_hand < 0,
        "projected_demand_value": demand * COST,
        "projected_supply_value": 0.0,
        "signal_units": signal,
        "planned_receipts_units": receipts,
        "planned_receipts_value": receipts * COST,
        "planned_on_hand_units": planned_on_hand,
        "planned_shortfall": planned_on_hand < 0,
    }


def _group(*buckets):
    """The one site group of the horizon (one item-site)."""
    return [
        {
            "group": {"dimension": "site", "code": SITE},
            "buckets": list(buckets),
            "item_sites_without_unit_cost": 0,
        }
    ]


# --- on-hand 1400: no order is placed in the months around the signal
# unless the signal needs one (the position never drops below ROP + D x
# days + cover, at most 100 + 310 + 300).
BASE_SEP_OH = HIGH - SEP_DEMAND  # 1100
BASE_OCT_OH = BASE_SEP_OH - OCT_DEMAND  # 790
BASE_NOV_OH = BASE_OCT_OH - NOV_DEMAND  # 490

# No signal: the no-signal roll.
BASELINE = _group(
    _bucket("2026-09", SEP_DEMAND, BASE_SEP_OH, 0.0, 0, BASE_SEP_OH),
    _bucket("2026-10", OCT_DEMAND, BASE_OCT_OH, 0.0, 0, BASE_OCT_OH),
    _bucket("2026-11", NOV_DEMAND, BASE_NOV_OH, 0.0, 0, BASE_NOV_OH),
)

# 300 over the whole of the third month: the first two months are
# unchanged, the third month's demand and value carry the 300 and its
# on-hand is 300 lower.
SIGNAL_NOV = _group(
    _bucket("2026-09", SEP_DEMAND, BASE_SEP_OH, 0.0, 0, BASE_SEP_OH),
    _bucket("2026-10", OCT_DEMAND, BASE_OCT_OH, 0.0, 0, BASE_OCT_OH),
    _bucket(
        "2026-11",
        NOV_DEMAND + NOV_SIGNAL,  # 300 + 300
        BASE_OCT_OH - NOV_DEMAND - NOV_SIGNAL,  # 790 - 600
        NOV_SIGNAL,
        0,
        BASE_OCT_OH - NOV_DEMAND - NOV_SIGNAL,
    ),
)

# 310 over 31 days (10-21 to 11-20): 110 in October, 200 in November.
CROSS_SEP_OH = HIGH - SEP_DEMAND  # 1100
CROSS_OCT_OH = CROSS_SEP_OH - OCT_DEMAND - CROSS_OCT  # 1100 - 420
CROSS_NOV_OH = CROSS_OCT_OH - NOV_DEMAND - CROSS_NOV  # 680 - 500
CROSS_HIGH = _group(
    _bucket("2026-09", SEP_DEMAND, CROSS_SEP_OH, 0.0, 0, CROSS_SEP_OH),
    _bucket(
        "2026-10",
        OCT_DEMAND + CROSS_OCT,  # 310 + 110
        CROSS_OCT_OH,
        CROSS_OCT,
        0,
        CROSS_OCT_OH,
    ),
    _bucket(
        "2026-11",
        NOV_DEMAND + CROSS_NOV,  # 300 + 200
        CROSS_NOV_OH,
        CROSS_NOV,
        0,
        CROSS_NOV_OH,
    ),
)

# --- on-hand 0: the planned review places an order every month -------
# No signal: S = ROP + D x days (no cover), the position rolls as
# prior + receipts - D x days.
LOW_SEP_OH = 0.0 - SEP_DEMAND  # -300
LOW_OCT_OH = LOW_SEP_OH - OCT_DEMAND  # -610
LOW_NOV_OH = LOW_OCT_OH - NOV_DEMAND  # -910
LOW_SEP_ORDER = ROP + SEP_DEMAND  # 400
LOW_SEP_END = 0.0 + LOW_SEP_ORDER - SEP_DEMAND  # 100
LOW_OCT_ORDER = ROP + OCT_DEMAND - LOW_SEP_END  # 310
LOW_OCT_END = LOW_SEP_END + LOW_OCT_ORDER - OCT_DEMAND  # 100
LOW_NOV_ORDER = ROP + NOV_DEMAND - LOW_OCT_END  # 300
LOW_NOV_END = LOW_OCT_END + LOW_NOV_ORDER - NOV_DEMAND  # 100
LOW_TOTAL = LOW_SEP_ORDER + LOW_OCT_ORDER + LOW_NOV_ORDER  # 1010
LOW_BASE = _group(
    _bucket("2026-09", SEP_DEMAND, LOW_SEP_OH, 0.0, LOW_SEP_ORDER, LOW_SEP_END),
    _bucket("2026-10", OCT_DEMAND, LOW_OCT_OH, 0.0, LOW_OCT_ORDER, LOW_OCT_END),
    _bucket("2026-11", NOV_DEMAND, LOW_NOV_OH, 0.0, LOW_NOV_ORDER, LOW_NOV_END),
)

# 300 over the whole of the third month: October's cover window
# (October plus the ceil(lt) = 10 lead-time days) reaches into 11-01 to
# 11-10, so it carries 10 days of the signal (100); November's cover
# window carries the whole 300.  Because October's raised order (410,
# not 310) already sits in the position when November reviews, the
# signal is bought once: 400 + 410 + 500 = 1010 + 300.
OCT_COVER = 10 * NOV_SHARE  # 100.0
NOV_COVER = NOV_DAYS * NOV_SHARE  # 300.0
LS_SEP_ORDER = ROP + SEP_DEMAND  # 400
LS_SEP_END = 0.0 + LS_SEP_ORDER - SEP_DEMAND  # 100
LS_OCT_ORDER = ROP + OCT_DEMAND + OCT_COVER - LS_SEP_END  # 410
LS_OCT_END = LS_SEP_END + LS_OCT_ORDER - OCT_DEMAND  # 200
LS_NOV_ORDER = ROP + NOV_DEMAND + NOV_COVER - LS_OCT_END  # 500
LS_NOV_END = LS_OCT_END + LS_NOV_ORDER - NOV_DEMAND - NOV_SIGNAL  # 100
LS_TOTAL = LS_SEP_ORDER + LS_OCT_ORDER + LS_NOV_ORDER  # 1310
LOW_SIGNAL_NOV = _group(
    _bucket("2026-09", SEP_DEMAND, LOW_SEP_OH, 0.0, LS_SEP_ORDER, LS_SEP_END),
    _bucket("2026-10", OCT_DEMAND, LOW_OCT_OH, 0.0, LS_OCT_ORDER, LS_OCT_END),
    _bucket(
        "2026-11",
        NOV_DEMAND + NOV_SIGNAL,
        LOW_OCT_OH - NOV_DEMAND - NOV_SIGNAL,  # -610 - 600
        NOV_SIGNAL,
        LS_NOV_ORDER,
        LS_NOV_END,
    ),
)

# 300 early in the third month (11-01 to 11-10): the whole signal sits
# in October's cover window, so the order placed at the start of
# October (610, not 310) carries it and November orders exactly what
# the no-signal seed orders (300).
LE_SEP_ORDER = ROP + SEP_DEMAND  # 400
LE_SEP_END = 0.0 + LE_SEP_ORDER - SEP_DEMAND  # 100
LE_OCT_ORDER = ROP + OCT_DEMAND + 10 * EARLY_SHARE - LE_SEP_END  # 610
LE_OCT_END = LE_SEP_END + LE_OCT_ORDER - OCT_DEMAND  # 400
LE_NOV_ORDER = ROP + NOV_DEMAND + 10 * EARLY_SHARE - LE_OCT_END  # 300
LE_NOV_END = LE_OCT_END + LE_NOV_ORDER - NOV_DEMAND - EARLY_SIGNAL  # 100
LE_TOTAL = LE_SEP_ORDER + LE_OCT_ORDER + LE_NOV_ORDER  # 1310
LOW_SIGNAL_EARLY = _group(
    _bucket("2026-09", SEP_DEMAND, LOW_SEP_OH, 0.0, LE_SEP_ORDER, LE_SEP_END),
    _bucket("2026-10", OCT_DEMAND, LOW_OCT_OH, 0.0, LE_OCT_ORDER, LE_OCT_END),
    _bucket(
        "2026-11",
        NOV_DEMAND + EARLY_SIGNAL,
        LOW_OCT_OH - NOV_DEMAND - EARLY_SIGNAL,
        EARLY_SIGNAL,
        LE_NOV_ORDER,
        LE_NOV_END,
    ),
)

# --- the review and spend horizons start from the pinned server date
# (2026-10): 2026-10 (31 days), 2026-11 (30), 2026-12 (31).  On-hand 0
# with the 300 signal standing: the same cover arithmetic as above,
# over these three months.
RV_OCT_OH = 0.0 - OCT_DEMAND  # -310
RV_NOV_OH = RV_OCT_OH - NOV_DEMAND - NOV_SIGNAL  # -310 - 600
RV_OCT_ORDER = ROP + OCT_DEMAND + OCT_COVER  # 510 (position 0)
RV_OCT_END = RV_OCT_ORDER - OCT_DEMAND  # 200
RV_NOV_ORDER = ROP + NOV_DEMAND + NOV_COVER - RV_OCT_END  # 500
RV_NOV_END = RV_OCT_END + RV_NOV_ORDER - NOV_DEMAND - NOV_SIGNAL  # 100
RV_DEC_ORDER = ROP + DEC_DEMAND - RV_NOV_END  # 310


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


def _seed(cur) -> None:
    """The one item-site, as test_issue927_freeform seeds one: smooth
    10.0 a day for 360 days ending 2026-09-30 (d 10, sd_d 0), three
    10-day lead-time observations (lt 10, sd_lt 0), unit cost 2.0,
    on-hand 1400 and no open purchase lines."""
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-971-{i}",
                f"de-971-{i}",
                ITEM,
                SITE,
                D,
                (FIRST_DAY + timedelta(days=i)).isoformat(),
                "shipment",
                "test",
            )
            for i in range(360)
        ],
    )
    base = date(2026, 8, 20)
    for i in range(3):
        ordered = base + timedelta(days=i)
        cur.execute(
            LEAD_SQL,
            (
                f"id-lt-971-{i}",
                f"lt-971-{i}",
                "SUP-971-1",
                ITEM,
                ordered.isoformat(),
                (ordered + timedelta(days=LT)).isoformat(),
                "test",
            ),
        )
    cur.execute(ITEM_SITE_SQL, ("id-is-971-1", ITEM, SITE, COST, ROP, 0.0, "test"))
    cur.execute(ON_HAND_SQL, ("id-oh-971-1", ITEM, SITE, HIGH, SYNC_AS_OF, "test"))


def _set_on_hand(cur, quantity) -> None:
    cur.execute(
        "UPDATE on_hand SET quantity_on_hand = %s "
        "WHERE item_code = %s AND site_code = %s",
        (quantity, ITEM, SITE),
    )


def test_issue971_freeform(client, monkeypatch, admin_headers):
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    # The projection resolves its default as_of and rejects a future
    # one against the pinned server date; the spend and review routes
    # resolve theirs the same way, and the shared default as-of date
    # (recommendations, the signal and override validations) is pinned
    # to the same server date.
    monkeypatch.setattr(projection_module, "date", _FixedDate)
    monkeypatch.setattr("app.aggregate.spend.date", _FixedDate)
    monkeypatch.setattr(review_module, "date", _FixedDate)
    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    admin = TestClient(app, headers=admin_headers)

    # --- seed ------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    def _projection(**extra):
        response = client.get(URL, params={"as_of": AS_OF_STR, "months": 3, **extra})
        assert response.status_code == 200, response.text
        return response.json()

    def _post_signal(start, end, quantity):
        response = admin.post(
            "/api/demand-signals",
            json={
                "kind": "promotion",
                "item_code": ITEM,
                "site_code": SITE,
                "start_date": start,
                "end_date": end,
                "quantity": quantity,
                "reason": "issue 971 proof",
            },
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def _withdraw_signal(signal_id):
        response = admin.post(
            f"/api/demand-signals/{signal_id}/withdraw",
            json={"reason": "issue 971 proof"},
        )
        assert response.status_code == 201, response.text

    def _set_on_hand_value(quantity):
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _set_on_hand(cur, quantity)
            conn.commit()
        finally:
            conn.close()

    # --- the no-signal baseline ------------------------------------------
    # One site group; the bucket's field set carries signal_units 0.0;
    # with 1400 on hand no order is placed in the months around the
    # signal unless the signal needs one.
    groups = _projection()
    assert len(groups) == 1
    for bucket in groups[0]["buckets"]:
        assert set(bucket) == BUCKET_KEYS
    assert groups == BASELINE
    assert all(bucket["planned_receipts_units"] == 0 for bucket in groups[0]["buckets"])

    # --- in its month: 300 over the whole of the third horizon month -----
    signal = _post_signal("2026-11-01", "2026-11-30", 300)
    groups = _projection()
    assert groups == SIGNAL_NOV
    third = groups[0]["buckets"][2]
    # 300 over the 30 days of the month is 10 a day: the month's units
    # are exactly 300, the demand adds them and the value adds 300 x
    # the unit cost.
    assert third["signal_units"] == NOV_DAYS * NOV_SHARE
    assert third["projected_demand_units"] == NOV_DEMAND + NOV_SIGNAL
    assert third["projected_demand_value"] == (NOV_DEMAND + NOV_SIGNAL) * COST
    # The first two months are unchanged.
    assert groups[0]["buckets"][0] == BASELINE[0]["buckets"][0]
    assert groups[0]["buckets"][1] == BASELINE[0]["buckets"][1]
    # The on-hand is 300 lower in the third month.
    assert (
        third["projected_on_hand_units"]
        == BASELINE[0]["buckets"][2]["projected_on_hand_units"] - 300
    )

    # --- withdrawn: the projection answers the no-signal values ----------
    _withdraw_signal(signal)
    assert _projection() == BASELINE

    # --- across months: 310 over 31 days, 11 in one month, 20 in the next
    signal = _post_signal("2026-10-21", "2026-11-20", 310)
    groups = _projection()
    assert groups == CROSS_HIGH
    assert groups[0]["buckets"][0] == BASELINE[0]["buckets"][0]
    assert groups[0]["buckets"][1]["signal_units"] == 11 * CROSS_SHARE
    assert groups[0]["buckets"][1]["projected_demand_units"] == OCT_DEMAND + CROSS_OCT
    assert groups[0]["buckets"][2]["signal_units"] == 20 * CROSS_SHARE
    assert groups[0]["buckets"][2]["projected_demand_units"] == NOV_DEMAND + CROSS_NOV
    _withdraw_signal(signal)

    # --- planned orders cover it once: on-hand 0 --------------------------
    _set_on_hand_value(0.0)

    # The no-signal seed: an order every month (400 / 310 / 300, total
    # 1010), the end planned on-hand 100 and no planned shortfall.
    groups = _projection()
    assert groups == LOW_BASE
    assert not any(bucket["planned_shortfall"] for bucket in groups[0]["buckets"])

    # The 300-unit signal in the third month: October's order carries
    # the 100 units of signal its cover window reaches into, November's
    # order carries the rest — the total rises by exactly 300, once,
    # and the end planned on-hand is the no-signal value.
    signal = _post_signal("2026-11-01", "2026-11-30", 300)
    groups = _projection()
    assert groups == LOW_SIGNAL_NOV
    receipts = [bucket["planned_receipts_units"] for bucket in groups[0]["buckets"]]
    assert sum(receipts) - LOW_TOTAL == 300
    assert groups[0]["buckets"][2]["planned_on_hand_units"] == LOW_NOV_END
    assert not any(bucket["planned_shortfall"] for bucket in groups[0]["buckets"])

    # --- ordered ahead: 300 early in the third month ----------------------
    _withdraw_signal(signal)
    signal = _post_signal("2026-11-01", "2026-11-10", 300)
    groups = _projection()
    assert groups == LOW_SIGNAL_EARLY
    # The early signal sits in October's cover window: the order placed
    # at the start of October carries it, so the planned shortfall stays
    # false where the no-signal seed has none.
    assert [bucket["planned_receipts_units"] for bucket in groups[0]["buckets"]] == [
        LE_SEP_ORDER,
        LE_OCT_ORDER,
        LE_NOV_ORDER,
    ]
    assert (
        sum(bucket["planned_receipts_units"] for bucket in groups[0]["buckets"])
        - LOW_TOTAL
        == 300
    )
    assert not any(bucket["planned_shortfall"] for bucket in groups[0]["buckets"])
    _withdraw_signal(signal)

    # --- an override wins: it removes the days it covers ------------------
    # The 31-day window (10-21 to 11-20) meets forecast period 2
    # (10-31 to 11-29): the override covers 10-31 to 11-20 of the
    # window, so those 21 days take no share.
    signal = _post_signal("2026-10-21", "2026-11-20", 310)
    groups = _projection()
    assert groups[0]["buckets"][1]["signal_units"] == 11 * CROSS_SHARE
    assert groups[0]["buckets"][2]["signal_units"] == 20 * CROSS_SHARE
    response = admin.post(
        "/api/forecast/overrides",
        json={
            "item_code": ITEM,
            "site_code": SITE,
            "period_start": "2026-10-31",
            "quantity": 0.0,
            "reason": "issue 971 proof",
        },
    )
    assert response.status_code == 201, response.text
    groups = _projection()
    # 10 days of the window stay unoverridden (10-21 to 10-30): 100 in
    # October; every November day of the window is overridden: 0.
    assert groups[0]["buckets"][1]["signal_units"] == 10 * CROSS_SHARE
    assert (
        groups[0]["buckets"][1]["projected_demand_units"]
        == OCT_DEMAND + 10 * CROSS_SHARE
    )
    assert groups[0]["buckets"][2]["signal_units"] == 0.0
    assert groups[0]["buckets"][2]["projected_demand_units"] == NOV_DEMAND
    response = admin.post(
        "/api/forecast/overrides/withdrawals",
        json={
            "item_code": ITEM,
            "site_code": SITE,
            "period_start": "2026-10-31",
            "reason": "issue 971 proof",
        },
    )
    assert response.status_code == 201, response.text
    _withdraw_signal(signal)

    # --- the levers: a signal is a stated quantity -------------------------
    # 300 over the third month, demand_multiplier 2: the month's demand
    # is 2 x d x days + the signal — the signal is not doubled.
    signal = _post_signal("2026-11-01", "2026-11-30", 300)
    groups = _projection(demand_multiplier=2)
    scaled = groups[0]["buckets"]
    assert scaled[0]["projected_demand_units"] == 2 * D * SEP_DAYS
    assert scaled[0]["signal_units"] == 0.0
    assert scaled[1]["projected_demand_units"] == 2 * D * OCT_DAYS
    assert scaled[2]["projected_demand_units"] == 2 * D * NOV_DAYS + NOV_SIGNAL
    assert scaled[2]["signal_units"] == NOV_SIGNAL

    # --- downstream: the review and the spend view carry the new figures --
    # The review and spend horizons start from the pinned server date
    # (2026-10): the third month of the horizon, 2026-11, carries the
    # signal — its demand_units read 300 + 300 with the 300 in it.
    response = client.get("/api/sop/review", params={"months": 3})
    assert response.status_code == 200, response.text
    review = response.json()
    assert review["as_of"] == FIXED_TODAY.isoformat()
    assert [row["month"] for row in review["months"]] == [
        "2026-10",
        "2026-11",
        "2026-12",
    ]
    by_month = {row["month"]: row for row in review["months"]}
    november = by_month["2026-11"]
    assert november["demand_units"] == NOV_DEMAND + NOV_SIGNAL
    assert november["demand_value"] == (NOV_DEMAND + NOV_SIGNAL) * COST
    assert november["planned_receipts_units"] == RV_NOV_ORDER
    assert november["planned_receipts_value"] == RV_NOV_ORDER * COST
    assert november["projected_on_hand_units"] == RV_NOV_OH
    assert november["planned_on_hand_units"] == RV_NOV_END
    assert november["planned_spend"] == RV_NOV_ORDER * COST
    assert november["committed_spend"] == 0.0
    assert november["total_spend"] == RV_NOV_ORDER * COST
    assert november["budget"] is None
    assert november["remaining"] is None
    assert november["over_budget"] is False
    assert november["sites_short_as_is"] == [SITE]
    assert november["sites_short_planned"] == []
    assert by_month["2026-10"]["planned_receipts_units"] == RV_OCT_ORDER
    assert by_month["2026-10"]["planned_spend"] == RV_OCT_ORDER * COST
    assert by_month["2026-12"]["planned_receipts_units"] == RV_DEC_ORDER

    response = client.get("/api/aggregate/spend", params={"months": 3})
    assert response.status_code == 200, response.text
    spend = {row["month"]: row for row in response.json()["months"]}
    assert spend["2026-11"]["planned_spend"] == RV_NOV_ORDER * COST
    assert spend["2026-11"]["total_spend"] == RV_NOV_ORDER * COST
    assert spend["2026-11"]["budget"] is None
    assert spend["2026-11"]["remaining"] is None
    assert spend["2026-11"]["over_budget"] is False
    assert spend["2026-10"]["planned_spend"] == RV_OCT_ORDER * COST

    # --- one read, counted honestly ----------------------------------------
    # One GET /api/aggregate/projection calls counted_signals once (and
    # counted_overrides once), and psycopg2.connect is the same object
    # before and during the call — checked from inside the wrapped
    # counted_signals, so a production swap or patch of the connect the
    # read would hide behind is caught.
    state = {"signals": 0, "overrides": 0, "connect_same": True}
    connect_before = psycopg2.connect

    real_signals = projection_module.counted_signals
    real_overrides = projection_module.counted_overrides

    def counting_signals(pairs):
        state["signals"] += 1
        state["connect_same"] = state["connect_same"] and (
            psycopg2.connect is connect_before
        )
        return real_signals(pairs)

    def counting_overrides(pairs):
        state["overrides"] += 1
        return real_overrides(pairs)

    monkeypatch.setattr(projection_module, "counted_signals", counting_signals)
    monkeypatch.setattr(projection_module, "counted_overrides", counting_overrides)
    response = client.get(URL, params={"as_of": AS_OF_STR, "months": 3})
    assert response.status_code == 200, response.text
    assert state["signals"] == 1
    assert state["overrides"] == 1
    assert state["connect_same"]
