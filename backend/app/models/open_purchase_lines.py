"""Pydantic models for open_purchase_lines — open purchase quantities by line.

Each row is the latest open quantity the ERP reported for one purchase
order line, keyed by business_key, the idempotency key of the sync.
as_of is the snapshot time the ERP data is current as of, and
last-fresh-wins: a snapshot with an older as_of never overwrites a newer
stored row. open_units is the quantity still open (not yet received) and
drops to zero when the line is fully received.
"""

from datetime import date

from pydantic import BaseModel


class OpenPurchaseLineUpsert(BaseModel):
    """Describes one open_purchase_lines row as supplied by the sync source."""

    item_code: str
    site_code: str
    supplier_code: str | None = None
    order_type: str
    open_units: float
    promised_on: date | None = None
    ordered_on: date | None = None
    as_of: str
    source_system: str | None = None


class OpenPurchaseLineResponse(OpenPurchaseLineUpsert):
    """Response model for a stored open_purchase_lines row."""

    id: str
    business_key: str
    synced_at: str
