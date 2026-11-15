"""PostgreSQL repository for exception_first_seen — the first-seen
date of every exceptions-list key.

One row per exceptions-list key (the stable ``<kind>:<ref>``
identity ``GET /api/exceptions`` answers): ``first_seen_on`` is the
date the key was first captured — the as-of date of the list the
capture saw, or the capture's current UTC date when that list
reported none.

``list`` answers every stored key with its first-seen date and
``insert_missing`` stores the date for the keys that have none yet —
each inserted with ``ON CONFLICT (key) DO NOTHING``, so a key that
already has a stored date is left untouched: the first-seen date is
never updated or deleted.
"""

from __future__ import annotations

from datetime import date

from app.db.connection import get_connection, get_cursor


class ExceptionFirstSeenPostgresRepository:
    """PostgreSQL repository for the exception_first_seen table."""

    def list(self) -> dict[str, date]:
        """Every stored key with its first-seen date, in key order."""
        with get_cursor() as cur:
            cur.execute(
                'SELECT "key", first_seen_on FROM exception_first_seen '
                'ORDER BY "key"'
            )
            return {row["key"]: row["first_seen_on"] for row in cur.fetchall()}

    def insert_missing(self, keys: list[str], first_seen_on: date) -> int:
        """Store ``first_seen_on`` for the keys that have none yet.

        Each key is inserted with ``ON CONFLICT (key) DO NOTHING``: a
        key that already has a stored date is left untouched — the
        first-seen date is never updated or deleted.  Returns the
        number of keys inserted.
        """
        keys = list(keys)
        if not keys:
            return 0
        conn = get_connection()
        try:
            inserted = 0
            with conn.cursor() as cur:
                for key in keys:
                    cur.execute(
                        "INSERT INTO exception_first_seen "
                        '("key", first_seen_on) '
                        "VALUES (%s, %s) "
                        "ON CONFLICT (key) DO NOTHING",
                        (key, first_seen_on),
                    )
                    inserted += cur.rowcount
            conn.commit()
            return inserted
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
