"""PostgreSQL repository for empirical_adoptions.

An empirical adoption records that a human adopted an empirical value
for one item-site.  The (item_code, site_code) pair is the primary
key, so a pair has at most one stored row: storing an adoption for a
pair that already has one replaces the stored row, and withdrawing it
removes the row.

The table has no foreign key to item_sites (and no ON DELETE
CASCADE): the item-site's existence is validated by the API at the
application level, never by the database.
"""

import psycopg2.extras

from app.db.connection import get_connection, get_cursor


class EmpiricalAdoptionPostgresRepository:
    """PostgreSQL repository for the empirical_adoptions table."""

    def list_all(self) -> list[dict]:
        """Every stored adoption, ordered by item_code then site_code."""
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM empirical_adoptions " "ORDER BY item_code, site_code"
            )
            return [dict(row) for row in cur.fetchall()]

    def get(self, item_code: str, site_code: str) -> dict | None:
        """The one stored adoption of one (item_code, site_code) pair,
        or None when the pair has no stored adoption."""
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM empirical_adoptions "
                "WHERE item_code = %s AND site_code = %s",
                (item_code, site_code),
            )
            row = cur.fetchone()
            return dict(row) if row else None

    def upsert(
        self,
        *,
        item_code: str,
        site_code: str,
        adopted_by: str,
        adopted_by_name: str | None,
        note: str | None,
    ) -> dict:
        """Store the adoption of one pair, or replace the stored one.

        adopted_by stores the token subject and adopted_by_name the
        caller's display name (null when the caller has none).
        adopted_at is left to the column's server default (now()) on
        an insert; a replacement for the same pair updates the stored
        row in place (who adopted it, the display name and the note,
        and the adoption time) instead of adding a second row.  The
        insert-or-replace decision is made atomically by ON CONFLICT
        on the (item_code, site_code) primary key.  Returns the stored
        row.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO empirical_adoptions "
                    "(item_code, site_code, adopted_by, adopted_by_name, note) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (item_code, site_code) DO UPDATE SET "
                    "adopted_by = EXCLUDED.adopted_by, "
                    "adopted_by_name = EXCLUDED.adopted_by_name, "
                    "adopted_at = now(), "
                    "note = EXCLUDED.note "
                    "RETURNING *",
                    (item_code, site_code, adopted_by, adopted_by_name, note),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete(self, item_code: str, site_code: str) -> bool:
        """Withdraw the stored adoption of one pair.

        Returns True when a stored adoption was removed and False
        when the pair had none.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM empirical_adoptions "
                    "WHERE item_code = %s AND site_code = %s",
                    (item_code, site_code),
                )
                removed = cur.rowcount > 0
            conn.commit()
            return removed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
