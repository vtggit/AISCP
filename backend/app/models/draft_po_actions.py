"""Pydantic models for draft_po_actions — persistent audit of draft-PO confirms.

One row is stored for every ``confirm=true`` request to
``POST /api/replenishment/actions/draft-po`` — the actor (token subject
and display name), the request's item/site/supplier/quantity/unit_price,
the translated item/site/supplier target codes the Odoo client was
actually called with (``target_item_code``, ``target_site_code``,
``target_supplier_code`` — equal to the request's code when it was
unmapped), and the outcome: ``created`` or ``already_existed`` (with
the Odoo order's id and name) or ``error`` (with the raised
exception's class name).  A ``confirm=false`` dry run stores no row.
No credential, environment value, or Odoo payload is stored.
"""

from pydantic import BaseModel


class DraftPoAction(BaseModel):
    """One stored draft-PO confirm audit row.

    ``created_at`` is the ISO-8601 rendering of the row's timestamptz
    column; ``actor_name``, ``target_item_code``, ``target_site_code``,
    ``target_supplier_code``, ``odoo_id``, ``odoo_name`` and
    ``error_class`` are null wherever the row does not carry them (the
    target codes are null only on rows written before the target
    columns existed).
    """

    id: int
    created_at: str
    actor_sub: str
    actor_name: str | None = None
    item_code: str
    site_code: str
    supplier_code: str
    target_item_code: str | None = None
    target_site_code: str | None = None
    target_supplier_code: str | None = None
    quantity: float
    unit_price: float
    outcome: str
    odoo_id: int | None = None
    odoo_name: str | None = None
    error_class: str | None = None
