"""Read-only API for demand_events."""

# parity: backend-only raw demand events, the input the forecast reads

from datetime import date

from fastapi import APIRouter, HTTPException, Path, Query, status

from app.api.validation import reject_nul_bytes
from app.models.demand_events import DemandEventResponse
from app.repositories.demand_events_postgres_repository import (
    DemandEventPostgresRepository,
)

router = APIRouter(prefix="/api/demand-events", tags=["demand-events"])
_repo = DemandEventPostgresRepository()


@router.get("", response_model=list[DemandEventResponse])
def list_demand_events(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000_000),
    item_code: str | None = Query(default=None, max_length=64),
    site_code: str | None = Query(default=None, max_length=64),
    occurred_from: date | None = Query(default=None),
    occurred_to: date | None = Query(default=None),
) -> list[dict]:
    """List demand events ordered by business_key."""
    reject_nul_bytes(
        item_code, site_code, detail="demand event filters must not contain NUL bytes"
    )
    return _repo.list_rows(
        limit,
        offset,
        item_code=item_code,
        site_code=site_code,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
    )


@router.get("/{business_key}", response_model=DemandEventResponse)
def get_demand_event(business_key: str = Path(..., max_length=128)) -> dict:
    """Fetch one demand event by its business key (source line id)."""
    reject_nul_bytes(
        business_key, detail="demand event filters must not contain NUL bytes"
    )
    row = _repo.get_by_business_key(business_key)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="demand event not found"
        )
    return row
