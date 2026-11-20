"""The data stamp: the input tables' data versions as one tuple (issue #1450).

The stamp is what a reader compares to know whether the planning inputs
changed since it last read them: one version number per input table, in
:data:`INPUT_TABLES` order.  Each version is bumped by the
``data_versions`` triggers of migration 0052, in the writer's own
transaction, so a stamp that is unchanged means none of the input
tables' rows changed — a cached answer over the inputs is still valid —
and a stamp that moved says which input table the new rows landed in.
Writes to a table that is not an input never move the stamp, so the
system's own output tables (decisions, forecasts, exceptions, audits)
cannot make a reader re-read.

``data_stamp()`` reads every version in one query; an input table that
has no row in ``data_versions`` reads as 0.
"""

from app.db.connection import get_cursor

__all__ = ["INPUT_TABLES", "data_stamp"]

#: The eleven input tables, in the order the stamp reports them: the ten
#: tables the ERP syncs write and the planner-entered demand signals,
#: each in the order its migration created the table.
INPUT_TABLES = (
    "inventory_items",
    "item_sites",
    "demand_events",
    "lead_time_observations",
    "on_hand",
    "open_purchase_lines",
    "suppliers",
    "open_sales_lines",
    "supplier_terms",
    "demand_signals",
    "erp_source_currencies",
)


def data_stamp() -> tuple[int, ...]:
    """The input tables' versions, one per :data:`INPUT_TABLES` entry.

    Every version is read in one query.  An input table that has no row
    in ``data_versions`` reads as 0, so a stamp over a partially seeded
    install still answers the full shape.
    """
    with get_cursor() as cur:
        cur.execute(
            "SELECT table_name, version FROM data_versions "
            "WHERE table_name IN ({})".format(", ".join("%s" for _ in INPUT_TABLES)),
            INPUT_TABLES,
        )
        versions = {row["table_name"]: int(row["version"]) for row in cur.fetchall()}
    return tuple(versions.get(table, 0) for table in INPUT_TABLES)
