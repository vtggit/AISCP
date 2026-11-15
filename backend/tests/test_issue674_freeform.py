"""Issue #674 proof: the drift CSV export renders whole safety stock.

AC-1 — the ``safety_stock_units`` cell of ``app.recommend.export.drift_csv``
is the ceiling of the row's ``safety_stock`` when it is a finite number
(the recommendation's whole-unit ``safety_stock_units``) and an empty
cell when the row's ``safety_stock`` is null.

AC-2 — the proof checks the pure function over three rows whose
``safety_stock`` is 888.2220412884972, 30.0 and null: their
``safety_stock_units`` cells are "889", "30" and "", and every other
cell of those rows equals what ``drift_csv`` rendered before this
change.  It then runs through the test client (the development planner
token), with ``drift_ranked`` monkeypatched to those three rows:
``GET /api/drift/export.csv`` returns the same cells.
"""

import csv
import io
import math
from datetime import date
from decimal import Decimal

import app.api.drift as api_drift
from app.recommend.export import drift_csv

SITE = "SITE-674"
AS_OF = date(2026, 9, 30)

# The drift export columns, in order.
COLUMNS = [
    "rank",
    "category",
    "item_code",
    "site_code",
    "direction",
    "current_reorder_point",
    "recommended_reorder_point_units",
    "safety_stock_units",
    "units_gap",
    "unit_cost",
    "money_at_stake",
    "notes",
    "money_basis",
    "arithmetic",
    "as_of",
    "recommended_order_quantity_units",
    "recommended_max_units",
    "current_order_qty",
    "current_max_qty",
    "abc_class",
    "service_level",
]

# Row 1: a fractional safety stock that the ceiling bumps up, every
# field set, the full order quantity and the class service level.
ROW_ONE = {
    "rank": 1,
    "category": "drift",
    "item_code": "A674",
    "site_code": SITE,
    "direction": "under",
    "current_reorder_point": 100.0,
    "reorder_point_units": 1001,
    "safety_stock": 888.2220412884972,
    "units_gap": -52.9,
    "unit_cost": 4.0,
    "money_at_stake": 211.6,
    "notes": ["low demand", "seasonal"],
    "money_basis": "unit_cost",
    "arithmetic": "reorder_point = lt * d + z * sigma_l = 1001.00",
    "as_of": AS_OF,
    "order_quantity": {
        "basis": "eoq",
        "clamped": None,
        "order_quantity": 170.6,
        "order_quantity_units": 171,
        "cover_days": 42.65,
        "annual_demand": 1460.0,
        "order_cost": 50.0,
        "holding_rate": 0.25,
        "unit_cost": 2.0,
        "arithmetic": (
            "order_quantity = sqrt(2 * 365 * d * order_cost / "
            "(holding_rate * unit_cost)) = 170.6"
        ),
        "recommended_max_units": 230,
        "current_order_qty": 50.0,
        "current_max_qty": 100.0,
    },
    "service_level": 0.98,
    "service_level_source": {"abc_class": "A", "source": "class"},
}

# Row 2: a whole-valued safety stock (the ceiling keeps it 30, while
# the pre-#674 rendering wrote "30.0"), no order quantity, a
# formula-like note exercising the formula guard.
ROW_TWO = {
    "rank": 2,
    "category": "drift",
    "item_code": "B674",
    "site_code": SITE,
    "direction": "over",
    "current_reorder_point": 120.0,
    "reorder_point_units": 60,
    "safety_stock": 30.0,
    "units_gap": 20.0,
    "unit_cost": 2.5,
    "money_at_stake": 50.0,
    "notes": ["=SUM(A1)"],
    "money_basis": "money_at_stake = units_gap * unit_cost = 20.00 * 2.5 = 50.00",
    "arithmetic": "@VLOOKUP(item)",
    "as_of": AS_OF,
    "order_quantity": None,
    "service_level": None,
    "service_level_source": None,
}

# Row 3: a null safety stock (an empty cell) and nulls everywhere the
# recommendation carries none.
ROW_THREE = {
    "rank": None,
    "category": "no_reorder_point",
    "item_code": "C674",
    "site_code": SITE,
    "direction": "over",
    "current_reorder_point": None,
    "reorder_point_units": None,
    "safety_stock": None,
    "units_gap": None,
    "unit_cost": None,
    "money_at_stake": None,
    "notes": ["no reorder point set"],
    "money_basis": "money_at_stake = unknown",
    "arithmetic": "reorder point = 120.00 + 1.64 * 20.00 = 152.90",
    "as_of": AS_OF,
    "order_quantity": None,
    "service_level": None,
    "service_level_source": None,
}

