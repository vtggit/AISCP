"""Read-only API for item_sites."""

from fastapi import APIRouter, HTTPException, Query, status

from app.models.item_sites import ItemSiteResponse
from app.repositories.item_sites_postgres_repository import (
    ItemSitePostgresRepository,
)

router = APIRouter(prefix="/api/item-sites", tags=["item-sites"])
_repo = ItemSitePostgresRepository()

_NUL = chr(0)


def _reject_nul_bytes(*values: str | None) -> None:
    for value in values:
        if value is not None and _NUL in value:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="item_code and site_code must not contain NUL bytes",
            )


@router.get("", response_model=list[ItemSiteResponse])
def list_item_sites(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    item_code: str | None = Query(default=None),
    site_code: str | None = Query(default=None),
) -> list[dict]:
    """List item/site rows ordered by (item_code, site_code)."""
    _reject_nul_bytes(item_code, site_code)
    return _repo.list_rows(limit, offset, item_code=item_code, site_code=site_code)


@router.get("/by-key", response_model=ItemSiteResponse)
def get_item_site_by_key(
    item_code: str = Query(...),
    site_code: str = Query(...),
) -> dict:
    """Fetch the single row for one (item_code, site_code) pair."""
    _reject_nul_bytes(item_code, site_code)
    row = _repo.get_by_key(item_code, site_code)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="item site not found"
        )
    return row
