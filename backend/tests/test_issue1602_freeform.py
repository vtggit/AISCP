"""Issue #1602 proof: one supplier terms picker, the zones take the
caller's minimum order quantity.

AC-1 — the one picker: ``app.supply.terms.pick_terms_row`` is the
``_pick_terms_row`` of ``app.api.supply_plan`` moved, body unchanged —
among the rows whose ``source_system`` equals the given one, the row
whose ``is_default`` is true, else the row with the lowest
``supplier_code``, and None when there is no such row.  The order
plan's answers are unchanged: for the seed of
``tests/test_issue1540_freeform.py``, ``GET /api/supply-plan``
answers the same as on main (proven below).

AC-2 — the zones take the minimum: ``buffer_zones`` gains, after
``settings``, the keyword parameter ``moq``: a finite number above 0
is the minimum order quantity, and None, a bool, 0, a negative number
or a value that is not a finite number stands for 0.  The green
zone's candidates and the ``arithmetic`` string show it where they
showed the (removed) module constant ``MOQ``, formatted the same way
(``:g``).  Called without ``moq`` the zones are exactly what
``buffer_zones`` returns today.

AC-3 — the buffer rows pass it: ``_ddmrp_stat_context`` reads the
stored supplier terms once per call and keeps them grouped by
``item_code`` under the key ``terms_by_item``; ``ddmrp_zone_rows``
passes ``buffer_zones`` the ``min_order_qty`` of the item's picked
terms row — ``pick_terms_row`` over the item's rows and the
``source_system`` of the item-site's row of the context's
``item_site_by_key`` — None with no item-site row, no terms row or a
null ``min_order_qty``.  Every zone row gains the field ``moq``: the
value the zones used, as a float (0.0 when none applied), null for a
row whose zones are not computed.  Every other field, the statuses,
the order of the rows and of the reads is unchanged.

Proven:

  * The arithmetic: ``buffer_zones(adu=2.0, dlt_days=10.0,
    order_cycle_days=20.0, demand_class="smooth", cv=0.2,
    settings={}, moq=500.0)`` has ``green`` 500.0 and its
    ``arithmetic`` contains ``max(2 * 20, 16, 500)``; the same call
    without ``moq``, and with ``moq`` each of None, 0, -5,
    ``float("nan")`` and ``True``, has ``green`` 40.0, exactly as
    ``buffer_zones`` returns today.
  * The picker: the default row over a lower supplier code, the
    lowest supplier code when none is default, the rows of another
    source system ignored, None for no rows.
  * The rows: the computable-buffer seed of
    ``tests/test_issue740_freeform.py`` plus a supplier terms row for
    its item and source system with a ``min_order_qty`` above the
    buffer's green zone, and a second, non-default row of a lower
    supplier code with a smaller minimum: through
    ``GET /api/ddmrp/zones`` the row's ``moq`` equals the default
    row's minimum, its ``green`` equals it, and its ``top_of_green``
    is the ceiling of red + yellow + that minimum; with the terms
    row's ``min_order_qty`` null, the row's ``moq`` is 0.0 and its
    zones equal those of the same seed with no terms row.
  * The order plan: ``GET /api/supply-plan`` answers the same for the
    seed of ``tests/test_issue1540_freeform.py`` as it does on main.
  * The plan route: ``GET /api/ddmrp/plan`` is built from the very
    same zone rows — the zone fields, the zones route's new ``moq``
    among them, pass through with the rest — and its declared answer
    shape is unchanged by the issue: its row model and its schema in
    ``backend/openapi.json`` carry no ``moq`` field of their own.
"""

import json
import math
from datetime import date, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

import app.api.supply_plan as supply_plan
from app.db.connection import get_connection
from app.ddmrp.zones import buffer_zones
from app.main import app
from app.supply.terms import pick_terms_row

ZONES_URL = "/api/ddmrp/zones"
BUFFERS_URL = "/api/ddmrp/buffers"
PLAN_URL = "/api/supply-plan"
DDMRP_PLAN_URL = "/api/ddmrp/plan"

# The zones row: the computable buffer of test 740 (ADU 2.0, DLT 10
# days, "smooth", the recommendation's order cycle), with the
# item-site's source system.
SITE = "SITE-1602-1"
ITEM = "ITEM-1602-A"
SOURCE_SYSTEM = "test"

