"""Issue #645 proof: the recommended order quantity and max.

AC-1 — ``app.recommend.order_quantity`` is a new module with the
constants ``DEFAULT_ORDER_COST`` 50.0, ``DEFAULT_HOLDING_RATE`` 0.25,
``MIN_COVER_DAYS`` 7, ``MAX_COVER_DAYS`` 365 and ``ASSUMED_COVER_DAYS``
30, the settings readers ``order_cost_from_env`` (
``float(AIERP_ORDER_COST)`` when it parses to a finite number > 0,
else the default) and ``holding_rate_from_env`` (
``float(AIERP_HOLDING_RATE)`` when it parses to a finite number with
0 < x <= 1, else the default), and the pure function
``recommend_order_quantity(d, unit_cost, order_cost, holding_rate)``:
None when ``d`` is not a finite number > 0, else the economic order
quantity ``sqrt(2 * 365 * d * order_cost / (holding_rate *
unit_cost))`` (basis ``"eoq"``) when ``unit_cost`` is a finite number
> 0, else ``30 * d`` (basis ``"assumed_30_days"``), clamped to
``[7 * d, 365 * d]`` (``clamped`` ``"min_cover"``/``"max_cover"``/null)
and exactly the keys ``basis``, ``clamped``, ``order_quantity`` (the
exact float after clamping), ``order_quantity_units`` (``max(1,
ceil(order_quantity))``), ``cover_days`` (``order_quantity_units /
d``), ``annual_demand`` (``365 * d``), ``order_cost``,
``holding_rate``, ``unit_cost`` (as given, null when absent) and a
one-line ``arithmetic`` string with the values substituted.

``app.recommend.reorder._finish`` adds one new key,
``order_quantity``, without touching any existing key: for a
recommendable item-site (``reorder_point_units`` not None) it is the
``recommend_order_quantity`` result for the recommendation's
``inputs["d"]``, its ``unit_cost``, the call's
``order_cost_from_env()`` and ``holding_rate_from_env()`` (read once
per ``recommendations_for_all`` call and once per single-key call,
never once per item-site), extended with
``recommended_max_units`` (``reorder_point_units`` plus
``order_quantity_units``) and the item-site's current ``order_qty``/
``max_qty`` as ``current_order_qty``/``current_max_qty`` (null when
absent); null otherwise, including when ``recommend_order_quantity``
returns None for a zero or non-finite ``d``.

AC-2 — the method as a pure function:
``recommend_order_quantity(2.0, 10.0, 50.0, 0.25)`` has basis ``"eoq"``,
``order_quantity`` sqrt(2*730*50/(0.25*10)) ≈ 170.88,
``order_quantity_units`` 171, ``clamped`` null and ``cover_days`` 85.5;
with ``unit_cost`` None it is ``"assumed_30_days"`` with 60 units; with
``unit_cost`` 100000 it is clamped ``"min_cover"`` to 14 units; with
``unit_cost`` 0.0001 it is clamped ``"max_cover"`` to 730 units; with
``d`` 0 it is None; the key set is exactly the stated one. The
settings readers: ``AIERP_ORDER_COST`` "80" gives 80.0 and "abc",
"-5", "0" and "inf" give 50.0; ``AIERP_HOLDING_RATE`` "0.3" gives 0.3
and "1.5", "0" and "x" give 0.25. Through the API (planner token, the
ranking inputs monkeypatched the way test_issue453_freeform does):
GET /api/recommendations/by-key for a recommendable item-site returns
an ``order_quantity`` with ``recommended_max_units`` =
``reorder_point_units + order_quantity_units`` plus that site's
``current_order_qty`` and ``current_max_qty``; a non-recommendable
item-site has ``order_quantity`` null; ``suggested_order_quantity``
and ``current`` are unchanged. ``app.api.recommendations`` gains the
``OrderQuantity`` model and the optional ``order_quantity`` field,
and backend/openapi.json is regenerated with exactly that model and
field added.
"""

