"""Read-only API for supplier terms."""

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.validation import reject_nul_bytes
from app.repositories.supplier_terms_postgres_repository import (
    SupplierTermsPostgresRepository,
)


class SupplierTermsResponse(BaseModel):
    """Response model for a stored supplier terms row."""

    id: str
    business_key: str
    source_system: str
    item_code: str
    supplier_code: str
    min_order_qty: float | None = None
    max_order_qty: float | None = None
    lot_size: float | None = None
    lead_time_days: float | None = None
    is_default: bool
    synced_at: str


router = APIRouter(prefix="/api/supplier-terms", tags=["supplier-terms"])
_repo = SupplierTermsPostgresRepository()


@router.get("", response_model=list[SupplierTermsResponse])
def list_supplier_terms(
    item_code: str | None = Query(default=None, max_length=64),
    supplier_code: str | None = Query(default=None, max_length=64),
) -> list[dict]:
    """List stored supplier terms ordered by item_code, is_default
    descending, then supplier_code, optionally filtered by item_code and
    supplier_code."""
    reject_nul_bytes(
        item_code,
        supplier_code,
        detail="supplier terms filters must not contain NUL bytes",
    )
    return _repo.list(item_code=item_code, supplier_code=supplier_code)
