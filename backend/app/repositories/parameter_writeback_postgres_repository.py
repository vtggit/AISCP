"""PostgreSQL repository for the parameter-writeback preview.

The preview lists the item-sites whose current planner decision is
"accepted" but not yet applied to the ERP.  "Outstanding" uses exactly
the decision summary's rule: an accepted decision is applied when the
item-site's current ``item_sites.reorder_point`` is not null and
differs from the decision's ``recommended_reorder_point_units`` by at
most 1; every other accepted decision is outstanding, including one
whose item-site row is missing (the LEFT JOIN keeps it, with a null
reorder point).  All of that runs in ONE query, whatever the number of
item-sites.
"""

from app.db.connection import get_cursor


class ParameterWritebackPostgresRepository:
    """PostgreSQL repository for the parameter-writeback preview."""

    def outstanding_accepted(self) -> list[dict]:
        """The outstanding accepted decisions, one query in total.

        Selects each item-site's current planner decision the same way
        ``PlannerDecisionPostgresRepository.current_decisions`` does
        (the latest row per (item_code, site_code) by decided_at, id
        breaking ties), keeps only the "accepted" ones that are
        outstanding by the summary's rule, and left-joins item_sites on
        (item_code, site_code) so every row carries the item-site's
        current ``reorder_point``, ``max_qty``, ``order_qty`` and
        ``source_system`` — all null when the item-site row is missing
        (``site_id`` is null in exactly that case).

        Each row carries, from the decision: its ``id`` (as
        ``decision_id``), ``item_code``, ``site_code``,
        ``recommended_reorder_point_units``, ``money_at_stake`` and
        ``decided_at``.  The rows are ordered by ``money_at_stake``
        descending with nulls last, then ``item_code`` and
        ``site_code``.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT d.id AS decision_id, "
                "d.item_code, d.site_code, "
                "d.recommended_reorder_point_units, "
                "d.money_at_stake, d.decided_at, "
                "sites.id AS site_id, "
                "sites.reorder_point, sites.max_qty, sites.order_qty, "
                "sites.source_system "
                "FROM planner_decisions AS d "
                "JOIN (SELECT DISTINCT ON (item_code, site_code) id "
                "      FROM planner_decisions "
                "      ORDER BY item_code, site_code, decided_at DESC, id DESC) "
                "      AS latest ON latest.id = d.id "
                "LEFT JOIN item_sites AS sites "
                "  ON sites.item_code = d.item_code "
                " AND sites.site_code = d.site_code "
                "WHERE d.decision = 'accepted' "
                "  AND (sites.reorder_point IS NULL "
                "       OR ABS(sites.reorder_point "
                "           - d.recommended_reorder_point_units) > 1) "
                "ORDER BY d.money_at_stake DESC NULLS LAST, "
                "d.item_code, d.site_code"
            )
            return [dict(row) for row in cur.fetchall()]
