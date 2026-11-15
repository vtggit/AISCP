"""Issue #484 proof: planned orders in the site-grouped projection.

AC-1 — ``app.aggregate.projection.aggregate_projection``, served
unchanged in shape at ``GET /api/aggregate/projection``, adds four
fields to every bucket and keeps every existing field, value, bound,
order and status code.  Per recommendable item-site (the same
``status "ok"`` set the projection already groups), with ``rop`` its
recommendation's ``reorder_point_units``, ``d`` its ``inputs.d`` and
``lt`` its ``inputs.lt`` in days (each non-finite or missing value
taken as 0): the item-site's own start-of-month on-hand starts from
its current on-hand (0 when absent) and, walked in month order, the
start-of-month inventory position is that on-hand plus every open
purchase line unit not yet received in an earlier bucket (a line
counts as received only in the bucket of its ``promised_on`` month,
exactly as the existing supply rule; lines with no ``promised_on``,
or promised before the first bucket or after the last, stay on order
throughout) plus every planned order placed in an earlier bucket that
has not yet arrived.  When the position is strictly below the
order-up-to level (the reorder point plus that month's demand),
``S = rop + d x days-in-month``, one planned order is placed for
``ceil(S - position)`` units; it arrives on the first day of that
month plus ``ceil(lt)`` days, counted as a receipt in the bucket
whose month contains the date (in no bucket when the date is after
the last, the order then staying on order).  The next start-of-month
on-hand is
this one plus that bucket's open-line receipts plus that bucket's
planned arrivals minus ``d x days-in-month``.  Per site group and
bucket: ``planned_receipts_units`` (the sum of the group's planned
orders arriving in that bucket), ``planned_receipts_value`` (the same
units at each item-site's ``unit_cost``, 0 for an item-site without
one), ``planned_on_hand_units`` (that bucket's
``projected_on_hand_units`` plus the group's cumulative
``planned_receipts_units`` through that bucket) and
``planned_shortfall`` (true when ``planned_on_hand_units`` is below
zero).  The computation still performs the same fixed number of reads
(one ``recommendations_for_all``, one on-hand read, one
open-purchase-line read) and writes nothing; the module docstring
names the method: a periodic-review order-up-to (R, S) policy —
monthly review, order-up-to the reorder point plus one month's
demand, arriving after the lead time.
``backend/app/api/aggregate.py`` adds the four fields to
``ProjectionBucket`` and ``backend/openapi.json`` reflects them.  No
recommendation, drift, excess, stockout, demand, scenario or draft-PO
behaviour changes.

AC-2 — proven below with ``recommendations_for_all``, the on-hand
read and the open-purchase-line read monkeypatched (no database),
using a past ``as_of`` of 2026-01-15:

  * the worked example: one recommendable item-site I1 at S1 with
    ``reorder_point_units`` 300, ``d`` 10, ``lt`` 20, ``unit_cost``
    2.0, on-hand 250 and no open lines, over 3 months — January (31
    days): the position 250 is below S 610, so a planned order of 360
    units (610 - 250) arrives on 21 January:
    ``planned_receipts_units`` 360, ``planned_receipts_value`` 720.0,
    ``projected_on_hand_units`` -60, ``planned_on_hand_units`` 300,
    ``planned_shortfall`` false; February (28 days): the position
    300 is below S 580, so an order of 280 units (580 - 300)
    arrives on 21 February — ``planned_receipts_units`` 280,
    ``planned_receipts_value`` 560.0,
    ``projected_on_hand_units`` -340, ``planned_on_hand_units`` 300,
    ``planned_shortfall`` false; March (31 days): the position 300
    is below S 610, so an order of 310 units (610 - 300) arrives on
    21 March — ``planned_receipts_units`` 310,
    ``planned_receipts_value`` 620.0,
    ``projected_on_hand_units`` -650, ``planned_on_hand_units`` 300,
    ``planned_shortfall`` false;
  * beyond the window: the same item-site with ``lt`` 100 over 2
    months places one order in January (360 units, arriving 11
    April, after the window) and a second in February (280 units,
    the position -60 + 360 = 300 being below S 580, arriving 12 May,
    also after the window), so both buckets of the 2-month run have
    ``planned_receipts_units`` 0; over 6 months the receipts per
    bucket are [0, 0, 0, 360, 280, 310] (11 April, 12 May and 9 June
    arrivals); ``planned_on_hand_units`` equals
    ``projected_on_hand_units`` in the 2-month buckets, and
    ``planned_shortfall`` is true when it is below zero;
  * open lines count in the position: an item-site with on-hand 100,
    ``rop`` 300 and an open line of 250 promised in February orders
    260 units in January (position 350, below S 610), 280 in
    February (position 300, below S 580) and 310 in March (position
    300, below S 610), and its February line receipt appears in
    ``projected_supply_units`` exactly as before;
  * unchanged: for an input with no planned orders, every existing
    field equals what the projection returned before this change; an
    item-site without a ``unit_cost`` contributes 0 to
    ``planned_receipts_value``; the read helpers are each called
    exactly once whatever the number of item-sites and months; and
    ``GET /api/aggregate/projection`` (through the app with the test
    Bearer, as the #460 test does) returns the four new fields on
    every bucket, while a future ``as_of`` and ``months`` 13 still
    return 422.
"""

