"""Issue #490 proof: the order-up-to (R, S) trigger removes the oscillation.

``app.aggregate.projection.aggregate_projection`` places a planned
order whenever the start-of-month inventory position is strictly
below the order-up-to level (the reorder point plus that month's
demand), ``S = rop + d x days-in-month`` — it no longer compares the
position with ``rop``.  Proven below with
``recommendations_for_all``, the on-hand read and the
open-purchase-line read monkeypatched (no database), using a past
``as_of`` of 2026-01-15 and a 6-month horizon for one recommendable
item-site I1 at S1 with ``reorder_point_units`` 20 — below one
month's demand — ``d`` 1, ``lt`` 5, ``unit_cost`` 1.0, on-hand 0 and
no open lines: comparing the position with ``rop`` alone would
alternate order / no order / order as the on-hand oscillates around
20 (January 0 -> 51 -> 20, February no order -> -8, ...); with the
order-up-to level the review orders up to ``S`` every month, so
every bucket has ``planned_shortfall`` false,
``planned_on_hand_units`` equal to 20 (the on-hand settles at the
reorder point at the end of every month) and a positive
``planned_receipts_units`` (51, 28, 31, 30, 31 and 30 units).
"""

from datetime import date

from app.aggregate import projection
from app.aggregate.projection import aggregate_projection

AS_OF = date(2026, 1, 15)


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
    """Monkeypatch the three reads (no database)."""

    def fake_recommendations(as_of=None):
        return list(recs)

    def fake_list_rows(limit, offset, **filters):
        return list(on_hand_rows)

    def fake_list_latest():
        return list(line_rows)

    monkeypatch.setattr(projection, "recommendations_for_all", fake_recommendations)
    monkeypatch.setattr(projection._on_hand_repo, "list_rows", fake_list_rows)
    monkeypatch.setattr(projection._open_po_repo, "list_latest", fake_list_latest)


def test_issue490_freeform(monkeypatch):
    # I1@S1: rop 20 (below one month's demand), d 1, lt 5,
    # unit_cost 1.0, on-hand 0, no open lines, 6 months.  Every month
    # the position 20 is below S = 20 + days-in-month, so an order of
    # S - 20 units is placed (S - 0 in January), arrives 5 days into
    # the same month and the on-hand settles at 20 at the end of
    # every month: no oscillation, no shortfall.
    recs = [_rec("I1", "S1", 20, 1, 5, 1.0)]
    on_hand = [{"item_code": "I1", "site_code": "S1", "quantity_on_hand": 0}]
    _install(monkeypatch, recs, on_hand, [])
    rows = aggregate_projection(as_of=AS_OF, months=6)
    assert len(rows) == 1
    group = rows[0]
    assert group["group"] == {"dimension": "site", "code": "S1"}
    assert group["item_sites_without_unit_cost"] == 0
    buckets = group["buckets"]
    assert [bucket["month"] for bucket in buckets] == [
        "2026-01",
        "2026-02",
        "2026-03",
        "2026-04",
        "2026-05",
        "2026-06",
    ]
    # Every month orders up to S: 20 + 31, 20 + 28, 20 + 31, 20 + 30,
    # 20 + 31 and 20 + 30, minus the position (0 in January, 20
    # since).
    assert [bucket["planned_receipts_units"] for bucket in buckets] == [
        51,
        28,
        31,
        30,
        31,
        30,
    ]
    for bucket in buckets:
        assert bucket["planned_receipts_units"] > 0
        assert bucket["planned_on_hand_units"] == 20.0
        assert bucket["planned_shortfall"] is False

    # --- a large finite lead time never overflows the arrival date -------------
    # ``lt`` is caller-controlled: a finite lead time whose arrival
    # date is not a representable date must not raise.  A huge
    # positive lead time pushes every planned arrival after the last
    # bucket, so no bucket receives anything: the orders simply stay
    # on order, the planned receipts are 0 everywhere and the
    # planned on-hand equals the projected on-hand (which runs
    # short).
    recs = [_rec("I1", "S1", 20, 1, 1e18, 1.0)]
    _install(monkeypatch, recs, on_hand, [])
    rows = aggregate_projection(as_of=AS_OF, months=6)
    buckets = rows[0]["buckets"]
    assert [bucket["projected_on_hand_units"] for bucket in buckets] == [
        -31.0,
        -59.0,
        -90.0,
        -120.0,
        -151.0,
        -181.0,
    ]
    for bucket in buckets:
        assert bucket["planned_receipts_units"] == 0
        assert bucket["planned_receipts_value"] == 0.0
        assert bucket["planned_on_hand_units"] == bucket["projected_on_hand_units"]
        assert bucket["planned_shortfall"] is True

    # A huge negative lead time is equally unrepresentable and
    # degrades to arriving in the bucket that places the order, so it
    # reproduces the small-lead-time receipts exactly.
    recs = [_rec("I1", "S1", 20, 1, -1e18, 1.0)]
    _install(monkeypatch, recs, on_hand, [])
    rows = aggregate_projection(as_of=AS_OF, months=6)
    buckets = rows[0]["buckets"]
    assert [bucket["planned_receipts_units"] for bucket in buckets] == [
        51,
        28,
        31,
        30,
        31,
        30,
    ]
    for bucket in buckets:
        assert bucket["planned_on_hand_units"] == 20.0
        assert bucket["planned_shortfall"] is False
