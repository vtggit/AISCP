"""PostgreSQL repository for supplier terms."""

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from app.db.connection import get_connection, get_cursor

_DATA_COLUMNS = (
    "item_code",
    "supplier_code",
    "min_order_qty",
    "max_order_qty",
    "lot_size",
    "lead_time_days",
    "is_default",
)


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("min_order_qty", "max_order_qty", "lot_size", "lead_time_days"):
        value = d.get(key)
        if isinstance(value, Decimal):
            d[key] = float(value)
    value = d.get("synced_at")
    if isinstance(value, datetime):
        d["synced_at"] = value.isoformat()
    return d


class SupplierTermsPostgresRepository:
    """PostgreSQL repository for the supplier_terms table."""

    def upsert_many(self, rows: list[dict]) -> dict:
        """Insert each row or, when the (source_system, business_key)
        pair already exists, update every other column and set synced_at
        to now.

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
                        "INSERT INTO supplier_terms "
                        "(id, business_key, source_system, item_code, "
                        "supplier_code, min_order_qty, max_order_qty, "
                        "lot_size, lead_time_days, is_default, synced_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (source_system, business_key) DO UPDATE SET "
                        "item_code = EXCLUDED.item_code, "
                        "supplier_code = EXCLUDED.supplier_code, "
                        "min_order_qty = EXCLUDED.min_order_qty, "
                        "max_order_qty = EXCLUDED.max_order_qty, "
                        "lot_size = EXCLUDED.lot_size, "
                        "lead_time_days = EXCLUDED.lead_time_days, "
                        "is_default = EXCLUDED.is_default, "
                        "synced_at = %s "
                        "RETURNING (xmax = 0) AS inserted",
                        (
                            str(uuid4()),
                            row.get("business_key"),
                            row.get("source_system"),
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

    def list(self, item_code=None, supplier_code=None) -> list[dict]:
        """Stored supplier terms as dicts, optionally filtered by
        item_code and/or supplier_code, ordered by item_code, then
        is_default descending, then supplier_code. Dates come back as
        ISO strings and an empty table returns ``[]``.
        """
        query = "SELECT * FROM supplier_terms"
        clauses = []
        params = []
        if item_code is not None:
            clauses.append("item_code = %s")
            params.append(item_code)
        if supplier_code is not None:
            clauses.append("supplier_code = %s")
            params.append(supplier_code)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY item_code, is_default DESC, supplier_code"
        with get_cursor() as cur:
            cur.execute(query, params)
            return [_row_to_dict(r) for r in cur.fetchall()]
