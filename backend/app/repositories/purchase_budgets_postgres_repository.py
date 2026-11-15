"""PostgreSQL repository for purchase_budgets — per-month purchase budgets.

A budget row caps one calendar month: ``month`` is the table's unique
key, so ``upsert`` stores the amount (and bumps ``updated_at`` and
``updated_by``) for an existing month instead of inserting a
duplicate row.  ``updated_at`` is written by the application as an
aware UTC datetime and returned as an ISO-8601 string, as the other
repositories do; ``amount`` comes back from the ``NUMERIC`` column as
a ``Decimal`` and is returned as a ``float``.
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


class PurchaseBudgetPostgresRepository:
    """PostgreSQL repository for the purchase_budgets table."""

    def list(self) -> list[dict]:
        """Every stored budget, in month order."""
        with get_cursor() as cur:
            cur.execute("SELECT * FROM purchase_budgets ORDER BY month")
            return [_row_to_dict(r) for r in cur.fetchall()]

    def upsert(self, month: str, amount: float, updated_by: str) -> dict:
        """Store one month's budget and return the stored row.

        A new month inserts a row; an existing one updates its amount,
        ``updated_at`` and ``updated_by`` — the id is kept, so no
        duplicate row is ever created.
        """
        now = datetime.now(UTC)
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO purchase_budgets "
                "(month, amount, updated_at, updated_by) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (month) DO UPDATE "
                "SET amount = EXCLUDED.amount, updated_at = %s, updated_by = %s "
                "RETURNING *",
                (month, amount, now, updated_by, now, updated_by),
            )
            return _row_to_dict(cur.fetchone())

    def delete(self, month: str) -> bool:
        """Delete one month's budget; True when a row was deleted."""
        with get_cursor() as cur:
            cur.execute("DELETE FROM purchase_budgets WHERE month = %s", (month,))
            return cur.rowcount > 0
