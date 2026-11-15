"""Issue #897 proof: in-flight transfer releases count as on-order supply.

AC-1 — ``app.recommend.reorder._on_order_units_by_key`` and
``_on_order_units_for`` also apply the in-flight transfer units: a
``transfer_releases`` row is in flight when its ``outcome`` is
"created" or "exists", its ``reference_nbr`` is non-null and its
``erp_status`` is null or none of "Released", "Completed" and
"Canceled".  The rows are read once per call through
``TransferReleasePostgresRepository.list_in_flight`` (every row whose
outcome is "created" or "exists" and whose ``reference_nbr`` is
non-null, one SELECT with no limit — ``list_newest`` keeps its 50-row
default and is not used for this), and each line (item_code,
quantity) adds its quantity to (item_code, the row's to_site_code)
and subtracts it from (item_code, the row's from_site_code) — that
stock is committed to leave.  Once the ERP reads the order "Released"
the row stops counting (the on-hand sync then shows the moved stock,
so nothing is counted twice).  ``suggested_order_quantity`` and every
consumer of ``on_order_units`` (the supply plan, its release, the
transfer suggestions, the exceptions inbox) follow with no change of
their own; no response gains or loses a field.  A row with malformed
lines or a non-numeric quantity contributes nothing (never a 500).
The purchase-order in-flight rule (#860) and every other route are
unchanged.

AC-2 — this proof: item A at sites 154 and 159, seeded the way
issue #860's proof seeds item-sites, demand, lead times, on-hand and
open purchase lines.  A "created" row with reference "000901",
``erp_status`` "On Hold" and one line of 120 units of A from 154 to
159 raises A/159's ``on_order_units`` by exactly 120 in
``GET /api/recommendations`` and lowers A/154's by exactly 120; the
same row with a null ``erp_status`` counts the same, and the same row
with ``erp_status`` "Released", "Completed" or "Canceled" counts
nothing; rows with outcome "error", "pending" or "aborted", or with a
null ``reference_nbr``, count nothing; with A/159 short by 100 and
the in-flight row of 120, ``GET /api/supply-plan`` no longer lists
A/159 and ``GET /api/supply-plan/transfers`` no longer suggests A
from 154 to 159; a row whose ``lines`` is not a list, or whose line
carries a non-numeric or a too-large integer quantity, next to a valid
row, leaves the valid row's 120 counted and answers 200.
"""

import json
from datetime import date, timedelta

from app.db.connection import get_connection
from app.recommend.reorder import recommendation_for_key

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
ITEM = "A897"
FROM_SITE = "154"
TO_SITE = "159"
REFERENCE_NBR = "000901"
TRANSFER_ORIGIN = f"AIERP transfer {AS_OF_STR} site {FROM_SITE} to {TO_SITE}"

SYNC_AS_OF = "2026-09-30T00:00:00+00:00"

# Item A at site 154 (the donor): 30.0 a day for 360 days (d 30,
# sd_d 0), a 50-day lead time (three identical observations) -> ROP
# 1500 units; 4000 on hand and 50 open -> position 4050, far above
# the point (not in the plan), holding 4050 - (1500 + 1480 order
# quantity) = 1070 beyond ROP + order quantity.
# Item A at site 159 (the receiver): 2.0 a day for 360 days (d 2,
# sd_d 0), the same 50-day lead time -> ROP 100 units; 0 on hand and
# no open lines -> short by 100.
DONOR_ON_HAND = 4000
DONOR_OPEN = 50
DONOR_ROP = 1500
DONOR_ORDER_UNITS = 1480  # EOQ 1479.86, whole-unit ceiling
DONOR_BASELINE_ON_ORDER = DONOR_OPEN
DONOR_SURPLUS = DONOR_ON_HAND + DONOR_OPEN - (DONOR_ROP + DONOR_ORDER_UNITS)
RECEIVER_ROP = 100
RECEIVER_BASELINE_SUGGESTED = RECEIVER_ROP  # short by 100, nothing in
TRANSFER_UNITS = 120
LINE_120 = {"item_code": ITEM, "quantity": TRANSFER_UNITS}

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
TRANSFER_SQL = (
    "INSERT INTO transfer_releases (origin, from_site_code, to_site_code, "
    "reference_nbr, outcome, line_count, lines, actor, erp_status) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)"
)


