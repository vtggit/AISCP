"""PostgreSQL repository for excess_decisions (append-only).

Every excess decision is stored as a new row that snapshots the
item-site's current dead-and-excess ranked row at decision time.  Rows
are never updated or deleted: the history of an item-site is the full
sequence of its decisions, and its current decision is the latest one
(by decided_at, id breaking ties).
"""

from datetime import date

import psycopg2.extras

from app.db.connection import get_connection, get_cursor


class ExcessDecisionPostgresRepository:
    """PostgreSQL repository for the excess_decisions table."""

    def insert(
        self,
        *,
        id: str,
        item_code: str,
        site_code: str,
        decision: str,
        action: str | None,
        reason: str | None,
        snooze_until: date | None,
        as_of: date,
        category: str,
        dead_reason: str | None,
        on_hand_units: float,
        excess_units: float,
        money_tied_up: float | None,
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
                    "INSERT INTO excess_decisions "
                    "(id, item_code, site_code, decision, action, reason, "
                    "snooze_until, as_of, category, dead_reason, on_hand_units, "
                    "excess_units, money_tied_up, decided_by, decided_by_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING *",
                    (
                        id,
                        item_code,
                        site_code,
                        decision,
                        action,
                        reason,
                        snooze_until,
                        as_of,
                        category,
                        dead_reason,
                        on_hand_units,
                        excess_units,
                        money_tied_up,
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
                "SELECT * FROM excess_decisions "
                "WHERE item_code = %s AND site_code = %s "
                "ORDER BY decided_at DESC, id DESC "
                "LIMIT %s OFFSET %s",
                (item_code, site_code, limit, offset),
            )
            return [dict(row) for row in cur.fetchall()]

    def current_all(self) -> list[dict]:
        """The current decision of every item-site that has one, unbounded.

        The same latest-row-per-(item_code, site_code) selection as
        ``current`` (latest by decided_at, id breaking ties), but without
        LIMIT/OFFSET: callers that must see every current decision in a
        single query (the excess-row decision enrichment) must not be
        limited to one page of results, or an item-site whose current
        decision sits past the first page would be reported as undecided.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT d.* FROM excess_decisions AS d "
                "JOIN (SELECT DISTINCT ON (item_code, site_code) id "
                "      FROM excess_decisions "
                "      ORDER BY item_code, site_code, decided_at DESC, id DESC) "
                "      AS latest ON latest.id = d.id "
                "ORDER BY d.decided_at DESC, d.id DESC"
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
                "SELECT d.* FROM excess_decisions AS d "
                "JOIN (SELECT DISTINCT ON (item_code, site_code) id "
                "      FROM excess_decisions "
                "      ORDER BY item_code, site_code, decided_at DESC, id DESC) "
                "      AS latest ON latest.id = d.id "
                "ORDER BY d.decided_at DESC, d.id DESC "
                "LIMIT %s OFFSET %s",
                (limit, offset),
            )
            return [dict(row) for row in cur.fetchall()]
