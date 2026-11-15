"""Pydantic models for inventory_items — AIERP's item master read model.

An inventory item mirrors the ERP's stock item: AIERP reads it, never owns it.
The ERP remains authoritative for identity, description, and unit of measure.
"""

from pydantic import BaseModel


class InventoryItemCreate(BaseModel):
    """Payload for creating an inventory item."""

    item_code: str
    description: str | None = None
    uom: str | None = None
    source_system: str | None = None


class InventoryItemUpdate(BaseModel):
    """Payload for updating an inventory item; every field optional."""

    item_code: str | None = None
    description: str | None = None
    uom: str | None = None
    source_system: str | None = None


class InventoryItemResponse(BaseModel):
    """Response model for an inventory item record."""

    id: str
    item_code: str
    description: str | None = None
    uom: str | None = None
    source_system: str | None = None
    created_at: str
    updated_at: str