import json
import math
from datetime import date, timedelta
from pathlib import Path

import pytest

from app.api.recommendations import OrderQuantity, RecommendationResponse
from app.db.connection import get_connection
from app.recommend.order_quantity import (
    ASSUMED_COVER_DAYS,
    DEFAULT_HOLDING_RATE,
    DEFAULT_ORDER_COST,
    MAX_COVER_DAYS,
    MIN_COVER_DAYS,
    holding_rate_from_env,
    order_cost_from_env,
    recommend_order_quantity,
)
from app.recommend.reorder import (
    METHOD,
    _finish,
    recommendation_for_key,
    recommendations_for_all,
)

AS_OF = date(2026, 9, 30)
AS_OF_STR = AS_OF.isoformat()
FIRST_DAY = AS_OF - timedelta(days=359)  # 2025-10-03
FIXED_TODAY = date(2026, 10, 2)
S1 = "S1"
A1, A2, A3, A4 = "I645-1", "I645-2", "I645-3", "I645-4"

SYNC_AS_OF = "2026-09-30T00:00:00+00:00"

OPENAPI = Path(__file__).resolve().parents[1] / "openapi.json"

# The pure-function result's exact key set.
QTY_KEYS = {
    "basis",
    "clamped",
    "order_quantity",
    "order_quantity_units",
    "cover_days",
    "annual_demand",
    "order_cost",
    "holding_rate",
    "unit_cost",
    "arithmetic",
}
# The recommendation's order_quantity object: the method's keys plus
# the three attached fields.
OBJECT_KEYS = QTY_KEYS | {
    "recommended_max_units",
    "current_order_qty",
    "current_max_qty",
}

# Hand-computed EOQ values under the default settings (50.0 / 0.25).
EOQ_A1 = math.sqrt(
    2 * MAX_COVER_DAYS * 10.0 * DEFAULT_ORDER_COST / (DEFAULT_HOLDING_RATE * 2.0)
)
UNITS_A1 = max(1, math.ceil(EOQ_A1))
EOQ_A2_RAW = math.sqrt(
    2 * MAX_COVER_DAYS * 2.0 * DEFAULT_ORDER_COST / (DEFAULT_HOLDING_RATE * 100000.0)
)

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


def _eoq_arithmetic(
    d: float,
    unit_cost: float,
    order_cost: float,
    holding_rate: float,
    raw: float | None = None,
) -> str:
    """The eoq arithmetic string with the module's exact formatting."""
    value = (
        raw
        if raw is not None
        else math.sqrt(2 * MAX_COVER_DAYS * d * order_cost / (holding_rate * unit_cost))
    )
    return (
        "order_quantity = sqrt(2 * 365 * d * order_cost / "
        "(holding_rate * unit_cost)) "
        f"= sqrt(2 * 365 * {d:g} * {order_cost:g} / "
        f"({holding_rate:g} * {unit_cost:g})) = {value:.2f}"
    )


def _assumed_arithmetic(d: float) -> str:
    """The assumed-30-days arithmetic string, exact formatting."""
    return f"order_quantity = 30 * d = {ASSUMED_COVER_DAYS:g} * {d:g} = {ASSUMED_COVER_DAYS * d:.2f}"


