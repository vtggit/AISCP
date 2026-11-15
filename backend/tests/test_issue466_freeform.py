"""Issue #466 proof: the projection reuses the natural as-of basis.

AC-1 — ``app.aggregate.projection.aggregate_projection`` fetches its
recommendations by passing the caller's ORIGINAL ``as_of`` through to
``app.recommend.reorder.recommendations_for_all``: ``None`` reaches
``recommendations_for_all`` as ``None`` and resolves there to the
natural demand basis (the earlier of the current server date and the
latest demand event — the same the recommendations and scenario
endpoints use) instead of the resolved (today-when-None) date.  The
resolved date still drives the bucket months and the future-``as_of``
validation, and everything else (grouping by ``site_code`` over the
``ok`` item-sites, the demand/supply/on-hand roll-forward, the
shortfall flag, the dollar view, the fixed read count, writing
nothing) is unchanged.  The effect: when the demand history ends
before the current server date, ``aggregate_projection(as_of=None)``
rolls up the same ``ok`` item-sites ``recommendations_for_all(as_of=None)``
yields, instead of collapsing to an empty list.

AC-2 — against the test database, with ``date.today`` pinned to
2026-07-01 — 366 days AFTER the seeded demand history ends
(2025-06-30), mirroring the demo where "today" is past the data:

  * the seed is smooth constant daily demand for the full 360-day
    window ending 2025-06-30 (I466-1 @ S1 10/day, I466-2 @ S1 2/day,
    I466-3 @ S2 1/day), three same-duration lead-time observations
    per item (not low confidence), item_sites (I466-2 without a unit
    cost), on-hand (30 / 10 / 100) and open purchase lines (I466-1
    2000 promised 2026-08-15, I466-3 12 promised 2026-09-05);
  * the regression condition: ``recommendations_for_all(as_of=None)``
    yields all three item-sites as ``status "ok"`` (its basis
    resolves to the end of the data, inside its 360-day window) while
    ``recommendations_for_all(as_of=date.today())`` yields none — the
    360-day window ending on the pinned today (2025-07-07 to
    2026-07-01) is empty of demand events;
  * ``aggregate_projection()`` (as_of None) returns a NON-empty list
    of groups whose site codes are exactly the sites of the ``ok``
    item-sites from ``recommendations_for_all(as_of=None)`` (S1, S2,
    ordered) — not an empty list;
  * each group still carries the default six buckets starting from
    the month of (the pinned) today (2026-07), with the
    demand/supply/on-hand and shortfall fields rolling forward
    exactly as before (S1: -332 / 1296 / 936 / 564 / 204 / -168 with
    the shortfall flag on the two negatives; S2: 69 / 38 / 20 / -11 /
    -41 / -72 with the shortfall flag on the three negatives);
  * the explicit stale date still collapses:
    ``aggregate_projection(as_of=date.today())`` returns ``[]``;
  * a future ``as_of`` (after the pinned server date) still raises
    ``ValueError`` and ``months`` outside 1..12 still raises — the
    resolved-date validation is intact.
"""

import sys
from datetime import date, timedelta

import pytest

from app.aggregate.projection import aggregate_projection
from app.db.connection import get_connection
from app.recommend.reorder import recommendations_for_all

END = date(2025, 6, 30)  # the seeded demand history ends here
FIRST_DAY = END - timedelta(days=359)  # 2024-07-06, the 360-day window start
FIXED_TODAY = date(2026, 7, 1)  # 366 days after END: "today" is past the data
FIRST_BUCKET = "2026-07"
S1, S2 = "S1", "S2"
I1, I2, I3 = "I466-1", "I466-2", "I466-3"

