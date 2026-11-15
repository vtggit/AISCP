"""CSV rendering for the ``drift_ranked``, ``excess_ranked``,
``stockout_ranked``, ``recommendation_changes`` and
``recommendations_for_all`` (planning parameters) rows.

``drift_csv`` is pure: it takes the dicts returned by
``app.recommend.drift.drift_ranked`` and renders them as CSV text with
a UTF-8 byte-order mark, one header row and one row per input row in
the given order. Money and cost are written with exactly two decimals,
quantities as plain numbers and ``safety_stock_units`` as the ceiling
of the row's ``safety_stock`` when it is a finite number (int, float
or Decimal — the recommendation's whole-unit
``safety_stock_units``), an empty cell when the row's
``safety_stock`` is null or not a finite number, ``None`` as empty
cells and ``notes`` joined with ``"; "``.
Values are quoted by Python's ``csv`` module and any text cell that
begins with ``=``, ``+``, ``-`` or ``@`` is prefixed with a single
quote so Excel never evaluates it as a formula.
"""

import csv
import io
import math
from collections.abc import Iterable
from decimal import Decimal

#: Export columns, in order. The two ``*_units`` columns map onto the
#: ``reorder_point_units`` and ``safety_stock`` keys of a drift row
#: (``safety_stock_units`` rendered as the ceiling of a finite
#: numeric ``safety_stock``, an empty cell otherwise), the
#: four order-quantity columns flatten the row's ``order_quantity``
#: object, ``abc_class`` flattens its ``service_level_source`` object
#: (empty cells without them) and ``service_level`` maps onto the
#: row's ``service_level``.
_COLUMNS = (
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
)

_ROW_KEYS = {
    "recommended_reorder_point_units": "reorder_point_units",
    "safety_stock_units": "safety_stock",
}

#: Export columns that flatten a nested drift-row object: the column
#: name maps onto a (container key, field key) pair.
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


def _text(value: str) -> str:
    """Prefix a formula-like text cell with a single quote."""
    if value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value


def _cell(row: dict, column: str) -> str:
    """Render one drift-row value for one export column."""
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
    if column == "safety_stock_units":
        if isinstance(value, (int, float, Decimal)) and math.isfinite(value):
            return str(math.ceil(value))
        return ""
    if column == "notes":
        return _text("; ".join(value))
    if isinstance(value, str):
        return _text(value)
    return str(value)


def drift_csv(rows: Iterable[dict]) -> str:
    """Render ``drift_ranked`` rows as CSV text.

    The text starts with a UTF-8 byte-order mark, followed by the
    header row and one row per input row in the given order.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(_COLUMNS))
    for row in rows:
        writer.writerow([_cell(row, column) for column in _COLUMNS])
    return "\ufeff" + buffer.getvalue()


#: Excess export columns, in order. The columns map 1:1 onto the
#: ``excess_ranked`` row keys.
_EXCESS_COLUMNS = (
    "rank",
    "category",
    "dead_reason",
    "item_code",
    "site_code",
    "on_hand_units",
    "justified_units",
    "excess_units",
    "months_of_cover",
    "unit_cost",
    "money_tied_up",
    "last_demand_on",
    "demand_window_units",
    "notes",
    "arithmetic",
    "as_of",
)

_EXCESS_MONEY_COLUMNS = ("unit_cost", "money_tied_up")


def _excess_cell(row: dict, column: str) -> str:
    """Render one excess-row value for one export column."""
    value = row.get(column)
    if value is None:
        return ""
    if column in _EXCESS_MONEY_COLUMNS:
        return f"{value:.2f}"
    if column == "notes":
        return _text("; ".join(value))
    if isinstance(value, str):
        return _text(value)
    return str(value)


def excess_csv(rows: Iterable[dict]) -> str:
    """Render ``excess_ranked`` rows as CSV text.

    The text starts with a UTF-8 byte-order mark, followed by the
    header row and one row per input row in the given order.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(_EXCESS_COLUMNS))
    for row in rows:
        writer.writerow([_excess_cell(row, column) for column in _EXCESS_COLUMNS])
    return "\ufeff" + buffer.getvalue()


