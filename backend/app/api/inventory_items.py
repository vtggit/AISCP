"""CRUD API for inventory_items.

The write routes (``POST``, ``PUT``, ``DELETE``) take
``current_user: AuthUser = Depends(require_admin)`` — the same
dependency ``app.api.purchase_budgets`` uses — so a signed-in
non-admin is refused with 403 before anything is written, while an
admin gets exactly today's behaviour; the ``GET`` routes stay open to
any signed-in user.
"""

# parity: backend-only item master data kept in step with the ERP, the system of record

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.erp_mappings import require_admin
from app.auth.models import AuthUser
from app.models.inventory_items import (
    InventoryItemCreate,
    InventoryItemResponse,
    InventoryItemUpdate,
)
from app.repositories.inventory_items_postgres_repository import (
    InventoryItemPostgresRepository,
)

router = APIRouter(prefix="/api/inventory-items", tags=["inventory-items"])
_repo = InventoryItemPostgresRepository()


@router.get("", response_model=list[InventoryItemResponse])
def list_inventory_items() -> list[dict]:
    """List every inventory item."""
    return _repo.list_all()


@router.post(
    "", response_model=InventoryItemResponse, status_code=status.HTTP_201_CREATED
)
def create_inventory_item(
    payload: InventoryItemCreate,
    current_user: AuthUser = Depends(require_admin),
) -> dict:
    """Create an inventory item."""
    return _repo.create(payload.model_dump())


@router.get("/{entity_id}", response_model=InventoryItemResponse)
def get_inventory_item(entity_id: str) -> dict:
    """Fetch one inventory item by id."""
    row = _repo.get_by_id(entity_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="inventory item not found"
        )
    return row


@router.put("/{entity_id}", response_model=InventoryItemResponse)
def update_inventory_item(
    entity_id: str,
    payload: InventoryItemUpdate,
    current_user: AuthUser = Depends(require_admin),
) -> dict:
    """Update an inventory item."""
    row = _repo.update(entity_id, payload.model_dump(exclude_unset=True))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="inventory item not found"
        )
    return row


@router.delete("/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_inventory_item(
    entity_id: str,
    current_user: AuthUser = Depends(require_admin),
) -> None:
    """Delete an inventory item."""
    if not _repo.delete(entity_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="inventory item not found"
        )
