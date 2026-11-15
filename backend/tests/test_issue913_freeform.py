"""Issue #913 proof: one recommendations computation per decision summary.

AC-1 — ``app.api.decision_summary.decision_summary`` gathers its rows
inside ``app.recommend.reorder.recommendations_memo``: the excess
ranking's and the stockout ranking's one ``recommendations_for_all``
call each (both at the request's resolved as-of) share the memo's
single computation, so the one ``GET /api/decisions/summary`` runs the
recommendations computation exactly once.  The answer is exactly the
same as before for the same data — the memo answers a repeated
same-as-of call with a deep copy of the first call's rows — and every
other route is unchanged.

AC-2 — this proof seeds demand, lead times, item-sites and on-hand
the way ``tests/test_issue910_freeform.py`` does (fixed dates, raw
INSERTs; "today" is pinned on the data date the way that test pins
its ranking inputs, so both rankings' resolved as-of is that date).
On top of that position it seeds one current decision per kind and per
bucket: a drift decision applied (I1, the item-site's reorder point
within one of the recommendation) and one outstanding without money
(I2), a drift dismissed (I3/S1) and one snoozed (I3/S2); an excess
accepted while its item-site is still listed (I3 — the one dead row
the excess ranking lists) and one no longer listed (I1), an excess
snoozed (I2) and a dismissed (I3/S2); a stockout accepted while still
listed (I1 — the one row the stockout ranking lists) and one no longer
listed without money (I2), a stockout dismissed (I3/S1) and a snoozed
(I3/S2).  It checks: once (one ``GET /api/decisions/summary`` runs the
recommendations computation exactly once, counted the way the #910
proof counts it — wrapping ``reorder._compute_recommendations``), the
same answer (its JSON equals the JSON of the same request computed
with the memo disabled — the two ``recommendations_for_all`` calls
then compute twice), and isolation (two consecutive requests each
compute once, so no memo leaks between requests).
"""

from contextlib import contextmanager
from datetime import date, timedelta

from app.db.connection import get_connection
from app.recommend import reorder

