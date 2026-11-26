"""Read-only API for item_sites."""

# parity: backend-only item-site master data kept in step with the ERP, the system of record

from fastapi import APIRouter, HTTPException, Query, status

from app.api.validation import reject_nul_bytes
from app.models.item_sites import ItemSiteResponse
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

router = APIRouter(prefix="/api/item-sites", tags=["item-sites"])
_repo = ItemSitePostgresRepository()


@router.get("", response_model=list[ItemSiteResponse])
def list_item_sites(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    item_code: str | None = Query(default=None),
    site_code: str | None = Query(default=None),
) -> list[dict]:
    """List item/site rows ordered by (item_code, site_code)."""
    reject_nul_bytes(
        item_code,
        site_code,
        detail="item_code and site_code must not contain NUL bytes",
    )
    return _repo.list_rows(limit, offset, item_code=item_code, site_code=site_code)


@router.get("/by-key", response_model=ItemSiteResponse)
def get_item_site_by_key(
    item_code: str = Query(...),
    site_code: str = Query(...),
) -> dict:
    """Fetch the single row for one (item_code, site_code) pair."""
    reject_nul_bytes(
        item_code,
        site_code,
        detail="item_code and site_code must not contain NUL bytes",
    )
    row = _repo.get_by_key(item_code, site_code)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="item site not found"
        )
    return row
