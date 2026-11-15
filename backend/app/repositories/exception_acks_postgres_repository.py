"""PostgreSQL repository for exception_acks — the planner's
acknowledgements of the exceptions triage list.

One row per exceptions-list key (the stable ``<kind>:<ref>``
identity ``GET /api/exceptions`` answers): ``upsert`` stores or
replaces the acknowledgement of a key (the replace sets the new
acknowledging planner's subject and name, a fresh ``acknowledged_at``
and the request's snooze and note — null when the request omits one),
``list`` answers every stored acknowledgement and ``delete`` removes
the acknowledgement of a key.
"""

from datetime import date

import psycopg2
import psycopg2.extras

from app.db.connection import get_connection, get_cursor


class ExceptionAcksPostgresRepository:
    """PostgreSQL repository for the exception_acks table."""

    def list(self) -> list[dict]:
        """Every stored acknowledgement row, in key order."""
        with get_cursor() as cur:
            cur.execute('SELECT * FROM exception_acks ORDER BY "key"')
            return [dict(row) for row in cur.fetchall()]

    def upsert(
        self,
        *,
        key: str,
        acknowledged_by: str,
        acknowledged_by_name: str | None,
        snooze_until: date | None,
        note: str | None,
    ) -> dict:
        """Store or replace the acknowledgement of one key.

        ``acknowledged_by`` stores the token subject and
        ``acknowledged_by_name`` its display name (null when it has
        none); ``snooze_until`` and ``note`` are the request's values,
        null when the request omits one.  A second acknowledgement of
        the key replaces the row: the new subject and name, a fresh
        ``acknowledged_at`` (``now()``) and the request's snooze and
        note.  Returns the stored row.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO exception_acks "
                    '("key", acknowledged_by, acknowledged_by_name, '
                    "snooze_until, note) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    'ON CONFLICT ("key") DO UPDATE '
                    "SET acknowledged_by = EXCLUDED.acknowledged_by, "
                    "    acknowledged_by_name = EXCLUDED.acknowledged_by_name, "
                    "    acknowledged_at = now(), "
                    "    snooze_until = EXCLUDED.snooze_until, "
                    "    note = EXCLUDED.note "
                    "RETURNING *",
                    (key, acknowledged_by, acknowledged_by_name, snooze_until, note),
                )
                row = cur.fetchone()
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def delete(self, key: str) -> bool:
        """Delete the acknowledgement of one key.

        Returns True when a stored row was deleted, False when the key
        carries no acknowledgement.
        """
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                cur.execute('DELETE FROM exception_acks WHERE "key" = %s', (key,))
                deleted = cur.rowcount > 0
            conn.commit()
            return deleted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