def _seed(cur) -> None:
    """The fixed-date rows: demand, lead times, item-sites, position.

    Item A at site 154: 30.0 a day for 360 days (d 30, sd_d 0), a
    50-day lead time (three identical observations) -> ROP 1500 units;
    4000 on hand, 50 open.  Item A at site 159: 2.0 a day for 360 days
    (d 2, sd_d 0) -> ROP 100 units; 0 on hand, nothing open — short
    by 100.
    """
    for site, daily in ((FROM_SITE, 30.0), (TO_SITE, 2.0)):
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-897-{site}-{i}",
                    f"de-897-{site}-{i}",
                    ITEM,
                    site,
                    daily,
                    (FIRST_DAY + timedelta(days=i)).isoformat(),
                    "shipment",
                    "test",
                )
                for i in range(360)
            ],
        )
    base = date(2026, 7, 10)
    for i in range(3):
        ordered = base + timedelta(days=i)
        cur.execute(
            LEAD_SQL,
            (
                f"id-lt-897-{i}",
                f"lt-897-{i}",
                "SUP-897",
                ITEM,
                ordered.isoformat(),
                (ordered + timedelta(days=50)).isoformat(),
                "test",
            ),
        )
    cur.execute(
        ITEM_SITE_SQL,
        ("id-is-897-154", ITEM, FROM_SITE, 2.0, 1500.0, 0.0, None, None, "test"),
    )
    cur.execute(
        ITEM_SITE_SQL,
        ("id-is-897-159", ITEM, TO_SITE, 2.0, 100.0, 0.0, None, None, "test"),
    )
    cur.execute(
        ON_HAND_SQL,
        ("id-oh-897-154", ITEM, FROM_SITE, float(DONOR_ON_HAND), SYNC_AS_OF, "test"),
    )
    cur.execute(
        ON_HAND_SQL,
        ("id-oh-897-159", ITEM, TO_SITE, 0.0, SYNC_AS_OF, "test"),
    )
    cur.execute(
        OPEN_LINE_SQL,
        (
            "id-ol-897",
            "po:PO-897-1:1",
            ITEM,
            FROM_SITE,
            "SUP-897",
            "po",
            float(DONOR_OPEN),
            "2026-11-15",
            "2026-09-20",
            SYNC_AS_OF,
            "test",
        ),
    )


def _insert_transfer(
    cur,
    reference_nbr: str | None,
    outcome: str,
    erp_status: str | None,
    lines: list | str,
) -> None:
    """One transfer_releases row of item A from 154 to 159; ``lines``
    a line snapshot list or a hostile value."""
    line_count = len(lines) if isinstance(lines, list) else 1
    cur.execute(
        TRANSFER_SQL,
        (
            TRANSFER_ORIGIN,
            FROM_SITE,
            TO_SITE,
            reference_nbr,
            outcome,
            line_count,
            json.dumps(lines),
            "test",
            erp_status,
        ),
    )


def _recommendation_rows(client) -> dict[tuple[str, str], dict]:
    """Every row of GET /api/recommendations (as-of 2026-09-30), keyed
    by (item_code, site_code)."""
    response = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert response.status_code == 200, response.text
    return {(row["item_code"], row["site_code"]): row for row in response.json()}


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


def _transfer_suggestions(client) -> list[dict]:
    """The suggestions of GET /api/supply-plan/transfers."""
    response = client.get("/api/supply-plan/transfers")
    assert response.status_code == 200, response.text
    return response.json()["suggestions"]