# The pinned "today": the data date, so both rankings' resolved
# as-of (``app.stats.demand.default_as_of`` over the seeded demand)
# is that date.
TODAY = date(2026, 10, 2)
TODAY_STR = TODAY.isoformat()
FIRST_DAY = TODAY - timedelta(days=359)
S1 = "S1"
S2 = "S2"
I1, I2, I3 = "I913-1", "I913-2", "I913-3"
SYNC_AS_OF = f"{TODAY_STR}T00:00:00+00:00"
DECIDED_BY = "seeder"

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
    "reorder_point, safety_stock, max_qty, order_qty, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
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
PLANNER_DECISION_SQL = (
    "INSERT INTO planner_decisions (id, item_code, site_code, decision, "
    "as_of, recommended_reorder_point_units, current_reorder_point, "
    "money_at_stake, decided_by, decided_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
EXCESS_DECISION_SQL = (
    "INSERT INTO excess_decisions (id, item_code, site_code, decision, "
    "action, reason, snooze_until, as_of, category, dead_reason, "
    "on_hand_units, excess_units, money_tied_up, decided_by, decided_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
STOCKOUT_DECISION_SQL = (
    "INSERT INTO stockout_decisions (id, item_code, site_code, decision, "
    "action, reason, snooze_until, as_of, category, p_stockout, "
    "on_hand_units, open_supply_units, expected_units_short, "
    "money_at_risk, decided_by, decided_at) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def _seed(cur) -> None:
    """The fixed-date rows: demand, lead times, item-sites, position,
    and the three kinds' current decisions.

    I1: 10.0 a day for 360 days (d 10, sd_d 0), LT 10, unit cost 2.0,
    30 on hand, no open lines — the one stockout-likely row (its
    position 30 below its 100 lead-time demand, no variability).
    I2: 2.0 a day for 360 days (d 2, sd_d 0), LT 5, unit cost 5.0,
    2 on hand, one open line of 12 units promised 5 days before
    today — its open supply lifts it out of stockout risk, and its
    cover window makes it neither dead nor excess.
    I3: no demand and no lead time, unit cost 3.0, 50 on hand — the
    one dead row the excess ranking lists (never moved, the whole on
    hand tied up) and in no ranking of the stockout kind.
    """
    for item, suffix, quantity in ((I1, "1", 10.0), (I2, "2", 2.0)):
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-913-{suffix}-{i}",
                    f"de-913-{suffix}-{i}",
                    item,
                    S1,
                    quantity,
                    (FIRST_DAY + timedelta(days=i)).isoformat(),
                    "shipment",
                    "test",
                )
                for i in range(360)
            ],
        )
    for item, suffix, duration, first in (
        (I1, "1", 10, "2026-09-20"),
        (I2, "2", 5, "2026-09-25"),
    ):
        base = date.fromisoformat(first)
        for i in range(3):
            ordered = base + timedelta(days=i)
            cur.execute(
                LEAD_SQL,
                (
                    f"id-lt-913-{suffix}-{i}",
                    f"lt-913-{suffix}-{i}",
                    f"SUP-913-{suffix}",
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=duration)).isoformat(),
                    "test",
                ),
            )
    cur.executemany(
        ITEM_SITE_SQL,
        [
            ("id-is-913-1", I1, S1, 2.0, 120.0, 5.0, 400.0, 50.0, "test"),
            ("id-is-913-2", I2, S1, 5.0, 12.0, 1.0, None, None, "test"),
            ("id-is-913-3", I3, S1, 3.0, None, None, None, None, "test"),
        ],
    )
    cur.executemany(
        ON_HAND_SQL,
        [
            ("id-oh-913-1", I1, S1, 30.0, SYNC_AS_OF, "test"),
            ("id-oh-913-2", I2, S1, 2.0, SYNC_AS_OF, "test"),
            ("id-oh-913-3", I3, S1, 50.0, SYNC_AS_OF, "test"),
        ],
    )
    # The one open line: I2's, promised 5 days before today (5 days
    # late) and ordered 20 days before today.
    cur.execute(
        OPEN_LINE_SQL,
        (
            "id-ol-913-1",
            "PO:ORD-913-1:1",
            I2,
            S1,
            "SUP-913-2",
            "po",
            12.0,
            (TODAY - timedelta(days=5)).isoformat(),
            (TODAY - timedelta(days=20)).isoformat(),
            SYNC_AS_OF,
            "test",
        ),
    )
    # The three kinds' current decisions, one per bucket: I1's drift
    # decision is applied (its item-site's reorder point 120.0 is the
    # recommendation 120), I2's is outstanding without money; I1's
    # excess decision is applied (no longer listed) and I3's is
    # outstanding (still the listed dead row); I1's stockout decision
    # is outstanding (still the listed risk row) and I2's is applied
    # without money (no longer listed).
    cur.executemany(
        PLANNER_DECISION_SQL,
        [
            (
                "pd-913-1",
                I1,
                S1,
                "accepted",
                TODAY_STR,
                120,
                120.0,
                500.0,
                DECIDED_BY,
                "2026-10-02T09:00:00+00:00",
            ),
            (
                "pd-913-2",
                I2,
                S1,
                "accepted",
                TODAY_STR,
                40,
                12.0,
                None,
                DECIDED_BY,
                "2026-10-02T09:05:00+00:00",
            ),
            (
                "pd-913-3",
                I3,
                S1,
                "dismissed",
                TODAY_STR,
                0,
                None,
                250.0,
                DECIDED_BY,
                "2026-10-02T09:10:00+00:00",
            ),
            (
                "pd-913-4",
                I3,
                S2,
                "snoozed",
                TODAY_STR,
                0,
                None,
                None,
                DECIDED_BY,
                "2026-10-02T09:15:00+00:00",
            ),
        ],
    )
    cur.executemany(
        EXCESS_DECISION_SQL,
        [
            (
                "ed-913-1",
                I3,
                S1,
                "accepted",
                "dispose",
                None,
                None,
                TODAY_STR,
                "dead",
                "never_moved",
                50.0,
                50.0,
                150.0,
                DECIDED_BY,
                "2026-10-02T10:00:00+00:00",
            ),
            (
                "ed-913-2",
                I1,
                S1,
                "accepted",
                "reduce",
                None,
                None,
                TODAY_STR,
                "excess",
                None,
                30.0,
                10.0,
                60.0,
                DECIDED_BY,
                "2026-10-02T10:05:00+00:00",
            ),
            (
                "ed-913-3",
                I2,
                S1,
                "snoozed",
                None,
                "review later",
                "2026-10-16",
                TODAY_STR,
                "excess",
                None,
                2.0,
                2.0,
                None,
                DECIDED_BY,
                "2026-10-02T10:10:00+00:00",
            ),
            (
                "ed-913-4",
                I3,
                S2,
                "dismissed",
                None,
                "not our site",
                None,
                TODAY_STR,
                "dead",
                "never_moved",
                5.0,
                5.0,
                None,
                DECIDED_BY,
                "2026-10-02T10:15:00+00:00",
            ),
        ],
    )
    cur.executemany(
        STOCKOUT_DECISION_SQL,
        [
            (
                "sd-913-1",
                I1,
                S1,
                "accepted",
                "expedite",
                None,
                None,
                TODAY_STR,
                "likely",
                0.9,
                30.0,
                0.0,
                70.0,
                60.0,
                DECIDED_BY,
                "2026-10-02T11:00:00+00:00",
            ),
            (
                "sd-913-2",
                I2,
                S1,
                "accepted",
                "raise_order",
                None,
                None,
                TODAY_STR,
                "likely",
                0.5,
                2.0,
                12.0,
                10.0,
                None,
                DECIDED_BY,
                "2026-10-02T11:05:00+00:00",
            ),
            (
                "sd-913-3",
                I3,
                S1,
                "dismissed",
                None,
                "no risk",
                None,
                TODAY_STR,
                "at_risk",
                0.2,
                50.0,
                0.0,
                0.0,
                90.0,
                DECIDED_BY,
                "2026-10-02T11:10:00+00:00",
            ),
            (
                "sd-913-4",
                I3,
                S2,
                "snoozed",
                None,
                "wait for EOQ",
                "2026-11-02",
                TODAY_STR,
                "at_risk",
                0.3,
                0.0,
                0.0,
                5.0,
                None,
                DECIDED_BY,
                "2026-10-02T11:15:00+00:00",
            ),
        ],
    )


