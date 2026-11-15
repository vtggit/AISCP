"""PostgreSQL repository for inventory_items."""

from datetime import UTC, datetime
from uuid import uuid4

from app.db.connection import get_connection, get_cursor

_DATA_COLUMNS = ("description", "uom", "source_system")


def _generate_id() -> str:
    return str(uuid4())


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("created_at", "updated_at"):
        if d.get(key) and isinstance(d[key], datetime):
            d[key] = d[key].isoformat()
    return d


class InventoryItemPostgresRepository:
    """PostgreSQL repository for the inventory_items table."""

    def upsert_many(self, rows: list[dict]) -> dict:
        """Insert each row or, when item_code already exists, update the
        other columns and stamp updated_at to now.

        The upsert keys on ``item_code`` (the unique index of
        0024_inventory_items_unique_code), setting ``description``,
        ``uom`` and ``source_system`` from the row, for the row's own
        source system only.  Every row is written in one transaction.
        The insert-or-update decision is made atomically by ON
        CONFLICT, so concurrent calls racing on the same new key
        serialize on the unique index and resolve as one insert plus
        one update instead of a unique violation.  The update runs
        only when the stored row's source_system matches the offered
        one (IS NOT DISTINCT FROM, so a null matches a null): a
        conflict stored under another source_system is refused,
        leaves the stored row untouched, and the offer counts in
        neither "inserted" nor "updated".  Returns the counts
        {"inserted": n, "updated": m}; "refused", the number of
        refused rows, is present only when it is above 0.
        """
        inserted = 0
        updated = 0
        refused = 0
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for row in rows:
                    now = datetime.now(UTC)
                    data_values = tuple(row.get(column) for column in _DATA_COLUMNS)
                    cur.execute(
                        "INSERT INTO inventory_items "
                        "(id, item_code, description, uom, source_system, "
                        "created_at, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (item_code) DO UPDATE SET "
                        "description = EXCLUDED.description, "
                        "uom = EXCLUDED.uom, "
                        "source_system = EXCLUDED.source_system, "
                        "updated_at = %s "
                        "WHERE inventory_items.source_system "
                        "IS NOT DISTINCT FROM EXCLUDED.source_system "
                        "RETURNING (xmax = 0) AS inserted",
                        (
                            str(uuid4()),
                            row.get("item_code"),
                            *data_values,
                            now,
                            now,
                            now,
                        ),
                    )
                    fetched = cur.fetchone()
                    if fetched is None:
                        # Another ERP's row: the stored row's
                        # source_system differs from the offered one, so
                        # the update was skipped and the row is untouched.
                        refused += 1
                        continue
                    if fetched[0]:
                        inserted += 1
                    else:
                        updated += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        result = {"inserted": inserted, "updated": updated}
        if refused:
            result["refused"] = refused
        return result

    def held_by_others(self, source_system: str, item_codes: list[str]) -> dict:
        """For the given item codes, the source_system that stores each in
        inventory_items under a different source_system.

        Every code is answered by one SQL statement, so the held set is
        a single consistent snapshot: codes stored under ``source_system``
        itself (or not stored at all) are absent from the result, and the
        answer maps each held code to the holder.
        """
        if not item_codes:
            return {}
        placeholders = ", ".join("%s" for _ in item_codes)
        with get_cursor() as cur:
            cur.execute(
                "SELECT item_code, source_system FROM inventory_items "
                f"WHERE item_code IN ({placeholders}) "
                "AND source_system <> %s",
                (*item_codes, source_system),
            )
            return {row["item_code"]: row["source_system"] for row in cur.fetchall()}

    def list_all(self) -> list[dict]:
        with get_cursor() as cur:
            cur.execute("SELECT * FROM inventory_items ORDER BY created_at DESC")
            return [_row_to_dict(r) for r in cur.fetchall()]

    def get_by_id(self, entity_id: str) -> dict | None:
        with get_cursor() as cur:
            cur.execute("SELECT * FROM inventory_items WHERE id = %s", (entity_id,))
            row = cur.fetchone()
            return _row_to_dict(row) if row else None

    def create(self, data: dict) -> dict:
        now = datetime.now(UTC)
        new_id = _generate_id()
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO inventory_items "
                "(id, item_code, description, uom, source_system, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (
                    new_id,
                    data.get("item_code"),
                    data.get("description"),
                    data.get("uom"),
                    data.get("source_system"),
                    now,
                    now,
                ),
            )
            return _row_to_dict(cur.fetchone())

    def update(self, entity_id: str, data: dict) -> dict | None:
        fields = [
            k
            for k in ("item_code", "description", "uom", "source_system")
            if k in data and data[k] is not None
        ]
        if not fields:
            return self.get_by_id(entity_id)
        assignments = ", ".join(f"{f} = %s" for f in fields)
        values = [data[f] for f in fields]
        values.append(datetime.now(UTC))
        values.append(entity_id)
        with get_cursor() as cur:
            cur.execute(
                f"UPDATE inventory_items SET {assignments}, updated_at = %s WHERE id = %s "
                "RETURNING *",
                tuple(values),
            )
            row = cur.fetchone()
            return _row_to_dict(row) if row else None

    def delete(self, entity_id: str) -> bool:
        with get_cursor() as cur:
            cur.execute("DELETE FROM inventory_items WHERE id = %s", (entity_id,))
            return cur.rowcount > 0
