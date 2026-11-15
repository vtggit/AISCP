"""Pydantic models for suppliers — the synced vendor read model.

The ERP (and its feed) stays the system of record; AIERP stores a synced
copy of each Acumatica BAccount vendor (``Type`` ``VE``), keyed by
``supplier_code`` (the ``BAccountID`` as text).
"""

from pydantic import BaseModel


class SupplierUpsert(BaseModel):
    """Describes one supplier row as supplied by the sync source."""

    supplier_code: str
    vendor_code: str | None = None
    name: str | None = None
    status: str | None = None
    source_system: str | None = None


class SupplierResponse(SupplierUpsert):
    """Response model for a stored supplier row."""

    id: str
    synced_at: str
