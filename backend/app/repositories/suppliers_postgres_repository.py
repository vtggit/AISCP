"""PostgreSQL repository for suppliers."""

from datetime import UTC, datetime
from uuid import uuid4

from app.db.connection import get_connection, get_cursor

_DATA_COLUMNS = ("vendor_code", "name", "status", "source_system")


def _row_to_dict(row) -> dict:
    d = dict(row)
    value = d.get("synced_at")
    if isinstance(value, datetime):
        d["synced_at"] = value.isoformat()
    return d


class SupplierPostgresRepository:
    """PostgreSQL repository for the suppliers table."""

    def upsert_many(self, rows: list[dict]) -> dict:
        """Insert each row or, when supplier_code already exists,
        update every other column and set synced_at to now.

        Every row is written in one transaction. The insert-or-update
        decision is made atomically by ON CONFLICT, so concurrent calls
        racing on the same new key serialize on the unique constraint and
        resolve as one insert plus one update instead of a unique
        violation. Returns the counts {"inserted": n, "updated": m}.
        """
        inserted = 0
        updated = 0
        conn = get_connection()
        try:
            with conn.cursor() as cur:
                for row in rows:
                    now = datetime.now(UTC)
                    data_values = tuple(row.get(column) for column in _DATA_COLUMNS)
                    cur.execute(
                        "INSERT INTO suppliers "
                        "(id, supplier_code, vendor_code, name, status, "
                        "source_system, synced_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (supplier_code) DO UPDATE SET "
                        "vendor_code = EXCLUDED.vendor_code, "
                        "name = EXCLUDED.name, "
                        "status = EXCLUDED.status, "
                        "source_system = EXCLUDED.source_system, "
                        "synced_at = %s "
                        "RETURNING (xmax = 0) AS inserted",
                        (
                            str(uuid4()),
                            row.get("supplier_code"),
                            *data_values,
                            now,
                            now,
                        ),
                    )
                    if cur.fetchone()[0]:
                        inserted += 1
                    else:
                        updated += 1
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"inserted": inserted, "updated": updated}

    def list_all(self) -> list[dict]:
        """Every stored supplier ordered by vendor_code."""
        with get_cursor() as cur:
            cur.execute("SELECT * FROM suppliers ORDER BY vendor_code")
            return [_row_to_dict(r) for r in cur.fetchall()]
