"""PostgreSQL repository for draft_po_actions — persistent audit of
draft-PO confirms.

One append-only row per confirmed draft-PO request: the actor (token
subject and display name), the request's item/site/supplier/quantity/
unit_price, the translated item/site/supplier target codes the Odoo
client was called with, and the outcome (``created`` /
``already_existed`` with the Odoo order's id and name, or ``error``
with the raised exception's class name).  Rows are never changed or
deleted.  ``created_at`` is written by the application as an aware UTC
datetime and returned as an ISO-8601 string, as the other repositories
do.
"""

from datetime import UTC, datetime

from app.db.connection import get_cursor


def _row_to_dict(row) -> dict:
    d = dict(row)
    if d.get("created_at") and isinstance(d["created_at"], datetime):
        d["created_at"] = d["created_at"].isoformat()
    return d


class DraftPoActionPostgresRepository:
    """PostgreSQL repository for the draft_po_actions table."""

    def create(
        self,
        *,
        actor_sub: str,
        actor_name: str | None,
        item_code: str,
        site_code: str,
        supplier_code: str,
        target_item_code: str | None = None,
        target_site_code: str | None = None,
        target_supplier_code: str | None = None,
        quantity: float,
        unit_price: float,
        outcome: str,
        odoo_id: int | None = None,
        odoo_name: str | None = None,
        error_class: str | None = None,
    ) -> dict:
        """Store one audit row for a confirmed draft-PO request and return it.

        ``created_at`` is set to now (aware UTC) and ``id`` comes from
        the table's identity primary key.  The ``target_*`` codes are
        the codes the Odoo client was actually called with (the
        request's code itself when unmapped) and are recorded
        alongside the request's codes; ``odoo_id`` / ``odoo_name`` are
        set on the ``created`` and ``already_existed`` outcomes and
        ``error_class`` on the ``error`` outcome; the rest are null.
        """
        now = datetime.now(UTC)
        with get_cursor() as cur:
            cur.execute(
                "INSERT INTO draft_po_actions "
                "(created_at, actor_sub, actor_name, item_code, site_code, "
                "supplier_code, target_item_code, target_site_code, "
                "target_supplier_code, quantity, unit_price, outcome, "
                "odoo_id, odoo_name, error_class) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, "
                "%s, %s, %s) "
                "RETURNING *",
                (
                    now,
                    actor_sub,
                    actor_name,
                    item_code,
                    site_code,
                    supplier_code,
                    target_item_code,
                    target_site_code,
                    target_supplier_code,
                    float(quantity),
                    float(unit_price),
                    outcome,
                    odoo_id,
                    odoo_name,
                    error_class,
                ),
            )
            return _row_to_dict(cur.fetchone())

    def list_recent(self, limit: int) -> list[dict]:
        """The newest ``limit`` audit rows, newest ``created_at`` first.

        Ordered by ``created_at`` DESC, ``id`` DESC: rows with equal
        ``created_at`` order by primary key descending, so an
        equal-timestamp tie is deterministic (insertion order
        reversed).
        """
        with get_cursor() as cur:
            cur.execute(
                "SELECT * FROM draft_po_actions "
                "ORDER BY created_at DESC, id DESC LIMIT %s",
                (limit,),
            )
            return [_row_to_dict(r) for r in cur.fetchall()]