def _seed(cur) -> None:
    """The fixed-date rows: demand, lead times, item-sites, position.

    A1: 10.0 a day for 360 days (d 10, sd_d 0), LT 10 -> ROP 100.0
    (100 units); unit cost 2.0, order_qty 50, max_qty 400, 30 on hand
    and 25 open -> suggested 45; EOQ sqrt(730000) ~ 854.40 (855 units,
    85.5 cover days) -> recommended max 955.
    A2: 2.0 a day, LT 5 -> ROP 10.0 (10 units); unit cost 100000.0 ->
    EOQ ~ 1.71, clamped min_cover to 14 units -> recommended max 24;
    no order_qty/max_qty in the item_sites row, no position ->
    suggested 10.
    A3: intermittent (two events, ADI 6) -> not_recommendable_demand
    -> order_quantity null, suggested null.
    A4: 2.0 a day, LT 5 -> ROP 10.0 (10 units); unit cost NULL ->
    assumed_30_days, 60 units -> recommended max 70; order_qty 30,
    max_qty NULL, no position -> suggested 10.
    """
    for item, quantity, first in (
        (A1, 10.0, FIRST_DAY),
        (A2, 2.0, FIRST_DAY),
        (A4, 2.0, FIRST_DAY),
    ):
        cur.executemany(
            DEMAND_SQL,
            [
                (
                    f"id-de-645-{item[-1]}-{i}",
                    f"de-645-{item[-1]}-{i}",
                    item,
                    S1,
                    quantity,
                    (first + timedelta(days=i)).isoformat(),
                    "shipment",
                    "test",
                )
                for i in range(360)
            ],
        )
    # A3: two events, 60 units each — one in the oldest bucket and one
    # on the as-of: 2 non-zero of 12 buckets -> ADI 6 -> intermittent.
    cur.executemany(
        DEMAND_SQL,
        [
            (
                "id-de-645-3-1",
                "de-645-3-1",
                A3,
                S1,
                60.0,
                "2025-10-04",
                "shipment",
                "test",
            ),
            (
                "id-de-645-3-2",
                "de-645-3-2",
                A3,
                S1,
                60.0,
                AS_OF_STR,
                "shipment",
                "test",
            ),
        ],
    )
    for item, duration, first in (
        (A1, 10, "2026-08-20"),
        (A2, 5, "2026-08-25"),
        (A4, 5, "2026-08-26"),
    ):
        base = date.fromisoformat(first)
        for i in range(3):
            ordered = base + timedelta(days=i)
            cur.execute(
                LEAD_SQL,
                (
                    f"id-lt-645-{item[-1]}-{i}",
                    f"lt-645-{item[-1]}-{i}",
                    f"SUP-645-{item[-1]}",
                    item,
                    ordered.isoformat(),
                    (ordered + timedelta(days=duration)).isoformat(),
                    "test",
                ),
            )
    # item_sites: A2 has no order_qty/max_qty, A4 has no unit cost and
    # no current settings but an order_qty of 30.
    cur.executemany(
        ITEM_SITE_SQL,
        [
            (f"id-is-645-{item[-1]}", item, S1, cost, rp, ss, mx, oq, "test")
            for item, cost, rp, ss, mx, oq in (
                (A1, 2.0, 120.0, 5.0, 400.0, 50.0),
                (A2, 100000.0, 12.0, 1.0, None, None),
                (A3, 3.0, 9.0, 1.0, 50.0, 7.0),
                (A4, None, None, None, None, 30.0),
            )
        ],
    )
    # Position: A1 30 on hand / 25 open; A3 7 on hand / 12 open.
    cur.executemany(
        ON_HAND_SQL,
        [
            ("id-oh-645-1", A1, S1, 30.0, SYNC_AS_OF, "test"),
            ("id-oh-645-3", A3, S1, 7.0, SYNC_AS_OF, "test"),
        ],
    )
    cur.executemany(
        OPEN_LINE_SQL,
        [
            (
                "id-ol-645-1",
                "bk-645-1",
                A1,
                S1,
                "SUP-645-1",
                "po",
                25.0,
                "2026-10-05",
                "2026-09-20",
                SYNC_AS_OF,
                "test",
            ),
            (
                "id-ol-645-3",
                "bk-645-3",
                A3,
                S1,
                "SUP-645-3",
                "po",
                12.0,
                "2026-10-05",
                "2026-09-20",
                SYNC_AS_OF,
                "test",
            ),
        ],
    )


