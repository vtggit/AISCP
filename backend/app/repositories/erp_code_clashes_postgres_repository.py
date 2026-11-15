"""PostgreSQL repository for erp_code_clashes — the refused clash pairs.

One row per (item_code, site_code, refused_source): the pair that
``refused_source`` offered to a sync while ``held_by`` stored it in
``item_sites``.  A new row is stamped by the table's server defaults
(``first_seen_at`` and ``last_seen_at`` both ``now()``); every sync
that still sees the pair upserts the same row, moving ``last_seen_at``
to now, refreshing ``held_by`` and clearing ``resolved_at``.  A sync
that no longer sees the pair held sets ``resolved_at`` to now instead,
so a clash someone fixed in the ERP closes itself.  No row is ever
deleted.
"""

import psycopg2.extras

from app.db.connection import get_connection


class ErpCodeClashPostgresRepository:
    """PostgreSQL repository for the erp_code_clashes table."""

    def record_clash(
        self,
        item_code: str,
        site_code: str,
        held_by: str,
        refused_source: str,
    ) -> dict:
        """Upsert the clash row of (item_code, site_code, refused_source).

        A new row sets both timestamps (the table's ``now()`` server
        defaults); an existing row updates ``last_seen_at`` and
        ``held_by`` and clears ``resolved_at``.  The insert-or-update
        decision is made atomically by ON CONFLICT on the table's
        unique constraint, so racing syncs refresh one row instead of
        creating a duplicate.  Returns the stored row.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO erp_code_clashes "
                    "(item_code, site_code, held_by, refused_source) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (item_code, site_code, refused_source) "
                    "DO UPDATE SET last_seen_at = now(), "
                    "held_by = EXCLUDED.held_by, resolved_at = NULL "
                    "RETURNING *",
                    (item_code, site_code, held_by, refused_source),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def list_open(self, limit: int = 200) -> list[dict]:
        """The open clash rows, most recently seen first.

        One statement: the rows whose ``resolved_at`` is still null,
        ordered by ``last_seen_at`` descending (the newest sync that
        still saw the pair first) and then ``id`` (the earlier row
        first), limited to ``limit`` rows.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM erp_code_clashes "
                    "WHERE resolved_at IS NULL "
                    "ORDER BY last_seen_at DESC, id ASC "
                    "LIMIT %s",
                    (limit,),
                )
                rows = cur.fetchall()
            conn.commit()
            return [dict(row) for row in rows]
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def resolve_unoffered(
        self,
        refused_source: str,
        held_pairs: set[tuple[str, str]],
    ) -> int:
        """Set ``resolved_at`` to now on this source's still-open clash
        rows whose pair the sync did not hold this time.

        Runs on every sync — including one that holds no pair at all —
        so a clash whose pair the sync no longer offers closes itself
        even when the sync result carries no ``clashes`` key.  Rows
        whose pair is in ``held_pairs`` are left open: the sync's
        record step refreshes them.  Returns the number of rows
        resolved.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                held_pairs = set(held_pairs)
                if held_pairs:
                    placeholders = ", ".join("(%s, %s)" for _ in held_pairs)
                    pair_values = [
                        value for pair in sorted(held_pairs) for value in pair
                    ]
                    cur.execute(
                        "UPDATE erp_code_clashes SET resolved_at = now() "
                        "WHERE refused_source = %s AND resolved_at IS NULL "
                        "AND (item_code, site_code) NOT IN "
                        "(SELECT * FROM (VALUES "
                        f"{placeholders}) AS v(item_code, site_code))",
                        (refused_source, *pair_values),
                    )
                else:
                    cur.execute(
                        "UPDATE erp_code_clashes SET resolved_at = now() "
                        "WHERE refused_source = %s AND resolved_at IS NULL",
                        (refused_source,),
                    )
                resolved = cur.rowcount
            conn.commit()
            return resolved
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