# The item's two terms rows of the source system: the default row
# (the HIGHER supplier code) with the minimum above the buffer's
# green zone, and the non-default row of the LOWER supplier code with
# the smaller minimum the picker must ignore.
SUPPLIER_DEFAULT = "S-1602-B"
SUPPLIER_LOWER = "S-1602-A"
MOQ_DEFAULT = 600.0
MOQ_LOWER = 100.0

# The order plan: the seed of test 1540, renamed.
PLAN_SITE = "1602"
PLAN_SOURCE = "test"
ITEM_B1 = "B-1602-1"
ITEM_B2 = "B-1602-2"
ITEM_R1 = "R-1602-1"
PLAN_ITEMS = (ITEM_B1, ITEM_B2, ITEM_R1)
PLAN_SUPPLIER = "S-1602-PLAN"
PLAN_VENDOR = "V-1602"
PLAN_NAME = "Buffer Stock Co."

# The buffer rows of the DDMRP plan the stand-in answers: B1's signal
# on, B2's off.
BUFFER_B1 = {
    "item_code": ITEM_B1,
    "site_code": PLAN_SITE,
    "status": "ok",
    "order_signal": True,
    "net_flow": 12.6,
    "top_of_red": 10,
    "top_of_yellow": 30,
    "top_of_green": 49,
    "suggested_order_units": 37,
}
BUFFER_B2 = {
    "item_code": ITEM_B2,
    "site_code": PLAN_SITE,
    "status": "ok",
    "order_signal": False,
    "net_flow": 35.0,
    "top_of_red": 10,
    "top_of_yellow": 30,
    "top_of_green": 49,
    "suggested_order_units": 0,
}
ON_BUFFER_ROWS = {
    (ITEM_B1, PLAN_SITE): BUFFER_B1,
    (ITEM_B2, PLAN_SITE): BUFFER_B2,
}

# B1's line: the raw 37 fitted to the lot of 10, the position the
# net flow 12.6 rounded down, the reorder point the top of yellow.
B1_ARITHMETIC = (
    "raw 37 (buffer signal: top of green 49 minus net flow 12.6, "
    "rounded up) → rounded to lot 10: 40"
)
R1_ARITHMETIC = "raw 25 (EOQ) → rounded to lot 10: 30"