SYNC_AS_OF = "2025-06-30T00:00:00+00:00"
DEFAULT_MONTHS = 6

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
    """Demand, lead times, item-sites and position, ending 2025-06-30."""
    # Smooth constant daily demand for the full 360-day window ending
    # on END: I1@S1 10/day, I2@S1 2/day, I3@S2 1/day (all buckets equal
    # -> adi 1.0, cv2 0 -> smooth, recommendable).
    for item, site, quantity in ((I1, S1, 10.0), (I2, S1, 2.0), (I3, S2, 1.0)):
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-466-{item[-1]}-{i}",
                    f"de-466-{item[-1]}-{i}",
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
    # Three same-duration lead-time observations per item (LT the
    # duration, sd_LT 0, n_used 3 -> not low confidence), all received
    # before END.
    for item, duration, first in (
        (I1, 10, "2025-05-01"),
        (I2, 8, "2025-05-05"),
        (I3, 5, "2025-05-10"),
    ):
        base = date.fromisoformat(first)
        for i in range(3):
            ordered = base + timedelta(days=i)
            cur.execute(
                LEAD_SQL,
                (
                    f"id-lt-466-{item[-1]}-{i}",
                    f"lt-466-{item[-1]}-{i}",
                    f"SUP-466-{item[-1]}",
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=duration)).isoformat(),
                    "test",
                ),
            )
    # item_sites: I2 has no unit cost.
    cur.executemany(
        ITEM_SITE_SQL,
        [
            ("id-is-466-1", I1, S1, 2.5, 120.0, 5.0, "test"),
            ("id-is-466-2", I2, S1, None, 40.0, 2.0, "test"),
            ("id-is-466-3", I3, S2, 3.0, 10.0, 0.0, "test"),
        ],
    )
    # on_hand: S1 starts from 30 + 10, S2 from 100.
    cur.executemany(
        ON_HAND_SQL,
        [
            ("id-oh-466-1", I1, S1, 30.0, SYNC_AS_OF, "test"),
            ("id-oh-466-2", I2, S1, 10.0, SYNC_AS_OF, "test"),
            ("id-oh-466-3", I3, S2, 100.0, SYNC_AS_OF, "test"),
        ],
    )
    # Open purchase lines of the newest sync: I1 2000 units promised
    # 2026-08-15 (the second bucket), I3 12 units promised 2026-09-05
    # (the third bucket).
    cur.executemany(
        OPEN_LINE_SQL,
        [
            (
                "id-ol-466-1",
                "bk-466-1",
                I1,
                S1,
                "SUP-466-1",
                "po",
                2000.0,
                "2026-08-15",
                "2025-06-20",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-466-3",
                "bk-466-3",
                I3,
                S2,
                "SUP-466-3",
                "po",
                12.0,
                "2026-09-05",
                "2025-06-20",
                SYNC_AS_OF,
                "test",
            ),
        ],
    )


# Hand-computed expectations for the default months=6, buckets from the
# month of the pinned today: 2026-07 (31), 2026-08 (31), 2026-09 (30),
# 2026-10 (31), 2026-11 (30), 2026-12 (31).
EXPECTED = {
    S1: {  # I1 d 10 (cost 2.5) + I2 d 2 (no cost): 12/day, on-hand 40.
        "without_unit_cost": 1,
        "buckets": [
            # 40 + 0 - 372
            {
                "demand": 372,
                "supply": 0.0,
                "on_hand": -332.0,
                "shortfall": True,
                "demand_value": 775.0,
                "supply_value": 0.0,
            },
            # -332 + 2000 - 372
            {
                "demand": 372,
                "supply": 2000.0,
                "on_hand": 1296.0,
                "shortfall": False,
                "demand_value": 775.0,
                "supply_value": 5000.0,
            },
            # 12 x 30 = 360; value 10 x 30 x 2.5
            {
                "demand": 360,
                "supply": 0.0,
                "on_hand": 936.0,
                "shortfall": False,
                "demand_value": 750.0,
                "supply_value": 0.0,
            },
            {
                "demand": 372,
                "supply": 0.0,
                "on_hand": 564.0,
                "shortfall": False,
                "demand_value": 775.0,
                "supply_value": 0.0,
            },
            {
                "demand": 360,
                "supply": 0.0,
                "on_hand": 204.0,
                "shortfall": False,
                "demand_value": 750.0,
                "supply_value": 0.0,
            },
            {
                "demand": 372,
                "supply": 0.0,
                "on_hand": -168.0,
                "shortfall": True,
                "demand_value": 775.0,
                "supply_value": 0.0,
            },
        ],
    },
    S2: {  # I3 d 1 (cost 3.0), on-hand 100.
        "without_unit_cost": 0,
        "buckets": [
            {
                "demand": 31,
                "supply": 0.0,
                "on_hand": 69.0,
                "shortfall": False,
                "demand_value": 93.0,
                "supply_value": 0.0,
            },
            {
                "demand": 31,
                "supply": 0.0,
                "on_hand": 38.0,
                "shortfall": False,
                "demand_value": 93.0,
                "supply_value": 0.0,
            },
            # 39 - 30 + 12
            {
                "demand": 30,
                "supply": 12.0,
                "on_hand": 20.0,
                "shortfall": False,
                "demand_value": 90.0,
                "supply_value": 36.0,
            },
            {
                "demand": 31,
                "supply": 0.0,
                "on_hand": -11.0,
                "shortfall": True,
                "demand_value": 93.0,
                "supply_value": 0.0,
            },
            {
                "demand": 30,
                "supply": 0.0,
                "on_hand": -41.0,
                "shortfall": True,
                "demand_value": 90.0,
                "supply_value": 0.0,
            },
            {
                "demand": 31,
                "supply": 0.0,
                "on_hand": -72.0,
                "shortfall": True,
                "demand_value": 93.0,
                "supply_value": 0.0,
            },
        ],
    },
}