_ROWS = [ROW_ONE, ROW_TWO, ROW_THREE]

# The expected safety_stock_units cells of the three rows, in order:
# the ceiling of 888.2220412884972, the ceiling of 30.0 and empty for
# the null.
_EXPECTED_UNITS = ["889", "30", ""]

_ROW_KEYS = {
    "recommended_reorder_point_units": "reorder_point_units",
    "safety_stock_units": "safety_stock",
}
_NESTED_ROW_KEYS = {
    "recommended_order_quantity_units": (
        "order_quantity",
        "order_quantity_units",
    ),
    "recommended_max_units": ("order_quantity", "recommended_max_units"),
    "current_order_qty": ("order_quantity", "current_order_qty"),
    "current_max_qty": ("order_quantity", "current_max_qty"),
    "abc_class": ("service_level_source", "abc_class"),
}
_MONEY_COLUMNS = ("unit_cost", "money_at_stake")
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _pre_text(value: str) -> str:
    """The pre-#674 formula guard for a text cell."""
    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _pre_cell(row: dict, column: str) -> str:
    """What the pre-#674 ``drift_csv`` rendered for one column."""
    if column in _NESTED_ROW_KEYS:
        container, field = _NESTED_ROW_KEYS[column]
        container = row.get(container)
        value = container.get(field) if container is not None else None
    else:
        value = row.get(_ROW_KEYS.get(column, column))
    if value is None:
        return ""
    if column in _MONEY_COLUMNS:
        return f"{value:.2f}"
    if column == "notes":
        return _pre_text("; ".join(value))
    if isinstance(value, str):
        return _pre_text(value)
    return str(value)


def test_issue674_freeform(monkeypatch, client):
    # --- The pure function: three rows, whole-unit safety stock. ------
    rows = [dict(row) for row in _ROWS]
    text = drift_csv(rows)
    assert text.startswith("\ufeff")
    body = text.encode("utf-8")
    assert body.startswith(b"\xef\xbb\xbf")
    parsed = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    assert parsed[0] == COLUMNS
    index = {name: i for i, name in enumerate(COLUMNS)}

    for row, cells, expected_units in zip(rows, parsed[1:], _EXPECTED_UNITS):
        # The safety_stock_units cell is the whole number (or empty).
        assert cells[index["safety_stock_units"]] == expected_units
        # Every other cell equals what the pre-#674 drift_csv rendered
        # for the same row.
        for column in COLUMNS:
            if column == "safety_stock_units":
                continue
            assert cells[index[column]] == _pre_cell(row, column), column

    # --- Through the route: the export returns the same cells. --------
    def fake_drift_ranked(as_of=None):
        return [dict(row) for row in _ROWS]

    monkeypatch.setattr(api_drift, "drift_ranked", fake_drift_ranked)

    response = client.get("/api/drift/export.csv", params={"as_of": "2026-09-30"})
    assert response.status_code == 200, response.text
    route_rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert route_rows[0] == COLUMNS
    assert [
        cells[index["safety_stock_units"]] for cells in route_rows[1:]
    ] == _EXPECTED_UNITS
    # The exported body is exactly what the pure function renders.
    assert response.text == text

    # --- Review defects: Decimal and non-finite safety stock. -----
    # A Decimal safety stock is a finite number: it is ceiled like a
    # float (Decimal("30.57") -> "31"), never rendered as "30.57".
    decimal_row = dict(ROW_ONE)
    decimal_row["safety_stock"] = Decimal("30.57")
    # A non-finite safety stock (inf, NaN or Decimal("Infinity")) is
    # not a finite number: the cell is empty, never "inf" or "nan".
    infinite_row = dict(ROW_ONE)
    infinite_row["safety_stock"] = math.inf
    nan_row = dict(ROW_ONE)
    nan_row["safety_stock"] = math.nan
    decimal_infinite_row = dict(ROW_ONE)
    decimal_infinite_row["safety_stock"] = Decimal("Infinity")
    defect_text = drift_csv([decimal_row, infinite_row, nan_row, decimal_infinite_row])
    defect_parsed = list(csv.reader(io.StringIO(defect_text.lstrip("\ufeff"))))
    assert defect_parsed[0] == COLUMNS
    assert [cells[index["safety_stock_units"]] for cells in defect_parsed[1:]] == [
        "31",
        "",
        "",
        "",
    ]
