"""The short-order release record: one row per short supplier released.

``record_short_orders(horizon_days, answer, actor)`` records the short
suppliers a confirmed order-plan release went out for: ``answer`` is
the plan the confirm's ``app.api.aggregate.confirm_release_groups``
returned — the release plan with each group's ``outcome`` on top —
and with no group whose ``outcome`` is ``created`` it writes nothing,
never reads the top-ups suggestions and answers 0.  Otherwise it
reads ``app.api.supply_plan.top_ups_by_supplier(horizon_days,
as_of)`` once, at the release's as-of — ``app.supply.release.
_data_as_of()``, the as-of ``order_plan_release`` builds its plan at —
and writes one ``short_order_releases`` row for each supplier entry of
the suggestions whose ``source_system`` is ``acumatica`` and whose
``supplier_code`` holds at least one of the answer's created groups:
the answer's ``as_of``, the entry's ``supplier_code``,
``source_system``, ``minimum`` and ``shortfall``, its ``suggestion``
as the row's ``suggested`` and its ``suggestion_reason``, the row's
``chosen`` ``add_top_ups`` when one of the supplier's created groups
holds a line whose ``source`` is ``top_up`` else ``order_anyway``,
and the given ``actor`` — the actor the release's ``plan_releases``
rows record.  All of the release's rows go out in one transaction,
and the count of the rows written is the answer.  A failure of the
suggestions' read or of the write is logged as a warning and answers
0: the record never changes the release's answer, its status, or the
standing-grant run's outcome or its ``standing_grant_runs`` row.
"""

import logging

from app.db.connection import get_cursor

logger = logging.getLogger(__name__)

_INSERT_SQL = (
    "INSERT INTO short_order_releases "
    "(as_of, supplier_code, source_system, minimum, shortfall, "
    "suggested, suggestion_reason, chosen, actor) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def _insert_rows(rows: list[tuple]) -> None:
    """All of the release's rows, in one transaction.

    One connection and one commit: every row of the release goes in
    together, and a raised write rolls the transaction back, so a
    partial record is never stored.
    """
    with get_cursor() as cur:
        for row in rows:
            cur.execute(_INSERT_SQL, row)


def record_short_orders(horizon_days: int, answer: dict, actor: str) -> int:
    """The short-order record of one confirmed order-plan release.

    See the module docstring for the contract: with no group whose
    ``outcome`` is ``created`` nothing is written, the top-ups
    suggestions are never read and the answer is 0; otherwise the
    suggestions ``top_ups_by_supplier(horizon_days, as_of)`` hold at
    the release's as-of, one row per ``acumatica`` supplier entry
    whose ``supplier_code`` holds at least one created group — the
    entry's ``minimum`` and ``shortfall``, its ``suggestion`` as
    ``suggested`` and its ``suggestion_reason``, the release's
    ``as_of``, ``chosen`` ``add_top_ups`` when one of the supplier's
    created groups holds a line whose ``source`` is ``top_up`` else
    ``order_anyway`` — written in one transaction, the count of the
    rows written the answer.  A failure of the read or the write is
    logged as a warning and answers 0, never raising into the
    release's answer or the run's outcome.
    """
    try:
        created_groups = [
            group for group in answer["groups"] if group.get("outcome") == "created"
        ]
        if not created_groups:
            return 0
        # Imported here: app.api.supply_plan imports this record's
        # callers for its release route, so a module-level import
        # would cycle.
        from app.api.supply_plan import top_ups_by_supplier
        from app.supply.release import _data_as_of

        suggestions = top_ups_by_supplier(horizon_days, _data_as_of())
        as_of = suggestions["as_of"]
        created_by_supplier: dict[str, list[dict]] = {}
        for group in created_groups:
            created_by_supplier.setdefault(group["supplier_code"], []).append(group)
        rows = []
        for entry in suggestions["suppliers"]:
            if entry.get("source_system") != "acumatica":
                continue
            groups = created_by_supplier.get(entry["supplier_code"])
            if not groups:
                continue
            chosen = (
                "add_top_ups"
                if any(
                    line.get("source") == "top_up"
                    for group in groups
                    for line in group["lines"]
                )
                else "order_anyway"
            )
            rows.append(
                (
                    as_of,
                    entry["supplier_code"],
                    entry["source_system"],
                    entry["minimum"],
                    entry["shortfall"],
                    entry["suggestion"],
                    entry["suggestion_reason"],
                    chosen,
                    actor,
                )
            )
        if not rows:
            return 0
        _insert_rows(rows)
        return len(rows)
    except Exception:
        logger.warning("failed to record the short-order release", exc_info=True)
        return 0
