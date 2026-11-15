"""PostgreSQL repository for ddmrp_buffers.

A DDMRP buffer designation records that a human designated one
item-site as a DDMRP buffer item.  The (item_code, site_code) pair
is the primary key, so a pair has at most one stored designation:
storing a designation for a pair that already has one replaces the
stored row, and removing it deletes the row.

The table has no foreign key to item_sites (and no ON DELETE
CASCADE): the item-site's existence is validated by the API at the
application level, never by the database.  A designation therefore
remains visible when its item-site row is absent; it is removed only
by the admin DELETE.
"""

import psycopg2.extras

from app.db.connection import get_connection, get_cursor


class DdmrpBufferPostgresRepository:
    """PostgreSQL repository for the ddmrp_buffers table."""

    def list_all(self) -> list[dict]:
        """Every stored designation, ordered by item_code then site_code."""
        with get_cursor() as cur:
            cur.execute("SELECT * FROM ddmrp_buffers " "ORDER BY item_code, site_code")
            return [dict(row) for row in cur.fetchall()]

    def get(self, item_code: str, site_code: str) -> dict | None:
        """The one stored designation of one (item_code, site_code) pair,
        or None when the pair has no stored designation."""
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM ddmrp_buffers "
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
        designated_by: str,
        designated_by_name: str | None,
        note: str | None,
    ) -> dict:
        """Store the designation of one pair, or replace the stored one.

        designated_by stores the token subject and designated_by_name
        the caller's display name (null when the caller has none).
        designated_at is left to the column's server default (now()) on
        an insert; a replacement for the same pair updates the stored
        row in place (who designated it, the display name and the note,
        and the designation time) instead of adding a second row.  The
        insert-or-replace decision is made atomically by ON CONFLICT
        on the (item_code, site_code) primary key.  Returns the stored
        row.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO ddmrp_buffers "
                    "(item_code, site_code, designated_by, designated_by_name, note) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT (item_code, site_code) DO UPDATE SET "
                    "designated_by = EXCLUDED.designated_by, "
                    "designated_by_name = EXCLUDED.designated_by_name, "
                    "designated_at = now(), "
                    "note = EXCLUDED.note "
                    "RETURNING *",
                    (item_code, site_code, designated_by, designated_by_name, note),
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
        """Remove the stored designation of one pair.

        Returns True when a stored designation was removed and False
        when the pair had none.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ddmrp_buffers "
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
