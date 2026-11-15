"""PostgreSQL repository for the settings and settings_changes tables.

``settings`` holds the stored value of one registered setting per
(scope, owner, key) — owner is the empty string for a system setting and
the token subject for a user setting.  ``settings_changes`` is the
append-only audit trail: one row per change (store or reset), with the
value stored before the change (null when the default applied) and the
new value (null on a reset).  A store and its change row are written in
one transaction, so the audit trail never drifts from the stored value.
"""

from typing import Any

import psycopg2
import psycopg2.extras

from app.db.connection import get_connection, get_cursor


class SettingsPostgresRepository:
    """PostgreSQL repository for the settings tables."""

    def get(self, scope: str, owner: str, key: str) -> dict | None:
        """The stored row of one setting, or None while the default applies."""
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM settings "
                "WHERE scope = %s AND owner = %s AND key = %s",
                (scope, owner, key),
            )
            row = cur.fetchone()
            return dict(row) if row is not None else None

    def store(
        self,
        *,
        scope: str,
        owner: str,
        key: str,
        value: Any,
        updated_by: str,
        updated_by_name: str | None,
        old_value: Any,
    ) -> dict:
        """Store one value and append its change row, in one transaction.

        updated_by stores the token subject and updated_by_name the
        caller's display name (null when the caller has none); the
        change row keeps the same pair as changed_by /
        changed_by_name.  The value is upserted on (scope, owner, key)
        and, in the same transaction, a change row is appended with
        old_value (the value stored before the change, None when the
        default applied) and new_value set to the stored value.
        Returns the change row, in the order the database assigned it.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "INSERT INTO settings "
                    "(scope, owner, key, value, updated_by, updated_by_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (scope, owner, key) DO UPDATE "
                    "SET value = EXCLUDED.value, "
                    "    updated_by = EXCLUDED.updated_by, "
                    "    updated_by_name = EXCLUDED.updated_by_name, "
                    "    updated_at = now() "
                    "RETURNING *",
                    (
                        scope,
                        owner,
                        key,
                        psycopg2.extras.Json(value),
                        updated_by,
                        updated_by_name,
                    ),
                )
                cur.execute(
                    "INSERT INTO settings_changes "
                    "(scope, owner, key, old_value, new_value, changed_by, "
                    "changed_by_name) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                    "RETURNING *",
                    (
                        scope,
                        owner,
                        key,
                        (
                            psycopg2.extras.Json(old_value)
                            if old_value is not None
                            else None
                        ),
                        psycopg2.extras.Json(value),
                        updated_by,
                        updated_by_name,
                    ),
                )
                change = cur.fetchone()
            conn.commit()
            return dict(change)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def reset(
        self,
        *,
        scope: str,
        owner: str,
        key: str,
        changed_by: str,
        changed_by_name: str | None,
        old_value: Any,
    ) -> dict:
        """Reset one stored value to its default and append its change row.

        changed_by stores the token subject and changed_by_name the
        caller's display name (null when the caller has none).
        Deletes the stored row (if any) and, in the same transaction,
        appends a change row with old_value set to the value that was
        stored and new_value NULL.  The caller only invokes this when a
        stored row exists; it returns the change row.
        """
        conn = get_connection()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "DELETE FROM settings "
                    "WHERE scope = %s AND owner = %s AND key = %s",
                    (scope, owner, key),
                )
                cur.execute(
                    "INSERT INTO settings_changes "
                    "(scope, owner, key, old_value, new_value, changed_by, "
                    "changed_by_name) "
                    "VALUES (%s, %s, %s, %s, NULL, %s, %s) "
                    "RETURNING *",
                    (
                        scope,
                        owner,
                        key,
                        psycopg2.extras.Json(old_value),
                        changed_by,
                        changed_by_name,
                    ),
                )
                change = cur.fetchone()
            conn.commit()
            return dict(change)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def changes(self) -> list[dict]:
        """Every change row, in the order the changes happened (id ASC)."""
        with get_cursor() as cur:
            cur.execute("SELECT * FROM settings_changes ORDER BY id ASC")
            return [dict(row) for row in cur.fetchall()]

    def stored_rows(self) -> list[dict]:
        """Every stored setting row."""
        with get_cursor() as cur:
            cur.execute("SELECT * FROM settings ORDER BY scope, owner, key")
            return [dict(row) for row in cur.fetchall()]