#: Stockout export columns, in order. The columns map 1:1 onto the
#: ``stockout_ranked`` row keys.
_STOCKOUT_COLUMNS = (
    "rank",
    "category",
    "item_code",
    "site_code",
    "p_stockout",
    "projected_stockout_on",
    "days_of_cover",
    "on_hand_units",
    "open_supply_units",
    "open_supply_due_by",
    "lead_time_days",
    "lead_time_demand",
    "recommended_reorder_point_units",
    "expected_units_short",
    "unit_cost",
    "money_at_risk",
    "notes",
    "arithmetic",
    "as_of",
)

_STOCKOUT_MONEY_COLUMNS = ("unit_cost", "money_at_risk")

_STOCKOUT_PROBABILITY_COLUMNS = ("p_stockout",)


def _stockout_cell(row: dict, column: str) -> str:
    """Render one stockout-row value for one export column."""
    value = row.get(column)
    if value is None:
        return ""
    if column in _STOCKOUT_MONEY_COLUMNS:
        return f"{value:.2f}"
    if column in _STOCKOUT_PROBABILITY_COLUMNS:
        return f"{value:.4f}"
    if column == "notes":
        return _text("; ".join(value))
    if isinstance(value, str):
        return _text(value)
    return str(value)


def stockout_csv(rows: Iterable[dict]) -> str:
    """Render ``stockout_ranked`` rows as CSV text.

    The text starts with a UTF-8 byte-order mark, followed by the
    header row and one row per input row in the given order.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(_STOCKOUT_COLUMNS))
    for row in rows:
        writer.writerow([_stockout_cell(row, column) for column in _STOCKOUT_COLUMNS])
    return "\ufeff" + buffer.getvalue()


#: Recommendation-change export columns, in order. The four contribution
#: columns flatten the row's ``contributions`` object and ``d_then`` /
#: ``d_now`` / ``lt_then`` / ``lt_now`` its ``inputs_then`` /
#: ``inputs_now`` objects (empty cells without them).
_CHANGES_COLUMNS = (
    "rank",
    "item_code",
    "site_code",
    "status_then",
    "status_now",
    "reorder_point_units_then",
    "reorder_point_units_now",
    "change_units",
    "primary_driver",
    "demand_level",
    "demand_variability",
    "lead_time",
    "lead_time_variability",
    "d_then",
    "d_now",
    "lt_then",
    "lt_now",
    "unit_cost",
    "money",
    "since",
    "as_of",
    "notes",
)

_CHANGES_CONTRIBUTION_COLUMNS = (
    "demand_level",
    "demand_variability",
    "lead_time",
    "lead_time_variability",
)

_CHANGES_MONEY_COLUMNS = ("unit_cost", "money")

_CHANGES_DECIMAL_COLUMNS = _CHANGES_CONTRIBUTION_COLUMNS + _CHANGES_MONEY_COLUMNS


def _changes_flat(row: dict) -> dict:
    """A change row with its nested fields flattened onto export columns."""
    flat = dict(row)
    contributions = row.get("contributions")
    for column in _CHANGES_CONTRIBUTION_COLUMNS:
        flat[column] = contributions.get(column) if contributions is not None else None
    for suffix, container in (("then", "inputs_then"), ("now", "inputs_now")):
        inputs = row.get(container)
        flat[f"d_{suffix}"] = inputs.get("d") if inputs is not None else None
        flat[f"lt_{suffix}"] = inputs.get("lt") if inputs is not None else None
    return flat


def _changes_cell(row: dict, column: str) -> str:
    """Render one change-row value for one export column."""
    value = row.get(column)
    if value is None:
        return ""
    if column in _CHANGES_DECIMAL_COLUMNS:
        return f"{value:.2f}"
    if column == "notes":
        return _text("; ".join(value))
    if isinstance(value, str):
        return _text(value)
    return str(value)


def changes_csv(rows: Iterable[dict]) -> str:
    """Render ``recommendation_changes`` rows as CSV text.

    The rows are the dicts ``app.recommend.changes.recommendation_changes``
    returns, each carrying its 1-based ``rank``. The text starts with a UTF-8
    byte-order mark, followed by the header row and one row per input row in
    the given order.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(_CHANGES_COLUMNS))
    for row in rows:
        writer.writerow(
            [_changes_cell(_changes_flat(row), column) for column in _CHANGES_COLUMNS]
        )
    return "\ufeff" + buffer.getvalue()