def _check_order_quantity(row: dict, key: str) -> None:
    """Every field of one recommendation row's order_quantity object."""
    quantity = row["order_quantity"]
    assert set(quantity) == OBJECT_KEYS, quantity
    assert quantity["order_cost"] == DEFAULT_ORDER_COST
    assert quantity["holding_rate"] == DEFAULT_HOLDING_RATE
    assert quantity["basis"] in ("eoq", "assumed_30_days")
    if key == A1:
        assert quantity["basis"] == "eoq"
        assert quantity["clamped"] is None
        assert quantity["order_quantity"] == pytest.approx(EOQ_A1)
        assert quantity["order_quantity_units"] == UNITS_A1 == 855
        assert quantity["cover_days"] == pytest.approx(85.5)
        assert quantity["annual_demand"] == pytest.approx(3650.0)
        assert quantity["unit_cost"] == pytest.approx(2.0)
        assert quantity["recommended_max_units"] == 100 + UNITS_A1 == 955
        assert quantity["current_order_qty"] == pytest.approx(50.0)
        assert quantity["current_max_qty"] == pytest.approx(400.0)
        assert quantity["arithmetic"] == _eoq_arithmetic(
            10.0, 2.0, DEFAULT_ORDER_COST, DEFAULT_HOLDING_RATE
        )
    elif key == A2:
        assert quantity["basis"] == "eoq"
        assert quantity["clamped"] == "min_cover"
        assert quantity["order_quantity"] == pytest.approx(14.0)
        assert quantity["order_quantity_units"] == 14
        assert quantity["cover_days"] == pytest.approx(7.0)
        assert quantity["annual_demand"] == pytest.approx(730.0)
        assert quantity["unit_cost"] == pytest.approx(100000.0)
        assert quantity["recommended_max_units"] == 10 + 14 == 24
        assert quantity["current_order_qty"] is None
        assert quantity["current_max_qty"] is None
        assert (
            quantity["arithmetic"]
            == _eoq_arithmetic(
                2.0, 100000.0, DEFAULT_ORDER_COST, DEFAULT_HOLDING_RATE, raw=EOQ_A2_RAW
            )
            + ", clamped to min_cover: 14.00"
        )
    elif key == A4:
        assert quantity["basis"] == "assumed_30_days"
        assert quantity["clamped"] is None
        assert quantity["order_quantity"] == pytest.approx(60.0)
        assert quantity["order_quantity_units"] == 60
        assert quantity["cover_days"] == pytest.approx(30.0)
        assert quantity["annual_demand"] == pytest.approx(730.0)
        assert quantity["unit_cost"] is None
        assert quantity["recommended_max_units"] == 10 + 60 == 70
        assert quantity["current_order_qty"] == pytest.approx(30.0)
        assert quantity["current_max_qty"] is None
        assert quantity["arithmetic"] == _assumed_arithmetic(2.0)
    else:
        raise AssertionError(key)


