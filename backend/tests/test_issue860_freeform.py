"""Issue #860 proof: in-flight releases count as on-order supply.

AC-1 — ``app.recommend.reorder._on_order_units_by_key`` and
``_on_order_units_for`` add each item-site's in-flight release units to
the open purchase units they sum today: the ``plan_releases`` rows
``app.aggregate.projection._in_flight_release_lines`` reads, kept by
``app.aggregate.projection._in_flight_supply_lines`` against the same
newest open-purchase snapshot's order numbers
(``app.aggregate.projection._feed_order_numbers``) — a row counts when
its ``outcome`` is "created" or "exists", its ``order_nbr`` is
non-null and that order number is not among the feed's, and each line
adds its ``quantity`` to (item_code, the row's site_code); a released
order the feed has picked up is counted from the feed only, never
twice, and a row with a malformed month, site code, order number or
lines contributes nothing (never a 500).  ``suggested_order_quantity``
and every consumer of ``on_order_units`` (the supply plan, its
release, the transfer suggestions, the exceptions inbox) follow with
no change of their own; no response gains or loses a field.

AC-2 — this proof: item A at site 154 has reorder point R (300 units
— 30.0 a day for 360 days, a 10-day lead time) and 40 on hand, no
open lines (suggested 260).  A "created" plan-release row with order
number "PO-FX3-1" and one line of 100 units of A at site 154, absent
from the feed, raises A/154's ``on_order_units`` by exactly 100 in
``GET /api/recommendations`` (100) and its ``suggested_order_quantity``
falls by 100 (260 -> 160); the same row with outcome "exists" counts
the same 100; rows with outcome "error", "pending" or "aborted", or
with a null ``order_nbr``, add nothing; once the newest
open-purchase snapshot carries a line whose business key holds
"PO-FX3-1", A/154 counts only the feed's open units for it (50, the
older snapshot's 999 never read); with enough in-flight units to lift
A/154's position to its reorder point or above (40 + 500 = 540, whose
240 units above the point burn 8 days at 30 a day, past the 7-day
horizon) ``GET /api/supply-plan`` no longer lists A/154; a "created"
row whose lines are not a list, next to a valid row, leaves the valid
row's 100 counted and answers 200.
"""

import json
from datetime import date, timedelta
from unittest import mock

from app.aggregate import projection as _projection
from app.db.connection import get_connection
from app.recommend import reorder as _reorder
from app.recommend.reorder import recommendation_for_key

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
SITE = "154"
ITEM = "A860"
ORDER_NBR = "PO-FX3-1"
# The wall clock pinned while proving the in-flight evaluation runs as
# of the recommendation's as-of date, never the server's current date.
WALL_CLOCK = date(2031, 1, 1)

SYNC_AS_OF = "2026-09-30T00:00:00+00:00"
OLD_SYNC_AS_OF = "2026-09-29T00:00:00+00:00"