#: Planning-parameter export columns, in order. ``abc_class`` flattens
#: the row's ``service_level_source`` object, ``service_level`` its
#: ``inputs`` object, ``recommended_reorder_point_units`` and
#: ``recommended_safety_stock_units`` map onto the row's
#: ``reorder_point_units`` and ``safety_stock_units``, the four
#: order-quantity columns flatten the row's ``order_quantity`` object,
#: ``current_reorder_point`` and ``current_safety_stock`` its
#: ``current`` object and the two empirical columns its ``empirical``
#: object — every flattened column an empty cell when its container is
#: null or the field is absent. ``unit_cost`` renders with exactly two
#: decimals, integers stay plain and every other value uses
#: ``str()``.
_PLANNING_COLUMNS = (
    "item_code",
    "site_code",
    "as_of",
    "status",
    "demand_class",
    "abc_class",
    "service_level",
    "recommended_reorder_point_units",
    "recommended_safety_stock_units",
    "recommended_order_quantity_units",
    "recommended_max_units",
    "current_reorder_point",
    "current_safety_stock",
    "current_order_qty",
    "current_max_qty",
    "empirical_reorder_point_units",
    "empirical_safety_stock_units",
    "unit_cost",
)

_PLANNING_ROW_KEYS = {
    "recommended_reorder_point_units": "reorder_point_units",
    "recommended_safety_stock_units": "safety_stock_units",
}

#: Planning-parameter export columns that flatten a nested
#: recommendation-row object: the column name maps onto a (container
#: key, field key) pair.
_PLANNING_NESTED_ROW_KEYS = {
    "abc_class": ("service_level_source", "abc_class"),
    "service_level": ("inputs", "service_level"),
    "recommended_order_quantity_units": (
        "order_quantity",
        "order_quantity_units",
    ),
    "recommended_max_units": ("order_quantity", "recommended_max_units"),
    "current_reorder_point": ("current", "reorder_point"),
    "current_safety_stock": ("current", "safety_stock"),
    "current_order_qty": ("order_quantity", "current_order_qty"),
    "current_max_qty": ("order_quantity", "current_max_qty"),
    "empirical_reorder_point_units": ("empirical", "reorder_point_units"),
    "empirical_safety_stock_units": ("empirical", "safety_stock_units"),
}

_PLANNING_MONEY_COLUMNS = ("unit_cost",)


def _planning_cell(row: dict, column: str) -> str:
    """Render one recommendation-row value for one export column."""
    if column in _PLANNING_NESTED_ROW_KEYS:
        container, field = _PLANNING_NESTED_ROW_KEYS[column]
        container = row.get(container)
        value = container.get(field) if container is not None else None
    else:
        value = row.get(_PLANNING_ROW_KEYS.get(column, column))
    if value is None:
        return ""
    if column in _PLANNING_MONEY_COLUMNS:
        return f"{value:.2f}"
    if isinstance(value, str):
        return _text(value)
    return str(value)


def planning_parameters_csv(rows: Iterable[dict]) -> str:
    """Render ``recommendations_for_all`` rows as CSV text.

    The rows are the dicts ``app.recommend.reorder.recommendations_for_all``
    returns. The text starts with a UTF-8 byte-order mark, followed by the
    header row and one row per input row in the given order.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(list(_PLANNING_COLUMNS))
    for row in rows:
        writer.writerow([_planning_cell(row, column) for column in _PLANNING_COLUMNS])
    return "\ufeff" + buffer.getvalue()