def test_issue897_freeform(client) -> None:
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # --- baseline: no transfer releases, no in-flight units ---------------
    rows = _recommendation_rows(client)
    donor = rows[(ITEM, FROM_SITE)]
    receiver = rows[(ITEM, TO_SITE)]
    assert donor["status"] == "ok", donor
    assert donor["reorder_point_units"] == DONOR_ROP, donor
    assert donor["on_order_units"] == DONOR_BASELINE_ON_ORDER, donor
    assert receiver["status"] == "ok", receiver
    assert receiver["reorder_point_units"] == RECEIVER_ROP, receiver
    assert receiver["on_order_units"] == 0, receiver
    # A/159 is short by exactly 100, and the plan and the transfer
    # suggestions both act on it while nothing is in flight.
    assert receiver["suggested_order_quantity"] == RECEIVER_BASELINE_SUGGESTED, receiver
    assert (ITEM, TO_SITE) in _plan_pairs(client)
    # And the transfer suggestions act on the same need: move 100
    # from the donor to the receiver.
    suggestions = _transfer_suggestions(client)
    assert [
        (
            suggestion["item_code"],
            suggestion["from_site"],
            suggestion["to_site"],
            suggestion["quantity"],
        )
        for suggestion in suggestions
    ] == [(ITEM, FROM_SITE, TO_SITE, RECEIVER_ROP)], suggestions
    assert (
        suggestions[0]["to_site_need"],
        suggestions[0]["from_site_surplus"],
    ) == (RECEIVER_ROP, DONOR_SURPLUS), suggestions

    # --- in flight: a "created" row, reference set, "On Hold" -------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _insert_transfer(cur, REFERENCE_NBR, "created", "On Hold", [LINE_120])
        conn.commit()
    finally:
        conn.close()

    rows = _recommendation_rows(client)
    # The 120 units raise the receiver's on-order by exactly 120 and
    # lower the donor's by exactly 120 (its open 50 go to -70).
    assert rows[(ITEM, TO_SITE)]["on_order_units"] == 120, rows
    assert rows[(ITEM, FROM_SITE)]["on_order_units"] == (
        DONOR_BASELINE_ON_ORDER - TRANSFER_UNITS
    ), rows
    # Everything follows: the receiver's suggested quantity is gone.
    assert rows[(ITEM, TO_SITE)]["suggested_order_quantity"] == 0, rows
    # The single-key path counts the same in-flight transfer units.
    single = recommendation_for_key(ITEM, TO_SITE, as_of=AS_OF)
    assert single["on_order_units"] == TRANSFER_UNITS
    assert single["suggested_order_quantity"] == 0

    # --- status unknown: a null erp_status counts the same ----------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transfer_releases")
            _insert_transfer(cur, REFERENCE_NBR, "created", None, [LINE_120])
        conn.commit()
    finally:
        conn.close()

    rows = _recommendation_rows(client)
    assert rows[(ITEM, TO_SITE)]["on_order_units"] == 120, rows
    assert rows[(ITEM, FROM_SITE)]["on_order_units"] == (
        DONOR_BASELINE_ON_ORDER - TRANSFER_UNITS
    ), rows

    # --- released / completed / canceled: the row stops counting ----------
    for status in ("Released", "Completed", "Canceled"):
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM transfer_releases")
                _insert_transfer(cur, REFERENCE_NBR, "created", status, [LINE_120])
            conn.commit()
        finally:
            conn.close()

        rows = _recommendation_rows(client)
        assert rows[(ITEM, TO_SITE)]["on_order_units"] == 0, (status, rows)
        assert rows[(ITEM, FROM_SITE)]["on_order_units"] == DONOR_BASELINE_ON_ORDER, (
            status,
            rows,
        )

    # --- not in flight: other outcomes, or a null reference_nbr ------------
    for outcome, reference in (
        ("error", REFERENCE_NBR),
        ("pending", REFERENCE_NBR),
        ("aborted", REFERENCE_NBR),
        ("created", None),
    ):
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM transfer_releases")
                _insert_transfer(cur, reference, outcome, "On Hold", [LINE_120])
            conn.commit()
        finally:
            conn.close()

        rows = _recommendation_rows(client)
        assert rows[(ITEM, TO_SITE)]["on_order_units"] == 0, (outcome, reference, rows)
        assert rows[(ITEM, FROM_SITE)]["on_order_units"] == DONOR_BASELINE_ON_ORDER, (
            outcome,
            reference,
            rows,
        )

    # --- the plan follows ---------------------------------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transfer_releases")
            _insert_transfer(cur, REFERENCE_NBR, "created", "On Hold", [LINE_120])
        conn.commit()
    finally:
        conn.close()

    # A/159 was short by 100; the in-flight 120 lift its position 20
    # units above its reorder point, which its 2.0 a day burn in 10
    # days — past the 7-day horizon — so the plan no longer lists it,
    # and it no longer needs a transfer from 154.
    assert (ITEM, TO_SITE) not in _plan_pairs(client), _plan_pairs(client)
    suggestions = _transfer_suggestions(client)
    assert not [
        suggestion
        for suggestion in suggestions
        if (
            suggestion["item_code"],
            suggestion["from_site"],
            suggestion["to_site"],
        )
        == (ITEM, FROM_SITE, TO_SITE)
    ], suggestions

    # --- hostile rows: malformed lines, next to a valid row -----------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM transfer_releases")
            _insert_transfer(cur, REFERENCE_NBR, "created", "On Hold", "not-a-list")
            _insert_transfer(
                cur,
                REFERENCE_NBR,
                "created",
                "On Hold",
                [{"item_code": ITEM, "quantity": "twelve"}],
            )
            _insert_transfer(
                cur,
                REFERENCE_NBR,
                "created",
                "On Hold",
                [{"item_code": ITEM, "quantity": 10**400}],
            )
            _insert_transfer(cur, REFERENCE_NBR, "created", "On Hold", [LINE_120])
        conn.commit()
    finally:
        conn.close()

    response = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert response.status_code == 200, response.text
    rows = {(row["item_code"], row["site_code"]): row for row in response.json()}
    # The hostile rows (lines not a list, a non-numeric quantity, a
    # quantity too large to convert to a float) contribute nothing;
    # the valid row's 120 still counts.
    assert rows[(ITEM, TO_SITE)]["on_order_units"] == 120, rows
    assert rows[(ITEM, FROM_SITE)]["on_order_units"] == (
        DONOR_BASELINE_ON_ORDER - TRANSFER_UNITS
    ), rows