from datetime import date, timedelta

from app.aggregate import projection
from app.aggregate.projection import aggregate_projection

AS_OF = date(2026, 1, 15)
URL = "/api/aggregate/projection"

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


def _rec(item, site, rop, d, lt, unit_cost):
    """One recommendable recommendation row as the projection sees it."""
    return {
        "status": "ok",
        "item_code": item,
        "site_code": site,
        "reorder_point_units": rop,
        "unit_cost": unit_cost,
        "inputs": {
            "d": d,
            "sd_d": 0.0,
            "lt": lt,
            "sd_lt": 0.0,
            "z": 1.6448536269514722,
            "service_level": 0.95,
        },
    }


def _install(monkeypatch, recs, on_hand_rows, line_rows):
    """Monkeypatch the three reads (no database) and count their calls."""
    calls = {"recommendations": 0, "on_hand": 0, "lines": 0}

    def fake_recommendations(as_of=None):
        calls["recommendations"] += 1
        return list(recs)

    def fake_list_rows(limit, offset, **filters):
        calls["on_hand"] += 1
        return list(on_hand_rows)

    def fake_list_latest():
        calls["lines"] += 1
        return list(line_rows)

    monkeypatch.setattr(projection, "recommendations_for_all", fake_recommendations)
    monkeypatch.setattr(projection._on_hand_repo, "list_rows", fake_list_rows)
    monkeypatch.setattr(projection._open_po_repo, "list_latest", fake_list_latest)
    return calls