ITEM_SITE_SQL = (
    "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
    "reorder_point, safety_stock, master_lead_time_days, "
    "source_system) VALUES (%s, %s, %s, 1.0, 25.0, 10.0, %s, %s)"
)
PLAN_ITEM_SITE_SQL = (
    "INSERT INTO item_sites (id, item_code, site_code, unit_cost, "
    "reorder_point, safety_stock, max_qty, order_qty, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
DEMAND_SQL = (
    "INSERT INTO demand_events (id, business_key, item_code, site_code, "
    "quantity, occurred_on, event_type, source_system) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
LEAD_TIME_SQL = (
    "INSERT INTO lead_time_observations (id, business_key, "
    "supplier_code, item_code, ordered_on, received_on, "
    "source_system) VALUES (%s, %s, %s, %s, %s, %s, 'test')"
)
TERMS_SQL = (
    "INSERT INTO supplier_terms (id, business_key, source_system, "
    "item_code, supplier_code, min_order_qty, max_order_qty, lot_size, "
    "lead_time_days, is_default) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)
SUPPLIER_SQL = (
    "INSERT INTO suppliers (id, supplier_code, vendor_code, name, status, "
    "source_system) VALUES (%s, %s, %s, %s, %s, %s)"
)


def _recommendation(item_code):
    """One status "ok" recommendation: on hand 5, on order 0,
    reorder point 20, order quantity 25 and daily demand 2 — the
    position below the reorder point makes the item-site due now."""
    return {
        "item_code": item_code,
        "site_code": PLAN_SITE,
        "status": "ok",
        "on_hand_units": 5,
        "on_order_units": 0,
        "reorder_point_units": 20,
        "order_quantity": {"basis": "eoq", "order_quantity_units": 25},
        "suggested_order_quantity": None,
        "inputs": {"d": 2.0, "lt": 5.0},
        "unit_cost": 10.0,
    }


class _Recommendations:
    """The stand-in for ``recommendations_for_all`` in
    ``app.api.supply_plan``: every call is counted, the three fixed
    recommendations are handed back unchanged."""

    calls = 0

    def __call__(self, as_of):
        _Recommendations.calls += 1
        return [_recommendation(item_code) for item_code in PLAN_ITEMS]


class _Buffers:
    """The stand-in for ``_buffer_rows`` in
    ``app.api.supply_plan``: every call is counted, the fixed buffer
    rows are handed back unchanged."""

    calls = 0

    def __call__(self, as_of):
        _Buffers.calls += 1
        return ON_BUFFER_ROWS


def _plan_seed(cur) -> None:
    """The three item-sites of the source system, the one supplier
    and its terms row per item — one supplier with a lot size of 10
    covering all three (the seed of test 1540)."""
    for item_code in PLAN_ITEMS:
        cur.execute(
            PLAN_ITEM_SITE_SQL,
            (
                f"id-is-1602-{item_code}",
                item_code,
                PLAN_SITE,
                10.0,
                20.0,
                0.0,
                None,
                None,
                PLAN_SOURCE,
            ),
        )
        cur.execute(
            TERMS_SQL,
            (
                f"id-st-1602-{item_code}",
                f"{item_code}:terms-1602",
                PLAN_SOURCE,
                item_code,
                PLAN_SUPPLIER,
                None,
                None,
                10,
                None,
                True,
            ),
        )
    cur.execute(
        SUPPLIER_SQL,
        (
            "id-sp-1602",
            PLAN_SUPPLIER,
            PLAN_VENDOR,
            PLAN_NAME,
            "active",
            PLAN_SOURCE,
        ),
    )


def _lines_by_item(body):
    """The plan's lines keyed by item_code, across every group."""
    return {
        line["item_code"]: line
        for group in body["suppliers"]
        for line in group["lines"]
    }


def test_issue1602_freeform(client, admin_token, monkeypatch):
    # The recommendation inputs of the zones' row: the registry
    # defaults, never ambient environment values.
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    admin = TestClient(app, headers={"Authorization": f"Bearer {admin_token}"})

    # --- AC-2: the zones take the minimum -------------------------------
    zone = buffer_zones(
        adu=2.0,
        dlt_days=10.0,
        order_cycle_days=20.0,
        demand_class="smooth",
        cv=0.2,
        settings={},
        moq=500.0,
    )
    assert zone["green"] == 500.0
    assert "max(2 * 20, 16, 500)" in zone["arithmetic"]

    # Without a moq, and with a moq that stands for 0, the zones are
    # exactly what buffer_zones returns today: green 40.0.
    baseline = buffer_zones(
        adu=2.0,
        dlt_days=10.0,
        order_cycle_days=20.0,
        demand_class="smooth",
        cv=0.2,
        settings={},
    )
    assert baseline["green"] == 40.0
    for moq in (None, 0, -5, float("nan"), True):
        zone = buffer_zones(
            adu=2.0,
            dlt_days=10.0,
            order_cycle_days=20.0,
            demand_class="smooth",
            cv=0.2,
            settings={},
            moq=moq,
        )
        assert zone["green"] == 40.0
        assert zone == baseline, "a moq that stands for 0 changes nothing"

    # --- AC-1: the one picker --------------------------------------------
    picker_rows = [
        {
            "source_system": SOURCE_SYSTEM,
            "supplier_code": SUPPLIER_DEFAULT,
            "is_default": True,
            "min_order_qty": MOQ_DEFAULT,
        },
        {
            "source_system": SOURCE_SYSTEM,
            "supplier_code": SUPPLIER_LOWER,
            "is_default": False,
            "min_order_qty": MOQ_LOWER,
        },
        {
            "source_system": "other",
            "supplier_code": "S-1602-0",
            "is_default": True,
            "min_order_qty": 1.0,
        },
    ]
    # The default row over a lower supplier code...
    assert pick_terms_row(picker_rows, SOURCE_SYSTEM) is picker_rows[0]
    # ...the lowest supplier code when none is default...
    assert pick_terms_row(picker_rows[1:], SOURCE_SYSTEM) is picker_rows[1]
    # ...the rows of another source system ignored...
    assert pick_terms_row(picker_rows, "other") is picker_rows[2]
    # ...and None for no rows.
    assert pick_terms_row(picker_rows, "none") is None
    assert pick_terms_row([], SOURCE_SYSTEM) is None

    # --- AC-3: the rows ----------------------------------------------------
    # The seed of test 740's computable buffer: 2.0 a day for 360
    # days ending on the as-of date (ADU 2.0, "smooth", cv 0) and the
    # three 8/10/12-day observations received 30 days before it
    # (pooled DLT 10, "observed").
    as_of = date.today() - timedelta(days=1)
    daily = {as_of - timedelta(days=offset): 2.0 for offset in range(360)}
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                ITEM_SITE_SQL,
                (f"id-is-1602-{ITEM}", ITEM, SITE, None, SOURCE_SYSTEM),
            )
            cur.executemany(
                DEMAND_SQL,
                [
                    (
                        f"id-de-1602-{day.isoformat()}",
                        f"de-1602-{day.isoformat()}",
                        ITEM,
                        SITE,
                        quantity,
                        day.isoformat(),
                        "shipment",
                        SOURCE_SYSTEM,
                    )
                    for day, quantity in sorted(daily.items(), reverse=True)
                ],
            )
            for i, lead in enumerate((8, 10, 12)):
                cur.execute(
                    LEAD_TIME_SQL,
                    (
                        f"id-lt-1602-{i}",
                        f"lt-1602-{i}",
                        f"SUP-1602-{ITEM}",
                        ITEM,
                        (as_of - timedelta(days=30 + lead)).isoformat(),
                        (as_of - timedelta(days=30)).isoformat(),
                    ),
                )
        conn.commit()
    finally:
        conn.close()

    resp = admin.put(
        BUFFERS_URL, json={"item_code": ITEM, "site_code": SITE, "note": "buffer"}
    )
    assert resp.status_code == 200, resp.text

    def _zone_row():
        resp = client.get(ZONES_URL)
        assert resp.status_code == 200, resp.text
        rows = resp.json()["rows"]
        assert [(row["item_code"], row["site_code"]) for row in rows] == [(ITEM, SITE)]
        return rows[0]

    # Baseline: the seed with no terms row — the row's moq is 0.0
    # (none applies) and the zones are the buffer_zones values.
    row = _zone_row()
    assert row["status"] == "ok"
    assert row["adu"] == 2.0
    assert row["dlt_days"] == 10.0
    assert row["dlt_source"] == "observed"
    assert row["red_base"] == 16.0
    assert row["red"] == 20.0
    assert row["yellow"] == 20.0
    assert row["green"] == 541.0
    assert row["top_of_green"] == 581
    assert row["moq"] == 0.0
    baseline_row = row

    # The terms rows of the item and source system: the default row
    # (the higher supplier code) with the minimum above the buffer's
    # green zone, and the non-default row of the LOWER supplier code
    # with a smaller minimum.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                TERMS_SQL,
                (
                    f"id-st-1602-{SUPPLIER_DEFAULT}",
                    f"{ITEM}:terms-1602-default",
                    SOURCE_SYSTEM,
                    ITEM,
                    SUPPLIER_DEFAULT,
                    MOQ_DEFAULT,
                    None,
                    None,
                    None,
                    True,
                ),
            )
            cur.execute(
                TERMS_SQL,
                (
                    f"id-st-1602-{SUPPLIER_LOWER}",
                    f"{ITEM}:terms-1602-lower",
                    SOURCE_SYSTEM,
                    ITEM,
                    SUPPLIER_LOWER,
                    MOQ_LOWER,
                    None,
                    None,
                    None,
                    False,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    # The picker takes the default row over the lower supplier code:
    # the row's moq is the default row's minimum, the green zone
    # takes it, and the top of green is the ceiling of red + yellow
    # + that minimum.
    row = _zone_row()
    assert row["status"] == "ok"
    assert row["moq"] == MOQ_DEFAULT
    assert row["green"] == MOQ_DEFAULT
    assert row["top_of_green"] == math.ceil(row["red"] + row["yellow"] + row["moq"])
    assert row["top_of_green"] == 640
    green_part = (
        "green = max(adu * order_cycle_days, red_base, moq) "
        f"= max({row['adu']:g} * {row['order_cycle_days']:g}, "
        f"{row['red_base']:g}, {row['moq']:g}) = {row['green']:g}"
    )
    assert green_part in row["arithmetic"]

    # With the terms row's min_order_qty null: the row's moq is 0.0
    # and its zones are exactly those of the same seed with no terms
    # row.
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE supplier_terms SET min_order_qty = NULL "
                "WHERE item_code = %s AND supplier_code = %s",
                (ITEM, SUPPLIER_DEFAULT),
            )
        conn.commit()
    finally:
        conn.close()

    row = _zone_row()
    assert row["moq"] == 0.0
    assert row == baseline_row

    # --- The plan route: the answer shape is unchanged ---------------------
    # The plan route is built from the very same shared zone rows, so
    # the zone fields — the zones route's new ``moq`` included — pass
    # through with the rest.  What the issue must not change is the
    # plan route's DECLARED answer shape: its row model and its schema
    # carry no ``moq`` field of their own.
    response = client.get(DDMRP_PLAN_URL)
    assert response.status_code == 200, response.text
    plan_rows = response.json()["rows"]
    assert [(row["item_code"], row["site_code"]) for row in plan_rows] == [(ITEM, SITE)]
    plan_row = plan_rows[0]
    zone_row = _zone_row()
    # The plan row is the zone row plus the plan's own fields — every
    # zone field, ``moq`` among them, passes through unchanged.
    assert set(plan_row) == set(zone_row) | {
        "on_hand",
        "open_supply",
        "qualified_demand",
        "net_flow",
        "planning_zone",
        "order_signal",
        "suggested_order_units",
        "planning_priority_pct",
        "on_hand_status",
        "on_hand_pct_of_red",
        "notes",
    }, "the plan row is the zone row plus the plan's own fields"
    for key, value in zone_row.items():
        assert plan_row[key] == value, key
    # The feed carries no open sales-order lines, so the two part
    # fields stay out of the row, as before the feed existed.
    assert "qualified_due" not in plan_row
    assert "qualified_spike" not in plan_row

    # The plan route's declared answer shape is unchanged: its row
    # schema in the schema file carries no ``moq`` field of its own
    # (nor any room for undeclared fields), while the zones row
    # schema does.
    openapi = json.loads(
        (Path(__file__).resolve().parents[1] / "openapi.json").read_text(
            encoding="utf-8"
        )
    )
    schemas = openapi["components"]["schemas"]
    assert "moq" in schemas["DdmrpZoneRow"]["properties"]
    assert "moq" in schemas["DdmrpZoneRow"]["required"]
    plan_ref = openapi["paths"][DDMRP_PLAN_URL]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]["$ref"]
    plan_model = schemas[plan_ref.rsplit("/", 1)[-1]]
    plan_row_schema = schemas[
        plan_model["properties"]["rows"]["items"]["$ref"].rsplit("/", 1)[-1]
    ]
    assert "moq" not in plan_row_schema["properties"]
    assert "moq" not in plan_row_schema["required"]
    assert "additionalProperties" not in plan_row_schema

    # --- The order plan: the seed of test 1540 -----------------------------
    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _plan_seed(cur)
        conn.commit()
    finally:
        conn.close()

    monkeypatch.setattr(supply_plan, "recommendations_for_all", _Recommendations())
    monkeypatch.setattr(supply_plan, "_buffer_rows", _Buffers())
    _Recommendations.calls = 0
    _Buffers.calls = 0

    response = client.get(PLAN_URL, params={"horizon_days": 7})
    assert response.status_code == 200, response.text
    on_body = response.json()
    lines = _lines_by_item(on_body)
    assert set(lines) == {
        ITEM_B1,
        ITEM_R1,
    }, "B2 has no line, whatever its reorder point says"

    b1 = lines[ITEM_B1]
    assert b1["source"] == "buffer"
    assert b1["due_in_days"] == 0
    assert b1["order_by"] == on_body["as_of"]
    assert b1["raw_quantity"] == 37
    assert b1["quantity"] == 40
    assert b1["position_units"] == 12
    assert b1["reorder_point_units"] == 30
    assert b1["arithmetic"] == B1_ARITHMETIC
    # The daily demand, supplier choice, terms, lead time,
    # arrival, unit cost and value are those of any line.
    assert b1["daily_demand"] == 2.0
    assert b1["terms"] == {
        "min_order_qty": None,
        "lot_size": 10.0,
        "max_order_qty": None,
    }
    assert b1["unit_cost"] == 10.0
    assert b1["value"] == "400.00"
    assert b1["lead_time_days"] == 5.0

    r1 = lines[ITEM_R1]
    assert r1["source"] == "reorder_point"
    assert r1["raw_quantity"] == 25
    assert r1["quantity"] == 30
    assert r1["position_units"] == 5
    assert r1["reorder_point_units"] == 20
    assert r1["due_in_days"] == 0
    assert r1["arithmetic"] == R1_ARITHMETIC

    # The supplier group's lines carry the source too, under the one
    # supplier with the lot of 10.
    group = next(g for g in on_body["suppliers"] if g["supplier_code"] == PLAN_SUPPLIER)
    assert group["vendor_code"] == PLAN_VENDOR
    assert {line["item_code"] for line in group["lines"]} == {ITEM_B1, ITEM_R1}
    assert {line["item_code"]: line["source"] for line in group["lines"]} == {
        ITEM_B1: "buffer",
        ITEM_R1: "reorder_point",
    }

    assert _Recommendations.calls == 1
    assert _Buffers.calls == 1, "the DDMRP plan is read once per call"
