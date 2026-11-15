"""Pydantic models for demand_events — observed demand occurrences.

Each event records one unit of observed demand (a shipment, a production
issue, or a kit issue) for an item at a site on a given date. Rows are
synced from the ERP by business_key, the source transaction line id, which
is the idempotency key of the sync.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel

EventTypeName = Literal["shipment", "production_issue", "kit_issue"]


class DemandEventUpsert(BaseModel):
    """Describes one demand event row as supplied by the sync source."""

    business_key: str
    item_code: str
    site_code: str
    quantity: float
    occurred_on: date
    event_type: EventTypeName
    source_system: str | None = None


class DemandEventResponse(DemandEventUpsert):
    """Response model for a stored demand event row."""

    id: str
    synced_at: str
