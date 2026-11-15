"""PostgreSQL repository for planner_decisions (append-only).

Every planner decision is stored as a new row that snapshots the
item-site's current drift row at decision time.  Rows are never updated
or deleted: the history of an item-site is the full sequence of its
decisions, and its current decision is the latest one (by decided_at,
id breaking ties).
"""

from datetime import date

import psycopg2.extras

from app.db.connection import get_connection, get_cursor


class PlannerDecisionPostgresRepository:
    """PostgreSQL repository for the planner_decisions table."""

    def insert(
        self,
        *,
        id: str,
        item_code: str,
        site_code: str,
        decision: str,
        reason: str | None,
        snooze_until: date | None,
        as_of: date,
        recommended_reorder_point_units: int,
        current_reorder_point: float | None,
        money_at_stake: float | None,
        decided_by: str,
        decided_by_name: str | None,
    ) -> dict:
        """Store one new decision and return the stored row.

        decided_by stores the token subject and decided_by_name the
        caller's display name (null when the caller has none).
        decided_at is left to the column's server default (now()), so
        the stored row carries the database's decision time.  Because
        this is the only write path and it is an INSERT, earlier
        decisions can never be changed or deleted.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO planner_decisions "
                    "(id, item_code, site_code, decision, reason, snooze_until, "
                    "as_of, recommended_reorder_point_units, current_reorder_point, "
                    "money_at_stake, decided_by, decided_by_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING *",
                    (
                        id,
                        item_code,
                        site_code,
                        decision,
                        reason,
                        snooze_until,
                        as_of,
                        recommended_reorder_point_units,
                        current_reorder_point,
                        money_at_stake,
                        decided_by,
                        decided_by_name,
                    ),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def history(
        self, item_code: str, site_code: str, limit: int, offset: int
    ) -> list[dict]:
        """Every decision of one item-site, newest first.

        Ordered by decided_at DESC with id DESC as the tiebreaker, so
        rows decided in the same instant still come back in a stable
        order (the later-inserted id sorts first).
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM planner_decisions "
                "WHERE item_code = %s AND site_code = %s "
                "ORDER BY decided_at DESC, id DESC "
                "LIMIT %s OFFSET %s",
                (item_code, site_code, limit, offset),
            )
            return [dict(row) for row in cur.fetchall()]

    def decision_summary(self) -> dict:
        """Aggregate the current decision of every item-site in one query.

        The current decision of an item-site is its latest planner
        decision (by decided_at, id breaking ties).  The query
        aggregates those current decisions against the item_sites
        reorder points with a fixed number of queries whatever the
        number of item-sites.

        An accepted decision is applied when the item-site's current
        ``item_sites.reorder_point`` is not null and differs from the
        decision's ``recommended_reorder_point_units`` by at most 1;
        every other accepted decision is outstanding, including one
        whose item-site row is missing (the LEFT JOIN keeps it, with a
        null reorder point).  ``money`` values sum the decisions'
        snapshotted ``money_at_stake`` (nulls contribute nothing) and
        ``without_money`` counts the decisions whose snapshot is null.
        Returns ``accepted_count``, ``accepted_money``,
        ``accepted_without_money``, the same three for ``applied`` and
        ``outstanding``, and ``dismissed_count`` / ``snoozed_count``.
        """
        applied = (
            "latest.decision = 'accepted' "
            "AND sites.reorder_point IS NOT NULL "
            "AND ABS(sites.reorder_point - latest.recommended_reorder_point_units) <= 1"
        )
        with get_cursor() as cur:
            cur.execute(
                "WITH latest AS ("
                "  SELECT DISTINCT ON (item_code, site_code) "
                "         item_code, site_code, decision, "
                "         recommended_reorder_point_units, money_at_stake "
                "  FROM planner_decisions "
                "  ORDER BY item_code, site_code, decided_at DESC, id DESC"
                ") "
                "SELECT "
                f"  COUNT(*) FILTER (WHERE latest.decision = 'accepted') "
                "    AS accepted_count, "
                "  COALESCE(SUM(latest.money_at_stake) "
                "    FILTER (WHERE latest.decision = 'accepted'), 0) "
                "    AS accepted_money, "
                "  COUNT(*) FILTER (WHERE latest.decision = 'accepted' "
                "    AND latest.money_at_stake IS NULL) "
                "    AS accepted_without_money, "
                f"  COUNT(*) FILTER (WHERE {applied}) AS applied_count, "
                f"  COALESCE(SUM(latest.money_at_stake) FILTER (WHERE {applied}), 0) "
                "    AS applied_money, "
                f"  COUNT(*) FILTER (WHERE {applied} "
                "    AND latest.money_at_stake IS NULL) AS applied_without_money, "
                f"  COUNT(*) FILTER (WHERE latest.decision = 'accepted' "
                f"    AND NOT ({applied})) AS outstanding_count, "
                f"  COALESCE(SUM(latest.money_at_stake) "
                f"    FILTER (WHERE latest.decision = 'accepted' AND NOT ({applied})), 0) "
                "    AS outstanding_money, "
                f"  COUNT(*) FILTER (WHERE latest.decision = 'accepted' "
                "    AND latest.money_at_stake IS NULL "
                f"    AND NOT ({applied})) AS outstanding_without_money, "
                "  COUNT(*) FILTER (WHERE latest.decision = 'dismissed') "
                "    AS dismissed_count, "
                "  COUNT(*) FILTER (WHERE latest.decision = 'snoozed') "
                "    AS snoozed_count "
                "FROM latest "
                "LEFT JOIN item_sites AS sites "
                "  ON sites.item_code = latest.item_code "
                " AND sites.site_code = latest.site_code",
            )
            row = cur.fetchone()
        return dict(row)

    def current_decisions(self) -> list[dict]:
        """The current decision of every item-site that has one, with the
        item-site's current reorder point.

        The same latest-row-per-(item_code, site_code) selection as
        ``current`` (latest by decided_at, id breaking ties), joined
        against item_sites so each row also carries
        ``erp_reorder_point`` — the item-site's current
        ``item_sites.reorder_point`` (null when the item-site row is
        missing).  A single query whatever the number of item-sites.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT d.item_code, d.site_code, d.decision, d.reason, "
                "d.snooze_until, d.as_of, d.recommended_reorder_point_units, "
                "d.current_reorder_point, d.money_at_stake, d.decided_at, "
                "sites.reorder_point AS erp_reorder_point "
                "FROM planner_decisions AS d "
                "JOIN (SELECT DISTINCT ON (item_code, site_code) id "
                "      FROM planner_decisions "
                "      ORDER BY item_code, site_code, decided_at DESC, id DESC) "
                "      AS latest ON latest.id = d.id "
                "LEFT JOIN item_sites AS sites "
                "  ON sites.item_code = d.item_code "
                " AND sites.site_code = d.site_code"
            )
            return [dict(row) for row in cur.fetchall()]

    def current(self, limit: int, offset: int) -> list[dict]:
        """The current decision of every item-site that has one.

        The current decision is the latest row per (item_code,
        site_code) by decided_at, id breaking ties; the result is
        ordered by decided_at DESC with id DESC as the tiebreaker.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT d.* FROM planner_decisions AS d "
                "JOIN (SELECT DISTINCT ON (item_code, site_code) id "
                "      FROM planner_decisions "
                "      ORDER BY item_code, site_code, decided_at DESC, id DESC) "
                "      AS latest ON latest.id = d.id "
                "ORDER BY d.decided_at DESC, d.id DESC "
                "LIMIT %s OFFSET %s",
                (limit, offset),
            )
            return [dict(row) for row in cur.fetchall()]

    def current_all(self) -> list[dict]:
        """The current decision of every item-site that has one, unbounded.

        The same latest-row-per-(item_code, site_code) selection as
        ``current`` (latest by decided_at, id breaking ties), but without
        LIMIT/OFFSET: callers that must see every current decision in a
        single query (the drift-row decision enrichment) must not be
        limited to one page of results, or an item-site whose current
        decision sits past the first page would be reported as undecided.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT d.* FROM planner_decisions AS d "
                "JOIN (SELECT DISTINCT ON (item_code, site_code) id "
                "      FROM planner_decisions "
                "      ORDER BY item_code, site_code, decided_at DESC, id DESC) "
                "      AS latest ON latest.id = d.id "
                "ORDER BY d.decided_at DESC, d.id DESC"
            )
            return [dict(row) for row in cur.fetchall()]