def test_issue466_freeform(client, monkeypatch):
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)

    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    # Pin date.today everywhere it is read: the projection's resolved
    # date, the demand statistics' natural basis, and this module's own
    # date.today() so the regression calls use the pinned date.
    monkeypatch.setattr("app.aggregate.projection.date", _FixedDate)
    monkeypatch.setattr("app.stats.demand.date", _FixedDate)
    monkeypatch.setattr(sys.modules[__name__], "date", _FixedDate)

    # --- seed --------------------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # --- the regression condition -------------------------------------------
    # The natural basis (None -> the earlier of today and the latest
    # demand event = END) sees the data...
    natural = recommendations_for_all(as_of=None)
    ok = {
        (row["item_code"], row["site_code"]): row
        for row in natural
        if row["status"] == "ok"
    }
    assert set(ok) == {(I1, S1), (I2, S1), (I3, S2)}
    # ...while the pinned today's 360-day window (2025-07-07 to
    # 2026-07-01) is empty of demand events: no recommendable item-site.
    stale = recommendations_for_all(as_of=date.today())
    assert all(row["status"] != "ok" for row in stale)

    # --- the fix: as_of None rolls up the natural basis' ok set -------------
    groups = aggregate_projection()
    assert groups, "the projection must not collapse to an empty list"
    assert [group["group"]["code"] for group in groups] == sorted(
        {site for _, site in ok}
    )

    # --- the buckets: default six, from the month of (pinned) today ---------
    for group in groups:
        expected = EXPECTED[group["group"]["code"]]
        assert len(group["buckets"]) == DEFAULT_MONTHS
        assert group["buckets"][0]["month"] == FIRST_BUCKET
        assert group["item_sites_without_unit_cost"] == expected["without_unit_cost"]
        for row, want in zip(group["buckets"], expected["buckets"]):
            assert row["projected_demand_units"] == want["demand"]
            assert row["projected_supply_units"] == pytest.approx(want["supply"])
            assert row["projected_on_hand_units"] == pytest.approx(want["on_hand"])
            assert row["projected_shortfall"] is want["shortfall"]
            # The shortfall flag tracks the sign of the rolled on-hand.
            assert row["projected_shortfall"] is (row["projected_on_hand_units"] < 0)
            assert row["projected_demand_value"] == pytest.approx(want["demand_value"])
            assert row["projected_supply_value"] == pytest.approx(want["supply_value"])
    assert [row["month"] for row in groups[0]["buckets"]] == [
        "2026-07",
        "2026-08",
        "2026-09",
        "2026-10",
        "2026-11",
        "2026-12",
    ]

    # --- the explicit stale date still collapses to an empty list ------------
    # Only the None path changed: an explicit as_of is passed through as
    # given, so the pinned today (past the data) still yields no groups.
    assert aggregate_projection(as_of=date.today()) == []

    # --- the resolved-date validation is intact -------------------------------
    with pytest.raises(ValueError):
        aggregate_projection(as_of=FIXED_TODAY + timedelta(days=1))
    with pytest.raises(ValueError):
        aggregate_projection(months=0)
    with pytest.raises(ValueError):
        aggregate_projection(months=13)
