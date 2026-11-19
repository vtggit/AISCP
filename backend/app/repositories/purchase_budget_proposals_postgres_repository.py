"""PostgreSQL repository for purchase_budget_proposals — the record
row of every purchase-budget save.

The table records each store of ``PUT
/api/purchase-budgets/{month}`` (issue #1432): one row per save —
append-only, never updated and never deleted — holding the saved
``amount`` and ``saved_by`` (the caller's username), the
``proposed_amount`` the proposals route proposed for the month at
that moment (null when the month was outside the 12-month horizon of
the save's spend read, or when that read raised) and the ``margin``
it was proposed with (null alongside a null proposal), ``saved_at``
the ``now()`` server default of when the save was stored.

``append`` inserts one row and returns it.  ``latest_per_month``
answers the latest row of each month — the highest ``saved_at``, the
``id`` breaking the tie — in one query, in month order.  The
``_insert`` helper runs the row's INSERT on the caller's open
cursor, so the save that writes the budget and the record together
runs it on the budget save's own cursor and the two writes commit
together in one transaction.  The ``NUMERIC`` columns come back from
the database as ``Decimal`` and are returned as ``float``;
``saved_at`` as an ISO-8601 string, as the other repositories do.
"""

from datetime import datetime

from app.db.connection import get_cursor


def _row_to_dict(row) -> dict:
    d = dict(row)
    for key in ("amount", "proposed_amount", "margin"):
        if d.get(key) is not None:
            d[key] = float(d[key])
    if d.get("saved_at") and isinstance(d["saved_at"], datetime):
        d["saved_at"] = d["saved_at"].isoformat()
    return d


def _insert(
    cur,
    month: str,
    amount: float,
    proposed_amount: float | None,
    margin: float | None,
    saved_by: str,
) -> dict:
    """Insert one record row on ``cur`` and return the stored row.

    ``cur`` is the caller's open cursor: ``append`` opens its own,
    and the budget save passes the cursor of the transaction the
    budget itself is stored in, so the two writes commit together.
    """
    cur.execute(
        "INSERT INTO purchase_budget_proposals "
        "(month, amount, proposed_amount, margin, saved_by) "
        "VALUES (%s, %s, %s, %s, %s) "
        "RETURNING *",
        (month, amount, proposed_amount, margin, saved_by),
    )
    return _row_to_dict(cur.fetchone())


class PurchaseBudgetProposalPostgresRepository:
    """PostgreSQL repository for the purchase_budget_proposals table.

    Append-only: the repository inserts and reads record rows, and
    never updates or deletes one.
    """

    def append(
        self,
        month: str,
        amount: float,
        proposed_amount: float | None,
        margin: float | None,
        saved_by: str,
    ) -> dict:
        """Insert one record row and return the stored row."""
        with get_cursor() as cur:
            return _insert(cur, month, amount, proposed_amount, margin, saved_by)

    def latest_per_month(self) -> list[dict]:
        """The latest row of each month, in one query, in month order.

        Latest by ``saved_at``, the ``id`` breaking the tie.
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT DISTINCT ON (month) * "
                "FROM purchase_budget_proposals "
                "ORDER BY month, saved_at DESC, id DESC"
            )
            return [_row_to_dict(row) for row in cur.fetchall()]
