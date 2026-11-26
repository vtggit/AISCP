"""Issue #1637 proof: a finished or canceled release is not in flight.

AC-1 — ``app.aggregate.projection._in_flight_supply_lines`` skips a
release row whose ``erp_status`` is one of ``FINAL_ERP_STATUSES`` —
the three statuses after which
``PlanReleasePostgresRepository.list_readback_order_nbrs`` stops
reading the order — while a row whose ``erp_status`` is null or any
other value ("On Hold", "Open") is kept exactly as before.

AC-2 — this proof: item A at site 154 is seeded the way
``tests/test_issue860_freeform.py`` seeds it (30.0 a day for 360
days, a 10-day lead time -> reorder point 300; unit cost 2.0; 40 on
hand and no open lines -> suggested 260).  A "created" plan-release
row with an order number absent from the feed and one line of 100
units of A at site 154 raises A/154's ``on_order_units`` in
``GET /api/recommendations`` by exactly 100 (0 -> 100, suggested
260 -> 160); the same row with ``erp_status`` "Canceled",
"Completed" or "Closed" adds nothing (back to 0), and with
"On Hold", "Open" or a null ``erp_status`` it adds the 100.  Called
directly, ``_in_flight_supply_lines`` returns no line for a row
carrying a final status and keeps the same row's line while its
``erp_status`` is "Open".  The setup's signal day shares,
``projection._signal_day_shares``, given the last representable as-of
date (``date.max``), give no shares for a signal covering an earlier
date — there is no day after it — instead of raising.  The proof
reads every field strictly (``row["on_order_units"]``, never ``.get``
with a default).
"""

import json
from datetime import date, timedelta

import psycopg2
import psycopg2.extras

from app.aggregate import projection
from app.db.connection import get_connection

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
SITE = "154"
ITEM = "A1637"
ORDER_NBR = "PO-FX4-1"
ARRIVAL = date(2026, 10, 1)  # the first day of the release month

SYNC_AS_OF = "2026-09-30T00:00:00+00:00"

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
RELEASE_SQL = (
    "INSERT INTO plan_releases (month, site_code, supplier_code, origin, "
    "order_nbr, outcome, line_count, value, lines, actor, erp_status) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
ORIGIN = f"AIERP order plan {AS_OF_STR} site {SITE} vendor SUP-1637"


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
                f"id-de-1637-{i}",
                f"de-1637-{i}",
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
                f"id-lt-1637-{i}",
                f"lt-1637-{i}",
                "SUP-1637",
                ITEM,
                ordered.isoformat(),
                (ordered + timedelta(days=10)).isoformat(),
                "test",
            ),
        )
    cur.execute(
        ITEM_SITE_SQL,
        ("id-is-1637", ITEM, SITE, 2.0, 300.0, 0.0, None, None, "test"),
    )
    cur.execute(ON_HAND_SQL, ("id-oh-1637", ITEM, SITE, 40.0, SYNC_AS_OF, "test"))


def _set_release(cur, erp_status: str | None) -> None:
    """The one in-flight "created" row, replacing any stored row:
    order number PO-FX4-1 (absent from the feed), one line of 100
    units of A at site 154, month 2026-10, carrying ``erp_status``
    (NULL when None)."""
    cur.execute("DELETE FROM plan_releases")
    cur.execute(
        RELEASE_SQL,
        (
            "2026-10",
            SITE,
            "SUP-1637",
            ORIGIN,
            ORDER_NBR,
            "created",
            1,
            200.0,
            json.dumps([LINE_100]),
            "test",
            erp_status,
        ),
    )


def _stored_release_row(conn) -> dict:
    """The stored plan-release row as ``SELECT *`` returns it — the
    row shape ``_in_flight_release_lines`` passes on."""
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM plan_releases WHERE order_nbr = %s", (ORDER_NBR,))
        rows = cur.fetchall()
    assert len(rows) == 1, rows
    return dict(rows[0])


def _recommendation_row(client) -> dict:
    """Item A at site 154 from GET /api/recommendations (as-of 2026-09-30)."""
    response = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert response.status_code == 200, response.text
    rows = {(row["item_code"], row["site_code"]): row for row in response.json()}
    return rows[(ITEM, SITE)]


def test_issue1637_freeform(client) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # --- baseline: no release rows, no open lines --------------------------
    row = _recommendation_row(client)
    assert row["status"] == "ok", row
    assert row["reorder_point_units"] == ROP_UNITS, row
    assert row["on_hand_units"] == ON_HAND, row
    assert row["on_order_units"] == 0, row
    assert row["suggested_order_quantity"] == BASELINE_SUGGESTED, row

    # --- in flight: a "created" row absent from the feed --------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _set_release(cur, None)
        conn.commit()
    finally:
        conn.close()

    row = _recommendation_row(client)
    # The baseline's 0 rises by exactly 100; the suggested quantity
    # falls by 100 (260 -> 160).
    assert row["on_order_units"] == 100, row
    assert row["suggested_order_quantity"] == BASELINE_SUGGESTED - 100, row

    # --- a finished or canceled release is not in flight ---------------------
    for erp_status in projection.FINAL_ERP_STATUSES:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _set_release(cur, erp_status)
            conn.commit()
        finally:
            conn.close()

        row = _recommendation_row(client)
        assert row["on_order_units"] == 0, (erp_status, row)
        assert row["suggested_order_quantity"] == BASELINE_SUGGESTED, (erp_status, row)

    # --- still in flight: "On Hold", "Open" or a null status -----------------
    for erp_status in ("On Hold", "Open", None):
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _set_release(cur, erp_status)
            conn.commit()
        finally:
            conn.close()

        row = _recommendation_row(client)
        assert row["on_order_units"] == 100, (erp_status, row)
        assert row["suggested_order_quantity"] == BASELINE_SUGGESTED - 100, (
            erp_status,
            row,
        )

    # --- the filter itself, called directly -----------------------------------
    for erp_status in projection.FINAL_ERP_STATUSES:
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                _set_release(cur, erp_status)
                stored = _stored_release_row(conn)
            conn.commit()
        finally:
            conn.close()

        lines = projection._in_flight_supply_lines([stored], AS_OF, set())
        assert lines == [], (erp_status, lines)

    # The same row with a non-final status keeps its line.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _set_release(cur, "Open")
            stored = _stored_release_row(conn)
        conn.commit()
    finally:
        conn.close()

    lines = projection._in_flight_supply_lines([stored], AS_OF, set())
    assert len(lines) == 1, lines
    assert lines[0]["item_code"] == ITEM, lines
    assert lines[0]["site_code"] == SITE, lines
    assert lines[0]["open_units"] == 100.0, lines
    assert lines[0]["unit_cost"] == 2.0, lines
    assert lines[0]["promised_on"] == ARRIVAL, lines

    # --- the signal day-share setup at the last representable date ------
    # There is no day after date.max, so the setup gives no shares for a
    # signal covering an earlier date — it returns [] instead of
    # raising.
    assert (
        projection._signal_day_shares(
            date.max,
            [(1, ARRIVAL, ARRIVAL, 100.0)],
            [],
        )
        == []
    )
