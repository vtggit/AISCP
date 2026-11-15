"""Pydantic models for lead_time_observations — observed PO receipt lead times.

Each observation records one purchase-order receipt line: how many days
elapsed between the order date (POLine.OrderDate) and the receipt date
(POReceiptLine.ReceiptDate) for a supplier and an item. Rows are synced
from the ERP by business_key, the source receipt line id
(ReceiptType:ReceiptNbr:LineNbr), which is the idempotency key of the sync.
lead_time_days is computed by the database as received_on - ordered_on and
is never written by the sync.
"""

from datetime import date

from pydantic import BaseModel


class LeadTimeObservationUpsert(BaseModel):
    """Describes one lead time observation row as supplied by the sync source."""

    business_key: str
    supplier_code: str
    item_code: str
    site_code: str | None = None
    ordered_on: date
    received_on: date
    promised_on: date | None = None
    source_system: str | None = None


class LeadTimeObservationResponse(LeadTimeObservationUpsert):
    """Response model for a stored lead time observation row."""

    id: str
    lead_time_days: int
    synced_at: str
