"""Read-only API for lead_time_observations."""

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.models.lead_time_observations import LeadTimeObservationResponse
from app.repositories.lead_time_observations_postgres_repository import (
    LeadTimeObservationPostgresRepository,
)

router = APIRouter(
    prefix="/api/lead-time-observations", tags=["lead-time-observations"]
)
_repo = LeadTimeObservationPostgresRepository()

_NUL = chr(0)


def _reject_nul_bytes(*values: str | None) -> None:
    for value in values:
        if value is not None and _NUL in value:
            raise HTTPException(
                status_code=422,
                detail="lead time observation filters must not contain NUL bytes",
            )


@router.get("", response_model=list[LeadTimeObservationResponse])
def list_lead_time_observations(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000_000),
    supplier_code: str | None = Query(default=None, max_length=64),
    item_code: str | None = Query(default=None, max_length=64),
) -> list[dict]:
    """List lead time observations ordered by business_key."""
    _reject_nul_bytes(supplier_code, item_code)
    return _repo.list_rows(
        limit,
        offset,
        supplier_code=supplier_code,
        item_code=item_code,
    )


@router.get("/{business_key}", response_model=LeadTimeObservationResponse)
def get_lead_time_observation(
    business_key: str = Path(..., max_length=128),
) -> dict:
    """Fetch one lead time observation by its business key (source receipt
    line id)."""
    _reject_nul_bytes(business_key)
    row = _repo.get_by_business_key(business_key)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="lead time observation not found",
        )
    return row
