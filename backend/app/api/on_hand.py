"""Read-only API for on_hand."""

# parity: backend-only on-hand balances the recommendations read

from fastapi import APIRouter, HTTPException, Query, status

from app.api.validation import reject_nul_bytes
from app.models.on_hand import OnHandResponse
from app.repositories.on_hand_postgres_repository import OnHandPostgresRepository

router = APIRouter(prefix="/api/on-hand", tags=["on-hand"])
_repo = OnHandPostgresRepository()


@router.get("", response_model=list[OnHandResponse])
def list_on_hand(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000_000),
    item_code: str | None = Query(default=None, max_length=64),
    site_code: str | None = Query(default=None, max_length=64),
) -> list[dict]:
    """List on-hand rows ordered by (item_code, site_code)."""
    reject_nul_bytes(
        item_code, site_code, detail="on-hand filters must not contain NUL bytes"
    )
    return _repo.list_rows(
        limit,
        offset,
        item_code=item_code,
        site_code=site_code,
    )


@router.get("/by-key", response_model=OnHandResponse)
def get_on_hand(
    item_code: str = Query(..., max_length=64),
    site_code: str = Query(..., max_length=64),
) -> dict:
    """Fetch one on-hand row by (item_code, site_code)."""
    reject_nul_bytes(
        item_code, site_code, detail="on-hand filters must not contain NUL bytes"
    )
    row = _repo.get_row(item_code, site_code)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="on-hand row not found",
        )
    return row
