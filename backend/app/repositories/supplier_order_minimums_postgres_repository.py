"""PostgreSQL repository for supplier_order_minimums — per-supplier order minimums.

A minimum row is one (``source_system``, ``supplier_code``) pair —
the table's unique key — so ``upsert`` stores the amount (and bumps
``updated_at`` and ``updated_by``) for an existing pair instead of
inserting a duplicate row, keeping the row's ``id``.  ``list`` returns
the rows in whatever order the database gives them: the route sorts
them itself in Unicode code point order, so the served order does not
depend on the database's collation.  ``updated_at`` is written by the
application as an aware UTC datetime and returned as an ISO-8601
string, as the other repositories do; ``amount`` comes back from the
``NUMERIC`` column as a ``Decimal`` and is returned as a ``float``.
"""

from datetime import UTC, datetime

from app.db.connection import get_cursor


def _row_to_dict(row) -> dict:
    d = dict(row)
    if d.get("amount") is not None:
        d["amount"] = float(d["amount"])
    if d.get("updated_at") and isinstance(d["updated_at"], datetime):
        d["updated_at"] = d["updated_at"].isoformat()
    return d


class SupplierOrderMinimumPostgresRepository:
    """PostgreSQL repository for the supplier_order_minimums table."""

    def list(self) -> list[dict]:
        """Every stored minimum, unsorted (the route sorts them itself)."""
        with get_cursor() as cur:
            cur.execute("SELECT * FROM supplier_order_minimums")
            return [_row_to_dict(r) for r in cur.fetchall()]

    def upsert(
        self,
        source_system: str,
        supplier_code: str,
        amount: float,
        updated_by: str,
    ) -> dict:
        """Store one supplier's minimum and return the stored row.

        A new pair inserts a row; an existing one updates its amount,
        ``updated_at`` and ``updated_by`` — the id is kept, so no
        duplicate row is ever created.
        """
        now = datetime.now(UTC)
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO supplier_order_minimums "
                "(source_system, supplier_code, amount, updated_at, updated_by) "
                "VALUES (%s, %s, %s, %s, %s) "
                "ON CONFLICT (source_system, supplier_code) DO UPDATE "
                "SET amount = EXCLUDED.amount, updated_at = %s, updated_by = %s "
                "RETURNING *",
                (
                    source_system,
                    supplier_code,
                    amount,
                    now,
                    updated_by,
                    now,
                    updated_by,
                ),
            )
            return _row_to_dict(cur.fetchone())

    def delete(self, row_id: int) -> bool:
        """Delete one minimum by id; True when a row was deleted."""
        with get_cursor() as cur:
            cur.execute(
                "DELETE FROM supplier_order_minimums WHERE id = %s",
                (row_id,),
            )
            return cur.rowcount > 0