def test_issue484_freeform(monkeypatch, client):
    # --- the worked example ------------------------------------------------
    # I1@S1: rop 300, d 10, lt 20, unit_cost 2.0, on-hand 250, no open
    # lines, 3 months (January 31, February 28, March 31).
    recs = [_rec("I1", "S1", 300, 10, 20, 2.0)]
    on_hand = [{"item_code": "I1", "site_code": "S1", "quantity_on_hand": 250}]
    _install(monkeypatch, recs, on_hand, [])
    rows = aggregate_projection(as_of=AS_OF, months=3)
    assert len(rows) == 1
    group = rows[0]
    assert group["group"] == {"dimension": "site", "code": "S1"}
    assert group["item_sites_without_unit_cost"] == 0
    worked_jan, worked_feb, worked_mar = group["buckets"]
    assert [bucket["month"] for bucket in group["buckets"]] == [
        "2026-01",
        "2026-02",
        "2026-03",
    ]
    for bucket in group["buckets"]:
        assert set(bucket) == BUCKET_KEYS
    # January: the position 250 is below the order-up-to level
    # 300 + 310 = 610, so a planned order of 610 - 250 = 360 units
    # arrives on 21 January (in the bucket).
    assert worked_jan == {
        "month": "2026-01",
        "projected_demand_units": 310,
        "projected_supply_units": 0.0,
        "projected_on_hand_units": -60.0,
        "projected_shortfall": True,
        "projected_demand_value": 620.0,
        "projected_supply_value": 0.0,
        "signal_units": 0.0,
        "planned_receipts_units": 360,
        "planned_receipts_value": 720.0,
        "planned_on_hand_units": 300.0,
        "planned_shortfall": False,
    }
    # February: the position 300 is below 300 + 280 = 580, so an
    # order of 580 - 300 = 280 units arrives on 21 February.
    assert worked_feb == {
        "month": "2026-02",
        "projected_demand_units": 280,
        "projected_supply_units": 0.0,
        "projected_on_hand_units": -340.0,
        "projected_shortfall": True,
        "projected_demand_value": 560.0,
        "projected_supply_value": 0.0,
        "signal_units": 0.0,
        "planned_receipts_units": 280,
        "planned_receipts_value": 560.0,
        "planned_on_hand_units": 300.0,
        "planned_shortfall": False,
    }
    # March: the position 300 is below 300 + 310 = 610, so an order
    # of 610 - 300 = 310 units arrives on 21 March.
    assert worked_mar == {
        "month": "2026-03",
        "projected_demand_units": 310,
        "projected_supply_units": 0.0,
        "projected_on_hand_units": -650.0,
        "projected_shortfall": True,
        "projected_demand_value": 620.0,
        "projected_supply_value": 0.0,
        "signal_units": 0.0,
        "planned_receipts_units": 310,
        "planned_receipts_value": 620.0,
        "planned_on_hand_units": 300.0,
        "planned_shortfall": False,
    }

    # --- beyond the window ---------------------------------------------------
    # The same item-site with lt 100 over 2 months: the January order
    # of 360 units arrives on 11 April, after the last bucket, so it
    # is a receipt in no bucket and stays on order.
    recs = [_rec("I1", "S1", 300, 10, 100, 2.0)]
    _install(monkeypatch, recs, on_hand, [])
    rows = aggregate_projection(as_of=AS_OF, months=2)
    jan, feb = rows[0]["buckets"]
    assert jan["planned_receipts_units"] == 0
    assert feb["planned_receipts_units"] == 0
    assert jan["planned_receipts_value"] == 0.0
    assert feb["planned_receipts_value"] == 0.0
    assert jan["planned_on_hand_units"] == jan["projected_on_hand_units"] == -60.0
    assert feb["planned_on_hand_units"] == feb["projected_on_hand_units"] == -340.0
    assert jan["planned_shortfall"] is True
    assert feb["planned_shortfall"] is True
    # February now places a second order: the position -60 + 360 =
    # 300 is below S 580, so 280 units of it arrive 12 May, also
    # after the window.  Over a horizon long enough to show the
    # in-window arrivals, the receipts are the January order (11
    # April), the February order (12 May) and the March order (9
    # June); orders placed from April on arrive after the last
    # bucket.
    rows = aggregate_projection(as_of=AS_OF, months=6)
    assert [bucket["planned_receipts_units"] for bucket in rows[0]["buckets"]] == [
        0,
        0,
        0,
        360,
        280,
        310,
    ]

    # --- open lines count in the position --------------------------------------
    # On-hand 100, rop 300, an open line of 250 promised in February:
    # the January position is 100 + 250 = 350, below S 610, so an
    # order of 610 - 350 = 260 arrives 21 January, and the February
    # line receipt appears in projected_supply_units exactly as
    # before.
    recs = [_rec("I1", "S1", 300, 10, 20, 2.0)]
    on_hand = [{"item_code": "I1", "site_code": "S1", "quantity_on_hand": 100}]
    lines = [
        {
            "item_code": "I1",
            "site_code": "S1",
            "open_units": 250,
            "promised_on": date(2026, 2, 10),
        }
    ]
    _install(monkeypatch, recs, on_hand, lines)
    rows = aggregate_projection(as_of=AS_OF, months=3)
    jan, feb, mar = rows[0]["buckets"]
    assert jan["planned_receipts_units"] == 260
    assert jan["projected_supply_units"] == 0.0
    assert jan["projected_on_hand_units"] == -210.0
    assert jan["planned_on_hand_units"] == 50.0
    assert jan["planned_shortfall"] is False
    # February: the line is received (position 50 + 250 = 300 is
    # below S 580, so an order of 280 arrives 21 February) and the
    # line's receipt is the bucket's projected supply exactly as
    # before.
    assert feb["projected_supply_units"] == 250.0
    assert feb["projected_supply_value"] == 500.0
    assert feb["projected_on_hand_units"] == -240.0
    assert feb["planned_receipts_units"] == 280
    assert feb["planned_receipts_value"] == 560.0
    assert feb["planned_on_hand_units"] == 300.0
    assert feb["planned_shortfall"] is False
    # March: the position 300 is below S 610, so an order of 310
    # arrives 21 March.
    assert mar["planned_receipts_units"] == 310
    assert mar["projected_on_hand_units"] == -550.0
    assert mar["planned_on_hand_units"] == 300.0
    assert mar["planned_shortfall"] is False

    # --- unchanged ---------------------------------------------------------------
    # With on-hand 5000 the position never drops below the
    # order-up-to level, so there are no planned orders and every
    # existing field equals what the projection returned before this
    # change.
    recs = [_rec("I1", "S1", 300, 10, 20, 2.0)]
    on_hand = [{"item_code": "I1", "site_code": "S1", "quantity_on_hand": 5000}]
    _install(monkeypatch, recs, on_hand, [])
    rows = aggregate_projection(as_of=AS_OF, months=3)
    jan, feb, mar = rows[0]["buckets"]
    assert jan == {
        "month": "2026-01",
        "projected_demand_units": 310,
        "projected_supply_units": 0.0,
        "projected_on_hand_units": 4690.0,
        "projected_shortfall": False,
        "projected_demand_value": 620.0,
        "projected_supply_value": 0.0,
        "signal_units": 0.0,
        "planned_receipts_units": 0,
        "planned_receipts_value": 0.0,
        "planned_on_hand_units": 4690.0,
        "planned_shortfall": False,
    }
    assert feb["projected_on_hand_units"] == 4410.0
    assert feb["planned_on_hand_units"] == 4410.0
    assert mar["projected_on_hand_units"] == 4100.0
    assert mar["planned_on_hand_units"] == 4100.0
    for bucket in (jan, feb, mar):
        assert bucket["planned_receipts_units"] == 0
        assert bucket["planned_receipts_value"] == 0.0
        assert bucket["planned_on_hand_units"] == bucket["projected_on_hand_units"]
        assert bucket["planned_shortfall"] is False

    # An item-site without a unit cost contributes 0 to
    # planned_receipts_value; the group sums both item-sites' receipts.
    recs = [
        _rec("I2", "S2", 100, 5, 10, None),
        _rec("I3", "S2", 50, 2, 5, 4.0),
    ]
    on_hand = [
        {"item_code": "I2", "site_code": "S2", "quantity_on_hand": 0},
        {"item_code": "I3", "site_code": "S2", "quantity_on_hand": 10},
    ]
    _install(monkeypatch, recs, on_hand, [])
    rows = aggregate_projection(as_of=AS_OF, months=1)
    group = rows[0]
    assert group["group"]["code"] == "S2"
    assert group["item_sites_without_unit_cost"] == 1
    bucket = group["buckets"][0]
    # I2: position 0 < 100 + 155 = 255 -> ceil(255 - 0) = 255 (no
    # cost); I3: position 10 < 50 + 62 = 112 -> ceil(112 - 10) = 102
    # at 4.0.
    assert bucket["planned_receipts_units"] == 357
    assert bucket["planned_receipts_value"] == 408.0
    assert bucket["projected_demand_units"] == 217
    assert bucket["projected_on_hand_units"] == -207.0
    assert bucket["planned_on_hand_units"] == 150.0
    assert bucket["planned_shortfall"] is False

    # --- the fixed number of reads ----------------------------------------------
    # Whatever the number of item-sites and months, each read helper is
    # called exactly once.
    recs = [
        _rec("I1", "S1", 300, 10, 20, 2.0),
        _rec("I2", "S2", 100, 5, 10, None),
        _rec("I3", "S3", 50, 2, 5, 4.0),
    ]
    on_hand = [
        {"item_code": "I1", "site_code": "S1", "quantity_on_hand": 250},
        {"item_code": "I2", "site_code": "S2", "quantity_on_hand": 0},
        {"item_code": "I3", "site_code": "S3", "quantity_on_hand": 10},
    ]
    lines = [
        {
            "item_code": "I3",
            "site_code": "S3",
            "open_units": 250,
            "promised_on": date(2026, 2, 10),
        }
    ]
    calls = _install(monkeypatch, recs, on_hand, lines)
    rows = aggregate_projection(as_of=AS_OF, months=12)
    assert [g["group"]["code"] for g in rows] == ["S1", "S2", "S3"]
    assert calls == {"recommendations": 1, "on_hand": 1, "lines": 1}
    calls = _install(monkeypatch, recs, on_hand, lines)
    aggregate_projection(as_of=AS_OF, months=1)
    assert calls == {"recommendations": 1, "on_hand": 1, "lines": 1}

    # --- the endpoint through the app with the test Bearer -----------------------
    recs = [_rec("I1", "S1", 300, 10, 20, 2.0)]
    on_hand = [{"item_code": "I1", "site_code": "S1", "quantity_on_hand": 250}]
    _install(monkeypatch, recs, on_hand, [])
    response = client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 3})
    assert response.status_code == 200, response.text
    assert response.json() == [
        {
            "group": {"dimension": "site", "code": "S1"},
            "buckets": [worked_jan, worked_feb, worked_mar],
            "item_sites_without_unit_cost": 0,
        }
    ]
    # A future as_of and months above 12 are still 422.
    future = (date.today() + timedelta(days=1)).isoformat()
    assert client.get(URL, params={"as_of": future, "months": 3}).status_code == 422
    assert (
        client.get(URL, params={"as_of": AS_OF.isoformat(), "months": 13}).status_code
        == 422
    )