def _check_row(row: dict, key: str, as_of: date | str) -> None:
    """The recommendation row's pre-existing fields, unchanged."""
    assert row["item_code"] == key
    assert row["site_code"] == S1
    assert row["as_of"] == as_of
    assert row["method"] == METHOD
    assert row["demand_basis"] == "history"
    if key == A3:
        assert row["status"] == "not_recommendable_demand"
        assert row["demand_class"] == "intermittent"
        assert row["safety_stock"] is None
        assert row["reorder_point"] is None
        assert row.get("reorder_point_units") is None
        assert row.get("safety_stock_units") is None
        assert row.get("arithmetic") is None
        assert row["current"] == {"reorder_point": 9.0, "safety_stock": 1.0}
        assert row["delta_reorder_point"] is None
        assert row["on_hand_units"] == 7
        assert row["on_order_units"] == 12
        assert row["unit_cost"] == pytest.approx(3.0)
        assert row["suggested_order_quantity"] is None
        assert row["order_quantity"] is None
        return
    d, lt = {(A1): (10.0, 10.0), (A2): (2.0, 5.0), (A4): (2.0, 5.0)}[key]
    rp = d * lt
    assert row["status"] == "ok"
    assert row["demand_class"] == "smooth"
    assert row["safety_stock"] == pytest.approx(0.0)
    assert row["reorder_point"] == pytest.approx(rp)
    assert row["safety_stock_units"] == 0
    assert row["reorder_point_units"] == math.ceil(rp)
    assert row["inputs"] == {
        "d": d,
        "sd_d": 0.0,
        "lt": lt,
        "sd_lt": 0.0,
        "z": pytest.approx(1.644854),
        "service_level": 0.95,
    }
    suggested = {A1: 45, A2: 10, A4: 10}[key]
    current = {
        A1: (120.0, 5.0),
        A2: (12.0, 1.0),
        A4: (None, None),
    }[key]
    assert row["suggested_order_quantity"] == suggested
    assert row["current"] == {
        "reorder_point": current[0],
        "safety_stock": current[1],
    }
    if current[0] is not None:
        assert row["delta_reorder_point"] == pytest.approx(current[0] - rp)
    else:
        assert row["delta_reorder_point"] is None
    on_hand = {A1: 30, A2: 0, A4: 0}[key]
    on_order = {A1: 25, A2: 0, A4: 0}[key]
    assert row["on_hand_units"] == on_hand
    assert row["on_order_units"] == on_order
    assert row["unit_cost"] == pytest.approx({A1: 2.0, A2: 100000.0, A4: None}[key])
    # The new field, checked in detail.
    if key == A3:
        return
    _check_order_quantity(row, key)


