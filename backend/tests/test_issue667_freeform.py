"""Issue #667 proof: the drift CSV export gains the order-quantity and
service-level columns.

AC-1 — ``app.recommend.export.drift_csv`` gains six columns after
``as_of``, in this order: ``recommended_order_quantity_units`` (the
row's ``order_quantity.order_quantity_units``),
``recommended_max_units`` (``order_quantity.recommended_max_units``),
``current_order_qty`` (``order_quantity.current_order_qty``),
``current_max_qty`` (``order_quantity.current_max_qty``), ``abc_class``
(``service_level_source.abc_class``) and ``service_level`` (the row's
``service_level``).  Each cell is empty when the row's
``order_quantity`` or ``service_level_source`` is null, or when the
value is null.  Numbers render with ``str()`` and text cells keep the
existing formula guard.  Every existing column, its position and its
formatting, the excess and stockout exports, the BOM, the row order,
the filters and the filename are unchanged.

AC-2 — the proof checks the pure function over two rows: A667, whose
``order_quantity`` has ``order_quantity_units`` 171,
``recommended_max_units`` 230, ``current_order_qty`` 50.0 and
``current_max_qty`` 100.0, whose ``service_level`` is 0.98 and whose
``service_level_source`` is ``{"abc_class": "A", "source": "class"}``,
and B667, whose ``order_quantity``, ``service_level`` and
``service_level_source`` are all null.  It checks that the header is
the old fifteen columns followed by the six new ones, in order, that
the first row's six new cells are "171", "230", "50.0", "100.0", "A"
and "0.98", that the second row's six new cells are all empty, and
that every pre-existing cell equals what ``drift_csv`` produced before
this change for the same rows.  The proof then runs through the test
client (the development planner token), with ``drift_ranked``
monkeypatched to those two rows: ``GET /api/drift/export.csv`` returns
that header and those cells.
"""

import csv
import io
import math
from datetime import date

import app.api.drift as api_drift
from app.recommend.export import drift_csv

SITE = "SITE-667"
AS_OF = date(2026, 9, 30)

# The pre-existing drift export columns, in order.
OLD_COLUMNS = [
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
]

# The six columns issue #667 appends after "as_of", in order.
NEW_COLUMNS = [
    "recommended_order_quantity_units",
    "recommended_max_units",
    "current_order_qty",
    "current_max_qty",
    "abc_class",
    "service_level",
]

# Row 1: every pre-existing field set, the full order quantity and the
# class service level.
ROW_ONE = {
    "rank": 1,
    "category": "drift",
    "item_code": "A667",
    "site_code": SITE,
    "direction": "under",
    "current_reorder_point": 100.0,
    "reorder_point_units": 153,
    "safety_stock": 30.57,
    "units_gap": -52.9,
    "unit_cost": 4.0,
    "money_at_stake": 211.6,
    "notes": ["low demand", "seasonal"],
    "money_basis": "unit_cost",
    "arithmetic": "reorder_point = lt * d + z * sigma_l = 153.00",
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

# Row 2: the order quantity, the service level and its source are all
# null; cost and money are unknown; notes and arithmetic exercise the
# formula guard.
ROW_TWO = {
    "rank": 2,
    "category": "drift",
    "item_code": "B667",
    "site_code": SITE,
    "direction": "over",
    "current_reorder_point": 120.0,
    "reorder_point_units": 100,
    "safety_stock": 25.0,
    "units_gap": 20.0,
    "unit_cost": None,
    "money_at_stake": None,
    "notes": ["=SUM(A1)"],
    "money_basis": "unknown",
    "arithmetic": "@VLOOKUP(item)",
    "as_of": AS_OF,
    "order_quantity": None,
    "service_level": None,
    "service_level_source": None,
}

_OLD_ROW_KEYS = {
    "recommended_reorder_point_units": "reorder_point_units",
    "safety_stock_units": "safety_stock",
}
_OLD_MONEY_COLUMNS = ("unit_cost", "money_at_stake")
_OLD_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _old_text(value: str) -> str:
    """The pre-#667 formula guard for a text cell."""
    if value.startswith(_OLD_FORMULA_PREFIXES):
        return "'" + value
    return value


def _old_cell(row: dict, column: str) -> str:
    """What the pre-#667 ``drift_csv`` rendered for one old column,
    with #674's ceiling for ``safety_stock_units``."""
    value = row.get(_OLD_ROW_KEYS.get(column, column))
    if value is None:
        return ""
    if column in _OLD_MONEY_COLUMNS:
        return f"{value:.2f}"
    if (
        column == "safety_stock_units"
        and isinstance(value, (int, float))
        and math.isfinite(value)
    ):
        return str(math.ceil(value))
    if column == "notes":
        return _old_text("; ".join(value))
    if isinstance(value, str):
        return _old_text(value)
    return str(value)


def test_issue667_freeform(monkeypatch, client):
    # --- The pure function: two rows, the full pre- and post-change
    # column set.
    pure_text = drift_csv([dict(ROW_ONE), dict(ROW_TWO)])
    assert pure_text.startswith("\ufeff")
    pure_rows = list(csv.reader(io.StringIO(pure_text.lstrip("\ufeff"))))
    assert len(pure_rows) == 3

    # The header is the old fifteen columns followed by the six new
    # ones, in order.
    assert pure_rows[0] == OLD_COLUMNS + NEW_COLUMNS
    index = {name: i for i, name in enumerate(pure_rows[0])}

    first, second = pure_rows[1], pure_rows[2]
    # Row 1's six new cells.
    assert [first[index[column]] for column in NEW_COLUMNS] == [
        "171",
        "230",
        "50.0",
        "100.0",
        "A",
        "0.98",
    ]
    # Row 2's six new cells are all empty.
    assert [second[index[column]] for column in NEW_COLUMNS] == [""] * 6

    # Every pre-existing cell equals what the pre-#667 drift_csv
    # produced for the same rows.
    for row, cells in ((ROW_ONE, first), (ROW_TWO, second)):
        for column in OLD_COLUMNS:
            assert cells[index[column]] == _old_cell(row, column), column

    # --- Through the route: with drift_ranked monkeypatched to those
    # two rows, the export returns the same header and cells.
    def fake_drift_ranked(as_of=None):
        return [dict(ROW_ONE), dict(ROW_TWO)]

    monkeypatch.setattr(api_drift, "drift_ranked", fake_drift_ranked)

    response = client.get("/api/drift/export.csv", params={"as_of": "2026-09-30"})
    assert response.status_code == 200, response.text
    route_rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert route_rows[0] == OLD_COLUMNS + NEW_COLUMNS
    assert [route_rows[1][index[column]] for column in NEW_COLUMNS] == [
        "171",
        "230",
        "50.0",
        "100.0",
        "A",
        "0.98",
    ]
    assert [route_rows[2][index[column]] for column in NEW_COLUMNS] == [""] * 6
    # The exported body is exactly what the pure function renders.
    assert response.text == pure_text