# Item A at site 154: 30.0 a day for 360 days (d 30, sd_d 0), a 10-day
# lead time (three observations) -> ROP 300.0 (300 units); 40 on hand
# and no open lines -> suggested 260.
ROP_UNITS = 300
ON_HAND = 40
BASELINE_SUGGESTED = ROP_UNITS - ON_HAND  # 260
LINE_100 = {"item_code": ITEM, "quantity": 100, "unit_cost": 2.0, "value": 200.0}

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
RELEASE_SQL = (
    "INSERT INTO plan_releases (month, site_code, supplier_code, origin, "
    "order_nbr, outcome, line_count, value, lines, actor) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
ORIGIN = f"AIERP order plan {AS_OF_STR} site {SITE} vendor SUP-860"


class _FakeDate:
    """datetime.date for app.recommend.reorder with a pinned wall
    clock: the in-flight read's ``today()`` is the only call-time date
    use the two paths exercise."""

    @staticmethod
    def today() -> date:
        return WALL_CLOCK


def _seed(cur) -> None:
    """The fixed-date rows: demand, lead times, item-site, position.

    Item A at site 154: 30.0 a day for 360 days (d 30, sd_d 0), a
    10-day lead time (three observations) -> ROP 300.0 (300 units);
    unit cost 2.0; 40 on hand and no open lines -> suggested 260.
    """
    cur.executemany(
        DEMAND_SQL,
        [
            (
                f"id-de-860-{i}",
                f"de-860-{i}",
                ITEM,
                SITE,
                30.0,
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
                f"id-lt-860-{i}",
                f"lt-860-{i}",
                "SUP-860",
                ITEM,
                ordered.isoformat(),
                (ordered + timedelta(days=10)).isoformat(),
                "test",
            ),
        )
    cur.execute(
        ITEM_SITE_SQL,
        ("id-is-860", ITEM, SITE, 2.0, 300.0, 0.0, None, None, "test"),
    )
    cur.execute(ON_HAND_SQL, ("id-oh-860", ITEM, SITE, 40.0, SYNC_AS_OF, "test"))


def _insert_release(
    cur,
    order_nbr: str | None,
    outcome: str,
    lines: list | str,
    month: str = "2026-10",
) -> None:
    """One plan_releases row of item A at site 154; ``lines`` a line
    snapshot list (the released quantity) or a hostile value."""
    if isinstance(lines, list):
        line_count = len(lines)
        value = sum(line["quantity"] * line["unit_cost"] for line in lines)
    else:
        line_count = 1
        value = 0
    cur.execute(
        RELEASE_SQL,
        (
            month,
            SITE,
            "SUP-860",
            ORIGIN,
            order_nbr,
            outcome,
            line_count,
            value,
            json.dumps(lines),
            "test",
        ),
    )


def _recommendation_row(client) -> dict:
    """Item A at site 154 from GET /api/recommendations (as-of 2026-09-30)."""
    response = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert response.status_code == 200, response.text
    rows = {(row["item_code"], row["site_code"]): row for row in response.json()}
    return rows[(ITEM, SITE)]


def _plan_pairs(client) -> set[tuple[str, str]]:
    """The (item_code, site_code) pairs GET /api/supply-plan lists."""
    response = client.get("/api/supply-plan")
    assert response.status_code == 200, response.text
    plan = response.json()
    return {
        (line["item_code"], line["site_code"])
        for supplier in plan["suppliers"]
        for line in supplier["lines"]
    }


def test_issue860_freeform(client) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # --- baseline: no in-flight releases, no open lines -------------------
    row = _recommendation_row(client)
    assert row["status"] == "ok", row
    assert row["reorder_point_units"] == ROP_UNITS, row
    assert row["on_hand_units"] == ON_HAND, row
    assert row["on_order_units"] == 0, row
    assert row["suggested_order_quantity"] == BASELINE_SUGGESTED, row

    # --- in flight: a "created" row absent from the feed ------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _insert_release(cur, ORDER_NBR, "created", [LINE_100])
        conn.commit()
    finally:
        conn.close()

    row = _recommendation_row(client)
    # The baseline's 0 rises by exactly 100; the suggested quantity
    # falls by 100 (260 -> 160).
    assert row["on_order_units"] == 100, row
    assert row["suggested_order_quantity"] == BASELINE_SUGGESTED - 100, row
    # The single-key path counts the same in-flight units.
    single = recommendation_for_key(ITEM, SITE, as_of=AS_OF)
    assert single["on_order_units"] == 100
    assert single["suggested_order_quantity"] == BASELINE_SUGGESTED - 100

    # --- in-flight supply is evaluated as of the recommendation ----------
    # The projection clamps its in-flight arrivals against its own
    # as-of; the recommendation must do the same with its resolved
    # as-of date, never the wall clock.  Pin the wall clock to a
    # sentinel and capture the start date each in-flight filter
    # actually runs with, over both the list and the single-key path.
    captured_starts: list[date] = []
    real_supply_lines = _projection._in_flight_supply_lines

    def _capturing_supply_lines(release_rows, start, feed_order_numbers):
        captured_starts.append(start)
        return real_supply_lines(release_rows, start, feed_order_numbers)

    _projection._in_flight_supply_lines = _capturing_supply_lines
    try:
        with mock.patch.object(_reorder, "date", _FakeDate):
            row = _recommendation_row(client)
            single = recommendation_for_key(ITEM, SITE, as_of=AS_OF)
    finally:
        _projection._in_flight_supply_lines = real_supply_lines

    # One in-flight read per call, each run as of the recommendation's
    # as-of date (2026-09-30) — never the pinned wall clock
    # (2031-01-01) — and the in-flight 100 still counts in both.
    assert captured_starts == [AS_OF, AS_OF], captured_starts
    assert row["on_order_units"] == 100, row
    assert row["suggested_order_quantity"] == BASELINE_SUGGESTED - 100, row
    assert single["on_order_units"] == 100, single
    assert single["suggested_order_quantity"] == BASELINE_SUGGESTED - 100, single

    # --- "exists" counts the same ------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_releases WHERE order_nbr = %s", (ORDER_NBR,))
            _insert_release(cur, ORDER_NBR, "exists", [LINE_100])
        conn.commit()
    finally:
        conn.close()

    row = _recommendation_row(client)
    assert row["on_order_units"] == 100, row
    assert row["suggested_order_quantity"] == BASELINE_SUGGESTED - 100, row

    # --- not in flight: error / pending / aborted / null order_nbr --------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_releases")
            _insert_release(cur, "PO-FX3-2", "error", [LINE_100])
            _insert_release(cur, "PO-FX3-3", "pending", [LINE_100])
            _insert_release(cur, "PO-FX3-4", "aborted", [LINE_100])
            _insert_release(cur, None, "created", [LINE_100])
        conn.commit()
    finally:
        conn.close()

    row = _recommendation_row(client)
    assert row["on_order_units"] == 0, row
    assert row["suggested_order_quantity"] == BASELINE_SUGGESTED, row

    # --- never twice: the newest snapshot carries the order number --------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            # An older snapshot: the newest-snapshot sum never reads it.
            cur.execute(
                OPEN_LINE_SQL,
                (
                    "id-ol-860-old",
                    "po:OLD-9:1",
                    ITEM,
                    SITE,
                    "SUP-860",
                    "po",
                    999.0,
                    "2026-10-05",
                    "2026-09-19",
                    OLD_SYNC_AS_OF,
                    "test",
                ),
            )
            # The newest snapshot holds "PO-FX3-1": the released order
            # is counted from the feed only.
            cur.execute(
                OPEN_LINE_SQL,
                (
                    "id-ol-860-feed",
                    "po:PO-FX3-1:1",
                    ITEM,
                    SITE,
                    "SUP-860",
                    "po",
                    50.0,
                    "2026-10-05",
                    "2026-09-20",
                    SYNC_AS_OF,
                    "test",
                ),
            )
            _insert_release(cur, ORDER_NBR, "created", [LINE_100])
        conn.commit()
    finally:
        conn.close()

    row = _recommendation_row(client)
    assert row["on_order_units"] == 50, row
    assert row["suggested_order_quantity"] == ROP_UNITS - (ON_HAND + 50), row

    # --- the order plan follows --------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_releases")
            cur.execute("DELETE FROM open_purchase_lines")
        conn.commit()
    finally:
        conn.close()

    # Position 40 at or below the reorder point: due in 0 days, listed.
    pairs = _plan_pairs(client)
    assert (ITEM, SITE) in pairs, pairs

    # 500 in-flight units (one "created" row, two lines) lift the
    # position to 540, above the reorder point: the 240 units left
    # above it burn 8 days at 30 a day, past the 7-day horizon, so the
    # plan no longer lists A/154.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _insert_release(
                cur,
                "PO-FX3-10",
                "created",
                [
                    {
                        "item_code": ITEM,
                        "quantity": 300,
                        "unit_cost": 2.0,
                        "value": 600.0,
                    },
                    {
                        "item_code": ITEM,
                        "quantity": 200,
                        "unit_cost": 2.0,
                        "value": 400.0,
                    },
                ],
            )
        conn.commit()
    finally:
        conn.close()

    pairs = _plan_pairs(client)
    assert (ITEM, SITE) not in pairs, pairs

    # --- hostile row: lines not a list --------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM plan_releases")
            _insert_release(cur, "PO-FX3-20", "created", "not-a-list")
            _insert_release(cur, "PO-FX3-21", "created", [LINE_100])
        conn.commit()
    finally:
        conn.close()

    response = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert response.status_code == 200, response.text
    rows = {(row["item_code"], row["site_code"]): row for row in response.json()}
    row = rows[(ITEM, SITE)]
    # The hostile row contributes nothing; the valid row's 100 counts.
    assert row["on_order_units"] == 100, row
    assert row["suggested_order_quantity"] == BASELINE_SUGGESTED - 100, row
