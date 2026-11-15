"""Read-only API for suppliers."""

from fastapi import APIRouter

from app.models.suppliers import SupplierResponse
from app.repositories.suppliers_postgres_repository import (
    SupplierPostgresRepository,
)

router = APIRouter(prefix="/api/suppliers", tags=["suppliers"])
_repo = SupplierPostgresRepository()


@router.get("", response_model=list[SupplierResponse])
def list_suppliers() -> list[dict]:
    """List every stored supplier ordered by vendor_code."""
    return _repo.list_all()
