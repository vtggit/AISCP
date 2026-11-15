"""Read-only API for demand statistics per item and site."""

from datetime import date

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.stats.demand import demand_stats_for_all, demand_stats_for_pair

router = APIRouter(prefix="/api/demand-stats", tags=["demand-stats"])

_NUL = chr(0)


def _reject_nul_bytes(*values: str | None) -> None:
    for value in values:
        if value is not None and _NUL in value:
            raise HTTPException(
                status_code=422,
                detail="demand stats filters must not contain NUL bytes",
            )


class DemandStatsResponse(BaseModel):
    """One item-site's demand profile over its 30-day buckets."""

    item_code: str
    site_code: str
    as_of: date
    window_start: date
    window_end: date
    bucket_days: int
    n_buckets: int
    nonzero_buckets: int
    total_quantity: float
    mean_daily: float
    std_daily: float
    adi: float | None
    cv2: float | None
    demand_class: str
    recommendable: bool
    intermittent: bool
    method: str


@router.get("", response_model=list[DemandStatsResponse])
def list_demand_stats(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000_000),
    item_code: str | None = Query(default=None, max_length=64),
    site_code: str | None = Query(default=None, max_length=64),
    as_of: date | None = Query(default=None),
) -> list[dict]:
    """List demand stats per (item_code, site_code), ordered by item_code then site_code."""
    _reject_nul_bytes(item_code, site_code)
    rows = demand_stats_for_all(as_of)
    if item_code is not None:
        rows = [row for row in rows if row["item_code"] == item_code]
    if site_code is not None:
        rows = [row for row in rows if row["site_code"] == site_code]
    return rows[offset : offset + limit]


@router.get("/by-key", response_model=DemandStatsResponse)
def get_demand_stats_by_key(
    item_code: str = Query(..., max_length=64),
    site_code: str = Query(..., max_length=64),
    as_of: date | None = Query(default=None),
) -> dict:
    """Fetch one pair's demand stats; 404 when the pair has no demand."""
    _reject_nul_bytes(item_code, site_code)
    row = demand_stats_for_pair(item_code, site_code, as_of)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="demand stats not found"
        )
    return row