def test_issue913_freeform(client, monkeypatch) -> None:
    # Pin "today" on the data date, the way test_issue910_freeform
    # pins its ranking inputs: both rankings' resolved as-of (the
    # shared default over the seeded demand) is that date.
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return TODAY

    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    # Count the underlying computations the way the #910 proof
    # counts them, by wrapping the module's internal builder; each
    # entry is the computed as-of.
    computed: list[date] = []
    real_builder = reorder._compute_recommendations

    def counting_builder(resolved_as_of: date) -> list[dict]:
        computed.append(resolved_as_of)
        return real_builder(resolved_as_of)

    monkeypatch.setattr(reorder, "_compute_recommendations", counting_builder)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    expected = {
        "drift": {
            "accepted": {"count": 2, "money": 500.0, "without_money": 1},
            "applied": {"count": 1, "money": 500.0, "without_money": 0},
            "outstanding": {"count": 1, "money": 0.0, "without_money": 1},
            "dismissed": {"count": 1},
            "snoozed": {"count": 1},
        },
        "excess": {
            "accepted": {"count": 2, "money": 210.0, "without_money": 0},
            "applied": {"count": 1, "money": 60.0, "without_money": 0},
            "outstanding": {"count": 1, "money": 150.0, "without_money": 0},
            "dismissed": {"count": 1},
            "snoozed": {"count": 1},
        },
        "stockout": {
            "accepted": {"count": 2, "money": 60.0, "without_money": 1},
            "applied": {"count": 1, "money": 0.0, "without_money": 1},
            "outstanding": {"count": 1, "money": 60.0, "without_money": 0},
            "dismissed": {"count": 1},
            "snoozed": {"count": 1},
        },
        "total": {
            "accepted": {"count": 6, "money": 770.0, "without_money": 2},
            "applied": {"count": 3, "money": 560.0, "without_money": 1},
            "outstanding": {"count": 3, "money": 210.0, "without_money": 1},
            "dismissed": {"count": 3},
            "snoozed": {"count": 3},
        },
    }

    # --- AC-1 / AC-2: once ----------------------------------------------------
    computed.clear()
    response = client.get("/api/decisions/summary")
    assert response.status_code == 200, response.text
    body = response.json()
    # The one request runs the recommendations computation exactly
    # once, at the one shared as-of, and answers every bucket.
    assert computed == [TODAY]
    assert body == expected

    # --- AC-2: the same answer with the memo disabled -------------------------
    # With the memo disabled the two rankings' one
    # recommendations_for_all call each computes on its own (twice,
    # in order), and the JSON is exactly the memoized request's JSON.

    @contextmanager
    def _memo_disabled():
        yield

    monkeypatch.setattr("app.api.decision_summary.recommendations_memo", _memo_disabled)
    computed.clear()
    response2 = client.get("/api/decisions/summary")
    assert response2.status_code == 200, response2.text
    assert computed == [TODAY, TODAY]
    assert response2.json() == body
    assert response2.json() == expected

    # --- AC-2: isolation -------------------------------------------------------
    # Two consecutive requests each compute exactly once: the memo is
    # per-context and cleared on exit, so no memo leaks between
    # requests — and both answer the same JSON.
    monkeypatch.setattr(
        "app.api.decision_summary.recommendations_memo",
        reorder.recommendations_memo,
    )
    computed.clear()
    first = client.get("/api/decisions/summary")
    assert first.status_code == 200, first.text
    assert computed == [TODAY]
    assert first.json() == body
    second = client.get("/api/decisions/summary")
    assert second.status_code == 200, second.text
    assert computed == [TODAY, TODAY]
    assert second.json() == body