def test_issue645_freeform(client, monkeypatch) -> None:
    monkeypatch.delenv("AIERP_SERVICE_LEVEL", raising=False)
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)

    # --- AC-2: the method as a pure function -------------------------------
    result = recommend_order_quantity(2.0, 10.0, 50.0, 0.25)
    assert set(result) == QTY_KEYS
    assert result["basis"] == "eoq"
    assert result["order_quantity"] == pytest.approx(
        math.sqrt(2 * 730 * 50 / (0.25 * 10))
    )
    assert result["order_quantity"] == pytest.approx(170.88, abs=0.01)
    assert result["order_quantity_units"] == 171
    assert result["clamped"] is None
    assert result["cover_days"] == pytest.approx(85.5)
    assert result["annual_demand"] == pytest.approx(730.0)
    assert result["order_cost"] == 50.0
    assert result["holding_rate"] == 0.25
    assert result["unit_cost"] == 10.0
    assert result["arithmetic"] == _eoq_arithmetic(2.0, 10.0, 50.0, 0.25)

    result = recommend_order_quantity(2.0, None, 50.0, 0.25)
    assert set(result) == QTY_KEYS
    assert result["basis"] == "assumed_30_days"
    assert result["clamped"] is None
    assert result["order_quantity"] == pytest.approx(60.0)
    assert result["order_quantity_units"] == 60
    assert result["cover_days"] == pytest.approx(30.0)
    assert result["unit_cost"] is None
    assert result["arithmetic"] == _assumed_arithmetic(2.0)

    result = recommend_order_quantity(2.0, 100000, 50.0, 0.25)
    assert set(result) == QTY_KEYS
    assert result["basis"] == "eoq"
    assert result["clamped"] == "min_cover"
    assert result["order_quantity"] == pytest.approx(14.0)
    assert result["order_quantity_units"] == 14
    assert result["cover_days"] == pytest.approx(7.0)

    result = recommend_order_quantity(2.0, 0.0001, 50.0, 0.25)
    assert set(result) == QTY_KEYS
    assert result["basis"] == "eoq"
    assert result["clamped"] == "max_cover"
    assert result["order_quantity"] == pytest.approx(730.0)
    assert result["order_quantity_units"] == 730
    assert result["cover_days"] == pytest.approx(365.0)

    assert recommend_order_quantity(0, 10.0, 50.0, 0.25) is None
    assert recommend_order_quantity(float("inf"), 10.0, 50.0, 0.25) is None
    assert recommend_order_quantity(float("nan"), 10.0, 50.0, 0.25) is None

    # --- AC-2: the settings readers ----------------------------------------
    monkeypatch.setenv("AIERP_ORDER_COST", "80")
    assert order_cost_from_env() == pytest.approx(80.0)
    for bad in ("abc", "-5", "0", "inf"):
        monkeypatch.setenv("AIERP_ORDER_COST", bad)
        assert (
            order_cost_from_env()
            == pytest.approx(DEFAULT_ORDER_COST)
            == pytest.approx(50.0)
        )
    monkeypatch.delenv("AIERP_ORDER_COST", raising=False)
    assert order_cost_from_env() == pytest.approx(DEFAULT_ORDER_COST)

    monkeypatch.setenv("AIERP_HOLDING_RATE", "0.3")
    assert holding_rate_from_env() == pytest.approx(0.3)
    for bad in ("1.5", "0", "x"):
        monkeypatch.setenv("AIERP_HOLDING_RATE", bad)
        assert (
            holding_rate_from_env()
            == pytest.approx(DEFAULT_HOLDING_RATE)
            == pytest.approx(0.25)
        )
    monkeypatch.delenv("AIERP_HOLDING_RATE", raising=False)
    assert holding_rate_from_env() == pytest.approx(DEFAULT_HOLDING_RATE)

    # --- AC-1: the response model ------------------------------------------
    assert set(OrderQuantity.model_fields) == OBJECT_KEYS
    response = RecommendationResponse(
        item_code="A",
        site_code="B",
        as_of=AS_OF,
        status="ok",
        demand_class="smooth",
        method=METHOD,
        notes=[],
        current={"reorder_point": None, "safety_stock": None},
    )
    assert response.order_quantity is None
    assert RecommendationResponse.model_fields["order_quantity"].is_required() is False

    # --- AC-1: the committed OpenAPI contract ------------------------------
    openapi = json.loads(OPENAPI.read_text())
    order_schema = openapi["components"]["schemas"]["OrderQuantity"]
    assert set(order_schema["properties"]) == OBJECT_KEYS
    response_schema = openapi["components"]["schemas"]["RecommendationResponse"]
    options = response_schema["properties"]["order_quantity"]["anyOf"]
    assert {option["$ref"] for option in options if "$ref" in option} == {
        "#/components/schemas/OrderQuantity"
    }
    assert "null" in {option["type"] for option in options if "type" in option}
    assert response_schema["required"] == [
        "item_code",
        "site_code",
        "as_of",
        "status",
        "demand_class",
        "method",
        "notes",
        "current",
    ]

    # --- the ranking inputs, the way issue 453 monkeypatches them ----------
    class _FixedDate(date):
        @classmethod
        def today(cls) -> date:
            return FIXED_TODAY

    monkeypatch.setattr("app.stats.demand.date", _FixedDate)

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            _seed(cur)
        conn.commit()
    finally:
        conn.close()

    # --- service level: the bulk list --------------------------------------
    rows = recommendations_for_all(as_of=AS_OF)
    assert [(row["item_code"], row["site_code"]) for row in rows] == [
        (key, S1) for key in (A1, A2, A3, A4)
    ]
    by_key = {(row["item_code"], row["site_code"]): row for row in rows}
    for key in (A1, A2, A3, A4):
        _check_row(by_key[(key, S1)], key, AS_OF)
    # recommended_max_units is the reorder point plus the order quantity.
    for key in (A1, A2, A4):
        row = by_key[(key, S1)]
        quantity = row["order_quantity"]
        assert quantity["recommended_max_units"] == (
            row["reorder_point_units"] + quantity["order_quantity_units"]
        )

    # --- service level: one key equals its list row ------------------------
    for key in (A1, A2, A3, A4):
        single = recommendation_for_key(key, S1, as_of=AS_OF)
        assert single == by_key[(key, S1)]

    # --- AC-1: a zero d with a reorder point yields a null object ----------
    base = {
        "status": "ok",
        "demand_class": "smooth",
        "safety_stock": 0.0,
        "reorder_point": 10.0,
        "safety_stock_units": 0,
        "reorder_point_units": 10,
        "inputs": {
            "d": 0.0,
            "sd_d": 0.0,
            "lt": 5.0,
            "sd_lt": 0.0,
            "z": 1.644854,
            "service_level": 0.95,
        },
        "arithmetic": "reorder_point = d * LT + safety_stock = 0 * 5 + 0.00 = 0.00",
        "method": METHOD,
        "notes": [],
        "demand_basis": "history",
        "other_basis": None,
    }
    finished = _finish(
        dict(base),
        A1,
        S1,
        AS_OF,
        {
            "reorder_point": 12.0,
            "safety_stock": 5.0,
            "unit_cost": 2.0,
            "max_qty": 400.0,
            "order_qty": 50.0,
        },
        3,
        4,
        DEFAULT_ORDER_COST,
        DEFAULT_HOLDING_RATE,
    )
    assert finished["order_quantity"] is None
    # ...and the recommendation is otherwise unchanged.
    assert finished["suggested_order_quantity"] == 3
    assert finished["current"] == {"reorder_point": 12.0, "safety_stock": 5.0}
    assert finished["reorder_point_units"] == 10

    # --- AC-2: through the API ---------------------------------------------
    for key in (A1, A2, A3, A4):
        response = client.get(
            "/api/recommendations/by-key",
            params={"item_code": key, "site_code": S1},
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["order_quantity"] == by_key[(key, S1)]["order_quantity"]
        assert (
            body["suggested_order_quantity"]
            == by_key[(key, S1)]["suggested_order_quantity"]
        )
        assert body["current"] == by_key[(key, S1)]["current"]
        if key == A3:
            assert body["order_quantity"] is None
        else:
            assert body["order_quantity"]["recommended_max_units"] == (
                body["reorder_point_units"]
                + body["order_quantity"]["order_quantity_units"]
            )
    body = client.get(
        "/api/recommendations/by-key", params={"item_code": A1, "site_code": S1}
    ).json()
    assert body["order_quantity"]["current_order_qty"] == pytest.approx(50.0)
    assert body["order_quantity"]["current_max_qty"] == pytest.approx(400.0)
    body = client.get(
        "/api/recommendations/by-key", params={"item_code": A2, "site_code": S1}
    ).json()
    assert body["order_quantity"]["current_order_qty"] is None
    assert body["order_quantity"]["current_max_qty"] is None

    # The list route carries the new field too.
    response = client.get("/api/recommendations", params={"as_of": AS_OF_STR})
    assert response.status_code == 200, response.text
    list_rows = {(row["item_code"], row["site_code"]): row for row in response.json()}
    assert set(list_rows) == {(key, S1) for key in (A1, A2, A3, A4)}
    for key in (A1, A2, A3, A4):
        assert (
            list_rows[(key, S1)]["order_quantity"]
            == by_key[(key, S1)]["order_quantity"]
        )

    # --- AC-1: the environment is read once per call -----------------------
    counts = {"order_cost": 0, "holding_rate": 0}
    real_order_cost = order_cost_from_env
    real_holding_rate = holding_rate_from_env

    def counting_order_cost() -> float:
        counts["order_cost"] += 1
        return real_order_cost()

    def counting_holding_rate() -> float:
        counts["holding_rate"] += 1
        return real_holding_rate()

    monkeypatch.setattr(
        "app.recommend.reorder.order_cost_from_env", counting_order_cost
    )
    monkeypatch.setattr(
        "app.recommend.reorder.holding_rate_from_env", counting_holding_rate
    )
    recommendations_for_all(as_of=AS_OF)
    assert counts == {"order_cost": 1, "holding_rate": 1}, counts
    counts["order_cost"] = 0
    counts["holding_rate"] = 0
    recommendation_for_key(A1, S1, as_of=AS_OF)
    assert counts == {"order_cost": 1, "holding_rate": 1}, counts
