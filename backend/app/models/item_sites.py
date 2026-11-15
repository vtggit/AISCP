"""Pydantic models for item_sites — per-item, per-site planning parameters.

The ERP (and its feed) stays the system of record; AIERP stores a synced copy
of each item's planning parameters at each site, keyed by
(item_code, site_code).
"""

from pydantic import BaseModel


class ItemSiteUpsert(BaseModel):
    """Describes one item/site row as supplied by the sync source."""

    item_code: str
    site_code: str
    unit_cost: float | None = None
    reorder_point: float | None = None
    safety_stock: float | None = None
    max_qty: float | None = None
    order_qty: float | None = None
    master_lead_time_days: float | None = None
    source_system: str | None = None


class ItemSiteResponse(ItemSiteUpsert):
    """Response model for a stored item/site row."""

    id: str
    synced_at: str
